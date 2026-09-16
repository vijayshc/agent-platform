import { AdminModal } from "../adminShared";

/* Delete confirmation dialog for one knowledge document. */

interface KnowledgeDeleteModalProps {
  name: string | null;
  deleting: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export function KnowledgeDeleteModal({
  name,
  deleting,
  error,
  onClose,
  onConfirm,
}: KnowledgeDeleteModalProps) {
  return (
    <AdminModal
      title="Delete Document"
      open={name !== null}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="aa-btn aa-btn-primary" disabled={deleting} onClick={onConfirm}>
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </>
      }
    >
      {error && <div className="aa-error">{error}</div>}
      <p style={{ margin: 0 }}>
        Are you sure you want to delete <strong>{name}</strong>? This permanently removes the document
        and all of its chunks.
      </p>
    </AdminModal>
  );
}
