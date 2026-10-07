import { test, expect, type Page } from "@playwright/test";

/**
 * Agent list Clone/Delete/History against the scripted AGENT_PLATFORM_E2E=1
 * server (same harness as e2e/studio.spec.ts and e2e/agent.spec.ts: the
 * :5055 webServer in playwright.config.ts, never the live :5000 deployment).
 * Backend history/rollback/clone semantics are pinned by
 * tests/test_agent_versions.py; these specs prove the list affordances call
 * them (History dialog, Clone navigation, Delete dialog) plus the chat
 * ?agent= preselect, which is frontend-only.
 */

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("admin123");
  await page.locator("button[type=submit]").click();
  await page.waitForURL((url) => !url.pathname.includes("/login"));
}

function uid() {
  return `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
}

async function createPublishedPair(page: Page, slug: string, name: string) {
  const v1 = "Version one instructions.";
  const v2 = "Version two instructions.";
  const created = await page.request.post("/api/v1/agents", {
    data: { name, slug, kind: "agent", config: { kind: "agent", instructions: v1 } },
  });
  expect(created.ok()).toBeTruthy();
  const pub1 = await page.request.post(`/api/v1/agents/${slug}/publish`, { data: {} });
  expect(pub1.ok()).toBeTruthy();
  const put = await page.request.put(`/api/v1/agents/${slug}`, {
    data: { config: { kind: "agent", instructions: v2 } },
  });
  expect(put.ok()).toBeTruthy();
  const pub2 = await page.request.post(`/api/v1/agents/${slug}/publish`, { data: {} });
  expect(pub2.ok()).toBeTruthy();
  return { v1, v2 };
}

async function openActions(page: Page, slug: string, itemTestId: string) {
  const row = page.locator(`tr[data-slug="${slug}"]`);
  await expect(row).toBeVisible();
  // Close any stale menu, then open this row's. The list re-renders as the
  // search filter settles and a click that races that re-render can land
  // without toggling the menu, so retry the trigger until it opens.
  await page.keyboard.press("Escape");
  const trigger = row.getByTestId("agent-actions");
  const item = page.getByTestId(itemTestId);
  for (let attempt = 0; attempt < 4; attempt++) {
    if ((await item.count()) > 0) return;
    await trigger.click();
    try {
      await item.waitFor({ state: "attached", timeout: 2500 });
      return;
    } catch {
      // Menu did not open; retry the click on the settled DOM.
    }
  }
  await item.waitFor({ state: "visible", timeout: 15_000 });
}

test.describe("Agent versions (list Clone/Delete/History)", () => {
  test("History shows v1/v2, previews v1 and restores it", async ({ page }) => {
    const slug = `vprobe-${uid()}`;
    await login(page);
    const { v1 } = await createPublishedPair(page, slug, `VProbe ${slug}`);

    await page.goto("/agent-studio");
    await expect(page.getByTestId("agent-list")).toBeVisible();
    await page.getByTestId("agent-list-search").fill(slug);

    await openActions(page, slug, "agent-history");
    await page.getByTestId("agent-history").click();
    await expect(page.getByTestId("version-history")).toBeVisible();
    await expect(page.getByTestId("version-list")).toContainText("v2");
    await expect(page.getByTestId("version-list")).toContainText("v1");

    await page.getByTestId("version-preview-1").click();
    await expect(page.getByTestId("version-preview")).toBeVisible();
    await expect(page.getByTestId("version-preview-json")).toContainText("Version one instructions.");

    await page.getByTestId("version-preview-restore").click();
    await expect(page.getByTestId("version-restore-confirm")).toBeVisible();
    await page.getByTestId("version-restore-confirm-btn").click();
    await expect(page.getByTestId("version-history")).toHaveCount(0, { timeout: 20_000 });

    const full = await (await page.request.get(`/api/v1/agents/${slug}?full=1`)).json();
    expect(full.config.instructions).toBe(v1);
    expect(full.version).toBe(2);
  });

  test("Clone creates an unpublished copy; Delete removes it", async ({ page }) => {
    const slug = `cprobe-${uid()}`;
    await login(page);
    const created = await page.request.post("/api/v1/agents", {
      data: { name: `CProbe ${slug}`, slug, kind: "agent", config: { kind: "agent", instructions: "Clone me." } },
    });
    expect(created.ok()).toBeTruthy();

    await page.goto("/agent-studio");
    await expect(page.getByTestId("agent-list")).toBeVisible();
    await page.getByTestId("agent-list-search").fill(slug);

    await openActions(page, slug, "agent-clone");
    await page.getByTestId("agent-clone").click();
    await page.waitForURL(/slug=/, { timeout: 20_000 });
    const cloneSlug = new URL(page.url()).searchParams.get("slug");
    expect(cloneSlug).toBeTruthy();
    expect(cloneSlug).not.toBe(slug);

    const copy = await (await page.request.get(`/api/v1/agents/${cloneSlug}?full=1`)).json();
    expect(copy.published).toBe(false);
    expect(copy.version).toBe(1);
    expect(copy.config.instructions).toContain("Clone me.");

    await page.goto("/agent-studio");
    await expect(page.getByTestId("agent-list")).toBeVisible();
    await page.getByTestId("agent-list-search").fill(cloneSlug!);
    await openActions(page, cloneSlug!, "agent-delete");
    await page.getByTestId("agent-delete").click();
    await expect(page.getByTestId("agent-delete-dialog")).toBeVisible();
    await page.getByTestId("agent-delete-confirm").click();
    await expect(page.locator(`tr[data-slug="${cloneSlug}"]`)).toHaveCount(0, { timeout: 20_000 });
  });

  test("Chat ?agent= preselects the agent chip", async ({ page }) => {
    await login(page);
    await page.goto("/agent?agent=echo");
    await expect(page.getByTestId("agent-composer")).toBeVisible();
    await expect(page.getByTestId("agent-chip")).toContainText(/echo/i, { timeout: 20_000 });
  });

  test("Chat ?agent= preselects an unpublished draft with a Draft preview badge", async ({ page }) => {
    await login(page);
    const slug = `draft-${uid()}`;
    const created = await page.request.post("/api/v1/agents", {
      data: { name: `Draft ${slug}`, slug, kind: "agent", config: { kind: "agent", instructions: "Draft preview probe." } },
    });
    expect(created.ok()).toBeTruthy();
    const saved = await created.json();
    expect(saved.published).toBe(false);

    await page.goto(`/agent?agent=${slug}`);
    await expect(page.getByTestId("agent-composer")).toBeVisible();
    await expect(page.getByTestId("agent-chip")).toContainText(new RegExp(slug, "i"), { timeout: 20_000 });
    await expect(page.getByTestId("draft-badge")).toContainText(/Draft preview/i, { timeout: 20_000 });

    // Inaccessible slugs keep the existing "no longer available" path.
    await page.goto("/agent?agent=missing-draft-xyz");
    await expect(page.getByTestId("agent-composer")).toBeVisible();
    await expect(page.getByTestId("chat-error")).toContainText(/no longer available/i, { timeout: 20_000 });
  });
});
