/** Structured output: the LangChain structured-output strategy + JSON schema.
 *
 * Strategies mirror `response_format_for()` in
 * `src/agent_platform/runtime/agent_compile.py` (auto | tool | provider); the
 * catalog does not describe them because they are arguments of
 * `create_agent(response_format=…)`, not registry capabilities.
 */
import { useEffect, useState } from "react";
import { CodeEditor } from "../../shared/CodeEditor";
import type { NodeData, ResponseFormat } from "../model/types";
import { Field, Section, Select } from "./Fields";

/** Ordered best-first: `auto`/`tool` force a tool call, which reasoning models
 *  reject with a 400 ("Thinking mode does not support this tool_choice"), so the
 *  provider-native strategy is the default. */
const STRATEGIES = [
  {
    value: "provider",
    label: "Provider — native schema (recommended)",
    help: "ProviderStrategy(schema): the model returns the schema natively. Works with reasoning models.",
  },
  {
    value: "auto",
    label: "Auto — may force a tool call",
    help: "AutoStrategy(schema): falls back to a function call, which reasoning models reject with a 400.",
  },
  {
    value: "tool",
    label: "Tool — always forces a tool call",
    help: "ToolStrategy(schema): always a function call; reasoning models reject this with a 400.",
  },
];

const DEFAULT_STRATEGY = "provider";

/** A schema without a non-empty `title` is rejected by validation and by the
 *  provider, so every new schema starts with one. */
const EXAMPLE_SCHEMA = {
  title: "Result",
  type: "object",
  properties: {
    answer: { type: "string" },
    confidence: { type: "number" },
  },
  required: ["answer"],
};

const EMPTY_SCHEMA = { title: "Result", type: "object", properties: {}, required: [] };

export function OutputForm({
  data,
  onChange,
}: {
  data: NodeData;
  onChange: (patch: Partial<NodeData>) => void;
}) {
  const format: ResponseFormat | null = data.responseFormat ?? null;
  const enabled = Boolean(format);
  const [draft, setDraft] = useState(() => JSON.stringify(format?.schema ?? EXAMPLE_SCHEMA, null, 2));
  const [invalid, setInvalid] = useState<string | null>(null);

  useEffect(() => {
    if (format) setDraft(JSON.stringify(format.schema ?? {}, null, 2));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  function applySchema(text: string) {
    setDraft(text);
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      setInvalid(null);
      onChange({ responseFormat: { strategy: format?.strategy ?? DEFAULT_STRATEGY, schema: parsed } });
    } catch (error) {
      setInvalid(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <Section
      title="Structured output"
      subtitle="Return a typed object instead of free text."
      actions={
        <button
          type="button"
          className="as-btn as-btn-sm"
          data-testid="output-toggle"
          onClick={() =>
            onChange({
              responseFormat: enabled ? null : { strategy: DEFAULT_STRATEGY, schema: EMPTY_SCHEMA },
            })
          }
        >
          {enabled ? "Remove" : "Add"}
        </button>
      }
    >
      {!enabled ? (
        <p className="as-empty" data-testid="output-empty">
          Free text. Add a schema to make every run return a validated object.
        </p>
      ) : (
        <>
          <Field
            label="Strategy"
            help={
              STRATEGIES.find((s) => s.value === (format?.strategy ?? DEFAULT_STRATEGY))?.help ??
              STRATEGIES[0].help
            }
          >
            <Select
              value={format?.strategy ?? DEFAULT_STRATEGY}
              options={STRATEGIES.map((s) => ({ value: s.value, label: s.label }))}
              onChange={(strategy) => onChange({ responseFormat: { strategy, schema: format?.schema ?? {} } })}
              testId="output-strategy"
            />
          </Field>
          <Field label="JSON schema" help="The shape every answer must satisfy. A non-empty title is required.">
            <CodeEditor
              value={draft}
              language="json"
              filename="response_format.json"
              height={220}
              testId="output-schema"
              onChange={applySchema}
            />
          </Field>
          {invalid ? <p className="as-error">Schema is not valid JSON: {invalid}</p> : null}
          {!invalid && !(format?.schema as { properties?: unknown })?.properties ? (
            <p className="as-warning">Add at least one property so the model has something to fill.</p>
          ) : null}
        </>
      )}
    </Section>
  );
}
