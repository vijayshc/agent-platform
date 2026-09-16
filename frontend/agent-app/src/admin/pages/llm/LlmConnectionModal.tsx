import { useEffect, useRef, useState } from "react";
import { adminPostJson, AdminModal, AdminField } from "../../adminShared";
import { CodeEditor } from "../../../shared/CodeEditor";
import { HttpHeadersEditor } from "./HttpHeadersEditor";
import {
  EMPTY_FORM,
  EXTRA_BODY_EXAMPLE,
  formToPayload,
  toForm,
  validateForm,
  type LlmActionResponse,
  type LlmConnection,
  type LlmForm,
} from "./llmConnection";

const BASE = "/admin/config/llm/api";

interface Props {
  /** Connection being edited; null creates a new one. */
  editing: LlmConnection | null;
  /** Administrators only: the global default is deployment-wide state. */
  canSetDefault: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Add/edit form for one LLM connection. Mounted fresh per open (the page keys
 * it by connection id), so the fields always seed from the selected connection.
 */
export function LlmConnectionModal({ editing, canSetDefault, onClose, onSaved }: Props) {
  const [form, setForm] = useState<LlmForm>(() => (editing ? toForm(editing) : EMPTY_FORM));
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const errorRef = useRef<HTMLDivElement | null>(null);

  // The dialog body scrolls; a validation error must never stay off-screen, or
  // Save looks like it did nothing.
  useEffect(() => {
    if (formError) errorRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [formError]);

  async function save() {
    const invalid = validateForm(form);
    if (invalid) return setFormError(invalid);

    setSaving(true);
    setFormError(null);
    try {
      const d = await adminPostJson<LlmActionResponse>(`${BASE}/save`, formToPayload(form));
      if (d.status !== "success") throw new Error(d.message || "Save failed");
      onSaved();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <AdminModal
      title={editing ? `Edit ${editing.name}` : "New Connection"}
      open
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="aa-btn aa-btn-primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      {formError && (
        <div className="aa-error" role="alert" ref={errorRef} data-testid="llm-form-error">
          {formError}
        </div>
      )}

      <div className="aa-admin-grid2">
        <AdminField label="Name">
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="My LLM"
          />
        </AdminField>
        <AdminField label="Base URL">
          <input
            type="text"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="https://api.openai.com/v1"
          />
        </AdminField>
        <AdminField label="Model">
          <input
            type="text"
            value={form.model_name}
            onChange={(e) => setForm({ ...form, model_name: e.target.value })}
            placeholder="gpt-4o"
          />
        </AdminField>
        <AdminField label="API Key">
          <input
            type="password"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            placeholder={editing?.api_key_masked ? "••••••••" : "sk-…"}
          />
        </AdminField>
      </div>

      <AdminField label="Model Parameters (JSON)">
        <CodeEditor
          value={form.extraBodyText}
          onChange={(next) => setForm({ ...form, extraBodyText: next })}
          language="json"
          filename="model-parameters.json"
          label="Model parameters"
          height={200}
          testId="llm-extra-body"
        />
      </AdminField>
      {!form.extraBodyText.trim() && (
        <div className="aa-muted" style={{ fontSize: 12, marginTop: -6 }}>
          <button
            type="button"
            className="aa-link"
            onClick={() => setForm({ ...form, extraBodyText: EXTRA_BODY_EXAMPLE })}
          >
            Insert an example
          </button>
        </div>
      )}

      <AdminField label="HTTP Headers">
        <HttpHeadersEditor rows={form.headers} onChange={(headers) => setForm({ ...form, headers })} />
      </AdminField>

      <div style={{ display: "flex", gap: 24, marginTop: 4, flexWrap: "wrap" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={form.verify_ssl}
            onChange={(e) => setForm({ ...form, verify_ssl: e.target.checked })}
          />
          <span>Verify SSL certificate</span>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          <span>Enabled</span>
        </label>
        <label
          style={{ display: "flex", alignItems: "center", gap: 8 }}
          title={canSetDefault ? undefined : "Administrator only"}
        >
          <input
            type="checkbox"
            checked={form.is_default}
            disabled={!canSetDefault}
            onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
          />
          <span>
            Default
            {!canSetDefault && <span className="aa-muted"> (administrator only)</span>}
          </span>
        </label>
      </div>

      <AdminField label="System Instruction">
        <CodeEditor
          value={form.system_instruction}
          onChange={(next) => setForm({ ...form, system_instruction: next })}
          language="markdown"
          filename="system-instruction.md"
          label="System instruction"
          height={140}
          testId="llm-system-instruction"
        />
      </AdminField>
    </AdminModal>
  );
}
