"""Live typed-chart tests: real LLM, real MCP stdio transport, real typed contract.

These run against the running Flask app and the real OpenRouter model (see
``tests/livehelpers.py``)::

    python -m pytest tests/test_live_tool_data_charts.py -q

Every assertion is structural, because the model's prose and its choice of chart
are non-deterministic. What is *not* non-deterministic is the contract: a tool
result carries declared column types and native values, and a chart spec reaches
the browser only after the server has validated it.
"""

from __future__ import annotations

import json

import pytest

from livehelpers import DEFAULT_MODEL, LiveClient

CHART_BINDING = {
    "server": "Text2SQL",
    "tools": ["list_tables", "get_table_schema", "execute_sql_query"],
    "approval": [],
    "tool_data": {"execute_sql_query": {"sample": True, "sample_rows": 5, "cache_rows": 1000}},
}

INSTRUCTIONS = (
    "You are a data analyst. Use execute_sql_query to answer. When the user asks for a chart, "
    "emit a fenced chart block whose first line is the data reference and whose JSON names type, "
    "x and y, using only the columns printed in the [columns: ...] line."
)

COLUMN_TYPES = {
    "string",
    "integer",
    "number",
    "decimal",
    "boolean",
    "date",
    "datetime",
    "time",
    "mixed",
    "unknown",
}


@pytest.fixture(scope="module")
def chart_agent(api: LiveClient) -> dict:
    return api.create_agent(
        "Live Chart Data",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": INSTRUCTIONS,
            "model": DEFAULT_MODEL,
            "default_options": {"temperature": 0.1, "max_tokens": 1500},
            "mcp_bindings": [CHART_BINDING],
            "maf_skill_ids": ["text2sql"],
        },
    )


def _stream_until_done(api: LiveClient, slug: str, prompt: str, conversation_id) -> dict | None:
    body = {"agent_id": slug, "input": prompt, "stream": True, "conversation_id": conversation_id}
    with api.http.post(
        f"{api.base}/api/v1/runs", json=body, stream=True, timeout=(10, 240)
    ) as response:
        response.raise_for_status()
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not str(raw).startswith("data:"):
                continue
            try:
                event = json.loads(str(raw)[5:].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") == "done":
                return event
    return None


def test_a_live_chart_turn_ships_typed_data_and_a_server_normalised_spec(api, chart_agent):
    slug = chart_agent["slug"]
    conversation = api.create_conversation(slug, "live typed chart")
    cid = conversation.get("public_id") or conversation.get("id")
    done = _stream_until_done(
        api, slug, "Count customers per city, then draw a bar chart of it.", cid
    )
    assert done, "the run produced no done event"

    payloads = done.get("tool_data") or []
    assert payloads, "the turn shipped no tool data"

    payload = payloads[0]
    assert payload["call_id"] and payload["total_rows"] >= 1
    for column in payload["columns"]:
        assert column["type"] in COLUMN_TYPES, column

    # Values arrive as native JSON scalars: a number is a number, a missing value
    # is null. Nothing was stringified into the payload.
    for row in payload["rows"]:
        for value in row:
            assert value is None or isinstance(value, (str, int, float, bool)), value

    reply = done.get("reply") or ""
    assert "#CHART_" in reply or "#TABLE_" in reply, reply
    # A rendered spec is the server's canonical one, or an explicit refusal — never
    # the model's raw request passed through unchecked.
    if "#CHART_" in reply:
        assert '"aggregate"' in reply or '"error"' in reply, reply
