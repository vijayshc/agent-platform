import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./studio.css";
import { StudioApp } from "./StudioApp";

/** `/agent-studio/editor` — the Studio owns the ReactFlow provider so both the
 *  canvas and the shell toolbar can read the viewport. */
export function StudioPage() {
  return (
    <ReactFlowProvider>
      <StudioApp />
    </ReactFlowProvider>
  );
}
