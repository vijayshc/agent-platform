# Chrome Browser Automation on WSL2 (Antigravity Bridge)

This document outlines how to operate, launch, and connect the Antigravity Browser Subagent (running inside WSL2) with Google Chrome running natively on Windows.

> [!IMPORTANT]
> This file covers **launching the bridge**. Once the bridge is up, see
> **[CHROME_WEBTOOL.md](./CHROME_WEBTOOL.md)** for the operational reference:
> the exact Chrome DevTools MCP tool names/signatures, gotchas, login URL, and
> the single-shot UI-navigation recipe.

---

## ⚡ Quick 1-Command Setup (Run directly from WSL2)

You do **not** need to switch to Windows manually. Run this single automated script from the repository root:

```bash
./scripts/setup_chrome_bridge.sh http://localhost:5000/
```

This script automatically:
1. Verifies prerequisites and terminates any lingering non-debug Chrome processes on Windows.
2. Launches Chrome on Windows with `--remote-debugging-port=9222` and `--remote-debugging-address=0.0.0.0`.
3. Detects the Windows host IP and establishes the `socat` TCP bridge (`127.0.0.1:9222 -> WIN_IP:9222`).
4. Verifies the CDP connection via `http://127.0.0.1:9222/json/version` and confirms readiness.

> [!WARNING]
> **Step 1 force-kills EVERY Chrome window on Windows.** Line 28 runs
> `Stop-Process -Name chrome -Force`, so it also closes the user's own browser —
> including this DSH Web GUI tab if they are viewing it in Chrome. **Prefer the
> non-destructive launch below** unless the user has explicitly said it is fine to
> close their browser.

---

## ✅ Non-Destructive Launch (Preferred)

Start a *separate* debug instance with its own profile — the user's existing
windows stay open. Only skip the `Stop-Process`; everything else is unchanged:

```bash
# 1. Launch a dedicated debug instance (own profile, no killing)
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList '--remote-debugging-port=9222','--user-data-dir=C:\tmp\chrome-debug','--no-first-run','--no-default-browser-check','http://localhost:5000/login'"

# 2. Refresh the socat tunnel
WIN_IP=$(ip route show | grep -i default | awk '{ print $3 }')
killall socat 2>/dev/null || true
nohup socat TCP-LISTEN:9222,fork,reuseaddr TCP:$WIN_IP:9222 >/dev/null 2>&1 &

# 3. Check readiness with the MCP (list_pages), NOT with curl — see the next section
```

To later close **only** the debug instance (never the user's windows):

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { \$_.CommandLine -match 'chrome-debug' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
```

---

## 🚨 `curl http://127.0.0.1:9222/json/version` Fails — Two Independent Causes

Observed on this box on 2026-09-12. **Neither one means the MCP is broken.**

1. **Chrome 152 binds CDP to IPv6 loopback only.** Even passing
   `--remote-debugging-address=0.0.0.0` *and* `--remote-debugging-address=127.0.0.1`,
   `netstat -ano | Select-String ':9222'` showed only
   `TCP [::1]:9222 [::]:0 LISTENING <chrome pid>`. WSL reaches Windows through the
   portproxy, which targets **IPv4**:
   `netsh interface portproxy show all` → `172.27.240.1 9222 -> 127.0.0.1 9222`.
   So connections from WSL terminate in `TIME_WAIT`/RST and curl returns
   empty / `exit 28` (timeout) / `exit 52` (reset by peer).
   Repointing the portproxy at `::1` requires **Administrator**, which this box does
   not have (`IsInRole(Administrator)` → `False`), so on this machine the raw curl
   check simply cannot pass.
2. **The raw endpoint is not required by the MCP at all** (see the note above).

**Therefore: judge bridge readiness by calling the MCP `list_pages`.** If it
returns pages, the bridge is live. Do **not** re-run `setup_chrome_bridge.sh` —
and especially do not kill Chrome — just because curl failed.

---

## 🔧 Manual 1-Liner (Alternative)

> [!WARNING]
> The command below also starts with `Stop-Process -Name chrome -Force`, which
> closes **all** of the user's Chrome windows. Drop that first statement (and keep
> `--user-data-dir=C:\tmp\chrome-debug`) to use the non-destructive variant above.

If you prefer running the commands inline without the script:

```bash
# 1. Terminate lingering Chrome processes on Windows & start fresh instance with remote debugging
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList '--remote-debugging-port=9222','--remote-debugging-address=0.0.0.0','--user-data-dir=C:\tmp\chrome-debug','http://localhost:5000/'"

# 2. Start / refresh the WSL2-to-Windows socat tunnel
WIN_IP=$(ip route show | grep -i default | awk '{ print $3}')
killall socat 2>/dev/null || true
socat TCP-LISTEN:9222,fork,reuseaddr TCP:$WIN_IP:9222 &
sleep 2

# 3. Verify CDP connection
curl -s http://127.0.0.1:9222/json/version
```

If the `curl` output returns JSON containing `"webSocketDebuggerUrl"`, browser automation is immediately ready!

> [!IMPORTANT]
> **Failure of the raw `curl` bridge does NOT mean the Chrome DevTools MCP is down.**
> The MCP toolserver holds its **own** persistent connection to Chrome and does **not**
> depend on the `socat`/port-9222 raw CDP endpoint being reachable *from this shell*.
> If `curl http://127.0.0.1:9222/json/version` returns an empty reply / `exit 52`
> ("Connection reset by peer" / "Recv failure"), **do not assume the browser is
> broken.** Verify the live browser state via the MCP `list_pages` / `take_snapshot`
> tools (see `CHROME_WEBTOOL.md`) before re-running setup. Only re-run the bridge
> if the MCP tools themselves are unreachable.

---

## 📋 Step-by-Step Workflow (Manual Breakdown)

If you prefer to run the steps individually:

### Step 1: Launch Chrome with Remote Debugging
You can launch it from **WSL2** or directly in **Windows CMD/PowerShell**:

- **From WSL2 Terminal (Recommended):**
  ```bash
  /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList '--remote-debugging-port=9222','--remote-debugging-address=0.0.0.0','--user-data-dir=C:\tmp\chrome-debug','http://localhost:5000/'"
  ```

- **From Windows CMD / PowerShell:**
  ```powershell
  Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
  Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList "--remote-debugging-port=9222","--remote-debugging-address=0.0.0.0","--user-data-dir=C:\tmp\chrome-debug","http://localhost:5000/"
  ```

> [!IMPORTANT]
> The `--remote-debugging-address=0.0.0.0` flag ensures Chrome binds to IPv4 correctly so Windows `portproxy` and `socat` can connect without IPv6 localhost conflicts.

---

### Step 2: Establish the WSL2 `socat` Tunnel
Run in your WSL2 terminal:
```bash
WIN_IP=$(ip route show | grep -i default | awk '{ print $3}')
killall socat 2>/dev/null || true
socat TCP-LISTEN:9222,fork,reuseaddr TCP:$WIN_IP:9222 &
```
*(Tip: You can place this in your `~/.bashrc` to auto-forward on terminal startup).*

---

### Step 3: Verify Connection from WSL2
```bash
curl -s http://127.0.0.1:9222/json/version
```

> [!NOTE]
> On this machine (Chrome 152, no Administrator) this curl **cannot succeed** —
> Chrome listens on `[::1]:9222` while the portproxy targets IPv4 `127.0.0.1:9222`.
> That is expected and harmless: confirm readiness with the MCP `list_pages`
> instead. See "curl Fails — Two Independent Causes" above.

Expected output when it does work:
```json
{
   "Browser": "Chrome/...",
   "Protocol-Version": "1.3",
   "User-Agent": "...",
   "V8-Version": "...",
   "WebKit-Version": "...",
   "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/..."
}
```

To list all open tabs:
```bash
curl -s http://127.0.0.1:9222/json/list
```

---

## 🎯 Target URLs & Authentication

- **App Base URL**: `http://localhost:5000`
- **Admin Dashboard**: `http://localhost:5000/admin` (see `CHROME_WEBTOOL.md` §4.5 for the full admin route list)
- **Agent Studio / App**: `http://localhost:5000/agent`
- **Knowledge Base**: `http://localhost:5000/knowledge`
- **Login URL**: `http://localhost:5000/login`
- **Default Credentials**:
  - Username: `admin`
  - Password: `admin`

> **`admin123` does not work** — verified 2026-09-12 against the live login (returns
> *"Invalid username or password."*) and against the bcrypt hash in `text2sql.db`.
> Only `admin` matches. The submit button is labelled **"Sign in"**, and the login
> POST is async — wait for the authenticated shell before navigating, or you bounce
> back to `/login?next=…` and it looks like a bad password.

---

## 🔍 Troubleshooting & Root Causes

### 1. `Connection reset by peer` / `curl: (56) Recv failure: Connection reset by peer`
- **Root Cause A**: Windows `portproxy` is listening on port 9222 and accepts the connection from WSL, but **Chrome is not running on port 9222** on Windows to receive the forwarded traffic.
- **Fix**: Launch Chrome using the command in Step 1. Ensure Chrome didn't crash or close.
- **Root Cause B (Chrome 136+)**: Chrome is running, but its CDP listener is on **IPv6 loopback only** (`[::1]:9222`), while the portproxy connects to IPv4 `127.0.0.1:9222` — so the forwarded connection is reset. See the "curl Fails — Two Independent Causes" section above.
- **Fix**: Check `netstat -ano | Select-String ':9222'` for `[::1]:9222 LISTENING`. This is **not** fixable without Administrator, and it does **not** stop the Chrome DevTools MCP from working — verify with `list_pages` and move on.

### 2. `Connection refused` (Port 9222)
- **Root Cause**: `socat` is not running in WSL2, or `$WIN_IP` changed after a machine reboot / network change.
- **Fix**: Re-run Step 2 to bind `socat` to the current `$WIN_IP`.

### 3. Check Windows Chrome Process from WSL2
To verify if Chrome is actually running and listening on Windows from inside WSL2:
```bash
cd /mnt/c && /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -Command "Get-Process -Name chrome -ErrorAction SilentlyContinue; netstat -ano | Select-String 9222"
```

---

## 🛠️ One-Time Host Setup Reference (Already Configured)

*(For reference only if setting up a new Windows host machine)*

Run in **PowerShell as Administrator** on Windows:

```powershell
# 1. Forward port 9222 on all interfaces to Windows 127.0.0.1:
netsh interface portproxy add v4tov4 listenport=9222 listenaddress=0.0.0.0 connectport=9222 connectaddress=127.0.0.1

# 2. Allow inbound TCP traffic on port 9222 through Windows Firewall:
New-NetFirewallRule -DisplayName "Chrome Remote Debug" -Direction Inbound -LocalPort 9222 -Protocol TCP -Action Allow
```
