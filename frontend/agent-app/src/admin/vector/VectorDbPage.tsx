/** Vector database console: pick a collection, then search or filter it. */

import { useState } from "react";
import { Database, Plus, ShieldCheck } from "lucide-react";

import { AdminError, AdminLoading } from "../adminShared";
import { CollectionRail } from "./CollectionRail";
import { CollectionAccessModal } from "./CollectionAccessModal";
import { CreateCollectionModal } from "./CreateCollectionModal";
import { MetadataPanel } from "./MetadataPanel";
import { ResultsList } from "./ResultsList";
import { SearchControls } from "./SearchControls";
import { formatCount } from "./resultFormat";
import type { CollectionInfo } from "./vectorTypes";
import { useVectorSearch } from "./useVectorSearch";
import "./vector.css";
import "./vectorResults.css";

export function VectorDbPage() {
  const controller = useVectorSearch();
  const {
    collections,
    totalDocuments,
    loading,
    loadError,
    reload,
    selected,
    selectedInfo,
    select,
    fields,
    results,
  } = controller;

  const [createOpen, setCreateOpen] = useState(false);
  const [accessTarget, setAccessTarget] = useState<CollectionInfo | null>(null);

  if (loading) return <AdminLoading label="Loading vector collections…" />;
  if (loadError) {
    return (
      <div className="vb-page">
        <AdminError message={`Could not load vector collections: ${loadError}`} />
        <div className="vb-center">
          <button type="button" className="aa-btn aa-btn-ghost" onClick={reload}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="vb-page" data-testid="vector-db-page">
      <header className="vb-head">
        <h2 className="vb-title">Vector Database</h2>
        <div className="vb-head-stats">
          <span className="vb-stat">
            <b>{formatCount(collections.length)}</b> collections
          </span>
          <span className="vb-stat">
            <b>{formatCount(totalDocuments)}</b> vectors
          </span>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={() => setCreateOpen(true)}
            data-testid="vector-create-collection"
          >
            <Plus size={14} /> New collection
          </button>
        </div>
      </header>

      <div className="vb-body">
        <CollectionRail collections={collections} selected={selected} onSelect={select} />

        <section className="vb-main">
          {!selected && (
            <div className="vb-placeholder" data-testid="vector-empty-selection">
              <Database size={26} />
              <h3>Pick a collection</h3>
              <p>Choose a collection on the left to search its vectors by meaning, by keyword or both.</p>
            </div>
          )}

          {selected && (
            <>
              <div className="vb-collection-head">
                <div className="vb-collection-title">
                  <h3>{selected}</h3>
                  <span className="vb-count-pill">{formatCount(selectedInfo?.count ?? 0)} vectors</span>
                  {selectedInfo?.metadata?.created_by !== undefined && (
                    <span className="vb-meta-tag">created by {String(selectedInfo.metadata.created_by)}</span>
                  )}
                </div>
                <div className="vb-collection-actions">
                  <span
                    className={`vb-access-pill${selectedInfo?.restricted ? " restricted" : ""}`}
                    title={
                      selectedInfo?.restricted
                        ? "Only these roles (and administrators) can select this collection"
                        : "No roles granted yet — only administrators can select this collection"
                    }
                  >
                    {selectedInfo?.restricted
                      ? `Restricted · ${(selectedInfo.roles || []).join(", ")}`
                      : "Admins only"}
                  </span>
                  <button
                    type="button"
                    className="aa-btn aa-btn-ghost"
                    disabled={typeof selectedInfo?.access_id !== "number"}
                    onClick={() => selectedInfo && setAccessTarget(selectedInfo)}
                    data-testid="vector-collection-access"
                  >
                    <ShieldCheck size={14} /> Access
                  </button>
                </div>
              </div>

              <SearchControls
                query={controller.query}
                onQueryChange={controller.setQuery}
                onSearch={controller.search}
                searching={controller.searching}
                canSearch={controller.canSearch}
                mode={controller.mode}
                onModeChange={controller.setMode}
                limit={controller.limit}
                onLimitChange={controller.setLimit}
                placeholder={`Search ${selected}…`}
                dirty={controller.isDirty}
                filterApplied={controller.appliedFilter !== null}
              />

              <MetadataPanel
                fields={fields}
                fieldsSampled={controller.fieldsSampled}
                fieldsLoading={controller.fieldsLoading}
                fieldsError={controller.fieldsError}
                filterText={controller.filterText}
                onFilterTextChange={controller.setFilterText}
                onApply={controller.applyFilter}
                onClear={controller.clearFilter}
                appliedFilterText={controller.appliedFilterText}
                error={controller.filterError}
              />

              <ResultsList
                hits={results}
                stats={controller.stats}
                query={controller.executedQuery}
                searching={controller.searching}
                error={controller.searchError}
                onRetry={controller.retry}
                hasFilter={controller.appliedFilter !== null}
                onClearFilter={controller.clearFilter}
              />
            </>
          )}
        </section>
      </div>

      <CreateCollectionModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(name) => {
          reload();
          select(name);
        }}
      />
      <CollectionAccessModal
        collection={accessTarget}
        onClose={() => setAccessTarget(null)}
        onSaved={() => reload()}
      />
    </div>
  );
}
