import type { FunctionPlotData } from "@/features/canvas/types";

export interface QuadraticVisual {
  readonly a: number;
  readonly b: number;
  readonly c: number;
  readonly expression: string;
  readonly vertex: readonly [number, number];
  readonly roots: readonly number[];
  readonly plot: FunctionPlotData;
}

export function formatNumber(value: number): string {
  return Number(value.toPrecision(6)).toString();
}

/** The model selects coefficients; application code owns every plotted point. */
export function quadraticVisual(a: unknown, b: unknown, c: unknown): QuadraticVisual {
  const coefficients = [a, b, c];
  if (!coefficients.every((value) => typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 100)) {
    throw new Error("Use numeric coefficients between -100 and 100.");
  }
  const [leading, linear, constant] = coefficients as number[];
  if (Math.abs(leading) < 0.1) throw new Error("The quadratic coefficient must have magnitude at least 0.1.");
  const terms = [[leading, "x²"], [linear, "x"], [constant, ""]] as const;
  let expression = "";
  for (const [coefficient, variable] of terms) {
    if (coefficient === 0) continue;
    const sign = coefficient < 0 ? "−" : "+";
    const magnitude = variable && Math.abs(coefficient) === 1 ? "" : formatNumber(Math.abs(coefficient));
    expression += `${expression ? ` ${sign} ` : coefficient < 0 ? "−" : ""}${magnitude}${variable}`;
  }
  const vertexX = -linear / (2 * leading);
  const vertexY = leading * vertexX ** 2 + linear * vertexX + constant;
  const discriminant = linear ** 2 - 4 * leading * constant;
  let roots: number[] = [];
  if (discriminant === 0) roots = [vertexX];
  if (discriminant > 0) {
    const q = -0.5 * (linear + Math.sign(linear || 1) * Math.sqrt(discriminant));
    roots = [q / leading, constant / q].sort((left, right) => left - right);
  }
  const radius = Math.max(2, Math.sqrt(Math.max(4, Math.abs(vertexY)) / Math.abs(leading)) * 1.5);
  const minimumX = Math.min(vertexX - radius, -0.5);
  const maximumX = Math.max(vertexX + radius, 0.5);
  const points = Array.from({ length: 241 }, (_, index) => {
    const x = minimumX + ((maximumX - minimumX) * index) / 240;
    return [x, leading * x ** 2 + linear * x + constant];
  });
  const values = [0, vertexY, ...points.map((point) => point[1])];
  const minimumY = Math.min(...values);
  const maximumY = Math.max(...values);
  const padding = Math.max(1, (maximumY - minimumY) * 0.12);
  return {
    a: leading, b: linear, c: constant, expression,
    vertex: [vertexX, vertexY], roots,
    plot: {
      type: "function_plot", function: expression, points,
      x_range: [minimumX, maximumX], y_range: [minimumY - padding, maximumY + padding],
      color: "hsl(var(--lavender))", animate: false, show_axes: true,
    },
  };
}

export function quadraticSummary(visual: QuadraticVisual): string {
  const roots = visual.roots.length ? `Real roots: ${visual.roots.map(formatNumber).join(", ")}.` : "No real roots: the curve does not cross y = 0.";
  return `${visual.expression} = 0. ${roots} Vertex: (${visual.vertex.map(formatNumber).join(", ")}).`;
}
