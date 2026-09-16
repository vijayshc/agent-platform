# Text2SQL Assistant

A comprehensive AI-powered database query assistant with an **agent platform** (Chat, Studio, Runs) built on Microsoft Agent Framework (MAF).

## Key Features

* **Natural Language to SQL:** Converts English questions to SQL with schema awareness
* **Knowledge Base Q&A:** Vector search-based document Q&A with conversational support
* **Agents (`/agent`):** ChatGPT/Grok-style chat. The only runtime choice is **which agent**. Streaming, file chips, inline HITL, Spotlight picker
* **Agent Studio (`/agent-studio`):** Drag-and-drop designer (React + XYFlow). Agent (`create_react_agent`), Deep Agent (`create_deep_agent`), Supervisor, Swarm, Graph
* **Agent Runs (`/agent-runs`):** Live waterfall over MAF OpenTelemetry spans (not the old AutoGen event table)
* **Public execution API:** `POST /api/v1/runs` is the product. Chat, Studio, and API keys share the same path
* **AI Data Mapping Analyst:** Advanced data mapping and lineage analysis
* **MCP Skill Library:** Enterprise skill repository with semantic search (separate from MAF Agent Skills)
* **Audit Trail:** Daily-rotated audit files recording every allowed or rejected access, upload, download and admin write action
* **Security:** RBAC, CSRF protection, rate limiting, session cookie **and** API keys
* **Admin Dashboard:** User management, MCP server registry, system monitoring, and configuration

## Tech Stack

* **Backend:** Python 3.11 (`~/anaconda3/bin/python3` in this environment), Flask, SQLAlchemy
* **Agent runtime:** Microsoft Agent Framework (`agent-framework`) — compiler/host in `src/agent_platform/`, not a second orchestrator
* **AI:** Azure AI Inference, Sentence-Transformers, OpenRouter
* **Frontend (Agents / Studio / Runs):** Vite, React 18, TypeScript, `@xyflow/react`
* **Frontend (other products):** Flask templates, HTML, CSS, JavaScript, Bootstrap 5
* **Database:** SQLite (default), ChromaDB for vector storage
* **MCP:** Model Context Protocol stdio and HTTP servers, attached to LangGraph agents through `langchain-mcp-adapters`

Use **one interpreter** for the app, skill scripts, Workspace `run_command`, and MCP stdio servers this app launches: `sys.executable`. Do **not** create a per-agent venv or git worktree sandbox.

## Quick Start

This checkout is meant to run with Anaconda Python:

```bash
~/anaconda3/bin/python3 -m pip install -r requirements.txt
```

1. **Clone Repository:**
   ```bash
   git clone <repository-url> && cd text2sql
   ```

2. **Configuration:**
   - Copy `.env.example` to `.env`
   - Set required variables: `AZURE_ENDPOINT`, `AZURE_MODEL_NAME`, `GITHUB_TOKEN`, `DATABASE_URI`, `SECRET_KEY`

3. **Initialize Database** (one-time; idempotent, creates nothing sample-specific):
   ```bash
   ~/anaconda3/bin/python3 scripts/setup_platform.py       # schema + roles + admin user
   ~/anaconda3/bin/python3 scripts/feed_samples.py         # optional: sample agents + MCP servers
   ```
   Sample content is entirely optional: the app boots and serves against a
   database that only went through `setup_platform.py`.

4. **Build the agent SPA** (Chat, Studio, Runs). Flask serves the built assets from `static/agent-app/`:
   ```bash
   cd frontend/agent-app
   npm ci
   npm run build
   cd ../..
   ```

5. **Start Services:**
   ```bash
   # Start ChromaDB service
   cd chromadb_service && ./start_service.sh

   # Start main application (Anaconda interpreter)
   cd .. && ~/anaconda3/bin/python3 app.py
   # or: ./start.sh
   ```

6. **Access Application:**
   - Open `http://127.0.0.1:5000`
   - Default admin: `admin/admin123` (change immediately!)
   - **Agents:** `/agent`
   - **Agent Studio:** `/agent-studio`
   - **Agent Runs:** `/agent-runs`

## Agent platform

Three surfaces, **one** runtime.

```
Chat UI   ─┐
Studio UI ─┼─► POST /api/v1/runs ─► compiler ─► MAF run_stream / workflow
Runs UI   ─┤
API key   ─┘
```

| Surface | Route | What it is |
|---|---|---|
| Agents | `/agent` | Conversation. Spotlight picks a published agent or workflow by **name**. No mode / team / MCP / model dropdowns on the composer |
| Agent Studio | `/agent-studio` | XYFlow canvas. Palette is MAF types. Save, validate, publish, test-run (same `/api/v1/runs`) |
| Agent Runs | `/agent-runs` | Span waterfall (`invoke_agent`, `chat`, `execute_tool`, HITL). Live while streaming |

**MAF Agent Skills** (portable `SKILL.md` packages, `SkillsProvider`) are **not** the admin Skill Library. Both exist. Studio “Skills” means MAF skills.

Seeded published agents (after first boot): `design-reviewer`, `backend-reviewer`, `developer`, Magentic `engineering-studio`.

### Public API (session cookie or hashed API key)

```
POST   /api/v1/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/events          # SSE
POST   /api/v1/runs/{run_id}/approvals       # HITL
POST   /api/v1/runs/{run_id}/cancel

GET    /api/v1/agents
POST   /api/v1/agents/{id}/invoke
POST   /api/v1/agents/{id}/invoke/stream

GET/POST /api/v1/conversations
POST     /api/v1/conversations/{id}/messages
POST     /api/v1/attachments
```

SSE types: `status`, `token`, `agent_switch`, `tool_call`, `tool_result`, `approval_request`, `plan_review`, `skill_load`, `error`, `done`.

## Tests

Always use `~/anaconda3/bin/python3` (do not invoke `python` / `python3` from PATH).
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` is required because the installed pytest-flask is
incompatible with Flask 3.

The agent/LLM suites are **live**: they drive the running app at
`http://127.0.0.1:5000` (`LIVE_AGENT_BASE_URL`) and the real model stored in
`llm_connections`. There is no offline switch, so start the app first and leave it up:

```bash
./start.sh          # the suites log in as admin / admin
```

### Backend + live agent/LLM suites

```bash
cd /home/vijay/gitrepo/copilot/text2sql
PHOENIX_WORKING_DIR=$PWD/.phoenix \
LIVE_AGENT_BASE_URL=http://127.0.0.1:5000 \
~/anaconda3/bin/python3 -m pytest tests/ \
  --ignore=tests/test_hosting_boot_restore_live.py \
  --ignore=tests/test_hosting_crash_recovery_live.py
```

`tests/test_hosting_boot_restore_live.py` and `tests/test_hosting_crash_recovery_live.py`
are **standalone scripts, not pytest modules**: each runs its whole scenario at import
time, so pytest must ignore them (importing one aborts the session with an
`INTERNALERROR`). Run them directly, as shown below.

`PHOENIX_WORKING_DIR` is only needed where `$HOME` is read-only (sandboxes, CI); Phoenix
otherwise uses `~/.phoenix`.

### Hosted apps

These suites start their own platform instances and **refuse to run against this
deployment's database or app workspaces** (`DATABASE_URI` / `HOSTED_APPS_ROOT`): a test
instance sharing them adopts the running apps, rebinds their sockets and leaves the real
deployment answering 502. Give each script suite its own scratch state and create the
schema first:

```bash
export PHOENIX_WORKING_DIR=$PWD/.phoenix
export DATABASE_URI="sqlite:///$PWD/temp/pytest-scratch/crash.db"
export HOSTED_APPS_ROOT="$PWD/temp/pytest-scratch/crash-apps"
mkdir -p "$HOSTED_APPS_ROOT"
~/anaconda3/bin/python3 scripts/setup_platform.py        # creates admin / admin123

LIVE_AGENT_PASSWORD=admin123 ~/anaconda3/bin/python3 tests/test_hosting_crash_recovery_live.py
LIVE_AGENT_PASSWORD=admin123 ~/anaconda3/bin/python3 tests/test_hosting_boot_restore_live.py

# isolation tiers, then end to end against the live app
~/anaconda3/bin/python3 -m pytest tests/test_hosting_tiers_live.py -q
LIVE_AGENT_BASE_URL=http://127.0.0.1:5000 \
  ~/anaconda3/bin/python3 -m pytest tests/test_hosted_apps_live.py -q
```

`LIVE_AGENT_PASSWORD` must match the scratch instance's admin password — `admin123` after
`setup_platform.py`, while the script suites default to `admin` and otherwise stop at the
first API call with `401 Authentication required`. The end-to-end suite discovers the apps
origin from the platform's own `/apps/<slug>/` redirect; set
`LIVE_AGENT_APPS_URL=http://127.0.0.1:5001` to skip that discovery (e.g. behind a reverse
proxy).

### UI

```bash
# Playwright, against its own Flask e2e server on :5055 (admin / admin123)
cd frontend/agent-app
npx playwright test

# theme bootstrap regression harness - no server, prints one JSON document
cd /home/vijay/gitrepo/copilot/text2sql
node tests/js/theme_boot_check.mjs "$PWD"
```

## Detailed Documentation

- [Local Browser Proxy: End-to-End Design and Usage Guide](docs/local-browser-proxy.md)

## Architecture Overview

```
text2sql/
├── app.py                      # Flask host (auth, RBAC, sibling products)
├── frontend/agent-app/         # Vite React SPA (Chat, Studio, Runs)
├── static/agent-app/           # Built SPA assets Flask serves
├── src/agent_platform/         # MAF compiler/host, catalog, /api/v1 (framework only)
│   ├── runtime/                # definition JSON → MAF objects → run_stream
│   ├── catalog/                # agent_definitions, SKILL.md packages
│   ├── conversations/
│   ├── execution/              # runs, spans, API keys
│   ├── plugins/                # models, MCP, skills, tools, orchestration
│   └── api/                    # Flask /api/v1 blueprint
├── platform_samples/           # optional sample content: MCP tool servers, agents, skills
├── scripts/                    # setup_platform.py, feed_samples.py, mcp_http_service.py
├── src/agents/                 # Query-editor AI agents (not the agent platform)
├── src/models/                 # SQLAlchemy models (MCP, Skill Library, users, …)
├── src/routes/                 # Flask Blueprints for other products
├── src/services/               # MCP servers, observability sink
├── templates/                  # Flask templates (SPA shell + other products)
├── chromadb_service/
├── uploads/agent-workspace/    # Per-run/conversation sandbox (not a venv)
└── text2sql.db
```

Query Editor, Knowledge Finder, Data Mapping, and Code Generator remain Flask pages. The agent product is the React SPA.

## Advanced Features

### AI Data Mapping Analyst
Advanced enterprise-grade system for data warehousing and data mart development:
- Intelligent column mapping with AI-powered analysis
- Graph-based join path discovery using NetworkX
- Semantic column matching across different naming conventions
- ETL transformation logic generation
- New table structure proposals

### MCP Skill Library Server
Enterprise skill repository with:
- Vector search for natural language queries
- Dynamic category organization
- Step-by-step technical guidance
- Admin interface for skill management
- HTTP/SSE interface for MCP integration

This is **not** MAF Agent Skills. MAF skills are `SKILL.md` packages edited in Agent Studio.

### Agents, Studio, Runs
- Spotlight (`Ctrl/Cmd+K`) picks a published agent or Magentic workflow by name
- MCP tools attach as MAF MCP classes from the existing MCP server manager
- HITL Approve/Deny and Magentic plan review unblock the **same** run
- Workspace MCP (`list_dir`, `read_file`, `write_file`, `run_command`) uses `sys.executable`
- Runs UI shows MAF spans; Chat “view run” deep-links here

### Enhanced Search Features
- **Knowledge Base:** Conversational Q&A with vector similarity
- **Schema Exploration:** Comprehensive database metadata analysis

## Configuration

### Environment Variables
```bash
# AI Configuration
AZURE_ENDPOINT=your-azure-endpoint
AZURE_MODEL_NAME=your-model-name
MESSAGE_FORMAT=openai  # or llama

# Database
DATABASE_URI=sqlite:///text2sql.db

# Security
SECRET_KEY=your-secret-key
DEBUG=False

# Services
CHROMADB_SERVICE_URL=http://localhost:8001

# Conversation Limits
KNOWLEDGE_CONVERSATION_HISTORY_LIMIT=10
```

### LDAP Authentication (Optional)
```bash
AUTH_PROVIDER=ldap
LDAP_SERVER_URI=ldaps://ad.example.com
LDAP_USE_SSL=true
LDAP_USER_FILTER_TEMPLATE=(sAMAccountName={username})
LDAP_ALLOWED_GROUP_DN=CN=Text2SQL Users,OU=Groups,DC=example,DC=com
```

## Service Management

### ChromaDB Service
```bash
# Start service
cd chromadb_service && ./start_service.sh

# Health check
curl http://localhost:8001/health

# View logs
tail -f chromadb_service/service.log
```

### MCP Servers
Workspace, Text2SQL, and Knowledge MCP servers are seeded by the agent platform on boot (stdio, `sys.executable`). Register others in **Admin → MCP Servers**; each row reports the tools it advertises, and **Test tools** connects live with the same client a run uses.

#### HTTP MCP servers

A row with `server_type = http` is stored as `{"url": …, "headers": {…}}` and reached
over streamable HTTP with the same `langchain-mcp-adapters` client and the same
connection builder as stdio, so discovery, agent bindings, and HITL behave
identically.

An HTTP server is an endpoint, not a subprocess, so the app never starts one.
For a server this platform hosts, give the row a `service` block and run the
service script:

```json
{
  "url": "http://127.0.0.1:8765/mcp",
  "headers": {"Authorization": "Bearer <token>"},
  "service": {"module": "platform_samples.mcp_servers.text2sql"}
}
```

```bash
python scripts/mcp_http_service.py --autostart          # start every such row
python scripts/mcp_http_service.py --autostart --dry-run
python scripts/mcp_http_service.py --module platform_samples.mcp_servers.text2sql --port 8765 --token <token>
```

`--autostart` probes each row with a real MCP `initialize` and launches only the
ones that do not answer, so it is safe to run on every boot (for example from a
systemd unit or `start.sh`). Logs land in `logs/mcp-http-<name>.log`; servers
without a `service` block are never touched.

## Platform vs. sample content

`src/agent_platform/` is the framework: it contains no demo agents, no bundled
tool servers, and no table creation. Everything the sample experience needs
lives in `platform_samples/` and is registered by scripts:

| Script | Owns |
| --- | --- |
| `scripts/setup_platform.py` | schema, module permissions, roles, first admin user, default LLM connection |
| `scripts/feed_samples.py` | sample MCP servers, skill packages, agents, evals, demo dataset |
| `scripts/mcp_http_service.py` | hosting/starting the HTTP MCP endpoints |
| `scripts/demo_database.py` | recreating a throwaway demo database (destructive) |

`setup_platform.py --check` reports anything missing (tables, admin role holder,
LLM connection) without changing a thing, and the app refuses to boot against an
uninitialized database instead of serving a half-working instance. The feed is
create-only for agents and evals: `--force` rewrites them in place, so a
refreshed sample eval keeps its id and its recorded results. The feed also
rewrites rows that still point at the retired `src/agent_platform.seeds.*`
server modules.

Deleting `platform_samples/` leaves a fully working platform: agents and MCP
servers simply have to be created by the operator (bundle nothing, register
what you want in **Admin → MCP Servers**).

## Security

* Secure password hashing with bcrypt
* Role-Based Access Control (RBAC)
* CSRF protection and security headers
* Rate limiting on sensitive endpoints
* Audit logging for compliance
* API keys (hashed) with scopes `runs:write`, `agents:read`, `agents:write`
* HITL on mutating Workspace tools (`write_file`, `run_command`)

## Contributing

Contributions welcome! Please follow standard fork/branch/pull request workflow.

## License

MIT License
