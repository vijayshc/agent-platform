"""The real MCP transport: a tool's typed table must reach the tool message.

The runtime reads the table from ``ToolMessage.artifact["structured_content"]``,
which only exists if ``langchain-mcp-adapters`` publishes the server's
``structuredContent``. That wiring is the whole feature's foundation, so it is
tested against the **real** stdio server rather than a hand-built artifact.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _call_tool(sql: str):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    async def main():
        client = MultiServerMCPClient(
            {
                "Text2SQL": {
                    "command": sys.executable,
                    "args": ["-m", "platform_samples.mcp_servers.text2sql"],
                    "transport": "stdio",
                    "cwd": str(REPO_ROOT),
                }
            }
        )
        tool = next(t for t in await client.get_tools() if t.name == "execute_sql_query")
        return await tool.ainvoke(
            {
                "name": "execute_sql_query",
                "args": {"sql_query": sql},
                "id": "call-1",
                "type": "tool_call",
            }
        )

    return asyncio.run(main())


def test_the_real_mcp_transport_publishes_the_typed_table_as_the_artifact():
    message = _call_tool("SELECT customer_id, first_name FROM customers ORDER BY customer_id LIMIT 2")
    contract = message.artifact["structured_content"]
    assert contract["kind"] == "tool_data_table"
    assert contract["columns"] == [
        {"name": "customer_id", "type": "integer"},
        {"name": "first_name", "type": "string"},
    ]
    # Native values, not strings: the type declaration and the data agree.
    assert contract["rows"][0] == [1, "John"]
    assert isinstance(contract["rows"][0][0], int)


def test_the_real_mcp_transport_publishes_a_failure_as_an_error_envelope():
    message = _call_tool("DROP TABLE customers")
    contract = message.artifact["structured_content"]
    assert contract["kind"] == "tool_data_error"
    assert "read-only" in contract["message"]


def test_an_empty_result_is_typed_from_the_database_schema():
    # No rows means no values to type, so the producer reflects the schema. This
    # exercises the Inspector path end-to-end.
    message = _call_tool("SELECT customer_id, first_name FROM customers WHERE 1=0")
    contract = message.artifact["structured_content"]
    assert contract["rows"] == []
    assert {column["name"]: column["type"] for column in contract["columns"]} == {
        "customer_id": "integer",
        "first_name": "string",
    }
