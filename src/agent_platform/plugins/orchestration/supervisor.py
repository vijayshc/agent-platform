from __future__ import annotations

from typing import Any

from langgraph_supervisor import create_supervisor


class SupervisorPlugin:
    type_id = "supervisor"
    kind = "orchestration"
    label = "Supervisor"
    icon = "sitemap"
    schema = {
        "type": "object",
        "properties": {
            "participants": {"type": "array"},
            "manager": {"type": "object"},
            "output_mode": {"type": "string", "enum": ["last_message", "full_history"]},
            "parallel_tool_calls": {"type": "boolean"},
            "response_format": {"type": "object"},
        },
    }

    def compile(self, spec: dict[str, Any], ctx: Any) -> Any:
        participants = list(spec.get("participants") or ctx.participants or [])
        if not participants:
            raise RuntimeError("Supervisor workflow has no specialists")

        manager_spec = spec.get("manager") if isinstance(spec.get("manager"), dict) else {}
        manager_agent = spec.get("manager_agent") or getattr(ctx, "manager_agent", None)
        supervisor_name = str(
            manager_spec.get("name")
            or getattr(manager_agent, "name", None)
            or "supervisor"
        )
        # The router is the manager: it runs on the manager's model and speaks
        # with the manager's compiled instructions, so the connection's System
        # Instruction, the operating policy, skills and capabilities all apply
        # to routing decisions exactly as they do to the manager agent itself.
        prompt = str(
            getattr(manager_agent, "instructions", None)
            or manager_spec.get("instructions")
            or f"You are {supervisor_name}. Delegate work to the specialist agents."
        )
        model = (
            getattr(manager_agent, "traced_model", None)
            or getattr(manager_agent, "client", None)
            or getattr(ctx, "client", None)
        )
        if model is None:
            raise RuntimeError("Supervisor workflow has no model")

        # The manager's own tools are handed to the router (create_supervisor
        # builds the routing agent itself, so this is the only place they can be
        # attached). Agent *middleware* has no equivalent there — validation
        # warns when an author configures one on a manager.
        manager_tools = list(getattr(manager_agent, "tools", None) or [])
        # create_supervisor swallows checkpointer/store/name in a deprecated-kwargs
        # catch-all: everything that configures the graph goes to .compile(), and
        # the graph always gets a name (a nested supervisor needs one).
        builder = create_supervisor(
            participants,
            model=model,
            tools=manager_tools or None,
            prompt=prompt,
            supervisor_name=supervisor_name,
            output_mode=str(spec.get("output_mode") or "last_message"),
            parallel_tool_calls=bool(spec.get("parallel_tool_calls")),
            response_format=spec.get("response_format") or None,
        )
        graph_name = str(spec.get("name") or supervisor_name)
        graph = builder.compile(
            checkpointer=getattr(ctx, "checkpoint_storage", None),
            store=getattr(ctx, "store", None),
            name=graph_name,
        )
        graph.kind = "workflow"
        graph.name = graph_name
        return graph
