"""Read/write MAF SKILL.md packages for SkillsProvider.from_paths.

Every package row in ``maf_skills`` carries its creator (``created_by``), which
is what the generic resource-access store uses to answer "may this user open
this skill?".  The helpers :func:`visible_skill_names`, :func:`can_access_skill`
and :func:`register_skill_access` are the frozen interface the Studio catalogue
and runtime call; :func:`list_packages` itself stays intentionally unfiltered
for backward compatibility.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from src.agent_platform.catalog.skills_store import MafSkillStore
from src.agent_platform.paths import SKILLS_DIR, user_skills_dir
from src.auth import resource_access

#: Canonical resource type for on-disk SKILL.md packages (``maf_skills.id``).
SKILL_PACKAGE_RESOURCE_TYPE = "skill_package"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SCRIPT_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_DESCRIPTION_MAX = 1024


def slugify_skill(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug[:63]


def write_package(
    *,
    name: str,
    description: str = "",
    instructions: str = "",
    scripts: list[dict[str, str]] | None = None,
    previous_name: str | None = None,
    created_by: int | None = None,
    actor_id: int | None = None,
) -> dict[str, Any]:
    slug = slugify_skill(name)
    if not _NAME_RE.match(slug):
        raise ValueError("Skill name must be lowercase letters, digits, and hyphens")
    description = (description or "")[:_DESCRIPTION_MAX]
    wanted = _validated_scripts(scripts)
    if previous_name:
        prev = slugify_skill(previous_name)
        if prev and prev != slug:
            if actor_id is not None and not can_manage_skill(prev, actor_id):
                raise PermissionError(f"Only an administrator or the owner of '{prev}' may rename it")
            delete_package(prev)
        elif actor_id is not None and not can_access_skill(prev, actor_id):
            raise PermissionError(f"You do not have access to skill '{prev}'")
    root = user_skills_dir() / slug
    root.mkdir(parents=True, exist_ok=True)
    body = _render_skill_md(slug, description, instructions)
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    scripts_dir = root / "scripts"
    for filename, content in wanted:
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / filename).write_text(content, encoding="utf-8")
    names = {filename for filename, _content in wanted}
    if scripts_dir.is_dir():
        for existing in scripts_dir.iterdir():
            if existing.is_file() and existing.name not in names:
                existing.unlink()
        if not names:
            scripts_dir.rmdir()
    row = MafSkillStore.upsert(slug, description or slug, str(root), enabled=True, created_by=created_by)
    return read_package(slug) or {**row, "instructions": instructions, "scripts": scripts or []}


def _validated_scripts(scripts: list[dict[str, str]] | None) -> list[tuple[str, str]]:
    wanted: list[tuple[str, str]] = []
    for script in scripts or []:
        raw = str(script.get("filename") or "")
        filename = Path(raw).name
        if filename != raw or not _SCRIPT_RE.match(filename):
            raise ValueError(f"Invalid script filename: {raw or filename}")
        wanted.append((filename, str(script.get("content") or "")))
    return wanted


def read_package(name: str) -> dict[str, Any] | None:
    path = _package_dir(name)
    if path is None:
        return None
    md_path = path / "SKILL.md"
    if not md_path.is_file():
        return None
    raw = md_path.read_text(encoding="utf-8")
    parsed = parse_skill_md(raw)
    scripts: list[dict[str, str]] = []
    scripts_dir = path / "scripts"
    if scripts_dir.is_dir():
        for item in sorted(scripts_dir.iterdir()):
            if item.is_file():
                scripts.append({"filename": item.name, "content": item.read_text(encoding="utf-8")})
    store = (
        MafSkillStore.get_by_name(parsed.get("name") or name)
        or MafSkillStore.get_by_name(name)
        or {}
    )
    return {
        "id": store.get("id"),
        "name": parsed.get("name") or name,
        "description": parsed.get("description") or store.get("description") or "",
        "path": str(path),
        "enabled": bool(store.get("enabled", True)),
        "created_by": store.get("created_by"),
        "instructions": parsed.get("instructions") or "",
        "scripts": scripts,
        "skill_md": raw,
        "has_skill_md": True,
    }


def list_packages() -> list[dict[str, Any]]:
    MafSkillStore.ensure_tables()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in MafSkillStore.list_all():
        name = str(row.get("name") or "")
        pkg = read_package(name)
        if pkg:
            seen.add(pkg["name"])
            out.append(pkg)
        else:
            item = dict(row)
            item["has_skill_md"] = False
            item["instructions"] = ""
            item["scripts"] = []
            seen.add(name)
            out.append(item)
    for root in (user_skills_dir(), SKILLS_DIR):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in seen:
                continue
            pkg = read_package(child.name)
            if pkg:
                seen.add(pkg["name"])
                out.append(pkg)
    return out


def parse_skill_md(raw: str) -> dict[str, str]:
    match = _FRONTMATTER.match(raw or "")
    meta: dict[str, str] = {}
    body = raw or ""
    if match:
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = _unquote(value.strip())
        body = match.group(2)
    return {
        "name": meta.get("name") or "",
        "description": meta.get("description") or "",
        "instructions": body.strip(),
    }


def _render_skill_md(name: str, description: str, instructions: str) -> str:
    return (
        f"---\nname: {name}\ndescription: {_yaml_quote(description or name)}\n---\n\n"
        f"{(instructions or '').rstrip()}\n"
    )


def delete_package(name: str, *, actor_id: int | None = None) -> bool:
    """Delete a user package and its registry row.

    Returns ``True`` when anything was removed, so callers can distinguish a
    successful delete from an unknown name (the API maps ``False`` to a 404).
    With ``actor_id`` the caller must be the owner or an administrator.
    """
    slug = slugify_skill(name) or name
    if actor_id is not None and not can_manage_skill(slug, actor_id):
        raise PermissionError(f"Only an administrator or the owner of '{slug}' may delete it")
    root = user_skills_dir().resolve()
    path = (user_skills_dir() / slug).resolve()
    removed = False
    if path.is_dir() and path.parent == root:
        shutil.rmtree(path)
        removed = True
    row = MafSkillStore.get_by_name(slug) or MafSkillStore.get_by_name(name)
    if not row:
        return removed
    stored = Path(str(row.get("path") or "")).resolve()
    if stored == path or stored.parent == path or _is_under(stored, root):
        if row.get("id") is not None:
            resource_access.set_access(SKILL_PACKAGE_RESOURCE_TYPE, int(row["id"]), [])
        MafSkillStore.delete_by_name(str(row.get("name") or slug))
        removed = True
    return removed


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _yaml_quote(value: str) -> str:
    escaped = (
        (value or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        return (
            inner.replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
    return value


def package_dir(name: str) -> Path | None:
    """Locate a package directory on disk (user override first, then seeds)."""
    slug = slugify_skill(name) or name
    for root in (user_skills_dir(), SKILLS_DIR):
        candidate = root / slug
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate
    row = MafSkillStore.get_by_name(slug) or MafSkillStore.get_by_name(name)
    if row and row.get("path"):
        path = Path(str(row["path"]))
        if path.is_dir():
            return path
        if path.name == "SKILL.md":
            return path.parent
    return None


# Kept for callers that predate the public name.
_package_dir = package_dir


def list_summaries() -> list[dict[str, Any]]:
    """List packages without reading artifact contents (admin list view).

    ``list_packages`` inlines every script body, which is correct for the studio
    editor but wasteful for a table. This variant only walks the tree for sizes.
    """
    MafSkillStore.ensure_tables()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in MafSkillStore.list_all():
        name = str(row.get("name") or "")
        if not name or name in seen:
            continue
        path = package_dir(name)
        seen.add(name)
        if path is None:
            out.append(_legacy_row(row))
            continue
        out.append(_summary_row(name, path, row))
    for root in (user_skills_dir(), SKILLS_DIR):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in seen:
                continue
            if not (child / "SKILL.md").is_file():
                continue
            seen.add(child.name)
            out.append(_summary_row(child.name, child, MafSkillStore.get_by_name(child.name) or {}))
    return out


def _summary_row(name: str, path: Path, row: dict[str, Any]) -> dict[str, Any]:
    from src.agent_platform.catalog.skill_artifacts import package_stats

    raw = (path / "SKILL.md").read_text(encoding="utf-8", errors="replace") if (path / "SKILL.md").is_file() else ""
    parsed = parse_skill_md(raw)
    stats = package_stats(name)
    return {
        "id": row.get("id"),
        "name": name,
        "kind": "package",
        "description": parsed.get("description") or row.get("description") or "",
        "path": str(path),
        "enabled": bool(row.get("enabled", 1)),
        "created_by": row.get("created_by"),
        "has_skill_md": stats["has_skill_md"],
        "file_count": stats["file_count"],
        "dir_count": stats["dir_count"],
        "size_bytes": stats["size_bytes"],
        "updated_at": stats["updated_at"],
        "writable": stats["writable"],
        "seeded": str(path).startswith(str(SKILLS_DIR)),
    }


def _legacy_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": str(row.get("name") or ""),
        "kind": "legacy",
        "description": str(row.get("description") or ""),
        "path": str(row.get("path") or ""),
        "enabled": bool(row.get("enabled", 1)),
        "created_by": row.get("created_by"),
        "has_skill_md": False,
        "file_count": 0,
        "dir_count": 0,
        "size_bytes": 0,
        "updated_at": row.get("created_at"),
        "writable": False,
        "seeded": False,
    }


# --- tenancy: frozen helpers for the Studio catalogue and the runtime ------

def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _package_entry(name: str) -> dict[str, Any] | None:
    """Owner-bearing identity for a package, by name or slug.

    A package that lives only on disk (a seeded skill) has no row and therefore
    no owner: it is grandfathered-visible, exactly like a NULL owner.
    """
    candidates = [str(name or "")]
    slug = slugify_skill(name)
    if slug and slug not in candidates:
        candidates.append(slug)
    for candidate in candidates:
        if not candidate:
            continue
        row = MafSkillStore.get_by_name(candidate)
        if row:
            return {
                "name": str(row.get("name") or candidate),
                "id": _coerce_int(row.get("id")),
                "created_by": _coerce_int(row.get("created_by")),
            }
    for root in (user_skills_dir(), SKILLS_DIR):
        path = root / (slug or str(name or ""))
        if path.is_dir() and (path / "SKILL.md").is_file():
            return {"name": slug or str(name or ""), "id": None, "created_by": None}
    return None


def _package_entries() -> list[dict[str, Any]]:
    """Every package row/dir with its id and owner, without reading contents."""
    MafSkillStore.ensure_tables()
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in MafSkillStore.list_all():
        name = str(row.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        entries.append({
            "name": name,
            "id": _coerce_int(row.get("id")),
            "created_by": _coerce_int(row.get("created_by")),
        })
    for root in (user_skills_dir(), SKILLS_DIR):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in seen:
                continue
            if not (child / "SKILL.md").is_file():
                continue
            seen.add(child.name)
            row = MafSkillStore.get_by_name(child.name) or {}
            entries.append({
                "name": child.name,
                "id": _coerce_int(row.get("id")),
                "created_by": _coerce_int(row.get("created_by")),
            })
    return entries


def visible_skill_names(user_id: int | None) -> set[str] | None:
    """Package names the user may see; ``None`` means every package (admin)."""
    if user_id is None:
        return set()
    if resource_access.is_admin(user_id):
        return None
    rows = _package_entries()
    visible = resource_access.filter_visible(
        SKILL_PACKAGE_RESOURCE_TYPE, rows, user_id,
        owner_key="created_by", id_key="id",
    )
    return {str(row["name"]) for row in visible if row.get("name")}


def can_access_skill(name: str, user_id: int | None) -> bool:
    """Whether the user may read/use one SKILL.md package."""
    entry = _package_entry(name)
    if entry is None:
        return False
    return resource_access.can_access(
        SKILL_PACKAGE_RESOURCE_TYPE, entry["id"], entry["created_by"], user_id
    )


def can_manage_skill(name: str, user_id: int | None) -> bool:
    """Whether the user may rename/delete one package (owner or admin)."""
    if user_id is None:
        return False
    if resource_access.is_admin(user_id):
        return True
    entry = _package_entry(name)
    return bool(
        entry is not None
        and entry["created_by"] is not None
        and int(entry["created_by"]) == int(user_id)
    )


def set_skill_owner(name: str, user_id: int | None) -> None:
    """Claim an owner-less package row for the user (never reassigns)."""
    if user_id is None:
        return
    candidates = [str(name or ""), slugify_skill(name)]
    for candidate in candidates:
        if candidate and MafSkillStore.claim_owner(candidate, int(user_id)):
            return


def _skill_package_owner(package_id: int) -> int | None:
    row = MafSkillStore.get_by_id(int(package_id))
    return _coerce_int(row.get("created_by")) if row else None


def _skill_package_exists(package_id: int) -> bool:
    return MafSkillStore.get_by_id(int(package_id)) is not None


def register_skill_access() -> None:
    """Register the ``skill_package`` owner/existence resolvers (idempotent)."""
    resource_access.register_resource(
        SKILL_PACKAGE_RESOURCE_TYPE, _skill_package_owner, _skill_package_exists
    )


# Runs at import time so the generic access API can resolve a package as soon as
# this module is loaded (which the /api/v1 blueprint always does).
register_skill_access()
