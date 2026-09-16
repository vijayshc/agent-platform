"""
Admin LLM Connection Manager routes.

The admin "Configuration" key/value page has been removed; this blueprint now
only serves the LLM Connection Manager under ``/admin/config/llm``.

Tenancy: the list is filtered to the connections the caller may use (never a
403), a direct read answers 404 for anything the caller may not use, and every
mutating action (save/delete/set-default/test) is restricted to the owner or an
administrator. A role grant confers *use* (choosing the model), not destructive
control.
"""

from flask import Blueprint, request, jsonify, current_app

from src.auth.decorators import current_user_id_for_rbac, module_required
# Importing the model registers the ``llm_connection`` resource with the generic
# access API (owner + existence resolvers). app.py imports this blueprint at
# startup, so the registration is guaranteed before the first access request.
from src.models.llm_connection import LLMConnection
from src.utils.auth_utils import login_required

# Create the blueprint
config_bp = Blueprint('config', __name__, url_prefix='/admin/config')


# ---------------------------------------------------------------------------
# LLM Connection Manager (admin-managed OpenAI-compatible providers)
# ---------------------------------------------------------------------------

def _current_user_id():
    """The acting user id for resource decisions (session, JWT or API key)."""
    return current_user_id_for_rbac()


def _not_found():
    return jsonify({'status': 'error', 'message': 'Connection not found'}), 404


def _forbidden():
    return jsonify({
        'status': 'error',
        'message': 'Only the connection owner or an administrator can manage this connection',
    }), 403


def _stored_connection(connection_id):
    """The stored ``LLMConnection`` for a numeric id, or ``None``."""
    try:
        return LLMConnection.get_by_id(int(connection_id))
    except (TypeError, ValueError):
        return None


def _connection_row(connection_id: int, user_id):
    """A masked connection payload plus the acting user's capabilities."""
    from src.utils.llm_connection_manager import (
        can_manage_connection,
        can_set_global_default,
        list_connections,
    )

    for conn in list_connections():
        if conn.get('id') == connection_id:
            conn['can_manage'] = can_manage_connection(connection_id, user_id)
            conn['can_set_default'] = can_set_global_default(user_id)
            return conn
    return None


@config_bp.route('/llm', methods=['GET'])
@login_required
@module_required("llm")
def llm_connections_page():
    """Render the LLM Manager page (React)."""
    from src.routes.agent_routes import _render_admin_app
    return _render_admin_app()


@config_bp.route('/llm/api/list', methods=['GET'])
@login_required
@module_required("llm")
def api_llm_list():
    """List the connections the caller may use (API keys masked).

    The list is filtered, never rejected: a user without access to any tenant
    connection still sees the deployment-level (shared) ones, and an unrelated
    user's list simply comes back empty.
    """
    try:
        from src.utils.llm_connection_manager import (
            can_manage_connection,
            can_set_global_default,
            list_connections,
            visible_connection_ids,
        )

        user_id = _current_user_id()
        visible = visible_connection_ids(user_id)
        # Same for every row (it depends only on the identity), but sent top-level
        # too so the UI knows before any row exists (a new connection form).
        can_set_default = can_set_global_default(user_id)
        data = []
        for conn in list_connections():
            if visible is not None and conn.get('id') not in visible:
                continue
            data.append({
                **conn,
                'can_manage': can_manage_connection(conn.get('id'), user_id),
                'can_set_default': can_set_default,
            })
        return jsonify({'status': 'success', 'data': data, 'can_set_default': can_set_default})
    except Exception as e:
        current_app.logger.error(f"Error listing LLM connections: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@config_bp.route('/llm/api/get/<int:connection_id>', methods=['GET'])
@login_required
@module_required("llm")
def api_llm_get(connection_id):
    """Get a single connection the caller may use (API key masked).

    A connection the caller cannot use is indistinguishable from a missing one,
    so an unrelated tenant cannot probe for someone else's connection ids.
    """
    try:
        from src.utils.llm_connection_manager import can_access_connection

        user_id = _current_user_id()
        if not can_access_connection(connection_id, user_id):
            return _not_found()
        row = _connection_row(connection_id, user_id)
        if row is None:
            return _not_found()
        return jsonify({'status': 'success', 'data': row})
    except Exception as e:
        current_app.logger.error(f"Error getting LLM connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@config_bp.route('/llm/api/save', methods=['POST'])
@login_required
@module_required("llm")
def api_llm_save():
    """Create a connection (any authenticated user) or update one they own."""
    try:
        from src.utils.llm_connection_manager import (
            GLOBAL_DEFAULT_ADMIN_ONLY,
            LLMConnectionError,
            can_manage_connection,
            can_set_global_default,
            save_connection,
        )
        from src.utils.common_llm import reset_llm_engine

        payload = request.get_json(silent=True) or {}
        name = (payload.get('name') or '').strip()
        if not name:
            return jsonify({'status': 'error', 'message': 'Connection name is required'}), 400
        user_id = _current_user_id()
        existing = None
        cid = payload.get('id')
        if cid not in (None, ''):
            existing = _stored_connection(cid)
            if existing is None:
                return _not_found()
            if not can_manage_connection(existing.id, user_id):
                return _forbidden()
        # The default flag is deployment-wide: only an administrator may move it,
        # in either direction. Editing other fields (which resends the current
        # flag) is unaffected.
        if 'is_default' in payload:
            current = int(existing.is_default or 0) if existing is not None else 0
            wanted = 1 if payload.get('is_default') else 0
            if wanted != current and not can_set_global_default(user_id):
                return jsonify({'status': 'error', 'message': GLOBAL_DEFAULT_ADMIN_ONLY}), 403
        conn = save_connection(payload, user_id=user_id)
        reset_llm_engine()
        return jsonify({'status': 'success', 'message': 'Connection saved successfully', 'id': conn.id})
    except LLMConnectionError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 403
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error saving LLM connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@config_bp.route('/llm/api/set-default/<int:connection_id>', methods=['POST'])
@login_required
@module_required("llm")
def api_llm_set_default(connection_id):
    """Move the global default to a connection (administrator only).

    The default is deployment-wide state: an owner may manage their own
    connection, but only an administrator may point everyone's default-model
    path at it (a private default would deny model selection to every other
    user). Owners still select their own connection explicitly for a run.
    """
    try:
        from src.utils.llm_connection_manager import (
            GLOBAL_DEFAULT_ADMIN_ONLY,
            LLMConnectionError,
            can_manage_connection,
            can_set_global_default,
            set_default,
        )
        from src.utils.common_llm import reset_llm_engine

        user_id = _current_user_id()
        if _stored_connection(connection_id) is None:
            return _not_found()
        if not can_manage_connection(connection_id, user_id):
            return _forbidden()
        if not can_set_global_default(user_id):
            return jsonify({'status': 'error', 'message': GLOBAL_DEFAULT_ADMIN_ONLY}), 403
        conn = set_default(connection_id, user_id=user_id)
        if conn is None:
            return _not_found()
        reset_llm_engine()
        return jsonify({'status': 'success', 'message': 'Default connection updated'})
    except LLMConnectionError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 403
    except Exception as e:
        current_app.logger.error(f"Error setting default LLM connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@config_bp.route('/llm/api/delete/<int:connection_id>', methods=['DELETE'])
@login_required
@module_required("llm")
def api_llm_delete(connection_id):
    """Delete a connection (owner or administrator only)."""
    try:
        from src.utils.llm_connection_manager import can_manage_connection, delete_connection
        from src.utils.common_llm import reset_llm_engine

        user_id = _current_user_id()
        if _stored_connection(connection_id) is None:
            return _not_found()
        if not can_manage_connection(connection_id, user_id):
            return _forbidden()
        ok = delete_connection(connection_id)
        if not ok:
            return _not_found()
        reset_llm_engine()
        return jsonify({'status': 'success', 'message': 'Connection deleted successfully'})
    except Exception as e:
        current_app.logger.error(f"Error deleting LLM connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@config_bp.route('/llm/api/test', methods=['POST'])
@login_required
@module_required("llm", read_methods=("GET", "HEAD", "OPTIONS", "POST"))
def api_llm_test():
    """Test a connection by making a tiny chat completion against its payload.

    A stored connection may only be tested by its owner or an administrator; a
    brand-new payload (no stored id) is the caller's own unsaved form and is
    tested as typed.
    """
    try:
        from src.utils.llm_connection_manager import can_manage_connection, test_connection

        payload = request.get_json(silent=True) or {}
        cid = payload.get('id')
        if cid not in (None, ''):
            existing = _stored_connection(cid)
            if existing is None:
                return _not_found()
            if not can_manage_connection(existing.id, _current_user_id()):
                return _forbidden()
        result = test_connection(payload)
        if result.get('ok'):
            return jsonify({'status': 'success', **result})
        return jsonify({
            'status': 'error',
            'message': result.get('error', 'Connection test failed'),
            'endpoint': result.get('endpoint'),
            'model': result.get('model'),
        }), 200
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Error testing LLM connection: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
