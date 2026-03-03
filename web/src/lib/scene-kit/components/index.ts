import type { ComponentName, ComponentRenderer } from "../types";
import { rightTriangle } from "./right-triangle";
import { equation } from "./equation";
import { coordinatePlane } from "./coordinate-plane";
import { functionPlot } from "./function-plot";
import { numberLine } from "./number-line";
import { barChart } from "./bar-chart";
import { flowchart } from "./flowchart";
import { tree } from "./tree";
import { vennDiagram } from "./venn-diagram";
import { circleDiagram } from "./circle-diagram";
import { label } from "./label";

const COMPONENTS: Record<ComponentName, ComponentRenderer> = {
  right_triangle: rightTriangle,
  equation,
  coordinate_plane: coordinatePlane,
  function_plot: functionPlot,
  number_line: numberLine,
  bar_chart: barChart,
  flowchart,
  tree,
  venn_diagram: vennDiagram,
  circle_diagram: circleDiagram,
  label,
};

export function getComponent(name: string): ComponentRenderer | null {
  return COMPONENTS[name as ComponentName] ?? null;
}
