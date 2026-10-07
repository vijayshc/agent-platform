import { useEffect, useMemo, useState } from "react";
import { Bot, ChevronDown, LayoutGrid, List, Loader2, Pencil, Plus, Search, Star } from "lucide-react";
import { apiGet } from "../api";
import type { AgentDef } from "../types";
import { UserPanel, type HomeAdminMenuItem } from "./UserPanel";
import "./HomePage.css";

type View = "cards" | "list";
type SortKey = "updated" | "created" | "name";

function patternLabel(a: AgentDef): string {
  const cfg = (a.config || {}) as Record<string, unknown>;
  return String(a.pattern || cfg.pattern || cfg.runtime || a.kind || "agent");
}

/** Short row date; full ISO kept in the title attribute. */
function shortDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function HomePage() {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>(() => {
    try {
      const v = localStorage.getItem("aa_home_sort");
      return v === "name" || v === "created" ? v : "updated";
    } catch {
      return "updated";
    }
  });
  const [view, setView] = useState<View>(() => {
    try {
      return (localStorage.getItem("aa_home_view") as View) === "list" ? "list" : "cards";
    } catch {
      return "cards";
    }
  });
  const [username, setUsername] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [adminMenu, setAdminMenu] = useState<HomeAdminMenuItem[]>([]);
  const [studioAllowed, setStudioAllowed] = useState(false);
  const [editableSlugs, setEditableSlugs] = useState<Set<string> | null>(null);
  const [userOpen, setUserOpen] = useState(false);
  const [favs, setFavs] = useState<Set<string>>(new Set());

  const favKey = username ? `aa_home_favs_${username}` : null;

  useEffect(() => {
    if (!username) {
      setFavs(new Set());
      return;
    }
    try {
      const raw = localStorage.getItem(`aa_home_favs_${username}`);
      const arr = raw ? (JSON.parse(raw) as unknown) : [];
      setFavs(new Set(Array.isArray(arr) ? arr.filter((s) => typeof s === "string") : []));
    } catch {
      setFavs(new Set());
    }
  }, [username]);

  function toggleFav(slug: string) {
    setFavs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      try {
        if (favKey) localStorage.setItem(favKey, JSON.stringify([...next]));
      } catch {}
      return next;
    });
  }

  useEffect(() => {
    let cancelled = false;
    // Single-shot agents fetch: ?include_drafts=1 carries the full shape
    // (published flag + access) for BOTH the published list and the editable
    // set. Plain /api/v1/agents is only the 403 fallback for non-studio
    // callers. Published filtering + editable derivation happen client-side
    // from the same payload, and both states are committed in one tick with
    // /me so Chat + Edit land in the same paint (no pop-in).
    async function load() {
      const agentsTask = (async (): Promise<{ rows: AgentDef[]; full: boolean }> => {
        try {
          const full = await apiGet<{ agents: AgentDef[] }>("/api/v1/agents?include_drafts=1");
          return { rows: full.agents || [], full: true };
        } catch {
          const plain = await apiGet<{ agents: AgentDef[] }>("/api/v1/agents");
          return { rows: plain.agents || [], full: false };
        }
      })();
      const meTask = apiGet<{
        username?: string | null;
        email?: string | null;
        admin_menu?: HomeAdminMenuItem[];
      }>("/api/v1/me").catch(() => null);
      try {
        const [agentsRes, me] = await Promise.all([agentsTask, meTask]);
        if (cancelled) return;
        const published = agentsRes.rows.filter((a) => a.published);
        setAgents(published);
        // Editable set comes from the same payload — no second request.
        // The plain-list fallback carries no draft shape, so nothing is editable.
        setEditableSlugs(
          agentsRes.full ? new Set(agentsRes.rows.map((a) => a.slug)) : new Set<string>(),
        );
        if (me) {
          setUsername(me.username ?? null);
          setEmail(me.email ?? null);
          setAdminMenu(me.admin_menu ?? []);
          const keys = new Set((me.admin_menu ?? []).map((m) => String(m.key ?? "")));
          setStudioAllowed(keys.has("agent_studio"));
        } else {
          setStudioAllowed(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setEditableSlugs(new Set<string>());
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  function setViewPersisted(next: View) {
    setView(next);
    try {
      localStorage.setItem("aa_home_view", next);
    } catch {}
  }

  function setSortPersisted(next: SortKey) {
    setSort(next);
    try {
      localStorage.setItem("aa_home_sort", next);
    } catch {}
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = !q
      ? agents
      : agents.filter((a) =>
          `${a.name} ${a.slug} ${a.description || ""} ${patternLabel(a)}`.toLowerCase().includes(q),
        );
    const rows = [...base];
    if (sort === "name") rows.sort((x, y) => x.name.localeCompare(y.name));
    else if (sort === "created") rows.sort((x, y) => String(y.created_at || "").localeCompare(String(x.created_at || "")));
    else rows.sort((x, y) => String(y.updated_at || "").localeCompare(String(x.updated_at || "")));
    return rows;
  }, [agents, query, sort]);

  const pinned = useMemo(() => filtered.filter((a) => favs.has(a.slug)), [filtered, favs]);
  const rest = useMemo(() => filtered.filter((a) => !favs.has(a.slug)), [filtered, favs]);

  function favButton(a: AgentDef) {
    const active = favs.has(a.slug);
    return (
      <button
        type="button"
        className={`aa-btn aa-home-favbtn${active ? " active" : ""}`}
        data-testid="home-fav"
        title={active ? "Unpin" : "Pin"}
        aria-label={active ? `Unpin ${a.name}` : `Pin ${a.name}`}
        aria-pressed={active}
        onClick={() => toggleFav(a.slug)}
      >
        <Star size={13} fill={active ? "currentColor" : "none"} aria-hidden="true" />
      </button>
    );
  }

  function editButton(a: AgentDef) {
    if (!studioAllowed || !editableSlugs?.has(a.slug)) return null;
    return (
      <a
        className="aa-btn aa-home-editbtn"
        href={`/agent-studio/editor?slug=${encodeURIComponent(a.slug)}`}
        data-testid="home-edit"
        title={`Edit ${a.name}`}
        aria-label={`Edit ${a.name}`}
      >
        <Pencil size={13} aria-hidden="true" />
      </a>
    );
  }

  function nameLink(a: AgentDef) {
    return (
      <a
        className="aa-home-namelink"
        href={`/agent?agent=${encodeURIComponent(a.slug)}`}
        data-testid="home-chat"
        title={`Chat with ${a.name}`}
        aria-label={`Chat with ${a.name}`}
      >
        {a.name}
      </a>
    );
  }

  function cardNode(a: AgentDef) {
    return (
      <article key={a.id} className="aa-home-card" data-testid="home-card">
        <div className="aa-home-card-top">
          <h2 className="aa-home-card-name" title={a.name}>
            {nameLink(a)}
          </h2>
          {a.version != null ? <span className="aa-home-ver">v{a.version}</span> : null}
          {favButton(a)}
        </div>
        {a.description ? (
          <p className="aa-home-card-desc" title={a.description}>
            {a.description}
          </p>
        ) : null}
        <div className="aa-home-card-foot">
          <span className="aa-home-card-metaleft">
            {a.created_by_name ? (
              <span className="aa-home-card-by" title={`Created by ${a.created_by_name}`}>
                By {a.created_by_name}
              </span>
            ) : null}
            {a.created_by_name && a.updated_at ? <span aria-hidden="true">·</span> : null}
            {a.updated_at ? (
              <span
                className="aa-home-card-updated"
                title={`Updated ${a.updated_at}`}
                data-testid="home-card-updated"
              >
                Updated {shortDate(a.updated_at)}
              </span>
            ) : null}
          </span>
          <span className="aa-home-card-actions">{editButton(a)}</span>
        </div>
      </article>
    );
  }

  function rowNode(a: AgentDef) {
    return (
      <div key={a.id} className="aa-home-row" data-testid="home-row" role="row">
        <div className="aa-home-row-main">
          <span className="aa-home-row-name" title={a.name}>
            {nameLink(a)}
            {a.version != null ? <span className="aa-home-ver">v{a.version}</span> : null}
          </span>
          {a.description ? (
            <span className="aa-muted aa-home-row-desc" title={a.description}>
              {a.description}
            </span>
          ) : null}
        </div>
        <span
          className="aa-home-row-meta"
          title={`Created ${a.created_at || "unknown"} · Updated ${a.updated_at || "unknown"}`}
          data-testid="home-row-meta"
        >
          {a.created_by_name ? (
            <span className="aa-home-row-meta-by">By {a.created_by_name}</span>
          ) : null}
          {a.created_by_name && a.updated_at ? <span aria-hidden="true">·</span> : null}
          {a.updated_at ? <span>Updated {shortDate(a.updated_at)}</span> : null}
        </span>
        <span className="aa-home-row-actions">
          {favButton(a)}
          {editButton(a)}
        </span>
      </div>
    );
  }

  return (
    <div className="aa-root aa-home" data-testid="home-page">
      <header className="aa-home-topbar">
        <a className="aa-home-brand" href="/" aria-label="Agents home">
          <span className="aa-home-brand-mark">
            <Bot size={17} />
          </span>
          <span className="aa-home-brand-name">Agents</span>
        </a>
        <div className="aa-home-topbar-right">
          {username ? (
            <>
              <button
                type="button"
                className="aa-btn aa-btn-ghost aa-home-userbtn"
                aria-expanded={userOpen}
                aria-haspopup="dialog"
                data-testid="home-user-menu"
                title={username}
                onClick={() => setUserOpen(true)}
              >
                <span className="aa-home-user">{username}</span>
                <ChevronDown size={13} aria-hidden="true" />
              </button>
              <UserPanel
                open={userOpen}
                username={username}
                email={email}
                menu={adminMenu}
                onClose={() => setUserOpen(false)}
              />
            </>
          ) : null}
        </div>
      </header>

      <main className="aa-home-main">
        <div className="aa-home-pagehead">
          <div>
            <h1 className="aa-home-h1">Agent library</h1>
          </div>
          {studioAllowed ? (
            <a className="aa-btn aa-btn-primary aa-home-newbtn" href="/agent-studio/editor" data-testid="home-create">
              <Plus size={14} /> New agent
            </a>
          ) : null}
        </div>

        <div className="aa-home-toolbar">
          <label className="aa-home-searchwrap">
            <Search size={14} aria-hidden="true" />
            <input
              className="aa-home-search"
              placeholder="Search agents…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="home-search"
              aria-label="Search agents"
            />
          </label>
          <div className="aa-home-toolbar-right">
            <label className="aa-home-sortwrap">
              <span className="aa-home-sortlabel">Sort</span>
              <select
                className="aa-btn aa-home-sort"
                value={sort}
                onChange={(e) => setSortPersisted(e.target.value as SortKey)}
                data-testid="home-sort"
                aria-label="Sort agents"
              >
                <option value="updated">Recently updated</option>
                <option value="created">Recently created</option>
                <option value="name">Name</option>
              </select>
            </label>
            <div className="aa-home-viewtoggle" role="group" aria-label="Layout">
              <button
                type="button"
                className={`aa-btn aa-btn-ghost aa-home-iconbtn${view === "cards" ? " active" : ""}`}
                aria-pressed={view === "cards"}
                title="Card view"
                data-testid="home-view-cards"
                onClick={() => setViewPersisted("cards")}
              >
                <LayoutGrid size={15} />
              </button>
              <button
                type="button"
                className={`aa-btn aa-btn-ghost aa-home-iconbtn${view === "list" ? " active" : ""}`}
                aria-pressed={view === "list"}
                title="List view"
                data-testid="home-view-list"
                onClick={() => setViewPersisted("list")}
              >
                <List size={15} />
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <p className="aa-muted aa-home-status" role="status">
            <Loader2 size={14} className="aa-home-spin" /> Loading agents…
          </p>
        ) : error ? (
          <p className="aa-error aa-home-status">{error}</p>
        ) : filtered.length === 0 ? (
          <div className="aa-home-empty" data-testid="home-empty">
            <span className="aa-home-empty-mark">
              <Bot size={22} />
            </span>
            <p className="aa-home-empty-title">
              {query ? "No agents match your search" : "No published agents yet"}
            </p>
            <p className="aa-muted">
              {query ? "Try a different search term." : "Publish an agent from the Studio to see it here."}
            </p>
            {studioAllowed && !query ? (
              <a className="aa-btn aa-btn-primary aa-home-newbtn" href="/agent-studio/editor">
                <Plus size={14} /> Create agent
              </a>
            ) : null}
          </div>
        ) : view === "cards" ? (
          <>
            {pinned.length > 0 ? (
              <section aria-label="Pinned agents" data-testid="home-pinned">
                <h2 className="aa-home-section-title">Pinned</h2>
                <div className="aa-home-grid" data-testid="home-grid-pinned">
                  {pinned.map((a) => cardNode(a))}
                </div>
              </section>
            ) : null}
            <div className="aa-home-grid" data-testid="home-grid" aria-label={`${filtered.length} published agents`}>
              {rest.map((a) => cardNode(a))}
            </div>
          </>
        ) : (
          <>
            {pinned.length > 0 ? (
              <section aria-label="Pinned agents" data-testid="home-pinned">
                <h2 className="aa-home-section-title">Pinned</h2>
                <div className="aa-home-table" data-testid="home-list-pinned" role="table" aria-label="Pinned agents">
                  {pinned.map((a) => rowNode(a))}
                </div>
              </section>
            ) : null}
            <div className="aa-home-table" data-testid="home-list" role="table" aria-label={`${filtered.length} published agents`}>
              {rest.map((a) => rowNode(a))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
