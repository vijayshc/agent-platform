/** Types shared by the vector database search console. */

export type SearchMode = "semantic" | "keyword" | "hybrid";

/** How a result got into the list: a ranking signal, or a plain filter match. */
export type MatchKind = SearchMode | "both" | "filter";

/** Mode reported by the API: the search modes plus filter-only listing. */
export type ResultMode = SearchMode | "filter";

/** Native ChromaDB filter clause, e.g. `{"table": "products"}`. */
export type WhereClause = Record<string, unknown>;

export interface CollectionInfo {
  name: string;
  count: number;
  metadata: Record<string, unknown>;
  /** Stable integer identity used by the generic access API. */
  access_id?: number;
  /** Administrator who claimed the collection. */
  owner_id?: number | null;
  /** Role names currently granted on the collection. */
  roles?: string[];
  /** True when at least one role is granted (otherwise admin-only). */
  restricted?: boolean;
}

export interface CollectionsResponse {
  success: boolean;
  collections: CollectionInfo[];
  total_documents: number;
}

export interface CreateCollectionResponse {
  success: boolean;
  name?: string;
  error?: string;
}

export interface FieldInfo {
  name: string;
  values: string[];
}

export interface FieldsResponse {
  success: boolean;
  fields: FieldInfo[];
  /** How many records were inspected to discover the fields. */
  sampled: number;
}

export interface SearchHit {
  id: string;
  text: string;
  metadata: Record<string, unknown>;
  match: MatchKind;
  similarity: number | null;
  distance: number | null;
  bm25: number | null;
  matched_terms: string[];
}

export interface SearchStats {
  mode: ResultMode;
  returned: number;
  semantic_hits: number;
  keyword_hits: number;
  scanned: number | null;
  total: number | null;
  truncated: boolean;
  took_ms: number;
}

export interface SearchResponse {
  success: boolean;
  query: string;
  results: SearchHit[];
  stats: SearchStats;
}

export const MODE_INFO: Record<SearchMode, { label: string; hint: string }> = {
  semantic: {
    label: "Semantic",
    hint: "Embeds the query and asks the vector database for the closest vectors.",
  },
  keyword: {
    label: "Keyword",
    hint: "BM25 ranking over document text, metadata keys, values and ids.",
  },
  hybrid: {
    label: "Hybrid",
    hint: "Both signals, fused into one ranking.",
  },
};

export const MATCH_LABEL: Record<MatchKind, string> = {
  semantic: "Semantic",
  keyword: "Keyword",
  hybrid: "Hybrid",
  both: "Both",
  filter: "Filtered",
};

export const LIMIT_OPTIONS = [5, 10, 25, 50];
