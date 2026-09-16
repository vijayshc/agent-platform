/** Left rail: your agents + the catalog-driven component gallery.
 *
 * Agents, patterns and building blocks all come from the capability catalog, so
 * the palette never needs a code change when the backend ships a capability.
 * Cards drag onto the canvas (`application/studio-node`); blueprints (prompt
 * chaining, routing, …) apply on click because they are multi-node.
 */
import { useMemo, useState } from "react";
import { PanelLeftClose, Plus, Search } from "lucide-react";
import type { AgentDef } from "../../types";
import { blueprintLabel, catalogIcon, nodeKindEntries, patternEntries, runtimeEntries } from "../model/catalog";
import type { CatalogPattern, StudioCatalog } from "../model/types";
import "./palette.css";

const BLUEPRINT_PATTERNS = new Set(["supervisor", "swarm", "graph"]);

/** The blueprint a saved definition was built from (graph flows only). */
function configTemplate(agent: AgentDef): string | null {
  const config = (agent.config ?? {}) as Record<string, unknown>;
  const template = config.template ?? agent.template;
  return typeof template === "string" ? template : null;
}

export interface PaletteProps {
  catalog: StudioCatalog | null;
  agents: AgentDef[];
  activeSlug: string | null;
  /** Slug highlighted by the current canvas selection (Saved flow refs). */
  highlightSlug: string | null;
  loading: boolean;
  onOpenAgent: (slug: string) => void;
  onNewAgent: () => void;
  onAddComponent: (paletteType: string) => void;
  onApplyTemplate: (templateId: string, label: string) => void;
  onDragComponent: (event: React.DragEvent, paletteType: string) => void;
  onCollapse: () => void;
}

interface Item {
  id: string;
  label: string;
  hint: string;
  icon: string;
  draggable: boolean;
  badge?: string;
}

export function Palette(props: PaletteProps) {
  const {
    catalog,
    agents,
    activeSlug,
    highlightSlug,
    loading,
    onOpenAgent,
    onNewAgent,
    onAddComponent,
    onApplyTemplate,
    onDragComponent,
    onCollapse,
  } = props;
  const [query, setQuery] = useState("");

  const groups = useMemo(() => {
    const runtimes: Item[] = runtimeEntries(catalog).map((r) => ({
      id: r.id,
      label: r.label,
      hint: r.summary,
      icon: r.icon ?? "bot",
      draggable: true,
      badge: r.builder.split(".").pop(),
    }));
    const patterns: Item[] = patternEntries(catalog).map((p: CatalogPattern) => ({
      id: p.id,
      label: p.label,
      hint: p.summary,
      icon: p.icon ?? "workflow",
      draggable: BLUEPRINT_PATTERNS.has(p.id),
      badge: BLUEPRINT_PATTERNS.has(p.id) ? p.builder.split(".").pop() : "blueprint",
    }));
    // The "agent" node kind is the agent runtime under a second name; listing it
    // twice would duplicate the `palette-agent` hook and the capability itself.
    const runtimeIds = new Set(runtimes.map((r) => r.id));
    const kinds: Item[] = nodeKindEntries(catalog)
      .filter((k) => !runtimeIds.has(k.id))
      .map((k) => ({
        id: k.id,
        label: k.label,
        hint: k.summary ?? "",
        icon: k.icon ?? "wrench",
        draggable: true,
        badge: k.builder.split(".").pop(),
      }));
    return [
      { title: "Agents", items: runtimes },
      { title: "Patterns", items: patterns },
      { title: "Building blocks", items: kinds },
    ];
  }, [catalog]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return groups;
    return groups
      .map((group) => ({
        ...group,
        items: group.items.filter((item) =>
          `${item.label} ${item.hint} ${item.id} ${item.badge ?? ""}`.toLowerCase().includes(needle),
        ),
      }))
      .filter((group) => group.items.length);
  }, [groups, query]);

  // Same-name drafts and published copies are common: show the slug + status and
  // collapse rows that are identical in every visible field.
  const uniqueAgents = useMemo(() => {
    const seen = new Set<string>();
    return agents.filter((agent) => {
      const key = `${agent.slug}|${agent.name}|${agent.kind}|${agent.published ? "p" : "d"}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [agents]);

  const filteredAgents = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = needle
      ? uniqueAgents.filter((a) =>
          `${a.name} ${a.slug} ${a.kind} ${a.pattern ?? ""} ${a.published ? "published" : "draft"}`
            .toLowerCase()
            .includes(needle),
        )
      : uniqueAgents;
    return rows;
  }, [uniqueAgents, query]);

  function activate(item: Item) {
    if (item.draggable) onAddComponent(item.id);
    else onApplyTemplate(item.id, item.label);
  }

  return (
    <aside className="as-rail" data-testid="studio-palette">
      <div className="as-rail-head">
        <a className="as-rail-brand" href="/agent-studio" data-testid="studio-back">
          Agent Studio
        </a>
        <div className="as-row">
          <button type="button" className="as-btn as-btn-ghost as-btn-icon" title="New agent" onClick={onNewAgent} data-testid="studio-new">
            <Plus size={15} />
          </button>
          <button
            type="button"
            className="as-btn as-btn-ghost as-btn-icon"
            title="Hide the component palette"
            onClick={onCollapse}
            data-testid="studio-palette-collapse"
          >
            <PanelLeftClose size={15} />
          </button>
        </div>
      </div>

      <div className="as-rail-search">
        <Search size={13} className="as-rail-search-icon" />
        <input
          className="as-input as-rail-search-input"
          id="studio-palette-search"
          name="palette-search"
          aria-label="Search agents and components"
          placeholder="Search agents & components"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          data-testid="studio-palette-search"
        />
      </div>

      <div className="as-rail-scroll">
        {filtered.map((group) => (
          <section className="as-pal-group" key={group.title}>
            <h4 className="as-pal-group-title">
              {group.title} <span className="as-pal-count">{group.items.length}</span>
            </h4>
            {group.items.map((item) => {
              const Icon = catalogIcon(item.icon);
              return (
                <button
                  type="button"
                  key={item.id}
                  className="as-pal-item"
                  data-testid={`palette-${item.id}`}
                  draggable={item.draggable}
                  onDragStart={(event) => onDragComponent(event, item.id)}
                  onClick={() => activate(item)}
                  title={item.hint}
                >
                  <span className="as-pal-icon" aria-hidden="true">
                    <Icon size={14} />
                  </span>
                  <span className="as-pal-text">
                    <span className="as-pal-label">{item.label}</span>
                    <span className="as-pal-hint">{item.hint}</span>
                  </span>
                  {item.badge ? <span className="as-pal-badge">{item.badge}</span> : null}
                </button>
              );
            })}
          </section>
        ))}
        <section className="as-pal-group">
          <h4 className="as-pal-group-title">
            Your agents{" "}
            <span className="as-pal-count">
              {query.trim() ? `${filteredAgents.length}/${uniqueAgents.length}` : uniqueAgents.length}
            </span>
          </h4>
          {loading ? <div className="as-pal-empty">Loading…</div> : null}
          {!loading && !filteredAgents.length && filtered.length ? (
            <div className="as-pal-empty">
              {uniqueAgents.length ? "No agent matches that search." : "No agents yet — add one to the canvas."}
            </div>
          ) : null}
          {!loading && !filteredAgents.length && !filtered.length && !query.trim() ? (
            <div className="as-pal-empty">No agents yet — add one to the canvas.</div>
          ) : null}
          <div className="as-agent-list">
            {filteredAgents.map((agent) => (
              <button
                type="button"
                key={agent.slug}
                className={`as-agent-item${agent.slug === (highlightSlug ?? activeSlug) ? " is-active" : ""}${
                  agent.slug === activeSlug ? " is-editing" : ""
                }`}
                onClick={() => onOpenAgent(agent.slug)}
                title={`${agent.name} · ${agent.slug} · ${
                  blueprintLabel(agent.pattern, configTemplate(agent)) || agent.kind
                } · ${agent.published ? "published" : "draft"}${agent.description ? ` · ${agent.description}` : ""}`}
                data-testid={`studio-agent-${agent.slug}`}
              >
                <span className="as-agent-dot" data-published={agent.published ? "1" : "0"} aria-hidden="true" />
                <span className="as-agent-text">
                  <span className="as-agent-name">{agent.name}</span>
                  <span className="as-agent-slug">
                    {agent.description || agent.slug}
                    {agent.published ? "" : " · draft"}
                  </span>
                </span>
                <span className="as-agent-kind">
                  {blueprintLabel(agent.pattern, configTemplate(agent)) || agent.kind}
                </span>
              </button>
            ))}
          </div>
        </section>
        {!filtered.length && !filteredAgents.length ? (
          <div className="as-pal-empty">Nothing matches “{query}”.</div>
        ) : null}
      </div>

    </aside>
  );
}
