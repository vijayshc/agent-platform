"""Shared routes for browser-local LLM proxy request handling."""

from flask import Blueprint, jsonify, request

from src.utils.auth_utils import login_required
from src.utils.browser_llm_proxy import browser_llm_response_manager


browser_llm_proxy_bp = Blueprint('browser_llm_proxy', __name__)


@browser_llm_proxy_bp.route('/api/browser-llm/response', methods=['POST'])
@login_required
def submit_browser_llm_response():
    """Accept a browser-local LLM response for a pending shared proxy request."""
    payload = request.get_json() or {}
    request_id = str(payload.get('request_id') or '').strip()
    response_text = payload.get('response')
    error_text = payload.get('error')

    if not request_id:
        return jsonify({
            'success': False,
            'error': 'request_id is required',
        }), 400

    if response_text is None and error_text is None:
        return jsonify({
            'success': False,
            'error': 'response or error is required',
        }), 400

    success = browser_llm_response_manager.submit_response(
        request_id,
        response=response_text,
        error=error_text,
    )

    if not success:
        return jsonify({
            'success': False,
            'error': 'Pending browser-local LLM request not found',
        }), 404

    return jsonify({
        'success': True,
        'message': 'Browser-local LLM response accepted',
    })