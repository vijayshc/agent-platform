from __future__ import annotations

from flask import Blueprint
from src.agent_platform.agui.api import create_agui_blueprint
from src.agent_platform.api.access_routes import access_bp
from src.agent_platform.api.capability_routes import capability_bp
from src.agent_platform.api.catalog_routes import catalog_bp
from src.agent_platform.api.conversation_routes import conversation_bp
from src.agent_platform.api.run_routes import run_bp
from src.agent_platform.api.session_routes import session_bp
from src.agent_platform.api.skill_package_routes import skill_package_bp
from src.agent_platform.api.workspace_routes import workspace_bp


def create_blueprint() -> Blueprint:
    from src.agent_platform.eval.api import register_eval_routes

    bp = Blueprint("agent_platform_v1", __name__, url_prefix="/api/v1")
    bp.register_blueprint(create_agui_blueprint())
    bp.register_blueprint(catalog_bp)
    bp.register_blueprint(capability_bp)
    bp.register_blueprint(access_bp)
    bp.register_blueprint(skill_package_bp)
    bp.register_blueprint(workspace_bp)
    bp.register_blueprint(run_bp)
    bp.register_blueprint(conversation_bp)
    bp.register_blueprint(session_bp)
    register_eval_routes(bp)
    return bp
