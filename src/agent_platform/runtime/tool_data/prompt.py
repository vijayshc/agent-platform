"""The system-prompt contract for rendering cached tool data.

The text lives in a **skill package with a fixed name** (``chart-rendering``) so
it can be edited at runtime — Skill Library → save → the next run picks it up,
no restart — while still shipping a sensible built-in default. It is *not*
attached per agent: the platform looks the package up by name whenever an agent
has a data tool, so one edit changes every agent.

Because it is a package, it is listed in the Skill Library and can be assigned
like any other; that is intentional, and
``tests/test_skills_tenancy.py`` accounts for it when it asserts on the visible
package set.

Only agents that opted a tool into sampling receive this block, so an agent with
no data tools keeps its original prompt untouched.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.agent_platform.runtime.tool_data.policy import ToolDataPolicy

logger = logging.getLogger("text2sql.agent_platform")

#: The fixed skill-package name that overrides the built-in protocol text.
PROMPT_SKILL_NAME = "chart-rendering"

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)

_SKILL_DESCRIPTION = (
    "How to render a cached tool result as an interactive table or a chart. "
    "Loaded automatically into the prompt of any agent with a sampled data tool."
)

#: The built-in protocol. A ``chart-rendering`` skill package with the same body
#: replaces it at runtime; this text is the fallback and the seed.
DEFAULT_PROTOCOL = """## Rendering tool data (tables and charts)
Some tools return tabular data (a markdown table) far larger than this
conversation. When that happens you receive only a sample of the rows, followed
by a short reference:

    [data_ref=D1]
    [tool_data: execute_sql_query — 4,312 rows cached, 20 shown; render with #TABLE_D1 or #CHART_D1]

The full result is kept aside, so you can show or plot it **without calling the
tool again**. Use the reference exactly as printed — `D1`, not the long tool
call id, and never a label you invent.

A reference belongs to the **conversation**, not to one turn. Data you cached
earlier is still there, so when the user asks for another view of a result you
already have — a different chart type, the same rows as a table, two charts side
by side — reuse its reference instead of running the query again. Re-running it
wastes a tool call and produces a *new* reference that the user did not ask for.

Rules:
- **Show, don't repeat.** When you render a result with `#TABLE_D1` or a chart,
  do NOT also paste the raw markdown table. The tag *is* how the user sees the
  rows. Prose should summarise; the tag carries the data.
- Use the reference exactly as printed. Never append a suffix
  (`#CHART_D1_line` is wrong). To draw two charts from the same result, reuse
  `D1` in two blocks.
- **Reuse before re-querying.** If the conversation already holds the rows the
  user is asking about, render them from their reference. Only call a tool when
  you need data the conversation does not have yet.
- **Chart proactively.** When a result is chart-suitable — a group-by or
  aggregation, a time series, a category comparison, or a distribution — render
  a chart without being asked. You do not need the user to request one.
- You may render several charts, or plot several measures in one chart by
  listing multiple `y` columns. Add charts only when they show a distinct view.
- A sample omits rows. Never state totals, group counts, or per-row facts that
  depend on the omitted rows; the rendered table/chart is the source of truth.
- Put each `#TABLE_…` / `#CHART_…` block on its own line, separated from prose
  by blank lines.

### A. Full interactive table
Emit the placeholder alone on a line, or with an optional fenced spec:

    #TABLE_D1

```table
#TABLE_D1
{"title": "Revenue by region", "pageLength": 25}
```

### B. Chart
Emit a fenced `chart` block whose **first line** is the placeholder and whose
body is a JSON spec. Do not also write the placeholder as a separate bare line
before the fence — the line inside the fence is the placeholder:

```chart
#CHART_D1
{
  "type": "bar",
  "x": "region",
  "y": ["revenue"],
  "title": "Revenue by region",
  "xLabel": "Region",
  "yLabel": "Revenue (USD)",
  "valueFormat": "compact"
}
```

Chart spec fields:
- `type`: `line` | `area` | `bar` | `hbar` | `stackedBar` | `stackedArea` |
  `pie` | `donut` | `scatter`. Use `line`/`area` for time series, `bar`/`hbar`
  for comparisons, `pie`/`donut` for parts of a whole, `scatter` for correlation.
- `x`: the category or time column (required unless `pie`/`donut`, where it is
  the slice label).
- `y`: one column name, or a list of numeric columns for multiple series.
  Omit to plot every numeric column.
- `series`: a column whose values split the rows into separate lines/bars.
- `aggregate`: `sum` | `avg` | `count` | `min` | `max` — combines rows that share
  an `x` value (default `sum`).
- `sort`: `asc` | `desc` | `x` | `none`; `limit`: keep only the top N categories.
- `title`, `subtitle`, `xLabel`, `yLabel`: labels shown on the card.
- `layout`: `full` (default) or `half`. Two `half` charts that sit next to each
  other render side by side; at most two fit in a row.
- `valueFormat`: `number` | `compact` | `percent` | `currency`; `currency`: ISO
  code (default `USD`).
- `colorBy`: `category` (a color per bar/slice), `series` (a color per series),
  or `single`. Defaults sensibly per chart type.
- `height`: pixel height (default 300).
- `colors`: optional list of hex colors.
- `smooth`: `true` for smoothed line/area curves.
- `showLegend` / `showGrid`: `true`/`false`.

Guidance: keep bar/column charts to about 20 categories or fewer (use `limit`),
and reserve line/area/scatter for longer series. Column names must match the
table header exactly (matching is case-insensitive)."""


def _read_prompt(path: Path) -> str | None:
    """The body of a SKILL.md, frontmatter stripped."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER.match(content)
    body = (match.group(2) if match else content).strip()
    return body or None


def _skill_body() -> str | None:
    """The body of the fixed-name skill package, or ``None`` when absent.

    Read fresh on every compile, so an edit in the Skill Library takes effect on
    the next run without a restart.
    """
    try:
        from src.agent_platform.catalog.skills_store import MafSkillStore

        row = MafSkillStore.get_by_name(PROMPT_SKILL_NAME)
        if not row or not row.get("enabled", 1) or not row.get("path"):
            return None
        return _read_prompt(Path(str(row["path"])) / "SKILL.md")
    except Exception:
        logger.debug("chart-rendering skill unavailable; using built-in prompt", exc_info=True)
        return None


def load_protocol_instructions() -> str:
    """The protocol block: the editable skill body, else the built-in default."""
    return _skill_body() or DEFAULT_PROTOCOL


def tool_data_protocol_note(policy: ToolDataPolicy) -> str:
    """The protocol block for ``policy``, or an empty string when inactive."""
    if not policy.active:
        return ""
    return load_protocol_instructions()


def ensure_prompt_skill() -> str | None:
    """Seed the fixed-name skill package if it does not exist yet.

    Idempotent: an existing package (seeded or admin-edited) is never touched,
    so runtime edits are preserved. Returns the package path, or ``None`` when
    seeding was not possible.
    """
    try:
        from src.agent_platform.catalog.skills_store import MafSkillStore
        from src.agent_platform.paths import SKILLS_DIR

        existing = MafSkillStore.get_by_name(PROMPT_SKILL_NAME)
        root = Path(existing["path"]) if existing and existing.get("path") else SKILLS_DIR / PROMPT_SKILL_NAME
        skill_md = root / "SKILL.md"
        if not skill_md.exists():
            root.mkdir(parents=True, exist_ok=True)
            skill_md.write_text(
                "---\n"
                f"name: {PROMPT_SKILL_NAME}\n"
                f"description: {_SKILL_DESCRIPTION}\n"
                "---\n\n"
                f"{DEFAULT_PROTOCOL}\n",
                encoding="utf-8",
            )
        if not existing:
            MafSkillStore.upsert(
                PROMPT_SKILL_NAME,
                _SKILL_DESCRIPTION,
                str(root),
                enabled=True,
                created_by=None,
            )
        return str(root)
    except Exception:
        logger.warning("could not seed the chart-rendering prompt skill", exc_info=True)
        return None
