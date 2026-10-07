import { useEffect, type Dispatch, type SetStateAction } from "react";
import { apiGet } from "../api";
import type { AgentDef, Conversation } from "../types";
import type { MeInfo } from "./SettingsPanel";

export function chatAgentKey(username?: string | null): string {
  return `aa_chat_agent_${username || "anon"}`;
}

export function readStoredAgentSlug(username?: string | null): string | null {
  try {
    const v = localStorage.getItem(chatAgentKey(username));
    return v ? v : null;
  } catch {
    return null;
  }
}

export function storeAgentSlug(slug: string, username?: string | null) {
  try {
    localStorage.setItem(chatAgentKey(username), slug);
  } catch {}
}

interface BootstrapOpts {
  agents: AgentDef[];
  conv: Conversation | null;
  agent: AgentDef | null;
  setAgents: Dispatch<SetStateAction<AgentDef[]>>;
  setAgent: Dispatch<SetStateAction<AgentDef | null>>;
  setMe: Dispatch<SetStateAction<MeInfo | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  loadConversations: (reset: boolean) => Promise<void>;
  meRef: { current: MeInfo | null };
  rememberAgent: (slug: string) => void;
}

/**
 * Initial agent bootstrap: explicit ?agent= param always wins; otherwise the
 * last-used agent slug for this user (aa_chat_agent_<username|anon>) is
 * restored. Slugs missing from the published list go through the same
 * draft-preview fallback (GET /agents/:slug?full=1) the picker never lists;
 * unresolvable slugs leave the picker unselected with the standard error.
 * A second effect keeps conversation opens on the same path.
 */
export function useAgentBootstrap({
  agents,
  conv,
  agent,
  setAgents,
  setAgent,
  setMe,
  setError,
  loadConversations,
  meRef,
  rememberAgent,
}: BootstrapOpts) {
  useEffect(() => {
    loadConversations(true).catch((e) => setError(String(e.message || e)));
    const wanted = new URLSearchParams(window.location.search).get("agent");
    let cancelled = false;
    (async () => {
      let meInfo: MeInfo | null = null;
      try {
        meInfo = await apiGet<MeInfo>("/api/v1/me").catch(() => null);
      } catch {
        meInfo = null;
      }
      if (cancelled) return;
      meRef.current = meInfo;
      setMe(meInfo);
      let list: AgentDef[] = [];
      try {
        const a = await apiGet<{ agents: AgentDef[] }>("/api/v1/agents");
        list = a.agents || [];
      } catch (e) {
        if (!cancelled) setError(String((e as Error).message || e));
        return;
      }
      if (cancelled) return;
      setAgents(list);
      // Explicit ?agent= always wins over the remembered last-used agent.
      if (wanted) {
        const found = list.find((row) => row.slug === wanted);
        if (found) {
          setAgent(found);
          storeAgentSlug(found.slug, meInfo?.username);
        } else {
          // Test-before-publish: a saved-but-unpublished draft is not in the
          // published picker list. Resolve its full shape (Studio read +
          // agent access) so the chat can run it; strangers get the same
          // "no longer available" path as a removed agent.
          try {
            const draft = await apiGet<AgentDef>(
              `/api/v1/agents/${encodeURIComponent(wanted)}?full=1`,
            );
            if (cancelled) return;
            setAgent(draft);
            setAgents((prev) =>
              prev.some((row) => row.slug === draft.slug) ? prev : [...prev, draft],
            );
            storeAgentSlug(draft.slug, meInfo?.username);
          } catch {
            if (!cancelled) setError("This chat's agent is no longer available to you.");
          }
        }
        const url = new URL(window.location.href);
        url.searchParams.delete("agent");
        window.history.replaceState({}, "", `${url.pathname}${url.search ? url.search : ""}`);
        return;
      }
      // No explicit param: restore the last-used agent for this user.
      const remembered = readStoredAgentSlug(meInfo?.username);
      if (!remembered) return;
      const found = list.find((row) => row.slug === remembered);
      if (found) {
        setAgent(found);
        return;
      }
      // Remembered slug not in the published list: same draft-preview
      // fallback the ?agent= path uses. Unresolvable -> unselected + error.
      try {
        const draft = await apiGet<AgentDef>(
          `/api/v1/agents/${encodeURIComponent(remembered)}?full=1`,
        );
        if (cancelled) return;
        setAgent(draft);
        setAgents((prev) =>
          prev.some((row) => row.slug === draft.slug) ? prev : [...prev, draft],
        );
      } catch {
        if (!cancelled) setError("This chat's agent is no longer available to you.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadConversations, meRef, setAgent, setAgents, setError, setMe]);

  useEffect(() => {
    if (!agent && conv?.agent_slug && agents.length > 0) {
      const found = agents.find((a) => a.slug === conv.agent_slug);
      if (found) {
        setAgent(found);
        rememberAgent(found.slug);
        return;
      }
      // A conversation bound to an unpublished draft reopens the same way as
      // ?agent=: fetch the full shape the picker never lists.
      const slug = conv.agent_slug;
      apiGet<AgentDef>(`/api/v1/agents/${encodeURIComponent(slug)}?full=1`)
        .then((draft) => {
          setAgent(draft);
          setAgents((prev) =>
            prev.some((row) => row.slug === draft.slug) ? prev : [...prev, draft],
          );
          rememberAgent(draft.slug);
        })
        .catch(() => {});
    }
  }, [agents, conv, agent, rememberAgent, setAgent, setAgents]);
}
