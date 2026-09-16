# SKILL_SETUP.md — Skill packages, agent setup, and the issues we hit

Everything needed to install a skill, wire an agent to it, and keep runs healthy.
Written from the work of getting the Anthropic **pptx** skill to produce a real deck
through the chat UI.

---

## 1. What a "skill" is in this app

A skill is a **directory on disk** whose entry point is `SKILL.md`:

```
uploads/maf-skills/pptx/
├── SKILL.md                # YAML front matter (name, description) + instructions
├── scripts/                # runnable helpers
│   └── office/schemas/…    # arbitrarily deep, any file type
├── references/             # docs the agent reads on demand
└── assets/                 # images, fonts, …
```

* `SKILL.md` front matter supplies `name` and `description`. The description is what
  the model sees when deciding whether the skill applies.
* Everything else is an **artifact** — read via `read_skill_file`, executed via the
  `@skills/<name>/…` alias. Binary files are imported and served but not edited inline.
* Skills are stored in `uploads/maf-skills/<slug>/` (user) or
  `src/agent_platform/skills/<slug>/` (built-in, empty by default — the framework
  ships no skill content). A user package shadows a built-in one of the same name.
  The example packages used by the sample agents live in `platform_samples/skills/`
  and are registered by `scripts/feed_samples.py`.

At runtime the agent gets two tools (from `src/agent_platform/plugins/skills/file_skills.py`):

| Tool | Purpose |
|---|---|
| `load_skill(name)` | the instructions **plus a listing of bundled files** |
| `read_skill_file(name, path)` | one bundled file, path relative to the skill root |

The catalogue of available skills is appended to the system prompt. Nothing tells the
agent *which* tool to call or when — it decides, that is the point of a skill.

---

## 2. Installing a skill

**UI:** `/admin/skills` → **Import ZIP**.

**API:**
```bash
curl -b cookies.txt -X POST http://127.0.0.1:5000/api/v1/skills/import \
  -F "file=@pptx.zip" -F "replace=1"
```

Accepted archive shapes (all three are tested):

| Shape | Example | Result |
|---|---|---|
| wrapped | `pptx/SKILL.md` | one package |
| flat | `SKILL.md` at the ZIP root | one package, named from front matter |
| collection | `skills/pdf/SKILL.md`, `skills/pptx/SKILL.md` | one package per `SKILL.md` root |

Importer guarantees:

* Zip-slip, absolute paths, drive letters, `..`, symlinks and reserved names
  (`CON`, `NUL`, …) are rejected.
* Depth is capped at `MAX_PATH_DEPTH` (12) — **shared with the artifact API**, so
  anything that imports is also readable/editable/deletable.
* Unicode names (`références/naïve.md`, CJK) import and read correctly.
* Junk (`__MACOSX/`, `.DS_Store`, `__pycache__`) is skipped; **anything dropped for a
  real reason is reported** in `skipped`, not silently discarded.
* Caps: 96 MB archive, 256 MB uncompressed, 3000 files.
* Extraction goes to a temp dir inside `uploads/maf-skills/` then `rename`s, so a
  failed import never leaves a half-written package.

**Replacing:** without `replace=1` a name clash is reported as a conflict and nothing
is written. With it, the old package is removed first.

---

## 3. One-time environment setup

Skill toolchains are installed **into the repo** (`.agent-node/`, `.agent-python/`) —
gitignored, and the only place the sandbox can write. `run_command` puts them on
`PATH`/`NODE_PATH`/`PYTHONPATH` for every agent command, so agents never need an
absolute path or an install.

```bash
cd /home/vijay/gitrepo/copilot/text2sql

# Node packages a skill may require()  (pptx skill: pptxgenjs + react-icons + sharp)
npm install --prefix .agent-node --cache .npm-cache --no-audit --no-fund \
    pptxgenjs react-icons sharp

# Python packages (the pptx skill's markitdown "read content" path)
~/anaconda3/bin/python3 -m pip install --target .agent-python "markitdown[pptx]"
```

Already present in the app interpreter and used by the pptx skill:
`python-pptx 1.0.2`, `Pillow 12.2.0`, `fontTools 4.25.0`.

### The Workspace MCP server

Agents touch files through the `Workspace` MCP server
(`platform_samples/mcp_servers/workspace.py`, registered by `scripts/feed_samples.py`), tools
`list_dir` / `read_file` / `search_code` / `write_file` / `run_command`.

It runs under the app interpreter and receives `APP_ROOT`, from which it derives and
exports:

| Env | Value | Why |
|---|---|---|
| `PATH` | shims for `python`, `python3` → app interpreter | the agent types `python`, gets the right one |
| `NODE_PATH` | `<APP_ROOT>/.agent-node/node_modules` | `require('pptxgenjs')` just works |
| `PYTHONPATH` | `<APP_ROOT>/.agent-python` | `import markitdown` just works |
| `npm_config_cache` | `<APP_ROOT>/.npm-cache` | the default `~/.npm` is outside the sandbox and `npm install` failed with EROFS |

After changing that file, edit and re-save the server (or just re-run a listing):
stdio servers are launched fresh for every tool listing and every run, so no
restart endpoint exists.

---

## 4. Wiring an agent to skills

Create it through the API (`POST /api/v1/agents`) — the Studio also works, but see
gotcha **G7** below.

```json
{
  "name": "Presentation Designer",
  "kind": "agent",
  "published": true,
  "config": {
    "kind": "agent",
    "runtime": "agent",
    "instructions": "You are a presentation designer. Turn rough briefs into polished decks and leave the finished file in your workspace. …",
    "model": { "client": "9", "name": null },
    "default_options": { "temperature": 0.2, "max_tokens": 8000 },
    "timeout": { "run_timeout": 600, "wall_clock": 1200 },
    "recursion_limit": 60,
    "mcp_bindings": [
      { "server": "Workspace",
        "tools": ["list_dir", "read_file", "write_file", "run_command"],
        "approval": [] }
    ],
    "maf_skill_ids": ["pptx", "text2sql", "implementation", "design-review"]
  }
}
```

Notes:

* `instructions` are **goal-level only.** Do not name tools or skills — earlier drafts
  that said "first call `load_skill('pptx')`" were removed deliberately. Given a real
  goal and several skills, the agent picks the right one itself.
* `maf_skill_ids` lists the skills *visible* to this agent. Give more than one so
  discovery is a genuine choice.
* `approval: []` keeps a run autonomous. With `["write_file","run_command"]` the run
  pauses for HITL and needs a resume call.
* `timeout.wall_clock` is the hard ceiling for the whole run (seconds). `timeout.run_timeout`
  bounds a single model node.

---

## 5. Issues faced, and what fixed them

### I1 — The run looked hung; it wasn't
**Symptom:** a run sat at `running` for 10+ minutes with zero events.
**Investigation:** `faulthandler` stack dump showed the event loop idle in `select()`
waiting on the provider socket. Counting raw stream chunks showed **300–470 chunks per
10 s arriving continuously** — the model was reasoning, and the app forwarded none of it.
**Fix:** emit a `progress` event every 5 s (`phase`, `elapsed`, `chunks`) from the
streaming loop in `runtime/host.py`. The chat now shows "Working for 2m 36s" with live steps.

### I2 — `run_timeout` never fired
LangGraph's `TimeoutPolicy(refresh_on="auto")` restarts the timer on **every emitted
chunk**, so a model that dribbles tokens forever never trips it. A per-node `run_timeout`
is not a guarantee.
**Fix:** added `idle_timeout` (bounds *silence*) and a **wall-clock budget** checked on
every chunk in `host.py` — that one fires even while data flows.

### I3 — The tools idle timeout was shorter than the command timeout *(my bug)*
I first set tools `idle_timeout = 90 s`, but the MCP allows a command to run 180 s, so a
legitimately slow command was killed as if it were a hang.
**Fix:** `_TOOLS_IDLE_FLOOR = 240 s` in `runtime/policies.py`; the tools node can no
longer be configured below the command cap.

### I4 — `run_command` silently corrupted Python code
The server rewrote the command *text*, so `import py` became
`import /home/vijay/anaconda3/bin/python3` and `py = 3` became a syntax error.
**Fix:** stop editing the command. Put `python`/`python3` **shims on `PATH`** instead
(`_interpreter_bin_dir()`). Code is now untouched and the interpreter still resolves.

### I5 — Tool failures were easy to skim past
`run_command` returned `exit=1` plus output; the model often retried the same call.
**Fix:** non-zero exits now return an explicit
`ERROR: command failed with exit code N.` with a "do not repeat this exact command"
line, plus a 180 s kill for runaway commands. Verified over real MCP stdio.

### I6 — Agents burned turns probing the environment
The trace for a deck run was full of `which soffice`, `node -e require(…)`, and finally
`find / -iname "*arlito*"` — a **whole-filesystem scan that took >90 s** and tripped I3.
**Fix:** `runtime/sandbox.py` probes once per process and appends a short
**Sandbox environment** note to every agent's instructions: what's installed, what is
*not* (LibreOffice, `pdftoppm`, ImageMagick), which fonts exist, "don't scan the
filesystem", "don't run `npm install`". After this the same prompt went
`load_skill → list_dir → write_file` with **zero probes and zero failures**.

### I7 — `npm install` failed with EROFS
npm's default cache (`~/.npm`) is outside the workspace. **Fix:** exported
`npm_config_cache=<APP_ROOT>/.npm-cache` in `run_command`.

### I8 — The skill's toolchain wasn't actually installed
The real pptx skill says "`pptxgenjs` is preinstalled". It was not.
**Fix:** installed `pptxgenjs`, `react-icons`, `sharp` into `.agent-node` and
`markitdown` into `.agent-python`; `run_command` wires `NODE_PATH`/`PYTHONPATH` (see §3).

### I9 — No visual rendering of decks
`soffice`, `pdftoppm` and ImageMagick are **not installed and cannot be** (no root; apt
needs it). The skill's thumbnail/PDF visual-QA steps therefore cannot run.
**Mitigation:** the capability note states this up-front so agents substitute geometric
and font-metric checks instead of discovering it the hard way. Install LibreOffice at
the system level if you want the skill's rendering path.

### I10 — `sample-service` was seeded into every conversation
A deliberately-buggy demo service used by the seeded **Developer** agent and the eval
suite — but both `host.py` and the AG-UI bridge copied it into **every** run, including
production chat, and it showed up in the Files panel.
**Fix:** opt-in via `config.workspace.seed`, resolved through an **allow-list**
(`WORKSPACE_SEEDS`) so a config value can never be a path. Only the Developer demo opts
in; `eval/runner.py` still preloads it explicitly. A production workspace is now empty.

### I11 — Chat send was completely broken
`ChatPage.tsx` called `setPlusOpen(...)` which was **never defined**; every send threw a
`ReferenceError` *after* setting the streaming flag, so the UI stuck at "streaming".
Pre-existing — `vite build` does not type-check, so it shipped. **Fix:** removed the dead
calls and fixed the one ref type that failed `tsc`. The frontend now type-checks clean.

### I12 — Runs stuck at `running` forever
Nothing reconciled runs orphaned by a restart; 39 rows were stuck (oldest from Aug 30).
**Fix:** `RunStore.reconcile_orphans()` at startup marks non-terminal runs as
`Interrupted: the application restarted before this run finished.`

### I13 — Host paths leaked to the user
Paths came from four places: the skill block (`Root: /abs/path`), tool output, the
agent's final reply, and **token-by-token streaming where a path was split across
chunks** — the last one defeated per-chunk redaction, and the UI then kept its own
accumulated text instead of the server's clean reply.
**Fix (three parts):** `events.redact_paths()` applied to every emitted item, the error
and the accumulated reply; the skill block shows `@skills/<name>/…` instead of a root;
and the UI now **always adopts the server's reply on `done`**. Verified: 0 occurrences
of `/home/vijay` in a full run transcript.

### I14 — The library held the wrong skill
The agent "ignored" the skill by using python-pptx. It hadn't: the imported package was a
synthetic fixture whose `SKILL.md` says *"Build the deck with python-pptx"*. The real
Anthropic skill says *"Create a new deck → write a `pptxgenjs` script"*.
**Lesson:** an agent follows whatever skill it is given. Validate against the **real**
package. After importing the genuine one the agent used `pptxgenjs` immediately, set
`pres.layout = "LAYOUT_WIDE"`, avoided `#` in hex colours and used a fresh options object
per shape call — all footguns documented in that skill.

### I15 — A bad fixture caused an endless repair loop
The test skill shipped a 112-byte fake `.ttf`. `fontTools` could not parse it, so the
agent kept trying to fix/parse the asset. **Lesson:** fixtures must contain *valid* files
and runnable scripts, or they manufacture failures that look like platform bugs.

### I16 — `recursion_limit` too small for a real skill workflow
The default 25 super-steps is not enough for create → validate → polish. The first real
pptxgenjs run produced a valid deck and *then* errored with "Recursion limit of 25
reached", which reads like a failure after success.
**Fix:** set `recursion_limit: 60` for that agent (it is a per-definition config key).

### I17 — The Files panel showed scaffold and went stale
**Two causes:** (a) everything in the workspace was listed, including seeded fixtures;
(b) the panel refreshed only when the SSE stream closed, but agents keep working after
their visible reply.
**Fix:** a baseline manifest (`.agent-workspace-baseline.json`) written **once at
workspace init** — reads never create it — so only files created/modified by the agent
are listed; plus polling every 8 s while a run is live. Build output
(`node_modules`, `__pycache__`, `dist`, …) is excluded from both the listing and the walk.

### I18 — Running skill scripts littered the package with bytecode
Executing `@skills/pptx/scripts/office/validate.py` made CPython write
`__pycache__/*.pyc` **into the skill package**, so the anthropics `pptx` skill grew from
56 to 65 files. Harmless but wrong: a skill package is content, not a build tree.
**Fix:** `run_command` exports `PYTHONDONTWRITEBYTECODE=1`, and the package file count
now ignores caches so the reported size matches what the UI lists.

---

## 6. Verifying a skill end to end

```bash
cd /home/vijay/gitrepo/copilot/text2sql
B=http://127.0.0.1:5000
C="-b cookies.txt"

# 1. the package is present and complete
curl -s $C $B/api/v1/skills/overview | python3 -m json.tool | head -30

# 2. every artifact is readable (catches the depth/unicode regressions)
curl -s $C "$B/api/v1/skills/pptx/file?path=scripts/office/schemas/ecma/fouth-edition/opc-contentTypes.xsd"

# 3. the runtime exposes the package to the model
curl -s $C $B/api/v1/studio/mcp-servers/1/tools      # expect 5 tools, tools_error: null

# 4. run it and watch the trace with timestamps
curl -s $C -X POST $B/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"agent_id":"presentation-designer","task":"Create a one-page 16:9 slide deck about …"}'
curl -sN $C -H 'Accept: text/event-stream' $B/api/v1/runs/<public_id>/stream
```

A healthy run looks like:

```
load_skill(pptx) → list_dir → write_file(build_deck.js) → run_command(node build_deck.js)
                 → run_command(python @skills/pptx/scripts/office/validate.py …)
```

Red flags in a trace:

| Symptom | Likely cause |
|---|---|
| `find /`, `ls /`, `which soffice` repeatedly | capability note missing/stale (I6) |
| same `run_command` twice with `ERROR:` | failing tool, policy not applied (I5) |
| long silence between events | model reasoning; check `progress` events (I1/I2) |
| `exceeded its idle timeout` on `tools` | command cap vs idle floor (I3) |
| agent used a toolchain the skill didn't ask for | wrong skill installed (I14) |

Then confirm the deliverable itself, e.g.:

```bash
~/anaconda3/bin/python3 - <<'PY'
from pptx import Presentation
p = Presentation("uploads/agent-workspace/<ws>/deck.pptx")
print(len(p.slides), p.slide_width/914400, "x", p.slide_height/914400)
for s in p.slides:
    for sh in s.shapes:
        if sh.has_text_frame and sh.text_frame.text.strip():
            print(" ", sh.text_frame.text.replace("\n"," / ")[:70])
PY
```

---

## 7. Environment matrix

| Component | Status | Notes |
|---|---|---|
| python-pptx, Pillow, fontTools | ✅ in app interpreter | used by the pptx skill |
| `pptxgenjs`, `react-icons`, `sharp` | ✅ `.agent-node` | the skill's "create a deck" path |
| markitdown | ✅ `.agent-python` | the skill's "read content" path |
| node / npm | ✅ v24.5.0 / 11.5.1 | |
| LibreOffice (`soffice`) | ❌ needs root | no PDF/PNG render of office files |
| `pdftoppm` (poppler) | ❌ needs root | no PDF rasterising |
| ImageMagick | ❌ needs root | no image conversion |
| Fonts | dejavu, liberation, ubuntu | Calibri/Cambria/Arial absent |

---

## 8. Operational gotchas

* **G1 — `use_reloader=False`.** Backend changes need a full restart (`./start.sh`);
  frontend changes are served from the Vite manifest on the next page load.
* **G2 — the app binds `:5000`; a second instance cannot start.** Stop the first one.
* **G3 — the Workspace MCP is respawned per run** after editing
  `workspace_mcp_server.py`; module-level changes there are not picked
  up by a live subprocess.
* **G9 — the Workspace server can come up `stopped`** after an app restart, and then
  every `run_command`/`write_file` fails. Check `status` first:
  ```bash
  curl -s -b cookies.txt $B/api/v1/studio/mcp-servers/1/tools   # want tools, no tools_error
  ```
* **G4 — skills are discovered by description.** A vague `description:` in front matter
  is the most common reason an agent does not load a skill it should.
* **G5 — a user package shadows a seeded one of the same name.** Deleting a seeded
  package is refused; delete the user copy to fall back to the seed.
* **G6 — editing a seeded skill forks it** into `uploads/maf-skills/` on first save;
  the repo seed is never modified.
* **G7 — opening an agent in Agent Studio rewrites its config.** The Studio autosaves
  its own defaults (`studio`, `retry`, `maxContextTokens`, `timeout: 120`) and will
  silently discard hand-set values such as a raised `timeout`. Configure via the API, or
  re-check the config after opening the canvas.
* **G8 — token-level redaction is not enough.** Anything path-shaped must be redacted on
  the **assembled** text, and the client must adopt the server's final reply (I13).
