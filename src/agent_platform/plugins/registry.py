"""Plugin registry. Each plugin has a type id, inspector schema, and compile()."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    type_id: str
    kind: str
    label: str
    icon: str | None
    schema: dict[str, Any]

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        ...


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        self._plugins[plugin.type_id] = plugin

    def get(self, type_id: str) -> Plugin | None:
        return self._plugins.get(type_id)

    def by_kind(self, kind: str) -> list[Plugin]:
        return [p for p in self._plugins.values() if p.kind == kind]

    def all(self) -> list[Plugin]:
        return list(self._plugins.values())

    def compile(self, type_id: str, spec: dict[str, Any], ctx: Any) -> Any:
        plugin = self.get(type_id)
        if plugin is None:
            raise KeyError(f"Unknown plugin type: {type_id}")
        return plugin.compile(spec, ctx)

    def inspector_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "type_id": p.type_id,
                "kind": p.kind,
                "label": p.label,
                "icon": p.icon,
                "schema": p.schema,
            }
            for p in self._plugins.values()
        ]


_REGISTRY = PluginRegistry()


def get_registry() -> PluginRegistry:
    return _REGISTRY


def reset_registry() -> PluginRegistry:
    global _REGISTRY
    _REGISTRY = PluginRegistry()
    return _REGISTRY
