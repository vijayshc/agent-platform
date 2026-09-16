"""Out-of-the-box agent runtimes, workflow patterns and graph node kinds.

The Studio editor renders its palette and inspector from this registry, so a new
shipped capability is a backend-only change. Every ``builder`` string names the
real library constructor the compiler calls; see
``src/agent_platform/plugins/orchestration`` and ``runtime/compiler.py``.
"""

from __future__ import annotations

from typing import Any

WORKFLOWS_DOC = "https://docs.langchain.com/oss/python/langgraph/workflows-agents"
AGENTS_DOC = "https://docs.langchain.com/oss/python/langchain/agents"
DEEP_AGENTS_DOC = "https://docs.langchain.com/oss/python/deepagents/overview"
SUPERVISOR_DOC = "https://reference.langchain.com/python/langgraph-supervisor"
SWARM_DOC = "https://reference.langchain.com/python/langgraph-swarm"
MULTI_AGENT_DOC = "https://docs.langchain.com/oss/python/langgraph/multi-agent"

#: Agent runtimes: what a single agent node compiles to.
RUNTIMES: list[dict[str, Any]] = [
    {
        "id": "agent",
        "label": "Agent",
        "builder": "langchain.agents.create_agent",
        "summary": "Tool-calling ReAct agent with your model, tools, skills and guardrails.",
        "when": "Most tasks: one agent, a clear instruction and a set of tools.",
        "icon": "bot",
        "docs": AGENTS_DOC,
        "features": {
            "tools": True,
            "skills": True,
            "middleware": True,
            "structured_output": True,
            "subagents": False,
            "virtual_filesystem": False,
        },
    },
    {
        "id": "deep_agent",
        "label": "Deep Agent",
        "builder": "deepagents.create_deep_agent",
        "summary": "Planning, virtual filesystem, memory files and delegating subagents.",
        "when": "Long research/writing/build tasks that need planning and delegation.",
        "icon": "brain",
        "docs": DEEP_AGENTS_DOC,
        "features": {
            "tools": True,
            "skills": True,
            "middleware": True,
            "structured_output": True,
            "subagents": True,
            "virtual_filesystem": True,
            "memory": True,
            "permissions": True,
        },
    },
]

#: Workflow patterns: how several agents compose.
PATTERNS: list[dict[str, Any]] = [
    {
        "id": "supervisor",
        "label": "Supervisor",
        "template": "supervisor",
        "builder": "langgraph_supervisor.create_supervisor",
        "summary": "A manager agent routes work to specialist agents and writes the final answer.",
        "when": "Specialists with distinct skills, one entry point, one answer.",
        "icon": "sitemap",
        "docs": SUPERVISOR_DOC,
        "min_agents": 1,
    },
    {
        "id": "swarm",
        "label": "Swarm",
        "template": "swarm",
        "builder": "langgraph_swarm.create_swarm",
        "summary": "Peer agents hand control to each other with transfer tools.",
        "when": "Conversations that move between peers (support → billing → support).",
        "icon": "share-2",
        "docs": SWARM_DOC,
        "min_agents": 2,
    },
    {
        "id": "sequential",
        "label": "Prompt chaining",
        "template": "sequential",
        "builder": "langgraph.graph.StateGraph",
        "summary": "Each step refines the previous step's output, in order.",
        "when": "Draft → translate → polish; extract → validate → format.",
        "icon": "arrow-right",
        "docs": WORKFLOWS_DOC,
        "min_agents": 1,
    },
    {
        "id": "parallel",
        "label": "Parallelization",
        "template": "parallel",
        "builder": "langgraph.graph.StateGraph",
        "summary": "Run independent agents at the same time, then combine their answers.",
        "when": "Compare angles, or get a result faster from independent work.",
        "icon": "git-fork",
        "docs": WORKFLOWS_DOC,
        "min_agents": 2,
    },
    {
        "id": "routing",
        "label": "Routing",
        "template": "routing",
        "builder": "langgraph.graph.StateGraph",
        "summary": "Classify the request, then send it down the matching branch.",
        "when": "Different request types need different prompts, models or tools.",
        "icon": "route",
        "docs": WORKFLOWS_DOC,
        "min_agents": 2,
    },
    {
        "id": "orchestrator_worker",
        "label": "Orchestrator–worker",
        "template": "orchestrator_worker",
        "builder": "langgraph.graph.StateGraph + langgraph.types.Send",
        "summary": "A planner splits the task into items and workers run them in parallel.",
        "when": "Many independent items: sections of a report, files, records.",
        "icon": "network",
        "docs": WORKFLOWS_DOC,
        "min_agents": 2,
    },
    {
        "id": "evaluator_optimizer",
        "label": "Evaluator–optimizer",
        "template": "evaluator_optimizer",
        "builder": "langgraph.graph.StateGraph + langgraph.types.Command",
        "summary": "A generator produces, a critic judges, and the loop repeats until accepted.",
        "when": "Quality bars that need an explicit review pass.",
        "icon": "repeat",
        "docs": WORKFLOWS_DOC,
        "min_agents": 2,
    },
    {
        "id": "graph",
        "label": "Custom graph",
        "template": "custom",
        "builder": "langgraph.graph.StateGraph",
        "summary": "Wire any nodes and edges yourself, including loops and fan-out.",
        "when": "The shipped patterns do not fit.",
        "icon": "workflow",
        "docs": "https://docs.langchain.com/oss/python/langgraph/graph-api",
        "min_agents": 1,
    },
]

#: Node kinds a custom/pattern graph may contain.
NODE_KINDS: list[dict[str, Any]] = [
    {
        "id": "agent",
        "label": "Agent",
        "summary": "A full agent (model, tools, guardrails).",
        "icon": "bot",
        "builder": "langchain.agents.create_agent",
        "fields": [],
    },
    {
        "id": "router",
        "label": "Router",
        "summary": "Classifies the request and jumps to the matching branch.",
        "icon": "route",
        "builder": "langgraph.types.Command",
        "fields": [
            {"name": "routes", "label": "Routes", "type": "routes",
             "help": "One row per branch: name, what it means, destination."},
            {"name": "default", "label": "Fallback route", "type": "select"},
            {"name": "max_visits", "label": "Max passes", "type": "number",
             "help": "For routing loops: after this many visits the first route is taken, "
                     "so the flow always ends with an answer."},
        ],
    },
    {
        "id": "tool",
        "label": "Tool",
        "summary": "Runs one tool directly as a step.",
        "icon": "wrench",
        "builder": "langgraph.prebuilt.ToolNode",
        "fields": [
            {"name": "tool", "label": "Tool", "type": "select"},
        ],
    },
    {
        "id": "join",
        "label": "Combine",
        "summary": "Waits for parallel branches and merges their answers.",
        "icon": "git-merge",
        "builder": "langgraph.graph.StateGraph",
        "fields": [
            {"name": "strategy", "label": "Strategy", "type": "select",
             "options": ["concat", "last", "summarize"], "default": "concat"},
        ],
    },
    {
        "id": "map",
        "label": "Fan out",
        "summary": "Sends one worker run per item of a list produced upstream (LangGraph Send).",
        "icon": "git-fork",
        "builder": "langgraph.types.Send",
        "fields": [
            {"name": "over", "label": "List field", "type": "text",
             "help": "Key of the list in the upstream structured output, e.g. items."},
            {"name": "to", "label": "Worker", "type": "select"},
        ],
    },
    {
        "id": "human",
        "label": "Human review",
        "summary": "Pauses the run for a human decision (LangGraph interrupt).",
        "icon": "user-check",
        "builder": "langgraph.types.interrupt",
        "fields": [
            {"name": "message", "label": "What to ask", "type": "text"},
        ],
    },
    {
        "id": "subgraph",
        "label": "Saved flow",
        "summary": "Reuses another saved agent or workflow as a node.",
        "icon": "package",
        "builder": "langgraph.graph.StateGraph (subgraph)",
        "fields": [
            {"name": "ref", "label": "Definition", "type": "select"},
        ],
    },
    {
        "id": "set_state",
        "label": "Set values",
        "summary": "Writes fixed values into the flow state.",
        "icon": "edit",
        "builder": "langgraph.graph.StateGraph",
        "fields": [
            {"name": "values", "label": "Values", "type": "json"},
        ],
    },
]

NODE_KINDS_BY_ID: dict[str, dict[str, Any]] = {n["id"]: n for n in NODE_KINDS}
RUNTIMES_BY_ID: dict[str, dict[str, Any]] = {r["id"]: r for r in RUNTIMES}
PATTERNS_BY_ID: dict[str, dict[str, Any]] = {p["id"]: p for p in PATTERNS}

#: Blueprints the editor materialises on the canvas. A template lists nodes with
#: stable ``key``s plus edges between those keys; the editor assigns ids. The
#: compiler understands the same structure, so an API client can submit a
#: template without ever touching the canvas.
TEMPLATES: dict[str, dict[str, Any]] = {
    "sequential": {
        "pattern": "sequential",
        "nodes": [
            {"key": "first", "kind": "agent", "label": "Step 1",
             "agent": {"instructions": "Do the first step of the task."}},
            {"key": "second", "kind": "agent", "label": "Step 2",
             "agent": {"instructions": "Refine and complete the previous step's output."}},
        ],
        "edges": [{"from": "first", "to": "second"}],
        "entry": "first",
    },
    "parallel": {
        "pattern": "parallel",
        "nodes": [
            {"key": "fan", "kind": "set_state", "label": "Start", "values": {}},
            {"key": "left", "kind": "agent", "label": "Branch A",
             "agent": {"instructions": "Answer the task from the first angle."}},
            {"key": "right", "kind": "agent", "label": "Branch B",
             "agent": {"instructions": "Answer the task from a second, independent angle."}},
            {"key": "join", "kind": "join", "label": "Combine", "strategy": "concat"},
        ],
        "edges": [
            {"from": "fan", "to": "left"},
            {"from": "fan", "to": "right"},
            {"from": "left", "to": "join"},
            {"from": "right", "to": "join"},
        ],
        "entry": "fan",
    },
    "routing": {
        "pattern": "routing",
        "nodes": [
            {"key": "router", "kind": "router", "label": "Route",
             "routes": [
                 {"name": "type_a", "description": "First kind of request", "to": "a"},
                 {"name": "type_b", "description": "Second kind of request", "to": "b"},
             ],
             "default": "a"},
            {"key": "a", "kind": "agent", "label": "Handler A",
             "agent": {"instructions": "Handle the first kind of request."}},
            {"key": "b", "kind": "agent", "label": "Handler B",
             "agent": {"instructions": "Handle the second kind of request."}},
        ],
        "edges": [{"from": "router", "to": "a"}, {"from": "router", "to": "b"}],
        "entry": "router",
    },
    "orchestrator_worker": {
        "pattern": "orchestrator_worker",
        "nodes": [
            {"key": "planner", "kind": "agent", "label": "Planner",
             "agent": {
                 "instructions": (
                     "Break the task into the smallest set of independent work items. "
                     "Every item must be self-contained: it is handed to a worker that "
                     "sees only that item and must be able to finish it without asking "
                     "questions. Return the items in the 'items' field."
                 ),
                 # Provider-native structured output. A forced tool call is
                 # rejected by reasoning models ("thinking mode does not support
                 # this tool_choice"), and the schema needs a title because it is
                 # sent to the provider as a named response format.
                 "response_format": {
                     "strategy": "provider",
                     "schema": {
                         "title": "WorkItems",
                         "description": "The independent work items to run in parallel.",
                         "type": "object",
                         "properties": {
                             "items": {"type": "array", "items": {"type": "string"}},
                         },
                         "required": ["items"],
                     },
                 },
             }},
            {"key": "fan", "kind": "map", "label": "Fan out", "over": "items", "to": "worker"},
            {"key": "worker", "kind": "agent", "label": "Worker",
             "agent": {"instructions": (
                 "The latest user message is one self-contained work item from a plan. "
                 "Complete exactly that item and reply with the finished content only — "
                 "no questions, no commentary about the process."
             )}},
            {"key": "join", "kind": "join", "label": "Synthesize", "strategy": "summarize"},
        ],
        "edges": [
            {"from": "planner", "to": "fan"},
            {"from": "worker", "to": "join"},
        ],
        "entry": "planner",
    },
    "evaluator_optimizer": {
        "pattern": "evaluator_optimizer",
        "nodes": [
            {"key": "generator", "kind": "agent", "label": "Generator",
             "agent": {"instructions": "Produce or improve the answer to the task."}},
            {"key": "evaluator", "kind": "router", "label": "Evaluate",
             "model": {},
             "routes": [
                 {"name": "accept", "description": (
                     "The answer is complete, correct and readable. Accept it — do not ask "
                     "for another pass just to make it better."
                 ), "to": "__end__"},
                 {"name": "revise", "description": (
                     "Something required is missing, wrong or unclear"
                 ), "to": "generator"},
             ],
             # The fallback is a *node id* (or __end__), never a route name, and
             # max_visits bounds the loop: after 3 passes the first route wins so
             # the run ends with an answer instead of a recursion-limit error.
             "default": "generator",
             "max_visits": 3},
        ],
        "edges": [{"from": "generator", "to": "evaluator"}],
        "entry": "generator",
    },
    "custom": {
        "pattern": "graph",
        "nodes": [],
        "edges": [],
        "entry": "",
    },
}
