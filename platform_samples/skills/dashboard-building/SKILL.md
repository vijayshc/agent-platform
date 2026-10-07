---
name: dashboard-building
description: How to compose a full dashboard answer from cached tool results — KPI cards, charts, leaderboards, progress bars, textboxes and tables in one reply.
---

## Building a dashboard reply

A dashboard is one answer that combines several blocks from the **same cached
result reference** (`D1`). Never re-query for another view of data the
conversation already holds — reuse its reference.

### Composition order (top to bottom)

1. `#NOTE` with `style: "title"` — the dashboard section header.
2. KPI row — 3–4 `card` blocks with `"layout": "quarter"` (total, average, count).
3. Trend/comparison row — 1–2 `chart` blocks with `"layout": "half"`.
4. Breakdown row — a `list` and/or `progress` block with `"layout": "half"`.
5. `#NOTE` with `style: "insight"` — the one-sentence takeaway (numbers must
   come from the rendered blocks, never from the clipped sample).
6. Full `table` only when the user needs the raw rows.

### Example dashboard (all blocks share D1)

```note
#NOTE
{"style": "title", "title": "Revenue performance", "body": "By region and month."}
```

```card
#CARD_D1
{"metric": "revenue", "aggregate": "sum", "title": "Total revenue", "valueFormat": "compact", "layout": "quarter", "icon": "trending-up", "spark": {"x": "month", "type": "bar"}}
```

```card
#CARD_D1
{"metric": "revenue", "aggregate": "avg", "title": "Avg order value", "valueFormat": "currency", "layout": "quarter", "deltaMetric": "profit", "deltaAggregate": "sum"}
```

```card
#CARD_D1
{"metric": "id", "aggregate": "count", "title": "Orders", "valueFormat": "number", "layout": "quarter"}
```

```card
#CARD_D1
{"metric": "revenue", "aggregate": "max", "title": "Largest order", "valueFormat": "currency", "layout": "quarter"}
```

```chart
#CHART_D1
{"type": "area", "x": "month", "y": ["revenue"], "aggregate": "sum", "title": "Revenue trend", "layout": "half"}
```

```chart
#CHART_D1
{"type": "bar", "x": "region", "y": ["revenue"], "aggregate": "sum", "title": "Revenue by region", "layout": "half"}
```

```list
#LIST_D1
{"label": "region", "value": "revenue", "aggregate": "sum", "limit": 5, "title": "Top regions", "layout": "half"}
```

```progress
#PROGRESS_D1
{"metric": "revenue", "aggregate": "sum", "target": 1000000, "title": "Revenue vs quota", "layout": "half"}
```

```note
#NOTE
{"style": "insight", "title": "Takeaway", "body": "One sentence on what changed and why it matters."}
```

### Rules

- Every `card`/`list`/`progress`/`chart` names an exact column from the
  `[columns: …]` line. `metric`/`value` must be numeric unless the aggregate is
  `count`. A rejected block shows the user the reason, so get columns right.
- `target` on a progress block is a literal number you choose (quota, budget,
  goal) — it is the only literal number a data block ever carries.
- When you render a block, do not also paste the raw rows as a markdown table
  — the block is the result.
- Keep bar charts to ~20 categories (`limit`); use `line`/`area` for time.
- Give each KPI card an `icon` that matches its meaning — choose from the
  allowlisted names (`trending-up`, `wallet`, `users`, `shopping-cart`,
  `package`, `target`, `dollar-sign`, `hash`, `percent`, `award`, `activity`,
  `clock`, `calendar`, `bar-chart-3`, `line-chart`, `piggy-bank`, `truck`,
  `star`, `zap`, `globe`, `briefcase`, `circle-check`, `triangle-alert`,
  `arrow-up-right`, `arrow-down-right`, `user`, `trending-down`). An off-list
  name is rejected, so never invent one.
- Give each card/list/progress block a `color` (hex, e.g. `#6366f1`) chosen to
  suit its meaning — green for profit/success, rose for cost/loss, amber for
  warnings, indigo/cyan for neutral metrics. A row of differently-colored KPIs
  reads far better than one repeated accent.
- Layouts: `quarter` = 4 per row (KPIs), `third` = 3 per row, `half` = 2 per
  row, `full` = alone. Consecutive narrow blocks share a row automatically.
- Close every code fence with its own ``` line before the next block.
- Never narrate the mechanics. Do not mention fences, chart types, specs,
  `aggregate`, column names, data references (`D1`), caching, or why a field is
  required. Do not mention SQL mechanics: storage/column types, casting or type
  conversion, joins, filters, the source table, or how the query was written.
  Every sentence of prose must be about the business data — what it shows, the
  trend, the standout numbers — not about how it is fetched, stored or rendered.
