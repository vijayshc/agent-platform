"""Typed tool-data contract, archive, and server-side chart-spec validation.

Everything here runs against the real implementation: the contract is built and
parsed by the shared module, the Parquet archive writes and reads real files, the
spec validator runs the real JSON Schema, and the middleware is driven with a
real ``ToolMessage``. Nothing is mocked.
"""

from __future__ import annotations

import datetime
import decimal

import pytest
from langchain_core.messages import ToolMessage

from src.agent_platform.runtime.tool_data import (
    ERROR_KIND,
    Column,
    ToolData,
    ToolDataContractError,
    ToolDataScope,
    ToolDataStore,
    build_table,
    descriptors_from_payloads,
    normalize_reply,
    parse_contract,
    payloads_from_descriptors,
    resolve_tool_data,
)
from src.agent_platform.runtime.tool_data.middleware import ToolDataMiddleware
from src.agent_platform.runtime.tool_data.policy import ToolDataConfig, ToolDataPolicy

COLUMNS = ["region", "revenue"]
ROWS = [["east", 10.0], ["west", 7.0]]


def _scope(tmp_path) -> ToolDataScope:
    return ToolDataScope(key="conv-test", root=tmp_path / "tool-data")


def _cached(tmp_path, run_id: int | None = 7):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    data = store.put(
        scope,
        ToolData(
            call_id="call-1",
            tool_name="execute_sql_query",
            columns=[Column("region", "string"), Column("revenue", "number")],
            rows=[list(row) for row in ROWS],
            total_rows=2,
        ),
        run_id=run_id,
    )
    return store, scope, data


def _spec(text: str, store, scope):
    return normalize_reply(text, scope, store=store)


# ------------------------------------------------------------------ contract


def test_types_come_from_the_runtime_value_not_from_how_text_looks():
    table = build_table(
        ["iso_text", "native_date", "amount"],
        [["2024-01-01", datetime.date(2024, 1, 1), 5], ["2024-02-01", None, 7]],
    )
    # A string is a string. Only a driver that returns a date object declares one.
    assert [(column.name, column.type) for column in table.columns] == [
        ("iso_text", "string"),
        ("native_date", "date"),
        ("amount", "integer"),
    ]


def test_a_mixed_column_keeps_each_cell_the_scalar_it_is():
    table = build_table(["value"], [[1], ["a"], [True], [None]])
    assert table.columns[0].type == "mixed"
    # The integer stays an integer; nothing is stringified.
    assert table.rows == [[1], ["a"], [True], [None]]


def test_an_exact_decimal_travels_as_an_exact_string():
    exact = decimal.Decimal("12345678901234567890.123456789")
    table = build_table(["amount"], [[exact], [None]])
    assert table.columns[0].type == "decimal"
    assert table.rows == [[str(exact)], [None]]
    assert decimal.Decimal(table.rows[0][0]) == exact


def test_an_integer_too_large_for_json_becomes_an_exact_decimal():
    huge = 2**63 + 1
    table = build_table(["id"], [[huge], [1]])
    assert table.columns[0].type == "decimal"
    assert table.rows == [[str(huge)], ["1"]]


def test_a_non_finite_number_is_refused_rather_than_put_on_the_wire():
    # JSON cannot carry Infinity; json.dumps would emit a bare `Infinity` and the
    # browser's JSON.parse would reject the whole event.
    with pytest.raises(ToolDataContractError):
        build_table(["x"], [[float("inf")]])
    with pytest.raises(ToolDataContractError):
        parse_contract(
            {
                "kind": "tool_data_table",
                "version": 1,
                "columns": [{"name": "x", "type": "number"}],
                "rows": [[float("nan")]],
                "total_rows": 1,
            }
        )


def test_a_null_is_a_null_not_the_word_null():
    table = build_table(["value"], [[None], [3]])
    assert table.columns[0].type == "integer"
    assert table.rows == [[None], [3]]


def test_an_all_null_column_is_unknown():
    table = build_table(["value"], [[None], [None]])
    assert table.columns[0].type == "unknown"
    assert table.rows == [[None], [None]]


def test_contract_rejects_a_cell_that_contradicts_its_declared_type():
    with pytest.raises(ToolDataContractError):
        parse_contract(
            {
                "kind": "tool_data_table",
                "version": 1,
                "columns": [{"name": "n", "type": "integer"}],
                "rows": [["not-a-number"]],
                "total_rows": 1,
            }
        )


def test_contract_rejects_a_row_of_the_wrong_width():
    with pytest.raises(ToolDataContractError):
        parse_contract(
            {
                "kind": "tool_data_table",
                "version": 1,
                "columns": [{"name": "a", "type": "string"}, {"name": "b", "type": "string"}],
                "rows": [["only-one"]],
                "total_rows": 1,
            }
        )


def test_a_payload_that_is_not_a_table_is_left_alone():
    assert parse_contract(None) is None
    assert parse_contract({"some": "other structured content"}) is None


# ------------------------------------------------------------------- archive


def test_the_archive_round_trips_types_and_nulls_through_parquet(tmp_path):
    store, scope, data = _cached(tmp_path)
    assert data.ref == "D1"

    # A fresh store has an empty memory tier: the values can only come from disk.
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded is not None
    assert [(column.name, column.type) for column in reloaded.columns] == [
        ("region", "string"),
        ("revenue", "number"),
    ]
    assert reloaded.rows == ROWS


def test_the_archive_keeps_exact_decimals_and_mixed_scalars(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    exact = decimal.Decimal("12345678901234567890.123456789")
    store.put(
        scope,
        ToolData(
            call_id="call-types",
            tool_name="t",
            columns=[Column("amount", "decimal"), Column("mixed", "mixed")],
            rows=[[str(exact), 1], [None, "a"], ["1.5", True]],
            total_rows=3,
        ),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded.rows == [[str(exact), 1], [None, "a"], ["1.5", True]]
    assert decimal.Decimal(reloaded.rows[0][0]) == exact


def test_a_timezone_aware_datetime_round_trips_to_the_same_instant(tmp_path):
    aware = datetime.datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=5))
    )
    table = build_table(["ts"], [[aware], [None]])
    assert table.columns[0].type == "datetime"

    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(call_id="c", tool_name="t", columns=table.columns, rows=table.rows, total_rows=2),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    # Memory and archive must agree, and the instant must survive.
    assert reloaded.rows[0][0] == table.rows[0][0]
    assert datetime.datetime.fromisoformat(reloaded.rows[0][0]) == aware


def test_a_naive_datetime_stays_naive(tmp_path):
    naive = datetime.datetime(2024, 1, 2, 3, 4, 5)
    table = build_table(["ts"], [[naive]])
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(call_id="c", tool_name="t", columns=table.columns, rows=table.rows, total_rows=1),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded.rows[0][0] == "2024-01-02T03:04:05"


def test_a_resave_of_the_same_shape_with_different_values_rewrites(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    columns = [Column("region", "string"), Column("revenue", "number")]
    store.put(
        scope,
        ToolData(call_id="c", tool_name="t", columns=columns, rows=[["east", 1.0]], total_rows=1),
    )
    store.put(
        scope,
        ToolData(call_id="c", tool_name="t", columns=columns, rows=[["east", 2.0]], total_rows=1),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded.rows == [["east", 2.0]]


def test_values_with_pipes_newlines_and_backslashes_round_trip(tmp_path):
    values = ["a|b", "line1\nline2", "back\\slash", "NULL", "", "  spaced  ", "emoji 🙂"]
    table = build_table(["v"], [[value] for value in values] + [[None]])
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=table.columns,
            rows=table.rows,
            total_rows=len(table.rows),
        ),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded.rows == [[value] for value in values] + [[None]]


def test_date_time_and_boolean_round_trip_through_parquet(tmp_path):
    table = build_table(
        ["d", "t", "b"],
        [
            [datetime.date(2024, 1, 2), datetime.time(3, 4, 5), True],
            [None, None, False],
        ],
    )
    assert [column.type for column in table.columns] == ["date", "time", "boolean"]
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(call_id="c", tool_name="t", columns=table.columns, rows=table.rows, total_rows=2),
    )
    reloaded = ToolDataStore().resolve(scope, "D1")
    assert reloaded.rows == [["2024-01-02", "03:04:05", True], [None, None, False]]


def test_an_empty_result_declares_unknown_columns():
    table = build_table(["a", "b"], [])
    assert [column.type for column in table.columns] == ["unknown", "unknown"]
    assert table.rows == []


def test_descriptors_rehydrate_the_typed_table_after_a_restart(tmp_path):
    store, scope, _ = _cached(tmp_path)
    descriptors = descriptors_from_payloads(resolve_tool_data("see #TABLE_D1", scope, store=store))
    assert "rows" not in descriptors[0]
    assert descriptors[0]["columns"] == [
        {"name": "region", "type": "string"},
        {"name": "revenue", "type": "number"},
    ]
    payloads = payloads_from_descriptors(descriptors, scope, store=ToolDataStore())
    assert payloads[0]["rows"] == ROWS


def test_conversation_read_drops_a_descriptor_whose_archive_is_gone():
    from src.agent_platform.api.conversation_routes import _attach_tool_data

    messages = [{"meta": {"tool_data": [{"call_id": "D1", "total_rows": 5}], "agent": "a"}}]
    out = _attach_tool_data({"public_id": "conv-does-not-exist"}, messages)
    assert "tool_data" not in out[0]["meta"]
    assert out[0]["meta"]["agent"] == "a"


# -------------------------------------------------------------- spec validation

VALID = (
    "```chart\n#CHART_D1\n"
    '{"type": "bar", "x": "region", "y": "revenue", "aggregate": "sum"}\n```'
)


def test_a_valid_spec_is_normalised_with_every_field_explicit(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(VALID, store, scope)
    assert report[0]["status"] == "ok"
    assert '"type": "bar"' in out
    assert '"x": "region"' in out
    assert '"aggregate": "sum"' in out
    assert '"layout": "full"' in out
    assert '"height": 300' in out


def test_aggregate_is_required_for_a_chart_that_groups(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(
        '```chart\n#CHART_D1\n{"type": "bar", "x": "region", "y": "revenue"}\n```', store, scope
    )
    assert report[0]["status"] == "error"
    assert "aggregate" in report[0]["error"]
    assert '"error"' in out


def test_aggregate_none_is_rejected_on_a_grouping_chart(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "region", "y": "revenue", "aggregate": "none"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"


def test_a_spec_missing_a_required_field_is_an_error_not_a_default(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec('```chart\n#CHART_D1\n{"type": "bar"}\n```', store, scope)
    assert report[0]["status"] == "error"


def test_an_unknown_column_is_rejected_and_the_error_names_the_columns(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "nope", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"
    assert "region" in report[0]["error"]


def test_a_text_column_cannot_be_plotted_as_a_measure(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "region", "y": "region", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"


def test_a_duplicate_measure_is_rejected(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "region", "y": ["revenue", "revenue"], "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"


def test_case_alone_is_repaired_and_reported(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "Region", "y": "Revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "ok"
    assert report[0]["diagnostics"]
    assert '"x": "region"' in out


def test_a_pie_needs_one_measure_and_no_series(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=[
                Column("region", "string"),
                Column("revenue", "number"),
                Column("orders", "integer"),
            ],
            rows=[["east", 1.0, 2]],
            total_rows=1,
        ),
    )
    _, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "pie", "x": "region", "y": ["revenue", "orders"], "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"


def test_a_scatter_needs_a_numeric_x(tmp_path):
    store, scope, _ = _cached(tmp_path)
    _, report = _spec(
        '```chart\n#CHART_D1\n{"type": "scatter", "x": "region", "y": "revenue"}\n```', store, scope
    )
    assert report[0]["status"] == "error"


def test_a_scatter_must_not_carry_an_aggregate_or_a_series(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=[Column("nx", "number"), Column("y", "number"), Column("g", "string")],
            rows=[[1.0, 2.0, "a"], [2.0, 3.0, "b"]],
            total_rows=2,
        ),
    )
    _, report = _spec(
        '```chart\n#CHART_D1\n{"type": "scatter", "x": "nx", "y": "y", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"
    _, report2 = _spec(
        '```chart\n#CHART_D1\n{"type": "scatter", "x": "nx", "y": "y", "series": "g"}\n```',
        store,
        scope,
    )
    assert report2[0]["status"] == "error"


def test_grouping_rows_together_is_disclosed(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=[Column("region", "string"), Column("revenue", "number")],
            rows=[["east", 1.0], ["east", 2.0], ["west", 3.0]],
            total_rows=3,
        ),
    )
    out, report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "region", "y": "revenue", "aggregate": "avg"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "ok"
    assert any("grouped" in note for note in report[0]["diagnostics"])
    assert '"diagnostics"' in out


def test_a_clipped_result_is_disclosed(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=[Column("region", "string"), Column("revenue", "number")],
            rows=[["east", 1.0], ["west", 2.0]],
            total_rows=1000,
        ),
    )
    _, report = _spec(VALID, store, scope)
    assert any("cache limit" in note for note in report[0]["diagnostics"])


def test_normalisation_reaches_a_fixed_point(tmp_path):
    store, scope, _ = _cached(tmp_path)
    # Case repair produces a diagnostic, which the second pass must accept.
    first, _ = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "Region", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    second, second_report = _spec(first, store, scope)
    assert second_report[0]["status"] == "ok", second_report
    third, third_report = _spec(second, store, scope)
    assert third_report[0]["status"] == "ok", third_report
    # After one normalisation the reply is stable: the repair is no longer needed,
    # so its diagnostic drops out, and nothing else changes.
    assert third == second


def test_a_chart_fence_with_a_brace_is_validated_too(tmp_path):
    store, scope, _ = _cached(tmp_path)
    # ```chart{...} is a block to the client and the reference scan; the validator
    # must read it the same way or the spec reaches the browser unchecked.
    _, report = _spec(
        "```chart{\n#CHART_D1\n"
        '{"type": "bar", "x": "nope", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"


def test_a_block_whose_reference_is_gone_is_left_as_written(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(
        "```chart\n#CHART_D9\n"
        '{"type": "bar", "x": "region", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "unresolved"
    assert "#CHART_D9" in out


def test_a_table_block_carrying_chart_fields_says_which_fence_to_use(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(
        '```table\n#TABLE_D1\n{"type": "bar", "x": "region", "y": "revenue"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "error"
    assert "chart fence" in report[0]["error"]
    assert '"error"' in out


def test_a_bare_placeholder_does_not_swallow_the_prose_after_it(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec("#TABLE_D1\n\nHere is the table.\n\nMore prose.", store, scope)
    assert report[0]["status"] == "ok"
    assert "Here is the table." in out
    assert "More prose." in out


def test_a_half_width_table_keeps_its_layout(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, _ = _spec('```table\n#TABLE_D1\n{"layout": "half"}\n```', store, scope)
    assert '"layout": "half"' in out


def test_an_invented_reference_is_not_substituted_with_the_only_cached_table(tmp_path):
    store, scope, _ = _cached(tmp_path)
    assert resolve_tool_data("see #CHART_D9", scope, store=store) == []


def test_a_reference_with_a_suffix_is_not_resolved(tmp_path):
    store, scope, _ = _cached(tmp_path)
    assert store.resolve(scope, "D1_line") is None
    assert store.resolve(scope, "1") is None
    assert store.resolve(scope, "D1") is not None


def test_a_mixed_column_with_temporal_or_json_cells_is_json_safe():
    import json as jsonlib

    table = build_table(["v"], [[datetime.date(2024, 1, 1)], [{"a": 1}], [1], ["x"]])
    assert table.columns[0].type == "mixed"
    # The producer's output must survive the JSON wire and the consumer's checks.
    jsonlib.dumps(table.to_contract())
    assert parse_contract(table.to_contract()).rows == table.rows
    assert table.rows[0] == ["2024-01-01"]
    assert table.rows[1] == ['{"a": 1}']


def test_a_json_document_column_is_text_and_json_safe():
    import json as jsonlib

    table = build_table(["doc"], [[{"a": 1}], [{"b": [1, 2]}]])
    assert table.columns[0].type == "string"
    jsonlib.dumps(table.to_contract())
    assert table.rows == [['{"a": 1}'], ['{"b": [1, 2]}']]


def test_a_non_finite_decimal_is_refused():
    with pytest.raises(ToolDataContractError):
        build_table(["d"], [[decimal.Decimal("NaN")]])
    with pytest.raises(ToolDataContractError):
        parse_contract(
            {
                "kind": "tool_data_table",
                "version": 1,
                "columns": [{"name": "d", "type": "decimal"}],
                "rows": [["Infinity"]],
                "total_rows": 1,
            }
        )


def test_a_scatter_is_a_normalisation_fixed_point(tmp_path):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    store.put(
        scope,
        ToolData(
            call_id="c",
            tool_name="t",
            columns=[Column("nx", "number"), Column("y", "number")],
            rows=[[1.0, 2.0], [2.0, 3.0]],
            total_rows=2,
        ),
    )
    first, first_report = _spec(
        '```chart\n#CHART_D1\n{"type": "scatter", "x": "nx", "y": "y"}\n```', store, scope
    )
    assert first_report[0]["status"] == "ok", first_report
    assert '"aggregate": "none"' in first
    second, second_report = _spec(first, store, scope)
    assert second_report[0]["status"] == "ok", second_report
    assert second == first


def test_a_column_mixing_a_float_with_an_exact_number_is_decimal():
    table = build_table(["x"], [[1.5], [2**60 + 1]])
    assert table.columns[0].type == "decimal"
    assert table.rows == [["1.5"], [str(2**60 + 1)]]


def test_a_declared_type_fills_in_a_column_the_values_cannot_type():
    from src.agent_platform.runtime.tool_data import apply_declared_types

    table = build_table(["a", "b"], [])
    assert [column.type for column in table.columns] == ["unknown", "unknown"]
    refined = apply_declared_types(table, {"a": "integer", "b": "boolean"})
    assert [column.type for column in refined.columns] == ["integer", "boolean"]


def test_a_declared_type_does_not_override_a_column_that_has_values():
    from src.agent_platform.runtime.tool_data import apply_declared_types

    # The result column may be an expression, so its returned values win.
    table = build_table(["a"], [[1], [2]])
    refined = apply_declared_types(table, {"a": "boolean"})
    assert refined.columns[0].type == "integer"


def test_a_declared_type_wins_when_the_values_are_consistent_with_it():
    from src.agent_platform.runtime.tool_data import apply_declared_types

    # SQLite stores a boolean as 0/1: the schema says boolean, so it is boolean.
    table = build_table(["is_active"], [[0], [1]])
    assert table.columns[0].type == "integer"
    refined = apply_declared_types(table, {"is_active": "boolean"})
    assert refined.columns[0].type == "boolean"
    assert refined.rows == [[False], [True]]
    # A count that happens to share the name keeps its value type.
    counted = build_table(["is_active"], [[5], [7]])
    assert apply_declared_types(counted, {"is_active": "boolean"}).columns[0].type == "integer"


def test_category_identity_matches_the_browser(tmp_path):
    from src.agent_platform.runtime.tool_data.spec import _distinct

    # 1 and "1" are different categories; so are 1 and 1.0, because the browser's
    # grouping key is `${typeof value}:${String(value)}` too.
    assert _distinct([1, "1"]) == 2
    assert _distinct([1, 1.0]) == 2
    assert _distinct([True, 1]) == 2
    assert _distinct([1, 1, 1]) == 1


def test_a_file_written_in_another_format_is_not_read(tmp_path):
    import json as jsonlib

    import pyarrow as pa
    from pyarrow import parquet as pq

    from src.agent_platform.runtime.tool_data.archive import (
        ARCHIVE_DIRNAME,
        INDEX_VERSION,
        _META_CALL,
        _META_COLUMNS,
        _META_FORMAT,
        _META_TOOL,
        _META_TOTAL,
    )

    root = tmp_path / ARCHIVE_DIRNAME
    root.mkdir(parents=True)
    # A typed-v1 file: datetime as an Arrow timestamp, tagged typed-v1.
    table = pa.table(
        {"ts": pa.array([datetime.datetime(2024, 1, 1, 12, 0, 0)], type=pa.timestamp("us"))}
    )
    table = table.replace_schema_metadata(
        {
            _META_FORMAT: b"typed-v1",
            _META_CALL: b"c",
            _META_TOOL: b"t",
            _META_COLUMNS: jsonlib.dumps([{"name": "ts", "type": "datetime"}]).encode(),
            _META_TOTAL: b"1",
        }
    )
    pq.write_table(table, root / "D1.parquet")
    (root / "index.json").write_text(
        jsonlib.dumps(
            {
                "version": INDEX_VERSION,
                "next_index": 2,
                "refs": {
                    "D1": {
                        "call_id": "c",
                        "tool_name": "t",
                        "columns": [{"name": "ts", "type": "datetime"}],
                        "total_rows": 1,
                        "cached_rows": 1,
                        "hash": "",
                        "file": "D1.parquet",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    scope = ToolDataScope(key="conv-format", root=root)
    # Even with an index that names the file, the format check refuses it.
    assert ToolDataStore().resolve(scope, "D1") is None


def test_a_nested_datetime_in_a_json_document_is_iso_text():
    table = build_table(
        ["doc"],
        [[{"when": datetime.datetime(2024, 1, 1, 12, 0, 0)}], [{"when": "x"}]],
    )
    assert table.columns[0].type == "string"
    assert '"when": "2024-01-01T12:00:00"' in table.rows[0][0]


def test_a_native_datetime_is_not_mistaken_for_a_declared_date():
    from src.agent_platform.runtime.tool_data import apply_declared_types

    table = build_table(["when"], [[datetime.datetime(2024, 1, 2, 3, 4, 5)]])
    assert table.columns[0].type == "datetime"
    # A datetime is not a date, even though it subclasses one.
    refined = apply_declared_types(table, {"when": "date"})
    assert refined.columns[0].type == "datetime"


def test_the_chart_example_the_model_is_shown_is_valid():
    import json as jsonlib
    import re
    from pathlib import Path

    from src.agent_platform.runtime.tool_data.prompt import DEFAULT_PROTOCOL
    from src.agent_platform.runtime.tool_data.spec import _CHART_VALIDATOR, _schema_error

    skill = Path(__file__).resolve().parents[1] / "src/agent_platform/skills/chart-rendering/SKILL.md"
    for label, body in (("default", DEFAULT_PROTOCOL), ("skill", skill.read_text(encoding="utf-8"))):
        match = re.search(r"```chart\n#CHART_\w+\n(\{.*?\n\})\n```", body, re.DOTALL)
        assert match, f"{label}: no chart example in the prompt"
        spec = jsonlib.loads(match.group(1))
        problem = _schema_error(_CHART_VALIDATOR, spec)
        assert problem is None, f"{label}: the example the model sees is invalid: {problem}"


def test_a_reply_using_the_provider_call_id_is_rewritten_to_the_short_ref(tmp_path):
    store, scope, _ = _cached(tmp_path)
    out, report = _spec(
        "```chart\n#CHART_call-1\n"
        '{"type": "bar", "x": "region", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert report[0]["status"] == "ok", report
    # The payload is keyed by D1, so the reply must name D1.
    assert "#CHART_D1" in out


def test_an_error_block_is_a_fixed_point(tmp_path):
    store, scope, _ = _cached(tmp_path)
    chart_error, chart_report = _spec(
        "```chart\n#CHART_D1\n"
        '{"type": "bar", "x": "nope", "y": "revenue", "aggregate": "sum"}\n```',
        store,
        scope,
    )
    assert chart_report[0]["status"] == "error"
    chart_again, chart_again_report = _spec(chart_error, store, scope)
    assert chart_again_report[0]["status"] == "error"
    assert chart_again == chart_error

    table_error, table_report = _spec(
        '```table\n#TABLE_D1\n{"type": "bar", "x": "region", "y": "revenue"}\n```',
        store,
        scope,
    )
    assert table_report[0]["status"] == "error"
    table_again, table_again_report = _spec(table_error, store, scope)
    assert table_again_report[0]["status"] == "error"
    assert table_again == table_error


# ----------------------------------------------------------------- middleware


class _Request:
    def __init__(self, call_id: str, name: str) -> None:
        self.tool_call = {"id": call_id, "name": name}


def _middleware(tmp_path, tools=("execute_sql_query",)):
    policy = ToolDataPolicy({name: ToolDataConfig(enabled=True, sample_rows=1) for name in tools})
    return ToolDataMiddleware(policy, _scope(tmp_path), run_id=1, store=ToolDataStore())


def _message(contract, call_id="call-1", content="raw table"):
    return ToolMessage(
        content=content,
        tool_call_id=call_id,
        artifact={"structured_content": contract} if contract is not None else None,
    )


def test_the_middleware_samples_a_typed_table_and_stamps_its_columns(tmp_path):
    middleware = _middleware(tmp_path)
    table = build_table(COLUMNS, ROWS)
    result = middleware._process(
        _Request("call-1", "execute_sql_query"), _message(table.to_contract())
    )
    text = result.content
    assert "| east | 10" in text
    assert "1 more row omitted" in text
    assert "[data_ref=D1]" in text
    assert "[columns: region (string), revenue (number)]" in text
    # The full table lives in the cache now; the artifact must not ride along.
    assert result.artifact is None


def test_the_middleware_surfaces_a_broken_contract_instead_of_charting_it(tmp_path):
    middleware = _middleware(tmp_path)
    broken = {
        "kind": "tool_data_table",
        "version": 1,
        "columns": [{"name": "n", "type": "integer"}],
        "rows": [["oops"]],
        "total_rows": 1,
    }
    result = middleware._process(
        _Request("call-3", "execute_sql_query"), _message(broken, call_id="call-3")
    )
    assert "[tool_data error]" in result.content
    assert "malformed typed table" in result.content


def test_the_middleware_surfaces_a_missing_contract_for_an_opted_in_tool(tmp_path):
    middleware = _middleware(tmp_path)
    result = middleware._process(
        _Request("call-2", "execute_sql_query"),
        _message(None, call_id="call-2", content="just text"),
    )
    assert "[tool_data error]" in result.content
    assert "no typed table" in result.content


def test_the_middleware_passes_a_tool_error_envelope_through(tmp_path):
    middleware = _middleware(tmp_path)
    envelope = {"kind": ERROR_KIND, "version": 1, "message": "Database Error: no such table"}
    result = middleware._process(
        _Request("call-5", "execute_sql_query"),
        _message(envelope, call_id="call-5", content="Database error: no such table"),
    )
    assert result.content == "Database error: no such table\n\n[tool_call_id=call-5]"


def test_the_middleware_surfaces_unknown_structured_content(tmp_path):
    middleware = _middleware(tmp_path)
    result = middleware._process(
        _Request("call-6", "execute_sql_query"),
        _message({"kind": "something_else"}, call_id="call-6", content="x"),
    )
    assert "[tool_data error]" in result.content
    assert "not a tool_data_table" in result.content


def test_the_middleware_ignores_a_tool_that_did_not_opt_in(tmp_path):
    middleware = _middleware(tmp_path, tools=("other_tool",))
    result = middleware._process(
        _Request("call-4", "execute_sql_query"), _message(None, call_id="call-4", content="hi")
    )
    assert result.content == "hi\n\n[tool_call_id=call-4]"
