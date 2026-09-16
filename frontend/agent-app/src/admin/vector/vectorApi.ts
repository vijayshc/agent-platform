/** API client for the vector database search console. */

import { adminGet, adminPostJson } from "../adminShared";
import type {
  CollectionsResponse,
  CreateCollectionResponse,
  FieldsResponse,
  SearchMode,
  SearchResponse,
  WhereClause,
} from "./vectorTypes";

const BASE = "/admin/api/vector-db";

export interface SearchRequest {
  query: string;
  mode: SearchMode;
  limit: number;
  where: WhereClause | null;
}

export const vectorApi = {
  collections: () => adminGet<CollectionsResponse>(`${BASE}/collections`),

  createCollection: (name: string) =>
    adminPostJson<CreateCollectionResponse>(`${BASE}/collections`, { name }),

  fields: (collection: string) =>
    adminGet<FieldsResponse>(`${BASE}/collections/${encodeURIComponent(collection)}/fields`),

  search: (collection: string, request: SearchRequest) =>
    adminPostJson<SearchResponse>(`${BASE}/collections/${encodeURIComponent(collection)}/search`, request),
};
