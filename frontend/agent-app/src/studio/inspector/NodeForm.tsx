/** Building-block nodes: router, tool, join, map, human, subgraph, set_state.
 *
 * Fields come from the node kind's registry entry; only the *option sources*
 * (which nodes a destination may point at, which tools exist) are supplied here,
 * because they depend on the current canvas.
 */
import { AlertTriangle } from "lucide-react";
import { functionToolOptions, nodeFieldKey, nodeKindById } from "../model/catalog";
import type { AgentDef } from "../../types";
import type { CatalogField, NodeData, RouterRoute, StudioCatalog, StudioNode } from "../model/types";
import { Field, FieldRenderer, Section, TextInput, type SelectOption } from "./Fields";

export interface NodeFormProps {
  nodeId: string;
  data: NodeData;
  catalog: StudioCatalog | null;
  nodes: StudioNode[];
  agents: AgentDef[];
  onChange: (patch: Partial<NodeData>) => void;
  onRoutesChange: (routes: RouterRoute[], defaultRoute: string) => void;
}

export function NodeForm({ nodeId, data, catalog, nodes, agents, onChange, onRoutesChange }: NodeFormProps) {
  const kind = nodeKindById(catalog, data.paletteType);
  const nodeTargets: SelectOption[] = nodes
    .filter((n) => n.id !== nodeId)
    .map((n) => ({
      value: n.id,
      label: `${String(n.data.name || n.data.label || n.data.paletteType)} · ${n.data.paletteType}`,
    }));

  function optionsFor(field: CatalogField): SelectOption[] | undefined {
    if (field.type !== "select") return undefined;
    if (data.paletteType === "tool") return functionToolOptions(catalog);
    if (data.paletteType === "subgraph") {
      return agents.map((agent) => ({ value: agent.slug, label: `${agent.name} (${agent.kind})` }));
    }
    if (data.paletteType === "map") return nodeTargets;
    return undefined;
  }

  const brokenRef =
    data.paletteType === "subgraph" && data.ref && !agents.some((a) => a.slug === data.ref) ? String(data.ref) : null;
  const brokenTarget =
    data.paletteType === "map" && data.to && !nodes.some((n) => n.id === data.to) ? String(data.to) : null;

  return (
    <Section title={kind?.label ?? data.paletteType} subtitle={kind?.summary}>
      <p className="as-provenance">
        Compiles to <code>{kind?.builder ?? "langgraph.graph.StateGraph"}</code>
      </p>
      <Field label="Label" help="How this step is named in the run timeline.">
        <TextInput value={data.label ?? ""} testId="inspector-label" onChange={(label) => onChange({ label })} />
      </Field>

      {brokenRef ? (
        <p className="as-warning">
          <AlertTriangle size={12} /> No saved agent or flow has the slug “{brokenRef}”.
        </p>
      ) : null}
      {brokenTarget ? (
        <p className="as-warning">
          <AlertTriangle size={12} /> The worker node “{brokenTarget}” is no longer on the canvas.
        </p>
      ) : null}

      {(kind?.fields ?? []).map((field) => (
        <FieldRenderer
          key={field.name}
          field={field}
          value={(data as Record<string, unknown>)[nodeFieldKey(data.paletteType, field.name)]}
          options={optionsFor(field)}
          targets={nodeTargets}
          routesDefault={data.defaultRoute}
          onDefaultChange={(defaultRoute) => onRoutesChange(data.routes ?? [], defaultRoute)}
          onChange={(value) => {
            if (field.type === "routes") {
              onRoutesChange((value as RouterRoute[]) ?? [], data.defaultRoute ?? "");
              return;
            }
            onChange({ [nodeFieldKey(data.paletteType, field.name)]: value } as Partial<NodeData>);
          }}
        />
      ))}
    </Section>
  );
}
