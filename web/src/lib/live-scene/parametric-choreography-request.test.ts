import { describe, expect, it } from "vitest";

import { LiveSceneProtocolError } from "./patch";
import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "./parametric-choreography";
import { decodeParametricChoreographyRequestV3 } from "./parametric-choreography-request";

function request(
  routingMode: "reflex" | "director" = "reflex",
): Record<string, unknown> {
  const shared = {
    protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    routingMode,
    problemText: "  x² + 8x = 20  ",
    generation: 1,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  };
  return routingMode === "reflex"
    ? {
        ...shared,
        requestedRoute: { intent: "advance", targetStage: "complete" },
      }
    : { ...shared, prompt: "  Explain the missing corner.  " };
}

function textNode(id = "lesson__equation"): Record<string, unknown> {
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

function protocolCode(callback: () => unknown): string | undefined {
  try {
    callback();
  } catch (error) {
    return error instanceof LiveSceneProtocolError ? error.code : undefined;
  }
  return undefined;
}

describe("parametric choreography V3 request decoder", () => {
  it("selects and freezes the exact reflex request variant", () => {
    const decoded = decodeParametricChoreographyRequestV3(request());
    expect(decoded).toMatchObject({
      protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
      routingMode: "reflex",
      problemText: "x² + 8x = 20",
      generation: 1,
      requestedRoute: { intent: "advance", targetStage: "complete" },
    });
    expect(Object.isFrozen(decoded)).toBe(true);
    expect(Object.isFrozen(decoded.baseScene)).toBe(true);
    expect(Object.isFrozen(decoded.baseSemanticScene)).toBe(true);
    if (decoded.routingMode !== "reflex") throw new Error("expected reflex request");
    expect(Object.isFrozen(decoded.requestedRoute)).toBe(true);
  });

  it("selects the exact director variant and trims bounded intent", () => {
    const decoded = decodeParametricChoreographyRequestV3(request("director"));
    expect(decoded).toMatchObject({
      protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
      routingMode: "director",
      problemText: "x² + 8x = 20",
      prompt: "Explain the missing corner.",
    });
    expect("requestedRoute" in decoded).toBe(false);
  });

  it.each([
    ["missing protocol", (source: Record<string, unknown>) => {
      delete source.protocol;
    }, /missing field protocol/],
    ["unknown protocol", (source: Record<string, unknown>) => {
      source.protocol = "parametric_choreography_v4";
    }, /protocol must equal/],
    ["legacy absence", (source: Record<string, unknown>) => {
      delete source.protocol;
      delete source.routingMode;
      delete source.requestedRoute;
      source.prompt = "Legacy request";
    }, /routingMode/],
    ["unknown routing mode", (source: Record<string, unknown>) => {
      source.routingMode = "automatic";
    }, /routingMode has an unsupported value/],
  ])("rejects %s instead of falling back to V2", (_label, mutate, expected) => {
    const source = request();
    mutate(source);
    expect(() => decodeParametricChoreographyRequestV3(source)).toThrow(expected);
  });

  it("forbids fields from the opposite routing variant", () => {
    expect(() =>
      decodeParametricChoreographyRequestV3({
        ...request(),
        prompt: "Smuggled model intent",
      }),
    ).toThrow(/unknown field prompt/);
    expect(() =>
      decodeParametricChoreographyRequestV3({
        ...request("director"),
        requestedRoute: { intent: "advance", targetStage: "solve" },
      }),
    ).toThrow(/unknown field requestedRoute/);
  });

  it("accepts null problemText for a continuation but rejects open/coerced text", () => {
    expect(
      decodeParametricChoreographyRequestV3({
        ...request(),
        problemText: null,
      }).problemText,
    ).toBeNull();
    for (const problemText of [undefined, 8, "", " ", "x".repeat(2_001)]) {
      const source = request();
      if (problemText === undefined) delete source.problemText;
      else source.problemText = problemText;
      expect(() => decodeParametricChoreographyRequestV3(source)).toThrow();
    }
  });

  it("rejects strict generation and revision coercions", () => {
    for (const [path, value] of [
      ["generation", "1"],
      ["generation", true],
      ["generation", 0],
    ] as const) {
      const source = request();
      source[path] = value;
      expect(() => decodeParametricChoreographyRequestV3(source)).toThrow();
    }
    const baseRevision = request();
    (baseRevision.baseScene as Record<string, unknown>).revision = "0";
    expect(() => decodeParametricChoreographyRequestV3(baseRevision)).toThrow();
  });

  it("requires low-level and semantic revisions to advance in lockstep", () => {
    const source = request();
    (source.baseScene as Record<string, unknown>).revision = 2;
    expect(() => decodeParametricChoreographyRequestV3(source)).toThrow(
      /revisions must match/,
    );
  });

  it("strictly decodes accepted low-level nodes across the patch-sized boundary", () => {
    const source = request();
    const nodes = Array.from({ length: 17 }, (_, index) =>
      textNode(`lesson__node_${index}`),
    );
    (source.baseScene as Record<string, unknown>).nodes = nodes;
    const decoded = decodeParametricChoreographyRequestV3(source);
    expect(decoded.baseScene.nodes).toHaveLength(17);
    expect(Object.isFrozen(decoded.baseScene.nodes)).toBe(true);

    const openNode = request();
    (openNode.baseScene as Record<string, unknown>).nodes = [
      { ...textNode(), rawSvg: "<script />" },
    ];
    expect(() => decodeParametricChoreographyRequestV3(openNode)).toThrow(
      /unknown field rawSvg/,
    );

    const duplicate = request();
    (duplicate.baseScene as Record<string, unknown>).nodes = [
      textNode(),
      textNode(),
    ];
    expect(() => decodeParametricChoreographyRequestV3(duplicate)).toThrow(
      /more than once|duplicated/,
    );
  });

  it("accepts every backend semantic component variant as unrelated context", () => {
    const source = request();
    source.baseSemanticScene = {
      revision: 0,
      components: [
        {
          kind: "pythagorean_area_identity",
          id: "pythagoras",
          revealedRoles: ["triangle", "square_a"],
        },
        {
          kind: "completing_square",
          id: "legacy",
          lastMainCheckpoint: "missing_corner",
          cornerClarified: true,
        },
        {
          kind: "completing_square_parametric",
          id: "lesson",
          problemSpec: { v: 1, linearCoefficient: 8, rightHandSide: 20 },
          lastMainCheckpoint: "missing_corner",
          cornerClarified: false,
        },
      ],
      certificateHeadSha256: "a".repeat(64),
    };
    const decoded = decodeParametricChoreographyRequestV3(source);
    expect(decoded.baseSemanticScene.components.map(({ kind }) => kind)).toEqual([
      "pythagorean_area_identity",
      "completing_square",
      "completing_square_parametric",
    ]);
    expect(decoded.baseSemanticScene.certificateHeadSha256).toBe("a".repeat(64));
  });

  it("rejects semantic splices, duplicate identities, and unknown fields", () => {
    const v2AsV3 = request();
    v2AsV3.baseSemanticScene = {
      revision: 0,
      components: [
        {
          kind: "completing_square_parametric",
          id: "lesson",
          lastMainCheckpoint: "problem",
          cornerClarified: false,
        },
      ],
    };
    expect(() => decodeParametricChoreographyRequestV3(v2AsV3)).toThrow(
      /missing field problemSpec/,
    );

    const duplicate = request();
    duplicate.baseSemanticScene = {
      revision: 0,
      components: [
        {
          kind: "completing_square",
          id: "lesson",
          lastMainCheckpoint: null,
          cornerClarified: false,
        },
        {
          kind: "completing_square_parametric",
          id: "lesson",
          problemSpec: { v: 1, linearCoefficient: 8, rightHandSide: 20 },
          lastMainCheckpoint: null,
          cornerClarified: false,
        },
      ],
    };
    expect(() => decodeParametricChoreographyRequestV3(duplicate)).toThrow(
      /component id lesson is duplicated/,
    );

    const open = request();
    (open.baseSemanticScene as Record<string, unknown>).modelState = {};
    expect(() => decodeParametricChoreographyRequestV3(open)).toThrow(
      /unknown field modelState/,
    );
  });

  it("uses stable protocol error codes for request-budget violations", () => {
    const source = request();
    (source.baseScene as Record<string, unknown>).nodes = Array.from(
      { length: 129 },
      (_, index) => textNode(`node_${index}`),
    );
    expect(protocolCode(() => decodeParametricChoreographyRequestV3(source))).toBe(
      "budget_exceeded",
    );
  });
});
