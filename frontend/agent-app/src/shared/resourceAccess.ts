import { adminGet, adminPutJson } from "../admin/adminShared";

/* ------------------------------------------------------------------ *
 * Generic per-resource role grants.
 *
 * Every tenanted asset type (agent, skill, mcp_server, knowledge_document,
 * llm_connection, hosted_app) is managed through the same endpoint pair, so
 * feature UIs never add their own access HTTP layer:
 *
 *   GET  /api/v1/access/<resourceType>/<id>
 *   PUT  /api/v1/access/<resourceType>/<id>   { role_ids: number[] }
 * ------------------------------------------------------------------ */

export interface RoleOption {
  id: number;
  name: string;
}

export interface AccessEntry {
  role_id: number;
  role_name: string;
}

export interface ResourceAccessResponse {
  access: AccessEntry[];
  roles: RoleOption[];
}

function accessPath(resourceType: string, id: number): string {
  return `/api/v1/access/${encodeURIComponent(resourceType)}/${id}`;
}

export function getResourceAccess(
  resourceType: string,
  id: number,
): Promise<ResourceAccessResponse> {
  return adminGet<ResourceAccessResponse>(accessPath(resourceType, id));
}

export function putResourceAccess(
  resourceType: string,
  id: number,
  roleIds: number[],
): Promise<ResourceAccessResponse> {
  return adminPutJson<ResourceAccessResponse>(accessPath(resourceType, id), {
    role_ids: roleIds,
  });
}
