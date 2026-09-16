/** Studio API client: catalog, definitions, validate, plan, publish, runs, skills.
 *
 * Thin wrappers over `src/api.ts` so the editor never hand-writes a URL. Every
 * call is same-origin with session credentials.
 */
import { apiDelete, apiGet, apiPostJson, apiPostStream, apiPutJson } from "../api";
import type { AgentDef, SkillPackage, StudioResources } from "../types";
import type { CompilePlan, DefinitionBody, ValidateIssue, ValidateReport } from "./model/types";

export function listDefinitions(): Promise<{ agents: AgentDef[] }> {
  return apiGet<{ agents: AgentDef[] }>("/api/v1/agents?include_drafts=1");
}

export function getDefinition(slug: string): Promise<AgentDef> {
  return apiGet<AgentDef>(`/api/v1/agents/${encodeURIComponent(slug)}?full=1`);
}

export function createDefinition(body: DefinitionBody): Promise<AgentDef> {
  return apiPostJson<AgentDef>("/api/v1/agents", body);
}

export function updateDefinition(slug: string, body: DefinitionBody): Promise<AgentDef> {
  return apiPutJson<AgentDef>(`/api/v1/agents/${encodeURIComponent(slug)}`, body);
}

/** The validate route answers with either the rich report
 *  (`{ok, errors:[{code,message}], warnings, compile}`) or the flat compile
 *  result (`{valid, errors:[string], compiled_kind}`) — normalise both so the
 *  Checks panel and the inline node badges read one shape. */
function normalizeReport(raw: unknown): ValidateReport {
  const body = (raw ?? {}) as Record<string, unknown>;
  const issues = (value: unknown): ValidateIssue[] =>
    Array.isArray(value)
      ? value.map((entry) =>
          typeof entry === "string"
            ? { code: "invalid", message: entry }
            : {
                code: String((entry as ValidateIssue)?.code ?? "invalid"),
                message: String((entry as ValidateIssue)?.message ?? ""),
              },
        )
      : [];
  const ok =
    typeof body.ok === "boolean" ? body.ok : typeof body.valid === "boolean" ? body.valid : issues(body.errors).length === 0;
  const compileSource = (body.compile ?? {}) as Record<string, unknown>;
  const compile =
    body.compiled_kind !== undefined || body.agent_count !== undefined
      ? { ...compileSource, ok, kind: body.compiled_kind ?? null, agents: body.agent_count ?? null }
      : Object.keys(compileSource).length
        ? { ...compileSource, ok: typeof compileSource.ok === "boolean" ? compileSource.ok : ok }
        : undefined;
  return { ok, errors: issues(body.errors), warnings: issues(body.warnings), ...(compile ? { compile } : {}) };
}

export async function validateDefinition(slug: string | null, body: DefinitionBody): Promise<ValidateReport> {
  const path = slug ? `/api/v1/agents/${encodeURIComponent(slug)}/validate` : "/api/v1/agents/validate";
  return normalizeReport(await apiPostJson<unknown>(path, body));
}

export function publishDefinition(slug: string, published: boolean): Promise<AgentDef> {
  return apiPostJson<AgentDef>(`/api/v1/agents/${encodeURIComponent(slug)}/publish`, { published });
}

export function compilePlan(body: DefinitionBody): Promise<CompilePlan> {
  return apiPostJson<CompilePlan>("/api/v1/studio/plan", body);
}

export function studioResources(): Promise<StudioResources> {
  return apiGet<StudioResources>("/api/v1/studio/resources");
}

export function mcpServerTools(serverId: number): Promise<StudioResources["mcp_servers"][number]> {
  return apiGet<StudioResources["mcp_servers"][number]>(`/api/v1/studio/mcp-servers/${serverId}/tools`);
}

export interface RunStreamRequest {
  agent_id?: string;
  definition?: { name: string; kind: string; config: Record<string, unknown> };
  input: string;
  stream: true;
  /** Run-level connection; "default" means the deployment default. */
  model?: { client: string };
}

export function startRun(body: RunStreamRequest, signal?: AbortSignal): Promise<Response> {
  return apiPostStream("/api/v1/runs", body, signal);
}

/** Resume a paused run. `decisions` is the HumanInTheLoopMiddleware contract. */
export function resumeRun(
  runId: string,
  decisions: unknown[],
  signal?: AbortSignal,
): Promise<Response> {
  return apiPostStream(`/api/v1/runs/${encodeURIComponent(runId)}/approvals`, {
    decisions,
    stream: true,
  }, signal);
}

export function getRun(runId: string): Promise<{ status?: string; workspace_files?: string[] }> {
  return apiGet<{ status?: string; workspace_files?: string[] }>(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export function cancelRun(runId: string): Promise<{ status?: string }> {
  return apiPostJson<{ status?: string }>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {});
}

/* ------------------------------------------------------------------- skills */

export function listSkills(): Promise<{ skills: SkillPackage[] }> {
  return apiGet<{ skills: SkillPackage[] }>("/api/v1/skills");
}

export function getSkill(name: string): Promise<SkillPackage> {
  return apiGet<SkillPackage>(`/api/v1/skills/${encodeURIComponent(name)}`);
}

export function saveSkill(name: string | null, body: SkillPackage): Promise<SkillPackage> {
  return name
    ? apiPutJson<SkillPackage>(`/api/v1/skills/${encodeURIComponent(name)}`, body)
    : apiPostJson<SkillPackage>("/api/v1/skills", body);
}

export function deleteSkill(name: string): Promise<{ ok?: boolean }> {
  return apiDelete<{ ok?: boolean }>(`/api/v1/skills/${encodeURIComponent(name)}`);
}
