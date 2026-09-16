/**
 * UI plumbing against the scripted AGENT_PLATFORM_E2E=1 server
 * (frontend/agent-app/e2e + tests/e2e/agent_app/server.py). These specs
 * prove layout/composer/run-panel wiring, not live agent behavior.
 * Live LLM behavior is proven by tests/test_live_*.py and operator UI on :5000.
 */
import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("admin123");
  await page.locator("button[type=submit]").click();
  await page.waitForURL((url) => !url.pathname.includes("/login"));
}

async function openAgent(page: Page) {
  await login(page);
  await page.goto("/agent");
  await expect(page.getByTestId("agent-composer")).toBeVisible();
}

test.describe("Agent Chat + Runs", () => {
  test("1. empty composer, no chips, no mode/team/MCP dropdowns", async ({ page }) => {
    await openAgent(page);
    await expect(page.getByTestId("agent-composer")).toBeVisible();
    await expect(page.getByTestId("composer-input")).toHaveValue("");
    await expect(page.getByTestId("agent-chip")).toHaveCount(0);
    await expect(page.getByTestId("file-chip")).toHaveCount(0);
    await expect(page.locator("#modeSelect, #teamSelect, #workflowSelect, #serverSelect, #projectSelect")).toHaveCount(0);
    await expect(page.getByTestId("agent-composer").locator("select")).toHaveCount(0);
    await expect(page.getByTestId("plus-menu")).toHaveCount(0);
  });

  test("2. plus button opens the file picker directly (no agent/model menu)", async ({ page }) => {
    await openAgent(page);
    await expect(page.getByTestId("plus-button")).toHaveAttribute("aria-label", "Upload file");
    const chooser = page.waitForEvent("filechooser");
    await page.getByTestId("plus-button").click();
    await chooser;
    await expect(page.getByTestId("plus-menu")).toHaveCount(0);
    await expect(page.getByTestId("plus-select-agent")).toHaveCount(0);
    await expect(page.getByTestId("plus-select-model")).toHaveCount(0);
  });

  test("3. Spotlight filters, keyboard select, chip appears; opening again replaces agent", async ({ page }) => {
    await openAgent(page);
    await page.keyboard.press("Control+k");
    const search = page.getByTestId("spotlight-search");
    await expect(search).toBeFocused();
    await search.fill("echo");
    const items = page.getByTestId("spotlight-item");
    await expect(items.first()).toBeVisible();
    const names = await items.allTextContents();
    expect(names.every((n) => n.toLowerCase().includes("echo"))).toBeTruthy();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("spotlight")).toHaveCount(0);
    await expect(page.getByTestId("agent-chip")).toContainText(/echo/i);

    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("writer");
    await expect(page.locator('[data-testid="spotlight-item"][data-slug="writer"]')).toBeVisible();
    await page.locator('[data-testid="spotlight-item"][data-slug="writer"]').click();
    await expect(page.getByTestId("agent-chip")).toContainText(/writer/i);
    await expect(page.getByTestId("agent-chip")).toHaveCount(1);
  });

  test("4. Send without agent opens Spotlight", async ({ page }) => {
    await openAgent(page);
    await page.getByTestId("composer-input").fill("hello");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("spotlight")).toBeVisible();
  });

  test("5. File upload chip survives send", async ({ page }) => {
    await openAgent(page);
    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("echo");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    await page.getByTestId("file-input").setInputFiles({
      name: "upload.txt",
      mimeType: "text/plain",
      buffer: new TextEncoder().encode("hello from chip") as unknown as Uint8Array,
    });
    await expect(page.getByTestId("file-chip")).toContainText("upload.txt");
    await page.getByTestId("composer-input").fill("please read the file");
    const reqs: string[] = [];
    page.on("request", (r) => reqs.push(r.url()));
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("user-bubble").getByTestId("file-chip")).toContainText("upload.txt");
    await expect(page.getByTestId("assistant-bubble").first()).toBeVisible({ timeout: 20000 });
    expect(reqs.some((u) => u.includes("/api/v1/"))).toBeTruthy();
  });

  test("8. Orchestration e2e — multi-event types in the UI (scripted MAF)", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/")) apiCalls.push(r.url());
    });
    await openAgent(page);
    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("studio");
    await page.locator('[data-testid="spotlight-item"][data-slug="studio-scripted"]').click();
    await page.getByTestId("composer-input").fill("review sample-service");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("assistant-bubble").first()).toBeVisible({ timeout: 40000 });
    await expect(page.locator(".aa-speaker")).toContainText(/Design Reviewer/i, { timeout: 40000 });
    await expect(page.getByTestId("skill-card")).toBeVisible({ timeout: 40000 });
    await expect(page.getByTestId("tool-card").first()).toBeVisible();
    await expect(page.getByText(/Findings/i)).toBeVisible({ timeout: 40000 });
    expect(apiCalls.some((u) => u.includes("/api/v1/conversations/") && u.includes("/messages"))).toBeTruthy();
    expect(apiCalls.every((u) => !u.includes("/api/agent/chat"))).toBeTruthy();

    await page.goto("/agent-runs");
    await expect(page.locator('[data-testid="run-item"][data-agent="studio-scripted"]').first()).toBeVisible({
      timeout: 20000,
    });
  });

  test("10. Chat consumes /api/v1 (same public execution API)", async ({ page }) => {
    const urls: string[] = [];
    page.on("request", (r) => urls.push(new URL(r.url()).pathname));
    await openAgent(page);
    expect(urls.some((p) => p === "/api/v1/agents")).toBeTruthy();
    await page.keyboard.press("Control+k");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    await page.getByTestId("composer-input").fill("ping");
    await page.getByTestId("send-button").click();
    await expect(page.getByText("pong from echo")).toBeVisible({ timeout: 20000 });
    expect(urls.some((p) => p.startsWith("/api/v1/conversations") || p === "/api/v1/runs")).toBeTruthy();
    expect(urls.filter((p) => p.startsWith("/api/agent/chat"))).toHaveLength(0);
  });

  test("11. Theme + narrow viewport; second turn reuses conversation", async ({ page }) => {
    await openAgent(page);
    await page.getByTestId("profile-button").click();
    await page.getByTestId("theme-option-dark").click();
    await expect(page.locator("body")).toHaveClass(/theme-dark/);
    await page.getByTestId("profile-button").click();
    await page.getByTestId("theme-option-light").click();
    await expect(page.locator("body")).toHaveClass(/theme-light/);
    await page.setViewportSize({ width: 420, height: 800 });
    await expect(page.getByTestId("agent-composer")).toBeVisible();

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.keyboard.press("Control+k");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    const convIds: string[] = [];
    page.on("request", (r) => {
      const m = r.url().match(/\/api\/v1\/conversations\/([^/]+)\/messages/);
      if (m) convIds.push(m[1]);
    });
    await page.getByTestId("composer-input").fill("first turn");
    await page.getByTestId("send-button").click();
    await expect(page.getByText("pong from echo")).toBeVisible({ timeout: 20000 });
    await page.getByTestId("composer-input").fill("second turn");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("user-bubble")).toHaveCount(2);
    expect(new Set(convIds).size).toBe(1);
  });

  test("12. Runs UI live while streaming; waterfall; HITL decision; view-run deep link", async ({ page, context }) => {
    await openAgent(page);

    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("slow");
    await page.locator('[data-testid="spotlight-item"][data-slug="slow-echo"]').click();
    await page.getByTestId("composer-input").fill("stream please");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("streaming-status")).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("view-run")).toBeVisible({ timeout: 15000 });

    const runs = await context.newPage();
    await runs.goto("/agent-runs");
    await expect(runs.getByTestId("runs-table")).toHaveClass(/chat-datatable-table/);
    await expect(runs.locator(".aa-col-when")).toHaveClass(/sorting/);
    await runs.locator(".aa-col-agent").click();
    await expect(runs.locator(".aa-col-agent")).toHaveClass(/sorting_asc/);
    const live = runs.locator('[data-testid="run-item"][data-agent="slow-echo"][data-status="running"]');
    await expect(live.first()).toBeVisible({ timeout: 15000 });
    await live.first().click();
    await runs.getByTestId("cancel-run").click();
    await expect(runs.locator('[data-testid="run-item"][data-agent="slow-echo"]')).toHaveAttribute(
      "data-status",
      /cancel/,
      { timeout: 15000 },
    );
    await runs.getByTestId("filter-agent").fill("slow-echo");
    await expect(runs.locator('[data-testid="run-item"][data-agent="slow-echo"]').first()).toBeVisible();

    await page.getByTestId("new-chat").click();
    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("writer");
    await page.locator('[data-testid="spotlight-item"][data-slug="writer"]').click();
    await page.getByTestId("composer-input").fill("write the file");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("hitl-card")).toBeVisible({ timeout: 25000 });

    await page.reload();
    await expect(page.getByTestId("conversation-item").first()).toBeVisible({ timeout: 15000 });
    await page.getByTestId("conversation-item").first().click();
    await expect(page.getByTestId("hitl-card")).toBeVisible({ timeout: 15000 });

    const view = page.getByTestId("view-run");
    await expect(view).toBeVisible();
    const href = await view.getAttribute("href");
    expect(href).toMatch(/\/agent-runs\?run=/);

    await page.getByTestId("hitl-approve").click();
    await expect(page.getByText(/wrote it/i)).toBeVisible({ timeout: 25000 });
    await expect(page.getByTestId("hitl-card")).toHaveCount(0);
    await page.getByTestId("conversation-item").first().click();
    await expect(page.getByTestId("hitl-card")).toHaveCount(0);

    await page.goto(href!);
    await expect(page.getByTestId("waterfall")).toBeVisible();
    await expect(page.getByTestId("span-row").first()).toBeVisible({ timeout: 15000 });
    const types = await page.getByTestId("span-row").evaluateAll((els) =>
      els.map((el) => el.getAttribute("data-event-type") || ""),
    );
    expect(types).toContain("hitl_decision");
    expect(types.some((t) => t === "execute_tool" || t === "tool_call")).toBeTruthy();
    await page.locator('[data-testid="span-row"][data-event-type="hitl_decision"]').first().click();
    await expect(page.getByTestId("hitl-decision")).toContainText(/approved/i);

    await page.getByTestId("filter-hitl").check();
    await expect(page.locator('[data-testid="run-item"][data-agent="writer"]').first()).toBeVisible();
  });

  test("Spotlight can pick a supervisor workflow", async ({ page }) => {
    await openAgent(page);
    await page.keyboard.press("Control+k");
    await page.getByTestId("spotlight-search").fill("studio-supervisor");
    await expect(page.locator('[data-testid="spotlight-item"][data-slug="studio-supervisor"]')).toBeVisible();
    await page.locator('[data-testid="spotlight-item"][data-slug="studio-supervisor"]').click();
    await expect(page.getByTestId("agent-composer")).toBeVisible();
  });

  test("Conversation search in the left rail", async ({ page }) => {
    const searchUrls: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/conversations?q=")) searchUrls.push(r.url());
    });
    await openAgent(page);
    await page.keyboard.press("Control+k");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    await page.getByTestId("composer-input").fill("title-only weather chat");
    await page.getByTestId("send-button").click();
    await expect(page.getByText("pong from echo")).toBeVisible({ timeout: 20000 });
    await page.getByTestId("composer-input").fill("please inspect unique-search-needle-58cac");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("user-bubble")).toHaveCount(2, { timeout: 20000 });
    await page.getByTestId("new-chat").click();
    await page.keyboard.press("Control+k");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    await page.getByTestId("composer-input").fill("other conversation about sports");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("user-bubble").first()).toBeVisible({ timeout: 20000 });
    await page.getByTestId("conversation-search").fill("unique-search-needle-58cac");
    await expect.poll(() => searchUrls.some((u) => u.includes("q=unique-search-needle-58cac"))).toBeTruthy();
    await expect(page.getByTestId("conversation-item")).toHaveCount(1, { timeout: 15000 });
    await expect(page.getByTestId("conversation-item").first()).toContainText(/weather/i);
    await expect(page.getByTestId("conversation-item").first()).not.toContainText(/unique-search-needle-58cac/i);
    await page.getByTestId("conversation-search").fill("zzzz-no-match-xyz");
    await expect(page.getByTestId("conversation-empty")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("conversation-item")).toHaveCount(0);
  });

  test("Composer regains focus after the agent finishes", async ({ page }) => {
    await openAgent(page);
    await page.keyboard.press("Control+k");
    await page.locator('[data-testid="spotlight-item"][data-slug="echo"]').click();
    const composer = page.getByTestId("composer-input");
    await composer.fill("ping");
    // Clicking send moves focus to the button; it must return to the composer
    // once the response completes so the user can keep typing.
    await page.getByTestId("send-button").click();
    await expect(page.getByText("pong from echo")).toBeVisible({ timeout: 20000 });
    await expect(composer).toBeFocused();
  });
});
