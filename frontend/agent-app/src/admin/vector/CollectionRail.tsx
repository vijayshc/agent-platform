/** Left rail: the vector collections available for search. */

import { useMemo, useState } from "react";
import { Database, Search } from "lucide-react";

import { formatCount } from "./resultFormat";
import type { CollectionInfo } from "./vectorTypes";

/** Rendering every match would freeze the page with thousands of collections. */
const MAX_VISIBLE = 50;

interface Props {
  collections: CollectionInfo[];
  selected: string | null;
  onSelect: (name: string) => void;
}

export function CollectionRail({ collections, selected, onSelect }: Props) {
  const [filter, setFilter] = useState("");

  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return collections;
    return collections.filter((item) => item.name.toLowerCase().includes(needle));
  }, [collections, filter]);

  const visible = matches.slice(0, MAX_VISIBLE);
  const hidden = matches.length - visible.length;

  return (
    <aside className="vb-rail" data-testid="vector-collections">
      <div className="vb-rail-head">
        <span className="vb-rail-title">Collections</span>
        <span className="vb-count-pill">{formatCount(collections.length)}</span>
      </div>

      <div className="vb-rail-search">
        <Search size={14} className="vb-rail-search-icon" />
        <input
          className="vb-rail-search-input"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Find a collection…"
          aria-label="Find a collection"
          data-testid="vector-collection-filter"
        />
      </div>

      <div className="vb-rail-list">
        {visible.map((item) => {
          const active = item.name === selected;
          return (
            <button
              key={item.name}
              type="button"
              className={`vb-rail-item${active ? " active" : ""}`}
              onClick={() => onSelect(item.name)}
              data-testid={`vector-collection-${item.name}`}
            >
              <span className="vb-rail-icon">
                <Database size={15} />
              </span>
              <span className="vb-rail-copy">
                <span className="vb-rail-name" title={item.name}>
                  {item.name}
                </span>
                <span className="vb-rail-meta">
                  {formatCount(item.count)} {item.count === 1 ? "vector" : "vectors"}
                </span>
              </span>
            </button>
          );
        })}
        {matches.length === 0 && <div className="vb-rail-empty">No collection matches “{filter}”.</div>}
        {hidden > 0 && (
          <div className="vb-rail-empty">
            +{formatCount(hidden)} more — keep typing to narrow the list.
          </div>
        )}
      </div>
    </aside>
  );
}
