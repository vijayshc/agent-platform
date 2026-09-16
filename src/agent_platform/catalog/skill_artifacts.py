"""Artifact-tree access for SKILL.md skill packages.

A *skill package* is a directory holding ``SKILL.md`` plus any number of
supporting artifacts (``scripts/``, ``references/``, ``assets/`` ...). The
runtime only parses ``SKILL.md``; agents open the sibling files on demand, so
the admin surface has to browse, read and edit the whole tree -- including
deeply nested layouts such as ``scripts/office/schemas/...`` and binary assets
like fonts or images.

Every path that arrives from the client goes through :func:`safe_rel_path`,
which normalises it and refuses anything that would escape the package root.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from src.agent_platform.catalog.skill_packages import package_dir
from src.agent_platform.catalog.skills_store import MafSkillStore
from src.agent_platform.paths import user_skills_dir

# Text artifacts larger than this are listed but never loaded into an editor.
# The write cap is the same value so a saved file is always re-openable.
MAX_TEXT_BYTES = 2_000_000
MAX_WRITE_BYTES = MAX_TEXT_BYTES
MAX_ARTIFACTS = 5_000
# One limit shared with the ZIP importer: a file that imports must stay
# readable, editable and deletable through the artifact API.
MAX_PATH_DEPTH = 12
MAX_SEGMENT_LENGTH = 120

_LANGUAGE_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "ini",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".txt": "plaintext",
    ".rst": "restructuredtext",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".xsd": "xml",
    ".xsl": "xml",
    ".svg": "xml",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".r": "r",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".dockerfile": "dockerfile",
    ".csv": "plaintext",
    ".log": "plaintext",
    ".env": "ini",
    ".gitignore": "plaintext",
    ".editorconfig": "ini",
    ".cfg-dist": "ini",
    ".bat": "bat",
    ".cmd": "bat",
}

# Extensions we know are binary. Anything not listed is sniffed for NUL bytes.
_BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".webm", ".wav", ".ogg",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".pyc", ".class", ".jar",
    ".pptx", ".docx", ".xlsx", ".sqlite", ".db", ".parquet", ".npy", ".npz",
    ".pt", ".pth", ".onnx", ".h5", ".pkl", ".joblib", ".wasm",
}

_SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# Characters that are illegal in a path segment on any platform we care about.
# Everything else -- including non-ASCII names such as `références/` or CJK --
# is accepted, because real skills ship them.
_ILLEGAL_CHARS = set('<>:"|?*\\/')
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_segment(segment: str) -> bool:
    """Whether one path segment is safe to materialise on disk.

    Shared by the ZIP importer and the artifact API so a file that imports is
    always readable, editable and deletable afterwards.
    """
    if not segment or segment in {".", ".."}:
        return False
    if len(segment) > MAX_SEGMENT_LENGTH:
        return False
    if any(ch in _ILLEGAL_CHARS or ord(ch) < 32 for ch in segment):
        return False
    if segment != segment.strip() or segment.endswith("."):
        return False
    if segment.split(".")[0].lower() in _RESERVED_NAMES:
        return False
    return True


class ArtifactError(ValueError):
    """Raised for an artifact path or payload the API must reject."""


def language_for(path: str) -> str:
    name = Path(path).name.lower()
    if name in {"dockerfile", "containerfile"}:
        return "dockerfile"
    if name in {"makefile", "gnumakefile"}:
        return "plaintext"
    ext = Path(name).suffix
    if ext in _LANGUAGE_BY_EXT:
        return _LANGUAGE_BY_EXT[ext]
    if name.startswith("."):
        return _LANGUAGE_BY_EXT.get(name, "plaintext")
    return "plaintext"


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in _BINARY_EXT


def safe_rel_path(raw: str) -> str:
    """Normalise a client-supplied relative path or raise :class:`ArtifactError`."""
    value = (raw or "").strip().replace("\\", "/")
    if not value:
        raise ArtifactError("A file path is required")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ArtifactError("Absolute paths are not allowed")
    parts: list[str] = []
    for segment in value.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ArtifactError("Path traversal is not allowed")
        if not safe_segment(segment):
            raise ArtifactError(f"Unsupported path segment: {segment}")
        parts.append(segment)
    if not parts:
        raise ArtifactError("A file path is required")
    if len(parts) > MAX_PATH_DEPTH:
        raise ArtifactError(f"Path is nested more than {MAX_PATH_DEPTH} levels deep")
    return "/".join(parts)


def package_root(
    name: str, *, for_write: bool = False, created_by: int | None = None
) -> Path:
    """Resolve the package directory for ``name``.

    With ``for_write`` a package that only exists in the read-only seeds tree is
    copied into the user skills directory first, so seeded skills can be edited
    without mutating the repository.  ``created_by`` stamps the newly registered
    row with its creator.
    """
    slug = name
    user_root = user_skills_dir().resolve()
    found = package_dir(name)
    if found is not None:
        resolved = found.resolve()
        if not for_write or _is_within(resolved, user_root):
            return resolved
        target = user_root / resolved.name
        if target.resolve() != resolved:
            _copy_tree(resolved, target)
        _register(target, created_by=created_by)
        return target
    if not for_write:
        raise ArtifactError(f"Skill package not found: {name}")
    slug = _slug(name)
    target = user_root / slug
    target.mkdir(parents=True, exist_ok=True)
    _register(target, created_by=created_by)
    return target


def artifact_path(
    name: str, rel: str, *, for_write: bool = False, created_by: int | None = None
) -> Path:
    root = package_root(name, for_write=for_write, created_by=created_by)
    rel_path = safe_rel_path(rel)
    candidate = (root / rel_path).resolve()
    if not _is_within(candidate, root.resolve()):
        raise ArtifactError("Path escapes the skill package")
    return candidate


def list_artifacts(name: str) -> list[dict[str, Any]]:
    """Recursively describe every artifact in the package as a flat list."""
    root = package_root(name)
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(entries) >= MAX_ARTIFACTS:
            break
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_NAMES or part == "__pycache__" for part in path.relative_to(root).parts):
            continue
        if path.is_dir():
            entries.append({"path": rel, "type": "dir", "size": 0, "language": None, "binary": False, "editable": False, "is_skill_md": False})
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        binary = is_binary_path(rel)
        editable = (not binary) and size <= MAX_TEXT_BYTES
        entries.append({
            "path": rel,
            "type": "file",
            "size": size,
            "language": None if binary else language_for(rel),
            "binary": binary,
            "editable": editable,
            "is_skill_md": rel == "SKILL.md",
        })
    return entries


def read_artifact(name: str, rel: str) -> dict[str, Any]:
    """Return the content of one text artifact in the package."""
    if safe_rel_path(rel) == "SKILL.md":
        from src.agent_platform.catalog.skill_packages import read_package

        row = read_package(name)
        if row is None:
            raise ArtifactError(f"Skill package not found: {name}")
        return {
            "path": "SKILL.md",
            "content": row.get("skill_md") or "",
            "language": "markdown",
            "size": len(row.get("skill_md") or ""),
            "binary": False,
            "editable": True,
        }
    path = artifact_path(name, rel)
    if path.is_dir():
        raise ArtifactError(f"{safe_rel_path(rel)} is a folder, not a file")
    if not path.is_file():
        raise ArtifactError(f"File not found: {rel}")
    size = path.stat().st_size
    binary = is_binary_path(rel) or _looks_binary(path)
    if binary:
        return {"path": safe_rel_path(rel), "content": "", "language": None, "size": size, "binary": True, "editable": False}
    if size > MAX_TEXT_BYTES:
        raise ArtifactError(f"File is too large to edit ({size} bytes)")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": safe_rel_path(rel),
        "content": text,
        "language": language_for(rel),
        "size": size,
        "binary": False,
        "editable": True,
    }


def write_artifact(name: str, rel: str, content: str, *, created_by: int | None = None) -> dict[str, Any]:
    """Create or overwrite one text artifact."""
    rel_path = safe_rel_path(rel)
    payload = content or ""
    if len(payload.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ArtifactError("File content is too large to save")
    if rel_path == "SKILL.md":
        _write_skill_md(name, payload, created_by=created_by)
        return read_artifact(name, rel_path)
    path = artifact_path(name, rel_path, for_write=True, created_by=created_by)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    _touch(name, created_by)
    return read_artifact(name, rel_path)


def read_file_in_root(root: Path, rel: str) -> dict[str, Any]:
    """Read a text file directly under ``root`` (used by the runtime skill tools).

    Unlike :func:`read_artifact` this does not resolve a skill *name*, so it also
    works when the frontmatter name differs from the package folder.
    """
    rel_path = safe_rel_path(rel)
    path = (root / rel_path).resolve()
    if not _is_within(path, root.resolve()):
        raise ArtifactError("Path escapes the skill package")
    if path.is_dir():
        raise ArtifactError(f"{rel_path} is a folder, not a file")
    if not path.is_file():
        raise ArtifactError(f"File not found: {rel_path}")
    size = path.stat().st_size
    if is_binary_path(rel_path) or _looks_binary(path):
        return {"path": rel_path, "content": "", "language": None, "size": size, "binary": True, "editable": False}
    if size > MAX_TEXT_BYTES:
        raise ArtifactError(f"File is too large to read inline ({size} bytes)")
    return {
        "path": rel_path,
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "language": language_for(rel_path),
        "size": size,
        "binary": False,
        "editable": True,
    }


def list_files_in_root(root: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Flat file listing for a package directory (no name resolution)."""
    entries: list[dict[str, Any]] = []
    if not root.is_dir():
        return entries
    for path in sorted(root.rglob("*")):
        if len(entries) >= limit:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_NAMES or part == "__pycache__" for part in path.relative_to(root).parts):
            continue
        entries.append({"path": rel, "size": path.stat().st_size, "binary": is_binary_path(rel)})
    return entries


def ensure_directory(name: str, rel: str, *, created_by: int | None = None) -> dict[str, Any]:
    """Create a directory (and its parents) inside the package."""
    rel_path = safe_rel_path(rel)
    path = artifact_path(name, rel_path, for_write=True, created_by=created_by)
    path.mkdir(parents=True, exist_ok=True)
    _touch(name, created_by)
    return {"path": rel_path, "type": "dir"}


def delete_artifact(name: str, rel: str, *, created_by: int | None = None) -> bool:
    """Delete one artifact (file or directory tree), pruning empty parents."""
    rel_path = safe_rel_path(rel)
    if rel_path == "SKILL.md":
        raise ArtifactError("SKILL.md cannot be deleted")
    root = package_root(name, for_write=True, created_by=created_by).resolve()
    path = artifact_path(name, rel_path, for_write=True, created_by=created_by)
    # A symlink (or a path that resolves onto the package root) must never turn
    # "delete this file" into "delete the whole package".
    if path.resolve() == root:
        raise ArtifactError("Refusing to delete the skill package root")
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()
    else:
        return False
    parent = path.parent
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    _touch(name, created_by)
    return True


def package_stats(name: str) -> dict[str, Any]:
    """Cheap summary used by the list view (no file contents are read)."""
    try:
        root = package_root(name)
    except ArtifactError:
        return {"file_count": 0, "dir_count": 0, "size_bytes": 0, "has_skill_md": False, "updated_at": None, "writable": False}
    files = 0
    dirs = 0
    size = 0
    newest = 0.0
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if any(part in _SKIP_NAMES or part == "__pycache__" for part in rel_parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_dir():
            dirs += 1
            continue
        files += 1
        size += stat.st_size
        newest = max(newest, stat.st_mtime)
    from datetime import datetime, timezone

    return {
        "file_count": files,
        "dir_count": dirs,
        "size_bytes": size,
        "has_skill_md": (root / "SKILL.md").is_file(),
        "updated_at": datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(sep=" ", timespec="seconds") if newest else None,
        "writable": _is_within(root, user_skills_dir().resolve()),
    }


def _write_skill_md(name: str, content: str, *, created_by: int | None = None) -> None:
    from src.agent_platform.catalog.skill_packages import parse_skill_md

    root = package_root(name, for_write=True, created_by=created_by)
    (root / "SKILL.md").write_text(content, encoding="utf-8")
    parsed = parse_skill_md(content)
    slug = root.name
    MafSkillStore.upsert(slug, parsed.get("description") or slug, str(root), enabled=True, created_by=created_by)
    _touch(slug, created_by)


def ensure_package(name: str, *, description: str = "", created_by: int | None = None) -> Path:
    """Create a minimal package (SKILL.md only) when it does not exist yet."""
    root = package_root(name, for_write=True, created_by=created_by)
    md_path = root / "SKILL.md"
    if not md_path.is_file():
        slug = root.name
        md_path.write_text(
            f"---\nname: {slug}\ndescription: {description or slug}\n---\n\n# {slug}\n",
            encoding="utf-8",
        )
        _register(root, created_by=created_by)
    return root


def _register(root: Path, *, created_by: int | None = None) -> None:
    from src.agent_platform.catalog.skill_packages import parse_skill_md

    md_path = root / "SKILL.md"
    description = root.name
    if md_path.is_file():
        description = parse_skill_md(md_path.read_text(encoding="utf-8")).get("description") or root.name
    MafSkillStore.upsert(root.name, description, str(root), enabled=True, created_by=created_by)


def _touch(name: str, created_by: int | None = None) -> None:
    row = MafSkillStore.get_by_name(name)
    if row:
        MafSkillStore.upsert(
            str(row.get("name") or name),
            str(row.get("description") or name),
            str(row.get("path") or ""),
            enabled=bool(row.get("enabled", 1)),
            created_by=created_by,
        )


def _slug(name: str) -> str:
    from src.agent_platform.catalog.skill_packages import slugify_skill

    slug = slugify_skill(name)
    if not slug:
        raise ArtifactError("Skill name must be lowercase letters, digits, and hyphens")
    return slug


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        return
    shutil.copytree(source, target)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    return b"\x00" in chunk
