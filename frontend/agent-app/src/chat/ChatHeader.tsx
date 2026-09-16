import { FolderOpen } from "lucide-react";
import type { AgentDef, LlmModel } from "../types";
import type { MeInfo } from "./SettingsPanel";

export function ChatHeader({
  sidebarCollapsed,
  onToggleSidebar,
  agent,
  model,
  streaming,
  onOpenSpotlight,
  onOpenModels,
  onOpenSettings,
  me,
  fileCount,
  filesOpen,
  onToggleFiles,
}: {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  agent: AgentDef | null;
  model: LlmModel | null;
  streaming: boolean;
  onOpenSpotlight: () => void;
  onOpenModels: () => void;
  onOpenSettings: () => void;
  me: MeInfo | null;
  fileCount: number;
  filesOpen: boolean;
  onToggleFiles: () => void;
}) {
  return (
    <header className="aa-header">
      <div className="aa-header-left">
        <button
          type="button"
          className={`aa-sidebar-toggle${sidebarCollapsed ? "" : " hidden"}`}
          data-testid="rail-expand"
          aria-label="Open chat list"
          title="Open chat list"
          onClick={onToggleSidebar}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <line x1="9" y1="3" x2="9" y2="21" />
            <path d="M15 9l-3 3 3 3" />
          </svg>
        </button>
        <button
          type="button"
          className="aa-header-agent"
          onClick={onOpenSpotlight}
          aria-label={agent ? `Change agent ${agent.name}` : "Select agent"}
        >
          {agent ? (
            <span className="aa-chip aa-agent-chip" data-testid="agent-chip">
              <span className="aa-avatar aa-avatar-sm">{agent.name.slice(0, 1).toUpperCase()}</span>
              {agent.name}
              <span className="aa-badge">{agent.kind === "workflow" ? "Team" : "Agent"}</span>
            </span>
          ) : (
            <span className="aa-header-agent-empty">Select an agent</span>
          )}
        </button>
        <button
          type="button"
          className="aa-header-agent"
          onClick={onOpenModels}
          aria-label={model ? `Change model ${model.name}` : "Select model"}
        >
          {model ? (
            <span className="aa-chip aa-agent-chip" data-testid="model-chip">
              {model.name}
              {model.is_default ? <span className="aa-badge">Default</span> : null}
            </span>
          ) : (
            <span className="aa-header-agent-empty" data-testid="model-chip">
              Select a model
            </span>
          )}
        </button>
        {streaming && (
          <span className="aa-status running" data-testid="streaming-status">
            streaming
          </span>
        )}
      </div>
      <div className="aa-header-right">
        <button
          type="button"
          className={`aa-btn aa-btn-ghost aa-files-toggle${filesOpen ? " active" : ""}`}
          data-testid="toggle-files"
          aria-pressed={filesOpen}
          aria-label="Show files created by the agent"
          title="Files created by the agent"
          onClick={onToggleFiles}
        >
          <FolderOpen size={14} strokeWidth={1.8} />
          Files
          {fileCount > 0 && <span className="aa-files-count">{fileCount}</span>}
        </button>
        <button
          type="button"
          className="aa-profile-btn"
          data-testid="profile-button"
          aria-label="Open settings"
          onClick={onOpenSettings}
        >
          <span className="aa-avatar aa-avatar-sm">{(me?.username || "U").slice(0, 1).toUpperCase()}</span>
        </button>
      </div>
    </header>
  );
}
