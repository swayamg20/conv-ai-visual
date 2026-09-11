import { describe, expect, it } from "vitest";

import type {
  ProjectileMotionCheckpointId,
  ProjectileMotionClarificationTopic,
  ProjectileMotionProblemSpecV1,
  ProjectileMotionRouteV1,
} from "./projectile-motion";
import {
  MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
  PROJECTILE_CHOREOGRAPHY_DECLINE_REASONS,
  PROJECTILE_CHOREOGRAPHY_FAILURE_CODES,
  PROJECTILE_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES,
  PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
  PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
  decodeProjectileChoreographySceneCheckpointEventV1,
  decodeProjectileChoreographySceneStreamEventV1,
  parseProjectileChoreographySceneStreamEventV1,
} from "./projectile-choreography-stream";
import { LiveSceneProtocolError } from "./patch";

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

type JsonRecord = Record<string, unknown>;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function nested(value: JsonRecord, key: string): JsonRecord {
  return value[key] as JsonRecord;
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

function pose(): JsonRecord {
  return { v: 1, x: 0, y: 0, width: 800, height: 600 };
}

function component(
  problemSpec: ProjectileMotionProblemSpecV1,
  lastMainCheckpoint: string | null,
  clarifiedTopics: readonly ProjectileMotionClarificationTopic[] = [],
  activeClarification: ProjectileMotionClarificationTopic | null = null,
): JsonRecord {
  return {
    kind: "projectile_motion",
    id: "lesson",
    problemSpec,
    lastMainCheckpoint,
    clarifiedTopics,
    activeClarification,
  };
}

interface CheckpointFixtureOptions {
  readonly checkpointId: ProjectileMotionCheckpointId;
  readonly action: "advance" | "clarify" | "retarget";
  readonly clarificationTopic: ProjectileMotionClarificationTopic | null;
  readonly route: ProjectileMotionRouteV1;
  readonly beatBaseProblem: ProjectileMotionProblemSpecV1 | null;
  readonly resultProblem: ProjectileMotionProblemSpecV1;
  readonly baseComponent: JsonRecord | null;
  readonly resultComponent: JsonRecord;
  readonly baseRevision: number;
}

function defaultFixtureOptions(): CheckpointFixtureOptions {
  const resultProblem = problem();
  return {
    checkpointId: "setup",
    action: "advance",
    clarificationTopic: null,
    route: { intent: "advance", targetStage: "setup" },
    beatBaseProblem: null,
    resultProblem,
    baseComponent: null,
    resultComponent: component(resultProblem, "setup"),
    baseRevision: 0,
  };
}

function checkpointEvent(
  override: Partial<CheckpointFixtureOptions> = {},
): JsonRecord {
  const options = { ...defaultFixtureOptions(), ...override };
  const resultRevision = options.baseRevision + 1;
  const baseProblem =
    options.baseComponent === null
      ? null
      : (options.baseComponent.problemSpec as ProjectileMotionProblemSpecV1);
  const baseProblemSpecSha256 =
    baseProblem === null ? null : problemHash(baseProblem);
  const resultProblemSpecSha256 = problemHash(options.resultProblem);
  const baseHead = options.baseComponent === null ? null : "9".repeat(64);
  const resultHead = "3".repeat(64);
  const narration = "Launch, resolve the velocity, and follow the trajectory.";
  const presentation = {
    v: 1,
    checkpointId: options.checkpointId,
    checkpointNarration: narration,
    baseViewports: { cinematic: pose(), compact: pose() },
    resultViewports: { cinematic: pose(), compact: pose() },
    transientFree: true,
  };
  const beat = {
    v: 1,
    beatId: "beat-projectile",
    componentKind: "projectile_motion",
    componentId: "lesson",
    baseProblemSpec: options.beatBaseProblem,
    resultProblemSpec: options.resultProblem,
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
          id: "lesson__equation",
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 400,
          y: 80,
          text: "v₀ = 20 m/s, θ = 45°",
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
    operationTargets: ["lesson__equation"],
    obligationCodes: [...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS],
    verified: true,
  };
  const choreography = {
    v: 2,
    phase: {
      cues: [{ cue: "enter", targetIds: ["lesson__equation"] }],
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
      beatId: "beat-projectile",
      routedBeatSha256: "b".repeat(64),
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
      baseLowLevelSceneSha256: "c".repeat(64),
      resultLowLevelSceneSha256: "d".repeat(64),
      baseSemanticSceneSha256: "e".repeat(64),
      resultSemanticSceneSha256: "f".repeat(64),
      patchSha256: "0".repeat(64),
      receiptSha256: "1".repeat(64),
      presentationCheckpoint: clone(presentation),
      choreographySha256: "2".repeat(64),
      previousCertificateSha256: baseHead,
    },
    certificateSha256: resultHead,
  };
  return {
    type: "projectile_choreography_scene_checkpoint",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: options.baseRevision,
    resultRevision,
    patch,
    semantic: {
      baseProblemSpec: baseProblem,
      resultProblemSpec: options.resultProblem,
      beat,
      action: options.action,
      checkpointId: options.checkpointId,
      clarificationTopic: options.clarificationTopic,
      baseComponent: options.baseComponent,
      resultComponent: options.resultComponent,
      semanticBaseRevision: options.baseRevision,
      semanticResultRevision: resultRevision,
      semanticBaseCertificateSha256: baseHead,
      semanticResultCertificateSha256: resultHead,
      receipt,
      presentation,
      choreography,
      certificate,
    },
  };
}

function advanceEvent(): JsonRecord {
  const value = problem();
  return checkpointEvent({
    checkpointId: "trace_descent",
    action: "advance",
    route: { intent: "advance", targetStage: "flight" },
    beatBaseProblem: value,
    resultProblem: value,
    baseComponent: component(
      value,
      "apex_state",
      ["apex_acceleration"],
      "apex_acceleration",
    ),
    resultComponent: component(value, "trace_descent", ["apex_acceleration"]),
    baseRevision: 7,
  });
}

function clarificationEvent(): JsonRecord {
  const value = problem();
  return checkpointEvent({
    checkpointId: "horizontal_velocity_detail",
    action: "clarify",
    clarificationTopic: "horizontal_velocity",
    route: { intent: "clarify", topic: "horizontal_velocity" },
    beatBaseProblem: value,
    resultProblem: value,
    baseComponent: component(
      value,
      "trace_descent",
      ["apex_acceleration"],
      "apex_acceleration",
    ),
    resultComponent: component(
      value,
      "trace_descent",
      ["horizontal_velocity", "apex_acceleration"],
      "horizontal_velocity",
    ),
    baseRevision: 8,
  });
}

function retargetEvent(): JsonRecord {
  const baseProblem = problem();
  const resultProblem = problem(30, 60);
  const topics = [
    "horizontal_velocity",
    "apex_acceleration",
    "flight_symmetry",
  ] as const;
  return checkpointEvent({
    checkpointId: "parameters_retargeted",
    action: "retarget",
    route: { intent: "retarget", targetProblemSpec: resultProblem },
    beatBaseProblem: baseProblem,
    resultProblem,
    baseComponent: component(
      baseProblem,
      "summary",
      topics,
      "flight_symmetry",
    ),
    resultComponent: component(
      resultProblem,
      "summary",
      topics,
      "flight_symmetry",
    ),
    baseRevision: 12,
  });
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("projectile choreography V1 stream decoder", () => {
  it("decodes and deeply freezes a fresh V2 setup checkpoint", () => {
    const source = checkpointEvent();
    const decoded = decodeProjectileChoreographySceneStreamEventV1(source);
    expect(decoded).toEqual(source);
    expect(
      parseProjectileChoreographySceneStreamEventV1(JSON.stringify(source)),
    ).toEqual(decoded);
    expect(Object.isFrozen(decoded)).toBe(true);
    if (decoded.type !== "projectile_choreography_scene_checkpoint") return;
    expect(Object.isFrozen(decoded.semantic)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.resultProblemSpec)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.resultComponent)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.receipt.obligationCodes)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.choreography.phase.cues)).toBe(true);
    expect(Object.isFrozen(decoded.patch.operations)).toBe(true);
  });

  it("accepts an advance while preserving the ledger and clearing active detail", () => {
    const decoded = decodeProjectileChoreographySceneCheckpointEventV1(
      advanceEvent(),
    );
    expect(decoded.semantic.checkpointId).toBe("trace_descent");
    expect(decoded.semantic.resultComponent.activeClarification).toBeNull();
  });

  it("accepts a one-shot clarification in canonical ledger order", () => {
    const decoded = decodeProjectileChoreographySceneCheckpointEventV1(
      clarificationEvent(),
    );
    expect(decoded.semantic.resultComponent.clarifiedTopics).toEqual([
      "horizontal_velocity",
      "apex_acceleration",
    ]);
    expect(decoded.semantic.resultComponent.activeClarification).toBe(
      "horizontal_velocity",
    );
  });

  it("accepts a retarget that changes the problem and preserves continuity", () => {
    const decoded = decodeProjectileChoreographySceneCheckpointEventV1(
      retargetEvent(),
    );
    expect(decoded.semantic.resultProblemSpec).toEqual(problem(30, 60));
    expect(decoded.semantic.resultComponent.lastMainCheckpoint).toBe("summary");
    expect(decoded.semantic.resultComponent.activeClarification).toBe(
      "flight_symmetry",
    );
  });

  it.each(Object.keys(PROBLEM_HASHES))(
    "binds the fixed %s problem to its canonical digest",
    (key) => {
      const [speed, angle] = key.split(":").map(Number);
      const value = problem(
        speed as 20 | 25 | 30,
        angle as 30 | 45 | 60,
      );
      expect(
        decodeProjectileChoreographySceneCheckpointEventV1(
          checkpointEvent({
            resultProblem: value,
            resultComponent: component(value, "setup"),
          }),
        ).semantic.resultProblemSpec,
      ).toEqual(value);
    },
  );

  it.each([
    ["routed action", (event: JsonRecord) => {
      nested(event, "semantic").action = "retarget";
    }, /action and clarificationTopic must match routed beat/],
    ["base problem", (event: JsonRecord) => {
      nested(event, "semantic").baseProblemSpec = problem();
    }, /baseProblemSpec must match baseComponent/],
    ["result problem", (event: JsonRecord) => {
      nested(event, "semantic").resultProblemSpec = problem(25, 45);
    }, /resultProblemSpec must match/],
    ["component id", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "resultComponent").id = "other";
    }, /component ids must match/],
    ["component frontier", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "resultComponent").lastMainCheckpoint = null;
    }, /fresh setup advance/],
    ["receipt problem digest", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "receipt").resultProblemSpecSha256 =
        "a".repeat(64);
    }, /receipt problem hashes/],
    ["certificate problem digest", (event: JsonRecord) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      nested(certificate, "body").resultProblemSpecSha256 = "a".repeat(64);
    }, /certificate problem hashes/],
    ["patch identity", (event: JsonRecord) => {
      nested(event, "patch").patchId = "lesson__cp_summary";
    }, /patchId must match/],
    ["patch narration", (event: JsonRecord) => {
      nested(event, "patch").narration = "Different narration.";
    }, /patch narration must match/],
    ["operation target", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "receipt").operationTargets = [
        "lesson__other",
      ];
    }, /operationTargets must match/],
    ["receipt identity", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "receipt").componentId = "other";
    }, /receipt identity/],
    ["certificate identity", (event: JsonRecord) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      nested(certificate, "body").componentId = "other";
    }, /certificate identity/],
    ["certificate presentation", (event: JsonRecord) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      const body = nested(certificate, "body");
      nested(body, "presentationCheckpoint").checkpointNarration =
        "Different narration.";
    }, /presentationCheckpoint must match presentation/],
    ["base chain", (event: JsonRecord) => {
      nested(event, "semantic").semanticBaseCertificateSha256 = "8".repeat(64);
    }, /previous hash must match/],
    ["result chain", (event: JsonRecord) => {
      nested(event, "semantic").semanticResultCertificateSha256 = "8".repeat(64);
    }, /result chain must match/],
    ["event revisions", (event: JsonRecord) => {
      event.resultRevision = 2;
    }, /checkpoint revisions/],
    ["semantic revisions", (event: JsonRecord) => {
      nested(event, "semantic").semanticResultRevision = 2;
    }, /semantic metadata revisions/],
    ["certificate revisions", (event: JsonRecord) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      nested(certificate, "body").resultLowLevelRevision = 2;
    }, /certificate low-level revisions/],
    ["V1 choreography", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "choreography").v = 1;
    }, /choreography plan v must equal 2/],
  ])("rejects a mismatched %s join", (_label, mutate, expected) => {
    const event = checkpointEvent();
    mutate(event);
    expect(() => decodeProjectileChoreographySceneStreamEventV1(event)).toThrow(
      expected,
    );
  });

  it("rejects repeated clarifications and non-canonical result ledgers", () => {
    const repeated = clarificationEvent();
    const semantic = nested(repeated, "semantic");
    const base = nested(semantic, "baseComponent");
    base.clarifiedTopics = ["horizontal_velocity", "apex_acceleration"];
    base.activeClarification = "horizontal_velocity";
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(repeated),
    ).toThrow(/one-shot/);

    const reordered = clarificationEvent();
    nested(nested(reordered, "semantic"), "resultComponent").clarifiedTopics = [
      "apex_acceleration",
      "horizontal_velocity",
    ];
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(reordered),
    ).toThrow(/canonical pedagogical order/);
  });

  it("rejects a retarget that loses frontier or clarification continuity", () => {
    const frontier = retargetEvent();
    nested(nested(frontier, "semantic"), "resultComponent").lastMainCheckpoint =
      "trace_descent";
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(frontier),
    ).toThrow(/retarget must preserve/);

    const active = retargetEvent();
    nested(nested(active, "semantic"), "resultComponent").activeClarification =
      null;
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(active),
    ).toThrow(/retarget must preserve/);
  });

  it("requires the exact complete verifier obligation order", () => {
    const missing = checkpointEvent();
    const receipt = nested(nested(missing, "semantic"), "receipt");
    receipt.obligationCodes = PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS.slice(
      0,
      -1,
    );
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(missing),
    ).toThrow(/complete obligation suite/);

    const reordered = checkpointEvent();
    const reorderedReceipt = nested(
      nested(reordered, "semantic"),
      "receipt",
    );
    reorderedReceipt.obligationCodes = [
      ...PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
    ].reverse();
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1(reordered),
    ).toThrow(/complete obligation suite/);
  });

  it.each([
    ["event", (event: JsonRecord) => {
      event.extra = true;
    }],
    ["semantic metadata", (event: JsonRecord) => {
      nested(event, "semantic").extra = true;
    }],
    ["receipt", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "receipt").extra = true;
    }],
    ["certificate", (event: JsonRecord) => {
      nested(nested(event, "semantic"), "certificate").extra = true;
    }],
    ["certificate body", (event: JsonRecord) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      nested(certificate, "body").extra = true;
    }],
  ])("rejects unknown fields in %s", (_label, mutate) => {
    const event = checkpointEvent();
    mutate(event);
    expect(() => decodeProjectileChoreographySceneStreamEventV1(event)).toThrow(
      /contains unknown field extra/,
    );
  });

  it.each(PROJECTILE_CHOREOGRAPHY_DECLINE_REASONS)(
    "decodes the closed decline reason %s",
    (reasonCode) => {
      expect(
        decodeProjectileChoreographySceneStreamEventV1({
          type: "projectile_choreography_scene_stream_declined",
          generation: 2,
          attempt: 1,
          finalRevision: 4,
          reasonCode,
          message: "No compatible forward mutation was found.",
        }),
      ).toMatchObject({ reasonCode });
    },
  );

  it.each(PROJECTILE_CHOREOGRAPHY_FAILURE_CODES)(
    "decodes failure %s only with its exact retryability",
    (code) => {
      const retryable = (
        PROJECTILE_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES as readonly string[]
      ).includes(code);
      const event = {
        type: "projectile_choreography_scene_stream_failed",
        generation: 2,
        attempt: 1,
        code,
        message: "The visual checkpoint could not be compiled.",
        lastAcceptedRevision: 4,
        retryable,
      };
      expect(
        decodeProjectileChoreographySceneStreamEventV1(event),
      ).toMatchObject({ code, retryable });
      expect(() =>
        decodeProjectileChoreographySceneStreamEventV1({
          ...event,
          retryable: !retryable,
        }),
      ).toThrow(/failed retryable must be/);
    },
  );

  it("strictly decodes the three shared lifecycle events", () => {
    expect(
      decodeProjectileChoreographySceneStreamEventV1({
        type: "scene_stream_started",
        generation: 1,
        attempt: 1,
        baseRevision: 0,
      }).type,
    ).toBe("scene_stream_started");
    expect(
      decodeProjectileChoreographySceneStreamEventV1({
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 0,
        message: "Repairing the stream.",
      }).type,
    ).toBe("scene_stream_repairing");
    expect(
      decodeProjectileChoreographySceneStreamEventV1({
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 1.5,
        totalMs: 2.5,
        repaired: false,
      }).type,
    ).toBe("scene_stream_completed");
  });

  it("enforces lifecycle sequence, attempt, timing, and message budgets", () => {
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1({
        ...checkpointEvent(),
        sequence: MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS + 1,
      }),
    ).toThrow(/checkpoint sequence/);
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1({
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 2,
        toAttempt: 1,
        lastAcceptedRevision: 0,
        message: "Repairing.",
      }),
    ).toThrow(/toAttempt must follow/);
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1({
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 4,
        totalMs: 3,
        repaired: false,
      }),
    ).toThrow(/must not precede/);
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1({
        type: "projectile_choreography_scene_stream_declined",
        generation: 1,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "no_forward_progress",
        message: "x".repeat(513),
      }),
    ).toThrow(/at most 512/);
  });

  it.each([
    "scene_patch",
    "scene_stream_failed",
    "choreography_scene_checkpoint",
    "choreography_scene_stream_declined",
    "parametric_choreography_scene_checkpoint",
    "parametric_choreography_scene_stream_declined",
    "parametric_choreography_scene_stream_failed",
  ])("does not widen into the old %s protocol", (type) => {
    expect(() =>
      decodeProjectileChoreographySceneStreamEventV1({ type }),
    ).toThrow(/event type is unsupported/);
  });

  it("rejects malformed JSON with the protocol error code", () => {
    expect(
      protocolCode(() =>
        parseProjectileChoreographySceneStreamEventV1("{not-json"),
      ),
    ).toBe("invalid_json");
  });

  it("enforces the projectile wire's exact 64 KiB SSE-event budget", () => {
    expect(
      protocolCode(() =>
        parseProjectileChoreographySceneStreamEventV1(
          JSON.stringify({ type: "scene_stream_started", pad: "x".repeat(65_536) }),
        ),
      ),
    ).toBe("budget_exceeded");
  });
});
