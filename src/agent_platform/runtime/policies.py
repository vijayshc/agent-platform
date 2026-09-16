"""LangGraph node policies: retry, timeout, and cache.

Retry and cache apply to the LLM (``agent``) node only. Tool nodes must not
retry or cache: ``interrupt()`` HITL and filesystem writes have to run once.
"""

from __future__ import annotations

from typing import Any

from langgraph.cache.memory import InMemoryCache
from langgraph.types import CachePolicy, RetryPolicy, TimeoutPolicy

_DEFAULT_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    max_interval=20.0,
    jitter=True,
)
# ``run_timeout`` alone cannot catch a stalled model: LangGraph's default
# ``refresh_on="auto"`` restarts the timer every time the node emits anything, so
# a call that dribbles keepalive-ish chunks while making no progress runs
# forever. ``idle_timeout`` bounds the silence between events, which is what
# actually stops a hung provider call.
_DEFAULT_AGENT_TIMEOUT = TimeoutPolicy(run_timeout=120.0, idle_timeout=60.0)
# The workspace MCP caps a single command at 180s, so the tools node must stay
# idle-tolerant well past that -- otherwise a legitimately slow command is
# killed as if it were a hang.
_TOOLS_IDLE_FLOOR = 240.0
_DEFAULT_TOOLS_TIMEOUT = TimeoutPolicy(run_timeout=300.0, idle_timeout=_TOOLS_IDLE_FLOOR)


def apply_node_policies(graph: Any, config: dict[str, Any]) -> None:
    """Attach RetryPolicy, TimeoutPolicy, and CachePolicy to compiled nodes."""
    nodes = getattr(graph, "nodes", None) or {}
    retry = _retry_policy(config)
    agent_timeout, tools_timeout = _timeout_policies(config)
    cache_policy = _cache_policy(config)

    if cache_policy is not None:
        graph.cache = InMemoryCache()

    if "agent" in nodes or "model" in nodes:
        for model_node in ("agent", "model"):
            if model_node not in nodes:
                continue
            if retry is not None:
                # PregelNode.retry_policy is a sequence of RetryPolicy.
                nodes[model_node].retry_policy = (retry,)
            if agent_timeout is not None:
                nodes[model_node].timeout = agent_timeout
            if cache_policy is not None:
                nodes[model_node].cache_policy = cache_policy
    if "tools" in nodes and tools_timeout is not None:
        nodes["tools"].timeout = tools_timeout


def _retry_policy(config: dict[str, Any]) -> RetryPolicy | None:
    if config.get("disable_retry"):
        return None
    retry_cfg = dict(config.get("retry") or {})
    kwargs: dict[str, Any] = {
        "max_attempts": int(retry_cfg.get("max_attempts") or _DEFAULT_RETRY.max_attempts),
        "initial_interval": float(retry_cfg.get("initial_interval") or _DEFAULT_RETRY.initial_interval),
        "backoff_factor": float(retry_cfg.get("backoff_factor") or _DEFAULT_RETRY.backoff_factor),
        "max_interval": float(retry_cfg.get("max_interval") or _DEFAULT_RETRY.max_interval),
        "jitter": bool(retry_cfg.get("jitter", True)),
    }
    if retry_cfg.get("retry_on") is not None:
        kwargs["retry_on"] = retry_cfg["retry_on"]
    return RetryPolicy(**kwargs)


def _timeout_policies(config: dict[str, Any]) -> tuple[TimeoutPolicy | None, TimeoutPolicy | None]:
    if config.get("disable_timeout"):
        return None, None
    raw = config.get("timeout")
    if isinstance(raw, (int, float)):
        agent = TimeoutPolicy(run_timeout=float(raw), idle_timeout=_idle_default(float(raw)))
        tools_s = max(float(raw), _DEFAULT_TOOLS_TIMEOUT.run_timeout or 300.0)
        return agent, TimeoutPolicy(run_timeout=tools_s, idle_timeout=min(tools_s, _TOOLS_IDLE_FLOOR))
    cfg = dict(raw or {})
    agent_s = float(cfg.get("run_timeout") or cfg.get("agent") or _DEFAULT_AGENT_TIMEOUT.run_timeout or 120)
    tools_s = float(cfg.get("tools") or max(agent_s, float(_DEFAULT_TOOLS_TIMEOUT.run_timeout or 300)))
    idle_cfg = cfg.get("idle_timeout") or cfg.get("idle")
    agent_idle = float(idle_cfg) if idle_cfg else _idle_default(agent_s)
    # A tools idle below the command cap would abort legitimate slow work, so
    # the floor always wins for the tools node.
    tools_idle = max(_TOOLS_IDLE_FLOOR, float(idle_cfg)) if idle_cfg else _TOOLS_IDLE_FLOOR
    return (
        TimeoutPolicy(run_timeout=agent_s, idle_timeout=agent_idle),
        TimeoutPolicy(run_timeout=tools_s, idle_timeout=tools_idle),
    )


def _idle_default(run_timeout: float) -> float:
    """Silence budget: never longer than the run budget, never shorter than 30s."""
    return max(30.0, min(run_timeout, 90.0))


def _cache_policy(config: dict[str, Any]) -> CachePolicy | None:
    if config.get("disable_cache"):
        return None
    raw = config.get("cache")
    if not raw:
        return None
    ttl = 120
    if isinstance(raw, dict) and raw.get("ttl") is not None:
        ttl = int(raw["ttl"])
    elif isinstance(raw, int):
        ttl = int(raw)
    return CachePolicy(ttl=ttl)
