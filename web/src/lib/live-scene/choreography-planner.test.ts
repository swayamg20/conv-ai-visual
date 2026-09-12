import { describe, expect, it } from "vitest";

import { decodeViewportPoseV1 } from "./choreography";
import {
  planCheckpointChoreography,
  planProjectileCheckpointChoreography,
} from "./choreography-planner";
import { decodeCompiledCheckpointV2 } from "./checkpoint";
import { LiveSceneProtocolError } from "./patch";
import {
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
  decodeProjectileChoreographySceneCheckpointEventV1,
} from "./projectile-choreography-stream";
import { createSceneState } from "./state";
import type { SceneState } from "./types";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function nested(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return value[key] as Record<string, unknown>;
}

function pose(
  overrides: Partial<{
    x: number;
    y: number;
    width: number;
    height: number;
  }> = {},
): Record<string, unknown> {
  return { v: 1, x: 0, y: 0, width: 800, height: 600, ...overrides };
}

function textNode(id: string, text: string): Record<string, unknown> {
  return {
    id,
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x: 400,
    y: 80,
    text,
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 32,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function rectNode(id: string): Record<string, unknown> {
  return {
    id,
    kind: "rect",
    presentation: { enter: "scale", exit: "fade" },
    x: 100,
    y: 160,
    width: 120,
    height: 120,
    style: {
      stroke: "hsl(var(--amber))",
      strokeWidth: 2,
      opacity: 1,
      roughness: 0,
      fill: "transparent",
    },
  };
}

function lineNode(
  id: string,
  points: [[number, number], [number, number]],
): Record<string, unknown> {
  return {
    id,
    kind: "line",
    presentation: { enter: "draw", exit: "fade" },
    points,
    style: {
      stroke: "hsl(var(--sage))",
      strokeWidth: 3,
      opacity: 1,
      roughness: 0,
    },
  };
}

function pathNode(
  id: string,
  points: [number, number][],
  closed: boolean,
): Record<string, unknown> {
  return {
    id,
    kind: "path",
    presentation: { enter: "draw", exit: "fade" },
    points,
    closed,
    style: {
      stroke: "hsl(var(--amber))",
      strokeWidth: 3,
      opacity: 1,
      roughness: 0,
      fill: closed ? "hsl(var(--amber))" : "transparent",
    },
  };
}

function checkpoint(): Record<string, unknown> {
  const checkpointPresentation = {
    v: 1,
    checkpointId: "problem",
    checkpointNarration: "Transform the equation and reveal the next term.",
    baseViewports: {
      cinematic: pose(),
      compact: pose({ x: 100, width: 600 }),
    },
    resultViewports: {
      cinematic: pose({ x: 80, y: 75, width: 640, height: 450 }),
      compact: pose({ x: 160, y: 60, width: 480, height: 480 }),
    },
    transientFree: true,
  };
  return {
    beat: {
      v: 2,
      beatId: "beat-problem",
      componentKind: "completing_square",
      componentId: "lesson",
      route: { intent: "advance", targetStage: "setup" },
    },
    checkpointId: "problem",
    patch: {
      v: 1,
      patchId: "lesson__cp_problem",
      narration: "Transform the equation and reveal the next term.",
      operations: [
        { op: "put", node: textNode("lesson__equation", "x² + 6x = 7") },
        { op: "remove", id: "lesson__old" },
        { op: "put", node: rectNode("lesson__new") },
      ],
    },
    receipt: {
      issuer: "completing_square_verifier",
      componentId: "lesson",
      checkpointId: "problem",
      operationTargets: ["lesson__equation", "lesson__old", "lesson__new"],
      obligationCodes: ["stable_id", "component_ownership"],
      verified: true,
    },
    presentation: checkpointPresentation,
    choreography: {
      v: 1,
      phase: {
        cues: [
          { cue: "enter", targetIds: ["lesson__new"] },
          { cue: "exit", targetIds: ["lesson__old"] },
          { cue: "transform", targetIds: ["lesson__equation"] },
          { cue: "emphasize", targetIds: ["lesson__equation"] },
          { cue: "focus", targetIds: ["lesson__equation", "lesson__new"] },
        ],
        durationMs: 900,
        easing: "ease_in_out",
        holdAfterMs: 600,
      },
    },
    certificate: {
      body: {
        v: 2,
        issuer: "semantic_compiler",
        compilerVersion: "murmur.completing_square_choreography.v1",
        canonicalization: "murmur-json-v1",
        hashAlgorithm: "sha256",
        beatId: "beat-problem",
        routedBeatSha256: "a".repeat(64),
        componentKind: "completing_square",
        componentId: "lesson",
        checkpointId: "problem",
        baseRevision: 3,
        resultRevision: 4,
        baseLowLevelSceneSha256: "b".repeat(64),
        resultLowLevelSceneSha256: "c".repeat(64),
        baseSemanticSceneSha256: "d".repeat(64),
        resultSemanticSceneSha256: "e".repeat(64),
        patchSha256: "f".repeat(64),
        receiptSha256: "0".repeat(64),
        presentationCheckpoint: clone(checkpointPresentation),
        choreographySha256: "1".repeat(64),
        previousCertificateSha256: "9".repeat(64),
      },
      certificateSha256: "2".repeat(64),
    },
  };
}

function currentScene(revision = 3): SceneState {
  return createSceneState({
    revision,
    nodes: [
      textNode("other__background", "Keep me") as never,
      textNode("lesson__equation", "x² + 6x") as never,
      rectNode("lesson__old") as never,
    ],
  });
}

const PROJECTILE_PROBLEM = { v: 1, speedMps: 20, angleDeg: 45 } as const;
const PROJECTILE_PROBLEM_SHA256 =
  "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa";
const PROJECTILE_BASE_HEAD = "9".repeat(64);

function projectileMarker(centerX: number): Record<string, unknown> {
  return pathNode(
    "lesson__projectile_marker",
    [
      [centerX - 4, 466],
      [centerX + 4, 466],
      [centerX + 4, 474],
      [centerX - 4, 474],
    ],
    true,
  );
}

function projectileCurrentScene(includeMarker = true): SceneState {
  return createSceneState({
    revision: 2,
    nodes: [
      ...(includeMarker ? [projectileMarker(70) as never] : []),
      lineNode("lesson__vertical_state", [
        [70, 470],
        [70, 400],
      ]) as never,
    ],
  });
}

function projectileCheckpoint(): Record<string, unknown> {
  const checkpointId = "trace_ascent";
  const narration = "Trace the projectile upward while vertical speed falls.";
  const presentation = {
    v: 1,
    checkpointId,
    checkpointNarration: narration,
    baseViewports: { cinematic: pose(), compact: pose() },
    resultViewports: {
      cinematic: pose({ x: 30, y: 40, width: 700, height: 500 }),
      compact: pose({ x: 80, y: 50, width: 640, height: 480 }),
    },
    transientFree: true,
  };
  const baseComponent = {
    kind: "projectile_motion",
    id: "lesson",
    problemSpec: PROJECTILE_PROBLEM,
    lastMainCheckpoint: "decompose_velocity",
    clarifiedTopics: [],
    activeClarification: null,
  };
  const resultComponent = {
    ...baseComponent,
    lastMainCheckpoint: checkpointId,
  };
  const beat = {
    v: 1,
    beatId: "beat-projectile",
    componentKind: "projectile_motion",
    componentId: "lesson",
    baseProblemSpec: PROJECTILE_PROBLEM,
    resultProblemSpec: PROJECTILE_PROBLEM,
    route: { intent: "advance", targetStage: "flight" },
  };
  const operations = [
    { op: "put", node: projectileMarker(270) },
    {
      op: "put",
      node: lineNode("lesson__vertical_state", [
        [270, 470],
        [270, 440],
      ]),
    },
    {
      op: "put",
      node: pathNode(
        "lesson__trajectory_ascent",
        [
          [70, 470],
          [170, 370],
          [270, 470],
        ],
        false,
      ),
    },
  ];
  const choreography = {
    v: 2,
    phase: {
      cues: [
        { cue: "enter", targetIds: ["lesson__trajectory_ascent"] },
        { cue: "transform", targetIds: ["lesson__vertical_state"] },
        {
          cue: "trace_path",
          pathId: "lesson__trajectory_ascent",
          markerId: "lesson__projectile_marker",
        },
        { cue: "emphasize", targetIds: ["lesson__vertical_state"] },
        {
          cue: "focus",
          targetIds: ["lesson__projectile_marker", "lesson__trajectory_ascent"],
        },
      ],
      durationMs: 1_800,
      easing: "linear",
      holdAfterMs: 600,
    },
  };
  const receipt = {
    issuer: "projectile_motion_verifier",
    componentKind: "projectile_motion",
    componentId: "lesson",
    action: "advance",
    checkpointId,
    clarificationTopic: null,
    baseProblemSpecSha256: PROJECTILE_PROBLEM_SHA256,
    resultProblemSpecSha256: PROJECTILE_PROBLEM_SHA256,
    operationTargets: [
      "lesson__projectile_marker",
      "lesson__vertical_state",
      "lesson__trajectory_ascent",
    ],
    obligationCodes: [...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS],
    verified: true,
  };
  const certificate = {
    body: {
      v: 1,
      issuer: "projectile_motion_compiler",
      compilerVersion: PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
      canonicalization: "murmur-json-v1",
      hashAlgorithm: "sha256",
      beatId: "beat-projectile",
      routedBeatSha256: "a".repeat(64),
      componentKind: "projectile_motion",
      componentId: "lesson",
      action: "advance",
      checkpointId,
      clarificationTopic: null,
      baseProblemSpecSha256: PROJECTILE_PROBLEM_SHA256,
      resultProblemSpecSha256: PROJECTILE_PROBLEM_SHA256,
      baseLowLevelRevision: 2,
      resultLowLevelRevision: 3,
      baseSemanticRevision: 2,
      resultSemanticRevision: 3,
      baseLowLevelSceneSha256: "b".repeat(64),
      resultLowLevelSceneSha256: "c".repeat(64),
      baseSemanticSceneSha256: "d".repeat(64),
      resultSemanticSceneSha256: "e".repeat(64),
      patchSha256: "f".repeat(64),
      receiptSha256: "0".repeat(64),
      presentationCheckpoint: clone(presentation),
      choreographySha256: "1".repeat(64),
      previousCertificateSha256: PROJECTILE_BASE_HEAD,
    },
    certificateSha256: "2".repeat(64),
  };
  return {
    type: "projectile_choreography_scene_checkpoint",
    generation: 1,
    attempt: 1,
    sequence: 3,
    baseRevision: 2,
    resultRevision: 3,
    patch: {
      v: 1,
      patchId: `lesson__cp_${checkpointId}`,
      narration,
      operations,
    },
    semantic: {
      baseProblemSpec: PROJECTILE_PROBLEM,
      resultProblemSpec: PROJECTILE_PROBLEM,
      beat,
      action: "advance",
      checkpointId,
      clarificationTopic: null,
      baseComponent,
      resultComponent,
      semanticBaseRevision: 2,
      semanticResultRevision: 3,
      semanticBaseCertificateSha256: PROJECTILE_BASE_HEAD,
      semanticResultCertificateSha256: certificate.certificateSha256,
      receipt,
      presentation,
      choreography,
      certificate,
    },
  };
}

function projectilePlan(
  source = projectileCheckpoint(),
  scene = projectileCurrentScene(),
) {
  return planProjectileCheckpointChoreography({
    checkpoint: decodeProjectileChoreographySceneCheckpointEventV1(source),
    currentScene: scene,
    layout: "cinematic",
    currentViewport: decodeViewportPoseV1(pose()),
    previousCertificateSha256: PROJECTILE_BASE_HEAD,
  });
}

function plan(
  source = checkpoint(),
  overrides: Partial<{
    scene: SceneState;
    layout: "cinematic" | "compact";
    viewport: Record<string, unknown>;
    previous: string | null;
  }> = {},
) {
  return planCheckpointChoreography({
    checkpoint: decodeCompiledCheckpointV2(source),
    currentScene: overrides.scene ?? currentScene(),
    layout: overrides.layout ?? "cinematic",
    currentViewport: decodeViewportPoseV1(overrides.viewport ?? pose()),
    previousCertificateSha256: overrides.previous ?? "9".repeat(64),
  });
}

function errorCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("checkpoint choreography planner", () => {
  it("atomically joins a checkpoint and returns its exact motion, viewport, and cue plans", () => {
    const planned = plan();

    expect(planned.targetScene.revision).toBe(4);
    expect(planned.targetScene.nodes.map((node) => node.id)).toEqual([
      "other__background",
      "lesson__equation",
      "lesson__new",
    ]);
    expect(
      planned.motionPlan.steps.map((step) => [step.type, step.id]),
    ).toEqual([
      ["remove", "lesson__old"],
      ["update", "lesson__equation"],
      ["enter", "lesson__new"],
    ]);
    expect(planned.baseViewport).toEqual(pose());
    expect(planned.resultViewport).toEqual(
      pose({ x: 80, y: 75, width: 640, height: 450 }),
    );
    expect(planned.choreographyPlan.phase.cues).toHaveLength(5);
    expect(Object.isFrozen(planned)).toBe(true);
    expect(Object.isFrozen(planned.targetScene)).toBe(true);
    expect(Object.isFrozen(planned.motionPlan)).toBe(true);
  });

  it("locks viewport selection to the requested layout", () => {
    const planned = plan(checkpoint(), {
      layout: "compact",
      viewport: pose({ x: 100, width: 600 }),
    });

    expect(planned.baseViewport).toEqual(pose({ x: 100, width: 600 }));
    expect(planned.resultViewport).toEqual(
      pose({ x: 160, y: 60, width: 480, height: 480 }),
    );
  });

  it("rejects scene, chain, and viewport join mismatches", () => {
    expect(
      errorCode(() => plan(checkpoint(), { scene: currentScene(2) })),
    ).toBe("revision_mismatch");
    expect(
      errorCode(() => plan(checkpoint(), { previous: "8".repeat(64) })),
    ).toBe("revision_mismatch");
    expect(() => plan(checkpoint(), { previous: "not-a-digest" })).toThrow(
      /lowercase SHA-256/,
    );
    expect(() =>
      plan(checkpoint(), { viewport: pose({ x: 1, width: 799 }) }),
    ).toThrow(/does not join/);
  });

  it("rejects any patch or cue target outside the routed component namespace", () => {
    const source = checkpoint();
    const patch = nested(source, "patch");
    const operations = patch.operations as Record<string, unknown>[];
    nested(operations[2], "node").id = "other__new";
    const receipt = nested(source, "receipt");
    receipt.operationTargets = [
      "lesson__equation",
      "lesson__old",
      "other__new",
    ];
    const choreography = nested(nested(source, "choreography"), "phase");
    const cues = choreography.cues as Record<string, unknown>[];
    cues[0].targetIds = ["other__new"];

    expect(() => plan(source)).toThrow(/component namespace/);
  });

  it.each([
    ["enter", 0, ["lesson__equation"]],
    ["exit", 1, ["lesson__equation"]],
    ["transform", 2, ["lesson__new"]],
  ])(
    "requires %s cue targets to equal the corresponding visible diff",
    (_cue, index, targets) => {
      const source = checkpoint();
      const phase = nested(nested(source, "choreography"), "phase");
      (phase.cues as Record<string, unknown>[])[index].targetIds = targets;

      expect(() => plan(source)).toThrow(/must exactly match/);
    },
  );

  it.each(["emphasize", "focus"])(
    "requires %s targets to exist in the result scene",
    (cue) => {
      const source = checkpoint();
      const phase = nested(nested(source, "choreography"), "phase");
      const target = (phase.cues as Record<string, unknown>[]).find(
        (candidate) => candidate.cue === cue,
      );
      if (!target) throw new Error(`missing ${cue} fixture cue`);
      target.targetIds = ["lesson__old"];

      expect(() => plan(source)).toThrow(
        /must exist in the checkpoint result scene/,
      );
    },
  );

  it("rejects a patch operation that produces no visible change", () => {
    const source = checkpoint();
    nested(source, "patch").operations = [
      { op: "put", node: textNode("lesson__equation", "x² + 6x") },
    ];
    nested(source, "receipt").operationTargets = ["lesson__equation"];
    nested(nested(source, "choreography"), "phase").cues = [
      { cue: "transform", targetIds: ["lesson__equation"] },
      { cue: "focus", targetIds: ["lesson__equation"] },
    ];

    expect(() => plan(source)).toThrow(/patch must change the accepted scene/);
  });

  it("does not mutate accepted scene or checkpoint input on planning failure", () => {
    const source = checkpoint();
    const accepted = currentScene();
    const sourceBefore = clone(source);
    const acceptedBefore = clone(accepted);

    expect(() =>
      plan(source, { viewport: pose({ y: 1, height: 599 }) }),
    ).toThrow();
    expect(source).toEqual(sourceBefore);
    expect(accepted).toEqual(acceptedBefore);
  });
});

describe("projectile checkpoint choreography planner", () => {
  it("partitions V2 trace ownership across enter, transform, and retained marker motion", () => {
    const planned = projectilePlan();

    expect(planned.choreographyPlan.v).toBe(2);
    expect(planned.targetScene.revision).toBe(3);
    expect(
      planned.motionPlan.steps.map((step) => [step.type, step.id]),
    ).toEqual([
      ["update", "lesson__projectile_marker"],
      ["update", "lesson__vertical_state"],
      ["enter", "lesson__trajectory_ascent"],
    ]);
    expect(planned.choreographyPlan.phase.cues.map((cue) => cue.cue)).toEqual([
      "enter",
      "transform",
      "trace_path",
      "emphasize",
      "focus",
    ]);
    expect(planned.resultViewport).toEqual(
      pose({ x: 30, y: 40, width: 700, height: 500 }),
    );
  });

  it.each([
    ["enter", 0, ["lesson__projectile_marker"]],
    ["transform", 1, ["lesson__ghost"]],
  ])(
    "rejects a V2 %s cue that does not exactly own its visible diff",
    (_cue, index, ids) => {
      const source = projectileCheckpoint();
      const cues = nested(
        nested(nested(source, "semantic"), "choreography"),
        "phase",
      ).cues as Record<string, unknown>[];
      cues[index].targetIds = ids;

      expect(() => projectilePlan(source)).toThrow(/must exactly match/);
    },
  );

  it("requires a trace path to be a newly entered open path", () => {
    const source = projectileCheckpoint();
    const operations = nested(source, "patch").operations as Record<
      string,
      unknown
    >[];
    nested(operations[2], "node").closed = true;

    expect(() => projectilePlan(source)).toThrow(/newly entered open path/);
  });

  it("requires a trace marker to be a changed retained closed path", () => {
    const source = projectileCheckpoint();
    const cues = nested(
      nested(nested(source, "semantic"), "choreography"),
      "phase",
    ).cues as Record<string, unknown>[];
    cues[0].targetIds = [
      "lesson__projectile_marker",
      "lesson__trajectory_ascent",
    ];

    expect(() => projectilePlan(source, projectileCurrentScene(false))).toThrow(
      /changed retained closed path/,
    );
  });

  it("requires the retained marker to join both trace endpoints", () => {
    const source = projectileCheckpoint();
    const operations = nested(source, "patch").operations as Record<
      string,
      unknown
    >[];
    nested(operations[0], "node").points = [
      [256, 466],
      [264, 466],
      [264, 474],
      [256, 474],
    ];

    expect(() => projectilePlan(source)).toThrow(
      /move from the path start to its endpoint/,
    );
  });
});
