import { adminDelete, adminGet, adminPostJson } from "../adminShared";

/** One file or directory inside a skill package. */
export interface Artifact {
  path: string;
  type: "file" | "dir";
  size: number;
  language: string | null;
  binary: boolean;
  editable: boolean;
  is_skill_md: boolean;
}

/** A live SKILL.md package on disk (what the agent runtime loads). */
export interface SkillPackage {
  id: number | null;
  name: string;
  kind: "package" | "legacy";
  description: string;
  path: string;
  enabled: boolean;
  has_skill_md: boolean;
  file_count: number;
  dir_count: number;
  size_bytes: number;
  updated_at: string | null;
  writable: boolean;
  seeded: boolean;
  /** Only an owner or admin may rename/delete (and manage role grants). */
  can_manage?: boolean;
}

/** A row from the legacy DB Skill Library, listed read-only for continuity. */
export interface LegacySkill {
  id: number | null;
  name: string;
  skill_id: string;
  description: string;
  category: string;
  status: string;
  version: string;
  updated_at: string | null;
  can_manage?: boolean;
}

export interface SkillsOverview {
  packages: SkillPackage[];
  legacy: LegacySkill[];
  total_files: number;
}

export interface SkillTree {
  name: string;
  description: string;
  path: string;
  enabled: boolean;
  skill_md: string;
  artifacts: Artifact[];
}

export interface ArtifactContent {
  path: string;
  content: string;
  language: string | null;
  size: number;
  binary: boolean;
  editable: boolean;
}

export interface ImportedSkill {
  name: string;
  description: string;
  file_count: number;
  path: string;
}

export interface ImportConflict {
  name: string;
  files: number;
  reason: string;
}

export interface ImportResult {
  success: boolean;
  imported: ImportedSkill[];
  conflicts: ImportConflict[];
  file_count: number;
  roots: string[];
}

export function fetchSkills(signal?: AbortSignal): Promise<SkillsOverview> {
  return adminGet<SkillsOverview>("/api/v1/skills/overview", signal);
}

export function fetchSkillTree(name: string, signal?: AbortSignal): Promise<SkillTree> {
  return adminGet<SkillTree>(`/api/v1/skills/${encodeURIComponent(name)}/tree`, signal);
}

export function fetchArtifact(name: string, path: string, signal?: AbortSignal): Promise<ArtifactContent> {
  return adminGet<ArtifactContent>(`/api/v1/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`, signal);
}

export function saveArtifact(name: string, path: string, content: string): Promise<ArtifactContent> {
  return requestJson<ArtifactContent>(
    `/api/v1/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`,
    "PUT",
    { content },
  );
}

export function createFolder(name: string, path: string): Promise<{ path: string; type: "dir" }> {
  return requestJson<{ path: string; type: "dir" }>(
    `/api/v1/skills/${encodeURIComponent(name)}/folder`,
    "POST",
    { path },
  );
}

export function deleteArtifact(name: string, path: string): Promise<{ success: boolean }> {
  return adminDelete<{ success: boolean }>(
    `/api/v1/skills/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`,
  );
}

export function createSkillPackage(name: string, description: string): Promise<SkillTree> {
  return adminPostJson<SkillTree>(`/api/v1/skills/${encodeURIComponent(name)}/create`, { description });
}

export function deleteSkillPackage(name: string): Promise<{ success?: boolean; error?: string }> {
  return adminDelete<{ success?: boolean; error?: string }>(
    `/api/v1/skills/${encodeURIComponent(name)}/package`,
  );
}

export function importSkillZip(file: File, replace: boolean, name?: string): Promise<ImportResult> {
  const form = new FormData();
  form.append("file", file);
  if (replace) form.append("replace", "1");
  if (name) form.append("name", name);
  const token = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.getAttribute("content");
  return requestJson<ImportResult>("/api/v1/skills/import", "POST", form, token ? { "X-CSRF-Token": token } : undefined);
}

export function exportSkillUrl(name: string): string {
  return `/api/v1/skills/${encodeURIComponent(name)}/export`;
}


async function requestJson<T>(path: string, method: string, body: unknown, headers?: Record<string, string>): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...(headers || {}) },
    body: isForm ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const payload = await res.json();
      message = payload.error || payload.message || message;
    } catch {
      /* keep statusText */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

/** Group a flat artifact list into renderable rows with depth for indentation. */
export interface TreeRow extends Artifact {
  name: string;
  depth: number;
}

export function flattenTree(artifacts: Artifact[], expanded: Set<string>): TreeRow[] {
  const dirs = new Set(artifacts.filter((a) => a.type === "dir").map((a) => a.path));
  const rows: TreeRow[] = [];
  const sorted = artifacts.slice().sort((a, b) => {
    const aTop = a.path.split("/")[0];
    const bTop = b.path.split("/")[0];
    if (aTop !== bTop) return aTop.localeCompare(bTop);
    return a.path.localeCompare(b.path);
  });
  for (const item of sorted) {
    const segments = item.path.split("/");
    const parent = segments.slice(0, -1).join("/");
    if (parent && !isVisible(parent, dirs, expanded)) continue;
    rows.push({ ...item, name: segments[segments.length - 1], depth: segments.length - 1 });
  }
  return rows;
}

function isVisible(parent: string, dirs: Set<string>, expanded: Set<string>): boolean {
  const segments = parent.split("/");
  for (let i = 1; i <= segments.length; i += 1) {
    const prefix = segments.slice(0, i).join("/");
    if (dirs.has(prefix) && !expanded.has(prefix)) return false;
  }
  return true;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}
