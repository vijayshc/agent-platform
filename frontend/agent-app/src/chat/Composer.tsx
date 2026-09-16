import { useLayoutEffect, type RefObject } from "react";

export function Composer({
  draft,
  setDraft,
  files,
  setFiles,
  onSend,
  onStop,
  composerRef,
  fileRef,
  addFiles,
  streaming,
  canSend,
  centered,
}: {
  draft: string;
  setDraft: (v: string) => void;
  files: File[];
  setFiles: (files: File[] | ((p: File[]) => File[])) => void;
  onSend: () => void;
  onStop: () => void;
  composerRef: RefObject<HTMLTextAreaElement>;
  fileRef: RefObject<HTMLInputElement>;
  addFiles: (list: FileList | File[]) => void;
  streaming: boolean;
  canSend: boolean;
  centered?: boolean;
}) {
  useLayoutEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 24), 200)}px`;
  }, [draft, composerRef]);

  return (
    <div className={`aa-composer-wrap${centered ? " centered" : ""}`}>
      {files.length > 0 && (
        <div className="aa-file-row">
          {files.map((f, i) => (
            <span className="aa-chip" data-testid="file-chip" key={`${f.name}-${i}`}>
              {f.name}
              <button type="button" aria-label="Remove file" onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="aa-composer" data-testid="agent-composer">
        <button
          type="button"
          className="aa-icon-btn"
          data-testid="plus-button"
          aria-label="Upload file"
          title="Upload file"
          onClick={() => fileRef.current?.click()}
        >
          +
        </button>
        <textarea
          ref={composerRef}
          data-testid="composer-input"
          placeholder="Message"
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPaste={(e) => {
            const pasted = e.clipboardData.files;
            if (pasted && pasted.length) {
              e.preventDefault();
              addFiles(pasted);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (!streaming && canSend) onSend();
            }
          }}
        />
        {streaming ? (
          <button type="button" className="aa-icon-btn stop" data-testid="send-button" aria-label="Stop" onClick={onStop}>
            ■
          </button>
        ) : (
          <button
            type="button"
            className="aa-icon-btn send"
            data-testid="send-button"
            aria-label="Send"
            disabled={!canSend}
            onClick={onSend}
          >
            ↑
          </button>
        )}
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}
