"""Admin API for SKILL.md packages: artifact tree, editing, ZIP import/export.

Registered *in addition to* the legacy ``/skills`` CRUD in ``catalog_routes``:
that module keeps the studio's package list working, while this blueprint owns
everything the Skill Library page needs to manipulate real on-disk packages.

Every package operation is tenanted: list views are filtered to what the caller
may see, a direct read of someone else's package is a 403, and every *mutation*
(file/folder/package) is owner/admin only -- a role grant is view/use, never
control. Callers must hold the ``skills`` module (this page's gate) or
``agent_studio`` (the Studio skill palette).
"""

from __future__ import annotations

from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file

from src.agent_platform.api.auth import api_auth_required, current_user_id

skill_package_bp = Blueprint("skill_packages", __name__)


def _require_skill_module(min_level: str | None = None):
    """Allow the ``skills`` module (this page's own gate) or ``agent_studio``.

    The Skill Library page is gated on the ``skills`` module; the Studio skill
    palette reaches the same endpoints with ``agent_studio``. Both are accepted
    at the correct read/write level. Agent Studio users keep their old behavior.
    """
    from src.auth.modules import ACCESS_READ, ACCESS_WRITE, DEFAULT_READ_METHODS
    from src.utils.user_manager import UserManager

    user_id = current_user_id()
    if user_id is None:
        g.audit_reason = "authentication required"
        return jsonify({"error": "unauthorized", "message": "Authentication required"}), 401
    if min_level is None:
        method = (request.method or "GET").upper()
        min_level = ACCESS_READ if method in DEFAULT_READ_METHODS else ACCESS_WRITE
    if not UserManager().has_any_module_access(
        user_id, ("skills", "agent_studio"), min_level=min_level
    ):
        g.audit_reason = f"missing {min_level} access to module(s): skills, agent_studio"
        return jsonify({
            "error": "forbidden",
            "message": "Skills requires skills or agent_studio module access",
        }), 403
    return None


def _skill_or_denied(name: str):
    """Return ``(row, None)`` when accessible, else ``(None, error_response)``.

    A missing package is a 404 (the asset does not exist); an existing package
    the caller cannot access is a 403 (it exists, but is not theirs).
    """
    from src.agent_platform.catalog.skill_packages import can_access_skill, read_package

    row = read_package(name)
    if row is None:
        return None, (jsonify({"error": "skill not found"}), 404)
    if not can_access_skill(name, current_user_id()):
        return None, (jsonify({"error": "forbidden"}), 403)
    return row, None


def _manage_or_denied(name: str):
    """Reject a *mutation* by anyone but the owner or an administrator.

    A role grant confers view/use, never write control inside the package.
    Call after :func:`_skill_or_denied`, so a missing package is already 404.
    """
    from src.agent_platform.catalog.skill_packages import can_manage_skill

    if not can_manage_skill(name, current_user_id()):
        return jsonify({"error": "forbidden"}), 403
    return None


@skill_package_bp.get("/skills/overview")
@api_auth_required("agents:read")
def skills_overview():
    """Everything the admin list view renders, in one round-trip.

    Packages and legacy rows are filtered to the caller's access, so the page
    only ever renders server-authorized data.
    """
    denied = _require_skill_module("read")
    if denied:
        return denied
    from src.agent_platform.catalog.skill_packages import (
        can_manage_skill,
        list_summaries,
        visible_skill_names,
    )

    user_id = current_user_id()
    packages = list_summaries()
    names = visible_skill_names(user_id)
    if names is not None:
        packages = [p for p in packages if str(p.get("name") or "") in names]
    for package in packages:
        package["can_manage"] = can_manage_skill(str(package.get("name") or ""), user_id)
    return jsonify({
        "packages": packages,
        "legacy": _legacy_library(user_id),
        "total_files": sum(int(p.get("file_count") or 0) for p in packages),
    })


@skill_package_bp.get("/skills/<name>/tree")
@api_auth_required("agents:read")
def skill_tree(name: str):
    denied = _require_skill_module("read")
    if denied:
        return denied
    row, denied = _skill_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, list_artifacts

    try:
        artifacts = list_artifacts(name)
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "name": row.get("name") or name,
        "description": row.get("description") or "",
        "path": row.get("path"),
        "enabled": row.get("enabled", True),
        "skill_md": row.get("skill_md") or "",
        "artifacts": artifacts,
    })


@skill_package_bp.get("/skills/<name>/file")
@api_auth_required("agents:read")
def read_skill_file(name: str):
    denied = _require_skill_module("read")
    if denied:
        return denied
    _row, denied = _skill_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, read_artifact

    try:
        return jsonify(read_artifact(name, request.args.get("path") or ""))
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400


@skill_package_bp.post("/skills/<name>/folder")
@api_auth_required("agents:write")
def create_skill_folder(name: str):
    denied = _require_skill_module("write")
    if denied:
        return denied
    _row, denied = _skill_or_denied(name)
    if denied:
        return denied
    denied = _manage_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, ensure_directory

    data = request.get_json(silent=True) or {}
    try:
        return jsonify(ensure_directory(name, str(data.get("path") or ""), created_by=current_user_id())), 201
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400


@skill_package_bp.put("/skills/<name>/file")
@api_auth_required("agents:write")
def write_skill_file(name: str):
    denied = _require_skill_module("write")
    if denied:
        return denied
    _row, denied = _skill_or_denied(name)
    if denied:
        return denied
    denied = _manage_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, write_artifact

    data = request.get_json(silent=True) or {}
    if "content" not in data:
        return jsonify({"error": "content is required"}), 400
    try:
        return jsonify(write_artifact(
            name,
            request.args.get("path") or "",
            str(data.get("content") or ""),
            created_by=current_user_id(),
        ))
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400


@skill_package_bp.delete("/skills/<name>/file")
@api_auth_required("agents:write")
def delete_skill_file(name: str):
    denied = _require_skill_module("write")
    if denied:
        return denied
    _row, denied = _skill_or_denied(name)
    if denied:
        return denied
    denied = _manage_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, delete_artifact

    rel = request.args.get("path") or ""
    try:
        if not delete_artifact(name, rel, created_by=current_user_id()):
            return jsonify({"error": "file not found"}), 404
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"success": True, "path": rel})


@skill_package_bp.delete("/skills/<name>/package")
@api_auth_required("agents:write")
def delete_skill_package(name: str):
    """Delete a whole package from disk.

    Deletion is destructive control, so only the owner or an administrator may
    do it; a granted role confers view/use, never delete. The catalog blueprint
    keeps its own ``DELETE /skills/<name>`` for the legacy studio CRUD.
    """
    denied = _require_skill_module("write")
    if denied:
        return denied
    user_id = current_user_id()
    from src.agent_platform.catalog.skill_packages import (
        can_manage_skill,
        delete_package,
        read_package,
    )

    if read_package(name) is None:
        return jsonify({"error": "skill not found"}), 404
    if not can_manage_skill(name, user_id):
        return jsonify({"error": "forbidden"}), 403
    if not delete_package(name, actor_id=user_id):
        return jsonify({"error": "skill not found"}), 404
    return jsonify({"success": True})


@skill_package_bp.post("/skills/import")
@api_auth_required("agents:write")
def import_skill_zip():
    """Unpack an uploaded ZIP into one or more skill packages.

    Body: ``multipart/form-data`` with ``file`` (the archive), optional
    ``replace=1`` and optional ``name`` to force a single package name.
    """
    denied = _require_skill_module("write")
    if denied:
        return denied
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "A .zip file is required"}), 400
    filename = (upload.filename or "").strip()
    if filename and not filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip archives are supported"}), 400
    replace = str(request.form.get("replace") or "").lower() in {"1", "true", "yes", "on"}
    name = (request.form.get("name") or "").strip() or None
    user_id = current_user_id()

    from src.agent_platform.catalog.skill_archive import ArchiveError, import_archive

    from src.agent_platform.catalog.skill_archive import MAX_ARCHIVE_BYTES

    # Read one byte past the cap so an oversized upload is rejected instead of
    # being buffered into memory in full.
    blob = upload.read(MAX_ARCHIVE_BYTES + 1)
    if len(blob) > MAX_ARCHIVE_BYTES:
        return jsonify({"error": f"Archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB"}), 413
    try:
        result = import_archive(
            blob, replace=replace, name_override=name,
            created_by=user_id, actor_id=user_id,
        )
    except ArchiveError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - surfaced to the UI verbatim
        return jsonify({"error": f"Import failed: {exc}"}), 500
    from src.agent_platform.catalog.skill_packages import set_skill_owner

    for item in result.get("imported") or []:
        set_skill_owner(str(item.get("name") or ""), user_id)
    return jsonify({"success": True, **result}), 201


@skill_package_bp.get("/skills/<name>/export")
@api_auth_required("agents:read")
def export_skill_zip(name: str):
    denied = _require_skill_module("read")
    if denied:
        return denied
    _row, denied = _skill_or_denied(name)
    if denied:
        return denied
    from src.agent_platform.catalog.skill_archive import ArchiveError, export_archive
    from src.agent_platform.catalog.skill_artifacts import ArtifactError

    try:
        payload, filename = export_archive(name)
    except ArtifactError as exc:
        # A missing package (e.g. a stale legacy row) is a 404, not a 500.
        return jsonify({"error": str(exc)}), 404
    except ArchiveError as exc:
        return jsonify({"error": str(exc)}), 400

    return send_file(
        BytesIO(payload),
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@skill_package_bp.post("/skills/<name>/create")
@api_auth_required("agents:write")
def create_skill_package(name: str):
    denied = _require_skill_module("write")
    if denied:
        return denied
    user_id = current_user_id()
    from src.agent_platform.catalog.skill_artifacts import ArtifactError, ensure_package
    from src.agent_platform.catalog.skill_packages import (
        can_manage_skill,
        read_package,
        set_skill_owner,
    )

    existing = read_package(name)
    # Creating a brand-new package is itself the ownership claim; mutating an
    # existing one is owner/admin only (a grant is not a write grant).
    if existing is not None and not can_manage_skill(name, user_id):
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    try:
        ensure_package(name, description=str(data.get("description") or ""), created_by=user_id)
    except ArtifactError as exc:
        return jsonify({"error": str(exc)}), 400
    set_skill_owner(name, user_id)
    row = read_package(name)
    if row is None:
        return jsonify({"error": "could not create skill"}), 500
    return jsonify(row), 201


def _legacy_library(user_id: int | None) -> list[dict]:
    """Read-only, access-filtered view of the DB Skill Library."""
    try:
        from src.models.skill import (
            Skill,
            can_manage_skill_id,
            visible_skill_ids,
        )

        visible = visible_skill_ids(user_id)
        out = []
        for skill in Skill.get_all():
            if visible is not None and skill.id not in visible:
                continue
            row = skill.to_dict()
            out.append({
                "id": row.get("id"),
                "name": row.get("name") or "",
                "skill_id": row.get("skill_id") or "",
                "description": row.get("description") or "",
                "category": row.get("category") or "",
                "status": row.get("status") or "",
                "version": row.get("version") or "",
                "updated_at": row.get("updated_at"),
                "can_manage": can_manage_skill_id(skill.id, user_id),
            })
        return out
    except Exception:
        return []
