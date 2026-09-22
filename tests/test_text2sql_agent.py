"""Tests for the Text-to-SQL FastMCP Server, MAF Skills, and Agent Definition."""

import pytest

from platform_samples.mcp_servers.text2sql import (
    list_workspaces,
    list_tables,
    get_table_schema,
    get_join_conditions,
    search_similar_queries,
    execute_sql_query,
)
from platform_samples.agents import TEXT2SQL_AGENT


def test_list_workspaces():
    res = list_workspaces()
    assert "Available Workspaces" in res
    assert "dfd" in res


def test_list_tables():
    res = list_tables(workspace_name="dfd")
    assert "Tables in Database" in res
    assert "customers" in res or "products" in res


def test_get_table_schema_single():
    res = get_table_schema("customers", workspace_name="dfd")
    assert "Table: `customers`" in res
    assert "customer_id" in res
    assert "name" in res or "email" in res


def test_get_table_schema_multiple():
    res = get_table_schema("customers, products", workspace_name="dfd")
    assert "Table: `customers`" in res
    assert "Table: `products`" in res


def test_get_table_schema_empty():
    res = get_table_schema("")
    assert "Please provide at least one table name" in res


def test_get_join_conditions():
    res = get_join_conditions(table_names="orders, customers", workspace_name="dfd")
    assert "Join Conditions" in res or "No specific" in res


def test_search_similar_queries():
    res = search_similar_queries("find customers", limit=2)
    assert isinstance(res, str)
    assert len(res) > 0


def test_execute_sql_query_select_returns_a_typed_table():
    res = execute_sql_query("SELECT COUNT(*) AS total FROM customers")
    assert "Query Results" in res.content[0].text
    contract = res.structuredContent
    assert contract["kind"] == "tool_data_table"
    assert contract["version"] == 1
    assert contract["columns"] == [{"name": "total", "type": "integer"}]
    assert contract["rows"] == [[5]]
    assert contract["total_rows"] == 1


def test_execute_sql_query_declares_each_column_from_the_driver_value():
    res = execute_sql_query(
        "SELECT customer_id, first_name FROM customers ORDER BY customer_id LIMIT 2"
    )
    assert res.structuredContent["columns"] == [
        {"name": "customer_id", "type": "integer"},
        {"name": "first_name", "type": "string"},
    ]
    # A value is the value: an id stays a number, a name stays text.
    assert res.structuredContent["rows"][0][0] == 1
    assert isinstance(res.structuredContent["rows"][0][1], str)


def test_execute_sql_query_read_only_protection():
    res = execute_sql_query("DROP TABLE customers")
    assert "Error: Only read-only queries" in res.content[0].text
    # A failure is an explicit error envelope, not a missing table.
    assert res.structuredContent["kind"] == "tool_data_error"

    res2 = execute_sql_query("DELETE FROM customers WHERE id=1")
    assert "Error: Only read-only queries" in res2.content[0].text

    res3 = execute_sql_query("INSERT INTO customers VALUES (10, 'Test')")
    assert "Error: Only read-only queries" in res3.content[0].text


def test_execute_sql_query_rejects_a_second_statement():
    res = execute_sql_query("SELECT 1; DROP TABLE customers")
    assert "one statement" in res.content[0].text
    assert res.structuredContent["kind"] == "tool_data_error"


def test_execute_sql_query_join():
    sql = """
    SELECT p.name, SUM(oi.quantity) AS qty
    FROM products p
    JOIN order_items oi ON p.product_id = oi.product_id
    GROUP BY p.product_id, p.name
    LIMIT 3
    """
    res = execute_sql_query(sql)
    assert "Query Results" in res.content[0].text
    names = [column["name"] for column in res.structuredContent["columns"]]
    assert "name" in names
    assert "qty" in names


def test_text2sql_mcp_server_registered(temp_db):
    from platform_samples.feed import feed_mcp_servers
    from src.models.mcp_server import MCPServer

    assert "Text2SQL" in feed_mcp_servers()
    server = MCPServer.get_by_name("Text2SQL")
    assert server.name == "Text2SQL"
    assert server.config["args"] == ["-m", "platform_samples.mcp_servers.text2sql"]
    assert server.server_type == "stdio"


def test_text2sql_agent_definition():
    assert TEXT2SQL_AGENT["kind"] == "agent"
    assert TEXT2SQL_AGENT["runtime"] == "agent"
    assert any(b["server"] == "Text2SQL" for b in TEXT2SQL_AGENT["mcp_bindings"])
    assert "text2sql" in TEXT2SQL_AGENT["maf_skill_ids"]

