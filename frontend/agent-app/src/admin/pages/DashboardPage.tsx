import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AdminError, AdminLoading, PageHeader } from "../adminShared";
import { chartAxisProps, chartGridProps, compact, shortDay, useChartPalette, type ChartPalette } from "../chartTheme";
import { fetchDashboardAnalytics, type DashboardAnalytics } from "./dashboardApi";

interface BreakdownItem {
  name: string;
  value: number;
}

const STATUS_COLOR: Record<string, string> = {
  success: "#10b981",
  error: "#f43f5e",
  running: "#6366f1",
  awaiting_approval: "#f59e0b",
  cancelled: "#94a3b8",
};

/* ------------------------------------------------------------------ *
 * KPI Stat Card
 * ------------------------------------------------------------------ */
function StatCard({
  label,
  value,
  icon,
  tone,
  hint,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  tone: string;
  hint?: string;
}) {
  return (
    <div className="aa-kpi-card">
      <div className="aa-kpi-icon" data-tone={tone}>
        {icon}
      </div>
      <div className="aa-kpi-body">
        <div className="aa-kpi-label">{label}</div>
        <div className="aa-kpi-value">{value.toLocaleString()}</div>
        {hint && <div className="aa-kpi-hint">{hint}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Panel wrapper
 * ------------------------------------------------------------------ */
function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
  style,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div className={`aa-dash-panel ${className}`} style={style}>
      <div className="aa-dash-panel-head">
        <div className="aa-dash-panel-title">
          <h2>{title}</h2>
          {subtitle && <span className="aa-dash-panel-sub">{subtitle}</span>}
        </div>
        {action && <div className="aa-dash-panel-action">{action}</div>}
      </div>
      <div className="aa-dash-panel-body">{children}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Chart tooltip
 * ------------------------------------------------------------------ */
function ChartTooltip({
  active,
  payload,
  label,
  p,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color?: string }>;
  label?: string;
  p: ChartPalette;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="aa-chart-tip" style={{ background: p.tooltipBg, border: `1px solid ${p.tooltipBorder}` }}>
      <div className="aa-chart-tip-date">{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="aa-chart-tip-row">
          <span style={{ width: 8, height: 8, borderRadius: 2, background: entry.color }} />
          <span className="aa-chart-tip-name">{entry.name}</span>
          <span className="aa-chart-tip-val">{compact(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Donut chart (compact, centered)
 * ------------------------------------------------------------------ */
function DonutChart({ data, p, height = 220 }: { data: Array<BreakdownItem & { color?: string }>; p: ChartPalette; height?: number }) {
  const palette = p.series;
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const colorFor = (d: BreakdownItem & { color?: string }, i: number) => d.color || palette[i % palette.length];
  return (
    <div className="aa-donut-wrap" style={{ height }}>
      <div className="aa-donut-chart" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius="62%"
              outerRadius="92%"
              paddingAngle={2}
              stroke={p.card}
              strokeWidth={2}
            >
              {data.map((d, i) => (
                <Cell key={i} fill={colorFor(d, i)} />
              ))}
            </Pie>
            <Tooltip content={<ChartTooltip p={p} />} />
          </PieChart>
        </ResponsiveContainer>
        <div className="aa-donut-total">
          <div className="aa-donut-total-value">{total.toLocaleString()}</div>
          <div className="aa-donut-total-label">total</div>
        </div>
      </div>
      <div className="aa-donut-legend">
        {total === 0 && <div className="aa-dash-empty">No data yet</div>}
        {data.map((d, i) => (
          <div key={d.name} className="aa-donut-legend-item">
            <span className="aa-donut-legend-dot" style={{ background: colorFor(d, i) }} />
            <span className="aa-donut-legend-name">{d.name}</span>
            <span className="aa-donut-legend-val">{d.value.toLocaleString()}</span>
            <span className="aa-donut-legend-pct">
              {total > 0 ? Math.round((d.value / total) * 100) : 0}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Horizontal bar chart (Recharts, with value labels)
 * ------------------------------------------------------------------ */
function HBarChart({
  data,
  p,
  height = 230,
  colorIndex = 0,
  unit = "",
}: {
  data: BreakdownItem[];
  p: ChartPalette;
  height?: number;
  colorIndex?: number;
  unit?: string;
}) {
  if (!data || data.length === 0) {
    return <div className="aa-dash-empty">No data yet</div>;
  }
  const rule = chartGridProps(p);
  const tickCfg = chartAxisProps(p);
  const fill = p.series[colorIndex % p.series.length];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 4, right: 24, top: 4, bottom: 4 }}>
        <CartesianGrid {...rule} />
        <XAxis type="number" {...tickCfg} tickFormatter={compact} />
        <YAxis type="category" dataKey="name" {...tickCfg} width={130} tick={{ fill: p.text, fontSize: 11 }} />
        <Tooltip content={<ChartTooltip p={p} />} cursor={{ fill: p.grid, opacity: 0.3 }} />
        <Bar dataKey="value" name="Count" fill={fill} radius={[4, 4, 4, 4]} barSize={16} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ================================================================== *
 * Dashboard page
 * ================================================================== */
export function DashboardPage() {
  const [analytics, setAnalytics] = useState<DashboardAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const p = useChartPalette();

  useEffect(() => {
    fetchDashboardAnalytics(14)
      .then((a) => {
        setAnalytics(a);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const m = analytics?.metrics;
  const s = analytics?.series;
  const b = analytics?.breakdowns;

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  const axis = chartAxisProps(p);
  const grid = chartGridProps(p);

  // Runs-by-status donut uses explicit status colors.
  const runsByStatus = (b?.runsByStatus ?? []).map((item) => ({
    ...item,
    color: item.name in STATUS_COLOR ? STATUS_COLOR[item.name] : undefined,
  }));

  return (
    <div className="aa-admin-page">
      <PageHeader title="Dashboard" subtitle={`Overview of the past ${analytics?.days ?? 14} days`} />

      {/* ===================== KPI row ===================== */}
      <div className="aa-kpi-grid">
        <StatCard label="Users" value={m?.users ?? 0} tone="blue" hint={`${m?.roles ?? 0} roles`} icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>} />
        <StatCard label="Agents" value={m?.agents ?? 0} tone="green" hint={`${m?.publishedAgents ?? 0} published`} icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>} />
        <StatCard label="Interactions" value={m?.interactions ?? 0} tone="orange" hint={`${m?.interactionsToday ?? 0} today`} icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>} />
        <StatCard label="Hosted Apps" value={m?.hostedApps ?? 0} tone="purple" hint={`${m?.hostedAppsRunning ?? 0} running`} icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg>} />
        <StatCard label="Skills" value={m?.skills ?? 0} tone="yellow" icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>} />
        <StatCard label="MCP Servers" value={m?.mcpServers ?? 0} tone="cyan" hint={`${m?.mcpServersStdio ?? 0} stdio · ${(m?.mcpServers ?? 0) - (m?.mcpServersStdio ?? 0)} http`} icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>} />
      </div>

      {/* ============ Hero: Interactions trend + Runs by Status ============ */}
      <div className="aa-layout-grid">
        <Panel title="Interactions" subtitle="Messages per day" className="col-span-8">
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={s?.messages ?? []}>
              <defs>
                <linearGradient id="dashInteractions" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={p.primary} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={p.primary} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid {...grid} />
              <XAxis dataKey="date" {...axis} tickFormatter={shortDay} />
              <YAxis {...axis} tickFormatter={compact} width={40} />
              <Tooltip content={<ChartTooltip p={p} />} />
              <Area type="monotone" dataKey="count" name="Messages" stroke={p.primary} strokeWidth={2.5} fill="url(#dashInteractions)" />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Runs by Status" subtitle="Distribution" className="col-span-4">
          <DonutChart data={runsByStatus} p={p} height={280} />
        </Panel>
      </div>

      {/* ============ Activity: Runs, Top Agents, Messages by Role ============ */}
      <div className="aa-layout-grid">
        <Panel title="Runs" subtitle="Agent executions per day" className="col-span-4">
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={s?.runs ?? []}>
              <CartesianGrid {...grid} />
              <XAxis dataKey="date" {...axis} tickFormatter={shortDay} />
              <YAxis {...axis} tickFormatter={compact} width={40} />
              <Tooltip content={<ChartTooltip p={p} />} />
              <Bar dataKey="count" name="Runs" fill={p.series[1]} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Top Agents" subtitle="By run count" className="col-span-4">
          <HBarChart data={b?.runsByAgent ?? []} p={p} colorIndex={0} />
        </Panel>

        <Panel title="Messages by Role" subtitle="User vs assistant" className="col-span-4">
          <DonutChart data={b?.messagesByRole ?? []} p={p} height={230} />
        </Panel>
      </div>
    </div>
  );
}
