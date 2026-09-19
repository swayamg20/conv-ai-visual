import { describe, expect, it } from "vitest";

import storyboardFixture from "./fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a45.v1.json";
import { decodePairedProjectileComparisonSpecV1 } from "@/lib/live-scene/semantic-storyboard";
import {
  decodeSemanticStoryboardSceneStreamEventV1,
  type SemanticStoryboardSceneCheckpointEventV1,
} from "@/lib/live-scene/semantic-storyboard-stream";

import {
  EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
  adaptSemanticStoryboardStreamEvent,
  createAcceptedSemanticStoryboardCheckpoint,
  createSemanticStoryboardRequest,
  preflightSemanticStoryboardReplay,
  prepareSemanticStoryboardCheckpoint,
  type AcceptedSemanticStoryboardCheckpoint,
  type SemanticStoryboardFrontier,
} from "./semantic-storyboard-playback";

type JsonRecord = Record<string, unknown>;

const SETTLED = Object.freeze({
  status: "completed" as const,
  firstCuePresented: true,
});

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function wireEvents(value: unknown): JsonRecord[] {
  return (value as { events: JsonRecord[] }).events;
}

function checkpoints(
  value: unknown,
): SemanticStoryboardSceneCheckpointEventV1[] {
  return wireEvents(value)
    .map(decodeSemanticStoryboardSceneStreamEventV1)
    .filter(
      (event): event is SemanticStoryboardSceneCheckpointEventV1 =>
        event.type === "semantic_storyboard_scene_checkpoint",
    );
}

function acceptStory(
  scenario: unknown,
): readonly AcceptedSemanticStoryboardCheckpoint[] {
  const records: AcceptedSemanticStoryboardCheckpoint[] = [];
  let frontier: SemanticStoryboardFrontier = EMPTY_SEMANTIC_STORYBOARD_FRONTIER;
  const allCheckpoints = [
    ...checkpoints(storyboardFixture.anchor),
    ...checkpoints(scenario),
  ];
  for (const event of allCheckpoints) {
    const prepared = prepareSemanticStoryboardCheckpoint(
      frontier,
      event,
      "cinematic",
    );
    const accepted = createAcceptedSemanticStoryboardCheckpoint(
      prepared,
      SETTLED,
    );
    records.push(accepted);
    frontier = prepared.target;
  }
  return records;
}

describe("semantic storyboard playback adapter", () => {
  it("prepares the anchor only from its exact empty frontier", () => {
    const anchor = checkpoints(storyboardFixture.anchor)[0];
    const prepared = prepareSemanticStoryboardCheckpoint(
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      anchor,
      "cinematic",
    );

    expect(prepared.bootstrappedViewport).toBe(true);
    expect(prepared.base.scene).toEqual(anchor.transition.baseScene);
    expect(prepared.target.scene).toEqual(anchor.transition.resultScene);
    expect(prepared.target.semanticScene).toEqual(
      anchor.transition.resultSemanticScene,
    );
    expect(prepared.event.transition.checkpoint.certificate).toEqual(
      anchor.transition.checkpoint.certificate,
    );

    const nonEmpty: SemanticStoryboardFrontier = {
      ...EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      scene: anchor.transition.resultScene,
    };
    expect(() =>
      prepareSemanticStoryboardCheckpoint(nonEmpty, anchor, "cinematic"),
    ).toThrow(/frontier revisions differ|does not exact-match/);
  });

  it("uses the same adapter for different story lengths, orders, and IDs", () => {
    const formulaFirst = acceptStory(storyboardFixture.programs[0]);
    const motionFirst = acceptStory(storyboardFixture.programs[1]);

    expect(formulaFirst).toHaveLength(3);
    expect(motionFirst).toHaveLength(4);
    expect(
      formulaFirst.map(
        (record) => record.event.transition.checkpoint.checkpointId,
      ),
    ).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-reveal-range-formula",
      "storyboard-checkpoint-relate-unequal-range",
    ]);
    expect(
      motionFirst.map(
        (record) => record.event.transition.checkpoint.checkpointId,
      ),
    ).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-trace-higher-angle",
      "storyboard-checkpoint-trace-lower-angle",
      "storyboard-checkpoint-relate-unequal-range",
    ]);

    for (const records of [formulaFirst, motionFirst]) {
      const replay = preflightSemanticStoryboardReplay(records);
      expect(replay.records).toEqual(records);
      expect(replay.checkpoints).toHaveLength(records.length);
      expect(replay.frontier.scene).toEqual(records.at(-1)?.scene);
      expect(replay.frontier.semanticScene).toEqual(
        records.at(-1)?.semanticScene,
      );
    }
  });

  it("rejects a checkpoint whose certified base is not the current frontier", () => {
    const modelCheckpoint = checkpoints(storyboardFixture.programs[0])[0];
    expect(() =>
      prepareSemanticStoryboardCheckpoint(
        EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
        modelCheckpoint,
        "cinematic",
      ),
    ).toThrow(/does not exact-match the frontier/);
  });

  it("retains only post-paint settlements and revalidates replay receipts", () => {
    const prepared = prepareSemanticStoryboardCheckpoint(
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      checkpoints(storyboardFixture.anchor)[0],
      "compact",
    );
    expect(() =>
      createAcceptedSemanticStoryboardCheckpoint(prepared, {
        status: "cancelled_before_presented",
        firstCuePresented: false,
      }),
    ).toThrow(/fully settled visible checkpoint/);

    const accepted = createAcceptedSemanticStoryboardCheckpoint(
      prepared,
      SETTLED,
    );
    const tampered = clone(accepted) as AcceptedSemanticStoryboardCheckpoint;
    (tampered.presentation as { certificateSha256: string }).certificateSha256 =
      "f".repeat(64);
    expect(() => preflightSemanticStoryboardReplay([tampered])).toThrow(
      /does not exact-match its replay plan/,
    );
  });

  it("builds strict requests from the accepted frontier", () => {
    const problemSpec = decodePairedProjectileComparisonSpecV1(
      storyboardFixture.problemSpec,
    );
    const reflex = createSemanticStoryboardRequest(
      { routingMode: "reflex", problemSpec },
      1,
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
    );
    expect(reflex.routingMode).toBe("reflex");

    const anchor = prepareSemanticStoryboardCheckpoint(
      EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      checkpoints(storyboardFixture.anchor)[0],
      "cinematic",
    );
    const director = createSemanticStoryboardRequest(
      {
        routingMode: "director",
        problemSpec,
        prompt: "  trace the higher arc first  ",
      },
      2,
      anchor.target,
    );
    expect(director).toMatchObject({
      routingMode: "director",
      generation: 2,
      prompt: "trace the higher arc first",
      baseScene: anchor.target.scene,
      baseSemanticScene: anchor.target.semanticScene,
    });

    expect(() =>
      createSemanticStoryboardRequest(
        { routingMode: "director", problemSpec, prompt: "continue" },
        1,
        EMPTY_SEMANTIC_STORYBOARD_FRONTIER,
      ),
    ).toThrow(/requires the certified storyboard anchor/);
  });

  it("maps accepted_prefix to successful completion without losing its cause", () => {
    const adapted = adaptSemanticStoryboardStreamEvent({
      type: "semantic_storyboard_scene_stream_completed",
      generation: 2,
      attempt: 1,
      baseRevision: 1,
      finalRevision: 3,
      checkpointCount: 2,
      firstCheckpointMs: 18,
      totalMs: 42,
      reasonCode: "accepted_prefix",
      acceptedPrefixCause: "invalid_model_stream",
    });

    expect(adapted).toMatchObject({
      kind: "completed",
      patchCount: 2,
      repaired: false,
      completionMetadata: {
        reasonCode: "accepted_prefix",
        detailCode: "invalid_model_stream",
      },
    });
  });
});
