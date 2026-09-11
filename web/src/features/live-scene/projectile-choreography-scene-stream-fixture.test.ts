import { describe, expect, it } from "vitest";

import primaryFixtureValue from "./fixtures/projectile-motion-v1/projectile-motion-v20-a45.v1.json";
import {
  PROJECTILE_CHOREOGRAPHY_PROTOCOL,
  decodeProjectileMotionRequestV1,
  type ProjectileMotionRequestV1,
} from "@/lib/live-scene/projectile-choreography-request";
import type {
  ProjectileChoreographySceneCheckpointEventV1,
  ProjectileChoreographySceneStreamEventV1,
} from "@/lib/live-scene/projectile-choreography-stream";
import {
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionRouteV1,
} from "@/lib/live-scene/projectile-motion";

import {
  ProjectileChoreographyFixtureError,
  createProjectileChoreographyFixtureBatch,
  createProjectileChoreographyFixtureRunner,
} from "./projectile-choreography-scene-stream-fixture";
import {
  EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
  prepareProjectileChoreographyCheckpoint,
  type ProjectileChoreographyFrontier,
} from "./projectile-choreography-playback";

const PRIMARY_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 45,
} as const satisfies ProjectileMotionProblemSpecV1);
const RETARGET_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  angleDeg: 60,
} as const satisfies ProjectileMotionProblemSpecV1);
const ADVANCE = Object.freeze({
  intent: "advance",
  targetStage: "solve",
} as const satisfies ProjectileMotionRouteV1);

function request(
  frontier: ProjectileChoreographyFrontier,
  generation: number,
  problemSpec: ProjectileMotionProblemSpecV1,
  requestedRoute: ProjectileMotionRouteV1,
): ProjectileMotionRequestV1 {
  return decodeProjectileMotionRequestV1({
    protocol: PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    routingMode: "reflex",
    problemSpec,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
    requestedRoute,
  });
}

function checkpoints(
  events: readonly ProjectileChoreographySceneStreamEventV1[],
): readonly ProjectileChoreographySceneCheckpointEventV1[] {
  return events.filter(
    (event): event is ProjectileChoreographySceneCheckpointEventV1 =>
      event.type === "projectile_choreography_scene_checkpoint",
  );
}

function advanceFrontier(
  base: ProjectileChoreographyFrontier,
  events: readonly ProjectileChoreographySceneStreamEventV1[],
  count = checkpoints(events).length,
): ProjectileChoreographyFrontier {
  return checkpoints(events)
    .slice(0, count)
    .reduce(
      (frontier, event) =>
        prepareProjectileChoreographyCheckpoint(frontier, event, "cinematic")
          .target,
      base,
    );
}

async function collect(
  runner: ReturnType<typeof createProjectileChoreographyFixtureRunner>,
  fixtureRequest: ProjectileMotionRequestV1,
): Promise<readonly ProjectileChoreographySceneStreamEventV1[]> {
  const events: ProjectileChoreographySceneStreamEventV1[] = [];
  await runner({
    request: fixtureRequest,
    signal: new AbortController().signal,
    onEvent: (event) => events.push(event),
  });
  return events;
}

interface MutableFixtureEnvelope {
  providerRequestCount: number;
  lanes: {
    main: {
      events: Array<{ type: string; repaired?: boolean }>;
    };
  };
}

function mutablePrimaryFixture(): MutableFixtureEnvelope {
  return structuredClone(
    primaryFixtureValue,
  ) as unknown as MutableFixtureEnvelope;
}

describe("projectile choreography scene-stream fixture", () => {
  it.each([
    [20, 30],
    [20, 45],
    [20, 60],
    [30, 45],
    [30, 60],
  ] as const)(
    "streams the real six-checkpoint %i m/s at %i degree lifecycle",
    async (speedMps, angleDeg) => {
      const problemSpec = { v: 1, speedMps, angleDeg } as const;
      const fixtureRequest = request(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        1,
        problemSpec,
        ADVANCE,
      );
      const batch = createProjectileChoreographyFixtureBatch(fixtureRequest);
      expect(batch.lane).toBe("main");
      expect(batch.holdOpenUntilAbort).toBe(false);

      const events = await collect(
        createProjectileChoreographyFixtureRunner({
          eventDelayMs: 0,
          chunkDelayMs: 0,
        }),
        fixtureRequest,
      );
      expect(events.map((event) => event.type)).toEqual([
        "scene_stream_started",
        ...Array.from(
          { length: 6 },
          () => "projectile_choreography_scene_checkpoint",
        ),
        "scene_stream_completed",
      ]);
      expect(
        checkpoints(events).map((event) => event.semantic.checkpointId),
      ).toEqual(PROJECTILE_MOTION_MAIN_CHECKPOINTS);
      expect(
        advanceFrontier(EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER, events),
      ).toMatchObject({
        scene: { revision: 6 },
        semanticScene: {
          revision: 6,
          components: [
            {
              problemSpec,
              lastMainCheckpoint: "summary",
            },
          ],
        },
      });
    },
  );

  it("cuts adaptive playback at the certified apex and holds until interruption", async () => {
    const fixtureRequest = request(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      1,
      PRIMARY_PROBLEM,
      ADVANCE,
    );
    const batch = createProjectileChoreographyFixtureBatch(fixtureRequest, {
      mode: "adaptive",
    });
    expect(batch.lane).toBe("main");
    expect(batch.holdOpenUntilAbort).toBe(true);
    expect(
      checkpoints(batch.events).map((event) => event.semantic.checkpointId),
    ).toEqual(["setup", "decompose_velocity", "trace_ascent", "apex_state"]);
    expect(batch.events.at(-1)?.type).toBe(
      "projectile_choreography_scene_checkpoint",
    );

    const controller = new AbortController();
    const observed: ProjectileChoreographySceneStreamEventV1[] = [];
    const running = createProjectileChoreographyFixtureRunner({
      mode: "adaptive",
      eventDelayMs: 0,
      chunkDelayMs: 0,
    })({
      request: fixtureRequest,
      signal: controller.signal,
      onEvent: (event) => {
        observed.push(event);
        if (
          event.type === "projectile_choreography_scene_checkpoint" &&
          event.semantic.checkpointId === "apex_state"
        ) {
          controller.abort();
        }
      },
    });
    await expect(running).rejects.toMatchObject({ name: "AbortError" });
    expect(
      checkpoints(observed).map((event) => event.semantic.checkpointId),
    ).toEqual(["setup", "decompose_velocity", "trace_ascent", "apex_state"]);
  });

  it.each([
    [
      "horizontal_velocity",
      "horizontal_velocity_detail",
      "clarifyHorizontal",
      2,
    ],
    ["apex_acceleration", "apex_acceleration_detail", "clarifyApex", 4],
    ["flight_symmetry", "flight_symmetry_detail", "clarifySymmetry", 5],
  ] as const)(
    "selects the exact real %s clarification lane",
    async (topic, checkpointId, laneName, mainCheckpointCount) => {
      const mainRequest = request(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        1,
        PRIMARY_PROBLEM,
        ADVANCE,
      );
      const mainEvents = await collect(
        createProjectileChoreographyFixtureRunner({
          eventDelayMs: 0,
          chunkDelayMs: 0,
        }),
        mainRequest,
      );
      const frontier = advanceFrontier(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        mainEvents,
        mainCheckpointCount,
      );
      const clarificationRequest = request(frontier, 2, PRIMARY_PROBLEM, {
        intent: "clarify",
        topic,
      });
      expect(
        createProjectileChoreographyFixtureBatch(clarificationRequest).lane,
      ).toBe(laneName);
      const events = await collect(
        createProjectileChoreographyFixtureRunner({
          eventDelayMs: 0,
          chunkDelayMs: 0,
        }),
        clarificationRequest,
      );
      expect(checkpoints(events)).toHaveLength(1);
      expect(checkpoints(events)[0].semantic.checkpointId).toBe(checkpointId);
    },
  );

  it("re-envelopes every exact unclarified main suffix without changing patch or certificate bodies", async () => {
    const mainRequest = request(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      1,
      PRIMARY_PROBLEM,
      ADVANCE,
    );
    const mainBatch = createProjectileChoreographyFixtureBatch(mainRequest);
    const mainCheckpoints = checkpoints(mainBatch.events);

    for (
      let prefixCount = 1;
      prefixCount < mainCheckpoints.length;
      prefixCount += 1
    ) {
      const frontier = advanceFrontier(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        mainBatch.events,
        prefixCount,
      );
      const continuationRequest = request(
        frontier,
        2,
        PRIMARY_PROBLEM,
        ADVANCE,
      );
      const continuation =
        createProjectileChoreographyFixtureBatch(continuationRequest);
      const continuationCheckpoints = checkpoints(continuation.events);
      expect(continuation.lane).toBe("continueMain");
      expect(continuation.holdOpenUntilAbort).toBe(false);
      expect(
        continuationCheckpoints.map((event) => event.semantic.checkpointId),
      ).toEqual(PROJECTILE_MOTION_MAIN_CHECKPOINTS.slice(prefixCount));
      expect(continuationCheckpoints.map((event) => event.generation)).toEqual(
        Array.from({ length: continuationCheckpoints.length }, () => 2),
      );
      expect(continuationCheckpoints.map((event) => event.sequence)).toEqual(
        Array.from(
          { length: continuationCheckpoints.length },
          (_, index) => index + 1,
        ),
      );
      for (const [index, checkpoint] of continuationCheckpoints.entries()) {
        const source = mainCheckpoints[prefixCount + index];
        expect(checkpoint.patch).toEqual(source.patch);
        expect(checkpoint.semantic.certificate.body).toEqual(
          source.semantic.certificate.body,
        );
      }
      expect(advanceFrontier(frontier, continuation.events)).toMatchObject({
        scene: { revision: 6 },
        semanticScene: {
          revision: 6,
          components: [{ lastMainCheckpoint: "summary" }],
        },
      });
    }

    const oneCheckpointFrontier = advanceFrontier(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      mainBatch.events,
      1,
    );
    const streamed = await collect(
      createProjectileChoreographyFixtureRunner({
        eventDelayMs: 0,
        chunkDelayMs: 0,
      }),
      request(oneCheckpointFrontier, 2, PRIMARY_PROBLEM, ADVANCE),
    );
    expect(streamed.at(0)).toMatchObject({
      type: "scene_stream_started",
      generation: 2,
      baseRevision: 1,
    });
    expect(streamed.at(-1)).toMatchObject({
      type: "scene_stream_completed",
      generation: 2,
      finalRevision: 6,
      patchCount: 5,
    });
  });

  it("continues or retargets from the clarified apex and retargets after summary", async () => {
    const runner = createProjectileChoreographyFixtureRunner({
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    const mainEvents = await collect(
      runner,
      request(
        EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
        1,
        PRIMARY_PROBLEM,
        ADVANCE,
      ),
    );
    const apex = advanceFrontier(
      EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
      mainEvents,
      4,
    );
    const clarificationEvents = await collect(
      runner,
      request(apex, 2, PRIMARY_PROBLEM, {
        intent: "clarify",
        topic: "apex_acceleration",
      }),
    );
    const clarifiedApex = advanceFrontier(apex, clarificationEvents);

    const retargetAtApexRequest = request(clarifiedApex, 3, PRIMARY_PROBLEM, {
      intent: "retarget",
      targetProblemSpec: RETARGET_PROBLEM,
    });
    expect(
      createProjectileChoreographyFixtureBatch(retargetAtApexRequest).lane,
    ).toBe("retargetAtApex");
    const retargetAtApex = await collect(runner, retargetAtApexRequest);
    expect(checkpoints(retargetAtApex)[0].semantic.checkpointId).toBe(
      "parameters_retargeted",
    );

    const continuationEvents = await collect(
      runner,
      request(clarifiedApex, 3, PRIMARY_PROBLEM, ADVANCE),
    );
    expect(
      checkpoints(continuationEvents).map(
        (event) => event.semantic.checkpointId,
      ),
    ).toEqual(["trace_descent", "summary"]);
    const summary = advanceFrontier(clarifiedApex, continuationEvents);
    const retargetAfterSummaryRequest = request(summary, 4, PRIMARY_PROBLEM, {
      intent: "retarget",
      targetProblemSpec: RETARGET_PROBLEM,
    });
    expect(
      createProjectileChoreographyFixtureBatch(retargetAfterSummaryRequest)
        .lane,
    ).toBe("retargetAfterSummary");
    const retargetAfterSummary = await collect(
      runner,
      retargetAfterSummaryRequest,
    );
    expect(
      advanceFrontier(summary, retargetAfterSummary).semanticScene.components[0]
        .problemSpec,
    ).toEqual(RETARGET_PROBLEM);
  });

  it("rejects requests outside an exact problem, generation, route, and frontier", () => {
    expect(() =>
      createProjectileChoreographyFixtureBatch(
        request(
          EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
          1,
          { v: 1, speedMps: 25, angleDeg: 45 },
          ADVANCE,
        ),
      ),
    ).toThrowError(ProjectileChoreographyFixtureError);
    expect(() =>
      createProjectileChoreographyFixtureBatch(
        request(
          EMPTY_PROJECTILE_CHOREOGRAPHY_FRONTIER,
          2,
          PRIMARY_PROBLEM,
          ADVANCE,
        ),
      ),
    ).toThrowError(/generation, route, and certified frontier/);
  });

  it("rejects malformed generated envelopes before creating a runner", () => {
    const providerBacked = mutablePrimaryFixture();
    providerBacked.providerRequestCount = 1;
    expect(() =>
      createProjectileChoreographyFixtureRunner({
        fixtureValues: [providerBacked],
      }),
    ).toThrowError(/provider count is invalid/);

    const repaired = mutablePrimaryFixture();
    repaired.lanes.main.events.at(-1)!.repaired = true;
    expect(() =>
      createProjectileChoreographyFixtureRunner({ fixtureValues: [repaired] }),
    ).toThrowError(/lifecycle does not match/);
  });
});
