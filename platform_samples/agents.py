"""Production seed agents + sample orchestration workflows covering all supported types."""

from __future__ import annotations

from src.agent_platform.catalog.store import DefinitionStore

DEFAULT_MODEL = {"client": "default", "name": None}

WORKSPACE_READ = {
    "server": "Workspace",
    "tools": ["list_dir", "read_file", "search_code"],
}
WORKSPACE_WRITE = {
    "server": "Workspace",
    "tools": ["list_dir", "read_file", "search_code", "write_file", "run_command"],
    "approval": ["write_file", "run_command"],
}
WORKSPACE_RUN = {
    "server": "Workspace",
    "tools": ["run_command"],
    "approval": [],
}
TEXT2SQL_BINDING = {
    "server": "Text2SQL",
    "tools": [
        "list_workspaces",
        "list_tables",
        "get_table_schema",
        "get_join_conditions",
        "search_similar_queries",
        "execute_sql_query",
    ],
    "approval": [],
}

KNOWLEDGE_BINDING = {
    "server": "Knowledge",
    "tools": [
        "search_knowledge",
        "list_knowledge_documents",
        "get_document_info",
        "list_knowledge_tags",
    ],
    "approval": [],
}

# Knowledge Finder agent: grounded QA over the knowledge base documents.
KNOWLEDGE_AGENT = {
    "kind": "agent",
    "runtime": "agent",
    "description": "Retrieval-augmented knowledge assistant. Answers questions from uploaded documents with citations.",
    "instructions": (
        "You are the Knowledge Assistant. To answer a question, ALWAYS call "
        "search_knowledge and pass the user's exact question as the REQUIRED "
        "query argument (e.g. search_knowledge(query=\"<the user's question>\")). "
        "Never call search_knowledge without a query. Use list_knowledge_documents "
        "and list_knowledge_tags if the user asks what is available. Use "
        "get_document_info to retrieve metadata about a specific document. "
        "Always provide grounded answers with citations to the referenced documents."
    ),
    "model": DEFAULT_MODEL,
    "default_options": {"temperature": 0.1, "max_tokens": 8000},
    "mcp_bindings": [KNOWLEDGE_BINDING],
    "maf_skill_ids": [],
    "middleware": [],
}

# 1. Single Agent (Text-to-SQL)
TEXT2SQL_AGENT = {
    "kind": "agent",
    "runtime": "agent",
    "description": "Expert Text-to-SQL data analyst. Natural language to safe SQL and tabular results.",
    "instructions": (
        "You are the Text-to-SQL Agent. Load the text2sql skill. Discover database tables with "
        "list_tables, inspect column schemas and join conditions, retrieve verified query examples, "
        "safely execute SQL with execute_sql_query, and format the output as a Markdown table."
    ),
    "model": DEFAULT_MODEL,
    "default_options": {"temperature": 0.1, "max_tokens": 8000},
    "mcp_bindings": [TEXT2SQL_BINDING],
    "maf_skill_ids": ["text2sql"],
    "middleware": [],
}

# 2. Single Agent with HITL (Developer)
DEVELOPER = {
    "kind": "agent",
    "runtime": "agent",
    "description": "Principal implementer. Code + tests. HITL on write/command.",
    "instructions": (
        "You are the Developer. Load the implementation skill. Fix defects in the workspace "
        "sample-service. Use write_file and run_command only after approval. Never create a "
        "venv or worktree. Use the application interpreter."
    ),
    "model": DEFAULT_MODEL,
    "default_options": {"temperature": 0.2, "max_tokens": 8000},
    "mcp_bindings": [WORKSPACE_WRITE],
    "maf_skill_ids": ["implementation"],
    # Demo fixture: this agent exists to fix defects in a seeded sample service.
    "workspace": {"seed": "sample-service"},
    "middleware": [],
}

# 3. Deep Agent (create_deep_agent)
DEEP_AGENT = {
    "kind": "agent",
    "runtime": "harness",
    "description": "Deep Agent with planning, memory, filesystem, and context compaction.",
    "instructions": (
        "You are a Deep Agent. Use filesystem tools and memory to inspect the workspace, "
        "track todos, and give clear multi-turn progress summaries. Do not write files unless asked."
    ),
    "model": DEFAULT_MODEL,
    "default_options": {"temperature": 0.1, "max_tokens": 700},
    "recursion_limit": 16,
    "mcp_bindings": [WORKSPACE_READ],
    "middleware": [],
}
PROJECT_ASSISTANT = DEEP_AGENT

# 4. Supervisor: manager delegates across turns
RESEARCH_SUPERVISOR = {
    "kind": "workflow",
    "pattern": "supervisor",
    "description": "Supervisor team: Coordinator delegates to a data researcher and a writer.",
    "model": DEFAULT_MODEL,
    "recursion_limit": 25,
    "default_options": {"temperature": 0.1, "max_tokens": 700},
    "manager": {
        "name": "Coordinator",
        "instructions": (
            "You are the Coordinator. You never answer the user yourself and you never use SQL. "
            "For every user message you MUST make exactly two delegations, in this order: "
            "1) call transfer_to_dataresearcher once to gather the facts; "
            "2) the moment it returns, call transfer_to_writer once to write the final answer; "
            "then stop immediately. Never answer before both specialists have run. "
            "Never call the same specialist twice. Never invent SQL."
        ),
    },
    "participants": [
        {
            "name": "DataResearcher",
            "instructions": (
                "Query the database with at most three tool calls (list_tables, get_table_schema, "
                "execute_sql_query). Return findings only. Do not call placeholder tools. Do not keep querying."
            ),
            "mcp_bindings": [TEXT2SQL_BINDING],
        },
        {
            "name": "Writer",
            "instructions": (
                "Write a short user-facing answer from the researcher's findings. No tools. One reply, then stop."
            ),
        },
    ],
}

# 5. Swarm: Command handoffs across specialists
SUPPORT_SWARM = {
    "kind": "workflow",
    "pattern": "swarm",
    "description": "Support swarm: Concierge hands off to SQL or code specialists.",
    "model": DEFAULT_MODEL,
    "recursion_limit": 25,
    "default_options": {"temperature": 0.1, "max_tokens": 700},
    "start_agent": "Concierge",
    "handoffs": [
        {"from": "Concierge", "to": "SqlDesk"},
        {"from": "Concierge", "to": "CodeDesk"},
        {"from": "SqlDesk", "to": "CodeDesk"},
        {"from": "CodeDesk", "to": "SqlDesk"},
    ],
    "participants": [
        {
            "name": "Concierge",
            "instructions": (
                "Route once. Database/customer/order questions go to SqlDesk. "
                "File/code/workspace questions go to CodeDesk. If the user asked for BOTH, "
                "transfer to SqlDesk first. Do not answer yourself. Do not transfer more than once."
            ),
        },
        {
            "name": "SqlDesk",
            "instructions": (
                "Answer data questions with at most three SQL tools, then STOP. "
                "If the user asks about files, folders, or the workspace, transfer to CodeDesk immediately. "
                "Do not answer file questions yourself. Do not transfer back to Concierge."
            ),
            "mcp_bindings": [TEXT2SQL_BINDING],
        },
        {
            "name": "CodeDesk",
            "instructions": (
                "Answer file/workspace questions with at most two list_dir/read_file calls, then STOP. "
                "Only transfer to SqlDesk if the user also asked a data question you have not answered. "
                "Do not loop."
            ),
            "mcp_bindings": [WORKSPACE_READ],
        },
    ],
}

# 6. Graph: freeform multi-step pipeline
REVIEW_GRAPH = {
    "kind": "workflow",
    "pattern": "graph",
    "description": "Review graph: Intake frames the question, Analyst investigates, Closer answers.",
    "model": DEFAULT_MODEL,
    "recursion_limit": 25,
    "default_options": {"temperature": 0.1, "max_tokens": 700},
    "entry": "Intake",
    "nodes": [
        {
            "id": "Intake",
            "name": "Intake",
            "instructions": (
                "You have no tools. Write one sentence restating the user question, then stop. "
                "Do not call tools. Do not answer the question."
            ),
        },
        {
            "id": "Analyst",
            "name": "Analyst",
            "instructions": (
                "Use at most three Text2SQL tool calls, then output findings. Do not keep querying."
            ),
            "mcp_bindings": [TEXT2SQL_BINDING],
        },
        {
            "id": "Closer",
            "name": "Closer",
            "instructions": (
                "You have no tools. Give a short final answer from the analyst findings. "
                "Do not call tools. Under 80 words."
            ),
        },
    ],
    "edges": [
        {"from": "Intake", "to": "Analyst"},
        {"from": "Analyst", "to": "Closer"},
    ],
}


def seed_definitions(*, force: bool = False) -> list[str]:
    """Register the sample agents; returns the slugs actually written.

    Existing definitions are left alone unless ``force`` is set: an operator who
    edited a sample agent (or the eval that uses it) must not lose that work to a
    feed run.
    """
    DefinitionStore.ensure_tables()
    written: list[str] = []
    for slug, name, kind, config in (
        ("text2sql", "Text-to-SQL Agent", "agent", TEXT2SQL_AGENT),
        ("developer", "Developer", "agent", DEVELOPER),
        ("deep-agent", "Deep Agent", "agent", DEEP_AGENT),
        ("research-supervisor", "Research Supervisor", "workflow", RESEARCH_SUPERVISOR),
        ("support-swarm", "Support Swarm", "workflow", SUPPORT_SWARM),
        ("review-graph", "Review Graph", "workflow", REVIEW_GRAPH),
    ):
        if not force and DefinitionStore.get_by_slug(slug) is not None:
            continue
        DefinitionStore.upsert(slug=slug, name=name, kind=kind, config=config, published=True)
        written.append(slug)
    return written
