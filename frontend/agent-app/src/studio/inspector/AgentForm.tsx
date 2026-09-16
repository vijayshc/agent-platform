/** Agent runtime form: identity, instructions, model and the four cap groups. */
import { CodeEditor } from "../../shared/CodeEditor";
import { runtimeEntries } from "../model/catalog";
import type { NodeData, StudioCatalog } from "../model/types";
import { DeepAgentForm } from "./DeepAgentForm";
import { Field, Section, TextInput } from "./Fields";
import { MiddlewareForm } from "./MiddlewareForm";
import { OutputForm } from "./OutputForm";
import { ToolsForm } from "./ToolsForm";

export interface AgentFormProps {
  data: NodeData;
  catalog: StudioCatalog | null;
  /** Admin module keys the signed-in user may open. */
  modules: string[];
  onChange: (patch: Partial<NodeData>) => void;
}

export function AgentForm({ data, catalog, modules, onChange }: AgentFormProps) {
  const runtimes = runtimeEntries(catalog);
  const active = runtimes.find((r) => r.id === (data.runtime || data.paletteType)) ?? runtimes[0] ?? null;
  const features = active?.features ?? {};

  // Models live in LLM Manager and are chosen per run; a definition that still
  // pins one (or default options) says so, read-only.
  const pinnedConnection = data.modelClient && data.modelClient !== "default" ? data.modelClient : "";
  const pinnedOptions =
    data.temperature != null || data.maxTokens != null
      ? `temperature ${data.temperature ?? "—"} / max tokens ${data.maxTokens ?? "—"}`
      : "";
  const pinnedWindow = data.maxContextWindowTokens ? `${data.maxContextWindowTokens} token context window` : "";
  const pinnedModel = pinnedConnection || pinnedOptions || pinnedWindow
    ? `This agent pins ${[
        pinnedConnection ? `the ${pinnedConnection} connection` : "",
        pinnedOptions,
        pinnedWindow,
      ]
        .filter(Boolean)
        .join(", ")}. Model settings are managed in LLM Manager and chosen per run.`
    : "";

  return (
    <>
      <Section title="Identity" subtitle={active ? `Compiles to ${active.builder}` : undefined}>
        <Field label="Name" htmlFor="inspector-name">
          <TextInput
            value={data.name ?? ""}
            testId="inspector-name"
            placeholder="Researcher"
            onChange={(name) => onChange({ name, label: name })}
          />
        </Field>
        <Field label="Description" help="Shown to the supervisor or the planner when this agent joins a team.">
          <TextInput
            value={data.description ?? ""}
            testId="inspector-description"
            placeholder="Finds primary sources and returns citations"
            onChange={(description) => onChange({ description })}
          />
        </Field>
        <Field label="Instructions" help="The system prompt. Be specific about the output you expect.">
          <CodeEditor
            value={data.instructions ?? ""}
            onChange={(instructions) => onChange({ instructions })}
            language="markdown"
            filename="instructions.md"
            label="Agent instructions"
            height={220}
            minHeight={140}
            testId="inspector-instructions"
          />
        </Field>
      </Section>

      {runtimes.length > 1 ? (
        <Section title="Runtime" subtitle="What kind of agent this is. Switching keeps every setting.">
          <div className="as-runtime-cards">
            {runtimes.map((runtime) => {
              const selected = (data.runtime || data.paletteType) === runtime.id;
              return (
                <button
                  type="button"
                  key={runtime.id}
                  className={`as-runtime-card${selected ? " is-active" : ""}`}
                  onClick={() => onChange({ runtime: runtime.id, paletteType: runtime.id })}
                  data-testid={`runtime-${runtime.id}`}
                  title={runtime.when ?? runtime.summary}
                >
                  <span className="as-runtime-title">{runtime.label}</span>
                  <span className="as-runtime-summary">{runtime.summary}</span>
                  <code>{runtime.builder}</code>
                </button>
              );
            })}
          </div>
          <ul className="as-feature-list">
            {Object.entries(features)
              .filter(([, on]) => on)
              .map(([feature]) => (
                <li key={feature}>{feature.replace(/_/g, " ")}</li>
              ))}
          </ul>
        </Section>
      ) : null}

      {pinnedModel ? (
        <p className="as-note" data-testid="inspector-model-pin">
          {pinnedModel}
        </p>
      ) : null}

      {features.middleware === false ? null : <MiddlewareForm data={data} catalog={catalog} onChange={onChange} />}

      {features.structured_output === false ? null : <OutputForm data={data} onChange={onChange} />}

      {features.tools === false && features.skills === false ? (
        <Section title="Tools">
          <p className="as-muted">{active?.label} does not take tools, skills or function calls.</p>
        </Section>
      ) : (
        <ToolsForm data={data} catalog={catalog} modules={modules} onChange={onChange} />
      )}

      {data.runtime === "deep_agent" || data.paletteType === "deep_agent" ? (
        <DeepAgentForm data={data} catalog={catalog} onChange={onChange} />
      ) : null}
    </>
  );
}
