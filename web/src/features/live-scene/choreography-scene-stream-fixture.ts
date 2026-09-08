import goldenFixtureValue from "./fixtures/completing-the-square.v1.json";

import { createSceneState, type SceneState } from "@/lib/live-scene";

import {
  consumeChoreographySceneStreamResponse,
  decodeChoreographySceneStreamEvent,
  type ChoreographySceneCheckpointEvent,
  type ChoreographySceneStreamEvent,
  type ChoreographySceneStreamRequest,
  type ChoreographySceneStreamRunner,
  type ChoreographySemanticSceneState,
} from "./choreography-model-stream";
import {
  EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
  createChoreographyFrontier,
  createChoreographySemanticSceneState,
  prepareChoreographyCheckpoint,
  type ChoreographyFrontier,
} from "./choreography-playback";
import { createFixtureSseResponse } from "./fixture-sse";

export const CHOREOGRAPHY_SCENE_FIXTURE_MODES = ["main", "adaptive"] as const;
export type ChoreographySceneFixtureMode =
  (typeof CHOREOGRAPHY_SCENE_FIXTURE_MODES)[number];

export interface ChoreographySceneFixtureRunnerOptions {
  readonly mode?: ChoreographySceneFixtureMode;
  readonly eventDelayMs?: number;
  readonly chunkDelayMs?: number;
}

export type ChoreographySceneFixtureErrorCode =
  "invalid_fixture" | "base_mismatch";

export class ChoreographySceneFixtureError extends Error {
  readonly code: ChoreographySceneFixtureErrorCode;

  constructor(code: ChoreographySceneFixtureErrorCode, message: string) {
    super(message);
    this.name = "ChoreographySceneFixtureError";
    this.code = code;
  }
}

interface FixtureTranscript {
  readonly main: readonly ChoreographySceneCheckpointEvent[];
  readonly mainPrefixes: readonly ChoreographyFrontier[];
  readonly clarification: readonly ChoreographySceneStreamEvent[];
  readonly continuation: readonly ChoreographySceneStreamEvent[];
  readonly clarifiedPrefix: ChoreographyFrontier;
}

interface FixtureBatch {
  readonly events: readonly ChoreographySceneStreamEvent[];
  readonly holdOpenUntilAbort: boolean;
}

type UnknownRecord = Record<string, unknown>;

function fixtureFailure(
  code: ChoreographySceneFixtureErrorCode,
  message: string,
): never {
  throw new ChoreographySceneFixtureError(code, message);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return fixtureFailure("invalid_fixture", `${field} must be an object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  expected: readonly string[],
  field: string,
): void {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  if (
    actual.length !== required.length ||
    actual.some((key, index) => key !== required[index])
  ) {
    return fixtureFailure(
      "invalid_fixture",
      `${field} must contain exactly ${required.join(", ")}`,
    );
  }
}

function canonicalEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => canonicalEqual(value, right[index]))
    );
  }
  if (
    typeof left !== "object" ||
    left === null ||
    typeof right !== "object" ||
    right === null
  ) {
    return false;
  }
  const leftRecord = left as UnknownRecord;
  const rightRecord = right as UnknownRecord;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] &&
        canonicalEqual(leftRecord[key], rightRecord[key]),
    )
  );
}

function emptyFrontier(): ChoreographyFrontier {
  return createChoreographyFrontier({
    scene: createSceneState({ revision: 0, nodes: [] }),
    semanticScene: EMPTY_CHOREOGRAPHY_SEMANTIC_SCENE,
    viewport: null,
    layout: null,
    certificateHeadSha256: null,
  });
}

function checkpoints(
  events: readonly ChoreographySceneStreamEvent[],
): readonly ChoreographySceneCheckpointEvent[] {
  return events.filter(
    (event): event is ChoreographySceneCheckpointEvent =>
      event.type === "choreography_scene_checkpoint",
  );
}

function decodeEvents(
  value: unknown,
  field: string,
): readonly ChoreographySceneStreamEvent[] {
  if (!Array.isArray(value)) {
    return fixtureFailure("invalid_fixture", `${field} must be an array`);
  }
  try {
    return Object.freeze(value.map(decodeChoreographySceneStreamEvent));
  } catch (error) {
    return fixtureFailure(
      "invalid_fixture",
      `${field} failed strict decoding: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
}

function preparePrefix(
  base: ChoreographyFrontier,
  events: readonly ChoreographySceneCheckpointEvent[],
): readonly ChoreographyFrontier[] {
  const prefixes: ChoreographyFrontier[] = [base];
  let frontier = base;
  for (const event of events) {
    frontier = prepareChoreographyCheckpoint(
      frontier,
      event,
      "cinematic",
    ).target;
    prefixes.push(frontier);
  }
  return Object.freeze(prefixes);
}

function decodeFixture(value: unknown): FixtureTranscript {
  const fixture = record(value, "choreography fixture");
  exactKeys(
    fixture,
    [
      "v",
      "fixtureId",
      "compilerVersion",
      "generation",
      "attempt",
      "baseRevision",
      "resultRevision",
      "transcript",
      "events",
      "adaptiveTranscript",
    ],
    "choreography fixture",
  );
  if (
    fixture.v !== 1 ||
    fixture.fixtureId !== "completing-the-square" ||
    fixture.generation !== 1 ||
    fixture.attempt !== 1 ||
    fixture.baseRevision !== 0 ||
    fixture.resultRevision !== 8
  ) {
    return fixtureFailure(
      "invalid_fixture",
      "choreography fixture identity or revision range is invalid",
    );
  }

  const mainEvents = decodeEvents(fixture.events, "main events");
  const main = checkpoints(mainEvents);
  if (
    mainEvents.length !== 10 ||
    mainEvents[0]?.type !== "scene_stream_started" ||
    mainEvents.at(-1)?.type !== "scene_stream_completed" ||
    main.length !== 8 ||
    main.some(
      (event, index) =>
        event.generation !== 1 ||
        event.attempt !== 1 ||
        event.sequence !== index + 1 ||
        event.baseRevision !== index ||
        event.resultRevision !== index + 1,
    )
  ) {
    return fixtureFailure(
      "invalid_fixture",
      "main choreography lifecycle is not the exact eight-checkpoint transcript",
    );
  }
  const mainPrefixes = preparePrefix(emptyFrontier(), main);
  if (
    mainPrefixes[5].semanticScene.components[0]?.kind !== "completing_square"
  ) {
    return fixtureFailure(
      "invalid_fixture",
      "missing-corner adaptive base is unavailable",
    );
  }

  const adaptive = record(fixture.adaptiveTranscript, "adaptive transcript");
  exactKeys(
    adaptive,
    [
      "baseCheckpointId",
      "clarificationDecision",
      "continuationDecision",
      "checkpointIds",
      "authoredDurationMs",
      "events",
    ],
    "adaptive transcript",
  );
  if (adaptive.baseCheckpointId !== "missing_corner") {
    return fixtureFailure(
      "invalid_fixture",
      "adaptive transcript must branch at missing_corner",
    );
  }
  const adaptiveEvents = decodeEvents(adaptive.events, "adaptive events");
  const clarification = Object.freeze(adaptiveEvents.slice(0, 3));
  const continuation = Object.freeze(adaptiveEvents.slice(3));
  const clarificationCheckpoints = checkpoints(clarification);
  const continuationCheckpoints = checkpoints(continuation);
  if (
    clarification.map((event) => event.type).join(",") !==
      "scene_stream_started,choreography_scene_checkpoint,scene_stream_completed" ||
    continuation[0]?.type !== "scene_stream_started" ||
    continuation.at(-1)?.type !== "scene_stream_completed" ||
    clarificationCheckpoints[0]?.semantic.checkpointId !== "corner_detail" ||
    continuationCheckpoints
      .map((event) => event.semantic.checkpointId)
      .join(",") !== "balance_and_complete,factor_square,solve_roots"
  ) {
    return fixtureFailure(
      "invalid_fixture",
      "adaptive choreography lifecycle is malformed",
    );
  }
  const clarifiedPrefix = preparePrefix(
    mainPrefixes[5],
    clarificationCheckpoints,
  ).at(-1)!;
  preparePrefix(clarifiedPrefix, continuationCheckpoints);

  return Object.freeze({
    main,
    mainPrefixes,
    clarification,
    continuation,
    clarifiedPrefix,
  });
}

const FIXTURE = decodeFixture(goldenFixtureValue);

function exactBase(request: ChoreographySceneStreamRequest): {
  readonly scene: SceneState;
  readonly semanticScene: ChoreographySemanticSceneState;
} {
  let scene: SceneState;
  let semanticScene: ChoreographySemanticSceneState;
  try {
    scene = createSceneState(request.baseScene);
    semanticScene = createChoreographySemanticSceneState(
      request.baseSemanticScene,
    );
  } catch (error) {
    return fixtureFailure(
      "base_mismatch",
      `fixture request base is invalid: ${
        error instanceof Error ? error.message : "unknown error"
      }`,
    );
  }
  if (
    !canonicalEqual(scene, request.baseScene) ||
    !canonicalEqual(semanticScene, request.baseSemanticScene) ||
    scene.revision !== semanticScene.revision
  ) {
    return fixtureFailure(
      "base_mismatch",
      "fixture request must contain one canonical paired frontier",
    );
  }
  return { scene, semanticScene };
}

function sameBase(
  base: ReturnType<typeof exactBase>,
  frontier: ChoreographyFrontier,
): boolean {
  return (
    canonicalEqual(base.scene, frontier.scene) &&
    canonicalEqual(base.semanticScene, frontier.semanticScene)
  );
}

function adaptCheckpoint(
  source: ChoreographySceneCheckpointEvent,
  generation: number,
  sequence: number,
): ChoreographySceneCheckpointEvent {
  const event = decodeChoreographySceneStreamEvent({
    ...source,
    generation,
    attempt: 1,
    sequence,
  });
  if (event.type !== "choreography_scene_checkpoint") {
    return fixtureFailure("invalid_fixture", "adapted checkpoint changed type");
  }
  return event;
}

function lifecycle(
  request: ChoreographySceneStreamRequest,
  source: readonly ChoreographySceneCheckpointEvent[],
): readonly ChoreographySceneStreamEvent[] {
  const adapted = source.map((event, index) =>
    adaptCheckpoint(event, request.generation, index + 1),
  );
  return Object.freeze([
    decodeChoreographySceneStreamEvent({
      type: "scene_stream_started",
      generation: request.generation,
      attempt: 1,
      baseRevision: request.baseScene.revision,
    }),
    ...adapted,
    decodeChoreographySceneStreamEvent({
      type: "scene_stream_completed",
      generation: request.generation,
      finalRevision: adapted.at(-1)!.resultRevision,
      patchCount: adapted.length,
      firstPatchMs: 0,
      totalMs: 0,
      repaired: false,
    }),
  ]);
}

function completeFixtureEvent(
  request: ChoreographySceneStreamRequest,
): readonly ChoreographySceneStreamEvent[] {
  return Object.freeze([
    decodeChoreographySceneStreamEvent({
      type: "scene_stream_started",
      generation: request.generation,
      attempt: 1,
      baseRevision: request.baseScene.revision,
    }),
    decodeChoreographySceneStreamEvent({
      type: "scene_stream_failed",
      generation: request.generation,
      attempt: 1,
      code: "choreography_fixture_complete",
      message: "The choreography fixture is complete. Reset to teach it again.",
      lastAcceptedRevision: request.baseScene.revision,
      retryable: false,
    }),
  ]);
}

function fixtureBatch(
  request: ChoreographySceneStreamRequest,
  mode: ChoreographySceneFixtureMode,
): FixtureBatch {
  const base = exactBase(request);
  if (mode === "main") {
    const prefixIndex = FIXTURE.mainPrefixes.findIndex((prefix) =>
      sameBase(base, prefix),
    );
    if (prefixIndex < 0) {
      return fixtureFailure(
        "base_mismatch",
        "main fixture request does not match a certified main prefix",
      );
    }
    if (prefixIndex === FIXTURE.main.length) {
      return Object.freeze({
        events: completeFixtureEvent(request),
        holdOpenUntilAbort: false,
      });
    }
    return Object.freeze({
      events: lifecycle(request, FIXTURE.main.slice(prefixIndex)),
      holdOpenUntilAbort: false,
    });
  }

  if (sameBase(base, FIXTURE.mainPrefixes[0])) {
    const firstFive = FIXTURE.main
      .slice(0, 5)
      .map((event, index) =>
        adaptCheckpoint(event, request.generation, index + 1),
      );
    return Object.freeze({
      events: Object.freeze([
        decodeChoreographySceneStreamEvent({
          type: "scene_stream_started",
          generation: request.generation,
          attempt: 1,
          baseRevision: 0,
        }),
        ...firstFive,
      ]),
      holdOpenUntilAbort: true,
    });
  }
  if (sameBase(base, FIXTURE.mainPrefixes[5])) {
    return Object.freeze({
      events: lifecycle(request, checkpoints(FIXTURE.clarification)),
      holdOpenUntilAbort: false,
    });
  }
  if (sameBase(base, FIXTURE.clarifiedPrefix)) {
    return Object.freeze({
      events: lifecycle(request, checkpoints(FIXTURE.continuation)),
      holdOpenUntilAbort: false,
    });
  }
  return fixtureFailure(
    "base_mismatch",
    "adaptive fixture accepts only empty, missing-corner, or clarified-corner frontiers",
  );
}

/** Return deterministic server-shaped events for fixture inspection and tests. */
export function createChoreographySceneFixtureEvents(
  request: ChoreographySceneStreamRequest,
  mode: ChoreographySceneFixtureMode = "main",
): readonly ChoreographySceneStreamEvent[] {
  return fixtureBatch(request, mode).events;
}

/**
 * Run the compiler-generated fixture through the production byte-safe decoder.
 * Adaptive generation one intentionally remains open at missing_corner until
 * the runtime interrupts it, exactly like a live teacher waiting for a question.
 */
export function createChoreographySceneFixtureRunner(
  options: ChoreographySceneFixtureRunnerOptions = {},
): ChoreographySceneStreamRunner {
  const mode = options.mode ?? "main";
  const eventDelayMs = options.eventDelayMs ?? 180;
  const chunkDelayMs = options.chunkDelayMs ?? 3;
  return async ({ request, signal, onEvent }) => {
    const batch = fixtureBatch(request, mode);
    const response = createFixtureSseResponse(batch.events, signal, {
      eventDelayMs,
      chunkDelayMs,
      idPrefix: "choreography-fixture",
      holdOpenUntilAbort: batch.holdOpenUntilAbort,
    });
    await consumeChoreographySceneStreamResponse(response, onEvent);
  };
}
