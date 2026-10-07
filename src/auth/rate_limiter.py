"""Rate limiting and failed login tracking for the Text2SQL application."""

import functools
import threading
import time
from datetime import datetime
from flask import jsonify, request, session

# Thread-safe locks for rate limiting dictionaries
rate_limit_lock = threading.Lock()
failed_login_lock = threading.Lock()
ip_login_lock = threading.Lock()

# In-memory stores for rate limiting
rate_limit_store: dict = {}
failed_login_attempts: dict = {}
ip_login_attempts: dict = {}


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
                return f(*args, **kwargs)

            # Get client identifier (IP address or user_id if logged in)
            client_id = session.get('user_id', request.remote_addr)

            # Create a unique key for this client and endpoint
            key = f"{client_id}:{scope}"

            # Use thread-safe lock for rate limit store access
            with rate_limit_lock:
                now = time.time()
                # Initialize or get rate limiting data
                if key not in rate_limit_store:
                    rate_limit_store[key] = {'count': 0, 'reset_time': now + per}

                # Reset count if time period has passed
                if now > rate_limit_store[key]['reset_time']:
                    rate_limit_store[key] = {'count': 0, 'reset_time': now + per}

                # Check limit
                if rate_limit_store[key]['count'] >= limit:
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
                body, status, existing_headers = response
                existing_headers.update(headers)
                return body, status, existing_headers
            elif isinstance(response, tuple) and len(response) == 2:
                body, status = response
                return body, status, headers
            else:
                return response

        return decorated_function
    return decorator


def is_rate_limited_for_login(ip_address):
    """
    Check if an IP address is rate-limited for login attempts.
    Returns (is_limited, retry_after_seconds).
    """
    from config.config import RATE_LIMIT_ENABLED, LOGIN_ATTEMPT_LIMIT, LOGIN_ATTEMPT_WINDOW

    if not RATE_LIMIT_ENABLED:
        return False, 0

    now = datetime.utcnow().timestamp()

    with ip_login_lock:
        if ip_address not in ip_login_attempts:
            ip_login_attempts[ip_address] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,
                'blocked_until': 0
            }

        if ip_login_attempts[ip_address]['blocked_until'] > now:
            return True, int(ip_login_attempts[ip_address]['blocked_until'] - now)

        if now > ip_login_attempts[ip_address]['reset_time']:
            ip_login_attempts[ip_address] = {
                'attempts': 0,
                'reset_time': now + LOGIN_ATTEMPT_WINDOW,
                'blocked_until': 0
            }

        if ip_login_attempts[ip_address]['attempts'] >= LOGIN_ATTEMPT_LIMIT:
            block_minutes = min(60, 5 * (ip_login_attempts[ip_address]['attempts'] - (LOGIN_ATTEMPT_LIMIT - 1)))
            ip_login_attempts[ip_address]['blocked_until'] = now + (block_minutes * 60)
            return True, block_minutes * 60

        return False, 0


def record_failed_login(username, ip_address):
    """
    Record a failed login attempt for both username and IP.
    """
    from config.config import RATE_LIMIT_ENABLED, LOGIN_ATTEMPT_WINDOW

    if not RATE_LIMIT_ENABLED:
        return

    now = datetime.utcnow().timestamp()

    with failed_login_lock:
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
    Clear login attempts on successful login.
    """
    from config.config import RATE_LIMIT_ENABLED

    if not RATE_LIMIT_ENABLED:
        return

    with failed_login_lock:
        if username in failed_login_attempts:
            del failed_login_attempts[username]

    with ip_login_lock:
        if ip_address in ip_login_attempts:
            ip_login_attempts[ip_address]['attempts'] = 0


def check_for_account_lockout(username):
    """
    Check if an account should be locked due to too many failed attempts.
    """
    from config.config import RATE_LIMIT_ENABLED, FAILED_LOGIN_LOCKOUT_THRESHOLD

    if not RATE_LIMIT_ENABLED:
        return False

    with failed_login_lock:
        if username in failed_login_attempts:
            return failed_login_attempts[username]['attempts'] >= FAILED_LOGIN_LOCKOUT_THRESHOLD
        return False
