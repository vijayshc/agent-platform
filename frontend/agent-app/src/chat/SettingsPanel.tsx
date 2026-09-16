import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  Book,
  ChevronDown,
  Cpu,
  Database,
  FolderOpen,
  Gauge,
  KeyRound,
  LibraryBig,
  List,
  LogOut,
  Search,
  Server,
  Settings,
  Shield,
  Terminal,
  User,
  Users,
  Workflow,
} from "lucide-react";

export interface AdminMenuItem {
  key?: string;
  label: string;
  href: string;
  icon: string;
}

export interface MeInfo {
  user_id?: number | null;
  username?: string | null;
  email?: string | null;
  is_admin?: boolean;
  roles?: string[];
  modules?: string[];
  module_levels?: Record<string, "read" | "write">;
  admin_menu?: AdminMenuItem[];
  browser_llm_proxy_enabled_default?: boolean;
  browser_llm_proxy_url?: string;
}

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
  list: List,
};

const THEMES = [
  { key: "dark", label: "Dark Theme" },
  { key: "light", label: "Light Theme" },
  { key: "lightColored", label: "Light Colored" },
];

const LLM_ROUTE_STORAGE = "text2sql.llmRoutingMode";

function readTheme(): string {
  try {
    return (
      (window as unknown as { themeManager?: { currentTheme?: string } }).themeManager?.currentTheme ||
      localStorage.getItem("selectedTheme") ||
      "light"
    );
  } catch {
    return "light";
  }
}

function applyTheme(key: string) {
  const tm = (window as unknown as { themeManager?: { switchTheme: (t: string) => void } }).themeManager;
  if (tm && typeof tm.switchTheme === "function") {
    tm.switchTheme(key);
    return;
  }
  // Fallback: set body class + CSS vars via the global preload file.
  try {
    document.documentElement.classList.remove("theme-dark", "theme-light", "theme-lightColored");
    document.documentElement.classList.add(`theme-${key}`);
    document.body.classList.remove("theme-dark", "theme-light", "theme-lightColored");
    document.body.classList.add(`theme-${key}`);
    localStorage.setItem("selectedTheme", key);
    sessionStorage.setItem("selectedTheme", key);
  } catch {
    /* ignore */
  }
}

export function SettingsPanel({
  open,
  onClose,
  me,
}: {
  open: boolean;
  onClose: () => void;
  me: MeInfo | null;
}) {
  const [theme, setTheme] = useState(readTheme());
  const [llmMode, setLlmMode] = useState<string>(() => {
    try {
      return localStorage.getItem(LLM_ROUTE_STORAGE) || (me?.browser_llm_proxy_enabled_default ? "browser_proxy" : "backend");
    } catch {
      return me?.browser_llm_proxy_enabled_default ? "browser_proxy" : "backend";
    }
  });
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onPointer(e: PointerEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open, onClose]);

  if (!open) return null;

  const username = me?.username || "User";
  const initial = (username || "?").slice(0, 1).toUpperCase();
  const menu = me?.admin_menu || [];

  const switchTheme = (key: string) => {
    setTheme(key);
    applyTheme(key);
  };

  const setLlm = (value: string) => {
    setLlmMode(value);
    try {
      localStorage.setItem(LLM_ROUTE_STORAGE, value);
    } catch {
      /* ignore */
    }
    // Mirror the Flask #globalLlmRoutingSelect so server-side consumer code
    // (knowledge/metadata/llm_engine) reads the same stored mode.
    const flaskSelect = document.getElementById("globalLlmRoutingSelect") as HTMLSelectElement | null;
    if (flaskSelect) {
      flaskSelect.value = value;
      flaskSelect.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };

  return createPortal(
    <div className="aa-overlay aa-settings-overlay" data-testid="settings-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="aa-settings" ref={panelRef} role="dialog" aria-modal="true" aria-label="Settings" data-testid="settings-panel">
        <div className="aa-settings-head">
          <div className="aa-settings-avatar">{initial}</div>
          <div className="aa-settings-id">
            <div className="aa-settings-name" data-testid="settings-username">
              {username}
            </div>
            {me?.email && <div className="aa-settings-email">{me.email}</div>}
          </div>
          <button type="button" className="aa-settings-close" aria-label="Close settings" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="aa-settings-scroll">
          {menu.length > 0 && (
            <div className="aa-settings-section">
              <div className="aa-settings-section-title">Administration</div>
              <div className="aa-settings-rows">
                {menu.map((item) => {
                  const Icon = ICON_MAP[item.icon] || Settings;
                  return (
                    <a key={item.href} className="aa-settings-link" href={item.href} data-testid="settings-admin-link">
                      <Icon size={15} /> {item.label}
                      {item.key && me?.module_levels?.[item.key] === "read" && (
                        <span className="aa-rail-access-badge">read</span>
                      )}
                    </a>
                  );
                })}
              </div>
            </div>
          )}

          <div className="aa-settings-section">
            <div className="aa-settings-section-title">Appearance</div>
            <div className="aa-settings-rows">
              <div className="aa-settings-field">
                <label>Theme</label>
                <div className="aa-theme-grid" data-testid="theme-options">
                  {THEMES.map((t) => (
                    <button
                      key={t.key}
                      type="button"
                      className={`aa-theme-swatch${theme === t.key ? " active" : ""}`}
                      data-theme={t.key}
                      data-testid={`theme-option-${t.key}`}
                      aria-label={t.label}
                      title={t.label}
                      onClick={() => switchTheme(t.key)}
                    >
                      <span className="aa-theme-swatch-dot" data-swatch={t.key} />
                      <span className="aa-theme-tooltip">{t.label}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="aa-settings-field">
                <label htmlFor="settings-llm-select">LLM Routing</label>
                <div className="aa-llm-select-wrap">
                  <Cpu size={15} />
                  <select
                    id="settings-llm-select"
                    name="llm_routing"
                    className="aa-settings-select"
                    value={llmMode}
                    data-testid="settings-llm-select"
                    onChange={(e) => setLlm(e.target.value)}
                  >
                    <option value="backend">Backend API</option>
                    <option value="browser_proxy">Local Browser Proxy</option>
                  </select>
                  <ChevronDown size={14} />
                </div>
              </div>
            </div>
          </div>

          <div className="aa-settings-section">
            <div className="aa-settings-section-title">Account</div>
            <div className="aa-settings-rows">
              <a className="aa-settings-link" href="/change-password">
                <KeyRound size={15} /> Change Password
              </a>
              <a className="aa-settings-link" href="/logout">
                <LogOut size={15} /> Logout
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
