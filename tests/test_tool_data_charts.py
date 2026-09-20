"""Tool-data protocol: reference extraction, no-fallback resolution, and the
descriptor round-trip that keeps message rows out of the database.

The store and its Parquet archive run for real against a temp directory; nothing
is mocked. These are the server-side modules the chat chart feature is built on.
"""

from __future__ import annotations

from src.agent_platform.runtime.tool_data import (
    ToolData,
    ToolDataScope,
    ToolDataStore,
    descriptors_from_payloads,
    payloads_from_descriptors,
    referenced_call_ids,
    resolve_tool_data,
)

COLUMNS = ["region", "revenue"]
ROWS = [["east", "10"], ["west", "7"]]


def _scope(tmp_path) -> ToolDataScope:
    return ToolDataScope(key="conv-test", root=tmp_path / "tool-data")


def _cached(tmp_path, call_id: str = "call-1", run_id: int | None = 7):
    store = ToolDataStore()
    scope = _scope(tmp_path)
    data = store.put(
        scope,
        ToolData(
            call_id=call_id,
            tool_name="execute_sql_query",
            columns=list(COLUMNS),
            rows=[list(row) for row in ROWS],
            total_rows=2,
        ),
        run_id=run_id,
    )
    return store, scope, data


# --------------------------------------------------------------- references


def test_bare_and_fenced_references_are_found():
    text = 'Here:\n\n#TABLE_D1\n\n```chart\n#CHART_D2\n{"type":"bar"}\n```\n'
    assert referenced_call_ids(text) == ["D1", "D2"]


def test_references_are_deduplicated_in_order():
    assert referenced_call_ids("#CHART_D3 #TABLE_D1 #CHART_D3") == ["D3", "D1"]


def test_reference_inside_an_ordinary_code_fence_is_ignored():
    text = "Example code:\n\n```python\n#TABLE_D9\nprint('#CHART_D8')\n```\n\nReal: #CHART_D1"
    assert referenced_call_ids(text) == ["D1"]


def test_reference_inside_a_plain_or_tilde_fence_is_ignored():
    assert referenced_call_ids("```\n#TABLE_D1\n```") == []
    assert referenced_call_ids("~~~\n#TABLE_D1\n~~~") == []


# --------------------------------------------------------------- resolution


def test_resolve_tool_data_reads_the_cached_table(tmp_path):
    store, scope, data = _cached(tmp_path)
    payloads = resolve_tool_data("see #TABLE_D1", scope, store=store)
    assert [p["call_id"] for p in payloads] == [data.ref]
    assert payloads[0]["rows"] == ROWS


def test_invented_reference_is_not_substituted_with_the_only_cached_table(tmp_path):
    # One result was cached this run; a reference that matches nothing must stay
    # unresolved rather than silently drawing that result.
    store, scope, _ = _cached(tmp_path)
    assert resolve_tool_data("see #CHART_D9", scope, store=store) == []


def test_missing_archive_yields_nothing(tmp_path):
    scope = ToolDataScope(key="conv-empty", root=tmp_path / "absent")
    assert resolve_tool_data("see #TABLE_D1", scope, store=ToolDataStore()) == []


# -------------------------------------------------------------- descriptors


def test_descriptors_strip_rows_but_keep_metadata(tmp_path):
    store, scope, _ = _cached(tmp_path)
    descriptors = descriptors_from_payloads(resolve_tool_data("see #TABLE_D1", scope, store=store))
    assert len(descriptors) == 1
    assert "rows" not in descriptors[0]
    assert descriptors[0]["call_id"] == "D1"
    assert descriptors[0]["columns"] == COLUMNS
    assert descriptors[0]["total_rows"] == 2
    assert descriptors[0]["returned_rows"] == 2


def test_descriptors_rehydrate_from_the_archive_after_a_restart(tmp_path):
    store, scope, _ = _cached(tmp_path)
    descriptors = descriptors_from_payloads(resolve_tool_data("see #TABLE_D1", scope, store=store))

    # A fresh store has an empty memory tier: the rows can only come from the
    # Parquet archive, exactly as they do on a conversation reload.
    restarted = ToolDataStore()
    payloads = payloads_from_descriptors(descriptors, scope, store=restarted)
    assert payloads and payloads[0]["rows"] == ROWS
    assert payloads[0]["tool_name"] == "execute_sql_query"


def test_unresolvable_descriptor_rehydrates_to_nothing(tmp_path):
    scope = ToolDataScope(key="conv-gone", root=tmp_path / "absent")
    descriptors = [{"call_id": "D1", "tool_name": "t", "columns": COLUMNS, "total_rows": 2}]
    assert payloads_from_descriptors(descriptors, scope, store=ToolDataStore()) == []


def test_conversation_read_drops_a_descriptor_whose_archive_is_gone():
    # The route helper rehydrates from the archive; an unresolvable descriptor is
    # removed so the chat shows its "no longer available" card rather than an
    # empty chart.
    from src.agent_platform.api.conversation_routes import _attach_tool_data

    messages = [{"meta": {"tool_data": [{"call_id": "D1", "total_rows": 5}], "agent": "a"}}]
    out = _attach_tool_data({"public_id": "conv-does-not-exist"}, messages)
    assert "tool_data" not in out[0]["meta"]
    assert out[0]["meta"]["agent"] == "a"
