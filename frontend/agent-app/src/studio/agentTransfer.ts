import type { AgentDef } from "../types";

export const AGENT_EXPORT_FORMAT = "agent-studio-export";
export const AGENT_EXPORT_VERSION = 1;

/** A single agent in an import/export payload. */
export interface AgentTransferItem {
  name: string;
  slug?: string;
  kind: "agent" | "workflow";
  config: Record<string, unknown>;
}

/** A bundle of agents produced by the Agent Studio list page. */
export interface AgentExportBundle {
  format: string;
  version: number;
  exported_at: string;
  agents: AgentTransferItem[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Editor-compatible single-agent payload (also accepted on the list page). */
export function buildAgentFile(agent: AgentDef): AgentTransferItem {
  return {
    name: agent.name,
    slug: agent.slug,
    kind: agent.kind,
    config: (agent.config || {}) as Record<string, unknown>,
  };
}

/** Bundle containing every supplied agent. */
export function buildExportBundle(agents: AgentDef[]): AgentExportBundle {
  return {
    format: AGENT_EXPORT_FORMAT,
    version: AGENT_EXPORT_VERSION,
    exported_at: new Date().toISOString(),
    agents: agents.map(buildAgentFile),
  };
}

export function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * Accepts a bulk bundle (`{ agents: [...] }`), a bare array, or a single agent
 * file (the shape the Studio editor exports) and normalizes it to a list.
 */
export function parseImportFile(raw: string): AgentTransferItem[] {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new Error("File is not valid JSON.");
  }

  let entries: unknown[];
  if (Array.isArray(json)) {
    entries = json;
  } else if (isRecord(json) && Array.isArray(json.agents)) {
    entries = json.agents;
  } else {
    entries = [json];
  }

  const items: AgentTransferItem[] = [];
  for (const entry of entries) {
    if (!isRecord(entry)) continue;
    const config = isRecord(entry.config) ? entry.config : entry;
    const kind = String(entry.kind || config.kind || "agent").toLowerCase() === "workflow"
      ? "workflow"
      : "agent";
    const name = String(entry.name || config.name || "Imported agent").trim() || "Imported agent";
    const slug = entry.slug ? String(entry.slug) : undefined;
    items.push({ name, slug, kind, config: { ...config, kind } });
  }

  if (!items.length) throw new Error("No agents found in the file.");
  return items;
}
