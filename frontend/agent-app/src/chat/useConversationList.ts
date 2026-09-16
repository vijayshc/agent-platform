import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "../api";
import type { Conversation } from "../types";

const CONV_PAGE_SIZE = 30;

export function useConversationList(onError: (message: string | null) => void) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [convSearch, setConvSearch] = useState("");
  const [searchHits, setSearchHits] = useState<Conversation[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [convTotal, setConvTotal] = useState<number | null>(null);
  const [convLoadingMore, setConvLoadingMore] = useState(false);
  const convOffsetRef = useRef(0);
  const convTotalRef = useRef<number | null>(null);
  const convQueryRef = useRef("");
  const searchHitsRef = useRef<Conversation[] | null>(null);
  const wasSearchingRef = useRef(false);
  const searchGen = useRef(0);
  const searchAbort = useRef<AbortController | null>(null);

  const loadConversations = useCallback(
    async (reset: boolean) => {
      const offset = reset ? 0 : convOffsetRef.current;
      const q = convQueryRef.current.trim();
      const qs = q ? `&q=${encodeURIComponent(q)}` : "";
      const c = await apiGet<{ conversations: Conversation[]; total?: number; offset?: number }>(
        `/api/v1/conversations?limit=${CONV_PAGE_SIZE}&offset=${offset}${qs}`,
      );
      const rows = c.conversations || [];
      convOffsetRef.current = offset + rows.length;
      convTotalRef.current = typeof c.total === "number" ? c.total : convTotalRef.current;
      if (q) {
        const combined = reset ? rows : [...(searchHitsRef.current || []), ...rows];
        searchHitsRef.current = combined;
        setSearchHits(combined);
      } else {
        setConversations((prev) => (reset ? rows : [...prev, ...rows]));
        searchHitsRef.current = null;
      }
      setConvTotal(convTotalRef.current);
    },
    [],
  );

  useEffect(() => {
    const q = convSearch.trim();
    convQueryRef.current = q;
    if (!q) {
      searchAbort.current?.abort();
      searchGen.current += 1;
      searchHitsRef.current = null;
      setSearchHits(null);
      setSearchError(null);
      if (wasSearchingRef.current) {
        convOffsetRef.current = 0;
        convTotalRef.current = null;
        loadConversations(true).catch((e) => onError(String(e.message || e)));
      }
      wasSearchingRef.current = false;
      return;
    }
    const gen = ++searchGen.current;
    wasSearchingRef.current = true;
    const ac = new AbortController();
    searchAbort.current?.abort();
    searchAbort.current = ac;
    const handle = window.setTimeout(() => {
      apiGet<{ conversations: Conversation[]; total?: number; offset?: number }>(
        `/api/v1/conversations?q=${encodeURIComponent(q)}&limit=${CONV_PAGE_SIZE}&offset=0`,
        ac.signal,
      )
        .then((res) => {
          if (gen !== searchGen.current) return;
          const rows = res.conversations || [];
          convOffsetRef.current = rows.length;
          convTotalRef.current = typeof res.total === "number" ? res.total : convTotalRef.current;
          searchHitsRef.current = rows;
          setSearchHits(rows);
          setConvTotal(convTotalRef.current);
          setSearchError(null);
        })
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;
          if (gen !== searchGen.current) return;
          searchHitsRef.current = [];
          setSearchHits([]);
          setSearchError(String((e as Error).message || e));
        });
    }, 180);
    return () => window.clearTimeout(handle);
  }, [convSearch, loadConversations, onError]);

  const loadMoreConversations = useCallback(async () => {
    setConvLoadingMore(true);
    try {
      await loadConversations(false);
    } catch (e) {
      if ((e as Error).name !== "AbortError") onError(String((e as Error).message || e));
    } finally {
      setConvLoadingMore(false);
    }
  }, [loadConversations, onError]);

  return {
    conversations,
    setConversations,
    convSearch,
    setConvSearch,
    searchHits,
    searchError,
    convTotal,
    setConvTotal,
    convLoadingMore,
    loadConversations,
    loadMoreConversations,
    convOffsetRef,
    convTotalRef,
    convQueryRef,
  };
}
