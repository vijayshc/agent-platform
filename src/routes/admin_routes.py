"""
Admin routes for Text2SQL application.
Handles user management and role management pages.
"""

from flask import Blueprint, redirect, request, jsonify
from src.auth.decorators import admin_required, module_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/')
@module_required("dashboard")
def admin_index():
    """Admin dashboard (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()
    
@admin_bp.route('/mcp-servers')
@module_required("mcp_servers")
def mcp_servers():
    """MCP Server management page (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@admin_bp.route('/agent-teams')
@module_required("agent_studio")
def agent_teams():
    """Legacy /admin/agent-teams → Agent Studio (same permission)."""
    return redirect("/agent-studio")

@admin_bp.route('/agent-runs')
@module_required("observability")
def agent_runs():
    """Legacy /admin/agent-runs → /observability (same permission)."""
    return redirect("/observability")

@admin_bp.route('/users')
@admin_required()
@module_required("users")
def list_users():
    """List all users (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@admin_bp.route('/users/create', methods=['GET'])
@admin_required()
@module_required("users")
def create_user():
    """Create a new user (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@admin_bp.route('/users/<int:user_id>/edit', methods=['GET'])
@admin_required()
@module_required("users")
def edit_user(user_id):
    """Edit an existing user (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@admin_bp.route('/roles')
@admin_required()
@module_required("roles")
def list_roles():
    """List all roles and permissions (React). Admin-only."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()

@admin_bp.route('/api/dashboard/analytics')
@module_required("dashboard")
def dashboard_analytics():
    """Full analytics payload for the React dashboard (charts + metrics)."""
    from src.utils.dashboard_analytics import build_dashboard_payload
    days = request.args.get('days', default=14, type=int)
    days = max(7, min(days, 90))
    return jsonify(build_dashboard_payload(days))
