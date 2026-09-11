import { describe, expect, it } from "vitest";

import type { ChoreographyLayout } from "@/lib/live-scene";
import {
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
  type ProjectileChoreographySceneCheckpointEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  type ProjectileMotionCheckpointId,
  type ProjectileMotionClarificationTopic,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
  type ProjectileMotionStateV1,
} from "@/lib/live-scene/projectile-motion";

import {
  EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
  createAcceptedProjectileCheckpoint,
  preflightProjectileChoreographyReplay,
  prepareProjectileChoreographyCheckpoint,
  type AcceptedProjectileChoreographyCheckpoint,
  type ProjectileChoreographyFrontier,
} from "./projectile-choreography-playback";

const PROBLEM_HASHES = Object.freeze({
  "20:30": "f2ac3f33e3a48dacdd0e256937330b6f36d39451968aa3882d92f3ce3c436491",
  "20:45": "0e8a1195af0f5b3fd3814628344193687fc2cff9c8573baf0c7413921f797cfa",
  "20:60": "b6859d7baf2204ebc98de07b974b02c39521a27ea1c5b1eb475e3daa6a083b59",
  "25:30": "6014ed0114f009bfd6e50acd99ccdccd42f28a286465d9dc9a2987cf196ca85e",
  "25:45": "e947b64e547b77b7e7899b1f8936138d49d8f5a027b38e9878f4e991a34665c3",
  "25:60": "98add4003547fa75b0f8debc391ab4e8a17f2b8cc656f0f9b61b838f3fe82d6f",
  "30:30": "aa4172502435083f997288d04ccbde34d0fa446430d37b75700a2d4b8b0f5bfc",
  "30:45": "205df15d13df182f0a62c47c7c850888797d11fd52774db7d490d46c2fca80bb",
  "30:60": "3366d188d60fbfe6a523a63f9ee0ece7bee84eed709e056026d33f5b8859e8c3",
} satisfies Readonly<Record<string, string>>);

interface EventOptions {
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly action: "advance" | "clarify" | "retarget";
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly route: ProjectileMotionRouteV1;
  readonly baseComponent: ProjectileMotionStateV1 | null;
  readonly resultComponent: ProjectileMotionStateV1;
  readonly baseRevision: number;
  readonly baseHead: string | null;
}

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function problem(
  speedMps: 20 | 25 | 30 = 20,
  angleDeg: 30 | 45 | 60 = 45,
): ProjectileMotionProblemSpecV1 {
  return { v: 1, speedMps, angleDeg };
}

function problemHash(value: ProjectileMotionProblemSpecV1): string {
  return PROBLEM_HASHES[`${value.speedMps}:${value.angleDeg}`];
}

function component(
  problemSpec: ProjectileMotionProblemSpecV1,
  lastMainCheckpoint: ProjectileMotionStateV1["lastMainCheckpoint"],
  clarifiedTopics: readonly ProjectileMotionClarificationTopic[] = [],
  activeClarification: ProjectileMotionClarificationTopic | null = null,
): ProjectileMotionStateV1 {
  return {
    kind: "projectile_motion",
    id: "lesson",
    problemSpec,
    lastMainCheckpoint,
    clarifiedTopics,
    activeClarification,
  };
}

function head(revision: number): string {
  return ((revision % 9) + 1).toString().repeat(64);
}

function event(options: EventOptions): ProjectileChoreographySceneCheckpointEventV1 {
  const resultRevision = options.baseRevision + 1;
  const resultHead = head(resultRevision);
  const baseProblem = options.baseComponent?.problemSpec ?? null;
  const resultProblem = options.resultComponent.problemSpec;
  const baseProblemSpecSha256 =
    baseProblem === null ? null : problemHash(baseProblem);
  const resultProblemSpecSha256 = problemHash(resultProblem);
  const target = "lesson__equation";
  const narration = `${options.checkpointId} at ${resultProblem.speedMps} m/s and ${resultProblem.angleDeg} degrees.`;
  const viewport = { v: 1, x: 0, y: 0, width: 800, height: 600 };
  const presentation = {
    v: 1,
    checkpointId: options.checkpointId,
    checkpointNarration: narration,
    baseViewports: { cinematic: viewport, compact: viewport },
    resultViewports: { cinematic: viewport, compact: viewport },
    transientFree: true,
  };
  const beat = {
    v: 1,
    beatId: `beat_${options.baseRevision + 1}`,
    componentKind: "projectile_motion",
    componentId: "lesson",
    baseProblemSpec: baseProblem,
    resultProblemSpec: resultProblem,
    route: options.route,
  };
  const patch = {
    v: 1,
    patchId: `lesson__cp_${options.checkpointId}`,
    narration,
    operations: [
      {
        op: "put",
        node: {
          id: target,
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 400,
          y: 80,
          text: narration,
          style: {
            color: "hsl(var(--chalk))",
            fontSize: 32,
            opacity: 1,
            anchor: "middle",
          },
        },
      },
    ],
  };
  const receipt = {
    issuer: "projectile_motion_verifier",
    componentKind: "projectile_motion",
    componentId: "lesson",
    action: options.action,
    checkpointId: options.checkpointId,
    clarificationTopic: options.clarificationTopic,
    baseProblemSpecSha256,
    resultProblemSpecSha256,
    operationTargets: [target],
    obligationCodes: [...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS],
    verified: true,
  };
  const choreography = {
    v: 2,
    phase: {
      cues: [
        {
          cue: options.baseRevision === 0 ? "enter" : "transform",
          targetIds: [target],
        },
      ],
      durationMs: 800,
      easing: "ease_out_quart",
      holdAfterMs: 500,
    },
  };
  const certificate = {
    body: {
      v: 1,
      issuer: "projectile_motion_compiler",
      compilerVersion: PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
      canonicalization: "murmur-json-v1",
      hashAlgorithm: "sha256",
      beatId: beat.beatId,
      routedBeatSha256: "a".repeat(64),
      componentKind: "projectile_motion",
      componentId: "lesson",
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseProblemSpecSha256,
      resultProblemSpecSha256,
      baseLowLevelRevision: options.baseRevision,
      resultLowLevelRevision: resultRevision,
      baseSemanticRevision: options.baseRevision,
      resultSemanticRevision: resultRevision,
      baseLowLevelSceneSha256: "b".repeat(64),
      resultLowLevelSceneSha256: "c".repeat(64),
      baseSemanticSceneSha256: "d".repeat(64),
      resultSemanticSceneSha256: "e".repeat(64),
      patchSha256: "f".repeat(64),
      receiptSha256: "0".repeat(64),
      presentationCheckpoint: clone(presentation),
      choreographySha256: "1".repeat(64),
      previousCertificateSha256: options.baseHead,
    },
    certificateSha256: resultHead,
  };
  return {
    type: "projectile_choreography_scene_checkpoint",
    generation: 1,
    attempt: 1,
    sequence: resultRevision,
    baseRevision: options.baseRevision,
    resultRevision,
    patch,
    semantic: {
      baseProblemSpec: baseProblem,
      resultProblemSpec: resultProblem,
      beat,
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseComponent: options.baseComponent,
      resultComponent: options.resultComponent,
      semanticBaseRevision: options.baseRevision,
      semanticResultRevision: resultRevision,
      semanticBaseCertificateSha256: options.baseHead,
      semanticResultCertificateSha256: resultHead,
      receipt,
      presentation,
      choreography,
      certificate,
    },
  } as ProjectileChoreographySceneCheckpointEventV1;
}

function acceptedFrontier(
  accepted: AcceptedProjectileChoreographyCheckpoint,
): ProjectileChoreographyFrontier {
  return {
    scene: accepted.scene,
    semanticScene: accepted.semanticScene,
    viewport: accepted.viewport,
    layout: accepted.layout,
    certificateHeadSha256: accepted.presentation.certificateSha256,
  };
}

function accept(
  frontier: ProjectileChoreographyFrontier,
  checkpoint: ProjectileChoreographySceneCheckpointEventV1,
  layout: ChoreographyLayout = "cinematic",
): AcceptedProjectileChoreographyCheckpoint {
  return createAcceptedProjectileCheckpoint(
    prepareProjectileChoreographyCheckpoint(frontier, checkpoint, layout),
    { status: "completed", firstCuePresented: true },
  );
}

function advance(
  frontier: ProjectileChoreographyFrontier,
  lastMainCheckpoint: ProjectileMotionStateV1["lastMainCheckpoint"],
  targetStage: "setup" | "launch" | "flight" | "solve",
): AcceptedProjectileChoreographyCheckpoint {
  const base = frontier.semanticScene.components[0] ?? null;
  const result = component(
    base?.problemSpec ?? problem(),
    lastMainCheckpoint,
    base?.clarifiedTopics ?? [],
  );
  return accept(
    frontier,
    event({
      checkpointId: lastMainCheckpoint ?? "setup",
      action: "advance",
      clarificationTopic: null,
      route: { intent: "advance", targetStage },
      baseComponent: base,
      resultComponent: result,
      baseRevision: frontier.scene.revision,
      baseHead: frontier.certificateHeadSha256,
    }),
  );
}

describe("projectile choreography playback frontier", () => {
  it("joins advance, one-shot clarification, retarget, and continued flight atomically", () => {
    const records: AcceptedProjectileChoreographyCheckpoint[] = [];
    const setup = advance(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      "setup",
      "setup",
    );
    records.push(setup);
    const decomposed = advance(
      acceptedFrontier(setup),
      "decompose_velocity",
      "launch",
    );
    records.push(decomposed);

    const clarifyBase = decomposed.semanticScene.components[0];
    const clarified = accept(
      acceptedFrontier(decomposed),
      event({
        checkpointId: "horizontal_velocity_detail",
        action: "clarify",
        clarificationTopic: "horizontal_velocity",
        route: { intent: "clarify", topic: "horizontal_velocity" },
        baseComponent: clarifyBase,
        resultComponent: component(
          clarifyBase.problemSpec,
          "decompose_velocity",
          ["horizontal_velocity"],
          "horizontal_velocity",
        ),
        baseRevision: 2,
        baseHead: decomposed.presentation.certificateSha256,
      }),
    );
    records.push(clarified);
    expect(clarified.semanticScene.components[0]).toMatchObject({
      lastMainCheckpoint: "decompose_velocity",
      clarifiedTopics: ["horizontal_velocity"],
      activeClarification: "horizontal_velocity",
    });

    const retargetBase = clarified.semanticScene.components[0];
    const targetProblem = problem(30, 60);
    const retargeted = accept(
      acceptedFrontier(clarified),
      event({
        checkpointId: "parameters_retargeted",
        action: "retarget",
        clarificationTopic: null,
        route: { intent: "retarget", targetProblemSpec: targetProblem },
        baseComponent: retargetBase,
        resultComponent: component(
          targetProblem,
          retargetBase.lastMainCheckpoint,
          retargetBase.clarifiedTopics,
          retargetBase.activeClarification,
        ),
        baseRevision: 3,
        baseHead: clarified.presentation.certificateSha256,
      }),
    );
    records.push(retargeted);
    const retargetResult = retargeted.semanticScene.components[0];
    expect(retargetResult).toEqual({
      ...retargetBase,
      problemSpec: targetProblem,
    });

    const traced = advance(
      acceptedFrontier(retargeted),
      "trace_ascent",
      "flight",
    );
    records.push(traced);
    expect(traced.semanticScene.components[0]).toMatchObject({
      problemSpec: targetProblem,
      lastMainCheckpoint: "trace_ascent",
      clarifiedTopics: ["horizontal_velocity"],
      activeClarification: null,
    });
    expect(
      prepareProjectileChoreographyCheckpoint(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        setup.event,
        "cinematic",
      ).plan.choreographyPlan.v,
    ).toBe(2);

    const replay = preflightProjectileChoreographyReplay(records);
    expect(replay.checkpoints).toHaveLength(5);
    expect(replay.frontier).toEqual(acceptedFrontier(traced));
    expect(Object.isFrozen(replay.records)).toBe(true);
  });

  it("accepts only visible post-paint settlements", () => {
    const setup = prepareProjectileChoreographyCheckpoint(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      event({
        checkpointId: "setup",
        action: "advance",
        clarificationTopic: null,
        route: { intent: "advance", targetStage: "setup" },
        baseComponent: null,
        resultComponent: component(problem(), "setup"),
        baseRevision: 0,
        baseHead: null,
      }),
      "compact",
    );

    expect(
      createAcceptedProjectileCheckpoint(setup, {
        status: "cancelled_to_checkpoint",
        firstCuePresented: true,
      }).presentation.settlement,
    ).toBe("cancelled_to_checkpoint");
    expect(() =>
      createAcceptedProjectileCheckpoint(setup, {
        status: "cancelled_before_presented",
        firstCuePresented: false,
      }),
    ).toThrow(/fully settled visible checkpoint/);
    expect(() =>
      createAcceptedProjectileCheckpoint(setup, {
        status: "failed",
        firstCuePresented: true,
      }),
    ).toThrow(/fully settled visible checkpoint/);
  });

  it("rejects a certified event whose base component is not the accepted component", () => {
    const setup = advance(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      "setup",
      "setup",
    );
    const decomposed = advance(
      acceptedFrontier(setup),
      "decompose_velocity",
      "launch",
    );
    const staleBase = setup.semanticScene.components[0];
    const staleEvent = event({
      checkpointId: "decompose_velocity",
      action: "advance",
      clarificationTopic: null,
      route: { intent: "advance", targetStage: "launch" },
      baseComponent: staleBase,
      resultComponent: component(staleBase.problemSpec, "decompose_velocity"),
      baseRevision: 2,
      baseHead: decomposed.presentation.certificateSha256,
    });

    expect(() =>
      prepareProjectileChoreographyCheckpoint(
        acceptedFrontier(decomposed),
        staleEvent,
        "cinematic",
      ),
    ).toThrow(/base component does not exact-match/);
  });

  it("rejects premature, repeated, and malformed clarification state", () => {
    const setup = advance(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      "setup",
      "setup",
    );
    const setupState = setup.semanticScene.components[0];
    const premature = event({
      checkpointId: "horizontal_velocity_detail",
      action: "clarify",
      clarificationTopic: "horizontal_velocity",
      route: { intent: "clarify", topic: "horizontal_velocity" },
      baseComponent: setupState,
      resultComponent: component(
        setupState.problemSpec,
        "setup",
        ["horizontal_velocity"],
        "horizontal_velocity",
      ),
      baseRevision: 1,
      baseHead: setup.presentation.certificateSha256,
    });
    expect(() =>
      prepareProjectileChoreographyCheckpoint(
        acceptedFrontier(setup),
        premature,
        "cinematic",
      ),
    ).toThrow(/premature/);

    const decomposed = advance(
      acceptedFrontier(setup),
      "decompose_velocity",
      "launch",
    );
    const base = decomposed.semanticScene.components[0];
    const clarified = accept(
      acceptedFrontier(decomposed),
      event({
        checkpointId: "horizontal_velocity_detail",
        action: "clarify",
        clarificationTopic: "horizontal_velocity",
        route: { intent: "clarify", topic: "horizontal_velocity" },
        baseComponent: base,
        resultComponent: component(
          base.problemSpec,
          base.lastMainCheckpoint,
          ["horizontal_velocity"],
          "horizontal_velocity",
        ),
        baseRevision: 2,
        baseHead: decomposed.presentation.certificateSha256,
      }),
    );
    const clarifiedState = clarified.semanticScene.components[0];
    const repeated = event({
      checkpointId: "horizontal_velocity_detail",
      action: "clarify",
      clarificationTopic: "horizontal_velocity",
      route: { intent: "clarify", topic: "horizontal_velocity" },
      baseComponent: clarifiedState,
      resultComponent: clarifiedState,
      baseRevision: 3,
      baseHead: clarified.presentation.certificateSha256,
    });
    expect(() =>
      prepareProjectileChoreographyCheckpoint(
        acceptedFrontier(clarified),
        repeated,
        "cinematic",
      ),
    ).toThrow(/one-shot/);

    const inactive = event({
      checkpointId: "horizontal_velocity_detail",
      action: "clarify",
      clarificationTopic: "horizontal_velocity",
      route: { intent: "clarify", topic: "horizontal_velocity" },
      baseComponent: base,
      resultComponent: component(
        base.problemSpec,
        base.lastMainCheckpoint,
        ["horizontal_velocity"],
      ),
      baseRevision: 2,
      baseHead: decomposed.presentation.certificateSha256,
    });
    expect(() =>
      prepareProjectileChoreographyCheckpoint(
        acceptedFrontier(decomposed),
        inactive,
        "cinematic",
      ),
    ).toThrow(/clarification must preserve|canonical one-shot/);
  });

  it("rejects retarget continuity loss and tampered Replay records", () => {
    const setup = advance(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      "setup",
      "setup",
    );
    const current = setup.semanticScene.components[0];
    const targetProblem = problem(25, 30);
    const losesFrontier = event({
      checkpointId: "parameters_retargeted",
      action: "retarget",
      clarificationTopic: null,
      route: { intent: "retarget", targetProblemSpec: targetProblem },
      baseComponent: current,
      resultComponent: component(targetProblem, "decompose_velocity"),
      baseRevision: 1,
      baseHead: setup.presentation.certificateSha256,
    });
    expect(() =>
      prepareProjectileChoreographyCheckpoint(
        acceptedFrontier(setup),
        losesFrontier,
        "cinematic",
      ),
    ).toThrow(/retarget must preserve|change only/);

    const tampered = clone(setup);
    (
      tampered.presentation as {
        certificateSha256: string;
      }
    ).certificateSha256 = "f".repeat(64);
    expect(() => preflightProjectileChoreographyReplay([tampered])).toThrow(
      /does not exact-match/,
    );
  });
});
