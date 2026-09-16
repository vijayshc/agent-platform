import { RunsPage } from "./runs/RunsPage";
import { ChatPage } from "./chat/ChatPage";
import { AgentListPage } from "./studio/AgentListPage";
import { StudioPage } from "./studio/StudioPage";
import { AdminApp } from "./admin/AdminApp";
import { AdminShell } from "./admin/AdminShell";
import { AuthFlow } from "./auth/AuthFlow";
import { ErrorPage } from "./ErrorPage";

function errorCodeFromGlobal(): number | null {
  const code = (window as unknown as { __ERROR_CODE__?: number }).__ERROR_CODE__;
  return code === 404 || code === 500 || code === 403 ? code : null;
}

export function App() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const errorCode = errorCodeFromGlobal();
  if (errorCode) return <ErrorPage code={errorCode} />;
  if (
    path === "/login" ||
    path === "/change-password" ||
    path === "/reset-password-request" ||
    path.startsWith("/reset-password/") ||
    path === "/reauthenticate"
  ) {
    return <AuthFlow />;
  }
  if (path.startsWith("/admin") || path === "/skills") {
    return <AdminApp />;
  }
  if (path === "/observability" || path.startsWith("/observability/") || path === "/agent-runs" || path.startsWith("/agent-runs/")) {
    return (
      <AdminShell title="Observability" shellClass="aa-admin-shell-embed">
        <RunsPage />
      </AdminShell>
    );
  }
  if (path === "/agent-studio/editor" || path.startsWith("/agent-studio/editor/")) {
    return (
      <AdminShell title="Agent Studio" shellClass="aa-admin-shell-embed">
        <StudioPage />
      </AdminShell>
    );
  }
  if (path === "/agent-studio") {
    return (
      <AdminShell title="Agent Studio" shellClass="aa-admin-shell-embed">
        <AgentListPage />
      </AdminShell>
    );
  }
  return <ChatPage />;
}
