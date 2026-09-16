"""Workspace MCP (stdio). Cwd = AGENT_WORKSPACE. run_command uses sys.executable."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
for _h in logging.root.handlers:
    _h.setStream(sys.stderr)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("workspace")

# Long-running builds are legitimate; a stuck command is not. Bounded so a hung
# shell cannot pin a run forever.
COMMAND_TIMEOUT_SECONDS = 180


def _root() -> Path:
    raw = os.environ.get("AGENT_WORKSPACE") or os.getcwd()
    return Path(raw).resolve()


def _resolve(rel: str) -> Path:
    """Resolve a workspace path, treating ``/workspace`` as the workspace root.

    Paths are normally relative to the workspace root (``list_dir`` with no
    argument lists the root). Models frequently assume an absolute ``/workspace``
    mount point, so a leading ``/workspace`` or ``/workspace/`` prefix is mapped
    onto the root. Any path that still escapes the workspace is rejected.
    """
    root = _root()
    rel = (rel or ".").strip() or "."
    if rel in ("/", "/workspace", "workspace"):
        rel = "."
    elif rel.startswith("/workspace/"):
        rel = rel[len("/workspace/"):]
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {rel}") from exc
    return target


@mcp.tool()
def list_dir(path: str = ".") -> str:
    """List files and directories relative to the workspace root.

    ``path`` is relative to the workspace root (the sandbox the agent runs in);
    omit ``path`` or pass ``.`` to list the root. Returns ``not found: <path>``
    if it does not exist.
    """
    target = _resolve(path)
    if not target.exists():
        return f"not found: {path}"
    if target.is_file():
        return f"file: {path}"
    lines = []
    for child in sorted(target.iterdir()):
        kind = "dir" if child.is_dir() else "file"
        lines.append(f"{kind}\t{child.relative_to(_root())}")
    return "\n".join(lines) or "(empty)"


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file relative to the workspace root.

    ``path`` is relative to the workspace root. Returns ``not a file: <path>``
    if it is missing or not a regular file.
    """
    target = _resolve(path)
    if not target.is_file():
        return f"not a file: {path}"
    return target.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def search_code(query: str, path: str = ".") -> str:
    """Search workspace files for a regex or substring.

    ``path`` is relative to the workspace root.
    """
    root = _resolve(path)
    try:
        pattern = re.compile(query)
    except re.error:
        pattern = None
    hits: list[str] = []
    files = [root] if root.is_file() else root.rglob("*")
    for file in files:
        if not file.is_file():
            continue
        if file.suffix.lower() in {".pyc", ".png", ".jpg", ".lock"}:
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            matched = pattern.search(line) if pattern is not None else query in line
            if matched:
                rel = file.relative_to(_root())
                hits.append(f"{rel}:{i}:{line.strip()}")
                if len(hits) >= 80:
                    return "\n".join(hits)
    return "\n".join(hits) or "no matches"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write a UTF-8 file relative to the workspace root. Requires HITL approval.

    ``path`` is relative to the workspace root.
    """
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


@mcp.tool()
def run_command(command: str) -> str:
    """Run a shell command in the workspace root. Requires HITL approval.

    The command runs with the workspace root as its working directory. Paths are
    relative to the workspace root; ``/workspace`` is accepted as an alias for
    the root, matching the other workspace tools. ``python``/``python3`` resolve
    to the application interpreter. Pipes, ``&&``, ``cd``, and redirection are
    supported. No venv is created.
    """
    root = _root()
    command = (command or "").strip()
    if not command:
        return "empty command"
    # /workspace is the workspace root, same as the other tools. Rewrite it to
    # the real sandbox path so shell commands resolve instead of failing.
    command = re.sub(r"(?<![A-Za-z0-9_])/workspace(?![A-Za-z0-9_])", str(root), command)
    # `@skills/<name>/...` is the stable way to reference a bundled skill file.
    # The model never needs -- and never sees -- the host path behind it.
    command = command.replace("@skills/", f"{_skills_root()}/")
    env = os.environ.copy()
    # Running a skill's scripts must not litter __pycache__ into the (read-only
    # in spirit) skill package or the workspace.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    app_root = os.environ.get("APP_ROOT")
    if app_root:
        env["PYTHONPATH"] = os.pathsep.join([app_root, env.get("PYTHONPATH", "")])
    # Put `python`/`python3` shims first on PATH instead of rewriting the command
    # text. Substituting the interpreter path corrupted real code: `import py`
    # or `py = 3` inside a heredoc became the interpreter path.
    env["PATH"] = os.pathsep.join([str(_interpreter_bin_dir()), env.get("PATH", "")])
    # Node packages the skills rely on (e.g. pptxgenjs) live in a project dir so
    # the sandbox stays the only install target; make `require(...)` resolve them
    # without the model needing npm install or an absolute path.
    node_modules = _node_modules_dir()
    if node_modules:
        env["NODE_PATH"] = os.pathsep.join([node_modules, env.get("NODE_PATH", "")]).strip(os.pathsep)
    extra_python = _extra_python_dir()
    if extra_python:
        env["PYTHONPATH"] = os.pathsep.join([extra_python, env.get("PYTHONPATH", "")]).strip(os.pathsep)
    # npm needs a writable cache; the default (~/.npm) is outside the sandbox and
    # made `npm install` fail with EROFS, which sent agents down dead ends.
    cache = _npm_cache_dir()
    if cache:
        env.setdefault("npm_config_cache", cache)
        env.setdefault("npm_config_update_notifier", "false")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            env=env,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            f"ERROR: command timed out after {COMMAND_TIMEOUT_SECONDS}s and was killed.\n"
            "Do not retry this command; change approach or report the blocker."
        )
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        # A non-zero exit must be impossible to skim past, otherwise the model
        # treats the failure as progress and repeats the same call.
        return (
            f"ERROR: command failed with exit code {result.returncode}.\n"
            f"{out}\n"
            "Diagnose the cause or change approach; do not repeat this exact command."
        )
    return f"exit=0\n{out}"


def _node_modules_dir() -> str:
    """Project-local node_modules for skill toolchains (empty when absent)."""
    app_root = os.environ.get("APP_ROOT")
    if not app_root:
        return ""
    candidate = Path(app_root) / ".agent-node" / "node_modules"
    return str(candidate) if candidate.is_dir() else ""


def _extra_python_dir() -> str:
    """Project-local site-packages for skill toolchains (e.g. markitdown)."""
    app_root = os.environ.get("APP_ROOT")
    if not app_root:
        return ""
    candidate = Path(app_root) / ".agent-python"
    return str(candidate) if candidate.is_dir() else ""


def _npm_cache_dir() -> str:
    app_root = os.environ.get("APP_ROOT")
    if not app_root:
        return ""
    cache = Path(app_root) / ".npm-cache"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    return str(cache)


def _skills_root() -> str:
    """Directory holding installed skill packages (``@skills/...`` alias)."""
    import sys as _sys

    app_root = os.environ.get("APP_ROOT")
    if app_root:
        candidate = Path(app_root) / "uploads" / "maf-skills"
        if candidate.is_dir():
            return str(candidate)
    try:
        _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from src.agent_platform.paths import user_skills_dir

        return str(user_skills_dir())
    except Exception:
        return "skills"


def _interpreter_bin_dir() -> Path:
    """Directory of ``python``/``python3`` shims pointing at the app interpreter."""
    target = Path(sys.executable).resolve()
    bindir = Path(tempfile.gettempdir()) / "agent-platform-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for alias in ("python", "python3"):
        link = bindir / alias
        try:
            if link.is_symlink() and link.resolve() == target:
                continue
        except OSError:
            pass
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(target)
        except OSError:
            # Fall back to a wrapper script when symlinks are unavailable.
            link.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
            link.chmod(0o755)
    return bindir


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
