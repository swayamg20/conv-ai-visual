import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import {
  MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS,
  MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN,
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  PROJECTILE_STORYBOARD_COMPONENT_ID,
  decodePairedProjectileComparisonSpecV1,
  decodeProjectileStoryboardSemanticSceneStateV1,
  decodeProjectileStoryboardStateV1,
  decodeSemanticStoryboardRecordV1,
  decodeSemanticStoryboardRequestV1,
  storyboardHasForwardCapacity,
} from "./semantic-storyboard";

function problem(
  speedMps = 20,
  anglesDeg: readonly number[] = [30, 60],
): Record<string, unknown> {
  return { v: 1, speedMps, anglesDeg: [...anglesDeg] };
}

function trace(
  trajectoryId: "lower_angle" | "higher_angle",
): Record<string, unknown> {
  return { v: 1, act: "trace", trajectoryId };
}

function reveal(
  conceptId: "range_formula" | "complementary_angles",
): Record<string, unknown> {
  return { v: 1, act: "reveal", conceptId };
}

function relate(
  claimId: "equal_range" | "unequal_range" | "higher_apex" | "longer_flight",
  evidenceIds: readonly string[],
): Record<string, unknown> {
  return { v: 1, act: "relate", claimId, evidenceIds: [...evidenceIds] };
}

function component(
  acceptedRecords: readonly Record<string, unknown>[] = [],
  problemSpec: Record<string, unknown> = problem(),
): Record<string, unknown> {
  return {
    v: 1,
    kind: "projectile_comparison_storyboard",
    id: PROJECTILE_STORYBOARD_COMPONENT_ID,
    problemSpec,
    acceptedRecords: [...acceptedRecords],
  };
}

function semanticScene(
  acceptedRecords: readonly Record<string, unknown>[] = [],
  problemSpec: Record<string, unknown> = problem(),
): Record<string, unknown> {
  return {
    revision: 1 + acceptedRecords.length,
    components: [component(acceptedRecords, problemSpec)],
    certificateHeadSha256: "a".repeat(64),
  };
}

function request(
  routingMode: "reflex" | "director" = "reflex",
): Record<string, unknown> {
  const problemSpec = problem();
  if (routingMode === "reflex") {
    return {
      protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
      routingMode,
      problemSpec,
      generation: 1,
      baseScene: { revision: 0, nodes: [] },
      baseSemanticScene: { revision: 0, components: [] },
    };
  }
  return {
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    routingMode,
    problemSpec,
    generation: 2,
    baseScene: { revision: 1, nodes: [] },
    baseSemanticScene: semanticScene([], problemSpec),
    prompt: "  Compare their ranges visually.  ",
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

describe("Gate 1.8 paired projectile problem", () => {
  it("decodes all nine same-speed, ascending angle pairs and freezes them", () => {
    for (const speedMps of [20, 25, 30]) {
      for (const anglesDeg of [
        [30, 45],
        [30, 60],
        [45, 60],
      ]) {
        const decoded = decodePairedProjectileComparisonSpecV1(
          problem(speedMps, anglesDeg),
        );
        expect(decoded).toEqual({ v: 1, speedMps, anglesDeg });
        expect(Object.isFrozen(decoded)).toBe(true);
        expect(Object.isFrozen(decoded.anglesDeg)).toBe(true);
      }
    }
  });

  it.each([
    { v: true, speedMps: 20, anglesDeg: [30, 60] },
    { v: 1, speedMps: "20", anglesDeg: [30, 60] },
    { v: 1, speedMps: 40, anglesDeg: [30, 60] },
    { v: 1, speedMps: 20, anglesDeg: [30] },
    { v: 1, speedMps: 20, anglesDeg: [30, 30] },
    { v: 1, speedMps: 20, anglesDeg: [60, 30] },
    { v: 1, speedMps: 20, anglesDeg: [30, 90] },
    { v: 1, speedMps: 20, anglesDeg: [30, 60], gravity: 10 },
  ])("rejects unsupported, coerced, unordered, or open problem %j", (value) => {
    expect(() => decodePairedProjectileComparisonSpecV1(value)).toThrow();
  });
});

describe("Gate 1.8 atomic model records", () => {
  it.each([
    reveal("range_formula"),
    trace("lower_angle"),
    relate("equal_range", ["lower_trajectory", "higher_trajectory"]),
    { v: 1, act: "abstain", reasonCode: "unsupported_intent" },
  ])("decodes and freezes the exact minimal record %j", (value) => {
    const decoded = decodeSemanticStoryboardRecordV1(value);
    expect(decoded).toEqual(value);
    expect(Object.isFrozen(decoded)).toBe(true);
    if (decoded.act === "relate") {
      expect(Object.isFrozen(decoded.evidenceIds)).toBe(true);
    }
  });

  it.each([
    { act: "reveal", conceptId: "range_formula" },
    { v: true, act: "trace", trajectoryId: "lower_angle" },
    { v: 1, act: "reveal", conceptId: "full_lesson" },
    { v: 1, act: "trace", trajectoryId: "both_angles" },
    {
      v: 1,
      act: "relate",
      claimId: "compare_all",
      evidenceIds: ["range_formula"],
    },
    relate("equal_range", ["invented_evidence"]),
    relate("equal_range", []),
    relate("equal_range", ["higher_trajectory", "lower_trajectory"]),
    relate("equal_range", ["lower_trajectory", "lower_trajectory"]),
    { v: 1, act: "abstain", reasonCode: "try_later" },
    { ...trace("higher_angle"), durationMs: 900 },
  ])("rejects open, compound, or noncanonical record %j", (value) => {
    expect(() => decodeSemanticStoryboardRecordV1(value)).toThrow();
  });
});

describe("Gate 1.8 ordered semantic frontier", () => {
  it("retains one exact, deeply frozen problem-bound accepted prefix", () => {
    const records = [
      trace("lower_angle"),
      trace("higher_angle"),
      relate("equal_range", ["lower_trajectory", "higher_trajectory"]),
    ];
    const decoded = decodeProjectileStoryboardStateV1(component(records));

    expect(decoded.acceptedRecords).toEqual(records);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.acceptedRecords)).toBe(true);
    expect(Object.isFrozen(decoded.acceptedRecords[0])).toBe(true);
  });

  it("requires evidence to be valid and already visible in the prefix", () => {
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([
          relate("equal_range", ["lower_trajectory", "higher_trajectory"]),
        ]),
      ),
    ).toThrow(/already be visible/);

    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([
          reveal("range_formula"),
          reveal("complementary_angles"),
          relate("higher_apex", ["range_formula", "complementary_angles"]),
        ]),
      ),
    ).toThrow(/not valid for claimId/);
  });

  it("rejects duplicate effects, abstentions, and an open component id", () => {
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([trace("lower_angle"), trace("lower_angle")]),
      ),
    ).toThrow(/repeat a semantic effect/);
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([{ v: 1, act: "abstain", reasonCode: "already_present" }]),
      ),
    ).toThrow(/cannot contain abstain/);
    expect(() =>
      decodeProjectileStoryboardStateV1({
        ...component(),
        id: "model-selected",
      }),
    ).toThrow(/id must equal/);
  });

  it("binds complementary-only nouns and mutually exclusive range claims", () => {
    const nonComplementary = problem(20, [30, 45]);
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([reveal("complementary_angles")], nonComplementary),
      ),
    ).toThrow(/inapplicable/);
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component(
          [
            trace("lower_angle"),
            trace("higher_angle"),
            relate("equal_range", ["lower_trajectory", "higher_trajectory"]),
          ],
          nonComplementary,
        ),
      ),
    ).toThrow(/inapplicable/);
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component(
          [reveal("range_formula"), relate("unequal_range", ["range_formula"])],
          nonComplementary,
        ),
      ),
    ).not.toThrow();
    expect(() =>
      decodeProjectileStoryboardStateV1(
        component([
          reveal("range_formula"),
          relate("unequal_range", ["range_formula"]),
        ]),
      ),
    ).toThrow(/inapplicable/);
  });

  it("enforces the closed ledger cap before decoding an eighth effect", () => {
    expect(MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS).toBe(7);
    const source = component();
    const oversized = Array.from({ length: 8 }, () => trace("lower_angle"));
    Object.defineProperty(oversized, 0, {
      get: () => {
        throw new Error("record decoder must not run beyond the ledger cap");
      },
    });
    source.acceptedRecords = oversized;
    expect(() => decodeProjectileStoryboardStateV1(source)).toThrow(
      /exceeds the closed storyboard catalog/,
    );
  });

  it("mirrors the five-record turn cap and problem-specific frontier capacity", () => {
    expect(MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN).toBe(5);
    const sharedPrefix = [
      trace("lower_angle"),
      trace("higher_angle"),
      reveal("range_formula"),
    ];
    const complementaryProgram = [
      ...sharedPrefix,
      reveal("complementary_angles"),
      relate("equal_range", ["lower_trajectory", "higher_trajectory"]),
      relate("higher_apex", ["lower_trajectory", "higher_trajectory"]),
      relate("longer_flight", ["lower_trajectory", "higher_trajectory"]),
    ];
    const nonComplementaryProgram = [
      ...sharedPrefix,
      relate("unequal_range", ["lower_trajectory", "higher_trajectory"]),
      relate("higher_apex", ["lower_trajectory", "higher_trajectory"]),
      relate("longer_flight", ["lower_trajectory", "higher_trajectory"]),
    ];
    const almostComplementary = decodeProjectileStoryboardStateV1(
      component(complementaryProgram.slice(0, -1)),
    );
    const fullComplementary = decodeProjectileStoryboardStateV1(
      component(complementaryProgram),
    );
    const almostNonComplementary = decodeProjectileStoryboardStateV1(
      component(nonComplementaryProgram.slice(0, -1), problem(20, [30, 45])),
    );
    const fullNonComplementary = decodeProjectileStoryboardStateV1(
      component(nonComplementaryProgram, problem(20, [30, 45])),
    );

    expect(
      storyboardHasForwardCapacity(
        almostComplementary.problemSpec,
        almostComplementary.acceptedRecords,
      ),
    ).toBe(true);
    expect(
      storyboardHasForwardCapacity(
        fullComplementary.problemSpec,
        fullComplementary.acceptedRecords,
      ),
    ).toBe(false);
    expect(fullComplementary.acceptedRecords).toHaveLength(7);
    expect(
      storyboardHasForwardCapacity(
        almostNonComplementary.problemSpec,
        almostNonComplementary.acceptedRecords,
      ),
    ).toBe(true);
    expect(
      storyboardHasForwardCapacity(
        fullNonComplementary.problemSpec,
        fullNonComplementary.acceptedRecords,
      ),
    ).toBe(false);
    expect(fullNonComplementary.acceptedRecords).toHaveLength(6);
  });

  it("runtime-decodes both forward-capacity inputs and rejects open values", () => {
    expect(() =>
      storyboardHasForwardCapacity(problem(20, [60, 30]), []),
    ).toThrow(/distinct and ascending/);
    expect(() =>
      storyboardHasForwardCapacity(problem(), [
        { v: 1, act: "abstain", reasonCode: "already_present" },
      ]),
    ).toThrow(/cannot contain abstain/);
    expect(() =>
      storyboardHasForwardCapacity(problem(), [
        { v: 1, act: "trace", trajectoryId: "invented" },
      ]),
    ).toThrow(/unsupported value/);
    expect(() =>
      storyboardHasForwardCapacity(problem(), "not-an-array"),
    ).toThrow(/must be an array/);
  });

  it("pre-caps forward-capacity inputs before decoding their children", () => {
    const tooManyAngles = [30, 45, 60];
    Object.defineProperty(tooManyAngles, 0, {
      get: () => {
        throw new Error("angle decoder must not run beyond the pair cap");
      },
    });
    expect(() =>
      storyboardHasForwardCapacity(
        { v: 1, speedMps: 20, anglesDeg: tooManyAngles },
        [],
      ),
    ).toThrow(/exactly two angles/);

    const tooManyRecords = Array.from(
      { length: MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS + 1 },
      () => trace("lower_angle"),
    );
    Object.defineProperty(tooManyRecords, 0, {
      get: () => {
        throw new Error("record decoder must not run beyond the ledger cap");
      },
    });
    expect(() =>
      storyboardHasForwardCapacity(problem(), tooManyRecords),
    ).toThrow(/exceeds the closed storyboard catalog/);
  });

  it("enforces the anchor-plus-record revision and certificate head", () => {
    const accepted = [trace("lower_angle")];
    const decoded = decodeProjectileStoryboardSemanticSceneStateV1(
      semanticScene(accepted),
    );
    expect(decoded.revision).toBe(2);
    expect(Object.isFrozen(decoded.components)).toBe(true);

    expect(() =>
      decodeProjectileStoryboardSemanticSceneStateV1({
        ...semanticScene(accepted),
        revision: 3,
      }),
    ).toThrow(/one anchor plus accepted records/);
    expect(() =>
      decodeProjectileStoryboardSemanticSceneStateV1({
        revision: 1,
        components: [],
      }),
    ).toThrow(/uncertified revision 0/);
    expect(() =>
      decodeProjectileStoryboardSemanticSceneStateV1({
        revision: 1,
        components: [component()],
      }),
    ).toThrow(/requires a certificate head/);
    expect(() =>
      decodeProjectileStoryboardSemanticSceneStateV1({
        ...semanticScene(),
        certificateHeadSha256: "A".repeat(64),
      }),
    ).toThrow(/lowercase digest/);
  });
});

describe("Gate 1.8 request encoding", () => {
  it("encodes an exact prompt-free fresh Reflex anchor", () => {
    const decoded = decodeSemanticStoryboardRequestV1(request());

    expect(decoded).toEqual(request());
    expect(decoded.routingMode).toBe("reflex");
    expect("prompt" in decoded).toBe(false);
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.baseScene)).toBe(true);
    expect(Object.isFrozen(decoded.baseSemanticScene)).toBe(true);
  });

  it("encodes Director only from its exact certified frontier", () => {
    const source = request("director");
    const accepted = [trace("lower_angle")];
    source.baseScene = { revision: 2, nodes: [] };
    source.baseSemanticScene = semanticScene(accepted);
    const decoded = decodeSemanticStoryboardRequestV1(source);

    expect(decoded.routingMode).toBe("director");
    if (decoded.routingMode !== "director")
      throw new Error("expected Director");
    expect(decoded.prompt).toBe("Compare their ranges visually.");
    expect(decoded.baseSemanticScene.components[0].acceptedRecords[0]).toEqual(
      trace("lower_angle"),
    );
  });

  it.each(["\u0085", "\ufeff", "\u0085\ufeff"])(
    "rejects the explicit Unicode edge-whitespace-only prompt %j",
    (prompt) => {
      expect(() =>
        decodeSemanticStoryboardRequestV1({
          ...request("director"),
          prompt,
        }),
      ).toThrow(/non-empty string/);
    },
  );

  it("uses the same explicit Unicode prompt-edge normalization", () => {
    const decoded = decodeSemanticStoryboardRequestV1({
      ...request("director"),
      prompt: "\u0085\ufeff  Compare their ranges.  \ufeff\u0085",
    });
    expect(decoded.routingMode).toBe("director");
    if (decoded.routingMode !== "director")
      throw new Error("expected Director");
    expect(decoded.prompt).toBe("Compare their ranges.");
  });

  it.each([
    [
      "legacy protocol",
      () => ({ ...request(), protocol: "projectile_choreography_v1" }),
    ],
    ["Reflex prompt", () => ({ ...request(), prompt: "model call" })],
    [
      "Director route",
      () => ({ ...request("director"), requestedRoute: { intent: "advance" } }),
    ],
    ["unknown mode", () => ({ ...request(), routingMode: "automatic" })],
    ["unknown field", () => ({ ...request(), records: [] })],
  ])("rejects cross-protocol or cross-mode %s", (_label, makeValue) => {
    expect(() => decodeSemanticStoryboardRequestV1(makeValue())).toThrow();
  });

  it("requires Reflex to stay empty and Director to start from an anchor", () => {
    const anchoredReflex = request();
    anchoredReflex.baseScene = { revision: 1, nodes: [] };
    anchoredReflex.baseSemanticScene = semanticScene();
    expect(() => decodeSemanticStoryboardRequestV1(anchoredReflex)).toThrow(
      /anchor requires empty revision 0 scenes/,
    );

    const unanchoredDirector = request("director");
    unanchoredDirector.baseScene = { revision: 0, nodes: [] };
    unanchoredDirector.baseSemanticScene = { revision: 0, components: [] };
    expect(() => decodeSemanticStoryboardRequestV1(unanchoredDirector)).toThrow(
      /requires the certified storyboard anchor/,
    );
  });

  it("rejects mismatched revisions, problems, and unsafe generations", () => {
    const revisionMismatch = request("director");
    revisionMismatch.baseScene = { revision: 2, nodes: [] };
    expect(() => decodeSemanticStoryboardRequestV1(revisionMismatch)).toThrow(
      /revisions must match/,
    );

    const problemMismatch = request("director");
    problemMismatch.problemSpec = problem(20, [30, 45]);
    expect(() => decodeSemanticStoryboardRequestV1(problemMismatch)).toThrow(
      /problemSpec must match/,
    );

    for (const generation of [true, "2", 0, 1.5]) {
      expect(() =>
        decodeSemanticStoryboardRequestV1({ ...request(), generation }),
      ).toThrow(/safe integer/);
    }
  });

  it("preserves low-level node validation and its stable budget error", () => {
    const invalidNode = request();
    invalidNode.baseScene = { revision: 0, nodes: [{ kind: "model" }] };
    expect(() => decodeSemanticStoryboardRequestV1(invalidNode)).toThrow();

    const oversized = request();
    oversized.baseScene = {
      revision: 0,
      nodes: Array.from({ length: 129 }, () => ({})),
    };
    expect(
      protocolCode(() => decodeSemanticStoryboardRequestV1(oversized)),
    ).toBe("budget_exceeded");
  });

  it("rejects non-canonical aliases at every nested request layer", () => {
    const source = request("director");
    source.baseScene = { revision: 2, nodes: [] };
    source.baseSemanticScene = semanticScene([trace("lower_angle")]);

    const rename = (
      path: readonly (string | number)[],
      canonical: string,
      alias: string,
    ): Record<string, unknown> => {
      const payload = structuredClone(source);
      let target: unknown = payload;
      for (const segment of path) {
        target = (target as Record<string | number, unknown>)[segment];
      }
      const object = target as Record<string, unknown>;
      object[alias] = object[canonical];
      delete object[canonical];
      return payload;
    };

    for (const payload of [
      rename([], "routingMode", "routing_mode"),
      rename(["problemSpec"], "speedMps", "speed_mps"),
      rename(["baseScene"], "revision", "base_revision"),
      rename(
        ["baseSemanticScene"],
        "certificateHeadSha256",
        "certificate_head_sha256",
      ),
      rename(
        ["baseSemanticScene", "components", 0],
        "problemSpec",
        "problem_spec",
      ),
      rename(
        ["baseSemanticScene", "components", 0, "acceptedRecords", 0],
        "trajectoryId",
        "trajectory_id",
      ),
    ]) {
      expect(() => decodeSemanticStoryboardRequestV1(payload)).toThrow();
    }
  });
});
