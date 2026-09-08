import { describe, expect, it } from "vitest";

import { decodeViewportPoseV1 } from "./choreography";
import { planCheckpointChoreography } from "./choreography-planner";
import { decodeCompiledCheckpointV2 } from "./checkpoint";
import { LiveSceneProtocolError } from "./patch";
import { createSceneState } from "./state";
import type { SceneState } from "./types";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function nested(value: Record<string, unknown>, key: string): Record<string, unknown> {
  return value[key] as Record<string, unknown>;
}

function pose(
  overrides: Partial<{ x: number; y: number; width: number; height: number }> = {},
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
    expect(planned.motionPlan.steps.map((step) => [step.type, step.id])).toEqual([
      ["remove", "lesson__old"],
      ["update", "lesson__equation"],
      ["enter", "lesson__new"],
    ]);
    expect(planned.baseViewport).toEqual(pose());
    expect(planned.resultViewport).toEqual(pose({ x: 80, y: 75, width: 640, height: 450 }));
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
    expect(errorCode(() => plan(checkpoint(), { scene: currentScene(2) }))).toBe(
      "revision_mismatch",
    );
    expect(errorCode(() => plan(checkpoint(), { previous: "8".repeat(64) }))).toBe(
      "revision_mismatch",
    );
    expect(() => plan(checkpoint(), { previous: "not-a-digest" })).toThrow(
      /lowercase SHA-256/,
    );
    expect(() => plan(checkpoint(), { viewport: pose({ x: 1, width: 799 }) })).toThrow(
      /does not join/,
    );
  });

  it("rejects any patch or cue target outside the routed component namespace", () => {
    const source = checkpoint();
    const patch = nested(source, "patch");
    const operations = patch.operations as Record<string, unknown>[];
    nested(operations[2], "node").id = "other__new";
    const receipt = nested(source, "receipt");
    receipt.operationTargets = ["lesson__equation", "lesson__old", "other__new"];
    const choreography = nested(nested(source, "choreography"), "phase");
    const cues = choreography.cues as Record<string, unknown>[];
    cues[0].targetIds = ["other__new"];

    expect(() => plan(source)).toThrow(/component namespace/);
  });

  it.each([
    ["enter", 0, ["lesson__equation"]],
    ["exit", 1, ["lesson__equation"]],
    ["transform", 2, ["lesson__new"]],
  ])("requires %s cue targets to equal the corresponding visible diff", (_cue, index, targets) => {
    const source = checkpoint();
    const phase = nested(nested(source, "choreography"), "phase");
    (phase.cues as Record<string, unknown>[])[index].targetIds = targets;

    expect(() => plan(source)).toThrow(/must exactly match/);
  });

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

      expect(() => plan(source)).toThrow(/must exist in the checkpoint result scene/);
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

    expect(() => plan(source, { viewport: pose({ y: 1, height: 599 }) })).toThrow();
    expect(source).toEqual(sourceBefore);
    expect(accepted).toEqual(acceptedBefore);
  });
});
