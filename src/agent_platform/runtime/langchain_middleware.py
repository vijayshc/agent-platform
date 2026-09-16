"""Build LangChain's out-of-the-box middleware from a definition's config.

Every branch constructs a shipped ``langchain.agents.middleware`` class; nothing
is re-implemented here. The author's values come from ``config["middleware"]``
keyed by the ids in ``catalog.capabilities.middleware``. An unknown id is a hard
error: silently dropping a guardrail would be worse than refusing to compile.
"""

from __future__ import annotations

from typing import Any, Callable

from src.agent_platform.catalog.capabilities.middleware import MIDDLEWARE_BY_ID

#: Middleware ids that need a model connection of their own.
_MODEL_KEYS = {
    "summarization": None,
    "model_fallback": "models",
    "tool_selection": "model",
    "tool_emulator": "model",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    if isinstance(value, dict):
        return [str(k) for k, v in value.items() if v]
    return [str(v) for v in value if str(v).strip()]


def _int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_model(spec: dict[str, Any], ctx: Any) -> Any:
    from src.agent_platform.runtime.model_select import compile_model_client

    return compile_model_client(dict(spec or {}), ctx)


def _summarization(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import SummarizationMiddleware

    trigger_tokens = _int(values.get("trigger_tokens"), 8000) or 8000
    keep_messages = _int(values.get("keep_messages"), 20) or 20
    return SummarizationMiddleware(
        model=model,
        trigger=("tokens", trigger_tokens),
        keep=("messages", keep_messages),
    )


def _context_editing(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ClearToolUsesEdit, ContextEditingMiddleware

    edit = ClearToolUsesEdit(
        trigger=_int(values.get("trigger_tokens"), 100000) or 100000,
        keep=_int(values.get("keep_tool_uses"), 3) or 3,
        clear_tool_inputs=bool(values.get("clear_tool_inputs")),
        exclude_tools=tuple(_as_list(values.get("exclude_tools"))),
    )
    return ContextEditingMiddleware(edits=[edit])


def _todo_list(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import TodoListMiddleware

    return TodoListMiddleware()


def _human_in_the_loop(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    tools = _as_list(values.get("tools"))
    if not tools:
        return None
    reviewer_prompt = str(
        values.get("description_prefix") or "Tool execution requires approval"
    )
    # The review card renders each action's tool name and args from the
    # structured `action_requests` fields. Give every tool the reviewer prompt as
    # its description so LangChain does not fall back to its default
    # "Tool: …/Args: …" blurb, which duplicated both in the card.
    interrupt_on: dict[str, Any] = {
        name: {
            "allowed_decisions": ["approve", "edit", "reject", "respond"],
            "description": reviewer_prompt,
        }
        for name in tools
    }
    return HumanInTheLoopMiddleware(interrupt_on=interrupt_on)


def _model_call_limit(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ModelCallLimitMiddleware

    return ModelCallLimitMiddleware(
        run_limit=_int(values.get("run_limit")),
        thread_limit=_int(values.get("thread_limit")),
        exit_behavior=str(values.get("exit_behavior") or "end"),
    )


def _tool_call_limit(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ToolCallLimitMiddleware

    return ToolCallLimitMiddleware(
        tool_name=str(values.get("tool_name") or "") or None,
        run_limit=_int(values.get("run_limit")),
        thread_limit=_int(values.get("thread_limit")),
        exit_behavior=str(values.get("exit_behavior") or "continue"),
    )


def _model_fallback(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ModelFallbackMiddleware

    clients = [_resolve_model({"client": name}, ctx) for name in _as_list(values.get("models"))]
    if not clients:
        return None
    return ModelFallbackMiddleware(*clients)


def _model_retry(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ModelRetryMiddleware

    return ModelRetryMiddleware(
        max_retries=_int(values.get("max_retries"), 2) or 0,
        on_failure=str(values.get("on_failure") or "continue"),
        initial_delay=_float(values.get("initial_delay"), 1.0),
        backoff_factor=_float(values.get("backoff_factor"), 2.0),
    )


def _tool_retry(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ToolRetryMiddleware

    tools = _as_list(values.get("tools")) or None
    return ToolRetryMiddleware(
        max_retries=_int(values.get("max_retries"), 2) or 0,
        tools=tools,
        on_failure=str(values.get("on_failure") or "continue"),
        initial_delay=_float(values.get("initial_delay"), 1.0),
        backoff_factor=_float(values.get("backoff_factor"), 2.0),
    )


def _tool_error(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ToolErrorMiddleware

    # The middleware requires a handler; returning the exception as content is
    # its documented purpose ("return the error to the model as a ToolMessage").
    # A blank handler is therefore not configurable: the author chooses *which*
    # tools are covered, never whether the error is reported.
    def on_error(error: Exception, request: Any) -> str:
        name = ""
        tool_call = getattr(request, "tool_call", None)
        if isinstance(tool_call, dict):
            name = str(tool_call.get("name") or "")
        return f"{name or 'tool'} failed: {error}"

    return ToolErrorMiddleware(on_error=on_error, tools=_as_list(values.get("tools")) or None)


def _tool_selection(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import LLMToolSelectorMiddleware

    selector = values.get("model")
    return LLMToolSelectorMiddleware(
        model=_resolve_model({"client": selector}, ctx) if selector else None,
        max_tools=_int(values.get("max_tools")),
        always_include=_as_list(values.get("always_include")) or None,
    )


def _provider_tool_search(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ProviderToolSearchMiddleware

    return ProviderToolSearchMiddleware(searchable_tools=_as_list(values.get("searchable_tools")) or None)


def _pii(values: dict[str, Any], ctx: Any, model: Any) -> list[Any]:
    from langchain.agents.middleware import PIIMiddleware

    rules = values.get("rules")
    entries: list[dict[str, Any]] = []
    if isinstance(rules, list):
        entries = [r for r in rules if isinstance(r, dict)]
    elif isinstance(rules, dict):
        entries = [{"type": k, "strategy": v} for k, v in rules.items()]
    elif isinstance(rules, str):
        for line in rules.replace(",", "\n").split("\n"):
            if "=" in line:
                key, _, value = line.partition("=")
                entries.append({"type": key.strip(), "strategy": value.strip() or "redact"})
    built = [
        PIIMiddleware(
            str(entry.get("type") or "email"),
            strategy=str(entry.get("strategy") or "redact"),
            apply_to_input=bool(entry.get("apply_to_input", True)),
            apply_to_output=bool(entry.get("apply_to_output", False)),
            apply_to_tool_results=bool(entry.get("apply_to_tool_results", False)),
        )
        for entry in entries
    ]
    return built


def _shell_tool(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import ShellToolMiddleware

    startup = tuple(_as_list(values.get("startup_commands")))
    return ShellToolMiddleware(
        workspace_root=getattr(ctx, "workspace_dir", None),
        startup_commands=startup or None,
        tool_name=str(values.get("tool_name") or "shell"),
    )


def _file_search(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import FilesystemFileSearchMiddleware

    root = getattr(ctx, "workspace_dir", None)
    if not root:
        raise RuntimeError(
            "Filesystem search needs a run workspace; start the agent from a run so a "
            "workspace directory exists."
        )
    return FilesystemFileSearchMiddleware(root_path=str(root))


def _tool_emulator(values: dict[str, Any], ctx: Any, model: Any) -> Any:
    from langchain.agents.middleware import LLMToolEmulator

    emulator_model = values.get("model")
    return LLMToolEmulator(
        tools=_as_list(values.get("tools")) or None,
        model=_resolve_model({"client": emulator_model}, ctx) if emulator_model else None,
    )


_BUILDERS: dict[str, Callable[[dict[str, Any], Any, Any], Any]] = {
    "summarization": _summarization,
    "context_editing": _context_editing,
    "todo_list": _todo_list,
    "human_in_the_loop": _human_in_the_loop,
    "model_call_limit": _model_call_limit,
    "tool_call_limit": _tool_call_limit,
    "model_fallback": _model_fallback,
    "model_retry": _model_retry,
    "tool_retry": _tool_retry,
    "tool_error": _tool_error,
    "tool_selection": _tool_selection,
    "provider_tool_search": _provider_tool_search,
    "pii": _pii,
    "shell_tool": _shell_tool,
    "file_search": _file_search,
    "tool_emulator": _tool_emulator,
}


def _legacy_compaction(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pre-registry context management: ``maxContextWindowTokens`` → summarization.

    Definitions authored before the middleware registry expressed context
    management as a token budget that a hand-written middleware trimmed to. That
    intent is ``SummarizationMiddleware``; the mapping only applies when the
    author configured no middleware at all, and never overrides an explicit
    ``disable_compaction``.
    """
    if config.get("disable_compaction") or (config.get("harness") or {}).get("disable_compaction"):
        return {}
    harness = config.get("harness") or {}
    configured = (
        config.get("maxContextWindowTokens")
        or config.get("max_context_window_tokens")
        or harness.get("max_context_window_tokens")
    )
    if not configured:
        return {}
    from src.agent_platform.runtime.harness_config import context_window_tokens

    return {"summarization": {"trigger_tokens": context_window_tokens(config), "keep_messages": 20}}


def normalize_middleware_config(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The middleware map a config effectively asks for.

    ``hitl.approval`` (the per-tool approval list authored before the middleware
    registry existed) *is* ``HumanInTheLoopMiddleware``: it is folded in here so
    there is exactly one approval mechanism in the runtime.
    """
    entries: dict[str, dict[str, Any]] = {
        str(key): dict(value or {}) for key, value in (config.get("middleware") or {}).items()
    }
    approval = _as_list((config.get("hitl") or {}).get("approval"))
    if approval:
        current = entries.setdefault("human_in_the_loop", {})
        merged = list(dict.fromkeys([*_as_list(current.get("tools")), *approval]))
        current["tools"] = merged
    if not entries:
        entries.update(_legacy_compaction(config))
    return entries


def build_middleware(config: dict[str, Any], ctx: Any, *, model: Any) -> list[Any]:
    """Instantiate every configured OOTB middleware, in the author's order."""
    built: list[Any] = []
    for middleware_id, values in normalize_middleware_config(config).items():
        if middleware_id not in MIDDLEWARE_BY_ID:
            raise RuntimeError(
                f"Unknown middleware '{middleware_id}'. Known middleware: "
                + ", ".join(sorted(MIDDLEWARE_BY_ID))
            )
        builder = _BUILDERS.get(middleware_id)
        if builder is None:
            raise RuntimeError(f"Middleware '{middleware_id}' has no builder")
        instance = builder(values, ctx, model)
        if instance is None:
            continue
        built.extend(instance if isinstance(instance, list) else [instance])
    return built


def middleware_names(config: dict[str, Any]) -> list[str]:
    """Class names of the middleware a config asks for (compile-plan preview)."""
    names: list[str] = []
    for middleware_id in normalize_middleware_config(config):
        spec = MIDDLEWARE_BY_ID.get(middleware_id)
        if spec:
            names.append(str(spec["class"]).rsplit(".", 1)[-1])
    return names
