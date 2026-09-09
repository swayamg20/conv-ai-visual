import { describe, expect, it } from "vitest";

import { CHECKPOINT_COMPILER_V3_VERSION } from "./parametric-checkpoint";
import {
  PARAMETRIC_CHOREOGRAPHY_DECLINE_REASONS,
  PARAMETRIC_CHOREOGRAPHY_FAILURE_CODES,
  PARAMETRIC_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES,
  decodeParametricChoreographySceneStreamEventV3,
  parseParametricChoreographySceneStreamEventV3,
} from "./parametric-choreography-stream";
import { LiveSceneProtocolError } from "./patch";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function nested(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  return value[key] as Record<string, unknown>;
}

function problem(
  linearCoefficient = 8,
  rightHandSide = 20,
): Record<string, unknown> {
  return { v: 1, linearCoefficient, rightHandSide };
}

function pose(): Record<string, unknown> {
  return { v: 1, x: 0, y: 0, width: 800, height: 600 };
}

function checkpointEvent(
  checkpointId = "problem",
): Record<string, unknown> {
  const checkpointPresentation = {
    v: 1,
    checkpointId,
    checkpointNarration: "Start with x squared plus eight x equals twenty.",
    baseViewports: { cinematic: pose(), compact: pose() },
    resultViewports: { cinematic: pose(), compact: pose() },
    transientFree: true,
  };
  const beat = {
    v: 3,
    beatId: "beat-complete",
    componentKind: "completing_square_parametric",
    componentId: "lesson",
    problemSpec: problem(),
    route: { intent: "advance", targetStage: "complete" },
  };
  const patch = {
    v: 1,
    patchId: `lesson__cp_${checkpointId}`,
    narration: "Start with x squared plus eight x equals twenty.",
    operations: [
      {
        op: "put",
        node: {
          id: "lesson__equation",
          kind: "text",
          presentation: { enter: "fade", exit: "fade" },
          x: 400,
          y: 80,
          text: "x² + 8x = 20",
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
    issuer: "completing_square_verifier",
    componentKind: "completing_square_parametric",
    componentId: "lesson",
    problemSpecSha256: "a".repeat(64),
    checkpointId,
    operationTargets: ["lesson__equation"],
    obligationCodes: ["stable_id", "problem_identity"],
    verified: true,
  };
  const choreography = {
    v: 1,
    phase: {
      cues: [{ cue: "enter", targetIds: ["lesson__equation"] }],
      durationMs: 800,
      easing: "ease_out_quart",
      holdAfterMs: 500,
    },
  };
  const certificate = {
    body: {
      v: 3,
      issuer: "semantic_compiler",
      compilerVersion: CHECKPOINT_COMPILER_V3_VERSION,
      canonicalization: "murmur-json-v1",
      hashAlgorithm: "sha256",
      beatId: "beat-complete",
      routedBeatSha256: "b".repeat(64),
      componentKind: "completing_square_parametric",
      componentId: "lesson",
      problemSpecSha256: "a".repeat(64),
      checkpointId,
      baseRevision: 0,
      resultRevision: 1,
      baseLowLevelSceneSha256: "c".repeat(64),
      resultLowLevelSceneSha256: "d".repeat(64),
      baseSemanticSceneSha256: "e".repeat(64),
      resultSemanticSceneSha256: "f".repeat(64),
      patchSha256: "0".repeat(64),
      receiptSha256: "1".repeat(64),
      presentationCheckpoint: clone(checkpointPresentation),
      choreographySha256: "2".repeat(64),
      previousCertificateSha256: null,
    },
    certificateSha256: "3".repeat(64),
  };
  return {
    type: "parametric_choreography_scene_checkpoint",
    generation: 1,
    attempt: 1,
    sequence: 1,
    baseRevision: 0,
    resultRevision: 1,
    patch,
    semantic: {
      problemSpec: problem(),
      beat,
      checkpointId,
      resultComponent: {
        kind: "completing_square_parametric",
        id: "lesson",
        problemSpec: problem(),
        lastMainCheckpoint:
          checkpointId === "corner_detail" ? "missing_corner" : checkpointId,
        cornerClarified: checkpointId === "corner_detail",
      },
      semanticBaseRevision: 0,
      semanticResultRevision: 1,
      semanticBaseCertificateSha256: null,
      semanticResultCertificateSha256: "3".repeat(64),
      receipt,
      presentation: checkpointPresentation,
      choreography,
      certificate,
    },
  };
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("parametric choreography V3 stream decoder", () => {
  it("decodes a problem-bound checkpoint and freezes every decoded claim", () => {
    const source = checkpointEvent();
    const decoded = decodeParametricChoreographySceneStreamEventV3(source);
    expect(decoded).toEqual(source);
    expect(parseParametricChoreographySceneStreamEventV3(JSON.stringify(source))).toEqual(
      decoded,
    );
    expect(Object.isFrozen(decoded)).toBe(true);
    if (decoded.type !== "parametric_choreography_scene_checkpoint") return;
    expect(Object.isFrozen(decoded.semantic)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.problemSpec)).toBe(true);
    expect(Object.isFrozen(decoded.semantic.resultComponent)).toBe(true);
    expect(Object.isFrozen(decoded.patch.operations)).toBe(true);
  });

  it.each([
    ["semantic problem", (event: Record<string, unknown>) => {
      nested(nested(event, "semantic"), "problemSpec").linearCoefficient = 6;
      nested(nested(event, "semantic"), "problemSpec").rightHandSide = 7;
    }, /problemSpec must match routed beat/],
    ["component problem", (event: Record<string, unknown>) => {
      const component = nested(nested(event, "semantic"), "resultComponent");
      component.problemSpec = problem(6, 7);
    }, /resultComponent problemSpec must match/],
    ["component id", (event: Record<string, unknown>) => {
      nested(nested(event, "semantic"), "resultComponent").id = "other";
    }, /resultComponent id must match/],
    ["main frontier", (event: Record<string, unknown>) => {
      nested(nested(event, "semantic"), "resultComponent").lastMainCheckpoint =
        "area_model";
    }, /main checkpoint must match/],
    ["certificate component", (event: Record<string, unknown>) => {
      const certificate = nested(nested(event, "semantic"), "certificate");
      nested(certificate, "body").componentId = "other";
    }, /certificate componentId/],
  ])("rejects a mismatched %s join", (_label, mutate, expected) => {
    const event = checkpointEvent();
    mutate(event);
    expect(() => decodeParametricChoreographySceneStreamEventV3(event)).toThrow(
      expected,
    );
  });

  it("accepts the one corner-detail detour and rejects every other frontier", () => {
    expect(
      decodeParametricChoreographySceneStreamEventV3(
        checkpointEvent("corner_detail"),
      ).type,
    ).toBe("parametric_choreography_scene_checkpoint");

    const notClarified = checkpointEvent("corner_detail");
    nested(nested(notClarified, "semantic"), "resultComponent").cornerClarified =
      false;
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3(notClarified),
    ).toThrow(/clarified missing_corner frontier/);
  });

  it.each([
    ["event revision gap", (event: Record<string, unknown>) => {
      event.resultRevision = 2;
    }, /checkpoint revisions/],
    ["semantic revision gap", (event: Record<string, unknown>) => {
      nested(event, "semantic").semanticResultRevision = 2;
    }, /semantic metadata revisions/],
    ["semantic/low-level drift", (event: Record<string, unknown>) => {
      event.baseRevision = 1;
      event.resultRevision = 2;
    }, /semantic and low-level/],
    ["base chain mismatch", (event: Record<string, unknown>) => {
      nested(event, "semantic").semanticBaseCertificateSha256 = "9".repeat(64);
    }, /previousCertificateSha256/],
    ["result chain mismatch", (event: Record<string, unknown>) => {
      nested(event, "semantic").semanticResultCertificateSha256 = "9".repeat(64);
    }, /result chain head/],
  ])("rejects %s", (_label, mutate, expected) => {
    const event = checkpointEvent();
    mutate(event);
    expect(() => decodeParametricChoreographySceneStreamEventV3(event)).toThrow(
      expected,
    );
  });

  it("accepts a non-null prior head only when all chain fields join", () => {
    const event = checkpointEvent();
    const semantic = nested(event, "semantic");
    const certificate = nested(semantic, "certificate");
    const previous = "9".repeat(64);
    semantic.semanticBaseCertificateSha256 = previous;
    nested(certificate, "body").previousCertificateSha256 = previous;
    expect(
      decodeParametricChoreographySceneStreamEventV3(event).type,
    ).toBe("parametric_choreography_scene_checkpoint");
  });

  it("rejects unknown fields and strict integer/boolean coercions", () => {
    const event = checkpointEvent();
    event.protocol = "parametric_choreography_v3";
    expect(() => decodeParametricChoreographySceneStreamEventV3(event)).toThrow(
      /unknown field protocol/,
    );

    for (const [field, value] of [
      ["generation", "1"],
      ["attempt", true],
      ["sequence", 1.5],
      ["baseRevision", -1],
    ] as const) {
      const invalid = checkpointEvent();
      invalid[field] = value;
      expect(() => decodeParametricChoreographySceneStreamEventV3(invalid)).toThrow();
    }

    const verified = checkpointEvent();
    nested(nested(verified, "semantic"), "receipt").verified = 1;
    expect(() => decodeParametricChoreographySceneStreamEventV3(verified)).toThrow(
      /verified must equal true/,
    );
  });

  it("rejects V2 checkpoint and terminal discriminators without fallback", () => {
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...checkpointEvent(),
        type: "choreography_scene_checkpoint",
      }),
    ).toThrow(/event type is unsupported/);
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        type: "choreography_scene_stream_declined",
        generation: 1,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "no_forward_progress",
        message: "Already visible.",
      }),
    ).toThrow(/event type is unsupported/);
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        type: "scene_stream_failed",
        generation: 1,
        attempt: 1,
        code: "provider_error",
        message: "Unavailable.",
        lastAcceptedRevision: 0,
        retryable: true,
      }),
    ).toThrow(/event type is unsupported/);
  });

  it.each(PARAMETRIC_CHOREOGRAPHY_DECLINE_REASONS)(
    "accepts the closed %s decline reason",
    (reasonCode) => {
      const source = {
        type: "parametric_choreography_scene_stream_declined",
        generation: 2,
        attempt: 1,
        finalRevision: 4,
        reasonCode,
        message: "The board remains unchanged.",
      };
      expect(decodeParametricChoreographySceneStreamEventV3(source)).toEqual(
        source,
      );
    },
  );

  it("rejects open decline reasons", () => {
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        type: "parametric_choreography_scene_stream_declined",
        generation: 1,
        attempt: 1,
        finalRevision: 0,
        reasonCode: "model_decides_later",
        message: "No.",
      }),
    ).toThrow(/reasonCode has an unsupported value/);
  });

  it.each(PARAMETRIC_CHOREOGRAPHY_FAILURE_CODES)(
    "binds retryability for the closed %s failure",
    (code) => {
      const retryable = (
        PARAMETRIC_CHOREOGRAPHY_RETRYABLE_FAILURE_CODES as readonly string[]
      ).includes(code);
      const source = {
        type: "parametric_choreography_scene_stream_failed",
        generation: 1,
        attempt: 1,
        code,
        message: "The visual lesson could not continue.",
        lastAcceptedRevision: 3,
        retryable,
      };
      expect(decodeParametricChoreographySceneStreamEventV3(source)).toEqual(
        source,
      );
      expect(() =>
        decodeParametricChoreographySceneStreamEventV3({
          ...source,
          retryable: !retryable,
        }),
      ).toThrow(/failed retryable must be/);
    },
  );

  it("rejects open failure codes and non-boolean retryability", () => {
    const base = {
      type: "parametric_choreography_scene_stream_failed",
      generation: 1,
      attempt: 1,
      message: "Unavailable.",
      lastAcceptedRevision: 0,
      retryable: false,
    };
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...base,
        code: "invented_failure",
      }),
    ).toThrow(/failed code has an unsupported value/);
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...base,
        code: "choreography_integrity_error",
        retryable: 0,
      }),
    ).toThrow(/must be a boolean/);
  });

  it("strictly decodes the shared lifecycle shapes", () => {
    const lifecycle = [
      {
        type: "scene_stream_started",
        generation: 1,
        attempt: 1,
        baseRevision: 0,
      },
      {
        type: "scene_stream_repairing",
        generation: 1,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: 0,
        message: "Retrying once.",
      },
      {
        type: "scene_stream_completed",
        generation: 1,
        finalRevision: 1,
        patchCount: 1,
        firstPatchMs: 10,
        totalMs: 20,
        repaired: false,
      },
    ];
    expect(lifecycle.map(decodeParametricChoreographySceneStreamEventV3)).toEqual(
      lifecycle,
    );

    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...lifecycle[1],
        fromAttempt: 2,
      }),
    ).toThrow(/toAttempt must follow/);
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...lifecycle[2],
        repaired: 0,
      }),
    ).toThrow(/repaired must be a boolean/);
    expect(() =>
      decodeParametricChoreographySceneStreamEventV3({
        ...lifecycle[2],
        firstPatchMs: 21,
      }),
    ).toThrow(/totalMs must not precede/);
  });

  it("reports malformed JSON with the stable protocol code", () => {
    expect(
      protocolCode(() =>
        parseParametricChoreographySceneStreamEventV3("{not-json"),
      ),
    ).toBe("invalid_json");
  });
});
