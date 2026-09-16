/** Small shared interaction primitives for the trace explorer. */
import { useCallback, useEffect, useRef, useState } from "react";

import { copyToClipboard } from "./traceUtils";

/** Copy-to-clipboard with a transient "Copied" state. */
export function useCopyState(text: string, resetMs = 1400) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(
    () => () => {
      if (timer.current !== undefined) window.clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(async () => {
    const ok = await copyToClipboard(text);
    if (!ok) return;
    setCopied(true);
    if (timer.current !== undefined) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), resetMs);
  }, [text, resetMs]);

  return { copied, copy };
}

export function CopyButton({
  text,
  label = "Copy",
  copiedLabel = "Copied",
  className = "aa-tx-copy-btn",
  title,
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  className?: string;
  title?: string;
}) {
  const { copied, copy } = useCopyState(text);
  return (
    <button type="button" className={className} onClick={() => void copy()} title={title || label}>
      {copied ? copiedLabel : label}
    </button>
  );
}

/** Compact pill showing a label + truncated value; click copies the value. */
export function CopyChip({ label, value }: { label: string; value: string }) {
  const { copied, copy } = useCopyState(value);
  return (
    <button
      type="button"
      className={`aa-tx-copy-chip${copied ? " copied" : ""}`}
      onClick={() => void copy()}
      title={`${label}: ${value} — click to copy`}
    >
      <span className="aa-tx-copy-chip-label">{label}</span>
      <span className="aa-tx-copy-chip-value">{copied ? "Copied" : value}</span>
    </button>
  );
}
