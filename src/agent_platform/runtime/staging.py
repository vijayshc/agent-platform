"""Workspace attachment staging and run prompt formatting."""

from pathlib import Path
import shutil
from typing import Any, List, Optional


def stage_attachments(attachments: List[dict[str, Any]], workspace_dir: str) -> List[dict[str, Any]]:
    """Stage run attachments into the workspace directory."""
    out = []
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    for att in attachments:
        src = att.get("path") or att.get("stored_path")
        name = att.get("name") or att.get("filename") or (Path(src).name if src else "attachment")
        dst = ws / name
        if src and Path(src).exists() and not dst.exists():
            shutil.copy2(src, dst)
        row = dict(att)
        row["workspace_path"] = str(dst)
        out.append(row)
    return out


def build_run_prompt(input_text: str, attachments: Optional[List[dict[str, Any]]] = None) -> str:
    """Format input text and staged attachments into a combined run prompt."""
    text = (input_text or "").strip()
    if not attachments:
        return text
    parts = [text] if text else []
    parts.append("\n\nAttachments:")
    for att in attachments:
        name = att.get("name") or att.get("filename") or "file"
        path = att.get("workspace_path") or att.get("path")
        parts.append(f"- {name} (saved at {path})")
    return "\n".join(parts)


__all__ = [
    "stage_attachments",
    "build_run_prompt",
]
