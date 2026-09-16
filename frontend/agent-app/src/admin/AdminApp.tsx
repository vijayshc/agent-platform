import { AdminShell } from "./AdminShell";
import { DashboardPage } from "./pages/DashboardPage";
import { UsersPage } from "./pages/UsersPage";
import { RolesPage } from "./pages/RolesPage";
import { McpServersPage } from "./pages/McpServersPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { VectorDbPage } from "./vector/VectorDbPage";
import { FileBrowserPage } from "./pages/FileBrowserPage";
import { DatabasePage } from "./pages/DatabasePage";
import { LlmManagerPage } from "./pages/LlmManagerPage";
import { SkillsPage } from "./skills/SkillsPage";
import { HostedAppsPage } from "./pages/HostedAppsPage";

export function AdminApp() {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";

  let content: React.ReactNode;
  if (path === "/admin" || path === "/admin/") {
    content = <DashboardPage />;
  } else if (path.startsWith("/admin/users")) {
    content = <UsersPage />;
  } else if (path.startsWith("/admin/roles")) {
    content = <RolesPage />;
  } else if (path.startsWith("/admin/mcp-servers")) {
    content = <McpServersPage />;
  } else if (path.startsWith("/admin/knowledge")) {
    content = <KnowledgePage />;
  } else if (path.startsWith("/admin/vector-db")) {
    content = <VectorDbPage />;
  } else if (path.startsWith("/admin/file-browser")) {
    content = <FileBrowserPage />;
  } else if (path.startsWith("/admin/database")) {
    content = <DatabasePage />;
  } else if (path.startsWith("/admin/config/llm")) {
    content = <LlmManagerPage />;
  } else if (path.startsWith("/admin/hosted-apps")) {
    content = <HostedAppsPage />;
  } else if (path === "/skills" || path.startsWith("/admin/skills")) {
    content = <SkillsPage />;
  } else {
    content = <DashboardPage />;
  }

  return <AdminShell>{content}</AdminShell>;
}
