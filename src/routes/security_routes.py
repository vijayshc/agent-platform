"""
Security enhancement routes for the Text2SQL application.
Implements advanced security features including:
- CSRF protection
- Rate limiting
- Secure session management
- Password policy enforcement
"""

from flask import Blueprint, current_app, g, request, session, redirect, url_for, jsonify, abort, render_template, flash
import functools
import time
import re
import uuid
import threading
from datetime import datetime, timedelta
import secrets
from urllib.parse import urlparse
from src.utils.user_manager import UserManager
from src.utils.auth_utils import current_relative_url
from src.auth.decorators import get_route_requirements
import logging
from config.config import BROWSER_LLM_PROXY_ENABLED, BROWSER_LLM_PROXY_URL

# Initialize blueprint
security_bp = Blueprint('security', __name__)

# Initialize user manager
user_manager = UserManager()

# Logger
logger = logging.getLogger('text2sql')

# Thread-safe locks for rate limiting dictionaries
rate_limit_lock = threading.Lock()
failed_login_lock = threading.Lock()
ip_login_lock = threading.Lock()

# Store for rate limiting (in production, use Redis or another distributed cache)
rate_limit_store = {}
failed_login_attempts = {}
ip_login_attempts = {}

# Session configurations
SESSION_TIMEOUT = 30 * 60  # 30 minutes in seconds
SESSION_ABSOLUTE_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds


# Custom CSRF token functions
def generate_csrf_token():
    """Generate a new CSRF token and store in session"""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(16)
    return session['_csrf_token']


def validate_csrf_token(token):
    """Validate CSRF token against the one in session"""
    session_token = session.get('_csrf_token')
    if not session_token:
        return False
    return session_token == token


# CSRF protection decorator
def csrf_protect(f):
    """Decorator to check for CSRF token in POST/PUT/DELETE requests"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
            json_body = request.get_json(silent=True) or {}
            token = (
                request.form.get('_csrf_token')
                or request.headers.get('X-CSRF-Token')
                or (json_body.get('_csrf_token') if isinstance(json_body, dict) else None)
            )
            if not token or not validate_csrf_token(token):
                logger.warning(f"CSRF validation failed for {request.path}")
                abort(403, description="CSRF validation failed")
        return f(*args, **kwargs)
    return decorated_function


def validate_password_strength(password):
    """
    Validate password strength based on security best practices
    Returns (is_valid, message) tuple
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters long"
    
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
        
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
        
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one number"
        
    if not re.search(r'[^A-Za-z0-9]', password):
        return False, "Password must contain at least one special character"
        
    # Check for common passwords (in production, use a more comprehensive list)
    common_passwords = ['Password123!', 'Admin123!', 'Welcome123!']
    if password in common_passwords:
        return False, "Password is too common"
        
    return True, "Password is strong"


def requires_fresh_login(f):
    """
    Decorator to require a fresh login for sensitive operations
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            g.audit_reason = "authentication required"
            return redirect(url_for('auth.login'))
            
        # Check if the login is fresh (less than 10 minutes old)
        if not session.get('login_fresh') or \
           datetime.utcnow().timestamp() - session.get('login_time', 0) > 600:
            # Store the original URL for redirect after re-authentication
            g.audit_reason = "fresh login required"
            session['next_url'] = current_relative_url()
            return redirect(url_for('security.reauthenticate'))
            
        return f(*args, **kwargs)
    return decorated_function


def rate_limit(limit=5, per=60, scope='default'):
    """
    Rate limiting decorator
    - limit: maximum number of calls
    - per: time period in seconds
    - scope: unique name for the rate limit
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # Check if rate limiting is enabled
            from config.config import RATE_LIMIT_ENABLED
            
            if not RATE_LIMIT_ENABLED:
                # RATE LIMITING DISABLED - Bypass all rate limit checks
                response = f(*args, **kwargs)
                return response
            
            # Get client identifier (IP address or user_id if logged in)
            client_id = session.get('user_id', request.remote_addr)
            
            # Create a unique key for this client and endpoint
            key = f"{client_id}:{scope}"
            
            # Use thread-safe lock for rate limit store access
            with rate_limit_lock:
                # Initialize or get rate limiting data
                if key not in rate_limit_store:
                    rate_limit_store[key] = {'count': 0, 'reset_time': time.time() + per}
                
                # Reset count if time period has passed
                if time.time() > rate_limit_store[key]['reset_time']:
                    rate_limit_store[key] = {'count': 0, 'reset_time': time.time() + per}
                
                # Check limit
                if rate_limit_store[key]['count'] >= limit:
                    # Set rate limit headers
                    headers = {
                        'X-RateLimit-Limit': str(limit),
                        'X-RateLimit-Remaining': '0',
                        'X-RateLimit-Reset': str(int(rate_limit_store[key]['reset_time']))
                    }
                    return jsonify({'error': 'Too many requests'}), 429, headers
                    
                # Increment count
                rate_limit_store[key]['count'] += 1
                
                # Set rate limit headers
                headers = {
                    'X-RateLimit-Limit': str(limit),
                    'X-RateLimit-Remaining': str(limit - rate_limit_store[key]['count']),
                    'X-RateLimit-Reset': str(int(rate_limit_store[key]['reset_time']))
                }
            
            # Execute the function
            response = f(*args, **kwargs)
            
            # Add headers to the response
            if isinstance(response, tuple) and len(response) >= 3:
                # Response with status and headers
                body, status, existing_headers = response
                existing_headers.update(headers)
                return body, status, existing_headers
            elif isinstance(response, tuple) and len(response) == 2:
                # Response with status but no headers
                body, status = response
                return body, status, headers
            else:
                # Response is just the body
                return response
                
        return decorated_function
    return decorator


@security_bp.before_app_request
def session_timeout_check():
    """
    Check and enforce session timeout before each request
    """
    # Skip for static resources
    if request.path.startswith('/static'):
        return
        
    if 'user_id' in session:
        # Check for absolute timeout (max session length)
        if 'session_start' in session and \
           datetime.utcnow().timestamp() - session['session_start'] > SESSION_ABSOLUTE_TIMEOUT:
            # Session has expired absolutely, force logout
            g.audit_reason = "session expired"
            session.clear()
            return redirect(url_for('auth.login'))
            
        # Check for inactivity timeout
        if 'last_active' in session and \
           datetime.utcnow().timestamp() - session['last_active'] > SESSION_TIMEOUT:
            # Session has expired due to inactivity, force logout
            g.audit_reason = "session timed out"
            session.clear()
            return redirect(url_for('auth.login'))
            
        # Update last activity time
        session['last_active'] = datetime.utcnow().timestamp()


@security_bp.after_app_request
def add_security_headers(response):
    """
    Add security-related headers to all responses
    """
    # Content Security Policy - Updated to include DataTables CDN and other necessary resources
    connect_sources = ["'self'"]
    if BROWSER_LLM_PROXY_URL:
        parsed_proxy = urlparse(BROWSER_LLM_PROXY_URL)
        if parsed_proxy.scheme and parsed_proxy.netloc:
            connect_sources.append(f"{parsed_proxy.scheme}://{parsed_proxy.netloc}")

    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://code.jquery.com https://cdn.datatables.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com https://cdn.datatables.net",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://cdn.datatables.net",
        "img-src 'self' data: blob: https://cdn.datatables.net",
        "worker-src 'self' blob:",
        f"connect-src {' '.join(connect_sources)} http://127.0.0.1:6006 http://localhost:6006",
        "frame-src 'self' http://127.0.0.1:6006 http://localhost:6006"
    ]
    response.headers['Content-Security-Policy'] = "; ".join(csp_directives)
    
    # Prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    
    # Prevent clickjacking
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    
    # Enable XSS protection in browsers
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Control caching of sensitive information
    if 'Cache-Control' not in response.headers:
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    
    return response


@security_bp.route('/reauthenticate', methods=['GET', 'POST'])
@csrf_protect
def reauthenticate():
    """
    Require re-authentication for sensitive operations
    """
    error = None
    if request.method == 'POST':
        username = session.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            error = "Username and password required"
            g.audit_reason = "username and password required"
        else:
            user_id = user_manager.authenticate(username, password)
            
            if user_id:
                # Update session with fresh login time
                session['login_fresh'] = True
                session['login_time'] = datetime.utcnow().timestamp()

                # Success and failure are both recorded by the central audit trail.

                # Redirect to original destination
                next_url = session.pop('next_url', url_for('index'))
                if request.is_json:
                    return jsonify({'success': True, 'redirect': next_url})
                return redirect(next_url)
            else:
                error = "Invalid password"
                # Tell the audit trail why the reauthentication was rejected.
                g.audit_reason = "invalid password"
    
    # For GET request or failed POST from a JSON client, return a json payload so
    # the React SPA can render the reauthentication prompt inline. Otherwise serve
    # the SPA shell (legacy form submission still works via the POST branch above).
    if request.is_json:
        return jsonify({'success': False, 'error': error}), 401 if error else 200
    from src.routes.agent_routes import _render_admin_app

    return _render_admin_app()


@security_bp.route('/rotate-session', methods=['POST'])
def rotate_session():
    """
    Rotate session ID to prevent session fixation attacks
    This should be called after login, privilege changes, etc.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
        
    # Store important session data
    user_id = session.get('user_id')
    username = session.get('username')
    
    # Generate a new session ID while preserving the data
    session.sid = secrets.token_urlsafe(32)
    
    # Restore critical data
    session['user_id'] = user_id
    session['username'] = username
    session['session_start'] = datetime.utcnow().timestamp()
    session['last_active'] = datetime.utcnow().timestamp()
    
    return jsonify({'success': True}), 200


def is_rate_limited_for_login(ip_address):
    """
    Check if an IP address is rate-limited for login attempts
    """
    from config.config import RATE_LIMIT_ENABLED, LOGIN_ATTEMPT_LIMIT, LOGIN_ATTEMPT_WINDOW
    
    # Check if rate limiting is enabled
    if not RATE_LIMIT_ENABLED:
        return False, 0
    
    now = datetime.utcnow().timestamp()
    
    with ip_login_lock:
        # Initialize tracking for this IP if it doesn't exist
        if ip_address not in ip_login_attempts:
            ip_login_attempts[ip_address] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,  # Time window from config
                'blocked_until': 0
            }
        
        # Check if IP is currently blocked
        if ip_login_attempts[ip_address]['blocked_until'] > now:
            return True, int(ip_login_attempts[ip_address]['blocked_until'] - now)
        
        # Reset attempts if the window has passed
        if now > ip_login_attempts[ip_address]['reset_time']:
            ip_login_attempts[ip_address] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,
                'blocked_until': 0
            }
        
        # Check if too many attempts in this window
        if ip_login_attempts[ip_address]['attempts'] >= LOGIN_ATTEMPT_LIMIT:  # From config
            # Block for increasing amount of time
            block_minutes = min(60, 5 * (ip_login_attempts[ip_address]['attempts'] - (LOGIN_ATTEMPT_LIMIT - 1)))
            ip_login_attempts[ip_address]['blocked_until'] = now + (block_minutes * 60)
            return True, block_minutes * 60
        
        return False, 0


def record_failed_login(username, ip_address):
    """
    Record a failed login attempt for both username and IP
    """
    from config.config import RATE_LIMIT_ENABLED, LOGIN_ATTEMPT_WINDOW
    
    if not RATE_LIMIT_ENABLED:
        return  # Do nothing if rate limiting is disabled
    
    now = datetime.utcnow().timestamp()
    
    # Use thread-safe locks for all dictionary accesses
    with failed_login_lock:
        # Track by username
        if username not in failed_login_attempts:
            failed_login_attempts[username] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW
            }
        
        if now > failed_login_attempts[username]['reset_time']:
            failed_login_attempts[username] = {
                'attempts': 1,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW
            }
        else:
            failed_login_attempts[username]['attempts'] += 1
    
    with ip_login_lock:
        # Track by IP
        if ip_address not in ip_login_attempts:
            ip_login_attempts[ip_address] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,
                'blocked_until': 0
            }
        
        if now > ip_login_attempts[ip_address]['reset_time']:
            ip_login_attempts[ip_address] = {
                'attempts': 1,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,
                'blocked_until': 0
            }
        else:
            ip_login_attempts[ip_address]['attempts'] += 1


def clear_login_attempts(username, ip_address):
    """
    Clear login attempts on successful login
    """
    from config.config import RATE_LIMIT_ENABLED
    
    if not RATE_LIMIT_ENABLED:
        return  # Do nothing if rate limiting is disabled
    
    with failed_login_lock:
        if username in failed_login_attempts:
            del failed_login_attempts[username]
    
    with ip_login_lock:
        if ip_address in ip_login_attempts:
            # Keep the IP in the tracking dict but reset attempts
            ip_login_attempts[ip_address]['attempts'] = 0


def check_for_account_lockout(username):
    """
    Check if an account should be locked due to too many failed attempts
    """
    from config.config import RATE_LIMIT_ENABLED, FAILED_LOGIN_LOCKOUT_THRESHOLD
    
    if not RATE_LIMIT_ENABLED:
        return False  # Never lock accounts if rate limiting is disabled
    
    with failed_login_lock:
        if username in failed_login_attempts:
            # Lock account after configured number of failed attempts
            return failed_login_attempts[username]['attempts'] >= FAILED_LOGIN_LOCKOUT_THRESHOLD
        
        return False


# Agent-execution endpoints authorize per-agent via the user -> role -> agent
# check inside the route. The session/endpoint RBAC below cannot know which
# agent is being addressed, so authenticated callers are deferred to it.
_AGENT_EXEC_ENDPOINTS = frozenset(
    {
        "run_api.invoke_agent",
        "run_api.invoke_agent_stream",
        "run_api.create_run",
        "run_api.post_conversation_message",
        "agent_platform_agui.agui_input",
    }
)


def _extract_credential() -> str:
    header = request.headers.get('X-API-Key') or request.headers.get('Authorization') or ''
    if header.lower().startswith('bearer '):
        header = header[7:]
    return header.strip()


def _resolve_credential_user() -> tuple[str, int | None] | None:
    """Validate an API key or JWT access token from the request headers.

    Returns ``(auth_type, user_id)`` for a valid credential, else ``None``.
    """
    raw = _extract_credential()
    if not raw:
        return None
    from src.agent_platform.execution.api_keys import ApiKeyStore

    key = ApiKeyStore.verify(raw)
    if key is not None:
        return ("api_key", key.get("user_id"))
    from src.agent_platform.api.tokens import read_access_token

    token_user = read_access_token(raw)
    if token_user is not None:
        return ("token", token_user)
    return None


@security_bp.before_app_request
def enforce_own_origin_for_writes():
    """Refuse a state-changing request that a *different* origin initiated.

    Hosted apps are served from their own origin, so this is what stands between
    an app's page and the platform's write endpoints: a browser always names the
    initiating origin on a non-safe request, and it cannot be forged. A
    cookie-authenticated form post from the apps origin would otherwise still be
    delivered (cookies are port-agnostic and same-site), even though its response
    is unreadable.

    Requests without an ``Origin`` header - scripts, API-key and bearer clients,
    the test suites - are untouched: they carry no browser context to confuse.
    """
    from urllib.parse import urlsplit

    if (request.method or "").upper() in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None
    origin = (request.headers.get("Origin") or "").strip()
    if not origin:
        return None

    from config import config as platform_config

    allowed = {request.host}
    configured_platform = str(getattr(platform_config, "HOSTED_APPS_PLATFORM_ORIGIN", "") or "").strip()
    if configured_platform:
        allowed.add(urlsplit(configured_platform).netloc)

    if origin == "null" or urlsplit(origin).netloc not in allowed:
        g.audit_reason = f"cross-origin write refused (origin {origin!r})"
        logger.warning("Refused %s %s from origin %s", request.method, request.path, origin)
        if request.path.startswith("/api/") or request.path.startswith("/admin/api/") or request.is_json:
            return jsonify({"error": "Cross-origin request refused"}), 403
        return (
            "<!doctype html><meta charset='utf-8'><title>Forbidden</title>"
            "<h1>Cross-origin request refused</h1>"
            "<p>This action must be started from the platform's own pages.</p>",
            403,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    return None


@security_bp.before_app_request
def check_access_control():
    """Enforce module authorization for every view that declares one or more modules.

    Module ownership is declared once via ``@module_required("...")``.  This
    guard is the single server-side choke point, so route handlers cannot be
    accidentally exposed simply because a page route forgot a decorator during
    a future refactor.
    """

    if request.path.startswith('/static'):
        return

    endpoint = request.endpoint
    if not endpoint:
        return

    view = current_app.view_functions.get(endpoint)
    required_modules, read_methods = get_route_requirements(view)
    is_api_request = (
        request.path.startswith('/api/')
        or request.path.startswith('/admin/api/')
        or bool(request.is_json)
    )
    if not required_modules:
        # No module declared: public pages, auth pages, and the agent chat API
        # are authorized by their own decorators / agent-access checks.
        return

    user_id = session.get('user_id')

    # API clients authenticate with an API key or a signed Bearer token rather
    # than a browser session.  Their identity is still the owning user, so a
    # module-gated route must satisfy the same user -> role -> module check.
    if request.path.startswith('/api/v1/'):
        resolved = _resolve_credential_user()
        if resolved is not None:
            auth_type, cred_user_id = resolved
            if endpoint not in _AGENT_EXEC_ENDPOINTS:
                user_id = cred_user_id
                # Publish the verified identity so the audit trail can attribute
                # this request even when the view never runs (denied, error).
                g.user_id = cred_user_id
                g.auth_type = auth_type

    if not user_id:
        g.audit_reason = "authentication required"
        if is_api_request:
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('auth.login', next=current_relative_url()))

    from src.auth.modules import ACCESS_READ, ACCESS_WRITE

    method = (request.method or "GET").upper()
    required_level = ACCESS_READ if method in read_methods else ACCESS_WRITE
    if not user_manager.has_any_module_access(user_id, required_modules, min_level=required_level):
        g.audit_reason = (
            f"missing {required_level} access to module(s): {', '.join(required_modules)}"
        )
        logger.warning(
            "Access denied for user %s to endpoint %s (modules: %s, level: %s)",
            user_id,
            endpoint,
            ", ".join(required_modules),
            required_level,
        )
        if is_api_request:
            return jsonify({'error': 'Permission denied'}), 403
        flash('You do not have permission to access this page.', 'danger')
        return redirect(url_for('index'))
