"""Readable MCP failures.

``mcp``'s stdio/HTTP clients run inside anyio task groups, so a connect or
handshake failure surfaces as ``ExceptionGroup('unhandled errors in a
TaskGroup', [...])``. The leaf cause (connection refused, missing executable,
HTTP 401) is the only part worth showing an operator, and it is the part that
would otherwise be swallowed.
"""

from __future__ import annotations


def describe_error(exc: BaseException) -> str:
    """Flatten an exception (tree) into the distinct leaf messages."""
    if isinstance(exc, BaseExceptionGroup):
        leaves: list[str] = []
        for sub in exc.exceptions:
            text = describe_error(sub)
            if text and text not in leaves:
                leaves.append(text)
        if leaves:
            return "; ".join(leaves)
    text = str(exc).strip()
    return text or type(exc).__name__
