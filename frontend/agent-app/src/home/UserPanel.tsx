import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  Book,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  KeyRound,
  LibraryBig,
  LogOut,
  MessageSquare,
  Search,
  Server,
  Settings,
  Shield,
  Terminal,
  Users,
  Workflow,
  X,
} from "lucide-react";
import "./UserPanel.css";

export interface HomeAdminMenuItem {
  key?: string;
  label: string;
  href: string;
  icon: string;
}

/** Compact reuse of the AdminShell.tsx ICON_MAP pattern (same icon names). */
const ICON_MAP: Record<string, typeof Gauge> = {
  gauge: Gauge,
  users: Users,
  shield: Shield,
  server: Server,
  activity: Activity,
  workflow: Workflow,
  book: Book,
  search: Search,
  library: LibraryBig,
  database: Database,
  folder: FolderOpen,
  terminal: Terminal,
  settings: Settings,
  cpu: Cpu,
};

const THEME_OPTIONS = [
  { key: "dark", label: "Dark" },
  { key: "light", label: "Light" },
  { key: "lightColored", label: "Light colored" },
] as const;

function currentThemeName(): string {
  const classes = document.documentElement.classList;
  if (classes.contains("theme-lightColored")) return "lightColored";
  if (classes.contains("theme-light")) return "light";
  return "dark";
}

/** Same pattern as studio/StudioToolbar.tsx: apply through the app ThemeManager. */
function applyAppTheme(name: string): void {
  const manager = (window as unknown as { themeManager?: { applyTheme?: (theme: string) => void } })
    .themeManager;
  if (manager?.applyTheme) {
    manager.applyTheme(name);
    return;
  }
  const root = document.documentElement;
  [...root.classList, ...document.body.classList]
    .filter((token) => token.startsWith("theme-"))
    .forEach((token) => {
      root.classList.remove(token);
      document.body.classList.remove(token);
    });
  root.classList.add(`theme-${name}`);
  document.body.classList.add(`theme-${name}`);
  sessionStorage.setItem("selectedTheme", name);
  localStorage.setItem("selectedTheme", name);
}

export function UserPanel({
  open,
  username,
  email,
  menu,
  onClose,
}: {
  open: boolean;
  username: string;
  email?: string | null;
  menu: HomeAdminMenuItem[];
  onClose: () => void;
}) {
  const [theme, setTheme] = useState<string>(() => currentThemeName());
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    setTheme(currentThemeName());
    closeRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const initial = (username || "?").slice(0, 1).toUpperCase();

  const switchTheme = (key: string) => {
    setTheme(key);
    applyAppTheme(key);
  };

  return createPortal(
    <div
      className="aa-home-panel-overlay"
      data-testid="home-user-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside
        ref={panelRef}
        className="aa-home-panel"
        role="dialog"
        aria-modal="true"
        aria-label="User menu"
        data-testid="home-user-panel"
      >
        <div className="aa-home-panel-head">
          <div className="aa-home-panel-avatar" aria-hidden="true">
            {initial}
          </div>
          <div className="aa-home-panel-id">
            <div className="aa-home-panel-name" data-testid="home-user-name">
              {username}
            </div>
            {email ? <div className="aa-home-panel-email">{email}</div> : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            className="aa-home-panel-close"
            aria-label="Close user menu"
            data-testid="home-user-close"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </div>

        <div className="aa-home-panel-scroll">
          <nav className="aa-home-panel-pinned" aria-label="Chats">
            <a
              className="aa-home-panel-link aa-home-panel-link-primary"
              href="/agent"
              data-testid="home-chats-link"
            >
              <MessageSquare size={15} aria-hidden="true" />
              <span>Chats</span>
            </a>
          </nav>
          {menu.length > 0 ? (
            <section className="aa-home-panel-section" aria-label="Administration">
              <h2 className="aa-home-panel-title">Administration</h2>
              <nav className="aa-home-panel-links">
                {menu.map((item) => {
                  const Icon = ICON_MAP[item.icon] || Gauge;
                  return (
                    <a
                      key={item.href}
                      className="aa-home-panel-link"
                      href={item.href}
                      data-testid="home-admin-link"
                    >
                      <Icon size={15} aria-hidden="true" />
                      <span>{item.label}</span>
                    </a>
                  );
                })}
              </nav>
            </section>
          ) : null}

          <section className="aa-home-panel-section" aria-label="Appearance">
            <h2 className="aa-home-panel-title">Appearance</h2>
            <div className="aa-home-panel-swatches" role="radiogroup" aria-label="Theme">
              {THEME_OPTIONS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="radio"
                  aria-checked={theme === t.key}
                  aria-label={`${t.label} theme`}
                  title={t.label}
                  className={`aa-home-panel-swatch${theme === t.key ? " active" : ""}`}
                  data-testid={`home-theme-${t.key}`}
                  onClick={() => switchTheme(t.key)}
                >
                  <span className="aa-home-panel-dot" data-swatch={t.key} aria-hidden="true" />
                </button>
              ))}
            </div>
          </section>

          <section className="aa-home-panel-section" aria-label="Account">
            <h2 className="aa-home-panel-title">Account</h2>
            <nav className="aa-home-panel-links">
              <a className="aa-home-panel-link" href="/change-password">
                <KeyRound size={15} aria-hidden="true" />
                <span>Change password</span>
              </a>
              <a className="aa-home-panel-link" href="/logout" data-testid="home-signout">
                <LogOut size={15} aria-hidden="true" />
                <span>Sign out</span>
              </a>
            </nav>
          </section>
        </div>
      </aside>
    </div>,
    document.body,
  );
}
