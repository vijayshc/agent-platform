"""Unified, file-based audit trail for user access and privileged actions.

The trail is written from exactly one place — the authorization layer in
``src.auth`` — and not from individual routes or features.  Every module and
endpoint that participates in authorization is therefore audited automatically,
including features added later.

On-disk format (one event per line, files rotated by date)::

    <timestamp> : <ip> : <username> : <STATUS> : <endpoint> : <comments>

Today's events are appended to ``<AUDIT_LOG_DIR>/audit.log``.  At midnight the
file is archived as ``audit.log.YYYY-MM-DD`` and a fresh ``audit.log`` starts,
so a day of activity is always exactly one file.  Archives older than the
retention window are pruned automatically.

The comment field is the human-readable "what happened": the action
(``file upload``, ``file download``, ``write action``, ``login failed`` …), the
owning module and access level, the target, uploaded file names, and the reason
for a rejection.
"""

from __future__ import annotations

import datetime
import logging
import logging.handlers
import os
import threading
from typing import Any, Iterable, Sequence

from config.config import AUDIT_LOG_DIR, AUDIT_LOG_ENABLED, AUDIT_LOG_RETENTION_DAYS

#: Values allowed in the STATUS field.
STATUS_SUCCESS = "SUCCESS"
STATUS_REJECT = "REJECT"

#: State-changing methods are always audited, in every module, forever.
AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Path markers that make even a safe (GET) request worth auditing.
AUDITED_PATH_MARKERS = ("download", "upload", "export", "backup", "api-key")

#: View functions worth auditing whatever their method or module metadata,
#: mapped to the action label used in the comment.  Keyed by function name
#: because app-level endpoints change prefix with how the app is launched
#: (``app.reload_app`` vs ``__main__.reload_app``).
AUDITED_ACTION_ENDPOINTS = {
    "reload_app": "application reload",
}

#: Path prefixes whose remainder must never be persisted (single-use or
#: account-recovery credentials live in the URL).
SENSITIVE_PATH_PREFIXES = ("/reset-password/", "/auth/reset-password/")

#: JSON body keys safe to persist as the target of a write (never credentials).
SAFE_BODY_KEYS = (
    "username",
    "path",
    "name",
    "collection",
    "collection_name",
    "table",
    "skill",
    "agent",
    "slug",
    "modules",
    "roles",
)

#: Body keys holding SQL; only the leading verb is persisted, never the text.
SQL_BODY_KEYS = ("sql", "query")

#: Endpoint names that cross an authentication boundary, mapped to the method
#: that actually performs the action.  Rendering the login/password shells with
#: GET is page traffic, not a security event, so it is not audited.
AUTH_EVENT_METHODS = {
    "auth.login": "POST",
    "auth.logout": "GET",
    "auth.change_password": "POST",
    "auth.reset_password": "POST",
    "auth.reset_password_request": "POST",
    "security.reauthenticate": "POST",
}

#: Query-string keys that identify a target and are safe to persist (never secrets).
SAFE_QUERY_KEYS = ("path", "workspace", "collection", "collection_name", "table", "name")

#: Separator between the six fields of a line.
SEPARATOR = " : "

AUDIT_BASENAME = "audit.log"
_LOGGER_NAME = "text2sql.audit.trail"

logger = logging.getLogger("text2sql.audit")


def _clean(value: Any, limit: int) -> str:
    """Flatten a value to a single printable line that keeps the grammar intact."""
    if value is None:
        return "-"
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split()).replace(SEPARATOR, " - ")
    text = text[:limit]
    return text or "-"


def format_line(
    *,
    timestamp: datetime.datetime | None = None,
    ip: str | None,
    username: str | None,
    status: str,
    endpoint: str,
    comment: str,
) -> str:
    """Render one audit event in the canonical `` : ``-separated format."""
    moment = timestamp or datetime.datetime.now()
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in (STATUS_SUCCESS, STATUS_REJECT):
        normalized_status = STATUS_REJECT
    return SEPARATOR.join(
        (
            moment.strftime("%Y-%m-%d %H:%M:%S"),
            _clean(ip, 64),
            _clean(username, 120),
            normalized_status,
            _clean(endpoint, 300),
            _clean(comment, 800),
        )
    )


def build_comment(
    action: str,
    *,
    module: str | None = None,
    level: str | None = None,
    target: str | None = None,
    files: Sequence[str] | None = None,
    reason: str | None = None,
    http_status: int | None = None,
    forwarded_for: str | None = None,
    auth: str | None = None,
) -> str:
    """Compose the comment field from the facts an auditor needs."""
    parts: list[str] = [action] if action else []
    if module:
        parts.append(f"module={module}")
    if level:
        parts.append(f"level={level}")
    if target:
        parts.append(f"target={target}")
    if files:
        shown = ", ".join(str(name) for name in list(files)[:5])
        more = f", +{len(files) - 5} more" if len(files) > 5 else ""
        parts.append(f"files={len(files)}[{shown}{more}]")
    if auth:
        parts.append(f"auth={auth}")
    if reason:
        parts.append(f"reason={reason}")
    if http_status is not None:
        parts.append(f"http={http_status}")
    if forwarded_for:
        parts.append(f"xff={forwarded_for}")
    return "; ".join(parts) or "-"


class _DailyRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Rotating handler that keeps the audit file readable only by its owner."""

    def _open(self):
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:  # pragma: no cover - platform without chmod
            pass
        return stream


class _Trail:
    """Lazily-created, thread-safe writer for the daily-rotated audit file."""

    def __init__(self, directory: str, retention_days: int, enabled: bool) -> None:
        self._directory = directory
        self._retention_days = max(1, int(retention_days))
        self._enabled = bool(enabled)
        self._lock = threading.Lock()
        self._ready = False

    @property
    def path(self) -> str:
        return os.path.join(self._directory, AUDIT_BASENAME)

    def emit(self, line: str) -> None:
        if not self._enabled or not line:
            return
        self._ensure_ready()
        logging.getLogger(_LOGGER_NAME).info(line)

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            os.makedirs(self._directory, mode=0o700, exist_ok=True)
            try:
                os.chmod(self._directory, 0o700)
            except OSError:  # pragma: no cover - platform without chmod
                pass
            trail_logger = logging.getLogger(_LOGGER_NAME)
            trail_logger.setLevel(logging.INFO)
            trail_logger.propagate = False
            if not trail_logger.handlers:
                handler = _DailyRotatingFileHandler(
                    self.path,
                    when="midnight",
                    interval=1,
                    backupCount=self._retention_days,
                    encoding="utf-8",
                    delay=True,
                    utc=False,
                )
                handler.suffix = "%Y-%m-%d"
                handler.setFormatter(logging.Formatter("%(message)s"))
                trail_logger.addHandler(handler)
            self._ready = True


_trail = _Trail(AUDIT_LOG_DIR, AUDIT_LOG_RETENTION_DAYS, AUDIT_LOG_ENABLED)


def record(
    *,
    status: str,
    endpoint: str,
    comment: str,
    ip: str | None = None,
    username: str | None = None,
    timestamp: datetime.datetime | None = None,
) -> str | None:
    """Append one event to the audit trail.

    Never raises: auditing must not be able to break a user request.  Returns
    the line handed to the writer, or ``None`` when auditing is disabled or the
    writer could not be initialised.  The logging subsystem itself swallows
    handler I/O errors, so a returned line is not a durability guarantee.
    """
    line = format_line(
        timestamp=timestamp,
        ip=ip,
        username=username,
        status=status,
        endpoint=endpoint,
        comment=comment,
    )
    try:
        _trail.emit(line)
    except Exception:  # pragma: no cover - disk/permission failures
        logger.exception("Failed to append audit event: %s", line)
        return None
    return line


def enabled() -> bool:
    return AUDIT_LOG_ENABLED


def redact_path(path: str) -> str:
    """Hide credential-bearing URL segments (e.g. password-reset tokens)."""
    for prefix in SENSITIVE_PATH_PREFIXES:
        if path.startswith(prefix):
            return f"{prefix}<redacted>"
    return path


def safe_body_target(payload: Any) -> str | None:
    """First whitelisted, secret-free target carried in a JSON body.

    Only keys listed in :data:`SAFE_BODY_KEYS` are read, so passwords, tokens
    and free-form content can never reach the trail; SQL is reduced to its verb.
    """
    if not isinstance(payload, dict):
        return None
    for key in SAFE_BODY_KEYS:
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple, set)):
            items = [
                str(item)
                for item in value
                if isinstance(item, (str, int, float, bool))
            ]
            if not items:
                continue
            shown = ", ".join(items[:6])
            more = f", +{len(items) - 6} more" if len(items) > 6 else ""
            return f"{key}={shown}{more}"
        if isinstance(value, (str, int, float, bool)):
            return f"{key}={value}"
    for key in SQL_BODY_KEYS:
        statement = payload.get(key)
        if isinstance(statement, str) and statement.strip():
            return f"sql={statement.strip().split()[0].upper()}"
    return None


def safe_query_target(query: Any, keys: Iterable[str] = SAFE_QUERY_KEYS) -> str | None:
    """Return the first target-ish query parameter as ``key=value``, else ``None``."""
    try:
        for key in keys:
            value = query.get(key)
            if value:
                return f"{key}={value}"
    except Exception:  # pragma: no cover - defensive
        return None
    return None
