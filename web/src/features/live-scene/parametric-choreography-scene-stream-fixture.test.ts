import { describe, expect, it } from "vitest";

import primaryFixtureValue from "./fixtures/completing-square-parametric-b8-c20.v3.json";
import { PARAMETRIC_CHOREOGRAPHY_PROTOCOL } from "@/lib/live-scene/parametric-choreography";
import {
  decodeParametricChoreographyRequestV3,
  type ParametricChoreographyRequestV3,
} from "@/lib/live-scene/parametric-choreography-request";
import type {
  ParametricChoreographySceneCheckpointEventV3,
  ParametricChoreographySceneStreamEventV3,
} from "@/lib/live-scene/parametric-choreography-stream";

import {
  ParametricChoreographyFixtureError,
  createParametricChoreographyFixtureBatch,
  createParametricChoreographyFixtureRunner,
} from "./parametric-choreography-scene-stream-fixture";
import {
  EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
  prepareParametricChoreographyCheckpoint,
  type ParametricChoreographyFrontier,
} from "./parametric-choreography-playback";

const ADVANCE = Object.freeze({
  intent: "advance" as const,
  targetStage: "solve" as const,
});

function request(
  frontier: ParametricChoreographyFrontier,
  generation: number,
  problemText: string | null,
  requestedRoute: { readonly intent: "clarify_corner" } | typeof ADVANCE,
): ParametricChoreographyRequestV3 {
  return decodeParametricChoreographyRequestV3({
    protocol: PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    routingMode: "reflex",
    problemText,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
    requestedRoute,
  });
}

async function collect(
  runner: ReturnType<typeof createParametricChoreographyFixtureRunner>,
  fixtureRequest: ParametricChoreographyRequestV3,
): Promise<readonly ParametricChoreographySceneStreamEventV3[]> {
  const events: ParametricChoreographySceneStreamEventV3[] = [];
  await runner({
    request: fixtureRequest,
    signal: new AbortController().signal,
    onEvent: (event) => events.push(event),
  });
  return events;
}

function checkpoints(
  events: readonly ParametricChoreographySceneStreamEventV3[],
): readonly ParametricChoreographySceneCheckpointEventV3[] {
  return events.filter(
    (
      event,
    ): event is ParametricChoreographySceneCheckpointEventV3 =>
      event.type === "parametric_choreography_scene_checkpoint",
  );
}

function advanceFrontier(
  base: ParametricChoreographyFrontier,
  events: readonly ParametricChoreographySceneStreamEventV3[],
): ParametricChoreographyFrontier {
  return checkpoints(events).reduce(
    (frontier, event) =>
      prepareParametricChoreographyCheckpoint(
        frontier,
        event,
        "cinematic",
      ).target,
    base,
  );
}

interface MutableFixtureEvent {
  type: string;
  repaired?: boolean;
  patch?: { patchId: string };
}

interface MutableFixtureEnvelope {
  fixtureId: string;
  problemText: string;
  providerRequestCount: number;
  lanes: { main: { events: MutableFixtureEvent[] } };
}

function mutablePrimaryFixture(): MutableFixtureEnvelope {
  return structuredClone(primaryFixtureValue) as unknown as MutableFixtureEnvelope;
}

describe("parametric choreography fixture runner", () => {
  it.each([
    ["x² + 2x = 80", 2, 80],
    ["x² + 8x = 20", 8, 20],
    ["x² + 16x = 17", 16, 17],
  ] as const)(
    "strictly decodes and streams the eight-chapter %s fixture",
    async (problemText, linearCoefficient, rightHandSide) => {
      const fixtureRequest = request(
        EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
        1,
        problemText,
        ADVANCE,
      );
      const batch = createParametricChoreographyFixtureBatch(fixtureRequest);
      expect(batch.holdOpenUntilAbort).toBe(false);
      expect(batch.lane).toBe("main");

      const events = await collect(
        createParametricChoreographyFixtureRunner({
          eventDelayMs: 0,
          chunkDelayMs: 0,
        }),
        fixtureRequest,
      );
      expect(events.map((event) => event.type)).toEqual([
        "scene_stream_started",
        ...Array.from(
          { length: 8 },
          () => "parametric_choreography_scene_checkpoint",
        ),
        "scene_stream_completed",
      ]);
      expect(
        checkpoints(events).map((event) => event.semantic.checkpointId),
      ).toEqual([
        "problem",
        "area_model",
        "split_linear_term",
        "rearrange_halves",
        "missing_corner",
        "balance_and_complete",
        "factor_square",
        "solve_roots",
      ]);
      expect(
        checkpoints(events).map((event) => event.semantic.problemSpec),
      ).toEqual(
        Array.from({ length: 8 }, () => ({
          v: 1,
          linearCoefficient,
          rightHandSide,
        })),
      );
    },
  );

  it("holds the primary stream at missing_corner, then joins clarification and continuation exactly", async () => {
    const runner = createParametricChoreographyFixtureRunner({
      mode: "adaptive",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    const firstRequest = request(
      EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
      1,
      "x² + 8x = 20",
      ADVANCE,
    );
    const firstEvents: ParametricChoreographySceneStreamEventV3[] = [];
    const controller = new AbortController();
    const firstRun = runner({
      request: firstRequest,
      signal: controller.signal,
      onEvent: (event) => {
        firstEvents.push(event);
        if (
          event.type === "parametric_choreography_scene_checkpoint" &&
          event.semantic.checkpointId === "missing_corner"
        ) {
          controller.abort();
        }
      },
    });
    await expect(firstRun).rejects.toMatchObject({ name: "AbortError" });
    expect(
      checkpoints(firstEvents).map((event) => event.semantic.checkpointId),
    ).toEqual([
      "problem",
      "area_model",
      "split_linear_term",
      "rearrange_halves",
      "missing_corner",
    ]);

    const missingCorner = advanceFrontier(
      EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
      firstEvents,
    );
    const clarification = await collect(
      runner,
      request(missingCorner, 2, null, { intent: "clarify_corner" }),
    );
    expect(checkpoints(clarification)).toHaveLength(1);
    expect(checkpoints(clarification)[0].semantic.checkpointId).toBe(
      "corner_detail",
    );

    const clarified = advanceFrontier(missingCorner, clarification);
    const continuation = await collect(
      runner,
      request(clarified, 3, null, ADVANCE),
    );
    expect(
      checkpoints(continuation).map(
        (event) => event.semantic.checkpointId,
      ),
    ).toEqual(["balance_and_complete", "factor_square", "solve_roots"]);
    expect(advanceFrontier(clarified, continuation).scene.revision).toBe(9);
  });

  it("rejects any request outside an exact problem, route, generation, and certified base", () => {
    expect(() =>
      createParametricChoreographyFixtureBatch(
        request(
          EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
          1,
          "x² + 6x = 7",
          ADVANCE,
        ),
      ),
    ).toThrowError(ParametricChoreographyFixtureError);
    expect(() =>
      createParametricChoreographyFixtureBatch(
        request(
          EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
          1,
          "x² + 8x = 20",
          { intent: "clarify_corner" },
        ),
      ),
    ).toThrowError(/exact fixture problem, generation, route, and certified base/);
  });

  it("rejects a malformed generated envelope before creating its runner", () => {
    const malformed = mutablePrimaryFixture();
    malformed.providerRequestCount = 1;
    expect(() =>
      createParametricChoreographyFixtureRunner({
        fixtureValues: [malformed],
      }),
    ).toThrowError(/provider count is invalid/);
  });

  it("binds fixture text to its decoded problem and requires SSE-safe identity", () => {
    const mismatchedProblem = mutablePrimaryFixture();
    mismatchedProblem.problemText = "x² + 2x = 80";
    expect(() =>
      createParametricChoreographyFixtureRunner({
        fixtureValues: [mismatchedProblem],
      }),
    ).toThrowError(/problemText does not match problemSpec/);

    const unsafeId = mutablePrimaryFixture();
    unsafeId.fixtureId = "fixture\nid: forged";
    expect(() =>
      createParametricChoreographyFixtureRunner({ fixtureValues: [unsafeId] }),
    ).toThrowError(/SSE-safe identifier/);

    const inherited = Object.assign(
      Object.create({ inherited: true }) as Record<string, unknown>,
      mutablePrimaryFixture(),
    );
    expect(() =>
      createParametricChoreographyFixtureRunner({ fixtureValues: [inherited] }),
    ).toThrowError(/plain object/);
  });

  it("rejects repaired fixture terminals and duplicate patch identities", () => {
    const repaired = mutablePrimaryFixture();
    const terminal = repaired.lanes.main.events.at(-1);
    expect(terminal?.type).toBe("scene_stream_completed");
    terminal!.repaired = true;
    expect(() =>
      createParametricChoreographyFixtureRunner({ fixtureValues: [repaired] }),
    ).toThrowError(/lifecycle does not match/);

    const duplicatePatch = mutablePrimaryFixture();
    const checkpointEvents = duplicatePatch.lanes.main.events.filter(
      (event) => event.type === "parametric_choreography_scene_checkpoint",
    );
    expect(checkpointEvents).toHaveLength(8);
    checkpointEvents[1].patch!.patchId = checkpointEvents[0].patch!.patchId;
    expect(() =>
      createParametricChoreographyFixtureRunner({
        fixtureValues: [duplicatePatch],
      }),
    ).toThrowError(/deterministic identity|does not join its exact lane/);
  });

  it("validates fixture mode on both batch and stream entry points", () => {
    const fixtureRequest = request(
      EMPTY_PARAMETRIC_CHOREOGRAPHY_FRONTIER,
      1,
      "x² + 8x = 20",
      ADVANCE,
    );
    expect(() =>
      createParametricChoreographyFixtureBatch(fixtureRequest, {
        mode: "unknown" as unknown as "main",
      }),
    ).toThrowError(/fixture mode must be main or adaptive/);
    expect(() =>
      createParametricChoreographyFixtureRunner({
        mode: "unknown" as unknown as "main",
      }),
    ).toThrowError(/fixture mode must be main or adaptive/);
  });
});
