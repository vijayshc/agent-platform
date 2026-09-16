"""Tests for the Knowledge FastMCP Server and Agent Definition."""

import pytest

from platform_samples.mcp_servers.knowledge import (
    list_knowledge_documents,
    list_knowledge_tags,
    get_document_info,
)
from platform_samples.agents import KNOWLEDGE_AGENT


def test_list_knowledge_documents():
    res = list_knowledge_documents()
    assert isinstance(res, str)
    assert len(res) > 0


def test_list_knowledge_tags():
    res = list_knowledge_tags()
    assert isinstance(res, str)
    assert len(res) > 0


def test_get_document_info_not_found():
    res = get_document_info("nope")
    assert "not found" in res.lower() or "error" in res.lower()


def test_get_document_info_missing_id():
    res = get_document_info("")
    assert "document_id is required" in res.lower()


def test_knowledge_mcp_server_registered(temp_db):
    from platform_samples.feed import feed_mcp_servers
    from src.models.mcp_server import MCPServer

    assert "Knowledge" in feed_mcp_servers()
    server = MCPServer.get_by_name("Knowledge")
    assert server.name == "Knowledge"
    assert server.config["args"] == ["-m", "platform_samples.mcp_servers.knowledge"]
    assert server.server_type == "stdio"


def test_knowledge_agent_definition():
    assert KNOWLEDGE_AGENT["kind"] == "agent"
    assert KNOWLEDGE_AGENT["runtime"] == "agent"
    assert any(b["server"] == "Knowledge" for b in KNOWLEDGE_AGENT["mcp_bindings"])
    tools = KNOWLEDGE_AGENT["mcp_bindings"][0]["tools"]
    assert "search_knowledge" in tools
