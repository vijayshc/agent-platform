"""Files an agent produced inside a conversation workspace.

Chat runs share one directory per conversation (``uploads/agent-workspace/conv-<id>``,
see ``paths.run_workspace_dir``). Agents write real artifacts there -- decks,
reports, generated code -- so the chat surface needs a safe way to list them and
hand them back to the user.

Every path from the client is resolved against that single root and refused if it
escapes, so the download endpoint cannot be used to read arbitrary files.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, g, jsonify, request, send_file

from src.agent_platform.api.auth import api_auth_required, current_user_id
from src.agent_platform.api.api_helpers import can_access_conversation
from src.agent_platform.paths import conversation_workspace_dir
from src.agent_platform.runtime.workspace import produced_files

workspace_bp = Blueprint("conversation_workspace", __name__)

MAX_LISTED_FILES = 500
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ipynb_checkpoints"}
_SKIP_FILES = {".DS_Store", "Thumbs.db"}


@workspace_bp.get("/conversations/<conversation_id>/files")
@api_auth_required("runs:read")
def list_conversation_files(conversation_id: str):
    conv = _conversation_or_404(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    denied = _denied(conv)
    if denied:
        return denied

    root = conversation_workspace_dir(int(conv["id"]))
    files: list[dict[str, Any]] = []
    truncated = False
    # Only what the agent produced: the seeded scaffold is not output.
    produced = set(produced_files(root)) if root.is_dir() else set()
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if len(files) >= MAX_LISTED_FILES:
                truncated = True
                break
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if rel.as_posix() not in produced:
                continue
            if rel.name.startswith("."):
                continue
            if any(part in _SKIP_DIRS for part in rel.parts) or rel.name in _SKIP_FILES:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append({
                "path": rel.as_posix(),
                "name": rel.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "kind": _kind_of(rel.name),
            })
    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return jsonify({
        "files": files,
        "truncated": truncated,
        "workspace": str(root),
        "total": len(files),
    })


@workspace_bp.get("/conversations/<conversation_id>/files/download")
@api_auth_required("runs:read")
def download_conversation_file(conversation_id: str):
    conv = _conversation_or_404(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    denied = _denied(conv)
    if denied:
        return denied

    raw = (request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "path is required"}), 400
    root = conversation_workspace_dir(int(conv["id"])).resolve()
    target = (root / raw.replace("\\", "/")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return jsonify({"error": "path escapes the conversation workspace"}), 400
    if not target.is_file():
        return jsonify({"error": "file not found"}), 404
    size = target.stat().st_size
    if size > MAX_DOWNLOAD_BYTES:
        return jsonify({"error": "file is too large to download"}), 413

    guessed = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    inline = (request.args.get("inline") or "").lower() in {"1", "true", "yes"}
    return send_file(
        target,
        mimetype=guessed,
        as_attachment=not inline,
        download_name=target.name,
        conditional=True,
    )


@workspace_bp.delete("/conversations/<conversation_id>/files")
@api_auth_required("runs:write")
def delete_conversation_file(conversation_id: str):
    conv = _conversation_or_404(conversation_id)
    if conv is None:
        return jsonify({"error": "conversation not found"}), 404
    denied = _denied(conv)
    if denied:
        return denied

    raw = (request.args.get("path") or "").strip()
    if not raw:
        return jsonify({"error": "path is required"}), 400
    root = conversation_workspace_dir(int(conv["id"])).resolve()
    target = (root / raw.replace("\\", "/")).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return jsonify({"error": "path escapes the conversation workspace"}), 400
    if target == root or not target.exists():
        return jsonify({"error": "file not found"}), 404
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
    else:
        target.unlink()
    _prune_empty_parents(target.parent, root)
    return jsonify({"success": True, "path": raw})


def _conversation_or_404(conversation_id: str) -> dict | None:
    from src.agent_platform.conversations.store import ConversationStore

    return ConversationStore.get(conversation_id)


def _denied(conv: dict):
    if can_access_conversation(conv, current_user_id()):
        return None
    g.audit_reason = "no access to this conversation"
    return jsonify({"error": "forbidden", "message": "You do not have access to this conversation"}), 403


def _prune_empty_parents(start: Path, root: Path) -> None:
    current = start
    while current != root and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def _kind_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext in {"pptx", "ppt", "docx", "doc", "xlsx", "xls", "pdf"}:
        return "document"
    if ext in {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"}:
        return "image"
    if ext in {"csv", "tsv", "json", "yaml", "yml", "xml", "parquet"}:
        return "data"
    if ext in {"zip", "gz", "tar", "tgz"}:
        return "archive"
    if ext in {"md", "txt", "rst", "log"}:
        return "text"
    if ext in {"py", "js", "ts", "tsx", "jsx", "sh", "sql", "html", "css", "java", "go", "rs", "rb"}:
        return "code"
    return "file"
