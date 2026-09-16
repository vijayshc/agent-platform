import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api";

/** Mirrors the /api/v1/me auth_provider flag. */
interface MeInfo {
  username?: string;
  email?: string;
  is_ldap?: boolean;
  auth_provider?: string;
}

type AuthScreen = "login" | "reset-request" | "reset-password" | "change-password" | "reauth";

function csrfToken(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
  return meta?.content || "";
}

/** Resolve the path-name-driven auth screen (used on deep links). */
function screenFromPath(): AuthScreen {
  const path = window.location.pathname;
  if (path.startsWith("/auth/") || path.startsWith("/reset-password/")) return "reset-password";
  if (path.startsWith("/change-password")) return "change-password";
  if (path.startsWith("/reset-password-request") || path.startsWith("/auth/reset-password-request")) {
    return "reset-request";
  }
  if (path.startsWith("/reauthenticate")) return "reauth";
  if (path.startsWith("/auth/reset-password/")) return "reset-password";
  return "login";
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken(),
    },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error(String(data.error || data.message || res.statusText));
  return data as T;
}

export function AuthFlow() {
  const [screen, setScreen] = useState<AuthScreen>(screenFromPath);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState<string>(() => {
    const m = window.location.pathname.match(/\/(?:auth\/)?reset-password\/([^/?#]+)/);
    return m?.[1] || "";
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ldap, setLdap] = useState(false);

  useEffect(() => {
    apiGet<MeInfo>("/api/v1/me")
      .then((me) => setLdap(Boolean(me.is_ldap)))
      .catch(() => setLdap(false));
  }, []);

  const go = useCallback((next: AuthScreen) => {
    setError(null);
    setScreen(next);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      if (screen === "login") {
        const res = await postJson<{ redirect?: string; error?: string }>("/login", {
          username,
          password,
          next: new URLSearchParams(window.location.search).get("next") || undefined,
        });
        window.location.href = res.redirect || "/";
      } else if (screen === "reset-request") {
        await postJson("/reset-password-request", { username });
        setError(null);
      } else if (screen === "reset-password") {
        await postJson(`/reset-password/${token}`, {
          password: newPassword,
          password_confirm: confirm,
        });
        // Reset succeeded; offer login.
        setError(null);
        go("login");
        window.location.href = "/login";
      } else if (screen === "change-password") {
        await postJson("/change-password", {
          current_password: password,
          new_password: newPassword,
          confirm_password: confirm,
        });
        window.location.href = "/";
      } else if (screen === "reauth") {
        const res = await postJson<{ redirect?: string; error?: string }>("/reauthenticate", {
          password,
          _csrf_token: csrfToken(),
        });
        window.location.href = res.redirect || "/";
      }
    } catch (err) {
      setError(String((err as Error).message || err));
    } finally {
      setBusy(false);
    }
  }

  const title = {
    login: "Sign in",
    "reset-request": "Reset password",
    "reset-password": "Set a new password",
    "change-password": "Change password",
    reauth: "Confirm your password",
  }[screen];

  return (
    <div className="aa-root aa-auth-screen" data-page="auth">
      <div className="aa-auth-card">
        <div className="aa-auth-brand">
          <span className="aa-auth-brand-icon">T</span>
          <div>
            <div className="aa-auth-brand-title">Agent Platform</div>
            {screen !== "login" && (
              <div className="aa-auth-brand-sub">{title}</div>
            )}
          </div>
        </div>

        {error && <div className="aa-auth-error">{error}</div>}

        <form onSubmit={submit} className="aa-auth-form">
          {screen === "login" && (
            <>
              <label className="aa-field">
                <span>Username</span>
                <input
                  id="username"
                  autoFocus
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </label>
              <label className="aa-field">
                <span>Password</span>
                <input
                  id="password"
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              {!ldap && (
                <button
                  type="button"
                  className="aa-link"
                  onClick={() => go("reset-request")}
                >
                  Forgot password?
                </button>
              )}
              <button className="aa-btn aa-btn-primary" type="submit" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </>
          )}

          {screen === "reset-request" && (
            <>
              <label className="aa-field">
                <span>Username</span>
                <input
                  autoFocus
                  name="username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </label>
              <p className="aa-hint">
                We'll send password reset instructions to the email on file.
              </p>
              <button className="aa-btn aa-btn-primary" type="submit" disabled={busy}>
                {busy ? "Sending…" : "Send reset instructions"}
              </button>
              <button type="button" className="aa-link" onClick={() => go("login")}>
                Back to sign in
              </button>
            </>
          )}

          {screen === "reset-password" && (
            <>
              <label className="aa-field">
                <span>New password</span>
                <input
                  autoFocus
                  type="password"
                  name="new_password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </label>
              <label className="aa-field">
                <span>Confirm password</span>
                <input
                  type="password"
                  name="password_confirm"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                />
              </label>
              <button className="aa-btn aa-btn-primary" type="submit" disabled={busy}>
                {busy ? "Saving…" : "Save new password"}
              </button>
              <button type="button" className="aa-link" onClick={() => go("login")}>
                Back to sign in
              </button>
            </>
          )}

          {screen === "change-password" && (
            <>
              <label className="aa-field">
                <span>Current password</span>
                <input
                  autoFocus
                  type="password"
                  name="current_password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              <label className="aa-field">
                <span>New password</span>
                <input
                  type="password"
                  name="new_password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                  minLength={6}
                />
              </label>
              <label className="aa-field">
                <span>Confirm password</span>
                <input
                  type="password"
                  name="confirm_password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                />
              </label>
              <button className="aa-btn aa-btn-primary" type="submit" disabled={busy}>
                {busy ? "Saving…" : "Change password"}
              </button>
            </>
          )}

          {screen === "reauth" && (
            <>
              <p className="aa-hint">
                For security, confirm your password to continue.
              </p>
              <label className="aa-field">
                <span>Password</span>
                <input
                  autoFocus
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              <button className="aa-btn aa-btn-primary" type="submit" disabled={busy}>
                {busy ? "Verifying…" : "Verify"}
              </button>
            </>
          )}
        </form>
      </div>
    </div>
  );
}
