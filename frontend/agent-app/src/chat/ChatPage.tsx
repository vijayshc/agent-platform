import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPostJson, apiPostStream, uploadAttachment } from "../api";
import { readSse } from "../sse";
import { Spotlight } from "../shared/Spotlight";
import type { HitlDecision } from "../shared/hitl";
import type { AgentDef, AttachmentMeta, ChatMessage, Conversation, SseEvent } from "../types";
import { ChatMain } from "./ChatMain";
import { LeftRail } from "./LeftRail";
import { ModelPicker, useChatModels } from "./ModelPicker";
import { SettingsPanel, type MeInfo } from "./SettingsPanel";
import { applyEvent, hydrateMessages, pendingHitl, settleLiveReasoning, titleFromInput, uid } from "./chatLogic";
import { useConversationList } from "./useConversationList";

export function ChatPage() {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [agent, setAgent] = useState<AgentDef | null>(null);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [spotlight, setSpotlight] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [me, setMe] = useState<MeInfo | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("aa_rail_collapsed") === "true";
    } catch {
      return false;
    }
  });
  const [streaming, setStreaming] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [filesRefreshKey, setFilesRefreshKey] = useState(0);
  const [filesSince, setFilesSince] = useState<number | null>(null);
  const [fileCount, setFileCount] = useState(0);
  const turnStartedAt = useRef<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { models, modelId, setModelId, selected: selectedModel, open: modelOpen, setOpen: setModelOpen } = useChatModels();
  const {
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
  } = useConversationList(setError);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("aa_rail_collapsed", String(next));
      } catch {}
      return next;
    });
  }, []);
  const pendingSend = useRef(false);
  const sendingRef = useRef(false);
  const wasStreaming = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const userScrolledUp = useRef(false);
  // Marks the start of the most recent turn so we can scroll it to the top
  const turnStartRef = useRef<HTMLDivElement>(null);
  const aligningTop = useRef(false);
  const [transcriptHeight, setTranscriptHeight] = useState<number>(0);

  useEffect(() => {
    loadConversations(true).catch((e) => setError(String(e.message || e)));
    apiGet<{ agents: AgentDef[] }>("/api/v1/agents")
      .then((a) => setAgents(a.agents || []))
      .catch((e) => setError(String(e.message || e)));
    apiGet<MeInfo>("/api/v1/me").then(setMe).catch(() => setMe(null));
  }, [loadConversations]);

  useEffect(() => {
    if (!agent && conv?.agent_slug && agents.length > 0) {
      const found = agents.find((a) => a.slug === conv.agent_slug);
      if (found) setAgent(found);
    }
  }, [agents, conv, agent]);

  // When the agent finishes responding, return focus to the composer so the user
  // can keep typing without clicking. Skip when a HITL decision is pending (the
  // user must act in the transcript) or a dialog is open.
  useEffect(() => {
    const finished = wasStreaming.current && !streaming;
    wasStreaming.current = streaming;
    if (!finished) return;
    if (spotlight || settingsOpen || modelOpen) return;
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant && !lastAssistant.hitlResolved && pendingHitl(lastAssistant.events)) return;
    window.setTimeout(() => composerRef.current?.focus(), 0);
  }, [streaming, messages, spotlight, settingsOpen, modelOpen]);

  useEffect(() => {
    const el = transcriptRef.current;
    if (!el) return;
    const updateHeight = () => setTranscriptHeight(el.clientHeight);
    updateHeight();
    const ro = new ResizeObserver(updateHeight);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (userScrolledUp.current || aligningTop.current) return;
    const turn = turnStartRef.current;
    const el = transcriptRef.current;
    if (!el || !turn) return;

    // Follow bottom only if the streaming response has grown beyond the viewport
    const lastChild = turn.lastElementChild as HTMLElement | null;
    if (!lastChild) return;
    const childBottom = lastChild.getBoundingClientRect().bottom;
    const elBottom = el.getBoundingClientRect().bottom;

    if (childBottom > elBottom - 16) {
      bottomRef.current?.scrollIntoView({ block: "end", behavior: "auto" });
    }
  }, [messages]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSpotlight(true);
        return;
      }
      if (e.key === "Escape") {
        setSettingsOpen(false);
        setModelOpen(false);
      }
      if (e.key === "/" && !meta) {
        const t = e.target as HTMLElement | null;
        const tag = t?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || t?.isContentEditable) return;
        e.preventDefault();
        composerRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const canSend = Boolean(draft.trim() || files.length);

  function onTranscriptScroll() {
    if (aligningTop.current) return;
    const el = transcriptRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    userScrolledUp.current = !isNearBottom;
    stickToBottom.current = isNearBottom;
  }

  // Align the most recent turn to the top of the viewport, scrolling previous turns out of view.
  function scrollTurnToTop() {
    const turn = turnStartRef.current;
    const el = transcriptRef.current;
    if (!turn || !el) return;

    aligningTop.current = true;
    userScrolledUp.current = false;
    stickToBottom.current = false;

    const turnRect = turn.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const targetTop = el.scrollTop + (turnRect.top - elRect.top) - 24;

    el.scrollTo({
      top: Math.max(0, targetTop),
      behavior: "smooth",
    });

    window.setTimeout(() => {
      aligningTop.current = false;
    }, 500);
  }

  function stopInFlight() {
    abortRef.current?.abort();
    abortRef.current = null;
    sendingRef.current = false;
    setStreaming(false);
    setMessages((prev) =>
      prev.map((m) => {
        if (!m.streaming) return m;
        const next = {
          ...m,
          streaming: false,
          durationMs: m.startedAtMs ? Date.now() - m.startedAtMs : m.durationMs,
        };
        settleLiveReasoning(next);
        return next;
      }),
    );
  }

  function newChat() {
    stopInFlight();
    setConv(null);
    setMessages([]);
    setDraft("");
    setFiles([]);
    setError(null);
    composerRef.current?.focus();
  }

  async function openConversation(row: Conversation) {
    stopInFlight();
    const full = await apiGet<Conversation>(`/api/v1/conversations/${row.public_id || row.id}`);
    setConv(full);
    const slug = full.agent_slug;
    // The agent list is the server's filtered list of what this user may run.
    // Never fabricate an agent the server would reject: if this chat's agent is
    // no longer in the list, leave it unselected and say so.
    const found = slug ? agents.find((a) => a.slug === slug) : undefined;
    setAgent(found ?? null);
    if (slug && !found && agents.length > 0) {
      setError("This chat's agent is no longer available to you.");
    } else {
      setError(null);
    }
    setMessages(hydrateMessages(full, found?.name));
    stickToBottom.current = true;
  }

  function closeSpotlight() {
    pendingSend.current = false;
    setSpotlight(false);
  }

  function selectAgent(next: AgentDef) {
    setAgent(next);
    setSpotlight(false);
    if (pendingSend.current) {
      pendingSend.current = false;
      window.setTimeout(() => void send(next), 0);
    } else {
      window.setTimeout(() => composerRef.current?.focus(), 0);
    }
  }

  function addFiles(list: FileList | File[]) {
    const extra = Array.from(list);
    if (!extra.length) return;
    setFiles((prev) => [...prev, ...extra]);
  }

  function bumpConversation(thread: Conversation, title?: string): Conversation {
    const next: Conversation = {
      ...thread,
      title: title && title !== "New chat" ? title : thread.title,
      updated_at: new Date().toISOString(),
    };
    setConv(next);
    // Move the active conversation to the top: it is the most recently updated.
    setConversations((prev) => [next, ...prev.filter((c) => c.id !== next.id)]);
    return next;
  }

  // Keep the active conversation at the top of the rail and refresh its title.
  // The rail is paginated, so we can no longer rely on a full server reload.
  function applyTitle(thread: Conversation, title: string): Conversation {
    return bumpConversation(thread, title);
  }

  async function ensureConversation(selected: AgentDef, titleHint?: string): Promise<Conversation> {
    if (conv) return applyTitle(conv, titleHint || "");
    const created = await apiPostJson<Conversation>("/api/v1/conversations", {
      title: titleHint || "New chat",
      agent_id: selected.slug,
    });
    setConv(created);
    setConversations((prev) => [created, ...prev.filter((c) => c.id !== created.id)]);
    // Prepend shifts all previously loaded rows down by one, so advance the
    // next-page offset to avoid re-fetching the oldest loaded row. Only affects
    // the default (non-search) list; search pagination stays server-driven.
    if (!convQueryRef.current.trim()) {
      convOffsetRef.current += 1;
      convTotalRef.current = convTotalRef.current == null ? null : convTotalRef.current + 1;
      setConvTotal(convTotalRef.current);
    }
    return created;
  }

  async function consumeStream(res: Response, assistantId: string, signal: AbortSignal) {
    await readSse(
      res,
      (ev) => {
        // Clear streaming and sending flags as soon as the run finishes (done or error),
        // so the composer immediately allows subsequent messages to be sent.
        if (ev.type === "done" || ev.type === "error") {
          setStreaming(false);
          sendingRef.current = false;
        }
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? applyEvent(m, ev) : m)));
      },
      signal,
    );
    setMessages((prev) =>
      prev.map((m) => {
        if (m.id !== assistantId) return m;
        const next = {
          ...m,
          streaming: false,
          durationMs: m.startedAtMs ? Date.now() - m.startedAtMs : m.durationMs,
        };
        settleLiveReasoning(next);
        return next;
      }),
    );
    setStreaming(false);
    sendingRef.current = false;
  }

  async function send(overrideAgent?: AgentDef | null) {
    if (streaming) return;
    sendingRef.current = false;
    turnStartedAt.current = Math.floor(Date.now() / 1000) - 1;

    const activeSlug = conv?.agent_slug;
    const selected =
      (overrideAgent === undefined ? agent : overrideAgent) ||
      (activeSlug ? agents.find((a) => a.slug === activeSlug) : null) ||
      null;

    const text = draft.trim();
    if (!text && files.length === 0) return;
    if (!selected) {
      pendingSend.current = true;
      setSpotlight(true);
      return;
    }

    sendingRef.current = true;
    setStreaming(true);
    setError(null);
    let optimisticIds: { user: string; assistant: string } | null = null;
    let streamOpened = false;
    try {
      const titleHint = titleFromInput(text, files);
      const thread = await ensureConversation(selected, titleHint);
      const uploaded: AttachmentMeta[] = [];
      for (const file of files) {
        const row = await uploadAttachment(file, thread.id);
        uploaded.push({
          public_id: row.public_id,
          filename: row.filename,
          path: row.path,
          content_type: row.content_type,
        });
      }
      const userMsg: ChatMessage = {
        id: uid(),
        role: "user",
        content: text,
        attachments: uploaded.length ? uploaded : files.map((f) => ({ filename: f.name })),
      };
      const assistantId = uid();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        agent: selected.name,
        content: "",
        streaming: true,
        events: [],
        startedAtMs: Date.now(),
      };
      optimisticIds = { user: userMsg.id, assistant: assistantId };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setDraft("");
      setFiles([]);
      // Position the start of this new turn near the top of the viewport so the
      // streaming response has the full page to fill, matching the first message
      // (which naturally starts at the top). Stop bottom-follow so a token event
      // cannot scroll the fresh turn out of view, then align to top once the DOM
      // has rendered the new messages.
      stickToBottom.current = false;
      window.setTimeout(() => {
        scrollTurnToTop();
      }, 0);
      const ac = new AbortController();
      abortRef.current = ac;
      const res = await apiPostStream(
        `/api/v1/conversations/${thread.public_id || thread.id}/messages`,
        {
          input: text || "(attachment)",
          agent_id: selected.slug,
          stream: true,
          attachments: uploaded,
          model: modelId ? { client: modelId } : undefined,
        },
        ac.signal,
      );
      streamOpened = true;
      await consumeStream(res, assistantId, ac.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(String((e as Error).message || e));
        // The server refused the turn before opening a stream (e.g. 403 no
        // agent access). Drop the optimistic turn so the UI never pretends the
        // chat started, and hand the user their input back.
        if (optimisticIds && !streamOpened) {
          setMessages((prev) =>
            prev.filter((m) => m.id !== optimisticIds!.user && m.id !== optimisticIds!.assistant),
          );
          setDraft((prev) => (prev ? prev : text));
          setFiles((prev) => (prev.length ? prev : files));
        }
      }
    } finally {
      sendingRef.current = false;
      setStreaming(false);
      // The agent may have written files during this turn: re-list them and
      // flag anything created since the turn started.
      setFilesSince(turnStartedAt.current);
      setFilesRefreshKey((n) => n + 1);
    }
  }

  async function decide(msg: ChatMessage, ev: SseEvent, decisions: HitlDecision[]) {
    const runIdentifier = msg.runId || msg.runPublicId || ev.run_id || ev.public_id;
    if (!runIdentifier) return;
    setStreaming(true);
    sendingRef.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    // HumanInTheLoopMiddleware wants one decision per action request. HitlCard's
    // Approve/Reject buttons decide the whole card, so a single batch answer is
    // repeated for every action the interrupt asks about.
    const actionCount = ev.action_requests?.length || 0;
    const batch =
      decisions.length === 1 && actionCount > 1 && (decisions[0].type === "approve" || decisions[0].type === "reject")
        ? Array.from({ length: actionCount }, () => decisions[0])
        : decisions;
    const body = { decisions: batch, stream: true };
    try {
      const res = await apiPostStream(`/api/v1/runs/${runIdentifier}/approvals`, body, ac.signal);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msg.id
            ? { ...m, hitlResolved: true, streaming: true, durationMs: undefined, startedAtMs: m.startedAtMs ?? Date.now() }
            : m,
        ),
      );
      await consumeStream(res, msg.id, ac.signal);
      if (conv) bumpConversation(conv);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(String((e as Error).message || e));
        setMessages((prev) => prev.map((m) => (m.id === msg.id ? { ...m, hitlResolved: false, streaming: false } : m)));
      }
    } finally {
      sendingRef.current = false;
      setStreaming(false);
      setFilesRefreshKey((n) => n + 1);
    }
  }

  const empty = messages.length === 0;

  return (
    <div
      className="aa-root"
      data-page="chat"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
      }}
    >
      <LeftRail
        collapses={sidebarCollapsed}
        conversations={conversations}
        activeConv={conv}
        convSearch={convSearch}
        onConvSearch={setConvSearch}
        searchHits={searchHits}
        searchError={searchError}
        onOpenConversation={(c) => void openConversation(c)}
        onNewChat={newChat}
        onToggleCollapse={toggleSidebar}
        onLoadMore={loadMoreConversations}
        hasMore={convTotal == null || (searchHits ? searchHits.length : conversations.length) < convTotal}
        loadingMore={convLoadingMore}
        showAutomations={Boolean(me?.modules?.includes("agent_studio"))}
      />

      <ChatMain
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={toggleSidebar}
        agent={agent}
        model={selectedModel}
        streaming={streaming}
        onOpenSpotlight={() => setSpotlight(true)}
        onOpenModels={() => setModelOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
        me={me}
        empty={empty}
        draft={draft}
        setDraft={setDraft}
        files={files}
        setFiles={setFiles}
        onSend={() => void send()}
        onStop={stopInFlight}
        composerRef={composerRef}
        fileRef={fileRef}
        addFiles={addFiles}
        canSend={canSend}
        messages={messages}
        transcriptRef={transcriptRef}
        onTranscriptScroll={onTranscriptScroll}
        turnStartRef={turnStartRef}
        transcriptHeight={transcriptHeight}
        bottomRef={bottomRef}
        onDecide={(msg, ev, decisions) => void decide(msg, ev, decisions)}
        conversationId={conv ? String(conv.public_id || conv.id) : null}
        filesOpen={filesOpen}
        onToggleFiles={() => setFilesOpen((v) => !v)}
        filesRefreshKey={filesRefreshKey}
        filesSince={filesSince}
        fileCount={fileCount}
        onFileCount={setFileCount}
        live={streaming}
        onSettle={(id) =>
          setMessages((prev) =>
            prev.map((row) => (row.id === id ? { ...row, prevContent: undefined, swapping: false } : row)),
          )
        }
        error={error}
      />

      <Spotlight
        agents={agents}
        open={spotlight}
        onClose={closeSpotlight}
        onSelect={selectAgent}
        returnFocusRef={composerRef}
      />

      <ModelPicker
        models={models}
        modelId={modelId}
        open={modelOpen}
        onClose={() => setModelOpen(false)}
        onSelect={setModelId}
      />

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} me={me} />
    </div>
  );
}
