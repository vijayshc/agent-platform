/** Tools & knowledge: selected-tool bubbles, live MCP tool lists, skills, tools.
 *
 * The "Selected" row at the top of each section is the author's answer to "what
 * is actually attached to this agent?" — one chip per selection, labelled
 * `<server>/<tool>` with a `(HITL)` suffix when the tool needs approval. Chips
 * are interactive in both directions: the server list checks the box, the chip's
 * × unchecks it, and the shield toggles approval in place.
 */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, RefreshCw, ShieldCheck, X } from "lucide-react";
import { mcpServerTools } from "../api";
import type { McpToolDetail, StudioMcpServer } from "../../types";
import { functionToolOptions } from "../model/catalog";
import type { McpBinding, NodeData, StudioCatalog } from "../model/types";
import { Field, Section, TagsInput, Toggle } from "./Fields";

interface ToolListState {
  tools: string[];
  details: McpToolDetail[];
  error: string | null;
  loading: boolean;
}

/** A tool needs a human decision when the author said so or the server does. */
function needsApproval(binding: McpBinding | undefined, tool: string, detail?: McpToolDetail): boolean {
  if (!binding) return false;
  if ((binding.approval ?? []).includes(tool)) return true;
  const mode = (detail as { approval_mode?: string } | undefined)?.approval_mode;
  return mode === "always_require" || mode === "approval";
}

export function ToolsForm({
  data,
  catalog,
  modules,
  onChange,
}: {
  data: NodeData;
  catalog: StudioCatalog | null;
  /** Admin module keys the signed-in user may open. */
  modules: string[];
  onChange: (patch: Partial<NodeData>) => void;
}) {
  const servers: StudioMcpServer[] = catalog?.resources?.mcp_servers ?? [];
  const skills = catalog?.resources?.skills ?? [];
  const functions = functionToolOptions(catalog);
  const bindings = data.mcpBindings ?? [];
  const [expanded, setExpanded] = useState<number | null>(null);
  const [live, setLive] = useState<Record<number, ToolListState>>({});

  const boundIds = useMemo(
    () => new Set(bindings.map((b) => b.serverId).filter((id): id is number => id != null)),
    [bindings],
  );
  const selectedTools = useMemo(
    () =>
      bindings.flatMap((binding) =>
        (binding.tools ?? []).map((tool) => ({
          binding,
          tool,
          server: binding.serverName || String(binding.serverId ?? ""),
          hitl: (binding.approval ?? []).includes(tool),
        })),
      ),
    [bindings],
  );

  async function loadTools(serverId: number) {
    setLive((state) => ({
      ...state,
      [serverId]: {
        tools: state[serverId]?.tools ?? [],
        details: state[serverId]?.details ?? [],
        error: null,
        loading: true,
      },
    }));
    try {
      const server = await mcpServerTools(serverId);
      setLive((state) => ({
        ...state,
        [serverId]: {
          tools: server.tools ?? [],
          details: server.tool_details ?? [],
          error: server.tools_error ?? null,
          loading: false,
        },
      }));
    } catch (error) {
      setLive((state) => ({
        ...state,
        [serverId]: {
          tools: [],
          details: [],
          error: error instanceof Error ? error.message : String(error),
          loading: false,
        },
      }));
    }
  }

  // A bound server whose tools are unknown is fetched immediately: the author
  // must see the real tool list, never a stale empty one.
  useEffect(() => {
    for (const id of boundIds) {
      if (!live[id] && servers.some((s) => s.id === id)) void loadTools(id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundIds, servers]);

  function setBindings(next: McpBinding[]) {
    onChange({ mcpBindings: next });
  }

  function toggleServer(server: StudioMcpServer, on: boolean) {
    if (on) {
      setBindings([...bindings, { serverId: server.id, serverName: server.name, tools: [], approval: [] }]);
      setExpanded(server.id);
      if (!live[server.id]) void loadTools(server.id);
      return;
    }
    setBindings(bindings.filter((b) => b.serverId !== server.id));
  }

  function patchBinding(serverId: number, patch: Partial<McpBinding>) {
    setBindings(bindings.map((b) => (b.serverId === serverId ? { ...b, ...patch } : b)));
  }

  function toggleTool(serverId: number, tool: string, on: boolean) {
    const binding = bindings.find((b) => b.serverId === serverId);
    if (!binding) return;
    const tools = on ? [...binding.tools, tool] : binding.tools.filter((t) => t !== tool);
    patchBinding(serverId, {
      tools,
      // Deselecting a tool also drops its approval requirement.
      approval: on ? binding.approval : binding.approval.filter((t) => t !== tool),
    });
  }

  function toggleApproval(serverId: number, tool: string, on?: boolean) {
    const binding = bindings.find((b) => b.serverId === serverId);
    if (!binding) return;
    const active = on ?? !binding.approval.includes(tool);
    patchBinding(serverId, {
      approval: active ? [...new Set([...binding.approval, tool])] : binding.approval.filter((t) => t !== tool),
    });
  }

  function toggleFunction(name: string, on: boolean) {
    const current = data.functionTools ?? [];
    onChange({ functionTools: on ? [...current, name] : current.filter((t) => t !== name) });
  }

  function setSkills(next: string[]) {
    onChange({ skillIds: next });
  }

  return (
    <>
      <Section
        title="MCP servers"
        subtitle="Tools the agent can call. Selecting a server loads its live tool list."
        actions={
          <span className="as-chip" data-testid="mcp-selected-count">
            {selectedTools.length} selected
          </span>
        }
      >
        <SelectedRow
          testId="mcp-selected"
          empty="No MCP tool selected yet — pick one below."
          items={selectedTools.map((entry) => ({
            key: `${entry.server}/${entry.tool}`,
            label: `${entry.server}/${entry.tool}`,
            hitl: entry.hitl,
            testId: `selected-tool-${entry.server}-${entry.tool}`,
            onRemove: () => toggleTool(entry.binding.serverId as number, entry.tool, false),
            onToggleHitl: () => toggleApproval(entry.binding.serverId as number, entry.tool),
          }))}
        />

        {!servers.length ? <p className="as-empty">No MCP servers are configured on this deployment.</p> : null}
        <div className="as-tool-servers">
          {servers.map((server) => {
            const binding = bindings.find((b) => b.serverId === server.id);
            const state = live[server.id];
            const details = state ?? { tools: [], details: [], error: null, loading: false };
            const open = expanded === server.id;
            return (
              <div className={`as-tool-server${binding ? " is-bound" : ""}`} key={server.id}>
                <div className="as-tool-server-head">
                  <Toggle
                    label={server.name}
                    checked={Boolean(binding)}
                    onChange={(on) => toggleServer(server, on)}
                    testId={`mcp-server-${server.name}`}
                  />
                  <span className="as-chip">{server.server_type}</span>
                  {binding ? <span className="as-chip as-chip-ok">{binding.tools.length} selected</span> : null}
                  <div className="as-tool-server-actions">
                    <button
                      type="button"
                      className="as-btn as-btn-icon as-btn-ghost"
                      title="Refresh tool list"
                      onClick={() => void loadTools(server.id)}
                      disabled={details.loading}
                    >
                      <RefreshCw size={13} className={details.loading ? "as-spin" : undefined} />
                    </button>
                    {binding ? (
                      <button
                        type="button"
                        className="as-btn as-btn-icon as-btn-ghost"
                        title={open ? "Hide tools" : "Choose tools"}
                        onClick={() => setExpanded(open ? null : server.id)}
                      >
                        <ChevronDown size={13} className={open ? "as-flip" : undefined} />
                      </button>
                    ) : null}
                  </div>
                </div>
                {details.error ? (
                  <p className="as-error as-tool-error">
                    <AlertTriangle size={12} /> {details.error}
                  </p>
                ) : null}
                {open && binding ? (
                  <div className="as-tool-list">
                    {details.loading && !details.tools.length ? <p className="as-muted">Loading tools…</p> : null}
                    {!details.loading && !details.tools.length && !details.error ? (
                      <p className="as-muted">This server exposes no tools.</p>
                    ) : null}
                    {details.tools.map((tool) => {
                      const detail = details.details.find((d) => d.name === tool);
                      const selected = binding.tools.includes(tool);
                      const hitl = needsApproval(binding, tool, detail);
                      return (
                        <div className={`as-tool-row${selected ? " is-selected" : ""}`} key={tool}>
                          <label className="as-tool-pick" title={detail?.description ?? tool}>
                            <input
                              type="checkbox"
                              id={`mcp-tool-${server.id}-${tool}`}
                              name={`mcp-tool-${server.id}-${tool}`}
                              data-testid={`mcp-tool-${tool}`}
                              checked={selected}
                              onChange={(event) => toggleTool(server.id, tool, event.target.checked)}
                            />
                            <span className="as-tool-name">{tool}</span>
                            {detail?.description ? <span className="as-tool-desc">{detail.description}</span> : null}
                          </label>
                          <button
                            type="button"
                            className={`as-hitl-toggle${hitl ? " is-on" : ""}`}
                            aria-pressed={hitl}
                            disabled={!selected}
                            title={
                              !selected
                                ? "Select the tool first"
                                : hitl
                                  ? "Approval required before this tool runs — click to remove"
                                  : "Require a human approval before this tool runs"
                            }
                            data-testid={`mcp-tool-hitl-${tool}`}
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              toggleApproval(server.id, tool);
                            }}
                          >
                            <ShieldCheck size={12} /> HITL
                          </button>
                        </div>
                      );
                    })}
                    <div className="as-tool-bulk">
                      <button
                        type="button"
                        className="as-btn as-btn-ghost as-btn-sm"
                        onClick={() => patchBinding(server.id, { tools: [...details.tools] })}
                        disabled={!details.tools.length}
                      >
                        Select all
                      </button>
                      <button
                        type="button"
                        className="as-btn as-btn-ghost as-btn-sm"
                        onClick={() => patchBinding(server.id, { tools: [], approval: [] })}
                        disabled={!binding.tools.length}
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </Section>

      <Section
        title="Skills"
        subtitle="Advertised to the model before it plans. Packages are authored in the Skill Library."
        actions={
          <>
            <span className="as-chip" data-testid="skill-selected-count">
              {(data.skillIds ?? []).length} selected
            </span>
            {modules.includes("skills") ? (
              <a
                className="as-btn as-btn-sm"
                href="/admin/skills"
                target="_blank"
                rel="noreferrer"
                title="Open the Skill Library in a new tab"
                data-testid="studio-manage-skills"
              >
                Manage skills
              </a>
            ) : null}
          </>
        }
      >
        <SelectedRow
          testId="skill-selected"
          empty="No skill selected yet."
          items={(data.skillIds ?? []).map((name) => ({
            key: name,
            label: name,
            testId: `selected-skill-${name}`,
            onRemove: () => setSkills((data.skillIds ?? []).filter((s) => s !== name)),
          }))}
        />
        {!skills.length ? (
          <p className="as-empty">No skill packages yet — create one in the Skill Library.</p>
        ) : null}
        <div className="as-check-list">
          {skills.map((skill) => (
            <label className="as-check-row" key={skill.name} title={skill.description ?? skill.name}>
              <input
                type="checkbox"
                id={`skill-${skill.name}`}
                name={`skill-${skill.name}`}
                data-testid={`skill-${skill.name}`}
                checked={(data.skillIds ?? []).includes(skill.name)}
                onChange={(event) => {
                  const current = data.skillIds ?? [];
                  setSkills(event.target.checked ? [...current, skill.name] : current.filter((s) => s !== skill.name));
                }}
              />
              <span className="as-check-name">{skill.name}</span>
              {skill.description ? <span className="as-check-desc">{skill.description}</span> : null}
            </label>
          ))}
        </div>
      </Section>

      <Section
        title="Function tools"
        subtitle="Built-in platform tools, callable without an MCP server."
        actions={
          <span className="as-chip" data-testid="function-selected-count">
            {(data.functionTools ?? []).length} selected
          </span>
        }
      >
        <SelectedRow
          testId="function-selected"
          empty="No function tool selected yet."
          items={(data.functionTools ?? []).map((name) => ({
            key: name,
            label: name,
            testId: `selected-function-${name}`,
            onRemove: () => toggleFunction(name, false),
          }))}
        />
        {!functions.length ? <p className="as-empty">This deployment exposes no function tools.</p> : null}
        <div className="as-check-list">
          {functions.map((tool) => (
            <label className="as-check-row" key={tool.value} title={tool.label}>
              <input
                type="checkbox"
                id={`function-tool-${tool.value}`}
                name={`function-tool-${tool.value}`}
                data-testid={`function-tool-${tool.value}`}
                checked={(data.functionTools ?? []).includes(tool.value)}
                onChange={(event) => toggleFunction(tool.value, event.target.checked)}
              />
              <span className="as-check-name">{tool.label}</span>
            </label>
          ))}
        </div>
      </Section>

      <Section title="Approval overrides" subtitle="Per-server tool lists that require a human decision.">
        {!bindings.length ? <p className="as-muted">Bind an MCP server first.</p> : null}
        {bindings.map((binding) => (
          <Field key={String(binding.serverId)} label={`${binding.serverName || binding.serverId} — tools needing approval`}>
            <TagsInput
              value={binding.approval}
              onChange={(approval) => patchBinding(binding.serverId as number, { approval })}
              placeholder="Tool name, then Enter"
            />
          </Field>
        ))}
      </Section>
    </>
  );
}

interface SelectedItem {
  key: string;
  label: string;
  testId: string;
  hitl?: boolean;
  onRemove: () => void;
  onToggleHitl?: () => void;
}

/** The "what is attached right now" row shared by all three tool sections. */
function SelectedRow({
  testId,
  items,
  empty,
}: {
  testId: string;
  items: SelectedItem[];
  empty: string;
}) {
  return (
    <div className="as-selected-row" data-testid={testId}>
      <span className="as-selected-label">Selected</span>
      {items.length ? (
        <div className="as-selected-chips">
          {items.map((item) => (
            <span className="as-selected-chip" key={item.key} title={item.label} data-testid={item.testId}>
              {item.onToggleHitl ? (
                <button
                  type="button"
                  className={`as-chip-hitl${item.hitl ? " is-on" : ""}`}
                  aria-pressed={Boolean(item.hitl)}
                  aria-label={`${item.hitl ? "Remove" : "Require"} approval for ${item.label}`}
                  title={
                    item.hitl
                      ? "Approval required before this tool runs — click to remove"
                      : "Require a human approval before this tool runs"
                  }
                  onClick={item.onToggleHitl}
                >
                  <ShieldCheck size={11} />
                </button>
              ) : null}
              <span className="as-selected-name">
                {item.label}
                {item.hitl ? <span className="as-selected-hitl"> (HITL)</span> : null}
              </span>
              <button
                type="button"
                className="as-chip-remove"
                aria-label={`Remove ${item.label}`}
                title={`Remove ${item.label}`}
                onClick={item.onRemove}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <span className="as-selected-empty">{empty}</span>
      )}
    </div>
  );
}
