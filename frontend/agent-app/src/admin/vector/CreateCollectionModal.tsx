import { useEffect, useState } from "react";
import { AdminField, AdminModal } from "../adminShared";
import { vectorApi } from "./vectorApi";

/* ------------------------------------------------------------------ *
 * Create a vector collection.  Only administrators reach this page;
 * knowledge uploads can select a collection but never create one.
 * ------------------------------------------------------------------ */

interface CreateCollectionModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void;
}

export function CreateCollectionModal({ open, onClose, onCreated }: CreateCollectionModalProps) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setSaving(false);
    setError(null);
  }, [open]);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Enter a collection name.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await vectorApi.createCollection(trimmed);
      if (!res.success) {
        setError(res.error || "Failed to create the collection.");
        return;
      }
      onCreated(res.name || trimmed);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminModal
      title="New Collection"
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            disabled={saving}
            onClick={() => void submit()}
            data-testid="vector-create-submit"
          >
            {saving ? "Creating…" : "Create collection"}
          </button>
        </>
      }
    >
      <AdminField
        label="Name"
        hint="3–63 characters using letters, numbers, underscores, hyphens or dots."
      >
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. hr_records"
          autoFocus
          data-testid="vector-create-name"
        />
      </AdminField>
      {error && <div className="aa-error" role="alert">{error}</div>}
      <p className="aa-muted" style={{ margin: 0 }}>
        After creating it, use <strong>Access</strong> on the collection to grant roles. Until then only
        administrators can see it.
      </p>
    </AdminModal>
  );
}
