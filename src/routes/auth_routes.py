"""
Authentication routes for Text2SQL application.
Handles login, logout, password reset, and related functionality.

GET requests serve the React SPA shell (templates/agent_app.html) so the auth
flows are fully rendered by the React app. POSTs accept both legacy form posts
and JSON request bodies from the React client, and return a JSON payload for
API consumers.
"""

from flask import Blueprint, g, jsonify, redirect, request, session, url_for
from functools import wraps
from src.utils.auth_utils import current_relative_url, safe_next_target
from src.utils.user_manager import UserManager
from config.config import AUTH_PROVIDER

auth_bp = Blueprint('auth', __name__)
user_manager = UserManager()

# Decorator for routes that require admin role
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            g.audit_reason = "authentication required"
            return redirect(url_for('auth.login', next=current_relative_url()))

        if not user_manager.has_role(session.get('user_id'), 'admin'):
            from flask import flash

            g.audit_reason = "admin role required"
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))

        return f(*args, **kwargs)
    return decorated_function

# Decorator for permission-based access control
def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('user_id'):
                g.audit_reason = "authentication required"
                return redirect(url_for('auth.login', next=current_relative_url()))

            if not user_manager.has_permission(session.get('user_id'), permission):
                from flask import flash

                g.audit_reason = f"permission required: {permission}"
                flash('You do not have permission to access this resource.', 'danger')
                return redirect(url_for('index'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def _render_auth_shell():
    """Render the React SPA shell that mounts the auth flow components."""
    from src.routes.agent_routes import _render_agent_app

    return _render_agent_app()


def _payload():
    """Read the request body as JSON if present, otherwise fall back to form data.

    Returns a dict where repeated form fields (e.g. roles) become lists and
    single values stay strings.
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict()


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login as a JSON API (POST) or render the React SPA (GET)."""
    # If already logged in, redirect to index
    if session.get('user_id'):
        return _redirect_or_json(safe_next_target(request.args.get('next')) or url_for('index'))

    if request.method == 'POST':
        data = _payload()
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''

        if not username or not password:
            g.audit_reason = "username and password required"
            if request.is_json:
                return jsonify({'success': False, 'error': 'Username and password are required.'}), 400
            return redirect(url_for('auth.login'))

        # Authenticate user (local or LDAP based on config)
        user_id = user_manager.authenticate(username, password)
        if not user_id:
            # Reason recorded in the audit trail for this rejected login.
            g.audit_reason = "invalid credentials"
            message = (
                'Login failed: invalid credentials or not a member of the allowed group.'
                if AUTH_PROVIDER == 'ldap'
                else 'Invalid username or password.'
            )
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 401
            from flask import flash

            flash(message, 'danger')
            return redirect(url_for('auth.login'))

        # Check if user is active
        user = user_manager.get_user_by_id(user_id)
        if not user.is_active:
            g.audit_reason = "account inactive"
            message = 'Your account is inactive. Please contact an administrator.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 403
            from flask import flash

            flash(message, 'warning')
            return redirect(url_for('auth.login'))

        # Set user session
        session['user_id'] = user_id
        session['username'] = user.username
        session.permanent = True

        # Redirect to the page the user asked for, or to the home page.
        next_page = safe_next_target(
            request.args.get('next') or (data.get('next') if request.is_json else None)
        )
        return _redirect_or_json(next_page or url_for('index'))

    return _render_auth_shell()


def _redirect_or_json(target):
    """Return a JSON success payload for API consumers or an HTTP redirect.

    JSON logins also receive a signed Bearer access token so API clients can
    authenticate without relying on the session cookie.
    """
    if request.is_json:
        payload = {'success': True, 'redirect': target}
        user_id = session.get('user_id')
        if user_id:
            from src.agent_platform.api.tokens import TOKEN_TTL_SECONDS, issue_access_token

            payload['access_token'] = issue_access_token(int(user_id))
            payload['token_type'] = 'Bearer'
            payload['expires_in'] = TOKEN_TTL_SECONDS
        return jsonify(payload)
    return redirect(target)


@auth_bp.route('/logout')
def logout():
    """Handle user logout"""
    # Clear session
    session.clear()

    if request.is_json:
        return jsonify({'success': True})
    return redirect(url_for('auth.login'))


@auth_bp.route('/reset-password-request', methods=['GET', 'POST'])
def reset_password_request():
    """Handle password reset requests"""
    if AUTH_PROVIDER == 'ldap':
        g.audit_reason = "password reset unavailable with LDAP authentication"
        return _redirect_or_json(url_for('auth.login'))

    if request.method == 'POST':
        data = _payload()
        username = (data.get('username') or '').strip()

        if not username:
            g.audit_reason = "username required"
            if request.is_json:
                return jsonify({'success': False, 'error': 'Username is required.'}), 400
            return _render_auth_shell()

        if user_manager.generate_reset_token(username):
            user = user_manager.get_user_by_username(username)
            if user:
                print(f"RESET TOKEN for {username}: {user.reset_token}")
            if request.is_json:
                return jsonify({'success': True, 'message': 'Password reset instructions have been sent to your email.'})
            from flask import flash

            flash('Password reset instructions have been sent to your email.', 'success')
            return redirect(url_for('auth.login'))
        else:
            message = 'Username not found.'
            g.audit_reason = "unknown username"
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 404
            from flask import flash

            flash(message, 'danger')
            return _render_auth_shell()

    return _render_auth_shell()


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Handle password reset with token"""
    if AUTH_PROVIDER == 'ldap':
        g.audit_reason = "password reset unavailable with LDAP authentication"
        return _redirect_or_json(url_for('auth.login'))

    # Verify token
    user_id = user_manager.verify_reset_token(token)

    if not user_id:
        message = 'Invalid or expired token. Please request a new password reset.'
        g.audit_reason = "invalid or expired reset token"
        if request.method == 'POST' and request.is_json:
            return jsonify({'success': False, 'error': message}), 400
        if request.method == 'POST':
            from flask import flash, redirect as _r

            flash(message, 'danger')
            return _r(url_for('auth.reset_password_request'))
        return _render_auth_shell()

    if request.method == 'POST':
        data = _payload()
        password = data.get('password')
        password_confirm = data.get('password_confirm') or data.get('confirm_password')

        if not password or len(password) < 6:
            g.audit_reason = "weak password"
            message = 'Password must be at least 6 characters long.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

        if password != password_confirm:
            g.audit_reason = "password confirmation mismatch"
            message = 'Passwords do not match.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

        if user_manager.reset_password(user_id, password):
            if request.is_json:
                return jsonify({'success': True, 'message': 'Password reset. You can now log in.'})
            from flask import flash

            flash('Your password has been reset successfully.', 'success')
            return redirect(url_for('auth.login'))
        else:
            g.audit_reason = "password reset failed"
            message = 'Failed to reset password. Please try again.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 500
            return _render_auth_shell()

    return _render_auth_shell()


@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    """Allow logged-in users to change their password"""
    if not session.get('user_id'):
        g.audit_reason = "authentication required"
        return redirect(url_for('auth.login', next=current_relative_url()))

    user_id = session.get('user_id')
    if AUTH_PROVIDER == 'ldap':
        g.audit_reason = "password managed by identity provider"
        message = 'Password changes are managed by your identity provider (LDAP).'
        if request.is_json:
            return jsonify({'success': False, 'error': message}), 400
        from flask import flash

        flash(message, 'info')
        return redirect(url_for('index'))

    if request.method == 'POST':
        data = _payload()
        current_password = data.get('current_password') or ''
        new_password = data.get('new_password') or ''
        confirm_password = data.get('confirm_password') or ''

        if not current_password or not new_password or not confirm_password:
            g.audit_reason = "missing password fields"
            message = 'All fields are required.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

        if new_password != confirm_password:
            g.audit_reason = "password confirmation mismatch"
            message = 'New passwords do not match.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

        if len(new_password) < 6:
            g.audit_reason = "weak password"
            message = 'New password must be at least 6 characters long.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

        if user_manager.change_password(user_id, current_password, new_password):
            if request.is_json:
                return jsonify({'success': True, 'message': 'Password updated.'})
            from flask import flash

            flash('Your password has been updated successfully.', 'success')
            return redirect(url_for('index'))
        else:
            g.audit_reason = "current password incorrect"
            message = 'Current password is incorrect.'
            if request.is_json:
                return jsonify({'success': False, 'error': message}), 400
            return _render_auth_shell()

    return _render_auth_shell()
