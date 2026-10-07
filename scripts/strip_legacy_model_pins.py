"""One-off migration: strip legacy agent->model pins.

Agent-to-model linking was removed from Studio: the run-level (chat) model
picker owns the model and every saved definition must follow it
(``config.model == {"client": "default"}``). Definitions saved before the
removal still carry ``model.client == "<connection-id>"`` (top level, studio
canvas data, participants and graph nodes), which silently overrides the
caller's model pick at run time (see ``compile_model_client`` precedence).

Running this script rewrites those pins to ``"default"`` in place. Safe to
re-run: rows that already follow the run are left untouched.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "text2sql.db"

DEFAULT_CLIENTS = {"", "default"}


def _is_pin(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() not in DEFAULT_CLIENTS


def _strip_holder(holder: dict, changed: list[str], where: str) -> None:
    model = holder.get("model")
    if isinstance(model, dict) and _is_pin(model.get("client")):
        changed.append(f"{where}: model.client {model.get('client')!r} -> 'default'")
        holder["model"] = {"client": "default", "name": None}


def _strip_config(config: dict) -> list[str]:
    changed: list[str] = []
    _strip_holder(config, changed, "top-level")
    for key in ("manager", "aggregator", "manager_agent"):
        value = config.get(key)
        if isinstance(value, dict):
            _strip_holder(value, changed, key)
    for key in ("participants", "nodes"):
        for i, spec in enumerate(config.get(key) or []):
            if isinstance(spec, dict):
                _strip_holder(spec, changed, f"{key}[{i}]")
    graph = config.get("graph")
    if isinstance(graph, dict):
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            agent = node.get("agent")
            if isinstance(agent, dict):
                _strip_holder(agent, changed, f"graph node {node.get('id')}")
            elif isinstance(node.get("model"), dict):
                _strip_holder(node, changed, f"graph node {node.get('id')}")
    studio = config.get("studio")
    if isinstance(studio, dict):
        for node in studio.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            data = node.get("data")
            if not isinstance(data, dict):
                continue
            if _is_pin(data.get("modelClient")):
                changed.append(
                    f"studio node {node.get('id')}: "
                    f"modelClient {data.get('modelClient')!r} -> 'default'"
                )
                data["modelClient"] = "default"
                data["modelName"] = ""
            model = data.get("model")
            if isinstance(model, dict) and _is_pin(model.get("client")):
                changed.append(f"studio node {node.get('id')}: data.model pin dropped")
                data.pop("model", None)
    return changed


def main() -> int:
    if not DB.exists():
        print(f"DB not found: {DB}", file=sys.stderr)
        return 1
    backup = DB.with_suffix(".db.pre-unpin-bak")
    shutil.copy2(DB, backup)
    print(f"backup -> {backup}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, slug, config FROM agent_definitions").fetchall()
    total = 0
    for row in rows:
        try:
            config = json.loads(row["config"]) if isinstance(row["config"], str) else dict(row["config"] or {})
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"skip id={row['id']} slug={row['slug']}: unparsable config ({exc})")
            continue
        if not isinstance(config, dict):
            continue
        changed = _strip_config(config)
        if not changed:
            continue
        con.execute(
            "UPDATE agent_definitions SET config = ? WHERE id = ?",
            (json.dumps(config), row["id"]),
        )
        total += 1
        print(f"id={row['id']} slug={row['slug']}:")
        for line in changed:
            print(f"    {line}")
    con.commit()
    con.close()
    print(f"done: {total} definition(s) unpinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
