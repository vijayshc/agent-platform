"""Register all built-in plugins once.

Plugins cover the pieces that need a live compile step: models, MCP bindings,
skills, function tools and the orchestration patterns. Agent *middleware* is not
a plugin: every guardrail is a shipped ``langchain.agents.middleware`` class
described by ``catalog/capabilities`` and built by
``runtime/langchain_middleware``.
"""

from __future__ import annotations

from src.agent_platform.plugins.registry import PluginRegistry, get_registry

_REGISTERED = False


def register_builtin_plugins(registry: PluginRegistry | None = None) -> PluginRegistry:
    global _REGISTERED
    registry = registry or get_registry()
    if registry is get_registry() and _REGISTERED and registry.all():
        return registry

    from src.agent_platform.plugins.models.llm_connection import LLMConnectionModelPlugin
    from src.agent_platform.plugins.models.scripted import ScriptedModelPlugin
    from src.agent_platform.plugins.mcp.binding import MCPBindingPlugin
    from src.agent_platform.plugins.skills.file_skills import FileSkillsPlugin
    from src.agent_platform.plugins.tools.builtins import FunctionToolsPlugin
    from src.agent_platform.plugins.orchestration.supervisor import SupervisorPlugin
    from src.agent_platform.plugins.orchestration.swarm import SwarmPlugin
    from src.agent_platform.plugins.orchestration.graph import GraphPlugin

    for plugin in (
        LLMConnectionModelPlugin(),
        ScriptedModelPlugin(),
        MCPBindingPlugin(),
        FileSkillsPlugin(),
        FunctionToolsPlugin(),
        SupervisorPlugin(),
        SwarmPlugin(),
        GraphPlugin(),
    ):
        if registry.get(plugin.type_id) is None:
            registry.register(plugin)
    if registry is get_registry():
        _REGISTERED = True
    return registry
