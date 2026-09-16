"""Safe import of an uploaded Flask app archive.

An uploaded app is arbitrary code, so extraction is treated as hostile input:
absolute paths, ``..`` traversal, symlinks and device entries are rejected, the
expanded size and file count are capped, and the tree is materialised in a
staging directory and renamed into place only once it is complete.

The archive must carry an ``app.json`` manifest describing how to run it; an
archive without one is rejected rather than guessed at.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_FILES = 5_000
MAX_DEPTH = 24
MANIFEST_NAME = "app.json"
REQUIREMENTS_NAME = "requirements.txt"
PACKAGE_NAME = "package.json"
MAX_MANIFEST_BYTES = 64 * 1024

_SKIP_DIRS = {".git", "__pycache__", ".idea", ".vscode", "node_modules", ".venv", "venv"}
_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ArchiveError(ValueError):
    """The upload cannot be accepted as an application archive."""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:63].strip("-")
    return slug if _SLUG.match(slug) else ""


def _safe_parts(raw: str) -> list[str] | None:
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == ".." or not _SEGMENT.match(segment):
            return None
        parts.append(segment)
    if not parts or len(parts) > MAX_DEPTH:
        return None
    return parts


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def read_manifest(data: bytes) -> dict[str, Any]:
    """Parse and validate just the manifest, before anything is written to disk."""
    if not data:
        raise ArchiveError("The uploaded file is empty")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a valid ZIP archive") from exc

    with archive:
        entries = [info.filename.replace("\\", "/") for info in archive.infolist() if not info.filename.endswith("/")]
        manifest_paths = sorted(
            (entry for entry in entries if Path(entry).name == MANIFEST_NAME and entry.count("/") <= 2),
            key=lambda entry: entry.count("/"),
        )
        if not manifest_paths:
            raise ArchiveError(
                f"No {MANIFEST_NAME} found. A hosted app archive must contain a manifest "
                "describing its entry point."
            )
        chosen = manifest_paths[0]
        try:
            # Bounded: a small archive can still claim an enormous entry, and
            # this read happens before any size accounting.
            with archive.open(chosen) as handle:
                raw_manifest = handle.read(MAX_MANIFEST_BYTES + 1)
            if len(raw_manifest) > MAX_MANIFEST_BYTES:
                raise ArchiveError(f"{chosen} is larger than {MAX_MANIFEST_BYTES // 1024} KB")
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError(f"{chosen} is not valid JSON: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ArchiveError(f"{chosen} must contain a JSON object")

    root = str(Path(chosen).parent)
    root = "" if root == "." else root
    manifest["_root"] = root
    manifest["_manifest_path"] = chosen
    manifest["name"] = str(manifest.get("name") or "").strip()
    entry = str(manifest.get("entry") or "").strip()
    if not manifest["name"]:
        raise ArchiveError("The manifest must set a 'name'")
    manifest["entry"] = _validated_entry(entry)
    requested = str(manifest.get("slug") or "").strip().lower()
    manifest["slug"] = slugify(requested or manifest["name"])
    if not manifest["slug"]:
        raise ArchiveError("Could not derive a valid slug; set 'slug' in the manifest")

    from src.hosting import settings

    if settings.require_requirements() and not any(
        Path(entry).name == REQUIREMENTS_NAME and entry.count("/") == (root.count("/") + 1 if root else 0)
        for entry in entries
    ):
        raise ArchiveError(
            f"No {REQUIREMENTS_NAME} found next to {MANIFEST_NAME}. Every hosted app declares its "
            "Python dependencies in requirements.txt - an app that needs none ships a file "
            "containing only comments."
        )
    manifest["has_package_json"] = any(
        Path(entry).name == PACKAGE_NAME and entry.count("/") == (root.count("/") + 1 if root else 0)
        for entry in entries
    )
    return manifest


def _validated_entry(entry: str) -> str:
    """The entry becomes part of generated Python, so it is validated strictly.

    Only a dotted module path and a callable name are accepted - never a path,
    never punctuation that would turn the generated import into something else.
    """
    module, separator, attribute = entry.partition(":")
    if (not separator or not module or not attribute
            or not all(_IDENTIFIER.match(part) for part in module.split("."))
            or not _IDENTIFIER.match(attribute)):
        raise ArchiveError(
            "The manifest must set 'entry' as 'module:callable' using only letters, digits, "
            "underscores and dots (for example 'app:app')"
        )
    return f"{module}:{attribute}"


def _accepted_entries(archive: zipfile.ZipFile, root: str) -> list[tuple[zipfile.ZipInfo, list[str]]]:
    """Validate the whole archive without touching the filesystem.

    A rejected archive must leave nothing behind - not even an empty directory -
    or a later import of the same slug would be refused as "already installed"
    with nothing in the admin UI to remove.
    """
    prefix = f"{root}/" if root else ""
    accepted: list[tuple[zipfile.ZipInfo, list[str]]] = []
    total = 0
    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        if not raw or raw.endswith("/") or raw.startswith("__MACOSX/"):
            continue
        if _is_symlink(info):
            raise ArchiveError(f"'{raw}' is a symlink; archives may contain regular files only")
        parts = _safe_parts(raw)
        if parts is None:
            raise ArchiveError(f"'{raw}' is not a safe path")
        if any(part in _SKIP_DIRS for part in parts[:-1]) or parts[-1] in _SKIP_FILES:
            continue
        if prefix and not raw.startswith(prefix):
            continue
        relative = parts[len(prefix.split("/")) - 1:] if prefix else parts
        if not relative:
            continue
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise ArchiveError(
                f"Archive expands to more than {MAX_TOTAL_UNCOMPRESSED // (1024 * 1024)} MB"
            )
        if len(accepted) >= MAX_FILES:
            raise ArchiveError(f"Archive contains more than {MAX_FILES} files")
        accepted.append((info, relative))
    if not accepted:
        raise ArchiveError("The archive contains no usable files")
    return accepted


def extract(data: bytes, manifest: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Materialise the archive into ``destination`` (atomic; staging then rename)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a valid ZIP archive") from exc

    root = manifest["_root"]
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".import-{manifest['slug']}-", dir=str(destination.parent)))

    written = 0
    total = 0
    try:
        with archive:
            for info, relative in _accepted_entries(archive, root):
                total += info.file_size
                written += 1
                target = staging.joinpath(*relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle, length=1 << 20)
                written += 1

        # The manifest is the one file that must survive extraction.
        if not any(staging.rglob(MANIFEST_NAME)):
            raise ArchiveError(f"The extracted tree has no {MANIFEST_NAME}")
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {"file_count": written, "path": str(destination), "uncompressed_bytes": total}
