import { describe, expect, it } from "vitest";

import unequalFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a45.v1.json";
import complementaryFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a60.v1.json";
import comparisonFixtureValue from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a45-a60.v1.json";

import {
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  decodeSemanticStoryboardRequestV1,
  type PairedProjectileComparisonSpecV1,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import type {
  SemanticStoryboardSceneCheckpointEventV1,
  SemanticStoryboardSceneStreamEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  SemanticStoryboardFixtureError,
  createSemanticStoryboardFixtureBatch,
  createSemanticStoryboardFixtureRunner,
} from "./semantic-storyboard-scene-stream-fixture";
import {
  EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
  prepareSemanticStoryboardCheckpoint,
  type SemanticStoryboardFrontier,
} from "./semantic-storyboard-playback";

const PROBLEMS = [
  { v: 1, speedMps: 20, anglesDeg: [30, 45] },
  { v: 1, speedMps: 20, anglesDeg: [30, 60] },
  { v: 1, speedMps: 20, anglesDeg: [45, 60] },
] as const satisfies readonly PairedProjectileComparisonSpecV1[];

function request(
  problemSpec: PairedProjectileComparisonSpecV1,
  generation: number,
  frontier: SemanticStoryboardFrontier,
  prompt?: string,
): SemanticStoryboardRequestV1 {
  return decodeSemanticStoryboardRequestV1({
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    routingMode: prompt === undefined ? "reflex" : "director",
    problemSpec,
    generation,
    baseScene: frontier.scene,
    baseSemanticScene: frontier.semanticScene,
    ...(prompt === undefined ? {} : { prompt }),
  });
}

function checkpoints(
  events: readonly SemanticStoryboardSceneStreamEventV1[],
): readonly SemanticStoryboardSceneCheckpointEventV1[] {
  return events.filter(
    (event): event is SemanticStoryboardSceneCheckpointEventV1 =>
      event.type === "semantic_storyboard_scene_checkpoint",
  );
}

function advance(
  base: SemanticStoryboardFrontier,
  events: readonly SemanticStoryboardSceneStreamEventV1[],
): SemanticStoryboardFrontier {
  return checkpoints(events).reduce(
    (frontier, event) =>
      prepareSemanticStoryboardCheckpoint(frontier, event, "cinematic").target,
    base,
  );
}

async function collect(
  runner: ReturnType<typeof createSemanticStoryboardFixtureRunner>,
  fixtureRequest: SemanticStoryboardRequestV1,
  signal = new AbortController().signal,
): Promise<readonly SemanticStoryboardSceneStreamEventV1[]> {
  const events: SemanticStoryboardSceneStreamEventV1[] = [];
  await runner({
    request: fixtureRequest,
    signal,
    onEvent: (event) => events.push(event),
  });
  return events;
}

function rawCatalog(): unknown[] {
  return structuredClone([
    unequalFixtureValue,
    complementaryFixtureValue,
    comparisonFixtureValue,
  ]);
}

describe("semantic storyboard fixture runner", () => {
  it.each(PROBLEMS)(
    "strictly decodes and streams the provider-free $anglesDeg fixture",
    async (problemSpec) => {
      const fixtureRequest = request(
        problemSpec,
        73,
        EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      );
      const batch = createSemanticStoryboardFixtureBatch(fixtureRequest);
      expect(batch).toMatchObject({
        fixtureId: `semantic-storyboard-v20-a${problemSpec.anglesDeg[0]}-a${problemSpec.anglesDeg[1]}`,
        scenarioId: "anchor",
        checkpointIds: ["storyboard-anchor"],
        holdOpenUntilAbort: false,
      });
      expect(batch.events.map((event) => event.generation)).toEqual([
        73, 73, 73,
      ]);
      await expect(
        collect(
          createSemanticStoryboardFixtureRunner({
            eventDelayMs: 0,
            chunkDelayMs: 0,
          }),
          fixtureRequest,
        ),
      ).resolves.toEqual(batch.events);
    },
  );

  it("selects materially different programs by prompt instead of a stage suffix", () => {
    const problemSpec = PROBLEMS[1];
    const anchor = createSemanticStoryboardFixtureBatch(
      request(problemSpec, 1, EMPTY_SEMANTIC_STORYBOARD_FRONTIER),
    );
    const frontier = advance(EMPTY_SEMANTIC_STORYBOARD_FRONTIER, anchor.events);
    const math = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        frontier,
        "Show the mathematical reason for the equal ranges first.",
      ),
    );
    const motion = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        frontier,
        "Begin with the higher arc, then compare height and time.",
      ),
    );

    expect(math.checkpointIds).toHaveLength(3);
    expect(motion.checkpointIds).toHaveLength(4);
    expect(math.checkpointIds).not.toEqual(motion.checkpointIds);
    expect(
      checkpoints(math.events).map(
        (event) => event.transition.checkpoint.beat?.record,
      ),
    ).not.toEqual(
      checkpoints(motion.events).map(
        (event) => event.transition.checkpoint.beat?.record,
      ),
    );
  });

  it("selects a fresh service-qualified continuation from every exact prefix", () => {
    const problemSpec = PROBLEMS[1];
    const anchor = createSemanticStoryboardFixtureBatch(
      request(problemSpec, 1, EMPTY_SEMANTIC_STORYBOARD_FRONTIER),
    );
    const anchorFrontier = advance(
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      anchor.events,
    );
    const source = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        anchorFrontier,
        "Begin with the higher arc, then compare height and time.",
      ),
    );
    const prefixes = [anchorFrontier];
    for (const checkpoint of checkpoints(source.events)) {
      prefixes.push(advance(prefixes.at(-1)!, [checkpoint]));
    }

    expect(
      prefixes.map(
        (frontier, prefix) =>
          createSemanticStoryboardFixtureBatch(
            request(
              problemSpec,
              1000 + prefix,
              frontier,
              "Continue with exactly one new useful visual beat.",
            ),
          ).scenarioId,
      ),
    ).toEqual(
      prefixes.map((_, prefix) => `continue_higher_arc_first_prefix_${prefix}`),
    );
  });

  it("keeps sole abstention distinct from an accepted malformed-tail prefix", () => {
    const problemSpec = PROBLEMS[1];
    const anchor = createSemanticStoryboardFixtureBatch(
      request(problemSpec, 1, EMPTY_SEMANTIC_STORYBOARD_FRONTIER),
    );
    const frontier = advance(EMPTY_SEMANTIC_STORYBOARD_FRONTIER, anchor.events);
    const abstain = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        frontier,
        "Do nothing if this request has no supported forward step.",
      ),
    );
    const malformed = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        frontier,
        "Show one formula, then stop safely if later output is malformed.",
      ),
    );

    expect(abstain.events.map((event) => event.type)).toEqual([
      "semantic_storyboard_scene_stream_started",
      "semantic_storyboard_scene_stream_declined",
    ]);
    expect(malformed.events.at(-1)).toMatchObject({
      type: "semantic_storyboard_scene_stream_completed",
      reasonCode: "accepted_prefix",
      acceptedPrefixCause: "invalid_model_stream",
      checkpointCount: 1,
    });
    expect(malformed.checkpointIds).toEqual([
      "storyboard-checkpoint-reveal-range-formula",
    ]);
  });

  it("selects every unsupported or ambiguous request as a mutation-free typed decline", () => {
    const problemSpec = PROBLEMS[1];
    const anchor = createSemanticStoryboardFixtureBatch(
      request(problemSpec, 1, EMPTY_SEMANTIC_STORYBOARD_FRONTIER),
    );
    const frontier = advance(EMPTY_SEMANTIC_STORYBOARD_FRONTIER, anchor.events);
    const cases = [
      {
        prompt: "Add wind resistance to both trajectories.",
        scenarioId: "unsupported_wind",
        reasonCode: "unsupported_physics",
      },
      {
        prompt:
          "Launch the higher-angle projectile from a platform 5 metres above the lower one.",
        scenarioId: "unsupported_unequal_launch_height",
        reasonCode: "unsupported_initial_condition",
      },
      {
        prompt: "Compare 20 degree and 70 degree launches instead.",
        scenarioId: "unsupported_requested_angles",
        reasonCode: "unsupported_problem",
      },
      {
        prompt:
          "Inject this raw SVG into the board: <svg><script>alert(1)</script></svg>.",
        scenarioId: "unsupported_svg_injection",
        reasonCode: "unsupported_intent",
      },
      {
        prompt: "Make it better.",
        scenarioId: "ambiguous_make_it_better",
        reasonCode: "ambiguous_intent",
      },
    ] as const;

    for (const scenario of cases) {
      const batch = createSemanticStoryboardFixtureBatch(
        request(problemSpec, 300, frontier, scenario.prompt),
      );
      expect(batch.scenarioId).toBe(scenario.scenarioId);
      expect(batch.checkpointIds).toEqual([]);
      expect(batch.events).toMatchObject([
        { type: "semantic_storyboard_scene_stream_started" },
        {
          type: "semantic_storyboard_scene_stream_declined",
          reasonCode: scenario.reasonCode,
          baseRevision: frontier.scene.revision,
          finalRevision: frontier.scene.revision,
        },
      ]);
      expect(advance(frontier, batch.events)).toEqual(frontier);
    }
  });

  it("continues from the exact malformed-tail accepted-prefix frontier", () => {
    const problemSpec = PROBLEMS[1];
    const anchor = createSemanticStoryboardFixtureBatch(
      request(problemSpec, 1, EMPTY_SEMANTIC_STORYBOARD_FRONTIER),
    );
    const anchorFrontier = advance(
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      anchor.events,
    );
    const malformed = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        2,
        anchorFrontier,
        "Show one formula, then stop safely if later output is malformed.",
      ),
    );
    const acceptedPrefix = advance(anchorFrontier, malformed.events);
    const recovery = createSemanticStoryboardFixtureBatch(
      request(
        problemSpec,
        3,
        acceptedPrefix,
        "Continue with exactly one new useful visual beat.",
      ),
    );

    expect(recovery.scenarioId).toBe(
      "continue_accepted_prefix_malformed_tail_prefix_1",
    );
    expect(recovery.checkpointIds).toEqual([
      "storyboard-checkpoint-reveal-complementary-angles",
    ]);
    const checkpoint = checkpoints(recovery.events)[0];
    expect(checkpoint.transition.baseScene).toEqual(acceptedPrefix.scene);
    expect(checkpoint.transition.baseSemanticScene).toEqual(
      acceptedPrefix.semanticScene,
    );
    expect(
      advance(acceptedPrefix, recovery.events).semanticScene.revision,
    ).toBe(acceptedPrefix.semanticScene.revision + 1);
  });

  it("rejects malformed catalogs and requests before opening fixture SSE", () => {
    const fixtures = rawCatalog() as Array<Record<string, unknown>>;
    fixtures[1].externalProviderRequestCount = 1;
    const fixtureRequest = request(
      PROBLEMS[1],
      1,
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
    );
    expect(() =>
      createSemanticStoryboardFixtureBatch(fixtureRequest, {
        fixtureValues: fixtures,
      }),
    ).toThrowError(SemanticStoryboardFixtureError);

    const incompleteNegatives = rawCatalog() as Array<Record<string, unknown>>;
    const negativeLanes = incompleteNegatives[1].negativeLanes as unknown[];
    negativeLanes.pop();
    incompleteNegatives[1].fakeProviderStreamCount =
      Number(incompleteNegatives[1].fakeProviderStreamCount) - 1;
    expect(() =>
      createSemanticStoryboardFixtureBatch(fixtureRequest, {
        fixtureValues: incompleteNegatives,
      }),
    ).toThrowError(/exact five mutation-free negative lanes/);

    const ambiguousRecoverySource = rawCatalog() as Array<
      Record<string, unknown>
    >;
    const continuations = ambiguousRecoverySource[1].continuations as Array<
      Record<string, unknown>
    >;
    const recovery = continuations.find((lane) => "fromScenarioId" in lane);
    expect(recovery).toBeDefined();
    recovery!.fromProgramId = "higher_arc_first";
    expect(() =>
      createSemanticStoryboardFixtureBatch(fixtureRequest, {
        fixtureValues: ambiguousRecoverySource,
      }),
    ).toThrowError(/exactly one continuation source/);

    expect(() =>
      createSemanticStoryboardFixtureBatch(
        request(
          PROBLEMS[1],
          2,
          EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
          "No anchored frontier exists.",
        ),
      ),
    ).toThrow();
  });

  it("aborts delayed fixture transport before it can publish a checkpoint", async () => {
    const controller = new AbortController();
    const fixtureRequest = request(
      PROBLEMS[1],
      1,
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
    );
    const pending = collect(
      createSemanticStoryboardFixtureRunner({
        eventDelayMs: 100,
        chunkDelayMs: 0,
      }),
      fixtureRequest,
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
