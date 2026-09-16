/** Compact stat strip for a run's Phoenix trace. */
import type { RunRow, RunTrace } from "../types";
import { formatDur, latency } from "./runUtils";
import { formatCost, formatTokens, traceSpanIsError } from "./traceUtils";

function durationText(ms?: number | null): string {
  if (ms == null || !Number.isFinite(Number(ms))) return "";
  const value = Number(ms);
  if (value <= 0) return "0ms";
  return formatDur(value);
}

function Stat({
  label,
  value,
  sub,
  danger,
  title,
}: {
  label: string;
  value: string;
  sub?: string;
  danger?: boolean;
  title?: string;
}) {
  return (
    <div className={`aa-tx-stat${danger ? " danger" : ""}`} title={title}>
      <div className="aa-tx-stat-label">{label}</div>
      <div className="aa-tx-stat-value">{value}</div>
      {sub ? <div className="aa-tx-stat-sub">{sub}</div> : null}
    </div>
  );
}

export function RunSummaryCards({ trace, run }: { trace: RunTrace; run: RunRow }) {
  const summary = trace.summary;
  const spans = summary?.span_count ?? trace.spans.length;
  const errors =
    summary?.error_count ?? trace.spans.filter(traceSpanIsError).length;
  const durationMs = summary?.duration_ms ?? run.duration_ms ?? null;
  const duration = durationText(durationMs) || latency(run) || "—";
  const promptTokens = Number(summary?.prompt_tokens || 0);
  const completionTokens = Number(summary?.completion_tokens || 0);
  const totalTokens = Number(summary?.total_tokens || promptTokens + completionTokens);
  const toolCalls = summary?.tool_calls ?? 0;
  const llmCalls = summary?.llm_calls ?? 0;
  const models = summary?.models || [];
  const slowestMs = Number(summary?.slowest_span_ms);
  const durationMsNum = Number(durationMs);
  // "slowest 72.0s" under "72.0s" just restates the value: only surface the
  // slowest span when it is genuinely a different, interesting number.
  const slowest =
    Number.isFinite(slowestMs) &&
    slowestMs > 0 &&
    (!Number.isFinite(durationMsNum) || Math.abs(slowestMs - durationMsNum) > Math.max(1, durationMsNum * 0.01))
      ? durationText(slowestMs)
      : "";
  // Token pricing is optional: a zero/unknown cost is noise, not information.
  const costValue = Number(summary?.cost);
  const showCost = Number.isFinite(costValue) && costValue > 0;
  const cost = showCost ? formatCost(costValue) : "";

  return (
    <div className="aa-tx-stats" data-testid="trace-summary-cards">
      <Stat label="Spans" value={String(spans)} sub={summary?.kinds ? `${Object.keys(summary.kinds).length} kinds` : undefined} />
      <Stat
        label="Errors"
        value={String(errors)}
        sub={errors > 0 ? "needs attention" : "no failures"}
        danger={errors > 0}
      />
      <Stat
        label="Trace time"
        value={duration}
        sub={slowest ? `slowest span ${slowest}` : durationMsNum && run.duration_ms ? "spans window" : undefined}
        title="Time from the first span's start to the last span's end (the header shows wall-clock run time)"
      />
      <Stat
        label="Tokens"
        value={formatTokens(totalTokens) || "0"}
        sub={`${formatTokens(promptTokens) || 0} in · ${formatTokens(completionTokens) || 0} out`}
        title={`${promptTokens} prompt / ${completionTokens} completion / ${totalTokens} total`}
      />
      <Stat label="LLM calls" value={String(llmCalls)} />
      <Stat label="Tool calls" value={String(toolCalls)} />
      <Stat
        label="Models"
        value={models.length ? models.join(", ") : "—"}
        sub={models.length > 1 ? `${models.length} models` : undefined}
        title={models.join(", ")}
      />
      {cost ? <Stat label="Cost" value={cost} /> : null}
    </div>
  );
}
