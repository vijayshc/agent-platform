#!/usr/bin/env python3
"""Provision and verify the Agent Studio sample catalogue on the live app.

Upserts every definition from :mod:`sample_agents_catalog` (PUT when the slug
exists, POST when it does not), publishes and validates it, then invokes it for
real through ``POST /api/v1/runs`` (SSE). A sample passes only when the run
finished and the evidence it declares -- tool calls, agent switches, router
branch, fan-out width, loop count, structured keys, HITL approval -- is present
in the run. HITL pauses are detected from the ``approval_request`` event and
resumed through ``POST /api/v1/runs/<id>/approvals`` with
``{"decisions": [{"type": "approve"}]}``.

    ~/anaconda3/bin/python3 scripts/provision_live_usecase_agents.py
    ~/anaconda3/bin/python3 scripts/provision_live_usecase_agents.py --only deep-scout,change-clerk

Auth: ``X-API-Key`` from ``$LIVE_AGENT_API_KEY`` or ``temp/devkey``, falling
back to a session login (LIVE_AGENT_USER / LIVE_AGENT_PASSWORD).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_agents_catalog import catalog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("LIVE_AGENT_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
USER = os.environ.get("LIVE_AGENT_USER", "admin")
PASSWORD = os.environ.get("LIVE_AGENT_PASSWORD", "admin")
DEVKEY = ROOT / "temp" / "devkey"
DEFAULT_TIMEOUT = int(os.environ.get("LIVE_AGENT_TIMEOUT", "240"))
APPROVE_BODY = {"decisions": [{"type": "approve"}]}


# --------------------------------------------------------------------- client
class Client:
    """Thin live-API client: catalog CRUD, validation, plan, runs, approvals."""

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        self.http = requests.Session()
        key = os.environ.get("LIVE_AGENT_API_KEY") or (
            DEVKEY.read_text(encoding="utf-8").strip() if DEVKEY.is_file() else ""
        )
        if key:
            self.http.headers["X-API-Key"] = key
            probe = self.http.get(f"{BASE}/api/v1/agents?include_drafts=1", timeout=20)
            if probe.status_code == 200:
                return
            print(f"api key rejected ({probe.status_code}); falling back to session login")
        self._login()

    def _login(self) -> None:
        self.http.get(f"{BASE}/login", timeout=15)
        r = self.http.post(
            f"{BASE}/login", data={"username": USER, "password": PASSWORD}, timeout=20
        )
        if r.status_code >= 400:
            raise SystemExit(f"login failed: {r.status_code}")
        probe = self.http.get(f"{BASE}/api/v1/agents?include_drafts=1", timeout=20)
        if probe.status_code != 200:
            raise SystemExit(f"login did not grant studio access: {probe.status_code}")

    # ------------------------------------------------------------- catalog
    def upsert(self, spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
        slug = spec["slug"]
        body = {
            "name": spec["name"],
            "slug": slug,
            "kind": spec["kind"],
            "published": True,
            "config": spec["config"],
        }
        exists = self.http.get(f"{BASE}/api/v1/agents/{slug}", timeout=20).status_code == 200
        url = f"{BASE}/api/v1/agents/{slug}" if exists else f"{BASE}/api/v1/agents"
        r = self.http.put(url, json=body, timeout=60) if exists else self.http.post(
            url, json=body, timeout=60
        )
        if r.status_code not in {200, 201}:
            raise RuntimeError(f"upsert {slug} failed: {r.status_code} {r.text[:400]}")
        row = r.json()
        pub = self.http.post(
            f"{BASE}/api/v1/agents/{row.get('slug') or slug}/publish",
            json={"published": True},
            timeout=60,
        )
        if pub.status_code >= 400:
            raise RuntimeError(f"publish {slug} failed: {pub.status_code} {pub.text[:400]}")
        return pub.json(), ("updated" if exists else "created")

    def validate(self, slug: str) -> dict[str, Any]:
        """Both report shapes: ``{ok, errors, compile}`` and legacy ``{valid, ...}``."""
        r = self.http.post(f"{BASE}/api/v1/agents/{slug}/validate", timeout=120)
        if r.status_code >= 400:
            return {"ok": False, "errors": [f"http {r.status_code}: {r.text[:300]}"]}
        data = r.json()
        ok = data.get("ok")
        if ok is None:
            ok = data.get("valid")
        return {
            "ok": bool(ok),
            "errors": data.get("errors") or [],
            "warnings": data.get("warnings") or [],
            "compile": data.get("compile") or {},
        }

    def plan(self, spec: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(
            f"{BASE}/api/v1/studio/plan",
            json={"name": spec["name"], "kind": spec["kind"], "config": spec["config"]},
            timeout=60,
        )
        return r.json().get("plan") or {} if r.status_code == 200 else {}

    def slugs(self) -> set[str]:
        r = self.http.get(f"{BASE}/api/v1/agents?include_drafts=1", timeout=30)
        r.raise_for_status()
        return {str(a.get("slug")) for a in r.json().get("agents") or []}

    # ---------------------------------------------------------------- runs
    def get_run(self, run_id: str) -> dict[str, Any]:
        r = self.http.get(f"{BASE}/api/v1/runs/{run_id}", timeout=60)
        r.raise_for_status()
        return r.json()

    def invoke(self, slug: str, task: str) -> dict[str, Any]:
        """Run one definition; returns run record, SSE events, latency, approvals."""
        started = time.time()
        sse: list[dict[str, Any]] = []
        r = self.http.post(
            f"{BASE}/api/v1/runs",
            json={"agent_id": slug, "input": task, "stream": True},
            stream=True,
            timeout=(20, self.timeout + 60),
        )
        if r.status_code >= 400:
            raise RuntimeError(f"invoke {slug} failed: {r.status_code} {r.text[:400]}")
        public_id: str | None = None
        timed_out = False
        try:
            for raw in r.iter_lines(decode_unicode=True):
                if time.time() - started > self.timeout:
                    timed_out = True
                    break
                if not raw or not str(raw).startswith("data:"):
                    continue
                try:
                    event = json.loads(str(raw)[5:].strip())
                except json.JSONDecodeError:
                    continue
                public_id = public_id or event.get("public_id")
                sse.append(event)
                if event.get("type") in {"done", "error"}:
                    break
        finally:
            r.close()
        if not public_id:
            raise RuntimeError(f"invoke {slug} produced no run id")
        if timed_out:
            self.http.post(f"{BASE}/api/v1/runs/{public_id}/cancel", timeout=20)

        run = self.get_run(public_id)
        approvals: list[dict[str, Any]] = []
        if run.get("status") == "awaiting_approval" or _has_approval(sse):
            approvals = self.resolve_approval(public_id, run)
            run = self.get_run(public_id)
        return {
            "public_id": public_id,
            "run": run,
            "sse": sse,
            "approvals": approvals,
            "latency": round(time.time() - started, 1),
            "timed_out": timed_out,
        }

    def resolve_approval(self, run_id: str, run: dict[str, Any]) -> list[dict[str, Any]]:
        """Approve the pending action request(s) and run to completion."""
        pending = run.get("pending_json")
        if isinstance(pending, str):
            try:
                pending = json.loads(pending or "{}")
            except json.JSONDecodeError:
                pending = {}
        actions = [
            a for a in ((pending or {}).get("action_requests") or []) if isinstance(a, dict)
        ]
        decisions = [dict(APPROVE_BODY["decisions"][0]) for _ in actions] or list(
            APPROVE_BODY["decisions"]
        )
        r = self.http.post(
            f"{BASE}/api/v1/runs/{run_id}/approvals",
            json={"decisions": decisions},
            timeout=self.timeout + 60,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"approval {run_id} failed: {r.status_code} {r.text[:400]}")
        payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return list(payload.get("events") or []) + [
            {"type": "__resume__", "status": payload.get("status"), "actions": actions}
        ]


# ------------------------------------------------------------------- evidence
def _has_approval(events: list[dict[str, Any]]) -> bool:
    return any(e.get("type") == "approval_request" for e in events)


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten SSE events and persisted span events into one evidence list."""
    out: list[dict[str, Any]] = []
    for event in list(payload.get("sse") or []) + list(payload.get("approvals") or []):
        out.append(
            {
                "type": event.get("type"),
                "agent": event.get("agent") or event.get("agent_name"),
                "tool": event.get("tool_name"),
                "message": str(event.get("message") or ""),
                "content": str(event.get("content") or ""),
                "intermediate": bool(event.get("intermediate")),
                "span": str(event.get("agent") or ""),
                "raw": event,
            }
        )
    for event in (payload.get("run") or {}).get("events") or []:
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        out.append(
            {
                "type": event.get("event_type"),
                "agent": event.get("agent_name"),
                "tool": event.get("tool_name"),
                "message": f"{detail.get('message') or ''} {event.get('span_name') or ''}",
                "content": str(detail.get("content") or ""),
                "intermediate": bool(detail.get("intermediate")),
                "span": str(event.get("agent_name") or ""),
                "raw": event,
            }
        )
    return out


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen and value not in {"model", "tools", "agent"}:
            seen.append(value)
    return seen


def _norm_agent(name: Any) -> str:
    """A display name for an agent: span names arrive as ``node:<uuid>``."""
    text = str(name or "").strip()
    if text.startswith("invoke_agent "):
        text = text[len("invoke_agent ") :]
    text = text.split(":", 1)[0].strip()
    if "middleware" in text.lower():
        return ""
    return text


def _tools(records: list[dict[str, Any]]) -> list[str]:
    return _unique([str(r["tool"]) for r in records if r["type"] in {"tool_call", "tool_result"}])


def _agents(records: list[dict[str, Any]]) -> list[str]:
    names = [
        _norm_agent(r["agent"])
        for r in records
        if r["type"] in {"agent_switch", "invoke_agent", "chat"} and r["agent"]
    ]
    return _unique([n for n in names if n])


def _routes(records: list[dict[str, Any]]) -> list[str]:
    picks: list[str] = []
    for record in records:
        for match in re.finditer(r"→\s*([A-Za-z0-9_\-]+)", str(record["message"])):
            picks.append(match.group(1))
    return _unique(picks)


def _turns(records: list[dict[str, Any]], agent: str) -> int:
    """How many times an agent ran: fan-out branches, or turns inside one graph run.

    A ``Send`` fan-out gives every branch its own subgraph instance
    (``worker:<uuid>``), so distinct instance ids are the fan-out width. Inside
    one instance (an evaluator-optimizer loop) the count is the distinct complete
    messages the agent produced.
    """
    name = _norm_agent(agent).lower()
    own = [r for r in records if _norm_agent(r["agent"]).lower() == name]
    instances = {r["span"] for r in own if r["type"] == "agent_switch" and ":" in r["span"]}
    contents = {
        r["content"].strip()
        for r in own
        if r["type"] == "chat" and not r["intermediate"] and r["content"].strip()
    }
    return max(len(instances), len(contents))


def _reply_text(payload: dict[str, Any]) -> str:
    """Only what the agent said: the final reply plus streamed chat/token content."""
    parts = [str((payload.get("run") or {}).get("final_reply") or "")]
    for event in list(payload.get("sse") or []) + list(payload.get("approvals") or []):
        if event.get("type") in {"chat", "token"}:
            parts.append(str(event.get("content") or event.get("delta") or ""))
    return "\n".join(parts)


def _blob(payload: dict[str, Any]) -> str:
    run = payload.get("run") or {}
    parts = [run.get("final_reply") or "", run.get("error") or ""]
    parts.extend(json.dumps(e, default=str) for e in (payload.get("sse") or []))
    parts.extend(json.dumps(e, default=str) for e in (payload.get("approvals") or []))
    parts.extend(json.dumps(e, default=str) for e in (run.get("events") or []))
    return "\n".join(parts)


def _json_objects(blob: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for match in re.finditer(r"\{[^{}]*\}", blob):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def verify(spec: dict[str, Any], payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check the declared evidence against the run; returns (ok, evidence lines)."""
    expect = spec["expect"]
    run = payload["run"]
    records = _records(payload)
    tools, agents, routes = _tools(records), _agents(records), _routes(records)
    blob = _blob(payload).lower()
    reply = str(run.get("final_reply") or "").strip()
    failures: list[str] = []
    evidence = [
        f"status={run.get('status')} tools={tools or '-'} agents={agents or '-'}"
    ]
    if routes:
        evidence.append(f"router: {' → '.join(routes)}")

    if expect.get("status") and run.get("status") != expect["status"]:
        failures.append(f"status={run.get('status')} (want {expect['status']}) err={(run.get('error') or '')[:200]}")
    for tool in expect.get("tools") or []:
        if tool.lower() not in [t.lower() for t in tools]:
            failures.append(f"missing tool call: {tool}")
    for agent in expect.get("agents") or []:
        wanted = _norm_agent(agent).lower()
        seen = {_norm_agent(a).lower() for a in _agents(records)}
        if wanted not in seen:
            failures.append(f"missing agent: {agent}")
    for agent in expect.get("absent_agents") or []:
        wanted = _norm_agent(agent).lower()
        if wanted in {a.lower() for a in agents + routes}:
            failures.append(f"agent should not have run: {agent}")
    for text in expect.get("text") or []:
        if text.lower() not in blob:
            failures.append(f"missing text marker: {text!r}")
    for text in expect.get("absent_text") or []:
        if text.lower() in _reply_text(payload).lower():
            failures.append(f"agent repeated text that should be absent: {text!r}")
    if expect.get("route"):
        wanted = str(expect["route"]).lower()
        if not any(wanted == r.lower() for r in routes):
            failures.append(f"router picked {routes or 'nothing'} (want {wanted})")
    for wanted in expect.get("routes") or []:
        if not any(str(wanted).lower() == r.lower() for r in routes):
            failures.append(f"router never picked {wanted} (picked {routes or 'nothing'})")
    if expect.get("loop"):
        want = expect["loop"]
        turns = _turns(records, want["agent"])
        evidence.append(f"{want['agent']} turns={turns}")
        if turns < int(want.get("min", 2)):
            failures.append(f"{want['agent']} ran {turns}x (want >= {want['min']})")
    if expect.get("fanout"):
        want = expect["fanout"]
        turns = _turns(records, want["agent"])
        evidence.append(f"fan-out {want['agent']} runs={turns}")
        if turns < int(want.get("min", 2)):
            failures.append(f"{want['agent']} ran {turns}x (want >= {want['min']})")
    if expect.get("structured"):
        hit = any(
            all(key in obj for key in expect["structured"]) for obj in _json_objects(blob)
        )
        evidence.append(f"structured keys={expect['structured']} found={hit}")
        if not hit:
            failures.append(f"structured output without keys {expect['structured']}")
    if expect.get("hitl"):
        tool = str(expect["hitl"]).lower()
        approved = [e for e in (payload.get("approvals") or []) if e.get("type") == "__resume__"]
        pauses = [
            e for e in (payload.get("sse") or []) + (payload.get("approvals") or [])
            if e.get("type") == "approval_request"
        ]
        requested = [
            str(a.get("name") or "")
            for pause in pauses
            for a in (pause.get("action_requests") or [])
        ]
        ran = tool in [t.lower() for t in tools]
        evidence.append(f"approval pauses={len(pauses)} requested={requested} decisions=approve tool_ran={ran}")
        if not pauses:
            failures.append("no approval_request event was emitted")
        if requested and tool not in [r.lower() for r in requested]:
            failures.append(f"approval was for {requested}, not {tool}")
        if not approved:
            failures.append("run was not resumed through /approvals")
        if not ran:
            failures.append(f"{tool} never ran after approval")
    for rel in expect.get("files") or []:
        files = [str(f).lower() for f in run.get("workspace_files") or []]
        if rel.lower() not in files:
            failures.append(f"workspace file missing: {rel}")
        else:
            evidence.append(f"file written: {rel}")
    if reply:
        evidence.append(f"reply: {reply[:140].replace(chr(10), ' ')}")
    return not failures, evidence + [f"FAIL: {f}" for f in failures]


def _empty_reply(payload: dict[str, Any]) -> bool:
    run = payload.get("run") or {}
    return run.get("status") == "success" and not _reply_text(payload).strip()


def verify_plan(spec: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """Static provenance check: the OOTB builder and capabilities that will run."""
    want = spec["expect"].get("plan") or {}
    if not want:
        return []
    failures: list[str] = []
    if want.get("builder") and plan.get("builder") != want["builder"]:
        failures.append(f"builder={plan.get('builder')} (want {want['builder']})")
    have = {str(m) for m in plan.get("middleware") or []}
    missing = [m for m in want.get("middleware") or [] if m not in have]
    if missing:
        failures.append(f"plan middleware missing {missing}")
    for key in ("subagents", "memory"):
        for item in want.get(key) or []:
            if item not in (plan.get(key) or []):
                failures.append(f"plan {key} missing {item}")
    return failures


# --------------------------------------------------------------------- report
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="comma-separated slugs to run")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per run")
    parser.add_argument("--wait", type=int, default=120, help="seconds to wait for the app")
    args = parser.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    specs = [s for s in catalog() if not only or s["slug"] in only]
    if not specs:
        print(f"no samples match --only {args.only}")
        return 1

    # The app is supervised outside this script: wait for it rather than fail.
    deadline = time.time() + args.wait
    while True:
        try:
            ping = requests.get(f"{BASE}/login", timeout=10)
            if ping.status_code < 500:
                break
            print(f"app at {BASE} unhealthy ({ping.status_code}); waiting")
        except requests.RequestException as exc:
            print(f"app at {BASE} unreachable ({exc.__class__.__name__}); waiting")
        if time.time() >= deadline:
            print(f"app did not come up at {BASE} within {args.wait}s", file=sys.stderr)
            return 1
        time.sleep(5)

    client = Client(args.timeout)
    print(f"live agent API: {BASE}  ({len(specs)} samples)\n")

    results: list[tuple[str, str, bool, str, str, float]] = []
    validated = 0
    for index, spec in enumerate(specs, start=1):
        slug = spec["slug"]
        print(f"[{index:02d}/{len(specs):02d}] {slug} — {spec['name']} ({spec['group']})")
        action = "?"
        try:
            _, action = client.upsert(spec)
            validation = client.validate(slug)
            plan = client.plan(spec)
            valid = bool(validation.get("ok"))
            validated += 1 if valid else 0
            compile_info = validation.get("compile") or {}
            print(f"       {action}, published, validate={'ok' if valid else 'FAILED'}"
                  + (f" ({compile_info.get('kind')}, {compile_info.get('nodes')} nodes)"
                     if valid and compile_info else ""))
            if not valid:
                raise RuntimeError(f"validation failed: {validation.get('errors')}")
            if validation.get("warnings"):
                print(f"       warnings: {validation['warnings']}")
            plan_failures = verify_plan(spec, plan)
            if plan_failures:
                raise RuntimeError("plan check failed: " + "; ".join(plan_failures))
            payload = client.invoke(slug, spec["task"])
            ok, evidence = verify(spec, payload)
            attempts = 1
            # The provider occasionally returns an empty completion. That is a run
            # flake, not a bad definition, so retry once before reporting a failure.
            while not ok and attempts < 2 and _empty_reply(payload):
                print("       retry: the model returned an empty completion")
                payload = client.invoke(slug, spec["task"])
                ok, evidence = verify(spec, payload)
                attempts += 1
            if payload.get("timed_out"):
                ok = False
                evidence.append(f"TIMEOUT after {args.timeout}s")
            status = str((payload.get("run") or {}).get("status"))
            latency = float(payload.get("latency") or 0.0)
            print(f"       run={payload['public_id']} status={status} latency={latency}s"
                  + (f" attempts={attempts}" if attempts > 1 else ""))
            for line in evidence:
                print(f"       - {line}")
            marker = evidence[0] if evidence else ""
            results.append((slug, status, ok, marker, payload["public_id"], latency))
        except Exception as exc:  # one broken sample must not hide the others
            print(f"       ERROR: {exc}")
            results.append((slug, "error", False, str(exc)[:160], "-", 0.0))
        print()

    print("=" * 78)
    print(f"{'slug':<14} {'kind':<9} {'status':<17} {'marker':<6} {'latency':>8}  run")
    failures = 0
    for spec, (slug, status, ok, marker, run_id, latency) in zip(specs, results):
        failures += 0 if ok else 1
        print(
            f"{slug:<14} {spec['kind']:<9} {status:<17} "
            f"{'ok' if ok else 'MISS':<6} {latency:>7.1f}s  {run_id}"
        )
    listing = ""
    try:
        present = client.slugs()
        missing = [s["slug"] for s in specs if s["slug"] not in present]
        listing = (
            f"GET /api/v1/agents?include_drafts=1 -> {len(specs) - len(missing)}/{len(specs)} "
            "sample slugs present" + (f" (missing: {missing})" if missing else "")
        )
    except requests.RequestException as exc:
        missing = []
        listing = f"GET /api/v1/agents?include_drafts=1 -> unavailable while reporting ({exc.__class__.__name__})"
    print("=" * 78)
    print(f"{len(results) - failures}/{len(results)} passed")
    print(listing)
    print(f"POST /api/v1/agents/<slug>/validate -> {validated}/{len(results)} valid")
    return 1 if failures or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
