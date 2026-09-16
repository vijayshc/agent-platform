"""Shared live-LLM test helpers (no test_ prefix: not collected).

Both live test modules (``test_live_orchestration_api.py`` and
``test_live_agent_platform.py``) run against a REAL running Flask app with the
REAL OpenRouter model and REAL Workspace MCP tools. Every assertion is
structural (status, event types, non-empty reply, tool rows) because model
output is non-deterministic.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("LIVE_AGENT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
USERNAME = os.environ.get("LIVE_AGENT_USER", "admin")
PASSWORD = os.environ.get("LIVE_AGENT_PASSWORD", "admin")
INVOKE_TIMEOUT = int(os.environ.get("LIVE_AGENT_TIMEOUT", "90"))

DEFAULT_MODEL = {"client": "default", "name": None}
RUN_COMMAND = {"server": "Workspace", "tools": ["run_command"], "approval": []}
WS_READ = {"server": "Workspace", "tools": ["list_dir", "read_file", "search_code"], "approval": []}
WS_WRITE = {"server": "Workspace", "tools": ["write_file", "list_dir"], "approval": ["write_file"]}
SHORT = {"temperature": 0.1, "max_tokens": 300}

def require_live_app() -> LiveClient:
    """Ping + login + create a key (module-scope fixture body)."""
    try:
        ping = requests.get(f"{BASE_URL}/login", timeout=5)
    except requests.RequestException as exc:
        pytest.fail(f"App is not running at {BASE_URL}: {exc}")
    if ping.status_code >= 500:
        pytest.fail(f"App at {BASE_URL} returned {ping.status_code}")
    client = LiveClient(BASE_URL)
    client.login(USERNAME, PASSWORD)
    client.create_api_key()
    return client


class _FlaskResponse:
    """requests-like wrapper around a Flask test-client response."""

    def __init__(self, resp):
        self._resp = resp
        self.status_code = resp.status_code
        self.text = resp.get_data(as_text=True)

    def json(self):
        return self._resp.get_json()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} {self.text[:400]}")

    def close(self) -> None:
        return None


class FlaskHttp:
    """requests.Session-shaped adapter so LiveClient can drive Flask's test client."""

    def __init__(self, client):
        self._client = client
        self.headers: dict[str, str] = {}

    def _path(self, url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path or url
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path

    def _hdrs(self) -> dict[str, str]:
        return dict(self.headers)

    def get(self, url, timeout=None, **_kw):
        return _FlaskResponse(self._client.get(self._path(url), headers=self._hdrs()))

    def post(self, url, json=None, data=None, timeout=None, stream=None, allow_redirects=None, **_kw):
        if data is not None:
            return _FlaskResponse(self._client.post(self._path(url), data=data, headers=self._hdrs()))
        return _FlaskResponse(self._client.post(self._path(url), json=json, headers=self._hdrs()))

    def delete(self, url, json=None, timeout=None, **_kw):
        return _FlaskResponse(self._client.delete(self._path(url), json=json, headers=self._hdrs()))


def flask_live_client(test_client, base: str = "http://unit") -> "LiveClient":
    """LiveClient bound to a Flask test client (same create/teardown as live tests)."""
    return LiveClient(base, http=FlaskHttp(test_client))


class LiveClient:
    def __init__(self, base: str, http=None):
        self.base = (base or "").rstrip("/")
        self.http = http or requests.Session()
        self.api_key: str | None = None
        self._created_agent_slugs: list[str] = []
        self._created_conversation_ids: list[str] = []
        self._created_key_ids: list[int] = []

    def _timeout(self, seconds) -> dict:
        if isinstance(self.http, requests.Session):
            return {"timeout": seconds}
        return {}

    def login(self, username: str, password: str) -> None:
        r = self.http.get(f"{self.base}/login", **self._timeout(15))
        r.raise_for_status()
        r = self.http.post(
            f"{self.base}/login",
            data={"username": username, "password": password},
            allow_redirects=True,
            **self._timeout(15),
        )
        if r.status_code >= 400:
            pytest.fail(f"login failed: {r.status_code} {r.text[:400]}")
        probe = self.http.get(f"{self.base}/api/v1/agents?include_drafts=1", **self._timeout(15))
        if probe.status_code == 401:
            pytest.fail(f"login did not establish a session ({USERNAME})")

    def create_api_key(self) -> None:
        r = self.http.post(
            f"{self.base}/api/v1/api-keys",
            json={
                "name": f"live-e2e-{uuid.uuid4().hex[:8]}",
                "scopes": ["agents:read", "agents:write", "runs:write", "runs:read"],
            },
            **self._timeout(15),
        )
        if r.status_code != 201:
            pytest.fail(f"create api key failed: {r.status_code} {r.text[:500]}")
        body = r.json()
        self.api_key = body["key"]
        kid = body.get("id")
        if kid is not None:
            self._created_key_ids.append(int(kid))
        self.http.headers["X-API-Key"] = self.api_key

    def create_workflow(self, name: str, config: dict) -> dict:
        row = self.create_agent(name, config, kind="workflow")
        return row

    def create_agent(self, name: str, config: dict, kind: str = "agent") -> dict:
        slug = f"live-{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}"
        body = {"name": name, "slug": slug, "kind": kind, "published": True, "config": config}
        r = self.http.post(f"{self.base}/api/v1/agents", json=body, **self._timeout(30))
        if r.status_code not in {200, 201}:
            pytest.fail(f"create agent failed: {r.status_code} {r.text[:800]}")
        row = r.json()
        created_slug = row.get("slug") or slug
        self._created_agent_slugs.append(str(created_slug))
        pub = self.http.post(
            f"{self.base}/api/v1/agents/{created_slug}/publish",
            json={"published": True},
            **self._timeout(30),
        )
        if pub.status_code >= 400:
            pytest.fail(f"publish failed: {pub.status_code} {pub.text[:800]}")
        return pub.json()

    def create_conversation(self, slug: str, title: str) -> dict:
        r = self.http.post(
            f"{self.base}/api/v1/conversations",
            json={"title": title, "agent_slug": slug, "agent_id": slug},
            **self._timeout(15),
        )
        if r.status_code not in {200, 201}:
            pytest.fail(f"create conversation failed: {r.status_code} {r.text[:500]}")
        row = r.json()
        cid = row.get("public_id") or row.get("id")
        if cid is not None:
            self._created_conversation_ids.append(str(cid))
        return row

    def teardown(self) -> dict[str, list[tuple[str, int, str]]]:
        """DELETE every agent / conversation / API key this client created.

        Order: conversations, then agents, then keys (later DELETEs still
        authenticate). Returns {kind: [(id, status, body), ...]}.
        """
        results: dict[str, list[tuple[str, int, str]]] = {
            "conversations": [],
            "agents": [],
            "keys": [],
        }
        for cid in list(self._created_conversation_ids):
            results["conversations"].append(self._delete(f"/api/v1/conversations/{cid}", cid))
        self._created_conversation_ids.clear()
        for slug in list(self._created_agent_slugs):
            results["agents"].append(self._delete(f"/api/v1/agents/{slug}", slug))
        self._created_agent_slugs.clear()
        for kid in list(self._created_key_ids):
            results["keys"].append(self._delete(f"/api/v1/api-keys/{kid}", str(kid)))
        self._created_key_ids.clear()
        return results

    def _delete(self, path: str, ident: str) -> tuple[str, int, str]:
        try:
            r = self.http.delete(f"{self.base}{path}", **self._timeout(15))
        except requests.RequestException as exc:
            return ident, 0, str(exc)
        return ident, r.status_code, (r.text or "")[:800]

    def list_agent_slugs(self, *, include_drafts: bool = True) -> set[str]:
        q = "?include_drafts=1" if include_drafts else ""
        r = self.http.get(f"{self.base}/api/v1/agents{q}", **self._timeout(15))
        if r.status_code >= 400:
            pytest.fail(f"list agents failed: {r.status_code} {r.text[:500]}")
        rows = (r.json() or {}).get("agents") or []
        return {str(a.get("slug")) for a in rows if a.get("slug")}

    def invoke(self, slug: str, task: str, model: dict | str | None = None, conversation_id: int | str | None = None) -> dict:
        started = time.time()
        body: dict = {"agent_id": slug, "input": task, "stream": True}
        if model is not None:
            body["model"] = model
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        r = self.http.post(
            f"{self.base}/api/v1/runs",
            json=body,
            timeout=(10, 30),
            stream=True,
        )
        if r.status_code >= 400:
            body = ""
            try:
                body = r.text[:1200]
            except Exception:
                body = str(r.status_code)
            pytest.fail(f"invoke {slug} failed: {r.status_code} {body}")
        public_id = None
        sse_events: list[dict] = []
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if time.time() - started > INVOKE_TIMEOUT:
                    break
                if not raw or not str(raw).startswith("data:"):
                    continue
                try:
                    event = json.loads(str(raw)[5:].strip())
                except json.JSONDecodeError:
                    continue
                sse_events.append(event)
                public_id = public_id or event.get("public_id")
                if event.get("type") == "done":
                    public_id = public_id or event.get("public_id")
                    break
                # Leave a rambling token stream; the worker keeps running. Poll GET /runs instead.
                if public_id and time.time() - started > 8:
                    break
        finally:
            r.close()
        if not public_id:
            pytest.fail(f"invoke {slug} produced no run id: {sse_events[:8]}")
        run = self.wait_for_run(str(public_id), timeout=max(15, INVOKE_TIMEOUT - int(time.time() - started)))
        return {"run": run, "reply": run.get("final_reply"), "sse": sse_events, "status": run.get("status")}

    def cancel(self, run_id: str) -> None:
        try:
            self.http.post(f"{self.base}/api/v1/runs/{run_id}/cancel", timeout=10)
        except requests.RequestException:
            pass

    def get_run(self, run_id: str) -> dict:
        r = self.http.get(f"{self.base}/api/v1/runs/{run_id}", timeout=30)
        if r.status_code >= 400:
            pytest.fail(f"get run failed: {r.status_code} {r.text[:800]}")
        return r.json()

    def wait_for_run(self, run_id: str, timeout: int | None = None) -> dict:
        deadline = time.time() + (timeout or INVOKE_TIMEOUT)
        last = None
        while time.time() < deadline:
            last = self.get_run(run_id)
            if last.get("status") not in {"running", "cancelling"}:
                return last
            time.sleep(1.0)
        self.cancel(run_id)
        pytest.fail(f"run {run_id} still {last.get('status') if last else None} after timeout")


def _reply(payload: dict, run: dict) -> str:
    return (payload.get("reply") or run.get("final_reply") or "") or ""


def _event_blob(run: dict) -> str:
    chunks = []
    for event in run.get("events") or []:
        chunks.append(json.dumps(event, default=str))
    chunks.append(run.get("final_reply") or "")
    chunks.append(run.get("error") or "")
    return "\n".join(chunks)


def _tool_events(run: dict, name: str) -> list[dict]:
    """Return events that actually executed/called ``name`` (not instruction text)."""
    found = []
    name_l = name.lower()
    tool_types = {"execute_tool", "tool_call", "tool_result"}
    for event in run.get("events") or []:
        et = (event.get("event_type") or "").lower()
        tool = (event.get("tool_name") or "").lower()
        if tool == name_l and (et in tool_types or et == "span"):
            found.append(event)
            continue
        if et not in tool_types:
            continue
        detail = event.get("detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        attrs = (detail.get("attributes") or {}) if isinstance(detail, dict) else {}
        if not isinstance(attrs, dict):
            attrs = {}
        gen_tool = str(attrs.get("gen_ai.tool.name") or "").lower()
        span = str((detail or {}).get("span_name") or event.get("span_name") or "").lower()
        if gen_tool == name_l or span.endswith(name_l) or span.endswith(f" {name_l}"):
            found.append(event)
    return found


def _agent_bases(run: dict, sse: list | None = None) -> set[str]:
    return {n.split(":")[0] for n in _agents_seen(run, sse) if n}


def _has_tool_named(run: dict, *names: str) -> bool:
    blob = _event_blob(run).lower()
    return any(name.lower() in blob for name in names)


def _tool_call_counts(run: dict) -> dict[str, int]:
    """Count actual tool_call events only (ignore tool_result duplicates)."""
    counts: dict[str, int] = {}
    for event in run.get("events") or []:
        et = (event.get("event_type") or event.get("type") or "").lower()
        if et != "tool_call":
            continue
        name = str(event.get("tool_name") or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _agents_seen(run: dict, sse: list | None = None) -> set[str]:
    names: set[str] = set()
    for event in run.get("events") or []:
        if event.get("agent_name"):
            names.add(str(event["agent_name"]))
        detail = event.get("detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        span = ""
        if isinstance(detail, dict):
            span = str(detail.get("span_name") or "")
        if span.startswith("invoke_agent "):
            names.add(span.replace("invoke_agent ", "", 1))
    for event in sse or []:
        if event.get("agent"):
            names.add(str(event["agent"]))
    names.discard("")
    names.discard("None")
    return names


def _refetch(api: LiveClient, run_id: str) -> dict:
    time.sleep(1.2)
    return api.get_run(str(run_id))


def _invoke_counts(run: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in run.get("events") or []:
        if event.get("event_type") == "invoke_agent":
            agent = event.get("agent_name") or "unknown"
            counts[agent] = counts.get(agent, 0) + 1
        detail = event.get("detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except Exception:
                detail = {}
        span = str((detail or {}).get("span_name") or "") if isinstance(detail, dict) else ""
        if span.startswith("invoke_agent "):
            agent = span.replace("invoke_agent ", "", 1)
            counts[agent] = counts.get(agent, 0) + 1
    return counts
