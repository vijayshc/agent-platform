"""Regression tests: secrets must never appear in persisted run events or
serialized provider config (Gap H: SecretString + secret leakage audit).

Covers the three audit targets:
- MCP server config env / headers (src/models/mcp_server.py)
- LLM connection api key / http headers (src/models/llm_connection.py)
- Platform API keys (src/agent_platform/execution/api_keys.py)

ENGINE NOTE: PLUMBING. The run-level redaction tests drive a ScriptedChatClient
ONLY to produce a deterministic event stream; the assertion target is the
redaction pass, not agent behavior (that is proven live in
test_live_agent_platform.py / test_live_orchestration_api.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from src.models.secrets import SecretString
from src.agent_platform.execution.run_store import RunStore
from src.agent_platform.execution.span_sink import SpanSink
from src.models.llm_connection import LLMConnection
from src.models.mcp_server import MCPServer
from src.models.secrets import MASK, as_secret, merge_masked, wrap_credentials, wrap_headers

DANGER = "sk-leak-audit-9f3c2b-super-secret-value"
APP_ROOT = Path(__file__).resolve().parents[1]


def _assert_not_in(text: str, *needles: str) -> None:
    for needle in needles:
        assert needle not in text, f"secret leaked into serialized output: {needle}"


def _db_conn():
    from src.agent_platform import db

    return db.get_db_connection()


# --------------------------------------------------------------------------
# MCPServer config: env/headers credentials are SecretString at the source
# --------------------------------------------------------------------------


def test_mcp_config_wraps_credential_env_and_headers_in_secretstring():
    server = MCPServer(
        name="leaky",
        server_type="stdio",
        config={
            "command": "python",
            "args": ["-m", "svc"],
            "url": "https://mcp.example/mcp",
            "env": {
                "API_TOKEN": DANGER,
                "OPENAI_KEY": DANGER,
                "AWS_ACCESS_KEY_ID": DANGER,
                "GITHUB_PAT": DANGER,
                "APP_ROOT": "/app",
            },
            "headers": {"Authorization": f"Bearer {DANGER}", "X-Key": DANGER, "X-Tenant": "acme"},
        },
    )
    env = server.config["env"]
    headers = server.config["headers"]
    # env/headers are credential bags: every string is SecretString, not a name list.
    for key in ("API_TOKEN", "OPENAI_KEY", "AWS_ACCESS_KEY_ID", "GITHUB_PAT", "APP_ROOT"):
        assert isinstance(env[key], SecretString)
    assert isinstance(headers["Authorization"], SecretString)
    assert isinstance(headers["X-Key"], SecretString)
    assert isinstance(headers["X-Tenant"], SecretString)
    assert env["APP_ROOT"] == "/app"
    assert headers["X-Tenant"] == "acme"
    # command/args/url stay plaintext.
    assert server.config["command"] == "python"
    assert not isinstance(server.config["command"], SecretString)
    assert server.config["url"] == "https://mcp.example/mcp"
    # repr() is masked (this is the logging surface).
    assert DANGER not in repr(env["OPENAI_KEY"])
    assert DANGER not in repr(headers["X-Key"])
    assert "********" in repr(env["API_TOKEN"])


def test_mcp_config_masked_serialization_never_contains_secret():
    server = MCPServer(
        name="leaky",
        server_type="stdio",
        config={
            "command": "python",
            "env": {"API_TOKEN": DANGER, "OPENAI_KEY": DANGER, "AWS_ACCESS_KEY_ID": DANGER},
            "headers": {"Authorization": f"Bearer {DANGER}", "X-Key": DANGER},
        },
    )
    # API boundary must serialize via config_masked().
    masked = server.config_masked()
    text = json.dumps(masked)
    _assert_not_in(text, DANGER)
    assert masked["env"]["OPENAI_KEY"] == MASK
    assert masked["env"]["AWS_ACCESS_KEY_ID"] == MASK
    assert masked["headers"]["X-Key"] == MASK
    assert masked["command"] == "python"
    assert "********" in text
    # Direct json.dumps of the raw attr DOES carry the secret (SecretString is a
    # str subclass). This documents why config_masked() must be the boundary.
    assert DANGER in json.dumps(server.config)


def test_mcp_list_get_masks_non_heuristic_keys(temp_db):
    """GET/list of an MCP server must mask OPENAI_KEY / AWS_ACCESS_KEY_ID / X-Key."""
    MCPServer.create_table()
    server = MCPServer(
        name="leaky-keys",
        server_type="http",
        config={
            "url": "https://mcp.example/mcp",
            "env": {
                "OPENAI_KEY": DANGER,
                "AWS_ACCESS_KEY_ID": DANGER,
                "GITHUB_PAT": DANGER,
            },
            "headers": {"X-Key": DANGER, "Authorization": f"Bearer {DANGER}"},
        },
    )
    server.save()
    loaded = MCPServer.get_by_id(server.id)
    assert loaded is not None
    masked = loaded.config_masked()
    assert masked["url"] == "https://mcp.example/mcp"
    assert masked["env"]["OPENAI_KEY"] == MASK
    assert masked["env"]["AWS_ACCESS_KEY_ID"] == MASK
    assert masked["env"]["GITHUB_PAT"] == MASK
    assert masked["headers"]["X-Key"] == MASK
    _assert_not_in(json.dumps(masked), DANGER)
    listed = [row.config_masked() for row in MCPServer.get_all()]
    _assert_not_in(json.dumps(listed), DANGER)


def test_mcp_config_setter_wraps_new_values():
    server = MCPServer(name="s", server_type="stdio", config={})
    server.config = {"env": {"DATABASE_PASSWORD": DANGER}}
    assert isinstance(server.config["env"]["DATABASE_PASSWORD"], SecretString)


def test_merge_masked_restores_mcp_env_headers_and_flat_dicts():
    """MCP nested env/headers and a flat LLM http_headers dict both restore MASK."""
    previous = wrap_credentials(
        {
            "command": "python",
            "env": {"API_TOKEN": DANGER, "APP_ROOT": "/app"},
            "headers": {"Authorization": f"Bearer {DANGER}", "X-Tenant": "acme"},
        }
    )
    incoming = {
        "command": "python",
        "env": {"API_TOKEN": MASK, "APP_ROOT": "/app2"},
        "headers": {"Authorization": MASK, "X-Tenant": "other"},
    }
    merged = merge_masked(previous, incoming)
    assert merged["env"]["API_TOKEN"] == DANGER
    assert isinstance(merged["env"]["API_TOKEN"], SecretString)
    assert merged["env"]["APP_ROOT"] == "/app2"
    assert merged["headers"]["Authorization"] == f"Bearer {DANGER}"
    assert isinstance(merged["headers"]["Authorization"], SecretString)
    assert merged["headers"]["X-Tenant"] == "other"

    stored = wrap_headers({"Authorization": f"Bearer {DANGER}", "X-Tenant": "acme"})
    flat = merge_masked(stored, {"Authorization": MASK, "X-Tenant": "other"})
    assert flat["Authorization"] == f"Bearer {DANGER}"
    assert isinstance(flat["Authorization"], SecretString)
    assert flat["X-Tenant"] == "other"


def test_mcp_config_roundtrip_via_sqlite(temp_db):
    MCPServer.create_table()
    server = MCPServer(name="leaky", server_type="stdio", config={"env": {"API_TOKEN": DANGER}})
    server.save()
    loaded = MCPServer.get_by_id(server.id)
    assert loaded is not None
    assert loaded.config["env"]["API_TOKEN"] == DANGER  # str equality preserved
    assert isinstance(loaded.config["env"]["API_TOKEN"], SecretString)
    _assert_not_in(json.dumps(loaded.config_masked()), DANGER)


# --------------------------------------------------------------------------
# LLMConnection: api_key and credential headers
# --------------------------------------------------------------------------


def test_llm_connection_wraps_api_key_and_credential_headers():
    conn = LLMConnection(
        name="leaky",
        api_key=DANGER,
        http_headers={"Authorization": f"Bearer {DANGER}", "X-Key": DANGER, "X-Tenant": "acme"},
    )
    assert isinstance(conn.api_key, SecretString)
    assert isinstance(conn.http_headers["Authorization"], SecretString)
    assert isinstance(conn.http_headers["X-Key"], SecretString)
    assert isinstance(conn.http_headers["X-Tenant"], SecretString)
    assert conn.http_headers["X-Tenant"] == "acme"
    assert DANGER not in repr(conn.api_key)
    # str() still yields the secret so provider calls keep working.
    assert conn.api_key == DANGER


def test_llm_connection_masked_serialization_never_contains_secret():
    conn = LLMConnection(
        name="leaky",
        api_key=DANGER,
        http_headers={"Authorization": f"Bearer {DANGER}", "X-Key": DANGER},
    )
    masked = conn.config_dict_masked()
    text = json.dumps(masked)
    _assert_not_in(text, DANGER)
    assert masked["http_headers"]["X-Key"] == MASK
    assert "********" in text


def test_llm_manager_list_connection_headers_redacted(temp_db):
    """Headers returned by list_connections() must be masked."""

    from src.utils import llm_connection_manager as mgr

    LLMConnection.create_table()
    mgr.save_connection(
        {
            "name": "leaky",
            "base_url": "https://u",
            "api_key": DANGER,
            "model_name": "m",
            "http_headers": {"Authorization": f"Bearer {DANGER}", "X-Key": DANGER},
        }
    )
    listed = mgr.list_connections()
    dumped = json.dumps(listed)
    _assert_not_in(dumped, DANGER)
    headers = next(item["http_headers"] for item in listed if item["id"] == listed[0]["id"])
    assert headers["Authorization"] == MASK
    assert headers["X-Key"] == MASK


def test_llm_connection_api_key_setter_wraps_and_persists(temp_db):
    LLMConnection.create_table()
    conn = LLMConnection(name="leaky", api_key=DANGER, base_url="https://u", model_name="m")
    conn.save()
    loaded = LLMConnection.get_by_id(conn.id)
    assert loaded is not None and loaded.api_key == DANGER
    assert isinstance(loaded.api_key, SecretString)
    # Editing via setter (what _apply_payload does) keeps wrapping.
    loaded.api_key = "sk-changed"
    assert isinstance(loaded.api_key, SecretString)
    assert loaded.api_key == "sk-changed"


# --------------------------------------------------------------------------
# Platform API keys: only the one-time creation response carries the raw key
# --------------------------------------------------------------------------


def test_api_key_store_only_returns_raw_key_on_create(temp_db):
    from src.agent_platform.execution.api_keys import ApiKeyStore

    row = ApiKeyStore.create(user_id=1, name="audit", scopes=["runs:write"])
    raw = row["key"]
    assert raw.startswith("apk_")
    # list_for_user()/verify() must never re-introduce the plaintext.
    listed = ApiKeyStore.list_for_user(1)
    assert listed and "key" not in listed[0]
    _assert_not_in(json.dumps(listed), raw)
    verified = ApiKeyStore.verify(raw)
    assert verified is not None and "key" not in verified
    _assert_not_in(json.dumps(verified), raw)


def test_api_key_store_persists_only_hash_and_prefix(temp_db):
    from src.agent_platform.execution.api_keys import ApiKeyStore

    row = ApiKeyStore.create(user_id=1, name="audit")
    conn = _db_conn()
    stored = conn.execute("SELECT key_hash, prefix FROM api_keys WHERE id = ?", (row["id"],)).fetchone()
    conn.close()
    assert stored["prefix"] == row["prefix"]
    assert stored["key_hash"] != row["key"]
    assert row["key"] not in (stored["key_hash"], stored["prefix"])
    assert stored["key_hash"] == hashlib.sha256(row["key"].encode()).hexdigest()


# --------------------------------------------------------------------------
# agent_run_events: an MCP env secret never reaches persisted telemetry
# --------------------------------------------------------------------------


def _mcp_server_row(name: str) -> MCPServer:
    from platform_samples.mcp_servers import workspace as workspace_module

    MCPServer.create_table()
    server = MCPServer(
        name=name,
        server_type="stdio",
        config={
            "command": sys.executable,
            "args": ["-m", workspace_module.__name__],
            "env": {"API_TOKEN": DANGER, "APP_ROOT": str(APP_ROOT)},
        },
    )
    server.save()
    return server


def test_mcp_tool_schema_sent_to_model_never_contains_env_secret(temp_db):
    """PLUMBING unit test: MCP tool JSON schema sent to the model has no env secrets."""
    from src.agent_platform.plugins import register_builtin_plugins
    from src.agent_platform.runtime.compiler import compile_definition_sync

    register_builtin_plugins()
    _mcp_server_row("Schema-Audit")
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "Audit",
            "config": {
                "kind": "agent",
                "instructions": "List files.",
                "mcp_bindings": [{"server": "Schema-Audit", "tools": ["list_dir"]}],
                "model": {"client": "scripted", "responses": ["ok"]},
            },
        },
        # MCP access is per-user: connecting a server requires the run identity
        # (the row is owner-less/legacy, so any authenticated user may use it).
        user_id=1,
    )
    tool = compiled.mcp_tools[0]

    async def _load():
        try:
            await tool.connect()
            for fn in tool.functions:
                spec = json.dumps(
                    {
                        "name": getattr(fn, "name", ""),
                        "description": getattr(fn, "description", ""),
                    }
                )
                _assert_not_in(spec, DANGER)
        finally:
            closer = getattr(tool, "close", None)
            if closer:
                result = closer()
                if hasattr(result, "__await__"):
                    await result

    asyncio.run(_load())


def test_agent_run_events_never_contains_mcp_env_secret(temp_db, tmp_path, monkeypatch):
    """PLUMBING unit test: agent run events never contain MCP env secrets.

    Traces are no longer persisted to SQLite (they live in Arize Phoenix), so
    the redaction assertion targets the in-memory SpanSink replay surface --
    which is what the /events and AG-UI replay paths consume.

    Scripted client is used only to force a deterministic list_dir turn so the
    redaction pass can be inspected; not an agent-behavior assertion.
    """
    from src.agent_platform.runtime.host import RuntimeHost

    monkeypatch.setattr("src.agent_platform.paths.uploads_dir", lambda: tmp_path)
    from src.agent_platform.plugins.models.scripted import reset_shared_clients

    reset_shared_clients()
    _mcp_server_row("Run-Audit")
    # The run carries an identity: MCP access is checked against the running
    # user, and a run with no user must not be able to open a server session.
    run = RunStore.create(task="list files", agent_slug="audit", user_id=1)
    run_id = int(run["id"])
    RunStore.update_workspace(run_id, str(tmp_path / "ws"))
    definition = {
        "kind": "agent",
        "name": "Audit",
        "config": {
            "kind": "agent",
            "instructions": "List files.",
            "mcp_bindings": [{"server": "Run-Audit", "tools": ["list_dir"]}],
            "model": {
                "client": "scripted",
                "responses": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "list_dir",
                        "arguments": {"path": "."},
                    },
                    "listed",
                ],
            },
        },
    }
    host = RuntimeHost()
    result = asyncio.run(host.run_sync(definition=definition, input_text="list files", run_id=run_id))
    assert result["status"] == "success", result.get("error")
    events = SpanSink.get_events(run_id)
    assert events
    _assert_not_in(json.dumps(events), DANGER)


def test_span_sink_redacts_plain_str_equal_to_wrapped_secret(temp_db):
    """OTEL copies secrets as plain str; the replay surface must still redact them."""
    probe = "sk-LIVE-leak-proof-9f3c2b"
    wrapped = as_secret(probe)
    assert isinstance(wrapped, SecretString)
    run = RunStore.create(task="t", agent_slug="echo")
    run_id = int(run["id"])

    class ToolSpan:
        name = "execute_tool leak"
        attributes = {
            "gen_ai.tool.name": "leak",
            "arguments": str(wrapped),
            "authorization": f"Bearer {probe}",
            "api_key": probe,
            "http.request.header.x-key": probe,
        }
        start_time = 1_700_000_000_000_000_000
        end_time = 1_700_000_000_100_000_000
        parent = None

        def get_span_context(self):
            class Ctx:
                span_id = 99

            return Ctx()

    SpanSink.record_span(run_id, ToolSpan())
    SpanSink.record_event(run_id, "tool_call", source="runtime", detail={"arguments": probe})
    events = SpanSink.get_events(run_id)
    _assert_not_in(json.dumps(events), probe)
    for event in events:
        parsed = event.get("detail") or {}
        if "authorization" in parsed or "api_key" in parsed:
            assert parsed.get("authorization", MASK) == MASK
            assert parsed.get("api_key", MASK) == MASK


def test_mcp_http_binding_passes_unwrapped_headers(temp_db):
    """HTTP MCP bindings pass unwrapped headers on the MultiServerMCPClient connection."""
    from src.agent_platform.plugins import register_builtin_plugins
    from src.agent_platform.runtime.compiler import compile_definition_sync

    register_builtin_plugins()
    MCPServer.create_table()
    server = MCPServer(
        name="HTTP-Auth",
        server_type="http",
        config={
            "url": "https://mcp.example/mcp",
            "headers": {"X-Key": DANGER, "Authorization": f"Bearer {DANGER}"},
        },
    )
    server.save()
    compiled = compile_definition_sync(
        {
            "kind": "agent",
            "name": "H",
            "config": {
                "kind": "agent",
                "instructions": "x",
                "mcp_bindings": [{"server": "HTTP-Auth"}],
                "model": {"client": "scripted", "responses": ["ok"]},
            },
        },
        user_id=1,
    )
    tool = compiled.mcp_tools[0]
    assert tool.connection["transport"] == "http"
    headers = tool.connection["headers"]
    assert headers["X-Key"] == DANGER
    assert headers["Authorization"] == f"Bearer {DANGER}"
    assert type(headers["X-Key"]) is str
    assert type(headers["Authorization"]) is str
