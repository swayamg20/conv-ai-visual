import { expect, type Page } from "@playwright/test";

import type { SceneNode, SceneState } from "../src/lib/live-scene";

const LINE_TOLERANCE = 0.05;
const PATH_RELATIVE_TOLERANCE = Number.EPSILON;
const LATEX_BOUNDS_TOLERANCE_CSS_PX = 0.5;

type Point = readonly [number, number];
type StoryboardNode = Extract<
  SceneNode,
  { readonly kind: "line" | "path" | "latex_token" }
>;

export interface SemanticStoryboardDomMismatch {
  readonly code:
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
    | "latex_content_bounds"
    | "latex_style";
  readonly nodeId: string | null;
  readonly field: string;
  readonly expected: string;
  readonly actual: string;
}

type NodeSignature =
  | Readonly<{
      kind: "line";
      id: string;
      points: readonly [Point, Point];
      stroke: string;
      strokeWidth: number | null;
      opacity: number | null;
    }>
  | Readonly<{
      kind: "path";
      id: string;
      points: readonly Point[];
      closed: boolean;
      fill: string;
      stroke: string;
      strokeWidth: number | null;
      opacity: number | null;
    }>
  | Readonly<{
      kind: "latex_token";
      id: string;
      x: number | null;
      y: number | null;
      width: number | null;
      height: number | null;
      anchor: "start" | "middle" | "end" | null;
      latex: string;
      color: string;
      fontSize: number | null;
      opacity: number | null;
    }>;

export interface SemanticStoryboardSvgSignature {
  readonly sourceRevision: number;
  readonly viewBox: string;
  readonly paintOrder: readonly string[];
  readonly nodes: readonly NodeSignature[];
  readonly residueFree: boolean;
}

export interface SemanticStoryboardDomInspection {
  readonly signature: SemanticStoryboardSvgSignature;
  readonly mismatches: readonly SemanticStoryboardDomMismatch[];
}

/** Independent copy of the renderer contract: geometry precedes every label. */
export function expectedSemanticStoryboardPaintOrder(
  nodes: readonly SceneNode[],
): readonly string[] {
  const label = (node: SceneNode) =>
    node.kind === "text" ||
    node.kind === "latex" ||
    node.kind === "latex_token";
  return Object.freeze([
    ...nodes.filter((node) => !label(node)).map((node) => node.id),
    ...nodes.filter(label).map((node) => node.id),
  ]);
}

/** Project the settled browser DOM into stable, implementation-light semantics. */
export async function inspectSemanticStoryboardSvgAgainstScene(
  page: Page,
  scene: SceneState,
): Promise<SemanticStoryboardDomInspection> {
  const unsupported = scene.nodes.filter(
    (node) =>
      node.kind !== "line" &&
      node.kind !== "path" &&
      node.kind !== "latex_token",
  );
  if (unsupported.length) {
    throw new TypeError(
      `Semantic storyboard SVG oracle does not support: ${unsupported
        .map((node) => `${node.id}:${node.kind}`)
        .join(", ")}`,
    );
  }
  await page.evaluate(async () => document.fonts.ready);
  return page
    .getByTestId("semantic-storyboard-stage")
    .locator("svg")
    .first()
    .evaluate(
      (root, input): SemanticStoryboardDomInspection => {
        const SVG_NS = "http://www.w3.org/2000/svg";
        const mismatches: SemanticStoryboardDomMismatch[] = [];
        const signatures: NodeSignature[] = [];
        const same = (left: unknown, right: unknown) =>
          JSON.stringify(left) === JSON.stringify(right);
        const check = (
          code: SemanticStoryboardDomMismatch["code"],
          nodeId: string | null,
          passes: boolean,
          field: string = code,
          expected: string = "match",
          actual: string = "mismatch",
        ) => {
          if (!passes) {
            mismatches.push({ code, nodeId, field, expected, actual });
          }
        };
        const number = (value: string | null) => {
          if (!value?.trim()) return null;
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : null;
        };
        const pixels = (value: string) => {
          const match = value.match(/^([-+]?(?:\d+\.?\d*|\.\d+))px$/);
          return match ? Number(match[1]) : null;
        };
        const opacity = (element: Element) => {
          const parsed = Number.parseFloat(getComputedStyle(element).opacity);
          return Number.isFinite(parsed) ? parsed : null;
        };
        const round = (value: number) => Number(value.toFixed(6));
        const distance = (left: Point, right: Point) =>
          Math.hypot(left[0] - right[0], left[1] - right[1]);
        const directChild = (
          group: Element,
          tag: string,
          code: SemanticStoryboardDomMismatch["code"],
          id: string,
        ) => {
          const children = Array.from(group.children);
          const valid =
            children.length === 1 &&
            children[0].namespaceURI === SVG_NS &&
            children[0].localName === tag;
          check(code, id, valid);
          return valid ? children[0] : null;
        };

        const paintOrder = Array.from(
          root.children,
          (child) =>
            child.getAttribute("data-element-id") ?? `<${child.localName}>`,
        );
        check(
          "paint_order",
          null,
          same(paintOrder, input.paintOrder),
          "root child order",
          JSON.stringify(input.paintOrder),
          JSON.stringify(paintOrder),
        );
        const expectedIds = new Set(input.nodes.map((node) => node.id));
        for (const child of root.children) {
          const id = child.getAttribute("data-element-id");
          check(
            "unexpected_element",
            id,
            child.namespaceURI === SVG_NS &&
              child.localName === "g" &&
              id !== null &&
              expectedIds.has(id),
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
          if (element.namespaceURI !== SVG_NS) continue;
          const id =
            element
              .closest("[data-element-id]")
              ?.getAttribute("data-element-id") ?? null;
          for (const property of residue) {
            check(
              "presentation_residue",
              id,
              !element.hasAttribute(property) &&
                !(element as SVGElement).style.getPropertyValue(property),
              property,
              "absent",
              element.getAttribute(property) ??
                (element as SVGElement).style.getPropertyValue(property),
            );
          }
        }

        const parsePath = (d: string | null) => {
          if (!d) return null;
          const tokens: Array<string | number> = [];
          const pattern =
            /([MLZ])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/g;
          let end = 0;
          for (const match of d.matchAll(pattern)) {
            if (!/^[\s,]*$/.test(d.slice(end, match.index))) return null;
            tokens.push(match[1] ?? Number(match[2]));
            end = (match.index ?? 0) + match[0].length;
          }
          if (!/^[\s,]*$/.test(d.slice(end)) || tokens[0] !== "M") return null;
          const points: Point[] = [];
          let index = 0;
          while (index < tokens.length && tokens[index] !== "Z") {
            if (tokens[index] !== (index === 0 ? "M" : "L")) return null;
            const x = tokens[index + 1];
            const y = tokens[index + 2];
            if (typeof x !== "number" || typeof y !== "number") return null;
            points.push([x, y]);
            index += 3;
          }
          const closed = tokens[index] === "Z";
          if ((closed ? index + 1 : index) !== tokens.length) return null;
          return points.length >= 2 ? { points, closed } : null;
        };

        for (const node of input.nodes) {
          const matches = Array.from(root.children).filter(
            (element) => element.getAttribute("data-element-id") === node.id,
          );
          check("identity", node.id, matches.length === 1);
          if (matches.length !== 1) continue;
          const group = matches[0];
          check("identity", node.id, group.getAttribute("id") === node.id);
          const observedOpacity = opacity(group);
          check(
            "opacity",
            node.id,
            observedOpacity !== null &&
              Math.abs(observedOpacity - node.style.opacity) <= 1e-6,
          );

          if (node.kind === "path") {
            const path = directChild(group, "path", "path_structure", node.id);
            if (!path) continue;
            const parsed = parsePath(path.getAttribute("d"));
            let coordinatesMatch =
              parsed !== null && parsed.points.length === node.points.length;
            if (parsed && coordinatesMatch) {
              parsed.points.forEach((point, pointIndex) => {
                point.forEach((coordinate, axis) => {
                  const expected = node.points[pointIndex][axis];
                  if (
                    Math.abs(coordinate - expected) >
                    input.pathRelativeTolerance *
                      Math.max(1, Math.abs(coordinate), Math.abs(expected))
                  ) {
                    coordinatesMatch = false;
                  }
                });
              });
            }
            const geometryMatches =
              coordinatesMatch && parsed?.closed === node.closed;
            check(
              "path_geometry",
              node.id,
              geometryMatches,
              "M/L/Z geometry",
              `${node.points.length} points, closed=${node.closed}`,
              parsed
                ? `${parsed.points.length} points, closed=${parsed.closed}`
                : "unparseable",
            );
            const paint = {
              fill: path.getAttribute("fill") ?? "",
              stroke: path.getAttribute("stroke") ?? "",
              strokeWidth: number(path.getAttribute("stroke-width")),
              strokeLinecap: path.getAttribute("stroke-linecap") ?? "",
              strokeLinejoin: path.getAttribute("stroke-linejoin") ?? "",
            };
            check(
              "path_style",
              node.id,
              same(paint, {
                fill: node.style.fill,
                stroke: node.style.stroke,
                strokeWidth: node.style.strokeWidth,
                strokeLinecap: "round",
                strokeLinejoin: "round",
              }),
            );
            signatures.push({
              kind: "path",
              id: node.id,
              points: geometryMatches ? node.points : (parsed?.points ?? []),
              closed: geometryMatches ? node.closed : (parsed?.closed ?? false),
              fill: paint.fill,
              stroke: paint.stroke,
              strokeWidth: paint.strokeWidth,
              opacity: observedOpacity,
            });
            continue;
          }

          if (node.kind === "line") {
            const invalid = Array.from(group.querySelectorAll("*")).filter(
              (element) =>
                element.namespaceURI !== SVG_NS ||
                !["g", "path", "line", "polyline"].includes(element.localName),
            );
            const shapes = Array.from(
              group.querySelectorAll<SVGGeometryElement>("path,line,polyline"),
            );
            check(
              "line_structure",
              node.id,
              invalid.length === 0 && shapes.length > 0,
            );
            const paints = shapes.map((shape) => ({
              fill: shape.getAttribute("fill") ?? "",
              stroke: shape.getAttribute("stroke") ?? "",
              strokeWidth: number(shape.getAttribute("stroke-width")),
            }));
            check(
              "line_style",
              node.id,
              same(
                paints,
                shapes.map(() => ({
                  fill: "none",
                  stroke: node.style.stroke,
                  strokeWidth: node.style.strokeWidth,
                })),
              ),
            );
            const [start, finish] = node.points;
            const delta = [finish[0] - start[0], finish[1] - start[1]] as const;
            const expectedLength = Math.hypot(delta[0], delta[1]);
            const squared = expectedLength ** 2;
            let totalLength = 0;
            let observed: readonly [Point, Point] | null = null;
            for (const shape of shapes) {
              let length = Number.NaN;
              try {
                length = shape.getTotalLength();
              } catch {
                // The checks below retain a useful mismatch.
              }
              if (!Number.isFinite(length) || length <= 0) {
                check("line_geometry", node.id, false, "positive path length");
                continue;
              }
              totalLength += length;
              let crossTrackError = 0;
              let projectionLow = Infinity;
              let projectionHigh = -Infinity;
              for (let sample = 0; sample <= 32; sample += 1) {
                const point = shape.getPointAtLength((length * sample) / 32);
                const x = point.x - start[0];
                const y = point.y - start[1];
                const projection = (x * delta[0] + y * delta[1]) / squared;
                projectionLow = Math.min(projectionLow, projection);
                projectionHigh = Math.max(projectionHigh, projection);
                crossTrackError = Math.max(
                  crossTrackError,
                  Math.abs(x * delta[1] - y * delta[0]) / expectedLength,
                );
              }
              check(
                "line_geometry",
                node.id,
                crossTrackError <= input.lineTolerance &&
                  Math.abs(projectionLow) <= 0.001 &&
                  Math.abs(projectionHigh - 1) <= 0.001,
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
              if (!observed) {
                observed =
                  reverse < forward ? [endpoints[1], endpoints[0]] : endpoints;
              }
            }
            const ratio = expectedLength ? totalLength / expectedLength : NaN;
            check(
              "line_geometry",
              node.id,
              Number.isFinite(ratio) &&
                Math.min(Math.abs(ratio - 1), Math.abs(ratio - 2)) <= 0.02,
              "aggregate rendered length",
              "one or two coincident strokes",
              String(ratio),
            );
            signatures.push({
              kind: "line",
              id: node.id,
              points: observed
                ? [
                    [round(observed[0][0]), round(observed[0][1])],
                    [round(observed[1][0]), round(observed[1][1])],
                  ]
                : [
                    [Number.NaN, Number.NaN],
                    [Number.NaN, Number.NaN],
                  ],
              stroke: paints[0]?.stroke ?? "",
              strokeWidth: paints[0]?.strokeWidth ?? null,
              opacity: observedOpacity,
            });
            continue;
          }

          const foreign = directChild(
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
          check("latex_structure", node.id, container !== null);
          if (!container) continue;
          const katex =
            container.children.length === 1 &&
            container.firstElementChild instanceof HTMLElement &&
            container.firstElementChild.classList.contains("katex")
              ? container.firstElementChild
              : null;
          check("latex_structure", node.id, katex !== null);
          const left =
            node.x -
            (node.anchor === "middle"
              ? node.width / 2
              : node.anchor === "end"
                ? node.width
                : 0);
          const box = {
            left: number(foreign.getAttribute("x")),
            y: number(foreign.getAttribute("y")),
            width: number(foreign.getAttribute("width")),
            height: number(foreign.getAttribute("height")),
          };
          check(
            "latex_geometry",
            node.id,
            same(box, {
              left,
              y: node.y,
              width: node.width,
              height: node.height,
            }),
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
          const visual = katex?.querySelector<HTMLElement>(
            ":scope > .katex-html",
          );
          check("latex_structure", node.id, visual !== null);
          if (visual && katex) {
            // Match the dedicated token-fit proof: KaTeX's own layout box is
            // the rendered token. A Range over its glyph tree includes line-
            // box ink outside that semantic box after SVG viewBox scaling.
            const contentBounds = katex.getBoundingClientRect();
            const frameBounds = foreign.getBoundingClientRect();
            const describe = (bounds: DOMRect) =>
              JSON.stringify({
                left: round(bounds.left),
                top: round(bounds.top),
                right: round(bounds.right),
                bottom: round(bounds.bottom),
                width: round(bounds.width),
                height: round(bounds.height),
              });
            check(
              "latex_content_bounds",
              node.id,
              contentBounds.width > 0 &&
                contentBounds.height > 0 &&
                contentBounds.left >=
                  frameBounds.left - input.latexBoundsToleranceCssPx &&
                contentBounds.right <=
                  frameBounds.right + input.latexBoundsToleranceCssPx &&
                contentBounds.top >=
                  frameBounds.top - input.latexBoundsToleranceCssPx &&
                contentBounds.bottom <=
                  frameBounds.bottom + input.latexBoundsToleranceCssPx,
              "KaTeX layout box",
              `inside ${describe(frameBounds)}`,
              describe(contentBounds),
            );
          }
          const anchorMap = {
            "flex-start": "start",
            center: "middle",
            "flex-end": "end",
          } as const;
          const anchor =
            anchorMap[
              container.style.justifyContent as keyof typeof anchorMap
            ] ?? null;
          const colorProbe = document.createElement("div");
          colorProbe.style.color = node.style.color;
          const paint = {
            color: container.style.color,
            fontSize: pixels(container.style.fontSize),
            width: container.style.width,
            height: container.style.height,
            display: container.style.display,
            alignItems: container.style.alignItems,
            justifyContent: container.style.justifyContent,
            whiteSpace: container.style.whiteSpace,
          };
          check(
            "latex_style",
            node.id,
            same(paint, {
              color: colorProbe.style.color,
              fontSize: node.style.fontSize,
              width: "100%",
              height: "100%",
              display: "flex",
              alignItems: "center",
              justifyContent:
                node.anchor === "start"
                  ? "flex-start"
                  : node.anchor === "middle"
                    ? "center"
                    : "flex-end",
              whiteSpace: "nowrap",
            }),
          );
          signatures.push({
            kind: "latex_token",
            id: node.id,
            x:
              box.left === null || box.width === null || anchor === null
                ? null
                : box.left +
                  (anchor === "middle"
                    ? box.width / 2
                    : anchor === "end"
                      ? box.width
                      : 0),
            y: box.y,
            width: box.width,
            height: box.height,
            anchor,
            latex,
            color: paint.color,
            fontSize: paint.fontSize,
            opacity: observedOpacity,
          });
        }

        const stage = root.closest<HTMLElement>(
          '[data-testid="semantic-storyboard-stage"]',
        );
        const sourceRevision = Number(stage?.dataset.sceneRevision);
        check(
          "identity",
          null,
          sourceRevision === input.sourceRevision,
          "stage scene revision",
          String(input.sourceRevision),
          String(sourceRevision),
        );
        return {
          signature: {
            sourceRevision,
            viewBox: root.getAttribute("viewBox") ?? "",
            paintOrder,
            nodes: signatures,
            residueFree: !mismatches.some(
              (mismatch) =>
                mismatch.code === "transient_node" ||
                mismatch.code === "presentation_residue",
            ),
          },
          mismatches,
        };
      },
      {
        sourceRevision: scene.revision,
        nodes: scene.nodes as readonly StoryboardNode[],
        paintOrder: expectedSemanticStoryboardPaintOrder(scene.nodes),
        lineTolerance: LINE_TOLERANCE,
        pathRelativeTolerance: PATH_RELATIVE_TOLERANCE,
        latexBoundsToleranceCssPx: LATEX_BOUNDS_TOLERANCE_CSS_PX,
      },
    );
}

/** Assert exact terminal visual semantics and return a Replay-stable signature. */
export async function expectSemanticStoryboardSvgMatchesScene(
  page: Page,
  scene: SceneState,
): Promise<SemanticStoryboardSvgSignature> {
  const inspection = await inspectSemanticStoryboardSvgAgainstScene(
    page,
    scene,
  );
  expect(
    inspection.mismatches,
    "semantic storyboard SVG must exactly materialize its certified scene",
  ).toEqual([]);
  return inspection.signature;
}
