import { describe, expect, it } from "vitest";

import {
  CHECKPOINT_COMPILER_VERSION,
  CHECKPOINT_VERIFICATION_OBLIGATIONS,
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  decodeCompiledCheckpointV2,
  parseCompiledCheckpointV2,
} from "./checkpoint";
import { LIVE_SCENE_MAX_PATCH_OPERATIONS, LiveSceneProtocolError } from "./patch";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function pose(
  overrides: Partial<{ x: number; y: number; width: number; height: number }> = {},
): Record<string, unknown> {
  return { v: 1, x: 0, y: 0, width: 800, height: 600, ...overrides };
}

function presentation(
  checkpointId = "problem",
  checkpointNarration = "Start with the equation x squared plus six x equals seven.",
): Record<string, unknown> {
  return {
    v: 1,
    checkpointId,
    checkpointNarration,
    baseViewports: {
      cinematic: pose(),
      compact: pose({ x: 100, width: 600 }),
    },
    resultViewports: {
      cinematic: pose({ x: 80, y: 75, width: 640, height: 450 }),
      compact: pose({ x: 100, width: 600 }),
    },
    transientFree: true,
  };
}

function textNode(id: string): Record<string, unknown> {
  return {
    id,
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x: 400,
    y: 80,
    text: "x² + 6x = 7",
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 32,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function compiledCheckpoint(): Record<string, unknown> {
  const checkpointPresentation = presentation();
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
      narration: "Start with the equation x squared plus six x equals seven.",
      operations: [{ op: "put", node: textNode("lesson__equation") }],
    },
    receipt: {
      issuer: "completing_square_verifier",
      componentId: "lesson",
      checkpointId: "problem",
      operationTargets: ["lesson__equation"],
      obligationCodes: ["stable_id", "equation_identity"],
      verified: true,
    },
    presentation: checkpointPresentation,
    choreography: {
      v: 1,
      phase: {
        cues: [{ cue: "enter", targetIds: ["lesson__equation"] }],
        durationMs: 800,
        easing: "ease_out_quart",
        holdAfterMs: 500,
      },
    },
    certificate: {
      body: {
        v: 2,
        issuer: "semantic_compiler",
        compilerVersion: CHECKPOINT_COMPILER_VERSION,
        canonicalization: "murmur-json-v1",
        hashAlgorithm: "sha256",
        beatId: "beat-problem",
        routedBeatSha256: "a".repeat(64),
        componentKind: "completing_square",
        componentId: "lesson",
        checkpointId: "problem",
        baseRevision: 0,
        resultRevision: 1,
        baseLowLevelSceneSha256: "b".repeat(64),
        resultLowLevelSceneSha256: "c".repeat(64),
        baseSemanticSceneSha256: "d".repeat(64),
        resultSemanticSceneSha256: "e".repeat(64),
        patchSha256: "f".repeat(64),
        receiptSha256: "0".repeat(64),
        presentationCheckpoint: clone(checkpointPresentation),
        choreographySha256: "1".repeat(64),
        previousCertificateSha256: null,
      },
      certificateSha256: "2".repeat(64),
    },
  };
}

function nested(value: Record<string, unknown>, key: string): Record<string, unknown> {
  return value[key] as Record<string, unknown>;
}

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("compiled checkpoint V2 decoder", () => {
  it("decodes, normalizes, and deeply freezes the closed checkpoint envelope", () => {
    const source = compiledCheckpoint();
    const decoded = decodeCompiledCheckpointV2(source);

    expect(decoded).toEqual(source);
    expect(parseCompiledCheckpointV2(JSON.stringify(source))).toEqual(decoded);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.beat)).toBe(true);
    expect(Object.isFrozen(decoded.patch.operations)).toBe(true);
    expect(Object.isFrozen(decoded.receipt.operationTargets)).toBe(true);
    expect(Object.isFrozen(decoded.receipt.obligationCodes)).toBe(true);
    expect(Object.isFrozen(decoded.certificate.body.presentationCheckpoint)).toBe(true);
  });

  it("treats syntactically valid digests as opaque backend-issued commitments", () => {
    const source = compiledCheckpoint();
    const body = nested(nested(source, "certificate"), "body");
    body.routedBeatSha256 = "9".repeat(64);
    body.patchSha256 = "8".repeat(64);
    nested(source, "certificate").certificateSha256 = "7".repeat(64);

    const decoded = decodeCompiledCheckpointV2(source);
    expect(decoded.certificate.body.routedBeatSha256).toBe("9".repeat(64));
  });

  it.each([
    ["short", "abc"],
    ["uppercase", "A".repeat(64)],
    ["non-hex", "g".repeat(64)],
  ])("rejects %s SHA-256 syntax", (_label, digest) => {
    const source = compiledCheckpoint();
    nested(nested(source, "certificate"), "body").patchSha256 = digest;
    expect(() => decodeCompiledCheckpointV2(source)).toThrow(/lowercase SHA-256/);
  });

  it("rejects unknown or missing keys at every envelope boundary", () => {
    const topLevel = compiledCheckpoint();
    topLevel.signature = "open";
    expect(() => decodeCompiledCheckpointV2(topLevel)).toThrow("unknown field signature");

    const body = compiledCheckpoint();
    nested(nested(body, "certificate"), "body").clock = 1;
    expect(() => decodeCompiledCheckpointV2(body)).toThrow("unknown field clock");

    const receipt = compiledCheckpoint();
    delete nested(receipt, "receipt").verified;
    expect(() => decodeCompiledCheckpointV2(receipt)).toThrow("missing field verified");
  });

  it.each([
    ["patch identity", (value: Record<string, unknown>) => {
      nested(value, "patch").patchId = "lesson__cp_area_model";
    }, /patchId must match/],
    ["narration", (value: Record<string, unknown>) => {
      nested(value, "patch").narration = "A different caption.";
    }, /narration must match/],
    ["presentation checkpoint", (value: Record<string, unknown>) => {
      nested(value, "presentation").checkpointId = "area_model";
    }, /presentation checkpointId/],
    ["receipt component", (value: Record<string, unknown>) => {
      nested(value, "receipt").componentId = "other";
    }, /receipt componentId/],
    ["receipt checkpoint", (value: Record<string, unknown>) => {
      nested(value, "receipt").checkpointId = "area_model";
    }, /receipt checkpointId/],
    ["certificate beat", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").beatId = "other-beat";
    }, /certificate beatId/],
    ["certificate component", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").componentId = "other";
    }, /certificate componentId/],
    ["certificate checkpoint", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").checkpointId = "area_model";
    }, /certificate checkpointId/],
  ])("rejects a mismatched %s binding", (_label, mutate, expected) => {
    const source = compiledCheckpoint();
    mutate(source);
    expect(() => decodeCompiledCheckpointV2(source)).toThrow(expected);
  });

  it("requires receipt targets to equal patch targets in exact operation order", () => {
    const source = compiledCheckpoint();
    nested(source, "patch").operations = [
      { op: "put", node: textNode("lesson__a") },
      { op: "put", node: textNode("lesson__b") },
    ];
    nested(source, "receipt").operationTargets = ["lesson__b", "lesson__a"];

    expect(() => decodeCompiledCheckpointV2(source)).toThrow(/ordered patch targets/);
  });

  it("requires the embedded presentation to equal the presented checkpoint", () => {
    const source = compiledCheckpoint();
    const body = nested(nested(source, "certificate"), "body");
    nested(body, "presentationCheckpoint").checkpointNarration = "Different narration.";

    expect(() => decodeCompiledCheckpointV2(source)).toThrow(
      /presentationCheckpoint must match presentation/,
    );
  });

  it("requires certificate revisions to advance exactly once", () => {
    const source = compiledCheckpoint();
    nested(nested(source, "certificate"), "body").resultRevision = 2;

    expect(protocolCode(() => decodeCompiledCheckpointV2(source))).toBe(
      "revision_mismatch",
    );
  });

  it("accepts exactly the 1..16 operation target budget and rejects overflow", () => {
    const maximum = compiledCheckpoint();
    const targets = Array.from(
      { length: LIVE_SCENE_MAX_PATCH_OPERATIONS },
      (_, index) => `lesson__node_${index.toString().padStart(2, "0")}`,
    );
    nested(maximum, "patch").operations = targets.map((target) => ({
      op: "put",
      node: textNode(target),
    }));
    nested(maximum, "receipt").operationTargets = targets;
    expect(decodeCompiledCheckpointV2(maximum).receipt.operationTargets).toHaveLength(16);

    const overflow = compiledCheckpoint();
    const tooMany = [...targets, "lesson__node_16"];
    nested(overflow, "patch").operations = tooMany.map((target) => ({
      op: "put",
      node: textNode(target),
    }));
    nested(overflow, "receipt").operationTargets = tooMany;
    expect(protocolCode(() => decodeCompiledCheckpointV2(overflow))).toBe(
      "budget_exceeded",
    );
  });

  it("accepts each closed checkpoint and obligation and rejects open values", () => {
    for (const checkpointId of COMPLETING_SQUARE_CHECKPOINT_IDS) {
      const source = compiledCheckpoint();
      source.checkpointId = checkpointId;
      nested(source, "patch").patchId = `lesson__cp_${checkpointId}`;
      nested(source, "receipt").checkpointId = checkpointId;
      nested(source, "presentation").checkpointId = checkpointId;
      const body = nested(nested(source, "certificate"), "body");
      body.checkpointId = checkpointId;
      nested(body, "presentationCheckpoint").checkpointId = checkpointId;
      expect(decodeCompiledCheckpointV2(source).checkpointId).toBe(checkpointId);
    }

    const obligations = compiledCheckpoint();
    nested(obligations, "receipt").obligationCodes = [
      ...CHECKPOINT_VERIFICATION_OBLIGATIONS,
    ];
    expect(decodeCompiledCheckpointV2(obligations).receipt.obligationCodes).toEqual(
      CHECKPOINT_VERIFICATION_OBLIGATIONS,
    );

    const unsupportedCheckpoint = compiledCheckpoint();
    unsupportedCheckpoint.checkpointId = "draw_anything";
    expect(() => decodeCompiledCheckpointV2(unsupportedCheckpoint)).toThrow(
      /unsupported value/,
    );

    const unsupportedObligation = compiledCheckpoint();
    nested(unsupportedObligation, "receipt").obligationCodes = ["looks_good"];
    expect(() => decodeCompiledCheckpointV2(unsupportedObligation)).toThrow(
      /unsupported value/,
    );

    const duplicateObligation = compiledCheckpoint();
    nested(duplicateObligation, "receipt").obligationCodes = [
      "stable_id",
      "stable_id",
    ];
    expect(() => decodeCompiledCheckpointV2(duplicateObligation)).toThrow(/must be unique/);
  });

  it("rejects malformed JSON without weakening protocol validation", () => {
    expect(protocolCode(() => parseCompiledCheckpointV2("{not-json"))).toBe(
      "invalid_json",
    );
  });
});
