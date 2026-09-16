"""Concurrency gate for agent run execution.

Each agent run is executed on its own OS thread + asyncio event loop. Without a
cap, N concurrent users would spawn N threads/loops without bound, each also
loading its own LangGraph, LLM client, MCP subprocess and filesystem workspace.
That lets a burst of requests starve the process and makes one run impact every
other.

This module exposes a single process-wide bounded semaphore. ``RuntimeHost``
acquires one slot per run *before* doing any heavy work and releases it when the
run finishes (success, error, cancel, HITL pause or client disconnect). The cap
and wait timeout are configurable so operators can tune back-pressure without a
code change.

    AGENT_MAX_CONCURRENT_RUNS (default 16)   how many runs may execute at once
    AGENT_RUN_ACQUIRE_TIMEOUT (default 60s)  how long a run waits for a slot
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("text2sql.agent_platform.concurrency")

_DEFAULT_MAX_RUNS = int(os.environ.get("AGENT_MAX_CONCURRENT_RUNS", "16") or "16")
_DEFAULT_ACQUIRE_TIMEOUT = float(os.environ.get("AGENT_RUN_ACQUIRE_TIMEOUT", "60") or "60")

# Process-wide bounded semaphore. ``BoundedSemaphore`` raises ValueError if it is
# released more times than acquired, which guards against double-release bugs.
_run_slots = threading.BoundedSemaphore(max(1, _DEFAULT_MAX_RUNS))
_max_runs = max(1, _DEFAULT_MAX_RUNS)
_lock = threading.Lock()


def max_concurrent_runs() -> int:
    return _max_runs


def acquire_run_slot() -> threading.BoundedSemaphore:
    """Block until a run slot is free, or time out raising ``RuntimeError``.

    Returns the semaphore the slot was acquired on so the caller can release the
    exact same instance (symmetric even if the global cap is reconfigured).

    Raising (rather than silently failing) lets the run surface an explicit
    "server busy" error to the caller instead of stacking up hidden threads.
    """
    sem = _run_slots
    acquired = sem.acquire(timeout=_DEFAULT_ACQUIRE_TIMEOUT)
    if not acquired:
        raise RuntimeError(
            f"Agent execution capacity reached ({_max_runs} concurrent runs). "
            "Try again shortly."
        )
    return sem


def release_run_slot(sem: threading.BoundedSemaphore | None = None) -> None:
    target = sem if sem is not None else _run_slots
    try:
        target.release()
    except ValueError:  # pragma: no cover - defensive: never over-release
        logger.warning("release_run_slot called without a matching acquire")


def _set_limit(value: int) -> None:
    """Reconfigure the cap at runtime (used by tests / observability tooling)."""
    global _max_runs, _run_slots
    value = max(1, int(value))
    with _lock:
        # Replace the semaphore entirely so the new bound is exact. In-flight
        # acquisitions still hold a reference to the old semaphore; the new one
        # caps only future runs. This is safe because ``StreamResponse`` always
        # releases on the same semaphore it acquired.
        _run_slots = threading.BoundedSemaphore(value)
        _max_runs = value


def set_max_concurrent_runs(value: int) -> None:
    _set_limit(value)
