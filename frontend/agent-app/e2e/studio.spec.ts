import { test, expect, type Page } from "@playwright/test";

/** Node's Buffer, declared locally so the spec needs no @types/node. */
declare const Buffer: { from(input: string, encoding?: string): Uint8Array };

/** Agent Studio v2 editor.
 *
 * The DOM contract these tests drive (`studio-graph`, `palette-*`,
 * `studio-node-*`, `inspector-*`, `testrun-*`) is documented in
 * docs/agent-studio-v2.md §4 and must keep working: the editor is the API.
 */

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("admin123");
  await page.locator("button[type=submit]").click();
  await page.waitForURL((url) => !url.pathname.includes("/login"));
}

async function openEditor(page: Page, query = "") {
  await login(page);
  await page.goto(`/agent-studio/editor${query}`);
  await expect(page.getByTestId("agent-studio")).toBeVisible();
  await expect(page.getByTestId("studio-palette")).toBeVisible();
  await expect(page.getByTestId("studio-canvas")).toBeVisible();
}

async function freshEditor(page: Page) {
  await page.goto("/agent-studio/editor");
  await expect(page.getByTestId("studio-graph")).toHaveAttribute("data-node-count", "0");
}

function graph(page: Page) {
  return page.getByTestId("studio-graph");
}

/** Palette items are catalog-driven; clicking one adds (or loads) it. */
async function addFromPalette(page: Page, type: string) {
  await page.getByTestId(`palette-${type}`).click();
}

async function fillInstructions(page: Page, text: string) {
  await page.getByTestId("inspector-instructions").fill(text);
}

async function save(page: Page) {
  await page.getByTestId("studio-save").click();
  await expect(page.getByTestId("studio-status")).toContainText(/Saved/i, { timeout: 20_000 });
}

test.describe("Agent Studio v2", () => {
  test("new agent: add, guardrails, save, plan and reload", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Canvas Probe");
    await addFromPalette(page, "agent");
    await expect(page.getByTestId("studio-inspector")).toHaveAttribute("data-node-type", "agent");
    await page.getByTestId("inspector-name").fill("Reviewer");
    await fillInstructions(page, "Review the workspace with evidence.");
    await expect(graph(page)).toHaveAttribute("data-kind", "agent");
    await expect(graph(page)).toHaveAttribute("data-node-types", /agent/);

    // Guardrail: add summarization through the registry-driven picker.
    await page.getByTestId("middleware-add").click();
    await page.getByTestId("middleware-option-summarization").click();
    await expect(page.getByTestId("middleware-summarization")).toBeVisible();
    await expect(page.getByTestId("middleware-summarization")).toContainText("SummarizationMiddleware");

    // Structured output.
    await page.getByTestId("output-toggle").click();
    await expect(page.getByTestId("output-schema")).toBeVisible();

    await save(page);
    await expect(page).toHaveURL(/slug=canvas-probe/);

    // The plan names the exact builders a run would use.
    await page.getByTestId("inspector-tab-plan").click();
    await expect(page.getByTestId("studio-plan")).toContainText("langchain.agents.create_agent");
    await expect(page.getByTestId("studio-plan")).toContainText("SummarizationMiddleware");

    const saved = await (await page.request.get("/api/v1/agents/canvas-probe?full=1")).json();
    expect(saved.config.schema).toBe(2);
    expect(saved.config.instructions).toContain("Review the workspace");
    expect(saved.config.middleware.summarization.trigger_tokens).toBe(8000);
    expect(saved.config.response_format.schema.title).toBe("Result");

    await page.reload();
    await expect(page.getByTestId("studio-graph")).toHaveAttribute("data-node-types", /agent/, { timeout: 20_000 });
    // Nothing is selected on load: the dock shows the flow settings until a node
    // is clicked, which is where the saved agent settings must reappear.
    await expect(page.getByTestId("studio-inspector")).toHaveAttribute("data-node-type", "flow");
    await page.getByTestId("studio-node-agent").first().click();
    await expect(page.getByTestId("inspector-name")).toHaveValue("Reviewer");
    await expect(page.getByTestId("middleware-summarization")).toBeVisible();
  });

  test("validation shows errors inline and in the report panel", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Invalid Probe");
    await addFromPalette(page, "agent");
    await save(page);
    await page.getByTestId("studio-validate").click();
    await expect(page.getByTestId("studio-status")).toContainText(/Validation failed/i, { timeout: 20_000 });
    await page.getByTestId("inspector-tab-report").click();
    await expect(page.getByTestId("studio-report")).toHaveAttribute("data-ok", "0");
    await expect(page.getByTestId("studio-report")).toContainText(/instructions/i);
  });

  test("publish makes the agent available to the chat app", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Published Studio Agent");
    await addFromPalette(page, "agent");
    await page.getByTestId("inspector-name").fill("Published Studio Agent");
    await fillInstructions(page, "You are published from Agent Studio.");
    await save(page);
    await page.getByTestId("studio-publish").click();
    await expect(page.getByTestId("studio-status")).toContainText(/Published/i, { timeout: 20_000 });

    const listed = await page.request.get("/api/v1/agents");
    const slugs = ((await listed.json()) as { agents: { slug: string }[] }).agents.map((a) => a.slug);
    expect(slugs).toContain("published-studio-agent");
  });

  test("routing blueprint: a router node's routes drive config.graph", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Routing Probe");
    await addFromPalette(page, "routing");
    await expect(graph(page)).toHaveAttribute("data-pattern", "graph");
    await expect(graph(page)).toHaveAttribute("data-template", "routing");
    await expect(graph(page)).toHaveAttribute("data-node-types", /router/);
    await expect(page.getByTestId("studio-node-router").first()).toBeVisible();
    await expect(page.getByTestId("studio-node-agent")).toHaveCount(2);

    // Edit the first route's name from the router inspector.
    await page.getByTestId("studio-node-router").first().click();
    await expect(page.getByTestId("studio-inspector")).toHaveAttribute("data-node-type", "router");
    const firstRoute = page.locator(".as-route-row input").first();
    await firstRoute.fill("billing");
    await save(page);

    const saved = await (await page.request.get("/api/v1/agents/routing-probe?full=1")).json();
    expect(saved.kind).toBe("workflow");
    expect(saved.config.pattern).toBe("graph");
    expect(saved.config.template).toBe("routing");
    const router = saved.config.graph.nodes.find((n: { kind: string }) => n.kind === "router");
    expect(router.routes[0].name).toBe("billing");
    expect(router.routes[0].to).toBeTruthy();
    expect(saved.config.graph.entry).toBe(router.id);
    expect(saved.config.graph.edges.length).toBeGreaterThan(1);

    const validated = await (await page.request.post("/api/v1/agents/routing-probe/validate", { data: {} })).json();
    expect(validated.ok ?? validated.valid).toBeTruthy();
    expect(validated.errors ?? []).toHaveLength(0);
  });

  test("supervisor and swarm canvases compile", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Supervisor Probe");
    await addFromPalette(page, "agent");
    await page.getByTestId("inspector-name").fill("WorkerA");
    await fillInstructions(page, "Answer as a specialist.");
    await addFromPalette(page, "supervisor");
    await expect(graph(page)).toHaveAttribute("data-pattern", "supervisor");
    await expect(graph(page)).toHaveAttribute("data-edges", /agent->supervisor/);
    await page.getByTestId("studio-node-supervisor").first().click();
    await page.getByTestId("inspector-manager-name").fill("Lead");
    await expect(page.getByTestId("inspector-participants")).toContainText("WorkerA");
    await save(page);

    const supervisor = await (await page.request.get("/api/v1/agents/supervisor-probe?full=1")).json();
    expect(supervisor.config.pattern).toBe("supervisor");
    expect(supervisor.config.manager.name).toBe("Lead");
    expect(supervisor.config.participants).toHaveLength(1);
    const validated = await (await page.request.post("/api/v1/agents/supervisor-probe/validate", { data: {} })).json();
    expect(validated.ok ?? validated.valid).toBeTruthy();
    expect(validated.errors ?? []).toHaveLength(0);

    await freshEditor(page);
    await page.getByTestId("studio-title").fill("Swarm Probe");
    await addFromPalette(page, "swarm");
    await addFromPalette(page, "agent");
    await page.getByTestId("inspector-name").fill("Triage");
    await fillInstructions(page, "Route to a specialist.");
    await addFromPalette(page, "agent");
    await page.getByTestId("inspector-name").fill("Specialist");
    await fillInstructions(page, "Answer the question.");
    await expect(graph(page)).toHaveAttribute("data-pattern", "swarm");
    await save(page);

    const swarm = await (await page.request.get("/api/v1/agents/swarm-probe?full=1")).json();
    expect(swarm.config.pattern).toBe("swarm");
    expect(swarm.config.participants).toHaveLength(2);
    expect(swarm.config.start_agent).toBeTruthy();
  });

  test("deep agent with one subagent and a skill", async ({ page }) => {
    await openEditor(page);
    await page.getByTestId("studio-title").fill("Deep Probe");
    await addFromPalette(page, "deep_agent");
    await expect(graph(page)).toHaveAttribute("data-node-types", /deep_agent/);
    await page.getByTestId("inspector-name").fill("Researcher");
    await fillInstructions(page, "Plan, delegate and cite sources.");
    await page.getByTestId("subagent-add").click();
    await page.getByTestId("subagent-name-0").fill("scout");
    await save(page);

    const saved = await (await page.request.get("/api/v1/agents/deep-probe?full=1")).json();
    expect(saved.config.runtime).toBe("deep_agent");
    expect(saved.config.deep_agent.subagents[0].name).toBe("scout");
  });

  test("test run: HITL deny then approve through the run dock", async ({ page }) => {
    const requested: string[] = [];
    page.on("request", (request) => requested.push(new URL(request.url()).pathname));
    await openEditor(page, "?slug=hitl-writer");
    await expect(graph(page)).toHaveAttribute("data-node-types", /agent/, { timeout: 20_000 });

    await page.getByTestId("studio-testrun").click();
    await expect(page.getByTestId("testrun-drawer")).toBeVisible();
    await page.getByTestId("testrun-input").fill("write the file");
    await page.getByTestId("testrun-send").click();
    await expect(page.getByTestId("hitl-card")).toBeVisible({ timeout: 25_000 });
    const denyRunId = await page.getByTestId("testrun-drawer").getAttribute("data-run-id");
    expect(denyRunId).toBeTruthy();
    await page.getByTestId("hitl-deny").click();
    await page.getByTestId("hitl-confirm-deny").click();
    await expect(page.getByTestId("testrun-drawer")).toHaveAttribute("data-streaming", "0", { timeout: 25_000 });
    await expect.poll(async () => {
      const body = await (await page.request.get(`/api/v1/runs/${denyRunId}`)).json();
      return body.status;
    }).not.toBe("awaiting_approval");

    await page.getByTestId("testrun-input").fill("write the file again");
    await page.getByTestId("testrun-send").click();
    await expect(page.getByTestId("hitl-card")).toBeVisible({ timeout: 25_000 });
    const approveRunId = await page.getByTestId("testrun-drawer").getAttribute("data-run-id");
    expect(approveRunId).toBeTruthy();
    expect(approveRunId).not.toBe(denyRunId);
    await page.getByTestId("hitl-approve").click();
    await expect(page.getByTestId("testrun-drawer")).toHaveAttribute("data-streaming", "0", { timeout: 25_000 });
    await expect.poll(async () => {
      const body = await (await page.request.get(`/api/v1/runs/${approveRunId}`)).json();
      return body.status;
    }).not.toBe("awaiting_approval");

    expect(requested.some((path) => path === "/api/v1/runs")).toBeTruthy();
    expect(requested.some((path) => /\/api\/v1\/runs\/.+\/approvals/.test(path))).toBeTruthy();
  });

  test("skills are authored in the Skill Library and bound in the inspector", async ({ page }) => {
    // Skills are created through the admin API (the Studio only loads them),
    // so the session must exist before the request is made.
    await login(page);
    const created = await page.request.post("/api/v1/skills", {
      data: {
        name: "pw-probe-skill",
        description: "Playwright-created skill",
        instructions: "Advertise this skill and mention probe-token.",
      },
    });
    expect(created.ok()).toBeTruthy();
    const saved = await (await page.request.get("/api/v1/skills/pw-probe-skill")).json();
    expect(saved.skill_md).toMatch(/name: pw-probe-skill/);
    expect(saved.instructions).toMatch(/probe-token/);

    await page.goto("/agent-studio/editor");
    await expect(page.getByTestId("agent-studio")).toBeVisible();
    // No authoring surface in the Studio: the palette advertises no skill editor.
    await expect(page.getByTestId("studio-skills")).toHaveCount(0);
    await expect(page.getByTestId("skill-editor")).toHaveCount(0);

    await page.getByTestId("studio-title").fill("Skill Probe Agent");
    await addFromPalette(page, "agent");
    await fillInstructions(page, "Use the probe skill.");
    await page.getByTestId("skill-pw-probe-skill").check();
    await expect(page.getByTestId("skill-selected-count")).toContainText("1 selected");
    await expect(page.getByTestId("selected-skill-pw-probe-skill")).toBeVisible();
    // The bubble's × unselects it, and the checkbox follows.
    await page.getByTestId("selected-skill-pw-probe-skill").locator(".as-chip-remove").click();
    await expect(page.getByTestId("skill-pw-probe-skill")).not.toBeChecked();
    await expect(page.getByTestId("skill-selected-count")).toContainText("0 selected");
    await page.getByTestId("skill-pw-probe-skill").check();
    await save(page);

    const agent = await (await page.request.get("/api/v1/agents/skill-probe-agent?full=1")).json();
    expect(agent.config.maf_skill_ids).toEqual(expect.arrayContaining(["pw-probe-skill"]));
  });

  test("legacy top-level graph nodes keep their agent spec on load and save", async ({ page }) => {
    // Pre-v2 definitions stored the agent spec on the node itself
    // (`instructions`, `mcp_bindings`, …) instead of under `agent`. Loading and
    // saving one must not drop any of it.
    await login(page);
    await page.goto("/agent-studio/editor");
    await expect(page.getByTestId("agent-studio")).toBeVisible();
    const legacy = {
      name: "Legacy Shape Probe",
      slug: "legacy-shape-probe",
      kind: "workflow",
      config: {
        kind: "workflow",
        schema: 2,
        pattern: "graph",
        template: "custom",
        model: { client: "default", name: null },
        entry: "Intake",
        nodes: [
          { id: "Intake", name: "Intake", instructions: "Frame the question in one sentence." },
          {
            id: "Analyst",
            name: "Analyst",
            instructions: "Investigate with at most three tool calls, then report findings.",
            mcp_bindings: [{ server: "Workspace", tools: ["read_file"], approval: [] }],
            model: { client: "default", name: null },
          },
          { id: "Closer", name: "Closer", instructions: "Answer briefly from the findings." },
        ],
        edges: [
          { from: "Intake", to: "Analyst" },
          { from: "Analyst", to: "Closer" },
        ],
      },
    };
    await page.locator('input[type="file"][accept="application/json"]').setInputFiles({
      name: "legacy-shape.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(legacy)),
    });
    await expect(page.getByTestId("studio-graph")).toHaveAttribute("data-node-types", /agent,agent,agent/);
    await expect(page.getByTestId("studio-node-agent").first()).toContainText("Frame the question");

    await save(page);
    const saved = await (await page.request.get("/api/v1/agents/legacy-shape-probe?full=1")).json();
    expect(saved.kind).toBe("workflow");
    const nodes = Object.fromEntries(
      (saved.config.graph.nodes as { id: string; agent: Record<string, unknown> }[]).map((n) => [n.id, n.agent]),
    );
    expect(nodes.Intake.instructions).toBe("Frame the question in one sentence.");
    expect(nodes.Analyst.instructions).toBe("Investigate with at most three tool calls, then report findings.");
    expect(nodes.Closer.instructions).toBe("Answer briefly from the findings.");
    expect(JSON.stringify(nodes.Analyst.mcp_bindings)).toContain("read_file");

    const validated = await (
      await page.request.post("/api/v1/agents/legacy-shape-probe/validate", { data: {} })
    ).json();
    expect(validated.ok ?? validated.valid).toBeTruthy();
    expect((validated.errors ?? []).map((e: { code: string }) => e.code)).not.toContain("missing_instructions");
  });

  test("agent list page keeps its DataTable contract", async ({ page }) => {
    await login(page);
    await page.goto("/agent-studio");
    await expect(page.getByTestId("agent-list")).toBeVisible();
    await expect(page.getByTestId("agent-table")).toHaveClass(/chat-datatable-table/);
    await expect(page.getByTestId("agent-page-size")).toBeVisible();
    await expect(page.getByTestId("agent-list-search")).toBeVisible();
    const headers = page.getByTestId("agent-table").locator("th");
    await expect(headers.first()).toHaveClass(/sorting/);
    await headers.first().click();
    await expect(headers.first()).toHaveClass(/sorting_desc|sorting_asc/);
  });
});
