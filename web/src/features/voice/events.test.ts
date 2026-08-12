import { describe, expect, it } from "vitest";

import {
  decodeVoiceEvent,
  REQUIRED_VOICE_READY_COMPONENTS,
  VOICE_EVENT_SCHEMA_VERSION,
} from "./events";
import sharedCanvasPatchFixture from "./voice-event.fixture.json";

function rawEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: VOICE_EVENT_SCHEMA_VERSION,
    event_id: "event-1",
    event_type: "transport_connected",
    trace_id: "trace-1",
    voice_call_id: "call-1",
    session_id: "session-1",
    producer_id: "worker-1",
    producer_sequence: 1,
    emitted_at: "2026-08-12T00:00:00.000Z",
    payload: {},
    ...overrides,
  };
}

describe("Voice V2 event decoding", () => {
  it("returns a discriminated event with the reviewed envelope", () => {
    const decoded = decodeVoiceEvent(
      rawEvent({
        event_type: "agent_ready",
        event_id: "ready-1",
        producer_sequence: 2,
        correlation_id: "call-1",
        payload: {
          profile_id: "cascade-v1",
          required_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
          ready_components: [...REQUIRED_VOICE_READY_COMPONENTS, "stt", "llm", "tts"],
        },
      })
    );

    expect(decoded.ok).toBe(true);
    if (!decoded.ok) throw new Error(decoded.error.message);
    expect(decoded.event).toMatchObject({
      schema_version: 1,
      event_type: "agent_ready",
      producer_id: "worker-1",
      producer_sequence: 2,
      correlation_id: "call-1",
    });
    if (decoded.event.event_type === "agent_ready") {
      expect(decoded.event.payload.profile_id).toBe("cascade-v1");
    }
  });

  it("fails closed on unknown schema versions and event types", () => {
    const wrongVersion = decodeVoiceEvent(rawEvent({ schema_version: 2 }));
    const unknownType = decodeVoiceEvent(rawEvent({ event_type: "canvas_force_replace" }));

    expect(wrongVersion).toMatchObject({
      ok: false,
      error: { code: "unsupported_schema_version" },
    });
    expect(unknownType).toMatchObject({
      ok: false,
      error: { code: "unknown_event_type" },
    });
  });

  it("does not accept readiness until every required component is ready", () => {
    const decoded = decodeVoiceEvent(
      rawEvent({
        event_type: "agent_ready",
        payload: {
          profile_id: "cascade-v1",
          required_components: [...REQUIRED_VOICE_READY_COMPONENTS, "tts"],
          ready_components: [...REQUIRED_VOICE_READY_COMPONENTS],
        },
      })
    );

    expect(decoded).toMatchObject({ ok: false, error: { code: "invalid_payload" } });
  });

  it("requires task identity and generation together for task transitions", () => {
    const missingGeneration = decodeVoiceEvent(
      rawEvent({
        event_type: "task_working",
        task_id: "task-1",
        payload: { message: "Searching", progress: 0.25 },
      })
    );
    const complete = decodeVoiceEvent(
      rawEvent({
        event_type: "task_working",
        task_id: "task-1",
        task_generation: 1,
        ledger_sequence: 8,
        payload: { message: "Searching", progress: 0.25 },
      })
    );

    expect(missingGeneration).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(complete.ok).toBe(true);
  });

  it("accepts only a one-revision canvas patch with JSON data", () => {
    const valid = decodeVoiceEvent(
      rawEvent({
        event_type: "canvas_patch",
        event_id: "patch-1",
        task_id: "task-1",
        task_generation: 1,
        canvas_base_revision: 3,
        canvas_result_revision: 4,
        payload: {
          artifact_id: "artifact-1",
          artifact: { kind: "operations_v1", operations: [] },
        },
      })
    );
    const skippedRevision = decodeVoiceEvent(
      rawEvent({
        event_type: "canvas_patch",
        task_id: "task-1",
        task_generation: 1,
        canvas_base_revision: 3,
        canvas_result_revision: 5,
        payload: {
          artifact_id: "artifact-1",
          artifact: { kind: "operations_v1", operations: [] },
        },
      })
    );
    const nonJson = decodeVoiceEvent(
      rawEvent({
        event_type: "canvas_patch",
        task_id: "task-1",
        task_generation: 1,
        canvas_base_revision: 3,
        canvas_result_revision: 4,
        payload: {
          artifact_id: "artifact-1",
          artifact: { render: () => undefined },
        },
      })
    );

    expect(valid.ok).toBe(true);
    expect(skippedRevision).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(nonJson).toMatchObject({ ok: false, error: { code: "invalid_payload" } });
  });

  it("keeps transcript segment finality separate from turn commitment", () => {
    const segment = decodeVoiceEvent(
      rawEvent({
        event_type: "transcript_segment",
        payload: { segment_id: "segment-1", text: "I was", is_final: true },
      })
    );

    expect(segment.ok).toBe(true);
    if (!segment.ok) throw new Error(segment.error.message);
    expect(segment.event.event_type).toBe("transcript_segment");
    expect("speech_final" in segment.event.payload).toBe(false);
  });

  it("requires call ownership, scoped turn IDs, and positive task generations", () => {
    const missingCall = decodeVoiceEvent(rawEvent({ voice_call_id: undefined }));
    const missingSpeechTurn = decodeVoiceEvent(
      rawEvent({
        event_type: "assistant_speech_started",
        payload: { speech_id: "speech-1" },
      })
    );
    const zeroGeneration = decodeVoiceEvent(
      rawEvent({
        event_type: "artifact_proposed",
        task_id: "task-1",
        task_generation: 0,
        payload: { artifact_id: "artifact-1", artifact_kind: "operations_v1" },
      })
    );

    expect(missingCall).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(missingSpeechTurn).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(zeroGeneration).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
  });

  it("mirrors contract ID and payload text bounds", () => {
    const invalidPayloadId = decodeVoiceEvent(
      rawEvent({
        event_type: "transport_connected",
        payload: { connection_id: "has spaces" },
      })
    );
    const oversizedTurn = decodeVoiceEvent(
      rawEvent({
        event_type: "turn_committed",
        turn_id: "turn-1",
        payload: { text: "x".repeat(16_001) },
      })
    );
    const maximumTranscript = decodeVoiceEvent(
      rawEvent({
        event_type: "transcript_segment",
        payload: {
          segment_id: "segment-1",
          text: "x".repeat(64_000),
          is_final: true,
        },
      })
    );
    const oversizedTranscript = decodeVoiceEvent(
      rawEvent({
        event_type: "transcript_segment",
        payload: {
          segment_id: "segment-1",
          text: "x".repeat(64_001),
          is_final: true,
        },
      })
    );

    expect(invalidPayloadId).toMatchObject({
      ok: false,
      error: { code: "invalid_payload" },
    });
    expect(oversizedTurn).toMatchObject({
      ok: false,
      error: { code: "invalid_payload" },
    });
    expect(maximumTranscript.ok).toBe(true);
    expect(oversizedTranscript).toMatchObject({
      ok: false,
      error: { code: "invalid_payload" },
    });
  });

  it("requires optional payload values to be omitted instead of null", () => {
    expect(
      decodeVoiceEvent(
        rawEvent({
          event_type: "transport_connected",
          payload: { connection_id: null },
        })
      )
    ).toMatchObject({ ok: false, error: { code: "invalid_payload" } });
  });

  it("rejects unknown envelope fields and invalid aware timestamps", () => {
    const extraField = decodeVoiceEvent(rawEvent({ unexpected: true }));
    const naiveTimestamp = decodeVoiceEvent(
      rawEvent({ emitted_at: "2026-08-12T00:00:00" })
    );
    const impossibleDate = decodeVoiceEvent(
      rawEvent({ emitted_at: "2026-02-30T00:00:00Z" })
    );
    const nonLeapDay = decodeVoiceEvent(
      rawEvent({ emitted_at: "2025-02-29T00:00:00+05:30" })
    );
    const invalidTime = decodeVoiceEvent(
      rawEvent({ emitted_at: "2026-08-12T24:00:00Z" })
    );
    const validLeapDay = decodeVoiceEvent(
      rawEvent({ emitted_at: "2024-02-29T23:59:59.123456+05:30" })
    );

    expect(extraField).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(naiveTimestamp).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(impossibleDate).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(nonLeapDay).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(invalidTime).toMatchObject({
      ok: false,
      error: { code: "invalid_envelope" },
    });
    expect(validLeapDay.ok).toBe(true);
  });

  it("rejects unknown top-level payload keys while keeping artifacts opaque", () => {
    const unknownReadyKey = decodeVoiceEvent(
      rawEvent({
        event_type: "agent_ready",
        payload: {
          profile_id: "cascade-v1",
          required_components: REQUIRED_VOICE_READY_COMPONENTS,
          ready_components: REQUIRED_VOICE_READY_COMPONENTS,
          future_flag: true,
        },
      })
    );
    const nonEmptySessionPayload = decodeVoiceEvent(
      rawEvent({ event_type: "session_started", payload: { future_flag: true } })
    );
    const opaqueArtifact = decodeVoiceEvent(
      rawEvent({
        event_type: "canvas_patch",
        task_id: "task-1",
        task_generation: 1,
        canvas_base_revision: 0,
        canvas_result_revision: 1,
        payload: {
          artifact_id: "artifact-1",
          artifact: { renderer_specific_future_field: { nested: true } },
        },
      })
    );

    expect(unknownReadyKey).toMatchObject({
      ok: false,
      error: { code: "invalid_payload" },
    });
    expect(nonEmptySessionPayload).toMatchObject({
      ok: false,
      error: { code: "invalid_payload" },
    });
    expect(opaqueArtifact.ok).toBe(true);
  });

  it("round-trips the shared backend/browser JSON fixture", () => {
    const decoded = decodeVoiceEvent(sharedCanvasPatchFixture);
    expect(decoded.ok).toBe(true);
    if (!decoded.ok) throw new Error(decoded.error.message);

    const wireValue: unknown = JSON.parse(JSON.stringify(decoded.event));
    expect(decodeVoiceEvent(wireValue)).toEqual(decoded);
  });

  it("deep-freezes decoded envelopes and nested payload JSON", () => {
    const decoded = decodeVoiceEvent(sharedCanvasPatchFixture);
    expect(decoded.ok).toBe(true);
    if (!decoded.ok) throw new Error(decoded.error.message);
    if (decoded.event.event_type !== "canvas_patch") {
      throw new Error("Expected the shared fixture to be a canvas patch");
    }

    const artifact = decoded.event.payload.artifact;
    const operations = artifact.operations;
    if (!Array.isArray(operations)) {
      throw new Error("Expected fixture artifact operations");
    }
    expect(Object.isFrozen(decoded.event)).toBe(true);
    expect(Object.isFrozen(decoded.event.payload)).toBe(true);
    expect(Object.isFrozen(artifact)).toBe(true);
    expect(Object.isFrozen(operations)).toBe(true);
    expect(Object.isFrozen(operations[0])).toBe(true);
    expect(Reflect.set(artifact, "artifact_type", "mutated")).toBe(false);
    expect(artifact.artifact_type).toBe("operations_v1");
  });
});
