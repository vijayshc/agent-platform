from __future__ import annotations

from typing import Any

from langgraph_swarm import create_handoff_tool, create_swarm

from src.agent_platform.plugins.orchestration._util import resolve_participant


def swarm_handoff_map(spec: dict[str, Any], names: list[str]) -> dict[str, list[str]]:
    """Resolve the allowed handoff destinations per participant.

    Falls back to an all-to-all topology when the spec declares no usable
    handoff edges (matching the previous plugin behaviour).
    """
    allowed: dict[str, list[str]] = {n: [] for n in names}
    for edge in spec.get("handoffs") or []:
        src = edge.get("from")
        dst = edge.get("to")
        if src in allowed and dst in allowed and dst not in allowed[src]:
            allowed[src].append(dst)
    if not any(allowed.values()):
        for src in names:
            allowed[src] = [n for n in names if n != src]
    return allowed


def swarm_handoff_tools(agent_name: str, destinations: list[str]) -> list[Any]:
    """Build the ``transfer_to_*`` tools a swarm participant needs at build time."""
    return [
        create_handoff_tool(agent_name=dst, description=f"Transfer to {dst}.")
        for dst in destinations
    ]


class SwarmPlugin:
    type_id = "swarm"
    kind = "orchestration"
    label = "Swarm"
    icon = "share"
    schema = {
        "type": "object",
        "properties": {
            "participants": {"type": "array"},
            "start_agent": {"type": "string"},
            "handoffs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                    },
                },
            },
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        participants = list(spec.get("participants") or ctx.participants or [])
        if not participants:
            raise RuntimeError("Swarm workflow has no participants")

        start = resolve_participant(spec.get("start_agent"), participants) or participants[0]
        start_name = str(getattr(start, "name", None) or "agent_0")
        builder = create_swarm(participants, default_active_agent=start_name)
        graph_name = str(spec.get("name") or "swarm")
        # create_swarm swallows checkpointer/store in a deprecated-kwargs catch-all:
        # they belong to .compile(), which also needs a name for nested use.
        graph = builder.compile(
            checkpointer=getattr(ctx, "checkpoint_storage", None),
            store=getattr(ctx, "store", None),
            name=graph_name,
        )
        graph.kind = "workflow"
        graph.name = graph_name
        return graph
