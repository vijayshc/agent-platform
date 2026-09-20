import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, PanelLeftClose, Search, Workflow } from "lucide-react";
import type { Conversation } from "../types";
import { conversationTitle, formatChatWhen, groupedConversations } from "./chatLogic";

type HoverState = {
  c: Conversation;
  x: number;
  y: number;
} | null;

function ChatItem({
  c,
  activeConv,
  onOpenConversation,
  onMouseEnter,
  onMouseLeave,
}: {
  c: Conversation;
  activeConv: Conversation | null;
  onOpenConversation: (c: Conversation) => void;
  onMouseEnter: (c: Conversation, e: React.MouseEvent<HTMLButtonElement>) => void;
  onMouseLeave: () => void;
}) {
  const isActive = Boolean(activeConv && activeConv.id === c.id);
  return (
    <button
      type="button"
      className={`aa-rail-conv-item${isActive ? " active" : ""}`}
      data-testid="conversation-item"
      onClick={() => onOpenConversation(c)}
      onMouseEnter={(e) => onMouseEnter(c, e)}
      onMouseLeave={onMouseLeave}
    >
      <span className="aa-rail-conv-dot" />
      <span className="aa-rail-conv-title">{conversationTitle(c)}</span>
    </button>
  );
}

/** Max width of the conversation hover tooltip (`.aa-rail-conv-tip` max-width). */
const TIP_MAX_WIDTH = 260;
/** Gap between the rail edge and the hover tooltip. */
const TIP_GAP = 8;

export function LeftRail({
  collapses,
  conversations,
  activeConv,
  convSearch,
  onConvSearch,
  searchHits,
  searchError,
  onOpenConversation,
  onNewChat,
  onToggleCollapse,
  onLoadMore,
  hasMore,
  loadingMore,
  showAutomations,
}: {
  collapses: boolean;
  conversations: Conversation[];
  activeConv: Conversation | null;
  convSearch: string;
  onConvSearch: (v: string) => void;
  searchHits: Conversation[] | null;
  searchError: string | null;
  onOpenConversation: (c: Conversation) => void;
  onNewChat: () => void;
  onToggleCollapse: () => void;
  onLoadMore: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  showAutomations: boolean;
}) {
  const [hover, setHover] = useState<HoverState>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const loadingMoreRef = useRef(loadingMore);
  const hasMoreRef = useRef(hasMore);
  const onLoadMoreRef = useRef(onLoadMore);
  loadingMoreRef.current = loadingMore;
  hasMoreRef.current = hasMore;
  onLoadMoreRef.current = onLoadMore;
  const list = searchHits || conversations;
  const listLen = list.length;

  // Infinite scroll: observe the sentinel when it exists. The sentinel renders
  // only after the first page arrives, so re-run whenever the list length
  // changes (or when we have more and aren't already loading).
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || listLen === 0) return;
    observerRef.current?.disconnect();
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting && hasMoreRef.current && !loadingMoreRef.current) {
          onLoadMoreRef.current();
        }
      }
    });
    io.observe(el);
    observerRef.current = io;
    return () => io.disconnect();
  }, [listLen, hasMore, loadingMore]);

  const groups = groupedConversations(searchHits || conversations);

  function showTip(c: Conversation, e: React.MouseEvent<HTMLButtonElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    // Anchor the tooltip just outside the rail, to the right of the hovered
    // chat item, so it never covers the item (or the rest of the list) it
    // describes. Clamp to the viewport so it stays fully visible on narrow
    // windows.
    const railRight = e.currentTarget.closest(".aa-rail")?.getBoundingClientRect().right ?? rect.right;
    setHover({
      c,
      x: Math.min(railRight + TIP_GAP, Math.max(8, window.innerWidth - TIP_MAX_WIDTH - TIP_GAP)),
      y: Math.min(rect.top, Math.max(8, window.innerHeight - 104)),
    });
  }

  function hideTip() {
    setHover(null);
  }

  return (
    <aside className={`aa-rail${collapses ? " collapsed" : ""}`} data-testid="conversation-sidebar">
      <div className="aa-rail-top">
        <div className="aa-rail-brand-row">
          <a className="aa-rail-brand" href="/" aria-label="Home" data-testid="rail-home">
            <Bot size={18} />
          </a>
          <button
            type="button"
            className="aa-rail-icon-btn"
            data-testid="rail-collapse"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            onClick={onToggleCollapse}
          >
            <PanelLeftClose size={16} />
          </button>
        </div>

        <button type="button" className="aa-rail-item primary" data-testid="new-chat" onClick={onNewChat}>
          <span className="aa-rail-item-icon">
            <span className="aa-rail-plus">+</span>
          </span>
          <span className="aa-rail-item-label">New Chat</span>
        </button>

        {showAutomations && (
          <a className="aa-rail-item" href="/agent-studio" data-testid="rail-automations" title="Agent Studio">
            <span className="aa-rail-item-icon">
              <Workflow size={16} />
            </span>
            <span className="aa-rail-item-label">Automations</span>
          </a>
        )}
      </div>

      <div className="aa-rail-search">
        <Search size={14} />
        <input
          type="text"
          className="aa-rail-search-input"
          placeholder="Search"
          aria-label="Search chats"
          data-testid="rail-search-input"
          value={convSearch}
          onChange={(e) => onConvSearch(e.target.value)}
        />
      </div>

      <div className="aa-rail-convs">
        <div className="aa-rail-heading">Chats</div>
        <div className="aa-rail-conv-list">
          {searchError && (
            <div className="aa-error" style={{ padding: "8px 10px" }} data-testid="conversation-search-error">
              {searchError}
            </div>
          )}
          {convSearch.trim()
            ? list.map((c) => (
                <ChatItem key={c.public_id || c.id} c={c} activeConv={activeConv} onOpenConversation={onOpenConversation} onMouseEnter={showTip} onMouseLeave={hideTip} />
              ))
            : groups.map((group) => (
                <div key={group.label} className="aa-rail-conv-group">
                  <div className="aa-rail-subhead">{group.label}</div>
                  {group.items.map((c) => (
                    <ChatItem key={c.public_id || c.id} c={c} activeConv={activeConv} onOpenConversation={onOpenConversation} onMouseEnter={showTip} onMouseLeave={hideTip} />
                  ))}
                </div>
              ))}
          {searchHits && searchHits.length === 0 && convSearch.trim() && (
            <div className="aa-muted" style={{ padding: "8px 10px" }} data-testid="conversation-empty">
              No matching conversations
            </div>
          )}
          {!searchHits && conversations.length === 0 && (
            <div className="aa-muted" style={{ padding: "8px 10px" }} data-testid="conversation-empty">
              No conversations yet
            </div>
          )}
          {/* Sentinel observed for infinite scroll; triggers loading more pages. */}
          {list.length > 0 && hasMore && (
            <div ref={sentinelRef} className="aa-rail-conv-sentinel" data-testid="conversation-load-more" />
          )}
          {loadingMore && (
            <div className="aa-rail-conv-loading aa-muted" data-testid="conversation-loading-more">
              Loading…
            </div>
          )}
        </div>
      </div>

      {hover &&
        createPortal(
          <div
            className="aa-rail-conv-tip"
            role="tooltip"
            style={{ left: hover.x, top: hover.y }}
            onMouseEnter={hideTip}
          >
            <div className="aa-rail-conv-tip-row">
              <span className="aa-rail-conv-tip-label">Started</span>
              <span>{formatChatWhen(hover.c.created_at)}</span>
            </div>
            <div className="aa-rail-conv-tip-row">
              <span className="aa-rail-conv-tip-label">Ended</span>
              <span>{formatChatWhen(hover.c.updated_at)}</span>
            </div>
          </div>,
          document.body,
        )}
    </aside>
  );
}
