"""Live MCP transport tests: real app, real LLM, real MCP servers (stdio + HTTP).

Both transports must work through the shipped LangGraph path: on-demand
discovery in the admin/studio APIs and tool execution inside a real agent run.
The HTTP side runs a genuine ``mcp`` SDK server (`mcp_http_probe_server.py`) that
requires a bearer token, so header pass-through and the 401 error path are
covered too.

    python -m pytest tests/test_live_mcp_transports.py -q
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

from livehelpers import DEFAULT_MODEL, LiveClient

SHORT = {"temperature": 0.0, "max_tokens": 300}
PROBE_TOKEN = "probe-live-token"
PROBE_TOOLS = {"http_probe_add", "http_probe_echo"}
HTTP_OK_MARKER = 42  # 17 + 25 computed by the probe server, not by the model


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _probe_ready(url: str, token: str, deadline_s: float = 30.0) -> bool:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "readiness", "version": "1"},
        },
    }
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            r = requests.post(
                url,
                json=payload,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                },
                timeout=3,
            )
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def http_probe():
    """A real streamable-HTTP MCP server, launched and torn down by this module."""
    port = _free_port()
    server_file = Path(__file__).with_name("mcp_http_probe_server.py")
    proc = subprocess.Popen(
        [sys.executable, str(server_file), str(port), PROBE_TOKEN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        if not _probe_ready(url, PROBE_TOKEN):
            pytest.fail(f"HTTP MCP probe server never became ready on {url}")
        yield {"url": url, "token": PROBE_TOKEN, "port": port}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _register(api: LiveClient, name: str, url: str, token: str) -> dict:
    r = api.http.post(
        f"{api.base}/api/admin/mcp-servers",
        json={
            "name": name,
            "description": "live transport probe",
            "server_type": "http",
            "config": {"url": url, "headers": {"Authorization": f"Bearer {token}"}},
        },
        timeout=30,
    )
    assert r.status_code < 400, r.text[:500]
    return r.json()["server"]


@pytest.fixture(scope="module")
def http_server(api: LiveClient, http_probe):
    name = f"HTTP-Probe-{uuid.uuid4().hex[:6]}"
    row = _register(api, name, http_probe["url"], http_probe["token"])
    try:
        yield row
    finally:
        api.http.delete(f"{api.base}/api/admin/mcp-servers/{row['id']}", timeout=30)


# ---------------------------------------------------------------- discovery


def test_stdio_tools_list_without_any_lifecycle(api: LiveClient):
    """The seeded stdio server advertises tools with no start/stop step."""
    r = api.http.get(f"{api.base}/api/admin/mcp-servers", timeout=90)
    assert r.status_code == 200, r.text[:400]
    servers = {row["name"]: row for row in r.json()["servers"]}
    assert "Workspace" in servers, sorted(servers)
    workspace = servers["Workspace"]
    assert "list_dir" in workspace["tools"], workspace
    assert workspace["tools_error"] is None, workspace
    assert "status" not in workspace, "lifecycle status must be gone"


def test_stdio_single_server_tools_endpoint(api: LiveClient):
    r = api.http.get(f"{api.base}/api/admin/mcp-servers", timeout=90)
    workspace_id = next(
        row["id"] for row in r.json()["servers"] if row["name"] == "Workspace"
    )
    r = api.http.get(f"{api.base}/api/admin/mcp-servers/{workspace_id}/tools", timeout=90)
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["success"] is True, body
    names = {tool["name"] for tool in body["tools"]}
    assert {"list_dir", "read_file", "write_file"} <= names, names
    schema = next(t for t in body["tools"] if t["name"] == "list_dir")["parameters"]
    # Full model-facing JSON Schema, so the admin "Arguments:" preview can read
    # .properties rather than a bare properties map.
    assert "path" in (schema.get("properties") or {}), schema
    assert schema.get("type") == "object", schema
    assert "list_dir" in json.dumps(r.json())


def test_http_tools_list_uses_configured_headers(api: LiveClient, http_server):
    """Tool listing over HTTP succeeds only because the bearer header is sent."""
    r = api.http.get(
        f"{api.base}/api/admin/mcp-servers/{http_server['id']}/tools", timeout=90
    )
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["success"] is True, body
    assert {tool["name"] for tool in body["tools"]} == PROBE_TOOLS, body


def test_http_wrong_token_reports_readable_error(api: LiveClient, http_probe):
    """A rejected token surfaces the HTTP cause, not an anyio TaskGroup blob."""
    name = f"HTTP-BadAuth-{uuid.uuid4().hex[:6]}"
    row = _register(api, name, http_probe["url"], "definitely-wrong")
    try:
        r = api.http.get(f"{api.base}/api/admin/mcp-servers/{row['id']}/tools", timeout=90)
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        assert body["success"] is False, body
        error = body["error"] or ""
        assert "401" in error or "unauthorized" in error.lower(), error
        assert "TaskGroup" not in error, error
        # and the list view reports it per row rather than failing the page
        listing = api.http.get(f"{api.base}/api/admin/mcp-servers", timeout=90).json()
        listed = next(s for s in listing["servers"] if s["id"] == row["id"])
        assert listed["tools"] == [] and listed["tools_error"], listed
    finally:
        api.http.delete(f"{api.base}/api/admin/mcp-servers/{row['id']}", timeout=30)


# ------------------------------------------------------------ agent runs


def test_live_stdio_agent_run_executes_tool(api: LiveClient):
    """stdio: a real agent run calls list_dir through the LangGraph binding."""
    agent = api.create_agent(
        "Live Stdio Transport",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": (
                "You MUST call list_dir with path='.' before answering, then list the "
                "entries you actually saw."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [
                {"server": "Workspace", "tools": ["list_dir", "read_file"], "approval": []}
            ],
        },
    )
    payload = api.invoke(agent["slug"], "List the workspace root with list_dir.")
    run = payload["run"]
    assert run["status"] == "success", run.get("error") or payload.get("reply")
    assert _tool_ran(run, "list_dir"), json.dumps(run.get("events"))[:600]


def test_live_http_agent_run_executes_tool(api: LiveClient, http_server):
    """HTTP: a real agent run calls a tool over streamable HTTP and uses the value."""
    agent = api.create_agent(
        "Live Http Transport",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": (
                "Call the http_probe_add tool with a=17 and b=25, then answer with the "
                "exact number the tool returned."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [{"server": http_server["name"], "tools": ["http_probe_add"]}],
        },
    )
    payload = api.invoke(agent["slug"], "Add 17 and 25 with the tool.")
    run = payload["run"]
    assert run["status"] == "success", run.get("error") or payload.get("reply")
    assert _tool_ran(run, "http_probe_add"), json.dumps(run.get("events"))[:600]
    reply = payload.get("reply") or run.get("final_reply") or ""
    assert str(HTTP_OK_MARKER) in reply, reply[:300]


def test_live_http_hitl_approval_roundtrip(api: LiveClient, http_server):
    """HTTP + HITL: approval pauses the graph, resume executes the remote tool."""
    agent = api.create_agent(
        "Live Http Hitl",
        {
            "kind": "agent",
            "runtime": "agent",
            "instructions": (
                "You MUST call http_probe_echo with text='APPROVED-HTTP-OK' before "
                "answering, then repeat exactly what it returned."
            ),
            "model": DEFAULT_MODEL,
            "default_options": SHORT,
            "mcp_bindings": [
                {
                    "server": http_server["name"],
                    "tools": ["http_probe_echo"],
                    "approval": ["http_probe_echo"],
                }
            ],
        },
    )
    first = api.http.post(
        f"{api.base}/api/v1/agents/{agent['slug']}/invoke",
        json={"input": "echo the marker", "stream": False},
        timeout=(10, 300),
    )
    assert first.status_code < 400, first.text[:500]
    body = first.json()
    assert body.get("status") == "awaiting_approval", body
    run_id = (body.get("run") or {}).get("public_id")
    assert run_id, body

    run = api.wait_for_run(str(run_id))
    assert run["status"] == "awaiting_approval", run.get("error")
    pending = run.get("pending_json") or {}
    assert pending.get("action_requests"), pending
    assert pending["action_requests"][0]["name"] == "http_probe_echo", pending

    resumed = api.http.post(
        f"{api.base}/api/v1/runs/{run_id}/approvals",
        json={"decisions": [{"type": "approve"}], "stream": False},
        timeout=(10, 300),
    )
    assert resumed.status_code == 200, resumed.text[:500]
    run = api.wait_for_run(str(run_id), timeout=180)
    assert run["status"] == "success", run.get("error")
    assert _tool_ran(run, "http_probe_echo"), json.dumps(run.get("events"))[:600]


def _tool_ran(run: dict, name: str) -> bool:
    from livehelpers import _tool_events

    return bool(_tool_events(run, name))
