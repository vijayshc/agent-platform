/** State container for the vector search console: collections, filter and results. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { vectorApi, type SearchRequest } from "./vectorApi";
import type {
  CollectionInfo,
  FieldInfo,
  SearchHit,
  SearchMode,
  SearchStats,
  WhereClause,
} from "./vectorTypes";

export interface VectorSearchController {
  collections: CollectionInfo[];
  totalDocuments: number;
  loading: boolean;
  loadError: string | null;
  reload: () => void;

  selected: string | null;
  selectedInfo: CollectionInfo | null;
  select: (name: string) => void;

  fields: FieldInfo[];
  fieldsSampled: number;
  fieldsLoading: boolean;
  fieldsError: string | null;

  query: string;
  setQuery: (value: string) => void;
  mode: SearchMode;
  setMode: (value: SearchMode) => void;
  limit: number;
  setLimit: (value: number) => void;
  canSearch: boolean;

  filterText: string;
  setFilterText: (value: string) => void;
  appliedFilter: WhereClause | null;
  appliedFilterText: string;
  filterError: string | null;
  applyFilter: () => void;
  clearFilter: () => void;

  results: SearchHit[] | null;
  stats: SearchStats | null;
  searching: boolean;
  searchError: string | null;
  executedQuery: string;
  isDirty: boolean;
  search: () => void;
  retry: () => void;
}

/** Turn a dropped connection into something a human can act on. */
function describeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return /failed to fetch|networkerror|load failed|err_connection/i.test(message)
    ? "Lost connection to the server. Check that the app is running, then retry."
    : message;
}

export function useVectorSearch(): VectorSearchController {
  const [collections, setCollections] = useState<CollectionInfo[]>([]);
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [selected, setSelected] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldInfo[]>([]);
  const [fieldsSampled, setFieldsSampled] = useState(0);
  const [fieldsLoading, setFieldsLoading] = useState(false);
  const [fieldsError, setFieldsError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("semantic");
  const [limit, setLimit] = useState(10);

  const [filterText, setFilterText] = useState("");
  const [appliedFilterText, setAppliedFilterText] = useState("");
  const [appliedFilter, setAppliedFilter] = useState<WhereClause | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);

  const [results, setResults] = useState<SearchHit[] | null>(null);
  const [stats, setStats] = useState<SearchStats | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [executedQuery, setExecutedQuery] = useState("");

  const requestId = useRef(0);
  const hasSearched = useRef(false);
  const lastRequest = useRef<SearchRequest | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    vectorApi
      .collections()
      .then((data) => {
        if (cancelled) return;
        setCollections(data.collections || []);
        setTotalDocuments(data.total_documents || 0);
        setLoadError(null);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(describeError(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  const select = useCallback((name: string) => {
    setSelected(name);
    window.history.replaceState(null, "", `#${encodeURIComponent(name)}`);
    setResults(null);
    setStats(null);
    setSearchError(null);
    setQuery("");
    setExecutedQuery("");
    setFilterText("");
    setAppliedFilterText("");
    setAppliedFilter(null);
    setFilterError(null);
    hasSearched.current = false;
    lastRequest.current = null;
    requestId.current += 1;

    setFields([]);
    setFieldsSampled(0);
    setFieldsError(null);
    setFieldsLoading(true);
    vectorApi
      .fields(name)
      .then((data) => {
        setFields(data.fields || []);
        setFieldsSampled(data.sampled || 0);
      })
      .catch((error) => setFieldsError(describeError(error)))
      .finally(() => setFieldsLoading(false));
  }, []);

  // Restore the collection in the URL hash once the list is known.
  useEffect(() => {
    if (selected || loading || collections.length === 0) return;
    const fromHash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    const match = collections.find((item) => item.name === fromHash);
    if (match) select(match.name);
  }, [collections, loading, selected, select]);

  const execute = useCallback(
    async (request: SearchRequest) => {
      if (!selected) return;
      const id = requestId.current + 1;
      requestId.current = id;
      lastRequest.current = request;
      setSearching(true);
      setSearchError(null);
      try {
        const data = await vectorApi.search(selected, request);
        if (requestId.current !== id) return;
        setResults(data.results || []);
        setStats(data.stats || null);
        setExecutedQuery(request.query);
        hasSearched.current = true;
      } catch (error) {
        if (requestId.current !== id) return;
        setSearchError(describeError(error));
        setResults([]);
        setStats(null);
        setExecutedQuery(request.query);
        hasSearched.current = true;
      } finally {
        if (requestId.current === id) setSearching(false);
      }
    },
    [selected],
  );

  const search = useCallback(() => {
    const text = query.trim();
    if (!text && !appliedFilter) return;
    void execute({ query: text, mode, limit, where: appliedFilter });
  }, [execute, query, mode, limit, appliedFilter]);

  const retry = useCallback(() => {
    if (lastRequest.current) void execute(lastRequest.current);
  }, [execute]);

  /** Validate the filter editor and search with it; an empty editor clears it. */
  const applyFilter = useCallback(() => {
    const text = filterText.trim();
    if (!text) {
      setAppliedFilter(null);
      setAppliedFilterText("");
      setFilterError(null);
      if (query.trim()) void execute({ query: query.trim(), mode, limit, where: null });
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      setFilterError(error instanceof Error ? error.message : "Invalid JSON");
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setFilterError('The filter must be a JSON object, e.g. {"table": "products"}');
      return;
    }

    setFilterError(null);
    setAppliedFilter(parsed as WhereClause);
    setAppliedFilterText(text);
    // A filter on its own is a valid search: it lists the matching records.
    void execute({ query: query.trim(), mode, limit, where: parsed as WhereClause });
  }, [execute, filterText, query, mode, limit]);

  const clearFilter = useCallback(() => {
    setFilterText("");
    setAppliedFilterText("");
    setAppliedFilter(null);
    setFilterError(null);
    if (query.trim()) void execute({ query: query.trim(), mode, limit, where: null });
  }, [execute, query, mode, limit]);

  // Re-run the last search when the mode or result count changes.
  useEffect(() => {
    if (!hasSearched.current || !lastRequest.current) return;
    void execute({ ...lastRequest.current, mode, limit });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, limit]);

  const selectedInfo = useMemo(
    () => collections.find((item) => item.name === selected) || null,
    [collections, selected],
  );

  return {
    collections,
    totalDocuments,
    loading,
    loadError,
    reload: () => setReloadToken((token) => token + 1),

    selected,
    selectedInfo,
    select,

    fields,
    fieldsSampled,
    fieldsLoading,
    fieldsError,

    query,
    setQuery,
    mode,
    setMode,
    limit,
    setLimit,
    canSearch: query.trim().length > 0 || appliedFilter !== null,

    filterText,
    setFilterText,
    appliedFilter,
    appliedFilterText,
    filterError,
    applyFilter,
    clearFilter,

    results,
    stats,
    searching,
    searchError,
    executedQuery,
    isDirty: query.trim().length > 0 && query.trim() !== executedQuery,
    search,
    retry,
  };
}
