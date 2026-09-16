"""What the agent sandbox actually provides.

Agents otherwise discover missing tooling the expensive way: they probe, fail,
improvise a workaround, and burn turns re-testing what a single line of context
would have told them. This probes once per process and states the facts.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.agent_platform.paths import APP_ROOT


def _node_has(pkg: str, node_modules: Path) -> bool:
    """Whether `node` can resolve `pkg` the same way an agent's command would."""
    if not node_modules.is_dir():
        return False
    node = shutil.which("node")
    if not node:
        return False
    env = dict(os.environ)
    env["NODE_PATH"] = str(node_modules)
    try:
        result = subprocess.run(
            [node, "-e", f"require.resolve({pkg!r})"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _python_has(module: str) -> bool:
    """Probe with the *application* interpreter -- the one agents actually get."""
    env = dict(os.environ)
    extra = APP_ROOT / ".agent-python"
    if extra.is_dir():
        env["PYTHONPATH"] = os.pathsep.join([str(extra), env.get("PYTHONPATH", "")]).strip(os.pathsep)
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            timeout=25,
            env=env,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@functools.lru_cache(maxsize=1)
def sandbox_capabilities() -> dict[str, object]:
    node_modules = APP_ROOT / ".agent-node" / "node_modules"
    return {
        "node": shutil.which("node") is not None,
        "npm": shutil.which("npm") is not None,
        "node_packages": [p for p in ("pptxgenjs", "react-icons", "sharp") if _node_has(p, node_modules)],
        "python_pptx": _python_has("pptx"),
        "pillow": _python_has("PIL"),
        "fonttools": _python_has("fontTools"),
        "markitdown": _python_has("markitdown"),
        "libreoffice": bool(shutil.which("soffice") or shutil.which("libreoffice")),
        "pdftoppm": shutil.which("pdftoppm") is not None,
        "imagemagick": shutil.which("convert") is not None,
    }


def capabilities_note() -> str:
    """A short, factual statement of the sandbox for the system prompt."""
    caps = sandbox_capabilities()
    have: list[str] = []
    missing: list[str] = []

    if caps["python_pptx"]:
        have.append("python-pptx")
    if caps["pillow"]:
        have.append("Pillow")
    if caps["fonttools"]:
        have.append("fontTools")
    if caps["markitdown"]:
        have.append("markitdown")
    if caps["node"]:
        pkgs = ", ".join(caps["node_packages"])  # type: ignore[arg-type]
        have.append(f"node/npm with {pkgs}" if pkgs else "node/npm")
    if caps["npm"]:
        have.append("a writable npm cache if you truly need a package")

    if not caps["libreoffice"]:
        missing.append("LibreOffice (`soffice`) — no PDF/PNG rendering of office files")
    if not caps["pdftoppm"]:
        missing.append("`pdftoppm` (poppler) — no PDF rasterising")
    if not caps["imagemagick"]:
        missing.append("ImageMagick (`convert`/`magick`) — no image conversion")

    lines = ["\n\n## Sandbox environment", "", "Available: " + ("; ".join(have) if have else "nothing detected") + "."]
    if missing:
        lines.append(
            "Not installed: "
            + "; ".join(missing)
            + ". Do not spend turns probing for these or trying to install them — "
            "verify your work with the tools that are present."
        )
    fonts = sorted(p.name for p in Path("/usr/share/fonts/truetype").glob("*") if p.is_dir()) if Path("/usr/share/fonts/truetype").is_dir() else []
    if fonts:
        lines.append(
            "Fonts installed: " + ", ".join(fonts) + ". Others (Calibri, Cambria, Arial) are absent — "
            "pick from these or let the renderer substitute; do not search the filesystem for them."
        )
    if caps["node_packages"]:
        lines.append(
            "The node packages above are pre-provisioned and resolve without installing — do not "
            "run `npm install` and do not create `node_modules` in the workspace."
        )
    lines.append(
        "Only the workspace and `@skills/<name>/` are yours to read. Do not scan the wider "
        "filesystem (`find /`, `ls /`) — it is large, slow, and such commands time out."
    )
    lines.append(
        "Files you create belong in the workspace root and are addressed relatively "
        "(for example `deck.pptx`). Skill scripts are run through the `@skills/<name>/` alias."
    )
    return "\n".join(lines)
