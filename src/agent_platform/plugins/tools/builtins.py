from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState

from src.agent_platform.runtime.memory_tools import memory_tools

_REGISTRY: dict[str, BaseTool] = {}
_DANGEROUS_RAN = {"count": 0, "last": None}


def register_function_tool(fn_tool: BaseTool) -> None:
    _REGISTRY[fn_tool.name] = fn_tool


def get_function_tool(name: str) -> BaseTool | None:
    return _REGISTRY.get(name)


def list_function_tools() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, fn in _REGISTRY.items():
        desc = getattr(fn, "description", None) or getattr(fn, "__doc__", None) or ""
        out.append({"name": name, "description": str(desc or "").strip()})
    return out


@tool
def dangerous_write(
    path: str,
    content: str,
    workspace_dir: Annotated[str | None, InjectedState("workspace_dir")] = None,
) -> str:
    """Write content to a workspace-relative path. Requires human approval."""
    _DANGEROUS_RAN["count"] = int(_DANGEROUS_RAN["count"]) + 1
    _DANGEROUS_RAN["last"] = {"path": path, "content": content}
    get_stream_writer()({"type": "progress", "message": f"Writing {path}"})
    if not workspace_dir:
        raise RuntimeError("workspace_dir is missing from graph state")
    target = Path(workspace_dir) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


dangerous_write.metadata = {"approval_mode": "always_require"}
register_function_tool(dangerous_write)

for _mem in memory_tools():
    register_function_tool(_mem)


def dangerous_write_calls() -> dict[str, Any]:
    return dict(_DANGEROUS_RAN)


def reset_dangerous_write_calls() -> None:
    _DANGEROUS_RAN["count"] = 0
    _DANGEROUS_RAN["last"] = None


class FunctionToolsPlugin:
    type_id = "function_tools"
    kind = "tool"
    label = "Function Tools"
    icon = "wrench"
    schema = {
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> list[BaseTool]:
        names = spec.get("names") or spec.get("function_tools") or []
        tools: list[BaseTool] = []
        for name in names:
            found = get_function_tool(name)
            if found is None:
                raise KeyError(f"Unknown function tool: {name}")
            tools.append(found)
        extra = getattr(ctx, "extra_function_tools", None)
        if extra:
            tools.extend(extra)
        return tools
