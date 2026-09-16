from flask import Blueprint, request, jsonify
import logging

from src.agent_platform.catalog.mcp_discovery import (
    clear_mcp_tool_cache,
    list_mcp_servers,
    serialize_mcp_server,
)
from src.auth.decorators import current_user_id_for_rbac, module_required
from src.auth.resource_access import is_admin
from src.models.mcp_server import MCPServer, MCPServerType, can_access_server
from src.models.secrets import merge_masked

# Get the logger
logger = logging.getLogger('text2sql')

# Create Blueprint
mcp_admin_bp = Blueprint('mcp_admin', __name__)


def _can_manage(server: MCPServer, user_id: int | None) -> bool:
    """Only the owner or an administrator may edit/delete a server.

    A role grant confers *use* (connect / list tools); it never confers
    destructive control. An owner-less legacy row is manageable by admins only.
    """
    if user_id is None:
        return False
    if is_admin(user_id):
        return True
    return server.created_by is not None and int(server.created_by) == int(user_id)


def _serialize(server: MCPServer, *, uid: int | None = None,
               tools: list[str] | None = None,
               tools_error: str | None = None) -> dict:
    """Registry row for the admin API: masked config, no live tool list.

    Started/stopped status no longer exists; whether a server actually answers
    is reported per row by the live discovery pass in ``get_mcp_servers``.
    """
    row = {
        'id': server.id,
        'name': server.name,
        'description': server.description,
        'server_type': server.server_type,
        'config': server.config_masked(),
        'created_by': server.created_by,
        'can_manage': _can_manage(server, uid),
        'created_at': server.created_at.isoformat() if server.created_at else None,
        'updated_at': server.updated_at.isoformat() if server.updated_at else None,
    }
    if tools is not None:
        row['tools'] = tools
        row['tools_error'] = tools_error
    return row


@mcp_admin_bp.route('/api/admin/mcp-servers', methods=['GET'])
@module_required("mcp_servers")
def get_mcp_servers():
    """MCP servers the caller may see, with their live tool discovery state."""
    try:
        uid = current_user_id_for_rbac()
        servers = MCPServer.get_visible(uid)
        discovered = {
            row['id']: row for row in list_mcp_servers(user_id=uid, use_cache=True)
        }
        return jsonify({
            'success': True,
            'servers': [
                _serialize(
                    server,
                    uid=uid,
                    tools=discovered.get(server.id, {}).get('tools', []),
                    tools_error=discovered.get(server.id, {}).get('tools_error'),
                )
                for server in servers
            ]
        })
    except Exception as e:
        logger.exception("Error fetching MCP servers")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_admin_bp.route('/api/admin/mcp-servers', methods=['POST'])
@module_required("mcp_servers")
def add_mcp_server():
    """Add a new MCP server."""
    try:
        data = request.json

        # Validate required fields
        required_fields = ['name', 'server_type']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400

        # Validate server type
        if data['server_type'] not in [t.value for t in MCPServerType]:
            return jsonify({
                'success': False,
                'error': f'Invalid server type: {data["server_type"]}'
            }), 400

        # Create server object, stamped with the authenticated owner.
        server = MCPServer(
            name=data['name'],
            description=data.get('description', ''),
            server_type=data['server_type'],
            config=data.get('config', {}),
            created_by=current_user_id_for_rbac(),
        )

        # Save server to database
        server.save()

        return jsonify({
            'success': True,
            'server': _serialize(server, uid=server.created_by, tools=[], tools_error=None)
        })
    except Exception as e:
        logger.exception("Error adding MCP server")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_admin_bp.route('/api/admin/mcp-servers/<int:server_id>', methods=['GET'])
@module_required("mcp_servers")
def get_mcp_server(server_id):
    """Get a specific MCP server the caller may access."""
    try:
        uid = current_user_id_for_rbac()
        server = MCPServer.get_by_id(server_id)
        if not server or not can_access_server(server_id, uid):
            return jsonify({
                'success': False,
                'error': f'Server with ID {server_id} not found'
            }), 404

        return jsonify({
            'success': True,
            'server': _serialize(server, uid=uid)
        })
    except Exception as e:
        logger.exception(f"Error fetching MCP server {server_id}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_admin_bp.route('/api/admin/mcp-servers/<int:server_id>', methods=['PUT'])
@module_required("mcp_servers")
def update_mcp_server(server_id):
    """Update a specific MCP server (owner or administrator only)."""
    try:
        uid = current_user_id_for_rbac()
        server = MCPServer.get_by_id(server_id)
        if not server or not can_access_server(server_id, uid):
            return jsonify({
                'success': False,
                'error': f'Server with ID {server_id} not found'
            }), 404
        if not _can_manage(server, uid):
            return jsonify({
                'success': False,
                'error': 'Only the server owner or an administrator can edit this server'
            }), 403

        data = request.json

        # Update fields
        if 'name' in data:
            server.name = data['name']
        if 'description' in data:
            server.description = data['description']
        if 'server_type' in data:
            if data['server_type'] not in [t.value for t in MCPServerType]:
                return jsonify({
                    'success': False,
                    'error': f'Invalid server type: {data["server_type"]}'
                }), 400
            server.server_type = data['server_type']
        if 'config' in data:
            # The edit form round-trips masked credential values; keep the
            # stored ones when a masked placeholder comes back unchanged.
            server.config = merge_masked(server.config, data['config'])

        # Save server to database
        server.save()
        # The cached tool list belongs to the previous config.
        clear_mcp_tool_cache(server.id)

        return jsonify({
            'success': True,
            'server': _serialize(server, uid=uid)
        })
    except Exception as e:
        logger.exception(f"Error updating MCP server {server_id}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_admin_bp.route('/api/admin/mcp-servers/<int:server_id>', methods=['DELETE'])
@module_required("mcp_servers")
def delete_mcp_server(server_id):
    """Delete a specific MCP server (owner or administrator only)."""
    try:
        uid = current_user_id_for_rbac()
        server = MCPServer.get_by_id(server_id)
        if not server or not can_access_server(server_id, uid):
            return jsonify({
                'success': False,
                'error': f'Server with ID {server_id} not found'
            }), 404
        if not _can_manage(server, uid):
            return jsonify({
                'success': False,
                'error': 'Only the server owner or an administrator can delete this server'
            }), 403

        # Delete server from database
        server.delete()
        clear_mcp_tool_cache(server_id)

        return jsonify({
            'success': True,
            'message': f'Server {server.name} deleted successfully'
        })
    except Exception as e:
        logger.exception(f"Error deleting MCP server {server_id}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@mcp_admin_bp.route('/api/admin/mcp-servers/<int:server_id>/tools', methods=['GET'])
@module_required("mcp_servers")
def get_mcp_server_tools(server_id):
    """Live list_tools for a specific MCP server (always fresh).

    Uses the same connection path as a run, so a server is testable before any
    agent binds it and the reported tools are the ones agents receive. A role
    grant confers this *use*; a user without access gets 404.
    """
    try:
        uid = current_user_id_for_rbac()
        server = MCPServer.get_by_id(server_id)
        if not server or not can_access_server(server_id, uid):
            return jsonify({
                'success': False,
                'error': f'Server with ID {server_id} not found'
            }), 404

        result = serialize_mcp_server(server, user_id=uid, use_cache=False)
        if result['tools_error']:
            return jsonify({
                'success': False,
                'error': result['tools_error'],
                'tools': [],
            }), 200
        return jsonify({
            'success': True,
            'tools': result['tool_details'],
        })
    except Exception as e:
        logger.exception(f"Error getting MCP server tools for server {server_id}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
