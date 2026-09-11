import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
} from "./projectile-choreography-request";

function problem(speedMps = 20, angleDeg = 45): Record<string, unknown> {
  return { v: 1, speedMps, angleDeg };
}

function projectileState(
  problemSpec: Record<string, unknown> = problem(),
): Record<string, unknown> {
  return {
    kind: "projectile_motion",
    id: "projectile",
    problemSpec,
    lastMainCheckpoint: "apex_state",
    clarifiedTopics: ["horizontal_velocity", "apex_acceleration"],
    activeClarification: null,
  };
}

function request(
  routingMode: "reflex" | "director" = "reflex",
): Record<string, unknown> {
  const shared = {
    protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    routingMode,
    problemSpec: problem(),
    generation: 1,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  };
  return routingMode === "reflex"
    ? {
        ...shared,
        requestedRoute: { intent: "advance", targetStage: "flight" },
      }
    : { ...shared, prompt: "  Explain the launch visually.  " };
}

function continuingRequest(): Record<string, unknown> {
  const source = request();
  source.baseScene = { revision: 4, nodes: [] };
  source.baseSemanticScene = {
    revision: 4,
    components: [projectileState()],
    certificateHeadSha256: "a".repeat(64),
  };
  return source;
}

function textNode(id = "projectile__label"): Record<string, unknown> {
  return {
    id,
    kind: "text",
    presentation: { enter: "fade", exit: "fade" },
    x: 400,
    y: 80,
    text: "v₀ = 20 m/s",
    style: {
      color: "hsl(var(--chalk))",
      fontSize: 24,
      opacity: 1,
      anchor: "middle",
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

describe("projectile choreography V1 request decoder", () => {
  it("selects and deeply freezes the exact Reflex request", () => {
    const decoded = decodeProjectileMotionRequestV1(request());

    expect(decoded).toMatchObject({
      protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemSpec: problem(),
      generation: 1,
      requestedRoute: { intent: "advance", targetStage: "flight" },
    });
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.problemSpec)).toBe(true);
    expect(Object.isFrozen(decoded.baseScene)).toBe(true);
    expect(Object.isFrozen(decoded.baseScene.nodes)).toBe(true);
    expect(Object.isFrozen(decoded.baseSemanticScene)).toBe(true);
    expect(Object.isFrozen(decoded.baseSemanticScene.components)).toBe(true);
    if (decoded.routingMode !== "reflex") throw new Error("expected Reflex");
    expect(Object.isFrozen(decoded.requestedRoute)).toBe(true);
  });

  it("selects the Director variant and trims its bounded prompt", () => {
    const decoded = decodeProjectileMotionRequestV1(request("director"));

    expect(decoded).toMatchObject({
      protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
      routingMode: "director",
      problemSpec: problem(),
      prompt: "Explain the launch visually.",
    });
    expect("requestedRoute" in decoded).toBe(false);
  });

  it.each([
    [
      "missing protocol",
      (source: Record<string, unknown>) => delete source.protocol,
      /missing field protocol/,
    ],
    [
      "unknown protocol",
      (source: Record<string, unknown>) => {
        source.protocol = "parametric_choreography_v3";
      },
      /protocol must equal/,
    ],
    [
      "absent discriminator",
      (source: Record<string, unknown>) => delete source.routingMode,
      /routingMode has an unsupported value/,
    ],
    [
      "unknown routing mode",
      (source: Record<string, unknown>) => {
        source.routingMode = "automatic";
      },
      /routingMode has an unsupported value/,
    ],
  ])("rejects %s without another-protocol fallback", (_label, mutate, message) => {
    const source = request();
    mutate(source);
    expect(() => decodeProjectileMotionRequestV1(source)).toThrow(message);
  });

  it("forbids fields from the opposite mode and legacy problem text", () => {
    expect(() =>
      decodeProjectileMotionRequestV1({
        ...request(),
        prompt: "Smuggled Director intent",
      }),
    ).toThrow(/unknown field prompt/);
    expect(() =>
      decodeProjectileMotionRequestV1({
        ...request("director"),
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
    ).toThrow(/unknown field requestedRoute/);
    expect(() =>
      decodeProjectileMotionRequestV1({
        ...request(),
        problemText: "20 metres per second",
      }),
    ).toThrow(/unknown field problemText/);
  });

  it.each([undefined, 8, "", " ", "x".repeat(2_001)])(
    "rejects open Director prompt %p",
    (prompt) => {
      const source = request("director");
      if (prompt === undefined) delete source.prompt;
      else source.prompt = prompt;
      expect(() => decodeProjectileMotionRequestV1(source)).toThrow();
    },
  );

  it.each(["1", true, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "rejects coerced or unsafe generation %p",
    (generation) => {
      expect(() =>
        decodeProjectileMotionRequestV1({ ...request(), generation }),
      ).toThrow();
    },
  );

  it("requires low-level and semantic revisions to match exactly", () => {
    const source = request();
    (source.baseScene as Record<string, unknown>).revision = 2;
    expect(() => decodeProjectileMotionRequestV1(source)).toThrow(
      /revisions must match/,
    );

    const coerced = request();
    (coerced.baseSemanticScene as Record<string, unknown>).revision = "0";
    expect(() => decodeProjectileMotionRequestV1(coerced)).toThrow(
      /safe integer/,
    );
  });

  it("strictly decodes accepted low-level nodes across patch-sized chunks", () => {
    const source = request();
    const nodes = Array.from({ length: 17 }, (_, index) =>
      textNode(`projectile__node_${index}`),
    );
    (source.baseScene as Record<string, unknown>).nodes = nodes;
    const decoded = decodeProjectileMotionRequestV1(source);
    expect(decoded.baseScene.nodes).toHaveLength(17);
    expect(Object.isFrozen(decoded.baseScene.nodes)).toBe(true);

    const open = request();
    (open.baseScene as Record<string, unknown>).nodes = [
      { ...textNode(), rawSvg: "<script />" },
    ];
    expect(() => decodeProjectileMotionRequestV1(open)).toThrow(
      /unknown field rawSvg/,
    );

    const duplicate = request();
    (duplicate.baseScene as Record<string, unknown>).nodes = [
      textNode(),
      textNode(),
    ];
    expect(() => decodeProjectileMotionRequestV1(duplicate)).toThrow(
      /more than once|duplicated/,
    );
  });

  it("uses a stable budget code for an oversized base scene", () => {
    const source = request();
    (source.baseScene as Record<string, unknown>).nodes = Array.from(
      { length: 129 },
      (_, index) => textNode(`node_${index}`),
    );
    expect(protocolCode(() => decodeProjectileMotionRequestV1(source))).toBe(
      "budget_exceeded",
    );
  });

  it("accepts one exact continuing projectile frontier and certificate head", () => {
    const decoded = decodeProjectileMotionRequestV1(continuingRequest());

    expect(decoded.baseSemanticScene.components).toHaveLength(1);
    expect(decoded.baseSemanticScene.components[0].kind).toBe(
      "projectile_motion",
    );
    expect(decoded.baseSemanticScene.certificateHeadSha256).toBe(
      "a".repeat(64),
    );
    expect(Object.isFrozen(decoded.baseSemanticScene.components[0])).toBe(true);
  });

  it("allows an explicit null chain head but rejects malformed digests", () => {
    const source = request();
    source.baseSemanticScene = {
      revision: 0,
      components: [],
      certificateHeadSha256: null,
    };
    expect(
      decodeProjectileMotionRequestV1(source).baseSemanticScene
        .certificateHeadSha256,
    ).toBeNull();

    for (const certificateHeadSha256 of ["a".repeat(63), "A".repeat(64), 1]) {
      const invalid = request();
      invalid.baseSemanticScene = {
        revision: 0,
        components: [],
        certificateHeadSha256,
      };
      expect(() => decodeProjectileMotionRequestV1(invalid)).toThrow(
        /lowercase SHA-256/,
      );
    }
  });

  it("rejects cross-protocol, multiple, open, and problem-spliced semantic bases", () => {
    const crossProtocol = request();
    crossProtocol.baseSemanticScene = {
      revision: 0,
      components: [
        {
          kind: "completing_square_parametric",
          id: "equation",
          problemSpec: { v: 1, linearCoefficient: 8, rightHandSide: 20 },
          lastMainCheckpoint: "problem",
          cornerClarified: false,
        },
      ],
    };
    expect(() => decodeProjectileMotionRequestV1(crossProtocol)).toThrow(
      /projectile motion state/,
    );

    const multiple = continuingRequest();
    (multiple.baseSemanticScene as Record<string, unknown>).components = [
      projectileState(),
      { ...projectileState(), id: "projectile_2" },
    ];
    expect(() => decodeProjectileMotionRequestV1(multiple)).toThrow(
      /at most one/,
    );

    const open = continuingRequest();
    (open.baseSemanticScene as Record<string, unknown>).modelState = {};
    expect(() => decodeProjectileMotionRequestV1(open)).toThrow(
      /unknown field modelState/,
    );

    const changed = continuingRequest();
    changed.problemSpec = problem(30, 60);
    expect(() => decodeProjectileMotionRequestV1(changed)).toThrow(
      /must match the accepted projectile problem/,
    );
  });

  it("permits a retarget only as a route nested under the accepted current problem", () => {
    const source = continuingRequest();
    source.requestedRoute = {
      intent: "retarget",
      targetProblemSpec: problem(30, 60),
    };
    const decoded = decodeProjectileMotionRequestV1(source);
    if (decoded.routingMode !== "reflex") throw new Error("expected Reflex");
    expect(decoded.problemSpec).toEqual(problem());
    expect(decoded.requestedRoute).toEqual({
      intent: "retarget",
      targetProblemSpec: problem(30, 60),
    });
  });

  it("allows only advance routing on a fresh Reflex frontier", () => {
    for (const requestedRoute of [
      { intent: "clarify", topic: "horizontal_velocity" },
      { intent: "retarget", targetProblemSpec: problem(30, 60) },
    ]) {
      expect(() =>
        decodeProjectileMotionRequestV1({ ...request(), requestedRoute }),
      ).toThrow(/fresh projectile request must use an advance route/);
    }
    expect(() =>
      decodeProjectileMotionRequestV1(request("director")),
    ).not.toThrow();
  });
});
