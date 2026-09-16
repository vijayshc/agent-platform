/** Supervisor / swarm / custom-graph marker settings.
 *
 * Participants are the agent nodes wired to the marker; the panel names them so
 * the author can see the team without reading the canvas.
 */
import { patternById } from "../model/catalog";
import type { NodeData, StudioCatalog, StudioNode } from "../model/types";
import { Field, Section, Select, TextInput, Toggle } from "./Fields";

export interface WorkflowFormProps {
  data: NodeData;
  catalog: StudioCatalog | null;
  participants: StudioNode[];
  onChange: (patch: Partial<NodeData>) => void;
}

export function WorkflowForm({ data, catalog, participants, onChange }: WorkflowFormProps) {
  const pattern = patternById(catalog, data.paletteType);
  const names = participants.map((n) => String(n.data.name || n.data.label || "Agent"));
  const min = pattern?.min_agents ?? 1;
  const short = participants.length < min;

  return (
    <>
      <Section
        title={pattern?.label ?? data.paletteType}
        subtitle={pattern?.summary}
      >
        <p className="as-provenance">
          Compiles to <code>{pattern?.builder ?? "langgraph.graph.StateGraph"}</code>
        </p>
        {data.paletteType === "supervisor" ? (
          <>
            <Field label="Manager name" help="The agent other agents see in the transcript.">
              <TextInput
                value={data.managerName ?? ""}
                testId="inspector-manager-name"
                placeholder="Supervisor"
                onChange={(managerName) => onChange({ managerName })}
              />
            </Field>
            <Field label="Manager instructions" help="How the manager should route and answer.">
              <textarea
                className="as-textarea"
                rows={4}
                data-testid="inspector-manager-instructions"
                value={data.managerInstructions ?? ""}
                onChange={(event) => onChange({ managerInstructions: event.target.value })}
              />
            </Field>
            <div className="as-grid-2">
              <Field label="Output mode" help="What the supervisor returns to the caller.">
                <Select
                  value={data.outputMode ?? "last_message"}
                  options={[
                    { value: "last_message", label: "Last message" },
                    { value: "full_history", label: "Full history" },
                  ]}
                  onChange={(outputMode) => onChange({ outputMode })}
                />
              </Field>
              <Field label="Parallel tool calls" help="Let the manager hand off to several specialists at once.">
                <Toggle
                  label={data.parallelToolCalls ? "Enabled" : "Disabled"}
                  checked={Boolean(data.parallelToolCalls)}
                  onChange={(parallelToolCalls) => onChange({ parallelToolCalls })}
                />
              </Field>
            </div>
          </>
        ) : null}

        {data.paletteType === "swarm" ? (
          <Field label="Start agent" help="Who receives the first message. Agent → agent edges are the allowed handoffs.">
            <Select
              value={data.startAgent ?? ""}
              placeholder="First participant"
              options={names.map((name) => ({ value: name, label: name }))}
              onChange={(startAgent) => onChange({ startAgent })}
              testId="inspector-start-agent"
            />
          </Field>
        ) : null}

        {data.paletteType === "graph" ? (
          <p className="as-muted">
            This marker keeps the flow an explicit graph. Add building blocks and agents, then wire them — every
            edge becomes a <code>StateGraph</code> edge.
          </p>
        ) : null}
      </Section>

      <Section title="Participants" subtitle={`${participants.length} connected · ${min} required`}>
        {participants.length ? (
          <ul className="as-participants" data-testid="inspector-participants">
            {names.map((name, index) => (
              <li key={`${name}-${index}`}>
                <span className="as-participant-name">{name}</span>
                <span className="as-participant-runtime">{String(participants[index].data.runtime ?? "agent")}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="as-empty" data-testid="inspector-participants-empty">
            Connect {min} agent{min === 1 ? "" : "s"} to this {pattern?.label ?? "pattern"} node.
          </p>
        )}
        {short && participants.length ? (
          <p className="as-warning">
            {pattern?.label} needs at least {min} agents to compile.
          </p>
        ) : null}
      </Section>
    </>
  );
}
