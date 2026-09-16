"""ZIP import/export for SKILL.md skill packages.

A real-world skill archive (for example Anthropic's ``pptx`` skill) is a
directory tree: ``SKILL.md`` at the root plus ``scripts/``, ``references/``,
``assets/`` and arbitrarily deep sub-directories -- some of them binary.
Importing therefore means *materialising a tree*, not parsing a document.

Two layout shapes are accepted:

* **flat** -- ``SKILL.md`` sits at the archive root (``zip -r skill.zip .``);
* **wrapped** -- the skill lives inside one or more top-level folders, and the
  archive may carry several skills at once (GitHub's "Download ZIP").

:func:`discover_roots` finds every ``SKILL.md`` that has no ``SKILL.md``
ancestor, so each one becomes its own package.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from src.agent_platform.catalog.skill_packages import (
    can_manage_skill,
    parse_skill_md,
    slugify_skill,
)
from src.agent_platform.catalog.skills_store import MafSkillStore
from src.agent_platform.paths import user_skills_dir

MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024
MAX_FILES = 3_000
MAX_NAME_LENGTH = 120

_SKIP_PREFIXES = ("__MACOSX/",)
_SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".git"}
_SKIP_DIRS = {".git", "__MACOSX", "__pycache__", ".idea", ".vscode"}


class ArchiveError(ValueError):
    """Raised when an upload cannot be accepted as a skill archive."""


def import_archive(
    data: bytes,
    *,
    replace: bool = False,
    name_override: str | None = None,
    created_by: int | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """Unpack ``data`` (a ZIP) into one or more user skill packages.

    ``created_by`` stamps the importing user on newly created packages.  With
    ``actor_id``, replacing an existing package requires that caller to be its
    owner or an administrator; a package owned by someone else is reported as a
    conflict instead of being overwritten.
    """
    if not data:
        raise ArchiveError("The uploaded file is empty")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a valid ZIP archive") from exc

    with archive:
        entries, skipped = _collect(archive)
        roots = discover_roots(entries)
        if not roots:
            raise ArchiveError("No SKILL.md found in the archive")

        imported: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        seen_slugs: set[str] = set()
        for root in roots:
            declared = _read_declared_name(archive, root, entries)
            slug = _package_slug(root, declared, name_override if len(roots) == 1 else None)
            # Two roots in one archive can slugify to the same name; without this
            # the second is either reported as a pre-existing conflict or, with
            # replace=True, silently overwrites the first.
            if slug in seen_slugs:
                skipped.append({"path": root or ".", "reason": f"duplicate skill name '{slug}' in this archive"})
                continue
            exists = (user_skills_dir() / slug).exists() or MafSkillStore.get_by_name(slug) is not None
            if exists and not replace:
                conflicts.append({"name": slug, "files": _root_file_count(root, entries), "reason": "already exists"})
                continue
            if exists and actor_id is not None and not can_manage_skill(slug, actor_id):
                conflicts.append({"name": slug, "files": _root_file_count(root, entries), "reason": "owned by another user"})
                continue
            if _shadows_seeded(slug):
                skipped.append({"path": root or ".", "reason": f"'{slug}' overrides a seeded skill for this deployment"})
            seen_slugs.add(slug)
            imported.append(_extract_one(archive, root, slug, entries, created_by=created_by))

    return {
        "imported": imported,
        "conflicts": conflicts,
        "file_count": sum(item["file_count"] for item in imported),
        "roots": roots,
        "skipped": skipped[:50],
        "skipped_count": len(skipped),
    }


def export_archive(name: str) -> tuple[bytes, str]:
    """Zip an existing package for download, preserving relative paths."""
    from src.agent_platform.catalog.skill_artifacts import package_root

    root = package_root(name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part in _SKIP_DIRS or part in _SKIP_NAMES for part in rel.parts):
                continue
            if path.is_dir():
                continue
            if not path.is_file():
                continue
            archive.write(path, rel.as_posix())
    return buffer.getvalue(), f"{root.name}.zip"


def discover_roots(entries: list[str]) -> list[str]:
    """Every ``SKILL.md`` directory prefix that has no ``SKILL.md`` ancestor."""
    skill_dirs = sorted({str(Path(entry).parent).replace("\\", "/") for entry in entries if Path(entry).name == "SKILL.md"})
    normalized = ["" if folder == "." else folder for folder in skill_dirs]
    roots: list[str] = []
    for folder in sorted(normalized, key=lambda value: (value.count("/"), len(value))):
        # An empty root ("SKILL.md" at the archive root) contains everything else.
        if any(other == "" or folder == other or folder.startswith(f"{other}/") for other in roots):
            continue
        roots.append(folder)
    return roots


def _collect(archive: zipfile.ZipFile) -> tuple[list[str], list[dict[str, str]]]:
    """Validate every member; return safe paths plus anything we had to drop.

    Skipped entries are *reported* rather than silently discarded: a skill that
    loses a subtree during import must not look like it arrived intact. The
    segment rules come from ``skill_artifacts`` so an imported file is always
    readable, editable and deletable through the artifact API.
    """
    from src.agent_platform.catalog.skill_artifacts import MAX_PATH_DEPTH, safe_segment

    entries: list[str] = []
    skipped: list[dict[str, str]] = []
    total = 0
    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        if not raw or raw.endswith("/"):
            continue
        if raw.startswith(_SKIP_PREFIXES):
            continue
        if _is_symlink(info):
            skipped.append({"path": raw, "reason": "symlink entries are not imported"})
            continue
        parts = _safe_parts(raw, safe_segment, MAX_PATH_DEPTH)
        if parts is None:
            skipped.append({"path": raw, "reason": "unsupported or unsafe path"})
            continue
        if any(part in _SKIP_DIRS for part in parts[:-1]) or parts[-1] in _SKIP_NAMES:
            continue
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ArchiveError("Archive expands to more than 256 MB")
        if len(entries) >= MAX_FILES:
            raise ArchiveError(f"Archive contains more than {MAX_FILES} files")
        entries.append("/".join(parts))
    if not entries:
        raise ArchiveError("The archive contains no usable files")
    return entries, skipped


def _safe_parts(raw: str, is_safe_segment, max_depth: int) -> list[str] | None:
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        if not is_safe_segment(segment):
            return None
        parts.append(segment)
    if not parts or len(parts) > max_depth:
        return None
    return parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _package_slug(root: str, declared: str, override: str | None) -> str:
    """Prefer an explicit override, then the frontmatter name, then the folder."""
    if override:
        slug = slugify_skill(override)
        if not slug:
            raise ArchiveError("Skill name must be lowercase letters, digits, and hyphens")
        return slug
    folder = Path(root).name if root else ""
    for candidate in (declared, folder):
        slug = slugify_skill(candidate)
        if slug:
            return slug
    raise ArchiveError("Could not determine a skill name; pass one explicitly")


def _shadows_seeded(slug: str) -> bool:
    """Whether importing ``slug`` would override a skill shipped with the app."""
    from src.agent_platform.paths import SKILLS_DIR

    return (SKILLS_DIR / slug).is_dir()


def _read_declared_name(archive: zipfile.ZipFile, root: str, entries: list[str]) -> str:
    target = f"{root}/SKILL.md" if root else "SKILL.md"
    if target not in entries:
        return ""
    try:
        with archive.open(target) as handle:
            head = handle.read(8192).decode("utf-8", errors="replace")
    except (KeyError, OSError):
        return ""
    return parse_skill_md(head).get("name") or ""


def _root_file_count(root: str, entries: list[str]) -> int:
    prefix = f"{root}/" if root else ""
    return sum(1 for entry in entries if entry.startswith(prefix))


def _extract_one(
    archive: zipfile.ZipFile,
    root: str,
    slug: str,
    entries: list[str],
    *,
    created_by: int | None = None,
) -> dict[str, Any]:
    prefix = f"{root}/" if root else ""
    target = user_skills_dir() / slug
    if target.exists() and not target.is_dir():
        raise ArchiveError(f"'{slug}' exists and is not a directory")
    staging = Path(tempfile.mkdtemp(prefix=f".import-{slug}-", dir=str(user_skills_dir())))
    written = 0
    try:
        for entry in entries:
            if prefix and not entry.startswith(prefix):
                continue
            rel = entry[len(prefix):] if prefix else entry
            if not rel:
                continue
            destination = staging / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle, length=1 << 20)
            written += 1
        md_path = staging / "SKILL.md"
        if not md_path.is_file():
            raise ArchiveError(f"Archive entry for '{slug}' has no SKILL.md")
        declared = parse_skill_md(md_path.read_text(encoding="utf-8", errors="replace"))
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    description = declared.get("description") or slug
    MafSkillStore.upsert(slug, description, str(target), enabled=True, created_by=created_by)
    return {
        "name": slug,
        "description": description,
        "file_count": written,
        "path": str(target),
        "skill_md_name": declared.get("name") or "",
    }
