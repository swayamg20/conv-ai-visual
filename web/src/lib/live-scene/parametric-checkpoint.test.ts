import { describe, expect, it } from "vitest";

import { COMPLETING_SQUARE_CHECKPOINT_IDS } from "./checkpoint";
import { LiveSceneProtocolError } from "./patch";
import {
  CHECKPOINT_COMPILER_V3_VERSION,
  CHECKPOINT_VERIFICATION_OBLIGATIONS_V3,
  decodeCompiledCheckpointV3,
  parseCompiledCheckpointV3,
} from "./parametric-checkpoint";

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function pose(): Record<string, unknown> {
  return { v: 1, x: 0, y: 0, width: 800, height: 600 };
}

function presentation(
  checkpointId = "problem",
): Record<string, unknown> {
  return {
    v: 1,
    checkpointId,
    checkpointNarration: "Start with x squared plus eight x equals twenty.",
    baseViewports: { cinematic: pose(), compact: pose() },
    resultViewports: { cinematic: pose(), compact: pose() },
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
    text: "x² + 8x = 20",
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 32,
      opacity: 1,
      anchor: "middle",
    },
  };
}

function problem(): Record<string, unknown> {
  return { v: 1, linearCoefficient: 8, rightHandSide: 20 };
}

function compiledCheckpoint(
  checkpointId = "problem",
): Record<string, unknown> {
  const checkpointPresentation = presentation(checkpointId);
  const nodeId = "lesson__equation";
  return {
    beat: {
      v: 3,
      beatId: "beat-complete",
      componentKind: "completing_square_parametric",
      componentId: "lesson",
      problemSpec: problem(),
      route: { intent: "advance", targetStage: "complete" },
    },
    checkpointId,
    patch: {
      v: 1,
      patchId: `lesson__cp_${checkpointId}`,
      narration: "Start with x squared plus eight x equals twenty.",
      operations: [{ op: "put", node: textNode(nodeId) }],
    },
    receipt: {
      issuer: "completing_square_verifier",
      componentKind: "completing_square_parametric",
      componentId: "lesson",
      problemSpecSha256: "a".repeat(64),
      checkpointId,
      operationTargets: [nodeId],
      obligationCodes: ["stable_id", "problem_identity", "caption_facts"],
      verified: true,
    },
    presentation: checkpointPresentation,
    choreography: {
      v: 1,
      phase: {
        cues: [{ cue: "enter", targetIds: [nodeId] }],
        durationMs: 800,
        easing: "ease_out_quart",
        holdAfterMs: 500,
      },
    },
    certificate: {
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
    },
  };
}

function nested(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
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

describe("parametric checkpoint V3 decoder", () => {
  it("decodes, normalizes, and deeply freezes the closed V3 envelope", () => {
    const source = compiledCheckpoint();
    const decoded = decodeCompiledCheckpointV3(source);
    expect(decoded).toEqual(source);
    expect(parseCompiledCheckpointV3(JSON.stringify(source))).toEqual(decoded);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.beat.problemSpec)).toBe(true);
    expect(Object.isFrozen(decoded.patch.operations)).toBe(true);
    expect(Object.isFrozen(decoded.receipt.operationTargets)).toBe(true);
    expect(Object.isFrozen(decoded.receipt.obligationCodes)).toBe(true);
    expect(Object.isFrozen(decoded.certificate.body)).toBe(true);
  });

  it("treats valid SHA syntax as opaque while joining matching commitments", () => {
    const source = compiledCheckpoint();
    const receipt = nested(source, "receipt");
    const certificate = nested(source, "certificate");
    const body = nested(certificate, "body");
    receipt.problemSpecSha256 = "9".repeat(64);
    body.problemSpecSha256 = "9".repeat(64);
    body.routedBeatSha256 = "8".repeat(64);
    body.patchSha256 = "7".repeat(64);
    certificate.certificateSha256 = "6".repeat(64);

    expect(
      decodeCompiledCheckpointV3(source).certificate.body.routedBeatSha256,
    ).toBe("8".repeat(64));
  });

  it.each([
    ["top-level unknown field", (value: Record<string, unknown>) => {
      value.signature = "open";
    }],
    ["receipt unknown field", (value: Record<string, unknown>) => {
      nested(value, "receipt").confidence = 1;
    }],
    ["certificate body unknown field", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").proof = true;
    }],
    ["missing verified", (value: Record<string, unknown>) => {
      delete nested(value, "receipt").verified;
    }],
  ])("rejects %s", (_label, mutate) => {
    const source = compiledCheckpoint();
    mutate(source);
    expect(() => decodeCompiledCheckpointV3(source)).toThrow();
  });

  it.each([
    ["patch identity", (value: Record<string, unknown>) => {
      nested(value, "patch").patchId = "lesson__cp_area_model";
    }, /patchId must match/],
    ["narration", (value: Record<string, unknown>) => {
      nested(value, "patch").narration = "Different narration.";
    }, /narration must match/],
    ["presentation checkpoint", (value: Record<string, unknown>) => {
      nested(value, "presentation").checkpointId = "area_model";
    }, /presentation checkpointId/],
    ["receipt kind", (value: Record<string, unknown>) => {
      nested(value, "receipt").componentKind = "completing_square";
    }, /componentKind/],
    ["receipt component", (value: Record<string, unknown>) => {
      nested(value, "receipt").componentId = "other";
    }, /receipt componentId/],
    ["receipt checkpoint", (value: Record<string, unknown>) => {
      nested(value, "receipt").checkpointId = "area_model";
    }, /receipt checkpointId/],
    ["problem commitment", (value: Record<string, unknown>) => {
      nested(value, "receipt").problemSpecSha256 = "9".repeat(64);
    }, /problemSpecSha256 commitments/],
    ["certificate beat", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").beatId = "other";
    }, /certificate beatId/],
    ["certificate kind", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").componentKind =
        "completing_square";
    }, /componentKind/],
    ["certificate component", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").componentId = "other";
    }, /certificate componentId/],
    ["certificate checkpoint", (value: Record<string, unknown>) => {
      nested(nested(value, "certificate"), "body").checkpointId = "area_model";
    }, /certificate checkpointId/],
    ["embedded presentation", (value: Record<string, unknown>) => {
      nested(
        nested(nested(value, "certificate"), "body"),
        "presentationCheckpoint",
      ).checkpointNarration = "Different narration.";
    }, /presentationCheckpoint must match/],
  ])("rejects mismatched %s binding", (_label, mutate, expected) => {
    const source = compiledCheckpoint();
    mutate(source);
    expect(() => decodeCompiledCheckpointV3(source)).toThrow(expected);
  });

  it("rejects V2 receipts, certificates, and beats instead of auto-upgrading", () => {
    const v2Beat = compiledCheckpoint();
    const beat = nested(v2Beat, "beat");
    beat.v = 2;
    beat.componentKind = "completing_square";
    delete beat.problemSpec;
    expect(() => decodeCompiledCheckpointV3(v2Beat)).toThrow();

    const v2Receipt = compiledCheckpoint();
    const receipt = nested(v2Receipt, "receipt");
    delete receipt.componentKind;
    delete receipt.problemSpecSha256;
    expect(() => decodeCompiledCheckpointV3(v2Receipt)).toThrow();

    const v2Certificate = compiledCheckpoint();
    const body = nested(nested(v2Certificate, "certificate"), "body");
    body.v = 2;
    body.compilerVersion = "murmur.completing_square_choreography.v1";
    body.componentKind = "completing_square";
    delete body.problemSpecSha256;
    expect(() => decodeCompiledCheckpointV3(v2Certificate)).toThrow();
  });

  it("rejects coercions, malformed hashes, duplicate claims, and revision gaps", () => {
    const coerced = compiledCheckpoint();
    nested(nested(coerced, "certificate"), "body").v = "3";
    expect(() => decodeCompiledCheckpointV3(coerced)).toThrow(/must equal 3/);

    const verified = compiledCheckpoint();
    nested(verified, "receipt").verified = 1;
    expect(() => decodeCompiledCheckpointV3(verified)).toThrow(/must equal true/);

    const digest = compiledCheckpoint();
    nested(digest, "receipt").problemSpecSha256 = "A".repeat(64);
    expect(() => decodeCompiledCheckpointV3(digest)).toThrow(/lowercase SHA-256/);

    const duplicateTarget = compiledCheckpoint();
    nested(duplicateTarget, "receipt").operationTargets = [
      "lesson__equation",
      "lesson__equation",
    ];
    expect(() => decodeCompiledCheckpointV3(duplicateTarget)).toThrow(
      /must be unique/,
    );

    const duplicateObligation = compiledCheckpoint();
    nested(duplicateObligation, "receipt").obligationCodes = [
      "stable_id",
      "stable_id",
    ];
    expect(() => decodeCompiledCheckpointV3(duplicateObligation)).toThrow(
      /must be unique/,
    );

    const revisionGap = compiledCheckpoint();
    nested(nested(revisionGap, "certificate"), "body").resultRevision = 2;
    expect(protocolCode(() => decodeCompiledCheckpointV3(revisionGap))).toBe(
      "revision_mismatch",
    );
  });

  it("accepts every closed checkpoint and verifier obligation", () => {
    for (const checkpointId of COMPLETING_SQUARE_CHECKPOINT_IDS) {
      expect(decodeCompiledCheckpointV3(compiledCheckpoint(checkpointId)).checkpointId).toBe(
        checkpointId,
      );
    }
    const source = compiledCheckpoint();
    nested(source, "receipt").obligationCodes = [
      ...CHECKPOINT_VERIFICATION_OBLIGATIONS_V3,
    ];
    expect(decodeCompiledCheckpointV3(source).receipt.obligationCodes).toEqual(
      CHECKPOINT_VERIFICATION_OBLIGATIONS_V3,
    );

    const unsupported = compiledCheckpoint();
    nested(unsupported, "receipt").obligationCodes = ["looks_correct"];
    expect(() => decodeCompiledCheckpointV3(unsupported)).toThrow(
      /unsupported value/,
    );
  });

  it("rejects malformed JSON with the stable protocol error code", () => {
    expect(protocolCode(() => parseCompiledCheckpointV3("{bad"))).toBe(
      "invalid_json",
    );
  });
});
