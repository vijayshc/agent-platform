"""Out-of-the-box LangChain middleware exposed by the Studio.

Every entry is a thin declarative description of a shipped
``langchain.agents.middleware`` class: how to label it, which fields the
inspector must render, and which constructor arguments those fields map to.
``src/agent_platform/runtime/langchain_middleware.py`` turns an entry plus the
author's values into the real middleware instance. Nothing here re-implements a
middleware; the registry only describes what the library already provides.
"""

from __future__ import annotations

from typing import Any

MIDDLEWARE_CLASSES = "langchain.agents.middleware"


def _field(
    name: str,
    label: str,
    kind: str = "text",
    *,
    help: str = "",
    default: Any = None,
    options: list[Any] | None = None,
    group: str = "",
) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "label": label, "type": kind}
    if help:
        field["help"] = help
    if default is not None:
        field["default"] = default
    if options:
        field["options"] = options
    if group:
        field["group"] = group
    return field


#: Registry entries, ordered by how often an author reaches for them.
MIDDLEWARE: list[dict[str, Any]] = [
    {
        "id": "summarization",
        "label": "Summarization",
        "class": f"{MIDDLEWARE_CLASSES}.SummarizationMiddleware",
        "summary": "Summarize older history when the conversation approaches a token limit.",
        "icon": "scroll",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("trigger_tokens", "Summarize at (tokens)", "number", default=8000,
                   help="Conversation size that triggers summarization."),
            _field("keep_messages", "Keep recent messages", "number", default=20,
                   help="How many recent messages survive verbatim."),
        ],
    },
    {
        "id": "context_editing",
        "label": "Context editing",
        "class": f"{MIDDLEWARE_CLASSES}.ContextEditingMiddleware",
        "summary": "Clear old tool results from history to keep the context small.",
        "icon": "eraser",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("trigger_tokens", "Clear at (tokens)", "number", default=100000),
            _field("keep_tool_uses", "Keep recent tool results", "number", default=3),
            _field("clear_tool_inputs", "Also clear tool arguments", "boolean", default=False),
            _field("exclude_tools", "Never clear these tools", "tags"),
        ],
    },
    {
        "id": "todo_list",
        "label": "Planning (todos)",
        "class": f"{MIDDLEWARE_CLASSES}.TodoListMiddleware",
        "summary": "Give the agent a write_todos tool so it plans multi-step work.",
        "icon": "list-checks",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [],
    },
    {
        "id": "human_in_the_loop",
        "label": "Human approval",
        "class": f"{MIDDLEWARE_CLASSES}.HumanInTheLoopMiddleware",
        "summary": "Pause before selected tools and wait for a human decision.",
        "icon": "shield-check",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("tools", "Tools requiring approval", "tags",
                   help="Tool names (or * patterns). Each pause offers approve / edit / reject."),
            _field("description_prefix", "Prompt shown to the reviewer", "text",
                   default="Tool execution requires approval"),
        ],
    },
    {
        "id": "model_call_limit",
        "label": "Model call limit",
        "class": f"{MIDDLEWARE_CLASSES}.ModelCallLimitMiddleware",
        "summary": "Stop a runaway agent after N model calls.",
        "icon": "gauge",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("run_limit", "Calls per run", "number", default=25),
            _field("thread_limit", "Calls per thread", "number"),
            _field("exit_behavior", "When the limit is hit", "select", default="end",
                   options=["end", "error"]),
        ],
    },
    {
        "id": "tool_call_limit",
        "label": "Tool call limit",
        "class": f"{MIDDLEWARE_CLASSES}.ToolCallLimitMiddleware",
        "summary": "Cap how often a single tool may run.",
        "icon": "gauge",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("tool_name", "Tool name", "text", help="Empty applies the cap to every tool."),
            _field("run_limit", "Calls per run", "number", default=20),
            _field("thread_limit", "Calls per thread", "number"),
            _field("exit_behavior", "When the limit is hit", "select", default="continue",
                   options=["continue", "end", "error"]),
        ],
    },
    {
        "id": "model_fallback",
        "label": "Model fallback",
        "class": f"{MIDDLEWARE_CLASSES}.ModelFallbackMiddleware",
        "summary": "Retry the turn on another model connection when the primary fails.",
        "icon": "shuffle",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("models", "Fallback connections", "tags",
                   help="Connection ids or names, tried in order."),
        ],
    },
    {
        "id": "model_retry",
        "label": "Model retry",
        "class": f"{MIDDLEWARE_CLASSES}.ModelRetryMiddleware",
        "summary": "Retry failed model calls with exponential backoff.",
        "icon": "rotate-cw",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("max_retries", "Max retries", "number", default=2),
            _field("on_failure", "If retries are exhausted", "select", default="continue",
                   options=["continue", "error"]),
            _field("initial_delay", "Initial delay (s)", "number", default=1),
            _field("backoff_factor", "Backoff factor", "number", default=2),
        ],
    },
    {
        "id": "tool_retry",
        "label": "Tool retry",
        "class": f"{MIDDLEWARE_CLASSES}.ToolRetryMiddleware",
        "summary": "Retry failed tool calls with exponential backoff.",
        "icon": "rotate-cw",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("max_retries", "Max retries", "number", default=2),
            _field("tools", "Tools", "tags", help="Empty applies to every tool."),
            _field("on_failure", "If retries are exhausted", "select", default="continue",
                   options=["continue", "error"]),
        ],
    },
    {
        "id": "tool_error",
        "label": "Tool error handling",
        "class": f"{MIDDLEWARE_CLASSES}.ToolErrorMiddleware",
        "summary": "Return tool exceptions to the model as readable tool messages.",
        "icon": "alert-triangle",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("tools", "Tools", "tags", help="Empty applies to every tool."),
        ],
    },
    {
        "id": "tool_selection",
        "label": "LLM tool selection",
        "class": f"{MIDDLEWARE_CLASSES}.LLMToolSelectorMiddleware",
        "summary": "Let a smaller model pick the relevant tools for each request.",
        "icon": "filter",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("max_tools", "Max tools per request", "number", default=8),
            _field("always_include", "Always include", "tags"),
            _field("model", "Selector connection", "text",
                   help="Connection id; empty reuses the agent's own model."),
        ],
    },
    {
        "id": "provider_tool_search",
        "label": "Provider tool search",
        "class": f"{MIDDLEWARE_CLASSES}.ProviderToolSearchMiddleware",
        "summary": "Defer large tool sets behind the provider's native tool search.",
        "icon": "search",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("searchable_tools", "Searchable tools", "tags",
                   help="Empty exposes every tool to provider-side search."),
        ],
    },
    {
        "id": "pii",
        "label": "PII protection",
        "class": f"{MIDDLEWARE_CLASSES}.PIIMiddleware",
        "summary": "Detect and redact, mask, block or hash sensitive values.",
        "icon": "lock",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("rules", "Rules", "keyvalue",
                   help="One rule per line: email=redact, credit_card=mask, ip=block, url=hash."),
        ],
    },
    {
        "id": "shell_tool",
        "label": "Shell tool",
        "class": f"{MIDDLEWARE_CLASSES}.ShellToolMiddleware",
        "summary": "Give the agent a persistent shell rooted in the run workspace.",
        "icon": "terminal",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("startup_commands", "Startup commands", "tags"),
            _field("tool_name", "Tool name", "text", default="shell"),
        ],
    },
    {
        "id": "file_search",
        "label": "Filesystem search",
        "class": f"{MIDDLEWARE_CLASSES}.FilesystemFileSearchMiddleware",
        "summary": "Add Glob and Grep tools over the run workspace.",
        "icon": "folder-search",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [],
    },
    {
        "id": "tool_emulator",
        "label": "Tool emulator",
        "class": f"{MIDDLEWARE_CLASSES}.LLMToolEmulator",
        "summary": "Stub selected tools with an LLM (dry runs and demos).",
        "icon": "flask",
        "docs": "https://docs.langchain.com/oss/python/langchain/middleware",
        "fields": [
            _field("tools", "Tools to emulate", "tags",
                   help="Empty emulates every tool."),
        ],
    },
]

MIDDLEWARE_BY_ID: dict[str, dict[str, Any]] = {m["id"]: m for m in MIDDLEWARE}
