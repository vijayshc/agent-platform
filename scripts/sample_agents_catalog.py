"""Sample agent catalogue for the live Agent Studio (v2 definition schema).

One sample per out-of-the-box capability: both agent runtimes, the supervisor
and swarm patterns, every shipped graph template, the guardrail middleware map,
provider structured output and MCP-declared human-in-the-loop approval.

Every entry is a definition the API accepts as-is (``config.schema == 2``) plus
the task the runner sends and the evidence it must find in the run. The runner
(``scripts/provision_live_usecase_agents.py``) owns the HTTP plumbing; this
module owns only the catalogue so both stay small.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = {"client": "default", "name": None}
#: Short, deterministic generations: the samples assert on tool calls and
#: structure, never on prose.
SHORT = {"temperature": 0.1, "max_tokens": 300}

WS_RUN = {"server": "Workspace", "tools": ["run_command"]}
WS_READ = {"server": "Workspace", "tools": ["list_dir", "read_file", "search_code"]}
WS_WRITE_APPROVAL = {
    "server": "Workspace",
    "tools": ["write_file"],
    "approval": ["write_file"],
}

#: Schema the shipped orchestrator-worker template needs on its planner: a
#: provider-native response format carries a title, and the fan-out reads
#: ``structured_response["items"]``.
WORK_ITEMS_SCHEMA = {
    "title": "WorkItems",
    "description": "The independent work items to run in parallel.",
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    "required": ["items"],
}


def _agent(**config: Any) -> dict[str, Any]:
    """A v2 agent-kind config with the shared model options filled in."""
    out: dict[str, Any] = {"kind": "agent", "schema": 2, "model": DEFAULT_MODEL,
                           "default_options": SHORT}
    out.update(config)
    return out


def _graph(template: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
           entry: str, description: str) -> dict[str, Any]:
    """A v2 workflow config in the shipped blueprint shape (template + graph)."""
    return {
        "kind": "workflow",
        "schema": 2,
        "pattern": "graph",
        "template": template,
        "description": description,
        "model": DEFAULT_MODEL,
        "default_options": SHORT,
        "graph": {"entry": entry, "nodes": nodes, "edges": edges},
    }


def _node(node_id: str, label: str, instructions: str, **agent: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"instructions": instructions}
    spec.update(agent)
    return {"id": node_id, "kind": "agent", "label": label, "agent": spec}


def catalog() -> list[dict[str, Any]]:
    """The sample definitions, each with its task and expected run evidence."""
    return [
        # ------------------------------------------------------- agent runtime
        {
            "slug": "night-watch",
            "name": "Night Watch",
            "kind": "agent",
            "group": "agent runtime (MCP tool)",
            "task": "Is this host under load? Run `uptime` once and report the load averages.",
            "expect": {"tools": ["run_command"], "status": "success"},
            "config": _agent(
                runtime="agent",
                description="On-call host pulse: one Workspace MCP command, one quoted result.",
                instructions=(
                    "You are Night Watch. Call run_command exactly once with the command "
                    "`uptime`. Then reply with one sentence that quotes the load average "
                    "numbers. Do not call any other tool."
                ),
                mcp_bindings=[WS_RUN],
            ),
        },
        {
            "slug": "fact-keeper",
            "name": "Fact Keeper",
            "kind": "agent",
            "group": "agent runtime (function tool)",
            "task": "Remember this fact: the release freeze starts on Friday.",
            "expect": {"tools": ["remember_fact"], "status": "success"},
            "config": _agent(
                runtime="agent",
                description="ReAct agent whose only tool is the shipped remember_fact function tool.",
                instructions=(
                    "You are Fact Keeper. Call remember_fact exactly once with the fact from "
                    "the user message. Then confirm in one short sentence."
                ),
                function_tools=["remember_fact"],
            ),
        },
        # -------------------------------------------------- deep_agent runtime
        {
            "slug": "deep-scout",
            "name": "Deep Scout",
            "kind": "agent",
            "group": "deep_agent runtime",
            "task": (
                "Call write_todos with two todos first, then delegate with the task tool to "
                "subagent_type 'scout': find out which files are in the sample-service "
                "directory. Do not use your own file tools yourself."
            ),
            "expect": {
                "tools": ["write_todos", "task"],
                "text": ["order_service"],
                "status": "success",
                "plan": {
                    "builder": "deepagents.create_deep_agent",
                    "middleware": ["TodoListMiddleware"],
                    "subagents": ["scout"],
                    "memory": ["/memories/AGENTS.md"],
                },
            },
            "config": _agent(
                runtime="deep_agent",
                description="Deep agent: plans with todos, keeps a memory file, delegates to a scout.",
                instructions=(
                    "You are Deep Scout. Always plan with write_todos first (two short todos). "
                    "Delegate every file listing to the 'scout' subagent through the task tool — "
                    "never call list_dir, read_file or search_code yourself. Finish with one "
                    "short sentence naming the files the scout reported."
                ),
                mcp_bindings=[WS_READ],
                workspace={"seed": "sample-service"},
                middleware={"todo_list": {}},
                deep_agent={
                    "memory": ["/memories/AGENTS.md"],
                    "subagents": [
                        {
                            "name": "scout",
                            "description": "Workspace file scout: lists files and reports names.",
                            "prompt": (
                                "You are the scout. Use list_dir on sample-service and report "
                                "the file names you see. Reply with the file names only."
                            ),
                        }
                    ],
                },
            ),
        },
        # ---------------------------------------------------- supervisor pattern
        {
            "slug": "war-room",
            "name": "War Room",
            "kind": "workflow",
            "group": "workflow (supervisor)",
            "task": "Sev-2: is this box out of memory? Get the team's answer.",
            "expect": {"agents": ["Capacity", "Comms"], "text": ["wrap-up"], "status": "success"},
            "config": {
                "kind": "workflow",
                "schema": 2,
                "pattern": "supervisor",
                "description": "Incident supervisor: a manager delegates to two specialists.",
                "model": DEFAULT_MODEL,
                "default_options": SHORT,
                "manager": {
                    "name": "IncidentLead",
                    "instructions": (
                        "You route work and write the final answer. Step 1: call "
                        "transfer_to_capacity exactly once. Step 2: the moment it returns, call "
                        "transfer_to_comms exactly once. Step 3: after Comms replies, write the "
                        "final answer yourself as exactly two short bullets, beginning it with "
                        "the token WRAP-UP, then stop. Never call a transfer tool twice, never "
                        "call recall_facts, and never stop before Comms has replied."
                    ),
                },
                "participants": [
                    {
                        "name": "Capacity",
                        "instructions": (
                            "Answer in one sentence about memory pressure on this host and "
                            "mention the command `free -h`. Reply with text only — do not call "
                            "any tool."
                        ),
                    },
                    {
                        "name": "Comms",
                        "instructions": (
                            "Write one short Slack status line mentioning Sev-2. Reply with "
                            "text only — do not call any tool."
                        ),
                    },
                ],
            },
        },
        # --------------------------------------------------------- swarm pattern
        {
            "slug": "incident-desk",
            "name": "Incident Desk",
            "kind": "workflow",
            "group": "workflow (swarm)",
            "task": "Sev-2: is this box overloaded? I need the load average now.",
            "expect": {
                "tools": ["transfer_to_linuxsre", "run_command"],
                "agents": ["LinuxSre"],
                "status": "success",
            },
            "config": {
                "kind": "workflow",
                "schema": 2,
                "pattern": "swarm",
                "description": "Pager hands control to the Linux SRE, who runs uptime.",
                "model": DEFAULT_MODEL,
                "default_options": SHORT,
                "start_agent": "Pager",
                "handoffs": [
                    {"from": "Pager", "to": "LinuxSre"},
                    {"from": "LinuxSre", "to": "Pager"},
                ],
                "participants": [
                    {
                        "name": "Pager",
                        "instructions": (
                            "You only route. Transfer to LinuxSre immediately with the handoff "
                            "tool. Never answer the question yourself."
                        ),
                    },
                    {
                        "name": "LinuxSre",
                        "instructions": (
                            "Call run_command with `uptime` once. Then quote the load average in "
                            "one sentence and stop. Do not hand back to Pager."
                        ),
                        "mcp_bindings": [WS_RUN],
                    },
                ],
            },
        },
        # ------------------------------------------- graph template: sequential
        {
            "slug": "release-gate",
            "name": "Release Gate",
            "kind": "workflow",
            "group": "graph (sequential)",
            "task": "Should the release train leave the station today?",
            "expect": {"agents": ["Intake", "Verdict"], "text": ["ship"], "status": "success"},
            "config": _graph(
                "sequential",
                [
                    _node(
                        "intake",
                        "Intake",
                        "Restate the user request in one short line and end that line with the "
                        "token INTAKE-OK. Do not answer the question.",
                    ),
                    _node(
                        "verdict",
                        "Verdict",
                        "Read the previous line and answer the original request in one sentence. "
                        "Include the token SHIP. Do not call tools.",
                    ),
                ],
                [{"from": "intake", "to": "verdict"}],
                "intake",
                "Prompt chaining: intake restates the request, the verdict answers it.",
            ),
        },
        # --------------------------------------------- graph template: parallel
        {
            "slug": "red-blue",
            "name": "Red Blue Panel",
            "kind": "workflow",
            "group": "graph (parallel)",
            "task": "How should we think about SQL in sample-service order queries?",
            "expect": {
                "agents": ["RedTeam", "BlueTeam"],
                "text": ["red", "blue"],
                "status": "success",
            },
            "config": _graph(
                "parallel",
                [
                    {"id": "fan", "kind": "set_state", "label": "Start", "values": {}},
                    _node(
                        "red",
                        "RedTeam",
                        "Answer in exactly one sentence from the attacker's point of view. "
                        "Include the token RED.",
                    ),
                    _node(
                        "blue",
                        "BlueTeam",
                        "Answer in exactly one sentence from the defender's point of view. "
                        "Include the token BLUE.",
                    ),
                    {"id": "join", "kind": "join", "label": "Combine", "strategy": "concat"},
                ],
                [
                    {"from": "fan", "to": "red"},
                    {"from": "fan", "to": "blue"},
                    {"from": "red", "to": "join"},
                    {"from": "blue", "to": "join"},
                ],
                "fan",
                "Two independent branches run at once, then their answers are concatenated.",
            ),
        },
        # --------------------------------------------- graph template: routing
        {
            "slug": "triage-desk",
            "name": "Triage Desk",
            "kind": "workflow",
            "group": "graph (routing)",
            "task": "I was charged twice for the same invoice. How do I get a refund?",
            "expect": {
                "route": "billing",
                "agents": ["BillingDesk"],
                "absent_agents": ["TechnicalDesk"],
                "text": ["billing"],
                "status": "success",
            },
            "config": _graph(
                "routing",
                [
                    {
                        "id": "router",
                        "kind": "router",
                        "label": "Route",
                        "routes": [
                            {
                                "name": "billing",
                                "description": "Refunds, invoices, charges and payment questions",
                                "to": "billing",
                            },
                            {
                                "name": "technical",
                                "description": "Errors, crashes, login and how-to questions",
                                "to": "technical",
                            },
                        ],
                        "default": "billing",
                    },
                    _node(
                        "billing",
                        "BillingDesk",
                        "You handle billing. Reply in one sentence and include the token BILLING.",
                    ),
                    _node(
                        "technical",
                        "TechnicalDesk",
                        "You handle technical issues. Reply in one sentence and include the "
                        "token TECHNICAL.",
                    ),
                ],
                [{"from": "router", "to": "billing"}, {"from": "router", "to": "technical"}],
                "router",
                "A router classifies the request and jumps to the matching desk only.",
            ),
        },
        # ------------------------------- graph template: orchestrator_worker
        {
            "slug": "report-forge",
            "name": "Report Forge",
            "kind": "workflow",
            "group": "graph (orchestrator_worker)",
            "task": "Give three independent facts about why parameterized SQL prevents injection.",
            "expect": {
                "fanout": {"agent": "Worker", "min": 2},
                "text": ["sql"],
                "status": "success",
            },
            "config": _graph(
                "orchestrator_worker",
                [
                    _node(
                        "planner",
                        "Planner",
                        "Break the task into exactly three independent work items. Every item "
                        "must be self-contained (a worker sees only that item) and at most 12 "
                        "words long. Return them in the 'items' field.",
                        response_format={"strategy": "provider", "schema": WORK_ITEMS_SCHEMA},
                        # The shared 300-token budget is too small for a JSON array;
                        # a truncated structured response fails the run outright.
                        default_options={"temperature": 0.1, "max_tokens": 900},
                    ),
                    {"id": "fan", "kind": "map", "label": "Fan out", "over": "items", "to": "worker"},
                    _node(
                        "worker",
                        "Worker",
                        "The latest user message is one self-contained work item from a plan. "
                        "Complete exactly that item and reply with the finished content only — "
                        "no questions, no commentary about the process.",
                    ),
                    {"id": "join", "kind": "join", "label": "Synthesize", "strategy": "summarize"},
                ],
                [{"from": "planner", "to": "fan"}, {"from": "worker", "to": "join"}],
                "planner",
                "A planner splits the task, the fan-out runs one worker per item, a join synthesizes.",
            ),
        },
        # -------------------------------- graph template: evaluator_optimizer
        {
            "slug": "polish-loop",
            "name": "Polish Loop",
            "kind": "workflow",
            "group": "graph (evaluator_optimizer)",
            "task": "Write a slogan for parameterized SQL.",
            "expect": {
                "routes": ["revise", "accept"],
                "loop": {"agent": "Polisher", "min": 1},
                "text": ["polished"],
                "status": "success",
            },
            "config": _graph(
                "evaluator_optimizer",
                [
                    _node(
                        "generator",
                        "Generator",
                        "Reply with a slogan about parameterized SQL of at most six words. Reply "
                        "with the slogan only: no quotes, no commentary, and never write the "
                        "word POLISHED.",
                    ),
                    {
                        "id": "evaluator",
                        "kind": "router",
                        "label": "Evaluate",
                        "routes": [
                            {
                                "name": "accept",
                                "description": "The latest draft contains the token POLISHED",
                                "to": "__end__",
                            },
                            {
                                "name": "revise",
                                "description": "The latest draft does not contain the token POLISHED",
                                "to": "polisher",
                            },
                        ],
                        "default": "polisher",
                        # Bounded loop: after three passes the "accept" route wins,
                        # so the run ends with an answer instead of a step-budget error.
                        "max_visits": 3,
                    },
                    _node(
                        "polisher",
                        "Polisher",
                        "Rewrite the latest slogan in the conversation as one sentence of at least "
                        "twenty words. Always include the token POLISHED. Reply with that "
                        "sentence and nothing else, then stop.",
                    ),
                ],
                [
                    {"from": "generator", "to": "evaluator"},
                    {"from": "polisher", "to": "evaluator"},
                ],
                "generator",
                "Generate, evaluate, revise, re-evaluate: the graph loops until the "
                "evaluator accepts the polished draft.",
            ),
        },
        # ------------------------------------------------------------ guardrails
        {
            "slug": "guardrail-post",
            "name": "Guardrail Post",
            "kind": "agent",
            "group": "guardrails (middleware map)",
            "task": (
                "Call write_todos once with exactly two todos, then confirm the guardrails are "
                "on and repeat the ops contact exactly as you received it. "
                "Ops contact: ops@example.com."
            ),
            "expect": {
                "tools": ["write_todos"],
                "text": ["guarded", "redacted_email"],
                "absent_text": ["ops@example.com"],
                "status": "success",
                "plan": {
                    "builder": "langchain.agents.create_agent",
                    "middleware": [
                        "SummarizationMiddleware",
                        "TodoListMiddleware",
                        "ModelCallLimitMiddleware",
                        "PIIMiddleware",
                    ],
                },
            },
            "config": _agent(
                runtime="agent",
                description="Guardrail showcase: summarization, todos, call limit and PII redaction.",
                instructions=(
                    "You are Guardrail Post. Call write_todos exactly once with two short "
                    "todos — never call it a second time. Then reply in one sentence that "
                    "includes the token GUARDED and quotes the ops contact exactly as it "
                    "reached you."
                ),
                middleware={
                    "summarization": {"trigger_tokens": 8000, "keep_messages": 20},
                    "todo_list": {},
                    "model_call_limit": {"run_limit": 6, "exit_behavior": "end"},
                    "pii": {"rules": [{"type": "email", "strategy": "redact"}]},
                },
            ),
        },
        # ----------------------------------------------------- structured output
        {
            "slug": "ledger-clerk",
            "name": "Ledger Clerk",
            "kind": "agent",
            "group": "structured output (provider)",
            "task": "Book three licences at 12.50 USD each.",
            "expect": {
                "structured": ["item", "amount", "currency"],
                "status": "success",
            },
            "config": _agent(
                runtime="agent",
                description="Provider-native structured output: every reply is a LedgerEntry object.",
                instructions=(
                    "Extract the ledger entry from the user message into the required "
                    "structured format. Use the numbers the user gave."
                ),
                # A provider response format is JSON, and the shared 300-token budget
                # can truncate it (a truncated structured response fails the run).
                default_options={"temperature": 0.1, "max_tokens": 900},
                response_format={
                    "strategy": "provider",
                    "schema": {
                        "title": "LedgerEntry",
                        "description": "One accounting entry.",
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "amount": {"type": "number"},
                            "currency": {"type": "string"},
                        },
                        "required": ["item", "amount", "currency"],
                    },
                },
            ),
        },
        # -------------------------------------------------- human in the loop
        {
            "slug": "change-clerk",
            "name": "Change Clerk",
            "kind": "agent",
            "group": "HITL (MCP-declared approval)",
            "task": (
                "Open the change window: write notes/change-window.md with the content "
                "CHANGE-APPROVED."
            ),
            "expect": {
                "hitl": "write_file",
                "files": ["notes/change-window.md"],
                "status": "success",
            },
            "config": _agent(
                runtime="agent",
                description="Writes a change note; the MCP binding puts write_file behind HITL approval.",
                instructions=(
                    "You MUST call write_file with path notes/change-window.md and content "
                    "CHANGE-APPROVED. Do not answer before the tool returns."
                ),
                mcp_bindings=[WS_WRITE_APPROVAL],
            ),
        },
    ]
