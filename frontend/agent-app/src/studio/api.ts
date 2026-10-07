/** Studio API client: catalog, definitions, validate, plan, publish, skills.
 *
 * Thin wrappers over `src/api.ts` so the editor never hand-writes a URL. Every
 * call is same-origin with session credentials. Runs execute in the real chat
 * page (`/agent?agent=`), never in the Studio.
 */
import { apiDelete, apiGet, apiPostJson, apiPutJson } from "../api";
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

export function listVersions(id: string): Promise<{ versions: import("../types").AgentVersion[] }> {
  return apiGet<{ versions: import("../types").AgentVersion[] }>(`/api/v1/agents/${encodeURIComponent(id)}/versions`);
}

export function getVersion(id: string, ver: number): Promise<AgentDef> {
  return apiGet<AgentDef>(`/api/v1/agents/${encodeURIComponent(id)}/versions/${ver}`);
}

export function rollbackVersion(id: string, version: number): Promise<AgentDef> {
  return apiPostJson<AgentDef>(`/api/v1/agents/${encodeURIComponent(id)}/rollback`, { version });
}

export function cloneDefinition(id: string, body?: { name?: string; slug?: string }): Promise<AgentDef> {
  return apiPostJson<AgentDef>(`/api/v1/agents/${encodeURIComponent(id)}/clone`, body ?? {});
}

export function deleteDefinition(id: string): Promise<{ ok?: boolean }> {
  return apiDelete<{ ok?: boolean }>(`/api/v1/agents/${encodeURIComponent(id)}`);
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
