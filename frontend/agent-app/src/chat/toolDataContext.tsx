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
  // Exactly the reference the marker printed, or nothing. A near-miss (`1`, or
  // `D1_line`) is a reference the model wrote wrong; resolving it to whatever
  // looked closest is how a chart ends up showing data nobody asked for.
  return map[callId];
}

/** True while the turn is still streaming (data may simply not have arrived). */
export function useToolDataPending(): boolean {
  return useContext(ToolDataContext).pending;
}
