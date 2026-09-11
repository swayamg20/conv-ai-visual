import { expect, type Page } from "@playwright/test";

import {
  createSceneState,
  type SceneNode,
  type SceneState,
} from "../src/lib/live-scene";

const STAGE = "projectile-choreography-stage";
const LINE_TOLERANCE = 0.05;
type Point = readonly [number, number];
type ProjectileNode = Extract<
  SceneNode,
  { readonly kind: "line" | "path" | "latex_token" }
>;

type MismatchCode =
  | "unsupported_node"
  | "paint_order"
  | "unexpected_element"
  | "identity"
  | "transient_node"
  | "presentation_residue"
  | "opacity"
  | "line_structure"
  | "line_style"
  | "line_geometry"
  | "path_structure"
  | "path_geometry"
  | "path_style"
  | "latex_structure"
  | "latex_geometry"
  | "latex_content"
  | "latex_style";
type Mismatch = Readonly<{
  code: MismatchCode;
  nodeId: string | null;
  field: string;
  expected: string;
  actual: string;
}>;
// Browser-observed semantic fields only; RoughJS/KaTeX implementation markup is omitted.
type NodeSignature = Readonly<{ id: string; opacity: number | null }> &
  (
    | Readonly<{
        kind: "line";
        points: readonly [Point, Point];
        stroke: string;
        strokeWidth: number | null;
      }>
    | Readonly<{
        kind: "path";
        points: readonly Point[];
        closed: boolean;
        fill: string;
        stroke: string;
        strokeWidth: number | null;
        strokeLinecap: string;
        strokeLinejoin: string;
      }>
    | Readonly<{
        kind: "latex_token";
        x: number | null;
        y: number | null;
        width: number | null;
        height: number | null;
        anchor: "start" | "middle" | "end" | null;
        latex: string;
        color: string;
        fontSize: number | null;
      }>
  );
export type ProjectileSvgSemanticSignature = Readonly<{
  sourceRevision: number | null;
  viewBox: string;
  paintOrder: readonly string[];
  nodes: readonly NodeSignature[];
  residueFree: boolean;
}>;
type Inspection = Readonly<{
  signature: ProjectileSvgSemanticSignature;
  mismatches: readonly Mismatch[];
}>;
type SceneExpectation = SceneState | readonly SceneNode[];

const isNodeArray = (value: SceneExpectation): value is readonly SceneNode[] =>
  Array.isArray(value);

/** Independent copy of the reconciler's geometry-before-label contract. */
export function expectedProjectilePaintOrder(
  nodes: readonly SceneNode[],
): readonly string[] {
  const label = ({ kind }: SceneNode) =>
    kind === "text" || kind === "latex" || kind === "latex_token";
  return Object.freeze([
    ...nodes.filter((node) => !label(node)).map(({ id }) => id),
    ...nodes.filter(label).map(({ id }) => id),
  ]);
}

/** Independently project RoughJS/KaTeX DOM into stable visual semantics. */
export async function inspectProjectileSvgAgainstScene(
  page: Page,
  expectation: SceneExpectation,
): Promise<Inspection> {
  const scene = isNodeArray(expectation)
    ? createSceneState({ revision: 0, nodes: [...expectation] })
    : createSceneState(expectation);
  const unsupported = scene.nodes.filter(
    ({ kind }) => kind !== "line" && kind !== "path" && kind !== "latex_token",
  );
  if (unsupported.length)
    throw new TypeError(
      `Projectile SVG oracle does not support: ${unsupported.map(({ id, kind }) => `${id}:${kind}`).join(", ")}`,
    );

  const nodes = scene.nodes as readonly ProjectileNode[];
  return page
    .getByTestId(STAGE)
    .locator("svg")
    .first()
    .evaluate(
      (root, input): Inspection => {
        const NS = "http://www.w3.org/2000/svg";
        const mismatches: Mismatch[] = [];
        const signatures: NodeSignature[] = [];
        const same = (left: unknown, right: unknown) =>
          JSON.stringify(left) === JSON.stringify(right);
        const check = (
          code: MismatchCode,
          nodeId: string | null,
          passes: boolean,
        ) => {
          if (!passes)
            mismatches.push({
              code,
              nodeId,
              field: code,
              expected: "match",
              actual: "mismatch",
            });
        };
        const numeric = (value: string | null) => {
          if (!value?.trim()) return null;
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : null;
        };
        const pixels = (value: string) => {
          const match = value.match(/^([-+]?(?:\d+\.?\d*|\.\d+))px$/);
          return match ? Number(match[1]) : null;
        };
        const alpha = (element: Element) => {
          const value = Number.parseFloat(getComputedStyle(element).opacity);
          return Number.isFinite(value) ? value : null;
        };
        const round = (value: number) => Number(value.toFixed(6));
        const distance = (left: Point, right: Point) =>
          Math.hypot(left[0] - right[0], left[1] - right[1]);
        const oneSvgChild = (
          group: Element,
          tag: string,
          code: MismatchCode,
          id: string,
        ) => {
          const children = Array.from(group.children);
          const valid =
            children.length === 1 &&
            children[0].namespaceURI === NS &&
            children[0].localName === tag;
          check(code, id, valid);
          return valid ? children[0] : null;
        };

        const order = Array.from(
          root.children,
          (child) =>
            child.getAttribute("data-element-id") ?? `<${child.localName}>`,
        );
        check("paint_order", null, same(input.paintOrder, order));
        const ids = new Set(input.nodes.map(({ id }) => id));
        for (const child of root.children) {
          const id = child.getAttribute("data-element-id");
          check(
            "unexpected_element",
            id,
            child.namespaceURI === NS &&
              child.localName === "g" &&
              !!id &&
              ids.has(id),
          );
        }
        root
          .querySelectorAll(
            "[id$='--incoming'],[id$='--outgoing'],[data-element-id$='--incoming'],[data-element-id$='--outgoing']",
          )
          .forEach((element) =>
            check(
              "transient_node",
              element.getAttribute("data-element-id"),
              false,
            ),
          );
        const residue = [
          "transform",
          "transform-origin",
          "filter",
          "clip-path",
          "stroke-dasharray",
          "stroke-dashoffset",
        ];
        for (const element of [root, ...root.querySelectorAll("*")]) {
          if (element.namespaceURI !== NS) continue;
          const id =
            element
              .closest("[data-element-id]")
              ?.getAttribute("data-element-id") ?? null;
          for (const property of residue) {
            if (element.hasAttribute(property))
              check("presentation_residue", id, false);
            const value = (element as SVGElement).style.getPropertyValue(
              property,
            );
            if (value) check("presentation_residue", id, false);
          }
        }

        const parsePath = (d: string | null) => {
          if (!d) return null;
          const values: (string | number)[] = [];
          const token = /([MLZ])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/g;
          let end = 0;
          for (const match of d.matchAll(token)) {
            if (!/^[\s,]*$/.test(d.slice(end, match.index))) return null;
            values.push(match[1] ?? Number(match[2]));
            end = (match.index ?? 0) + match[0].length;
          }
          if (!/^[\s,]*$/.test(d.slice(end)) || values[0] !== "M") return null;
          const points: [number, number][] = [];
          let index = 0;
          while (index < values.length && values[index] !== "Z") {
            if (values[index] !== (index ? "L" : "M")) return null;
            const x = values[index + 1];
            const y = values[index + 2];
            if (typeof x !== "number" || typeof y !== "number") return null;
            points.push([x, y]);
            index += 3;
          }
          const closed = values[index] === "Z";
          return (closed ? index + 1 : index) === values.length &&
            points.length >= 2
            ? { points, closed }
            : null;
        };

        for (const node of input.nodes) {
          const found = Array.from(root.children).filter(
            (child) => child.getAttribute("data-element-id") === node.id,
          );
          check("identity", node.id, found.length === 1);
          if (found.length !== 1) continue;
          const group = found[0];
          check("identity", node.id, group.getAttribute("id") === node.id);
          const opacity = alpha(group);
          check(
            "opacity",
            node.id,
            opacity !== null && Math.abs(opacity - node.style.opacity) <= 1e-6,
          );

          if (node.kind === "path") {
            const path = oneSvgChild(group, "path", "path_structure", node.id);
            if (!path) continue;
            const parsed = parsePath(path.getAttribute("d"));
            const geometry = parsed ?? { points: [], closed: false };
            check(
              "path_geometry",
              node.id,
              same({ points: node.points, closed: node.closed }, geometry),
            );
            const paint = {
              fill: path.getAttribute("fill") ?? "",
              stroke: path.getAttribute("stroke") ?? "",
              strokeWidth: numeric(path.getAttribute("stroke-width")),
              strokeLinecap: path.getAttribute("stroke-linecap") ?? "",
              strokeLinejoin: path.getAttribute("stroke-linejoin") ?? "",
            };
            check(
              "path_style",
              node.id,
              same(
                {
                  fill: node.style.fill,
                  stroke: node.style.stroke,
                  strokeWidth: node.style.strokeWidth,
                  strokeLinecap: "round",
                  strokeLinejoin: "round",
                },
                paint,
              ),
            );
            signatures.push({
              kind: "path",
              id: node.id,
              ...geometry,
              ...paint,
              opacity,
            });
            continue;
          }

          if (node.kind === "line") {
            check("unsupported_node", node.id, node.style.roughness === 0);
            const descendants = Array.from(group.querySelectorAll("*"));
            const invalid = descendants.filter(
              (element) =>
                element.namespaceURI !== NS ||
                !["g", "path", "line", "polyline"].includes(element.localName),
            );
            const shapes = Array.from(
              group.querySelectorAll<SVGGeometryElement>("path,line,polyline"),
            );
            check(
              "line_structure",
              node.id,
              !invalid.length && !!shapes.length,
            );
            const paints = shapes.map((shape) => ({
              stroke: shape.getAttribute("stroke") ?? "",
              fill: shape.getAttribute("fill") ?? "",
              strokeWidth: numeric(shape.getAttribute("stroke-width")),
            }));
            check(
              "line_style",
              node.id,
              same(
                shapes.map(() => ({
                  stroke: node.style.stroke,
                  fill: "none",
                  strokeWidth: node.style.strokeWidth,
                })),
                paints,
              ),
            );

            const [start, finish] = node.points;
            const dx = finish[0] - start[0];
            const dy = finish[1] - start[1];
            const expectedLength = Math.hypot(dx, dy);
            const squared = expectedLength ** 2;
            check("line_geometry", node.id, !!expectedLength);
            let total = 0;
            let observed: readonly [Point, Point] | null = null;
            shapes.forEach((shape) => {
              let length = Number.NaN;
              try {
                length = shape.getTotalLength();
              } catch {
                // Reported by the next check.
              }
              const validLength = length > 0 && Number.isFinite(length);
              check("line_geometry", node.id, validLength);
              if (!validLength) return;
              total += length;
              let low = Infinity;
              let high = -Infinity;
              let error = 0;
              for (let sample = 0; sample <= 32; sample += 1) {
                const point = shape.getPointAtLength((length * sample) / 32);
                const x = point.x - start[0];
                const y = point.y - start[1];
                const projection = squared ? (x * dx + y * dy) / squared : NaN;
                low = Math.min(low, projection);
                high = Math.max(high, projection);
                error = Math.max(
                  error,
                  Math.abs(x * dy - y * dx) / expectedLength,
                );
              }
              check(
                "line_geometry",
                node.id,
                error <= input.lineTolerance &&
                  low >= -0.001 &&
                  low <= 0.001 &&
                  high >= 0.999 &&
                  high <= 1.001,
              );
              const first = shape.getPointAtLength(0);
              const last = shape.getPointAtLength(length);
              const endpoints = [
                [first.x, first.y],
                [last.x, last.y],
              ] as const;
              const forward = Math.max(
                distance(endpoints[0], start),
                distance(endpoints[1], finish),
              );
              const reverse = Math.max(
                distance(endpoints[0], finish),
                distance(endpoints[1], start),
              );
              check(
                "line_geometry",
                node.id,
                Math.min(forward, reverse) <= input.lineTolerance,
              );
              if (!observed)
                observed =
                  reverse < forward ? [endpoints[1], endpoints[0]] : endpoints;
            });
            const ratio = expectedLength ? total / expectedLength : null;
            check(
              "line_geometry",
              node.id,
              ratio !== null &&
                Math.min(Math.abs(ratio - 1), Math.abs(ratio - 2)) <= 0.02,
            );
            const points: [Point, Point] = observed
              ? [
                  [round(observed[0][0]), round(observed[0][1])],
                  [round(observed[1][0]), round(observed[1][1])],
                ]
              : [
                  [Number.NaN, Number.NaN],
                  [Number.NaN, Number.NaN],
                ];
            signatures.push({
              kind: "line",
              id: node.id,
              points,
              stroke: paints[0]?.stroke ?? "",
              strokeWidth: paints[0]?.strokeWidth ?? null,
              opacity,
            });
            continue;
          }

          const foreign = oneSvgChild(
            group,
            "foreignObject",
            "latex_structure",
            node.id,
          );
          if (!foreign) continue;
          const container =
            foreign.children.length === 1 &&
            foreign.children[0] instanceof HTMLDivElement
              ? foreign.children[0]
              : null;
          check("latex_structure", node.id, !!container);
          if (!container) continue;
          check(
            "latex_structure",
            node.id,
            container.children.length === 1 &&
              !!container.firstElementChild?.classList.contains("katex"),
          );
          const box = {
            left: numeric(foreign.getAttribute("x")),
            y: numeric(foreign.getAttribute("y")),
            width: numeric(foreign.getAttribute("width")),
            height: numeric(foreign.getAttribute("height")),
          };
          const left =
            node.x -
            (node.anchor === "middle"
              ? node.width / 2
              : node.anchor === "end"
                ? node.width
                : 0);
          check(
            "latex_geometry",
            node.id,
            same(
              { left, y: node.y, width: node.width, height: node.height },
              box,
            ),
          );
          const annotations = container.querySelectorAll(
            'annotation[encoding="application/x-tex"]',
          );
          const latex =
            annotations.length === 1 ? (annotations[0].textContent ?? "") : "";
          check(
            "latex_content",
            node.id,
            annotations.length === 1 && latex === node.latex,
          );
          const anchors = {
            "flex-start": "start",
            center: "middle",
            "flex-end": "end",
          } as const;
          const anchor =
            anchors[container.style.justifyContent as keyof typeof anchors] ??
            null;
          const probe = document.createElement("div");
          probe.style.color = node.style.color;
          const paint = {
            color: container.style.color,
            fontSize: pixels(container.style.fontSize),
            width: container.style.width,
            height: container.style.height,
            display: container.style.display,
            alignItems: container.style.alignItems,
            anchor,
            whiteSpace: container.style.whiteSpace,
          };
          check(
            "latex_style",
            node.id,
            same(
              {
                color: probe.style.color,
                fontSize: node.style.fontSize,
                width: "100%",
                height: "100%",
                display: "flex",
                alignItems: "center",
                anchor: node.anchor,
                whiteSpace: "nowrap",
              },
              paint,
            ),
          );
          const x =
            box.left === null || box.width === null || anchor === null
              ? null
              : box.left +
                (anchor === "middle"
                  ? box.width / 2
                  : anchor === "end"
                    ? box.width
                    : 0);
          signatures.push({
            kind: "latex_token",
            id: node.id,
            x,
            y: box.y,
            width: box.width,
            height: box.height,
            anchor,
            latex,
            color: paint.color,
            fontSize: paint.fontSize,
            opacity,
          });
        }

        return {
          signature: {
            sourceRevision: input.sourceRevision,
            viewBox: root.getAttribute("viewBox") ?? "",
            paintOrder: order,
            nodes: signatures,
            residueFree: !mismatches.some(
              ({ code }) =>
                code === "transient_node" || code === "presentation_residue",
            ),
          },
          mismatches,
        };
      },
      {
        sourceRevision: isNodeArray(expectation) ? null : scene.revision,
        nodes,
        paintOrder: expectedProjectilePaintOrder(nodes),
        lineTolerance: LINE_TOLERANCE,
      },
    );
}

/** Assert settled render semantics and return a Replay-stable signature. */
export async function expectProjectileSvgMatchesScene(
  page: Page,
  expectation: SceneExpectation,
): Promise<ProjectileSvgSemanticSignature> {
  const inspection = await inspectProjectileSvgAgainstScene(page, expectation);
  expect(
    inspection.mismatches,
    "projectile SVG must be the transient-free materialization of its fixture scene",
  ).toEqual([]);
  return inspection.signature;
}
