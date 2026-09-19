import { describe, expect, it } from "vitest";

import storyboardFixture from "@/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a45.v1.json";
import comprehensiveFixture from "@/features/live-scene/fixtures/semantic-storyboard-v1/semantic-storyboard-v20-a30-a60.v1.json";

import { LiveSceneProtocolError } from "./patch";
import {
  MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES,
  decodeSemanticStoryboardSceneStreamEventV1,
  parseSemanticStoryboardSceneStreamEventV1,
} from "./semantic-storyboard-stream";

type JsonRecord = Record<string, unknown>;

function clone<Value>(value: Value): Value {
  return JSON.parse(JSON.stringify(value)) as Value;
}

function nested(value: JsonRecord, key: string): JsonRecord {
  return value[key] as JsonRecord;
}

function events(value: unknown): JsonRecord[] {
  return (value as { events: JsonRecord[] }).events;
}

function anchorCheckpoint(): JsonRecord {
  return clone(events(storyboardFixture.anchor)[1]);
}

describe("semantic storyboard scene stream contract", () => {
  it("decodes backend-generated streams of different lengths and record orders", () => {
    const scenarios: unknown[] = [
      storyboardFixture.anchor,
      ...storyboardFixture.programs,
      ...storyboardFixture.continuations,
      comprehensiveFixture.anchor,
      ...comprehensiveFixture.programs,
      ...comprehensiveFixture.continuations,
      comprehensiveFixture.soleAbstain,
      comprehensiveFixture.acceptedPrefixMalformedTail,
    ];
    for (const scenario of scenarios) {
      for (const event of events(scenario)) {
        expect(decodeSemanticStoryboardSceneStreamEventV1(event).type).toBe(
          event.type,
        );
        expect(
          parseSemanticStoryboardSceneStreamEventV1(JSON.stringify(event)).type,
        ).toBe(event.type);
      }
    }

    expect(storyboardFixture.programs[0].checkpointIds).not.toEqual(
      storyboardFixture.programs[1].checkpointIds,
    );
    expect(storyboardFixture.programs[0].checkpointCount).not.toBe(
      storyboardFixture.programs[1].checkpointCount,
    );
  });

  it("preserves the complete certified transition and enforces patch equality", () => {
    const raw = anchorCheckpoint();
    const decoded = decodeSemanticStoryboardSceneStreamEventV1(raw);
    expect(decoded.type).toBe("semantic_storyboard_scene_checkpoint");
    if (decoded.type !== "semantic_storyboard_scene_checkpoint") return;

    expect(decoded.transition).toEqual(raw.transition);
    expect(decoded.patch).toEqual(decoded.transition.checkpoint.patch);
    expect(
      decoded.transition.checkpoint.certificate.body.checkpointOrigin,
    ).toBe("anchor");

    const mismatched = anchorCheckpoint();
    nested(mismatched, "patch").narration = "A different top-level patch.";
    expect(() =>
      decodeSemanticStoryboardSceneStreamEventV1(mismatched),
    ).toThrow(/top-level patch must equal/);
  });

  it("rejects snake_case and unknown keys recursively", () => {
    const topLevel = anchorCheckpoint();
    topLevel.base_revision = 0;
    expect(() => decodeSemanticStoryboardSceneStreamEventV1(topLevel)).toThrow(
      /unknown field base_revision/,
    );

    const nestedSnakeCase = anchorCheckpoint();
    const body = nested(
      nested(
        nested(nested(nestedSnakeCase, "transition"), "checkpoint"),
        "certificate",
      ),
      "body",
    );
    body.base_low_level_revision = body.baseLowLevelRevision;
    expect(() =>
      decodeSemanticStoryboardSceneStreamEventV1(nestedSnakeCase),
    ).toThrow(/unknown field base_low_level_revision/);

    const nestedUnknown = anchorCheckpoint();
    nested(nested(nestedUnknown, "transition"), "baseSemanticScene").extra =
      true;
    expect(() =>
      decodeSemanticStoryboardSceneStreamEventV1(nestedUnknown),
    ).toThrow(/unknown field extra/);
  });

  it("rejects matching but false problem commitments", () => {
    const value = anchorCheckpoint();
    const checkpoint = nested(nested(value, "transition"), "checkpoint");
    nested(checkpoint, "receipt").problemSpecSha256 = "f".repeat(64);
    nested(nested(checkpoint, "certificate"), "body").problemSpecSha256 =
      "f".repeat(64);
    expect(() => decodeSemanticStoryboardSceneStreamEventV1(value)).toThrow(
      /commitments do not match the bound problem/,
    );
  });

  it("accepts exactly the five dedicated event types", () => {
    const started = {
      type: "semantic_storyboard_scene_stream_started",
      generation: 7,
      attempt: 1,
      baseRevision: 1,
    };
    const completed = {
      type: "semantic_storyboard_scene_stream_completed",
      generation: 7,
      attempt: 1,
      baseRevision: 1,
      finalRevision: 3,
      checkpointCount: 2,
      firstCheckpointMs: 12.5,
      totalMs: 25,
      reasonCode: "accepted_prefix",
      acceptedPrefixCause: "provider_timeout",
    };
    const declined = {
      type: "semantic_storyboard_scene_stream_declined",
      generation: 7,
      attempt: 1,
      baseRevision: 1,
      finalRevision: 1,
      reasonCode: "unsupported_intent",
      message: "That request is outside this storyboard.",
    };
    const failed = {
      type: "semantic_storyboard_scene_stream_failed",
      generation: 7,
      attempt: 1,
      baseRevision: 1,
      code: "provider_timeout",
      message: "The storyboard provider timed out.",
      lastAcceptedRevision: 1,
      retryable: true,
    };

    expect(decodeSemanticStoryboardSceneStreamEventV1(started)).toEqual(
      started,
    );
    expect(
      decodeSemanticStoryboardSceneStreamEventV1(anchorCheckpoint()).type,
    ).toBe("semantic_storyboard_scene_checkpoint");
    expect(decodeSemanticStoryboardSceneStreamEventV1(completed)).toEqual(
      completed,
    );
    expect(decodeSemanticStoryboardSceneStreamEventV1(declined)).toEqual(
      declined,
    );
    expect(decodeSemanticStoryboardSceneStreamEventV1(failed)).toEqual(failed);

    for (const oldType of [
      "scene_stream_started",
      "scene_stream_completed",
      "projectile_choreography_scene_checkpoint",
      "scene_stream_repairing",
    ]) {
      expect(() =>
        decodeSemanticStoryboardSceneStreamEventV1({
          ...started,
          type: oldType,
        }),
      ).toThrow(/event type is unsupported/);
    }
  });

  it("rejects invalid terminal semantics and retryability", () => {
    const acceptedPrefix = {
      type: "semantic_storyboard_scene_stream_completed",
      generation: 2,
      attempt: 1,
      baseRevision: 1,
      finalRevision: 2,
      checkpointCount: 1,
      firstCheckpointMs: 5,
      totalMs: 8,
      reasonCode: "accepted_prefix",
      acceptedPrefixCause: null,
    };
    expect(() =>
      decodeSemanticStoryboardSceneStreamEventV1(acceptedPrefix),
    ).toThrow(/acceptedPrefixCause/);

    expect(() =>
      decodeSemanticStoryboardSceneStreamEventV1({
        type: "semantic_storyboard_scene_stream_failed",
        generation: 2,
        attempt: 1,
        baseRevision: 1,
        code: "provider_timeout",
        message: "Timed out.",
        lastAcceptedRevision: 1,
        retryable: false,
      }),
    ).toThrow(/retryable must be true/);
  });

  it("bounds the whole SSE event before parsing JSON", () => {
    const oversized = JSON.stringify({
      type: "semantic_storyboard_scene_stream_started",
      generation: 1,
      attempt: 1,
      baseRevision: 0,
      padding: "x".repeat(MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES),
    });
    expect(() => parseSemanticStoryboardSceneStreamEventV1(oversized)).toThrow(
      new LiveSceneProtocolError(
        "budget_exceeded",
        `semantic storyboard stream SSE event exceeds ${MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES} bytes`,
      ),
    );
    expect(() => parseSemanticStoryboardSceneStreamEventV1("{")).toThrow(
      /valid JSON/,
    );
  });
});
