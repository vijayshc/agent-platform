/**
 * Regression harness for the app theme bootstrap (static/js/theme-preload.js +
 * static/js/theme.js). Runs both scripts in a Node VM with a minimal DOM/storage
 * stub and reports what the boot sequence does to the two storages.
 *
 * Usage: node tests/js/theme_boot_check.mjs <repo-root>
 * Prints one JSON document: { ok, cases: [{name, ...observed}] }.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const root = process.argv[2] || process.cwd();
const preloadSrc = fs.readFileSync(path.join(root, "static/js/theme-preload.js"), "utf8");
const themeSrc = fs.readFileSync(path.join(root, "static/js/theme.js"), "utf8");

class ClassList {
  constructor() {
    this._s = new Set();
  }
  add(...cs) {
    cs.forEach((c) => c && this._s.add(c));
  }
  remove(...cs) {
    cs.forEach((c) => this._s.delete(c));
  }
  contains(c) {
    return this._s.has(c);
  }
  toggle(c, on) {
    if (on) this._s.add(c);
    else this._s.delete(c);
  }
  toString() {
    return [...this._s].join(" ");
  }
  [Symbol.iterator]() {
    return this._s[Symbol.iterator]();
  }
}

class Storage {
  constructor(seed = {}) {
    this.map = new Map(Object.entries(seed));
    this.writes = [];
  }
  getItem(k) {
    return this.map.has(k) ? this.map.get(k) : null;
  }
  setItem(k, v) {
    this.writes.push([k, String(v)]);
    this.map.set(k, String(v));
  }
  removeItem(k) {
    this.map.delete(k);
  }
  clear() {
    this.map.clear();
  }
}

function makeElement() {
  const classList = new ClassList();
  const style = { setProperty() {}, removeProperty() {} };
  return { classList, style, innerHTML: "", href: "", classListText: () => classList.toString() };
}

/** Boot a fresh page with the given storages; returns observed page state. */
function boot({ local = null, session = null, withBody = false } = {}) {
  const localStorage = new Storage(local === null ? {} : { selectedTheme: local });
  const sessionStorage = new Storage(session === null ? {} : { selectedTheme: session });
  const documentElement = makeElement();
  let body = withBody ? makeElement() : null;
  const listeners = {};
  const document = {
    documentElement,
    body,
    readyState: withBody ? "complete" : "loading",
    addEventListener: (type, fn) => {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    removeEventListener() {},
    dispatchEvent: () => true,
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
  };
  const sandbox = {
    document,
    console: { log() {}, warn() {}, error() {} },
    localStorage,
    sessionStorage,
    CustomEvent: class CustomEvent {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    setTimeout,
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(preloadSrc, context, { filename: "theme-preload.js" });
  if (!withBody) {
    // The parser has created <body> by the time DOMContentLoaded fires.
    body = makeElement();
    document.body = body;
    (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  }
  vm.runInContext(themeSrc, context, { filename: "theme.js" });
  return {
    state: () => ({
      html: documentElement.classList.toString(),
      body: body ? body.classList.toString() : "",
      local: localStorage.getItem("selectedTheme"),
      session: sessionStorage.getItem("selectedTheme"),
      writes: localStorage.writes.concat(sessionStorage.writes.map(([k, v]) => [`session:${k}`, v])),
    }),
    manager: sandbox.themeManager,
  };
}

const cases = [];
const record = (name, obs) => cases.push({ name, ...obs });

// 1. The persisted (localStorage) preference must win over a stale session value.
let r = boot({ local: "light", session: "dark", withBody: true });
let s = r.state();
record("persisted_light_beats_stale_session_dark", {
  html: s.html,
  local: s.local,
  session: s.session,
  localWrites: s.writes.filter(([k]) => k === "selectedTheme").length,
});

// 2. Same in the other direction (dark persisted, stale session light).
r = boot({ local: "dark", session: "light", withBody: true });
s = r.state();
record("persisted_dark_beats_stale_session_light", { html: s.html, local: s.local, session: s.session });

// 3. Boot must not rewrite storage at all.
r = boot({ local: "light", session: "dark", withBody: true });
record("boot_writes_no_storage", { writes: r.state().writes });

// 4. Preload path with no <body> yet (real <head> execution) + session fallback.
r = boot({ local: null, session: "dark" });
s = r.state();
record("session_is_the_fallback_when_nothing_persisted", { html: s.html, body: s.body, session: s.session });

// 5. Default when no preference exists anywhere.
r = boot({});
s = r.state();
record("defaults_to_light", { html: s.html, body: s.body });

// 6. A user gesture still persists (chat settings -> switchTheme, Studio -> applyTheme).
r = boot({ local: "light", session: "light", withBody: true });
r.manager.switchTheme("dark");
const afterSwitch = r.state();
r.manager.applyTheme("light");
record("user_gesture_persists", {
  switchTheme: { local: afterSwitch.local, session: afterSwitch.session, html: afterSwitch.html },
  applyTheme: { local: r.state().local, session: r.state().session, html: r.state().html },
});

// 7. An unknown stored theme falls back to light without corrupting storage.
r = boot({ local: "neon", session: "neon", withBody: true });
s = r.state();
record("unknown_theme_falls_back_to_light", { html: s.html, local: s.local, session: s.session });

const ok =
  cases[0].html.includes("theme-light") &&
  cases[0].local === "light" &&
  cases[0].session === "dark" &&
  cases[0].localWrites === 0 &&
  cases[1].html.includes("theme-dark") &&
  cases[1].local === "dark" &&
  cases[2].writes.length === 0 &&
  cases[3].html.includes("theme-dark") &&
  cases[3].body.includes("theme-dark") &&
  cases[4].html.includes("theme-light") &&
  cases[5].switchTheme.local === "dark" &&
  cases[5].switchTheme.session === "dark" &&
  cases[5].applyTheme.local === "light" &&
  cases[6].html.includes("theme-light") &&
  // Boot never rewrites storage, so an unknown stored value is left untouched
  // while the page renders the light fallback.
  cases[6].local === "neon" &&
  cases[6].session === "neon";

console.log(JSON.stringify({ ok, cases }, null, 2));
process.exit(ok ? 0 : 1);
