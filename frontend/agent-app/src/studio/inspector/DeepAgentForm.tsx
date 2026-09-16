/** Deep agent extras: subagents, memory files, filesystem permissions, interrupts.
 *
 * These map one-to-one onto `deepagents.create_deep_agent` arguments via
 * `config.deep_agent` (see docs/agent-studio-v2.md §1).
 */
import { Plus, Trash2 } from "lucide-react";
import { modelClientOptions } from "../model/catalog";
import type { DeepPermission, DeepSubagent, NodeData, StudioCatalog } from "../model/types";
import { emptyDeepAgent } from "../model/types";
import { Collapsible, Field, Section, Select, TagsInput, Toggle } from "./Fields";

const OPERATIONS = ["read", "write", "execute"];
const MODES = ["ask", "allow", "deny"];

export function DeepAgentForm({
  data,
  catalog,
  onChange,
}: {
  data: NodeData;
  catalog: StudioCatalog | null;
  onChange: (patch: Partial<NodeData>) => void;
}) {
  const spec = data.deepAgent ?? emptyDeepAgent();
  const modelOptions = [{ value: "", label: "Inherit the agent model" }, ...modelClientOptions(catalog)];

  function patch(next: Partial<typeof spec>) {
    onChange({ deepAgent: { ...spec, ...next } });
  }

  function setSubagent(index: number, next: Partial<DeepSubagent>) {
    patch({ subagents: spec.subagents.map((s, i) => (i === index ? { ...s, ...next } : s)) });
  }

  function setPermission(index: number, next: Partial<DeepPermission>) {
    patch({ permissions: spec.permissions.map((p, i) => (i === index ? { ...p, ...next } : p)) });
  }

  const knownTools = [
    ...(data.mcpBindings ?? []).flatMap((b) => b.tools),
    ...(data.functionTools ?? []),
    ...Object.keys(spec.interruptOn ?? {}),
  ].filter((tool, index, all) => all.indexOf(tool) === index);

  return (
    <>
      <Section
        title="Subagents"
        subtitle="Delegate a scoped job to a named subagent with its own prompt and tools."
        actions={
          <button
            type="button"
            className="as-btn as-btn-sm"
            data-testid="subagent-add"
            onClick={() =>
              patch({
                subagents: [
                  ...spec.subagents,
                  { name: "", description: "", prompt: "", tools: [], model: "" },
                ],
              })
            }
          >
            <Plus size={13} /> Add
          </button>
        }
      >
        {!spec.subagents.length ? (
          <p className="as-empty" data-testid="subagent-empty">
            No subagents. The deep agent plans and works on its own until you add one.
          </p>
        ) : null}
        {spec.subagents.map((subagent, index) => (
          <div className="as-subagent" key={index} data-testid={`subagent-${index}`}>
            <div className="as-subagent-head">
              <span className="as-subagent-title">Subagent {index + 1}</span>
              <button
                type="button"
                className="as-btn as-btn-icon as-btn-ghost"
                title="Remove subagent"
                onClick={() => patch({ subagents: spec.subagents.filter((_, i) => i !== index) })}
              >
                <Trash2 size={13} />
              </button>
            </div>
            <Field label="Name">
              <input
                className="as-input"
                value={subagent.name}
                data-testid={`subagent-name-${index}`}
                placeholder="researcher"
                onChange={(event) => setSubagent(index, { name: event.target.value })}
              />
            </Field>
            <Field label="Description" help="What this subagent is for — the planner reads it.">
              <input
                className="as-input"
                value={subagent.description}
                placeholder="Finds primary sources and returns citations"
                onChange={(event) => setSubagent(index, { description: event.target.value })}
              />
            </Field>
            <Field label="Instructions">
              <textarea
                className="as-textarea"
                rows={4}
                value={subagent.prompt}
                placeholder="You are a meticulous researcher…"
                onChange={(event) => setSubagent(index, { prompt: event.target.value })}
              />
            </Field>
            <Field label="Tools" help="Tool names this subagent may call. Empty inherits the parent's tools.">
              <TagsInput value={subagent.tools} onChange={(tools) => setSubagent(index, { tools })} />
            </Field>
            <Field label="Model">
              <Select
                value={subagent.model}
                options={modelOptions}
                onChange={(model) => setSubagent(index, { model })}
              />
            </Field>
          </div>
        ))}
      </Section>

      <Section title="Memory files" subtitle="Files the agent keeps across runs (AGENTS.md conventions).">
        <Field label="Paths" help="Type a path, then press Enter to add it (clicking away also commits).">
          <TagsInput
            value={spec.memory}
            onChange={(memory) => patch({ memory })}
            placeholder="/memories/AGENTS.md"
          />
        </Field>
      </Section>

      <Section
        title="Permissions"
        subtitle="Filesystem policy the runtime enforces before a tool touches a path."
        actions={
          <button
            type="button"
            className="as-btn as-btn-sm"
            onClick={() => patch({ permissions: [...spec.permissions, { operations: ["write"], paths: [], mode: "ask" }] })}
          >
            <Plus size={13} /> Add rule
          </button>
        }
      >
        {!spec.permissions.length ? <p className="as-empty">No rules — the runtime default policy applies.</p> : null}
        {spec.permissions.map((permission, index) => (
          <div className="as-permission" key={index}>
            <div className="as-permission-ops">
              {OPERATIONS.map((operation) => (
                <Toggle
                  key={operation}
                  label={operation}
                  checked={permission.operations.includes(operation)}
                  onChange={(on) =>
                    setPermission(index, {
                      operations: on
                        ? [...permission.operations, operation]
                        : permission.operations.filter((op) => op !== operation),
                    })
                  }
                />
              ))}
            </div>
            <Field label="Paths" help="Type a path, then press Enter to add it.">
              <TagsInput
                value={permission.paths}
                onChange={(paths) => setPermission(index, { paths })}
                placeholder="/workspace/**"
              />
            </Field>
            <div className="as-permission-foot">
              <Select
                value={permission.mode}
                options={MODES.map((mode) => ({ value: mode, label: mode }))}
                onChange={(mode) => setPermission(index, { mode })}
              />
              <button
                type="button"
                className="as-btn as-btn-icon as-btn-ghost"
                title="Remove rule"
                onClick={() => patch({ permissions: spec.permissions.filter((_, i) => i !== index) })}
              >
                <Trash2 size={13} />
              </button>
            </div>
          </div>
        ))}
      </Section>

      <Section title="Interrupt on" subtitle="Pause the run for a human decision before these tools run.">
        {!knownTools.length ? (
          <p className="as-muted">Bind a tool first — there is nothing to interrupt on yet.</p>
        ) : null}
        <div className="as-check-list">
          {knownTools.map((tool) => (
            <label className="as-check-row" key={tool}>
              <input
                type="checkbox"
                checked={Boolean(spec.interruptOn?.[tool])}
                onChange={(event) => patch({ interruptOn: { ...spec.interruptOn, [tool]: event.target.checked } })}
              />
              <span className="as-check-name">{tool}</span>
            </label>
          ))}
        </div>
      </Section>

      <Collapsible title="How this compiles" subtitle="deepagents.create_deep_agent">
        <p className="as-muted">
          The deep agent is built by <code>deepagents.create_deep_agent</code>; every subagent becomes a
          <code> SubAgent</code> with its own prompt, tools and model, memory files are mounted into the virtual
          filesystem, and permissions become the runtime&apos;s filesystem policy.
        </p>
      </Collapsible>
    </>
  );
}
