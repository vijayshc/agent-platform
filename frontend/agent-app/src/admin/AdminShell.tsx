import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  Book,
  Bot,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  LibraryBig,
  Server,
  Shield,
  Terminal,
  Users,
  Workflow,
} from "lucide-react";

import { adminGet } from "./adminShared";

export interface AdminNavItem {
  key?: string;
  label: string;
  href: string;
  icon: string;
}

const ICON_MAP: Record<string, typeof Gauge> = {
  gauge: Gauge,
  users: Users,
  shield: Shield,
  server: Server,
  activity: Activity,
  workflow: Workflow,
  book: Book,
  library: LibraryBig,
  database: Database,
  folder: FolderOpen,
  terminal: Terminal,
  cpu: Cpu,
};

function isActive(href: string, path: string): boolean {
  if (href === "/admin") return path === "/admin" || path === "/admin/";
  if (href === "/admin/config/llm") return path === "/admin/config/llm" || path.startsWith("/admin/config/llm/");
  if (href.endsWith("/")) return path === href.replace(/\/$/, "") || path.startsWith(href.replace(/\/$/, ""));
  return path === href || path.startsWith(`${href}/`);
}

function titleFor(path: string): string {
  if (path === "/admin" || path === "/admin/") return "Dashboard";
  if (path.startsWith("/admin/users")) return "Users";
  if (path.startsWith("/admin/roles")) return "Roles & Permissions";
  if (path.startsWith("/admin/mcp-servers")) return "MCP Servers";
  if (path.startsWith("/admin/knowledge")) return "Knowledge Management";
  if (path.startsWith("/admin/vector-db")) return "Vector Database";
  if (path.startsWith("/admin/file-browser")) return "File Browser";
  if (path.startsWith("/admin/database")) return "Database Query Editor";
  if (path.startsWith("/admin/config/llm")) return "LLM Manager";
  if (path.startsWith("/admin/skills") || path === "/skills") return "Skill Library";
  return "Admin";
}

export function AdminShell({ children, shellClass }: { children: ReactNode; title?: string; shellClass?: string }) {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("aa_admin_rail_collapsed") === "true";
    } catch {
      return false;
    }
  });
  // Both rail controls (the rail's collapse button and the floating expand
  // button) write through here. Persisting in only one of them left storage on
  // "collapsed" after an expand, so the next route remounted the shell
  // collapsed — the state must round-trip through storage, not just React.
  const setRailCollapsed = useCallback((next: boolean) => {
    setCollapsed(next);
    try {
      localStorage.setItem("aa_admin_rail_collapsed", String(next));
    } catch {}
  }, []);
  // The server is the source of truth: /api/v1/me returns only the modules the
  // signed-in user may use, together with READ/WRITE level per module.
  const [menu, setMenu] = useState<AdminNavItem[]>([]);
  const [moduleLevels, setModuleLevels] = useState<Record<string, "read" | "write">>({});

  useEffect(() => {
    let cancelled = false;
    adminGet<{ admin_menu?: AdminNavItem[]; module_levels?: Record<string, "read" | "write"> }>(
      "/api/v1/me",
    )
      .then((data) => {
        if (cancelled) return;
        setMenu(data.admin_menu || []);
        setModuleLevels(data.module_levels || {});
      })
      .catch(() => {
        if (!cancelled) {
          setMenu([]);
          setModuleLevels({});
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const currentNav = menu.find((item) => isActive(item.href, path));
  const currentLevel = currentNav?.key ? moduleLevels[currentNav.key] : undefined;
  const readOnlyModule = currentLevel === "read";

  return (
    <div className="aa-root aa-admin-root" data-page="admin">
      <aside className={`aa-admin-rail${collapsed ? " collapsed" : ""}`} data-testid="admin-rail">
        <div className="aa-rail-top">
          <div className="aa-rail-brand-row">
            <a className="aa-rail-brand" href="/" aria-label="Home">
              <Bot size={18} />
            </a>
            <button
              type="button"
              className="aa-rail-icon-btn"
              aria-label="Collapse sidebar"
              title="Collapse sidebar"
              onClick={() => setRailCollapsed(!collapsed)}
            >
              <span className="aa-admin-collapse-glyph">◀</span>
            </button>
          </div>
          <div className="aa-rail-heading">Administration</div>
          <div className="aa-admin-nav">
            {menu.map((item) => {
              const Icon = ICON_MAP[item.icon] || Gauge;
              return (
                <a
                  key={item.href}
                  className={`aa-rail-item${isActive(item.href, path) ? " active" : ""}`}
                  href={item.href}
                  data-testid={`admin-nav-${item.href}`}
                >
                  <span className="aa-rail-item-icon">
                    <Icon size={16} />
                  </span>
                  <span className="aa-rail-item-label">{item.label}</span>
                  {item.key && moduleLevels[item.key] === "read" && (
                    <span className="aa-rail-access-badge">read</span>
                  )}
                </a>
              );
            })}
          </div>
        </div>
        <div className="aa-rail-bottom">
          <a className="aa-rail-item" href="/">
            <span className="aa-rail-item-icon">
              <Bot size={16} />
            </span>
            <span className="aa-rail-item-label">Back to Agent</span>
          </a>
        </div>
      </aside>

      <section className="aa-main aa-admin-main">
        <div className={`aa-admin-content${shellClass ? " " + shellClass : ""}`} data-testid="admin-content">
          {readOnlyModule && currentNav && (
            <div className="aa-readonly-banner" data-testid="module-readonly-banner">
              Read-only access to {currentNav.label}
            </div>
          )}
          {collapsed && (
            <button
              type="button"
              className="aa-sidebar-toggle aa-admin-expand"
              aria-label="Open nav"
              title="Open nav"
              onClick={() => setRailCollapsed(false)}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                <line x1="9" y1="3" x2="9" y2="21" />
                <path d="M15 9l-3 3 3 3" />
              </svg>
            </button>
          )}
          {children}
        </div>
      </section>
    </div>
  );
}
