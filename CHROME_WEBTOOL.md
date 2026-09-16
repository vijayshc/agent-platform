# Chrome Web Tool — Practical Navigation Reference

**Purpose:** This is the *operational* companion to `CHROME_BROWSE.md`.
`CHROME_BROWSE.md` covers how to **launch the bridge**. This file covers how to
**drive the UI via the Chrome DevTools MCP** once the bridge is up. It records
the concrete tool names, required signatures, and gotchas discovered through
hands-on use — so navigation works in a **single shot** next time.

---

## 0. TL;DR — Single-Shot Recipe

> **How the tools are exposed.** Current sessions get the Chrome DevTools MCP as
> **first-class tools** named `mcp__chrome__<name>` (e.g. `mcp__chrome__take_snapshot`).
> Older notes referenced an *execute runtime* using `tools["chrome-devtools"].<name>`;
> the signatures are identical, only the prefix differs. The recipe below uses the
> direct form.

```bash
# 1. Ensure bridge + app are up (see CHROME_BROWSE.md for the NON-destructive launch)
curl -s -o /dev/null -w "app:%{http_code}\n" http://127.0.0.1:5000/login   # expect 200
# A failing `curl http://127.0.0.1:9222/json/version` does NOT mean the MCP is down
# (gotchas #6 and #17) — judge the bridge by whether list_pages returns pages.
```

```js
// 2. Always start from list_pages — the session may begin at about:blank
let pages = await mcp__chrome__list_pages({});      // "1: about:blank [selected]"

// 3. Open the app (an anonymous tab is redirected to /login)
await mcp__chrome__new_page({ url: "http://localhost:5000/admin" });
pages = await mcp__chrome__list_pages({});           // RE-READ: pageIds shift after new_page
// -> "2: Agents (http://localhost:5000/login) [selected]"  => pageId = 2

// 4. Log in — snapshot FIRST, UIDs are never stable
let snap = await mcp__chrome__take_snapshot({ pageId });
// Login page (label is "Sign in", NOT "Login"):
//   uid=<p>_4 textbox "Username" | uid=<p>_6 textbox "Password" | uid=<p>_8 button "Sign in"
await mcp__chrome__fill_form({ pageId, elements: [
  { uid: "<p>_4", value: "admin" },
  { uid: "<p>_6", value: "admin" },        // password is `admin` — `admin123` is INVALID (§3)
]});
await mcp__chrome__click({ pageId, uid: "<p>_8" });
// The login POST is ASYNC — wait for the authenticated shell BEFORE navigating,
// or you bounce back to /login?next=… with "Invalid username or password." (gotcha #16)
// Use a POST-login-only marker: "Agents" is the document title on the login page too,
// so waiting on it resolves instantly and is a no-op!
await mcp__chrome__wait_for({ pageId, text: ["+ New Chat", "Dashboard", "Back to Agent"], timeout: 15000 });

// 5. Go to the page under test and capture it
await mcp__chrome__navigate_page({ pageId, type: "url", url: "<TARGET_URL>", timeout: 20000 });
await mcp__chrome__take_snapshot({ pageId });
await mcp__chrome__take_screenshot({ pageId, format: "png" });   // returns an attachment path; read it as an image
// Content below the fold needs the SPA's own container scrolled first — gotchas #14/#15.
```

---

## 1. Available Tools (catalog verified 2026-09-02, re-checked live 2026-09-12)

Every tool below was actually **invoked** against the running app, not just
documented. `✅` = exercised successfully (some were only exercised against an
"error path", noted inline). `⚠️` = present in the catalog but **not yet
exercised** — verify before relying on it.

| Tool | Signature | Notes |
|------|-----------|-------|
| `list_pages` | `list_pages({})` | Returns `## Pages\nN: <title> (<url>) [selected]`. This is your source of `pageId`s. |
| `new_page`   | `new_page({ url })` | **THE way to navigate.** `navigate`/`goto` do NOT exist. Opens fresh tab + selects it. ✅ |
| `select_page`| `select_page({ pageId, bringToFront? })` | Switch active context. ✅ |
| `close_page` | `close_page({ pageId })` | Last open page cannot be closed — closing the last tab leaves you on `about:blank`. ✅ |
| `resize_page`| `resize_page({ pageId, width, height })` | Resize viewport. ✅ (e.g. 1280×900) |
| `take_snapshot`| `take_snapshot({ pageId, verbose?, filePath? })` | A11y tree with `uid=` keys. **`pageId` is REQUIRED in the execute runtime.** `verbose:true` returns a much deeper tree (adds `ignored`/`generic`/`list`/`listitem`/`InlineTextBox` nodes). ✅ normal + verbose |
| `fill`       | `fill({ pageId, uid, value })` | Type into inputs/textarea, select `<select>`, set checkbox/toggle/radio (`"true"`/`"false"`). ✅ |
| `type_text`  | `type_text({ pageId, text, submitKey? })` | Type into **previously focused** element (appends). ✅ |
| `press_key`  | `press_key({ pageId, key })` | e.g. `Enter`, `Control+A`, `Control+Shift+R`. ✅ |
| `click`      | `click({ pageId, uid, dblClick? })` | Click element; `dblClick:true` for double-click. ✅ both modes |
| `hover`      | `hover({ pageId, uid })` | Hover element. ✅ |
| `drag`       | `drag({ pageId, from_uid, to_uid })` | Drag UI element. ✅ on live rows (needs **fresh** UIDs). |
| `upload_file`| `upload_file({ pageId, uid, filePaths[] })` | Upload via a file `<input>` **or** the button that opens the file chooser. File paths must be local to the *browser* instance. ✅ (uploaded and appeared in the UI) |
| `wait_for`   | `wait_for({ pageId, text[], timeout })` | Resolves when **any** string appears. `timeout:0` = default. ✅ |
| `handle_dialog` | `handle_dialog({ pageId, action, promptText? })` | `accept` / `dismiss`, optional `promptText`. ✅ handled a real native `confirm` dialog. |
| `list_console_messages` | `list_console_messages({ pageId })` | Lists console messages with `msgid=` + severity. Call this *before* `get_console_message`. ✅ |
| `get_console_message` | `get_console_message({ pageId, msgid })` | Takes a `msgid` from `list_console_messages`. Returns message + args + stack trace. ✅ |
| `lighthouse_audit` | `lighthouse_audit({ pageId, mode?, device?, outputDirPath? })` | `mode` = `navigation`|`snapshot` (default `navigation`), `device` = `desktop`|`mobile`. ✅ snapshot mode. Writes reports to a temp dir (see §2 gotcha #13). |
| `performance_stop_trace` | `performance_stop_trace({ pageId, filePath? })` | Stops/returns current trace. Returns `null` when nothing is recorded. ✅ (null path) |
| `performance_analyze_insight` | `performance_analyze_insight({ pageId, insightSetId, insightName })` | Needs a recorded trace; returns **"No recorded traces found"** otherwise (see §2 gotcha #12). ✅ (no-trace path) |
| `take_heapsnapshot` | `take_heapsnapshot({ pageId, filePath })` | Writes a JS heap dump to `filePath`. ✅ (32 MB dump written). Output goes outside the repo (see §2 gotcha #13). |
| `navigate_page` | `navigate_page({ pageId, type: "url"\|"back"\|"forward"\|"reload", url?, timeout?, ignoreCache?, handleBeforeUnload?, initScript? })` | **Exists and is the preferred way to move an existing tab.** ✅ used to reach `/admin` (gotcha #1 is stale). |
| `take_screenshot` | `take_screenshot({ pageId, uid?, fullPage?, format?, quality?, filePath? })` | **The visual-regression tool** required by AGENTS.md; returns a normalized attachment path you read as an image. ✅ viewport + element |
| `fill_form` | `fill_form({ pageId, elements: [{ uid, value }] })` | Batch fill of inputs/selects/checkboxes — prefer this over repeated `fill`. ✅ |
| `evaluate_script` | `evaluate_script({ pageId, function, args?, waitForStableDom?, dialogAction?, filePath? })` | Run JS in the page, returns JSON. Pass `waitForStableDom: false` for pure reads. ✅ (used to enumerate panels and locate the scroll container) |
| `list_network_requests` / `get_network_request` | `({ pageId, pageSize?, pageIdx?, resourceTypes? })` / `({ pageId, reqid?, requestFilePath?, responseFilePath? })` | Inspect XHR/headers/cookies. ⚠️ not yet exercised |
| `emulate` | `emulate({ pageId, viewport?, colorScheme?, networkConditions?, userAgent?, geolocation?, cpuThrottlingRate?, extraHttpHeaders? })` | Device / dark-mode / network emulation. ⚠️ not yet exercised |
| `performance_start_trace` | `performance_start_trace({ pageId, reload?, autoStop?, filePath? })` | **Exists** in the current catalog — gotcha #12 is stale. ⚠️ not yet exercised |

> **`search` / `goto` are NOT callable** in the execute runtime, and
> `browser_navigate` / `page_navigate` never existed — but **`navigate_page` does**
> (see the table above). Invoke tools directly to test them (§2 gotcha #1).

---

## 2. Hard-Won Gotchas (these cost the trial-and-error)

1. **`navigate_page` EXISTS (updated 2026-09-12) — the old "no navigation tool"
   claim was wrong.** The catalog exposes
   `navigate_page({ pageId, type: "url"|"back"|"forward"|"reload", url, timeout })`
   and it works (used it to reach `http://localhost:5000/admin`).
   - The original note was misled by the execute-runtime proxy, which returns
     `"function"` for ANY name and only fails when actually *invoked*
     (`Unknown tool 'chrome-devtools.navigate'`).
   - `browser_navigate` / `page_navigate` / `goto` still do not exist; `new_page({url})`
     also still works but leaves an extra tab behind.
   - ✅ The only reliable way to test a tool is to **call it**.

2. **`pageId` is REQUIRED by `take_snapshot` (and most tools) in the execute runtime.**
   Calling `take_snapshot({})` (or `{ pageId: undefined }`) throws:
   `Invalid arguments for tool "chrome-devtools_take_snapshot": pageId: Missing key`.
   Always pass `pageId` explicitly; grab it from `list_pages`.

3. **UIDs are NOT stable across snapshots / interactions.** A `take_snapshot`
   returns `uid=<pagePrefix>_<n>` (e.g. `6_176` = page 6, node 176). The `n` part
   changes whenever the DOM re-renders (clicking, filling, navigating rows). So:
   - Always **re-snapshot immediately before** interacting with a fresh context.
   - **Never cache a `uid`** and reuse it on a different pageId — you'll get
     `Element uid "<n>" not found on page <p>`.
   - The per-page prefix also shifts after a browser restart, so re-derive all
     UIDs after any `new_page`/reload.

4. **MCP `list_pages` ≠ curl `/json/list`.** The two return different ID spaces.
   - curl lists raw CDP tabs (with hex page `id`s like `3C282C82...`).
   - The MCP uses small integers (`pageId: 1, 2, 3...`) and its own page set.
   - **Do not assume a tab visible via curl maps to an MCP `pageId`.** Snapshot
     via the MCP after `new_page`, and read the `pageId` from `list_pages`.

5. **Fresh MCP session starts at `about:blank`.** I had to `new_page(...)` to get
   the app open. Don't rely on a pre-existing tab being selected/usable.

6. **The MCP session can be live even when the raw CDP `curl` bridge is dead.**
   The MCP toolserver maintains its **own** connection to Chrome and does not rely
   on the `socat`/port-9222 raw endpoint being reachable from the shell. Symptoms:
   `curl -s http://127.0.0.1:9222/json/version` returns empty / `exit 52`
   (*"Connection reset by peer"*), yet `list_pages` / `take_snapshot` work fine.
   **Do not re-run `setup_chrome_bridge.sh` just because the raw `curl` fails** —
   only do so if the MCP tools themselves are unreachable. If you do re-run setup,
   note that page IDs change (a "browser restarted" notice may appear), so always
   re-read `list_pages` to get fresh `pageId`s.

7. **`search` tool is NOT callable inside the execute runtime.**
   `search(...)` throws `Unknown tool: search`. Tool discovery there must be done
   by probing invocation (see gotcha #1) or by referencing this file's §1 table.

8. **Auth is required (Flask `@login_required`).** Unauthenticated requests to
   app routes return **302 → `/login?next=...`**. Flow:
   1. `new_page({ url: <LOGIN_URL> })` → the login page.
   2. `take_snapshot` → find username/password/Login UIDs.
   3. `fill` + `click` the Login button.
   4. `wait_for(["Agents", "Knowledge", "Dashboard"])` → authenticated.
   **Already-signed-in sessions short-circuit** — `new_page("/login")` and even
   `new_page("/admin/vector-db")` redirect straight to `/` if you're logged in.

9. **`wait_for` text-matching is NOT a plain substring match for all cases.**
   Resolves when the given string appears in the a11y tree. A string matches
   reliably when the exact text node exists (e.g. `admin`, `items`, `NAME`).
   `TODAY`/`YESTERDAY` were *present* in a snapshot but `wait_for` timed out on
   them — so prefer matching a stable, exact label rather than segment labels.
   `wait_for` **throws** (does not return false) on timeout, so wrap it in try/catch.

10. **`click` will FAIL if an element opens a native dialog.** Clicking an element
    that triggers `confirm`/`alert`/`prompt` blocks the click and returns:
    `# Open dialog\n<dialog text>\nCall handle_dialog to handle it before continuing.`
    You must then call `handle_dialog({ pageId, action: "accept" | "dismiss" })`
    to unblock. Do NOT call `handle_dialog` when no dialog is open — it throws
    `Error: No open dialog found`.

11. **`upload_file` needs a file-chooser trigger, and paths live on the browser host.**
    Pass either the file `<input>` UID *or* the button that opens the chooser
    (the file-browser page's hidden `input[type=file]` is opened by the
    "Upload File" button). `filePaths` must be **local to the browser instance**,
    not the MCP process. Verify success by re-snapshotting — the uploaded file
    name should appear in the UI, and the physical file should land in `uploads/`.

12. **`performance_analyze_insight` needs prior recorded traces — and the
    recorder now EXISTS.** `performance_start_trace` is present in the current
    catalog (2026-09-12), so a trace source is available: start it, then use
    `performance_stop_trace` / `performance_analyze_insight`. With no recorded
    trace, `performance_analyze_insight` returns
    `"No recorded traces found. Record a performance trace so you have Insights
    to analyze."` and `performance_stop_trace` returns `null`. ⚠️ this path has
    not been exercised end-to-end yet.

13. **Some tools write output OUTSIDE the repo.** `lighthouse_audit` and
    `take_heapsnapshot` write to `/tmp` (or a path you pass). `take_snapshot`'s
    `filePath` option returns the snapshot **inline** rather than writing to repo.
    The MCP is a separate process, so `filePath` is relative to *that* process's
    CWD — pass absolute paths to control where files land.

14. **SPA pages scroll an INNER container — `window.scrollTo()` is a silent no-op.**
    The agent-app shell scrolls `.aa-admin-content` (measured 2026-09-12:
    `document.body.scrollHeight === clientHeight === 860` while the real content is
    1368px). `window.scrollTo(0, document.body.scrollHeight)` does nothing and
    reports `scrollY: 0` — indistinguishable from "page fits on one screen".
    ```js
    const el = document.querySelector('.aa-admin-content');   // admin SPA content pane
    el.scrollTop = el.scrollHeight;                            // -> 508 (= 1368 - 860)
    ```
    Locate it generically when the class is unknown:
    ```js
    [...document.querySelectorAll('*')].filter(e =>
      e.scrollHeight > e.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(e).overflowY))
    ```

15. **`take_screenshot({ fullPage: true })` did NOT capture past the viewport here.**
    With the inner-scroller layout of gotcha #14 it returned the 1908×860 viewport
    only. Scroll the container first, then take a viewport screenshot — don't trust
    `fullPage` on the SPAs. The result is a normalized attachment path (`/home/…/objects/<sha>`);
    read it as an image directly, and copy it to a `.png` path first if you need to edit it.

16. **Login is an async React POST — wait for the shell before navigating.**
    `click` on "Sign in" returns as soon as the click is dispatched. Navigating
    immediately lands you back on `/login?next=…` showing *"Invalid username or
    password."* even when the credentials were correct — which reads like a bad
    password and sends you chasing the wrong bug.
    - Wait on a **post-login-only** marker. Which one exists depends on the shell
      you land on — chat shell: `"+ New Chat"`, `"Automations"`, `"What should we
      explore?"`; admin shell: `"Dashboard"`, `"Back to Agent"`, `"Audit Logs"`.
      A set covering both: `wait_for({ pageId, text: ["+ New Chat", "Dashboard",
      "Back to Agent"], timeout: 15000 })`.
    - ⚠️ **Do NOT wait on `"Agents"`** — that is the document title of the *login*
      page (`RootWebArea "Agents" url="…/login"`), so the wait resolves immediately
      and silently protects nothing. This is the trap that produces the bug above.

17. **Chrome 152 binds CDP to IPv6 loopback only, so a failing raw `curl` proves nothing.**
    With `--remote-debugging-address=0.0.0.0` (and with `127.0.0.1`) the listener
    appeared as `TCP [::1]:9222 LISTENING` (PID = the debug chrome.exe), while WSL
    reaches Windows through the portproxy
    `172.27.240.1:9222 -> 127.0.0.1:9222` (IPv4) — so WSL connections died in
    `TIME_WAIT`/RST and `curl --max-time 3 http://127.0.0.1:9222/json/version`
    returned empty / exit 28 / exit 52 **while the MCP worked perfectly**. Pointing
    the portproxy at `::1` needs Administrator (`netsh interface portproxy`), which
    this box does not have. **Judge the bridge only by `list_pages`.**

---

## 3. Login URL & Credentials

| Login URL | `http://localhost:5000/login` |
|-----------|-------------------------------|
| **Username** | `admin` |
| **Password** | `admin` |

> **`admin123` is INVALID** — verified 2026-09-12 both against the live login
> (returns *"Invalid username or password."*) and against the bcrypt hash in
> `text2sql.db` (`select password_hash from users where username='admin'`), where
> only `admin` matches. Earlier revisions of this file listed `admin123`; that was wrong.

---

## 4. Login Form Snapshot (reference UIDs)

Actual snapshot (2026-09-12) — the document title is `Agents`, the submit button
is labelled **"Sign in"** (not "Login"), and there is an extra `Forgot password?` button:

```
uid=1_0 RootWebArea "Agents" url="http://localhost:5000/login"
  uid=1_4 textbox "Username" focusable focused required
  uid=1_6 textbox "Password" required
  uid=1_7 button "Forgot password?"
  uid=1_8 button "Sign in"                         <- click to submit
```

`<p>` = the per-page UID prefix. **It is neither stable nor reliably equal to the
pageId** — the first snapshot of page `2` came back as `1_*`, and only after a
navigation did it become `2_*`. Re-derive every UID from a fresh `take_snapshot`
immediately before interacting.

---

| Route | Page title | Notes |
|-------|-----------|-------|
| `http://localhost:5000/login` | `Agents` (SPA shell) | Login form — **password is `admin`** (§3). Redirects to `/` if already authenticated. |


---

## 5. Quick UI-Validation Checklist

1. `list_pages` → note current `pageId`s. (Session may start at `about:blank`.)
2. `new_page({ url })` → open target route (or login).
3. `take_snapshot({ pageId })` → full a11y tree with UIDs.
4. Interact: `fill` / `click` / `press_key` / `wait_for`.
   - Re-snapshot immediately before each interaction — UIDs are not stable.
5. Re-`take_snapshot({ pageId })` to confirm the resulting UI state.
6. Handle native dialogs: if a `confirm`/`alert` appears, call
   `handle_dialog({ pageId, action: "accept" | "dismiss" })`.
7. For visual/design validation use `take_screenshot` (or
   `lighthouse_audit({ pageId, mode: "snapshot" })`) — per AGENTS.md, judge UI via
   **visual regressions**, not DOM automation. Scroll the page's own container
   first (gotcha #14); `fullPage: true` does not cover it (gotcha #15).
8. Clean up artifacts: `upload_file` writes into `uploads/`; Lighthouse/heap
   snapshots go to `/tmp`. Remove test files so they don't pollute the repo.
