"""Log-record context: name the account behind each line.

Every configured formatter renders a ``user`` field.  It is filled here, once per
record, from the Flask session while a request is being handled, and from a
worker-thread binding afterwards.  A line written outside any request (startup,
background work, schedulers) shows :data:`ANONYMOUS`.

Why the thread binding exists: Werkzeug writes its access line from
``send_response``, which current Werkzeug calls while the response body is being
iterated - after Flask has already popped the request context.  The account
captured while the request was handled is kept on the worker thread so that
access line can still name it.
"""

from __future__ import annotations

import logging
import threading

#: Placeholder for records that are not tied to a signed-in request.
ANONYMOUS = "-"

_binding = threading.local()


def bind_request_user(username: str | None) -> None:
    """Remember the account for log lines written after the request context ends."""
    _binding.username = str(username) if username else ANONYMOUS


def current_username() -> str:
    """Return the account behind the record being emitted, else ``-``."""
    try:
        from flask import has_request_context, session

        if has_request_context():
            name = session.get("username")
            if name:
                return str(name)
    except Exception:
        # Identity lookup must never be able to break request logging.
        pass
    return getattr(_binding, "username", ANONYMOUS) or ANONYMOUS


class UserContextFilter(logging.Filter):
    """Attach ``record.user`` so formatters can render ``%(user)s``."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.user = current_username()
        return True


def install_request_user_logging(app) -> None:
    """Bind the signed-in account around each request so access lines name it."""
    from flask import session

    @app.before_request
    def _bind_user_before_request():
        # Also resets the binding on a reused worker thread.
        try:
            bind_request_user(session.get("username"))
        except Exception:
            bind_request_user(None)
        return None

    @app.after_request
    def _bind_user_after_request(response):
        # Login sets the session inside the view; keeping the stronger value
        # attributes a successful login, while a logout keeps its actor.
        try:
            name = session.get("username")
        except Exception:
            name = None
        if name:
            bind_request_user(name)
        return response
