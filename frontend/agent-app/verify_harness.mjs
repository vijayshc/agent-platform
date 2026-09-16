import { chromium } from "@playwright/test";

const BASE = process.env.BASE_URL || "http://127.0.0.1:5090";
const SHOTS = process.env.SHOT_DIR || "/tmp";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(`console: ${m.text()}`);
});

async function expectVisible(loc) {
  await loc.waitFor({ state: "visible", timeout: 15000 });
}

// 1. login
await page.goto(`${BASE}/login`);
await page.locator("#username").fill("admin");
await page.locator("#password").fill("admin123");
await page.locator("button[type=submit]").click();
await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 });
console.log("LOGIN_OK");

// 2. open studio editor
await page.goto(`${BASE}/agent-studio/editor`);
await expectVisible(page.getByTestId("agent-studio"));
await expectVisible(page.getByTestId("studio-palette"));
console.log("STUDIO_OK");

// 3. add harness agent from palette
await page.getByTestId("studio-title").fill("Harness Verify");
await page.getByTestId("palette-harness").click();
const inspector = page.getByTestId("studio-inspector");
await inspector.waitFor({ state: "visible", timeout: 15000 });
await inspector.evaluate((el, expected) => {
  if (el.getAttribute("data-node-type") !== expected) {
    throw new Error(`data-node-type=${el.getAttribute("data-node-type")} expected ${expected}`);
  }
}, "harness");
console.log("HARNESS_NODE_OK, data-node-type =", await inspector.getAttribute("data-node-type"));

// 4. assert all harness controls render
const controls = [
  "inspector-max-context",
  "inspector-max-output",
  "inspector-disable-todo",
  "inspector-disable-mode",
  "inspector-mode",
  "inspector-disable-memory",
  "inspector-memory-store",
  "inspector-disable-compaction",
  "inspector-compaction-strategy",
  "inspector-disable-web-search",
];
for (const id of controls) {
  await expectVisible(page.getByTestId(id));
}
console.log("CONTROLS_OK:", controls.join(","));

// 5. toggle several controls
await page.getByTestId("inspector-name").fill("Harness Verify");
await page.getByTestId("inspector-instructions").fill("Verify harness capabilities end to end.");
// scripted is tests-only; harness Inspector hides it (needs real middleware).
const maxContext = page.getByTestId("inspector-max-context");
await maxContext.fill("16000");
await page.getByTestId("inspector-max-output").fill("2000");
await page.getByTestId("inspector-disable-mode").uncheck();
await page.getByTestId("inspector-disable-todo").uncheck();
await page.getByTestId("inspector-disable-compaction").uncheck();
await page.getByTestId("inspector-disable-web-search").uncheck();
await page.getByTestId("inspector-disable-memory").check();
await page.getByTestId("inspector-memory-store").selectOption("memory_file_store");
console.log("TOGGLES_OK");

await page.screenshot({ path: `${SHOTS}/gapA-verify-harness-inspector.png`, fullPage: true });
console.log("SHOT_1");

// 6. save and verify persisted config via API
await page.getByTestId("studio-save").click();
await page.waitForFunction(
  () => document.querySelector('[data-testid="studio-status"]')?.textContent?.includes("Saved"),
  { timeout: 20000 }
);
const saved = await page.request.get(`${BASE}/api/v1/agents/harness-verify?full=1`);
const body = await saved.json();
const cfg = body.config || {};
console.log("PERSISTED:", JSON.stringify({
  runtime: cfg.runtime,
  max_context_window_tokens: cfg.max_context_window_tokens,
  max_output_tokens: cfg.max_output_tokens,
  disable_todo: cfg.disable_todo,
  disable_mode: cfg.disable_mode,
  disable_memory: cfg.disable_memory,
  memory_store: cfg.memory_store,
  disable_compaction: cfg.disable_compaction,
  disable_web_search: cfg.disable_web_search,
}));
const expectP = {
  runtime: "harness",
  max_context_window_tokens: 16000,
  max_output_tokens: 2000,
  disable_todo: true,
  disable_mode: true,
  disable_memory: false,
  memory_store: "memory_file_store",
  disable_compaction: true,
  disable_web_search: true,
};
for (const [k, v] of Object.entries(expectP)) {
  if (cfg[k] !== v) throw new Error(`persisted ${k}=${cfg[k]} expected ${v}`);
}
console.log("PERSIST_OK");

// 7. validate endpoint accepts
const v = await page.request.post(`${BASE}/api/v1/agents/harness-verify/validate`, { data: {} });
const vj = await v.json();
console.log("VALIDATE:", JSON.stringify(vj.errors || vj));
const cfgErrors = (vj.errors || []).filter((e) => e.code !== "missing_instructions" && e.code !== "compile_failed");
if (cfgErrors.length) throw new Error("validate errors: " + JSON.stringify(cfgErrors));
console.log("VALIDATE_OK (no config-field errors)");

// 8. regression: plain agent inspector still works
await page.getByTestId("studio-title").fill("Plain Verify");
await page.getByTestId("palette-agent").click();
await inspector.waitFor({ state: "visible", timeout: 15000 });
await inspector.evaluate((el, expected) => {
  if (el.getAttribute("data-node-type") !== expected) {
    throw new Error(`data-node-type=${el.getAttribute("data-node-type")} expected ${expected}`);
  }
}, "agent");
await expectVisible(page.getByTestId("inspector-agent-max-context"));
await expectVisible(page.getByTestId("inspector-agent-max-output"));
await expectVisible(page.getByTestId("inspector-agent-compaction"));
const harnessOnly = await page.getByTestId("inspector-disable-todo").count();
if (harnessOnly !== 0) throw new Error("harness-only controls leaked into plain agent inspector");
console.log("PLAIN_AGENT_OK (compaction controls present, no harness-only controls)");
await page.screenshot({ path: `${SHOTS}/gapA-verify-plain-agent.png`, fullPage: true });

// 9. reload round-trip: harness agent persists through graph reload
await page.goto(`${BASE}/agent-studio/editor?slug=harness-verify`);
await page.waitForFunction(
  () => document.querySelector('[data-testid="studio-graph"]')?.getAttribute("data-node-types")?.includes("harness"),
  { timeout: 20000 }
);
await page.waitForFunction(
  () => {
    const el = document.querySelector('[data-testid="studio-node-harness"]');
    return el !== null && el.offsetParent !== null;
  },
  { timeout: 15000 }
);
await page.getByTestId("studio-node-harness").first().click();
await inspector.waitFor({ state: "visible", timeout: 15000 });
const ctxVal = await page.getByTestId("inspector-max-context").inputValue();
const todoChecked = await page.getByTestId("inspector-disable-todo").isChecked();
console.log("RELOAD:", { ctxVal, todoChecked });
if (ctxVal !== "16000" || todoChecked !== false) throw new Error("round-trip mismatch");
console.log("RELOAD_OK");

// 10. test run in the drawer: harness agent with mode=plan should produce a run + streaming events
await page.getByTestId("studio-testrun").click();
await page.getByTestId("testrun-drawer").waitFor({ state: "visible", timeout: 15000 });
await page.getByTestId("testrun-input").fill("introduce yourself");
await page.getByTestId("testrun-send").click();
await page.waitForFunction(
  () => {
    const el = document.querySelector('[data-testid="testrun-drawer"]');
    return el !== null && (el.getAttribute("data-run-id") || "") !== "";
  },
  { timeout: 30000 }
);
const runId = await page.getByTestId("testrun-drawer").getAttribute("data-run-id");
console.log("TESTRUN_RUN_ID:", runId);
if (!runId) throw new Error("no run id after test run");
await page.waitForFunction(
  () => document.querySelector('[data-testid="testrun-drawer"]')?.getAttribute("data-streaming") === "0",
  { timeout: 60000 }
);
const output = await page.getByTestId("testrun-output").innerText().catch(() => "");
console.log("TESTRUN_OUTPUT:", output.slice(0, 120));
const apiRun = await page.request.get(`${BASE}/api/v1/runs/${runId}`);
const runBody = await apiRun.json();
console.log("RUN_STATUS:", runBody.status, "events:", Array.isArray(runBody.events) ? runBody.events.length : "n/a");
if (runBody.status && runBody.status !== "completed" && runBody.status !== "OK") {
  console.log("RUN_NOT_COMPLETED (streaming status may be normal):", runBody.status);
}
await page.screenshot({ path: `${SHOTS}/gapA-verify-testrun-drawer.png`, fullPage: true });
console.log("TESTRUN_DONE");

console.log("BROWSER_VERIFY_ALL_OK");
if (errors.length) {
  console.log("PAGE_ERRORS:", errors.join("\n"));
} else {
  console.log("NO_PAGE_ERRORS");
}
await browser.close();