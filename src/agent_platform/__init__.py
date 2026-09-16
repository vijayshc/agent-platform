"""Agent platform: compile definitions to Microsoft Agent Framework and host runs."""

from src.agent_platform.plugins.registry import PluginRegistry, get_registry

__all__ = ["PluginRegistry", "get_registry"]
