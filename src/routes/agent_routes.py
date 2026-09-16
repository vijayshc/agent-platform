from flask import Blueprint, current_app

from src.utils.auth_utils import login_required
from src.auth.decorators import module_required
import json
import logging
import os

logger = logging.getLogger("text2sql")

agent_bp = Blueprint("agent", __name__)

_agent_asset_cache = {"mtime": None, "js": None, "css": None}


def _resolve_agent_assets():
    """Return the content-hashed JS/CSS bundle filenames produced by Vite.

    Reading the Vite build manifest guarantees the served assets are the latest
    build, so no manual cache-busting version string is required. Falls back to
    the legacy deterministic filenames if the manifest is not present.
    """
    manifest_path = os.path.join(
        current_app.static_folder, "agent-app", ".vite", "manifest.json"
    )
    try:
        mtime = os.path.getmtime(manifest_path)
    except OSError:
        return {
            "agent_js": "agent-app/assets/index.js",
            "agent_css": "agent-app/assets/index.css",
        }
    if _agent_asset_cache["mtime"] != mtime:
        with open(manifest_path) as f:
            manifest = json.load(f)
        entry = next((item for item in manifest.values() if item.get("isEntry")), None)
        css = (entry or {}).get("css") or []
        _agent_asset_cache["mtime"] = mtime
        _agent_asset_cache["js"] = "agent-app/" + entry["file"]
        _agent_asset_cache["css"] = (
            "agent-app/" + css[0] if css else "agent-app/assets/index.css"
        )
    return {"agent_js": _agent_asset_cache["js"], "agent_css": _agent_asset_cache["css"]}


def _render_agent_app(error_code: int | None = None):
    from flask import render_template

    # The React SPA fetches everything it needs from the v1 API; the shell only
    # needs the error code and the content-hashed asset names. Building a
    # SchemaManager here would re-read and re-parse schema.json on every page
    # load for a value no template consumes.
    return render_template(
        "agent_app.html",
        error_code=error_code,
        **_resolve_agent_assets(),
    )


def _render_admin_app():
    """Render the React admin shell for legacy /admin/* page routes.

    The React client routes internally on window.location.pathname to the
    correct admin component. Using the same agent_app.html template keeps a
    single bundle and the full-viewport layout. Module decorators on the calling
    routes still enforce access control.
    """
    return _render_agent_app()


@agent_bp.route("/", methods=["GET"])
@agent_bp.route("/agent", methods=["GET"])
@login_required
def agent_page():
    return _render_agent_app()


@agent_bp.route("/observability", methods=["GET"])
@module_required("observability")
def observability_page():
    return _render_agent_app()


@agent_bp.route("/agent-runs", methods=["GET"])
@module_required("observability")
def agent_runs_page():
    """Legacy /agent-runs → rebuilt /observability (keeps query string)."""
    from flask import redirect, request, url_for

    query = request.query_string.decode("utf-8") if request.query_string else ""
    target = url_for("agent.observability_page")
    return redirect(f"{target}?{query}" if query else target)


@agent_bp.route("/agent-studio", methods=["GET"])
@agent_bp.route("/agent-studio/editor", methods=["GET"])
@module_required("agent_studio")
def agent_studio_page():
    return _render_agent_app()
