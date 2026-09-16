"""
Admin API routes for Text2SQL application.
Provides RESTful API endpoints for admin functionality.
"""

from flask import Blueprint, request, jsonify, session
from src.utils.user_manager import EscalationGuardError, UserManager
from src.utils.feedback_manager import FeedbackManager
from src.auth.decorators import admin_required, module_required

admin_api_bp = Blueprint('admin_api', __name__, url_prefix='/admin/api')
user_manager = UserManager()

# Initialize feedback manager
feedback_manager = FeedbackManager()

@admin_api_bp.route('/roles', methods=['POST'])
@admin_required()
@module_required("roles")
def create_role():
    """Create a new role"""
    data = request.json
    
    if not data or 'name' not in data:
        return jsonify({'error': 'Role name is required'}), 400
    
    name = data['name']
    description = data.get('description', '')
    
    try:
        role_id = user_manager.create_role(name, description)
        
        return jsonify({
            'success': True,
            'message': f'Role {name} created successfully',
            'role_id': role_id
        }), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/roles/<int:role_id>', methods=['PUT'])
@admin_required()
@module_required("roles")
def update_role(role_id):
    """Update an existing role"""
    data = request.json
    
    if not data or 'name' not in data:
        return jsonify({'error': 'Role name is required'}), 400
    
    name = data['name']
    description = data.get('description', '')
    
    try:
        # Check if role exists
        role = user_manager.get_role_by_id(role_id)
        if not role:
            return jsonify({'error': 'Role not found'}), 404
        
        # Prevent updating admin role name
        if role.name == 'admin' and name != 'admin':
            return jsonify({'error': 'Cannot change the admin role name'}), 403
        
        user_manager.update_role(role_id, name, description)
        
        return jsonify({
            'success': True,
            'message': f'Role {name} updated successfully'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/roles/<int:role_id>', methods=['DELETE'])
@admin_required()
@module_required("roles")
def delete_role(role_id):
    """Delete a role"""
    try:
        # Check if role exists
        role = user_manager.get_role_by_id(role_id)
        if not role:
            return jsonify({'error': 'Role not found'}), 404
        
        # Prevent deleting admin role
        if role.name == 'admin':
            return jsonify({'error': 'Cannot delete the admin role'}), 403
        
        role_name = role.name
        user_manager.delete_role(role_id)
        
        return jsonify({
            'success': True,
            'message': f'Role {role_name} deleted successfully'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/roles/<int:role_id>/permissions', methods=['GET'])
@admin_required()
@module_required("roles")
def get_role_permissions(role_id):
    """Get the modules granted to a role.

    ``permissions`` / ``permission_ids`` are kept in the payload for older
    clients, but the supported response field is ``modules``.
    """
    try:
        role = user_manager.get_role_by_id(role_id)
        if not role:
            return jsonify({'error': 'Role not found'}), 404

        permissions = user_manager.get_role_permissions(role_id)
        module_levels = user_manager.get_role_module_levels(role_id)
        return jsonify({
            'success': True,
            'role_id': role_id,
            'role_name': role.name,
            'modules': list(module_levels.keys()),
            'module_levels': module_levels,
            'permissions': [p.name for p in permissions],
            'permission_ids': [p.id for p in permissions],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/roles/<int:role_id>/permissions', methods=['PUT'])
@admin_required()
@module_required("roles")
def update_role_permissions(role_id):
    """Replace a role's module access.

    Preferred payload::

        {"modules": ["knowledge", "users"]}

    Legacy clients may still send ``{"permissions": [permission_id, ...]}``;
    those IDs are resolved to module permissions.
    """
    data = request.get_json(silent=True) or {}

    try:
        role = user_manager.get_role_by_id(role_id)
        if not role:
            return jsonify({'error': 'Role not found'}), 404

        if role.name == 'admin':
            return jsonify({'error': 'Admin always has access to every module and cannot be modified.'}), 403

        if 'modules' in data:
            module_keys = data.get('modules') or []
            if not isinstance(module_keys, list):
                return jsonify({'error': 'modules must be a list of module keys.'}), 400
            user_manager.update_role_modules(role_id, module_keys)
        elif 'permissions' in data:
            permission_ids = data.get('permissions') or []
            if not isinstance(permission_ids, list):
                return jsonify({'error': 'permissions must be a list of permission ids.'}), 400
            user_manager.update_role_permissions(role_id, permission_ids)
        else:
            return jsonify({'error': 'modules list is required.'}), 400

        module_levels = user_manager.get_role_module_levels(role_id)
        return jsonify({
            'success': True,
            'role_id': role_id,
            'role_name': role.name,
            'modules': list(module_levels.keys()),
            'module_levels': module_levels,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/roles', methods=['GET'])
@admin_required()
@module_required("roles", "users")
def get_all_roles():
    """Get all roles.

    Admin-only: the role catalog is itself privileged data (it reveals who can
    do what) and the only shipped consumers are the admin Roles/Users pages.
    The generic sharing UI reads its own role list from
    ``/api/v1/access/<type>/<id>`` and the platform studio from
    ``/api/v1/studio/roles``, so nothing outside the admin area depends on this
    endpoint.
    """
    try:
        roles = user_manager.get_all_roles()
        role_list = []
        
        for role in roles:
            role_data = {
                'id': role.id,
                'name': role.name,
                'description': role.description,
                'user_count': len(role.users)
            }
            role_list.append(role_data)
        
        return jsonify({
            'success': True,
            'roles': role_list
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_api_bp.route('/modules', methods=['GET'])
@admin_required()
@module_required("roles")
def list_modules():
    """Return the canonical module catalog used by the role editor."""
    from src.auth.modules import MODULES

    return jsonify({
        'success': True,
        'modules': [module.as_dict() for module in MODULES],
    })


@admin_api_bp.route('/embeddings/migrate', methods=['GET', 'POST'])
@admin_required()
@module_required("vector_db")
def migrate_embeddings():
    """Migrate existing embeddings from SQLite to ChromaDB vector database
    
    GET: Returns status and statistics about embeddings
    POST: Executes the migration process
    """
    try:
        # Connect to the feedback manager if not already connected
        if not feedback_manager.engine:
            feedback_manager.connect()
        
        # For GET requests, just return statistics
        if request.method == 'POST':
            # Get stats from SQLite and ChromaDB
            stats = feedback_manager.get_feedback_stats()
            
            # Check if vector store is connected
            vector_store_connected = feedback_manager.vector_store.client is not None
            
            # Return status info
            return jsonify({
                'success': True,
                'total_feedback_entries': stats['total'],
                'positive_feedback': stats['positive'],
                'negative_feedback': stats['negative'],
                'vector_store_connected': vector_store_connected,
                'ready_for_migration': vector_store_connected and stats['total'] > 0
            })
        
        # For POST requests, execute the migration
        else:
            # Execute the migration
            result = feedback_manager.migrate_existing_embeddings()
            
            if result['success']:
                
                return jsonify({
                    'success': True,
                    'message': f"Successfully migrated {result['migrated']} of {result['total']} embeddings",
                    'migrated': result['migrated'],
                    'failed': result['failed'],
                    'total': result['total'],
                    'time_seconds': result['time_seconds']
                })
            else:
                
                return jsonify({
                    'success': False,
                    'message': "Failed to migrate embeddings to vector database"
                }), 500
    except Exception as e:
        
        return jsonify({'error': str(e)}), 500


# ==================== User management (JSON, React admin) ====================

def _user_dict(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "roles": [
            {"id": r.id, "name": r.name, "description": r.description} for r in user.roles
        ],
    }


@admin_api_bp.route('/users', methods=['GET'])
@admin_required()
@module_required("users")
def api_list_users():
    """List all users (JSON for React admin)."""
    try:
        users = user_manager.get_all_users()
        return jsonify({"success": True, "users": [_user_dict(u) for u in users]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_api_bp.route('/users', methods=['POST'])
@admin_required()
@module_required("users")
def api_create_user():
    """Create a user from JSON payload."""
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    role_ids = [int(r) for r in data.get("roles") or [] if str(r).isdigit()]

    if not username or not email or not password:
        return jsonify({"error": "Username, email, and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400

    try:
        user_id = user_manager.create_user(username, email, password)
        for role_id in role_ids:
            user_manager.add_user_to_role(user_id, role_id)
        user = user_manager.get_user_by_id(user_id)
        return jsonify({"success": True, "user": _user_dict(user)}), 201
    except EscalationGuardError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_api_bp.route('/users/<int:user_id>', methods=['PUT'])
@admin_required()
@module_required("users")
def api_update_user(user_id):
    """Update a user from JSON payload."""
    user = user_manager.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found."}), 404

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    is_active = data.get("is_active")
    role_ids = [int(r) for r in data.get("roles") or [] if str(r).isdigit()]

    # The escalation guard is authoritative: a forbidden change on a protected
    # account is refused with 403 before generic field validation, so a partial
    # payload cannot turn a refusal into an unrelated 400.
    try:
        user_manager.assert_user_update_allowed(
            user, is_active=is_active, role_ids=role_ids
        )
    except EscalationGuardError as e:
        return jsonify({"error": str(e)}), 403

    if not username or not email:
        return jsonify({"error": "Username and email are required."}), 400
    if password and len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400

    try:
        user_manager.update_user(
            user_id,
            username,
            email,
            password if password else None,
            is_active=is_active,
        )
        current_role_ids = [r.id for r in user.roles]
        for rid in current_role_ids:
            if rid not in role_ids:
                user_manager.remove_user_from_role(user_id, rid)
        for rid in role_ids:
            if rid not in current_role_ids:
                user_manager.add_user_to_role(user_id, rid)
        user = user_manager.get_user_by_id(user_id)
        return jsonify({"success": True, "user": _user_dict(user)})
    except EscalationGuardError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_api_bp.route('/users/<int:user_id>', methods=['DELETE'])
@admin_required()
@module_required("users")
def api_delete_user(user_id):
    """Delete a user from JSON request."""
    user = user_manager.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found."}), 404

    # The escalation guard owns the status code: a protected account (built-in
    # admin or last active administrator) is always refused with 403, before
    # the self-delete convenience check can answer something else.
    try:
        user_manager.assert_user_deletable(user)
    except EscalationGuardError as e:
        return jsonify({"error": str(e)}), 403

    if user_id == session.get('user_id'):
        return jsonify({"error": "Cannot delete your own account."}), 403

    try:
        user_manager.delete_user(user_id)
        return jsonify({"success": True})
    except EscalationGuardError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500