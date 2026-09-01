import { createSceneState, type SceneState } from "@/lib/live-scene";

const CHALK = "hsl(var(--chalk))";
const CHALK_SOFT = "hsl(var(--chalk-soft))";
const LAVENDER = "hsl(var(--lavender))";
const AMBER = "hsl(var(--amber))";

const DRAW_PRESENTATION = { enter: "draw", exit: "fade" } as const;
const FADE_PRESENTATION = { enter: "fade", exit: "fade" } as const;

type SceneNode = SceneState["nodes"][number];

function textNode(
  id: string,
  text: string,
  x: number,
  y: number,
  options: {
    color?: string;
    fontSize?: number;
    anchor?: "start" | "middle" | "end";
  } = {}
): SceneNode {
  return {
    id,
    kind: "text",
    x,
    y,
    text,
    style: {
      color: options.color ?? CHALK,
      fontSize: options.fontSize ?? 22,
      opacity: 1,
      anchor: options.anchor ?? "start",
    },
    presentation: FADE_PRESENTATION,
  };
}

function lineNode(
  id: string,
  points: readonly [readonly [number, number], readonly [number, number]],
  color = LAVENDER,
  strokeWidth = 4
): SceneNode {
  return {
    id,
    kind: "line",
    points,
    style: {
      stroke: color,
      strokeWidth,
      opacity: 1,
      roughness: 0.75,
    },
    presentation: DRAW_PRESENTATION,
  };
}

function pathNode(
  id: string,
  points: readonly (readonly [number, number])[],
  color: string,
  strokeWidth: number
): SceneNode {
  return {
    id,
    kind: "path",
    points,
    closed: false,
    style: {
      stroke: color,
      strokeWidth,
      fill: "none",
      opacity: 1,
      roughness: 0.45,
    },
    presentation: DRAW_PRESENTATION,
  };
}

export const PYTHAGORAS_EMPTY_SCENE = createSceneState({
  revision: 0,
  nodes: [],
});

const FOUNDATION_NODES: readonly SceneNode[] = [
  textNode("lesson-title", "A right triangle, built one idea at a time", 400, 64, {
    fontSize: 28,
    anchor: "middle",
  }),
  lineNode("triangle-side-a", [
    [185, 405],
    [525, 405],
  ]),
  lineNode("triangle-side-b", [
    [525, 405],
    [525, 145],
  ]),
  lineNode("triangle-hypotenuse", [
    [525, 145],
    [185, 405],
  ]),
  pathNode(
    "triangle-right-angle",
    [
      [485, 405],
      [485, 365],
      [525, 365],
    ],
    CHALK_SOFT,
    2.5
  ),
  textNode("triangle-label-a", "a", 350, 438, {
    color: CHALK_SOFT,
    fontSize: 24,
    anchor: "middle",
  }),
  textNode("triangle-label-b", "b", 552, 280, {
    color: CHALK_SOFT,
    fontSize: 24,
    anchor: "middle",
  }),
  textNode("triangle-label-c", "c", 332, 255, {
    color: CHALK_SOFT,
    fontSize: 24,
    anchor: "middle",
  }),
];

export const PYTHAGORAS_FOUNDATION_SCENE = createSceneState({
  revision: 1,
  nodes: FOUNDATION_NODES,
});

const THEOREM_TITLE = textNode(
  "lesson-title",
  "The two shorter sides determine the hypotenuse",
  400,
  64,
  { fontSize: 28, anchor: "middle" }
);

const THEOREM_EQUATION: SceneNode = {
  id: "equation-pythagoras",
  kind: "latex",
  x: 130,
  y: 470,
  latex: "a^2 + b^2 = c^2",
  style: {
    color: AMBER,
    fontSize: 30,
    opacity: 1,
  },
  presentation: FADE_PRESENTATION,
};

export const PYTHAGORAS_THEOREM_SCENE = createSceneState({
  revision: 2,
  nodes: [
    THEOREM_TITLE,
    ...FOUNDATION_NODES.filter((node) => node.id !== "lesson-title"),
    THEOREM_EQUATION,
    textNode(
      "theorem-caption",
      "The areas of the two smaller squares add to the largest.",
      400,
      565,
      { color: CHALK_SOFT, fontSize: 17, anchor: "middle" }
    ),
  ],
});

const INTERRUPTION_ONLY_IDS = new Set([
  "angle-callout-line",
  "angle-callout-title",
  "angle-callout-body",
  "angle-callout-body-2",
]);

/**
 * Branch from whichever scene is currently committed. If the theorem was not
 * accepted yet, it cannot leak into this branch; if it was accepted already,
 * the explanation deliberately retains it.
 */
export function createRightAngleExplanationScene(
  current: SceneState
): SceneState {
  const retained = current.nodes.filter(
    (node) =>
      node.id !== "lesson-title" &&
      node.id !== "triangle-right-angle" &&
      !INTERRUPTION_ONLY_IDS.has(node.id)
  );
  const retainedIds = new Set(retained.map((node) => node.id));
  const missingFoundation = FOUNDATION_NODES.filter(
    (node) =>
      node.id !== "lesson-title" &&
      node.id !== "triangle-right-angle" &&
      !retainedIds.has(node.id)
  );

  return createSceneState({
    revision: current.revision + 1,
    nodes: [
      textNode("lesson-title", "Why is this angle exactly 90 degrees?", 400, 64, {
        fontSize: 28,
        anchor: "middle",
      }),
      ...retained,
      ...missingFoundation,
      pathNode(
        "triangle-right-angle",
        [
          [485, 405],
          [485, 365],
          [525, 365],
        ],
        AMBER,
        4
      ),
      pathNode(
        "angle-callout-line",
        [
          [492, 372],
          [545, 305],
          [570, 305],
        ],
        AMBER,
        2.5
      ),
      textNode("angle-callout-title", "90 degrees", 580, 298, {
        color: AMBER,
        fontSize: 23,
      }),
      textNode(
        "angle-callout-body",
        "Horizontal and vertical legs",
        580,
        326,
        { color: CHALK_SOFT, fontSize: 14 }
      ),
      textNode("angle-callout-body-2", "meet perpendicularly.", 580, 348, {
        color: CHALK_SOFT,
        fontSize: 14,
      }),
    ],
  });
}

export const PYTHAGORAS_SEMANTIC_IDS = Object.freeze([
  "lesson-title",
  "triangle-side-a",
  "triangle-side-b",
  "triangle-hypotenuse",
  "triangle-right-angle",
  "triangle-label-a",
  "triangle-label-b",
  "triangle-label-c",
  "equation-pythagoras",
  "theorem-caption",
  "angle-callout-line",
  "angle-callout-title",
  "angle-callout-body",
  "angle-callout-body-2",
] as const);
