/** Per-tool data settings: how much of a large tabular tool result reaches the
 *  model, and how much stays available for on-demand charts and tables.
 *
 *  Kept deliberately small so the tool list stays scannable: a single toggle
 *  button per selected tool opens one compact panel, and nothing is rendered
 *  for tools the author has not opted in.
 */
import { useEffect, useRef } from "react";
import { BarChart3 } from "lucide-react";
import type { ToolDataSetting } from "../model/types";
import { NumberInput, Toggle } from "./Fields";

export const DEFAULT_TOOL_DATA: ToolDataSetting = { sample: false, sampleRows: 20, cacheRows: 0 };

/** Mirrors ``MAX_CACHE_ROWS`` in the runtime's tool-data policy: "0 = all" is
 *  really "as many as this cap allows", so the UI must say so. */
const MAX_CACHE_ROWS = 50000;

/** The badge on a selected-tool chip: "20 rows" when sampling is on. */
export function toolDataSummary(setting: ToolDataSetting | undefined): string | undefined {
  if (!setting?.sample) return undefined;
  return `${Math.max(1, setting.sampleRows || 20)} rows`;
}

export function ToolDataButton({
  active,
  open,
  summary,
  onClick,
  testId,
}: {
  active: boolean;
  open: boolean;
  summary?: string;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      className={`as-data-toggle${active ? " is-on" : ""}${open ? " is-open" : ""}`}
      aria-expanded={open}
      aria-label={active ? "Edit chart and table data settings" : "Configure chart and table data settings"}
      title={
        active
          ? `Rows shown to the model: ${summary ?? "sampled"}. Click to edit.`
          : "Send a sample of this tool's rows to the model and keep the full result for charts and tables"
      }
      data-testid={testId}
      onClick={onClick}
    >
      <BarChart3 size={12} />
      {active ? <span className="as-data-toggle-count">{summary ?? "on"}</span> : null}
    </button>
  );
}

export function ToolDataPanel({
  tool,
  setting,
  onChange,
  testId,
}: {
  tool: string;
  setting: ToolDataSetting | undefined;
  /** ``undefined`` clears the setting so the tool passes through untouched. */
  onChange: (next: ToolDataSetting | undefined) => void;
  testId: string;
}) {
  const value = setting ?? DEFAULT_TOOL_DATA;
  const rows = Math.max(1, value.sampleRows || 20);
  const panelRef = useRef<HTMLDivElement>(null);

  // The panel lives inside the tool list's own scroll area; bring it into view
  // so the row it belongs to does not scroll away.
  useEffect(() => {
    panelRef.current?.scrollIntoView({ block: "nearest" });
  }, []);

  return (
    <div className="as-data-panel" ref={panelRef} data-testid={testId}>
      <div className="as-data-panel-title">
        <BarChart3 size={12} />
        <span>Chart &amp; table data</span>
        <code title={tool}>{tool}</code>
      </div>
      <div className="as-data-panel-head">
        <Toggle
          label="Send a sample of the rows to the model"
          checked={value.sample}
          testId={`${testId}-enable`}
          onChange={(on) => onChange({ ...value, sample: on })}
        />
      </div>
      {value.sample ? (
        <div className="as-data-fields">
          <label className="as-data-field">
            <span className="as-data-field-label">Rows sent</span>
            <NumberInput
              value={value.sampleRows}
              min={1}
              max={1000}
              testId={`${testId}-rows`}
              onChange={(next) => onChange({ ...value, sampleRows: next ?? 20 })}
            />
          </label>
          <label className="as-data-field">
            <span className="as-data-field-label">Rows cached</span>
            <NumberInput
              value={value.cacheRows}
              min={0}
              max={MAX_CACHE_ROWS}
              placeholder="0 = max"
              testId={`${testId}-cache`}
              onChange={(next) => onChange({ ...value, cacheRows: next ?? 0 })}
            />
          </label>
        </div>
      ) : null}
      <p className="as-data-hint">
        {value.sample ? (
          <>
            The model reads the first <b>{rows}</b> rows. Up to{" "}
            <b>{MAX_CACHE_ROWS.toLocaleString()}</b> rows stay available for tables and charts without
            running the tool again; a larger result is clipped to that cap.
          </>
        ) : (
          <>Off: this tool&apos;s result goes to the model in full.</>
        )}
      </p>
    </div>
  );
}
