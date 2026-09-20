import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { ToolDataPayload } from "./toolDataTypes";

interface ToolDataState {
  map: Record<string, ToolDataPayload>;
  /** The turn is still streaming: a placeholder without data yet is expected. */
  pending: boolean;
}

const EMPTY: ToolDataState = { map: {}, pending: false };
const ToolDataContext = createContext<ToolDataState>(EMPTY);

/** Makes a turn's cached tool data available to the placeholders in its text. */
export function ToolDataProvider({
  items,
  pending,
  children,
}: {
  items?: ToolDataPayload[];
  pending?: boolean;
  children: ReactNode;
}) {
  const map = useMemo(() => {
    const next: Record<string, ToolDataPayload> = {};
    for (const item of items ?? []) {
      if (item?.call_id) next[item.call_id] = item;
    }
    return next;
  }, [items]);
  const value = useMemo<ToolDataState>(() => ({ map, pending: Boolean(pending) }), [map, pending]);
  return <ToolDataContext.Provider value={value}>{children}</ToolDataContext.Provider>;
}

export function useToolData(callId: string): ToolDataPayload | undefined {
  const { map } = useContext(ToolDataContext);
  const exact = map[callId];
  if (exact) return exact;
  // The reference is `D1`; a model that drops the letter (`1`) still resolves.
  if (/^\d+$/.test(callId)) {
    const byNumber = map[`D${callId}`];
    if (byNumber) return byNumber;
  }
  // A model may append a label to the reference (`D1_line`). Peel trailing
  // `_segment` pieces until a cached result matches.
  let candidate = callId;
  while (candidate.includes("_")) {
    candidate = candidate.slice(0, candidate.lastIndexOf("_"));
    if (!candidate) break;
    const found = map[candidate];
    if (found) return found;
  }
  // Anything else is a reference that resolved to nothing. It stays unresolved
  // so the block reports missing data; guessing "the only cached table" here
  // would silently draw a chart of data the model never referenced.
  return undefined;
}

/** True while the turn is still streaming (data may simply not have arrived). */
export function useToolDataPending(): boolean {
  return useContext(ToolDataContext).pending;
}
