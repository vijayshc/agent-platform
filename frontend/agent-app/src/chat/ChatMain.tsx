import type { RefObject } from "react";
import type { AgentDef, ChatMessage, LlmModel, SseEvent } from "../types";
import type { HitlDecision } from "../shared/hitl";
import type { MeInfo } from "./SettingsPanel";
import { ChatHeader } from "./ChatHeader";
import { Composer } from "./Composer";
import { MessageBubble } from "./MessageBubble";
import { groupMessagesIntoTurns } from "./chatLogic";
import { WorkspaceFiles } from "./WorkspaceFiles";

export function ChatMain({
  sidebarCollapsed,
  onToggleSidebar,
  agent,
  model,
  streaming,
  onOpenSpotlight,
  onOpenModels,
  onOpenSettings,
  me,
  empty,
  draft,
  setDraft,
  files,
  setFiles,
  onSend,
  onStop,
  composerRef,
  fileRef,
  addFiles,
  canSend,
  messages,
  transcriptRef,
  onTranscriptScroll,
  turnStartRef,
  transcriptHeight,
  bottomRef,
  onDecide,
  onSettle,
  error,
  conversationId,
  filesOpen,
  onToggleFiles,
  filesRefreshKey,
  filesSince,
  fileCount,
  onFileCount,
  live,
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
  empty: boolean;
  draft: string;
  setDraft: (v: string) => void;
  files: File[];
  setFiles: (files: File[] | ((p: File[]) => File[])) => void;
  onSend: () => void;
  onStop: () => void;
  composerRef: RefObject<HTMLTextAreaElement>;
  fileRef: RefObject<HTMLInputElement>;
  addFiles: (list: FileList | File[]) => void;
  canSend: boolean;
  messages: ChatMessage[];
  transcriptRef: RefObject<HTMLDivElement>;
  onTranscriptScroll: () => void;
  turnStartRef: RefObject<HTMLDivElement>;
  transcriptHeight: number;
  bottomRef: RefObject<HTMLDivElement>;
  onDecide: (msg: ChatMessage, ev: SseEvent, decisions: HitlDecision[]) => void;
  onSettle: (id: string) => void;
  error: string | null;
  conversationId: string | null;
  filesOpen: boolean;
  onToggleFiles: () => void;
  filesRefreshKey: number;
  filesSince: number | null;
  fileCount: number;
  onFileCount: (n: number) => void;
  live: boolean;
}) {
  const composer = (
    <Composer
      draft={draft}
      setDraft={setDraft}
      files={files}
      setFiles={setFiles}
      onSend={onSend}
      onStop={onStop}
      composerRef={composerRef}
      fileRef={fileRef}
      addFiles={addFiles}
      streaming={streaming}
      canSend={canSend}
      centered={empty}
    />
  );

  const panel = (
    <WorkspaceFiles
      conversationId={conversationId}
      open={filesOpen}
      onClose={onToggleFiles}
      refreshKey={filesRefreshKey}
      since={filesSince}
      onCount={onFileCount}
      live={live}
    />
  );

  return (
    <>
    <section className="aa-main">
      {/* placement: panel sits beside the transcript */}
      <ChatHeader
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
        agent={agent}
        model={model}
        streaming={streaming}
        onOpenSpotlight={onOpenSpotlight}
        onOpenModels={onOpenModels}
        onOpenSettings={onOpenSettings}
        me={me}
        fileCount={fileCount}
        filesOpen={filesOpen}
        onToggleFiles={onToggleFiles}
      />

      {empty ? (
        <div className="aa-empty">
          <div className="aa-empty-heading">What should we explore?</div>
          {composer}
        </div>
      ) : (
        <>
          <div className="aa-transcript" data-testid="transcript" ref={transcriptRef} onScroll={onTranscriptScroll}>
            <div className="aa-transcript-inner">
              {groupMessagesIntoTurns(messages).map((turn) => (
                <div
                  key={turn.id}
                  ref={turn.isLatest ? turnStartRef : undefined}
                  className={`aa-chat-turn${turn.isLatest ? " aa-chat-turn-latest" : ""}`}
                  style={turn.isLatest && transcriptHeight ? { minHeight: `${Math.max(200, transcriptHeight - 36)}px` } : undefined}
                >
                  {turn.messages.map((m) => (
                    <MessageBubble
                      key={m.id}
                      msg={m}
                      agentName={agent?.name}
                      onDecide={(ev, decisions) => onDecide(m, ev, decisions)}
                      streaming={streaming}
                      onSettle={onSettle}
                    />
                  ))}
                </div>
              ))}
              <div ref={bottomRef} />
            </div>
          </div>
          {composer}
        </>
      )}
      {error && (
        <div className="aa-error aa-chat-error" data-testid="chat-error">
          {error}
        </div>
      )}
    </section>
    {panel}
    </>
  );
}
