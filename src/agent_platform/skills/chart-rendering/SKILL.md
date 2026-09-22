---
name: chart-rendering
description: How to render a cached tool result as an interactive table or a chart. Loaded automatically into the prompt of any agent with a sampled data tool.
---

## Rendering tool data (tables and charts)
Some tools return a typed table far larger than this conversation. When that
happens you receive only a sample of the rows, followed by a short reference and
the columns it holds:

    [data_ref=D1]
    [tool_data: execute_sql_query — 4,312 rows cached, 20 shown]
    [columns: month (date), region (string), revenue (number)]
    render with #TABLE_D1 or #CHART_D1

The full result is kept aside, so you can show or plot it **without calling the
tool again**. Use the reference exactly as printed — `D1`, not the long tool
call id, and never a label you invent.

A reference belongs to the **conversation**, not to one turn. Data you cached
earlier is still there, so when the user asks for another view of a result you
already have — a different chart type, the same rows as a table, two charts side
by side — reuse its reference instead of running the query again. Re-running it
wastes a tool call and produces a *new* reference that the user did not ask for.
The `[columns: …]` line tells you what that result holds and how each column is
typed; rely on it rather than on memory.

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
  "aggregate": "sum",
  "title": "Revenue by region",
  "xLabel": "Region",
  "yLabel": "Revenue (USD)",
  "valueFormat": "compact"
}
```

Chart spec fields:
- `type` (**required**): `line` | `area` | `bar` | `hbar` | `stackedBar` |
  `stackedArea` | `pie` | `donut` | `scatter`. Use `line`/`area` for time series,
  `bar`/`hbar` for comparisons, `pie`/`donut` for parts of a whole, `scatter`
  for correlation.
- `x` (**required**): the category or time column. For `scatter` it must be a
  numeric column.
- `y` (**required**): one numeric column name, or a list of numeric columns for
  multiple series. Only `integer`, `number` and `decimal` columns are measures; a
  text, date, boolean or `mixed` column is not a measure and is rejected.
- `series`: a column whose values split the rows into separate lines/bars. Not
  allowed on `scatter`.
- `aggregate` (**required** for every chart except `scatter`): `sum` | `avg` |
  `count` | `min` | `max` — how rows that share an `x` value are combined. State
  it explicitly: there is no default, because summing a rate or an average is
  wrong. A `scatter` plots one point per row: it must not aggregate (the runtime
  writes `aggregate: "none"` for it) and must not carry `series`.
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

**There is no implicit default for `type`, `x`, `y` or `aggregate`.** A spec that
omits one of them, names a column the result does not have, plots a non-numeric
column as `y`, or asks a `scatter` to group, is rejected before it reaches the
user, who sees the reason instead of a chart. Use the exact column names from the
`[columns: …]` line, and keep
bar/column charts to about 20 categories or fewer (use `limit`); reserve
line/area/scatter for longer series. When rows share an `x`, they are combined
with `aggregate` and the chart states how many rows were grouped, so choose the
aggregation the measure actually needs (for example `avg` for a rate).
