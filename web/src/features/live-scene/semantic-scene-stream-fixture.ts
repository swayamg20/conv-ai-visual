import goldenTranscriptValue from "./fixtures/pythagorean-area-identity.v1.json";

import {
  PYTHAGOREAN_ROLE_ORDER,
  applySemanticScenePatch,
  createSemanticSceneState,
  createSceneState,
  type SceneState,
  type SemanticScenePatchEvent,
  type SemanticSceneState,
} from "@/lib/live-scene";

import {
  consumeSemanticSceneStreamResponse,
  decodeSemanticSceneStreamEvent,
  type SemanticSceneStreamEvent,
  type SemanticSceneStreamRequest,
  type SemanticSceneStreamRunner,
} from "./model-stream";

export const SEMANTIC_SCENE_FIXTURE_MODES = ["normal", "stale"] as const;

export type SemanticSceneFixtureMode =
  (typeof SEMANTIC_SCENE_FIXTURE_MODES)[number];

export interface SemanticSceneFixtureRunnerOptions {
  readonly mode?: SemanticSceneFixtureMode;
  /** Delay between lifecycle/atom events. Non-zero lab values make progress visible. */
  readonly eventDelayMs?: number;
  /** Delay between deliberately awkward UTF-8/SSE transport chunks. */
  readonly chunkDelayMs?: number;
}

export type SemanticSceneFixtureErrorCode =
  | "invalid_fixture"
  | "base_mismatch";

export class SemanticSceneFixtureError extends Error {
  readonly code: SemanticSceneFixtureErrorCode;

  constructor(code: SemanticSceneFixtureErrorCode, message: string) {
    super(message);
    this.name = "SemanticSceneFixtureError";
    this.code = code;
  }
}

interface GoldenPrefix {
  readonly scene: SceneState;
  readonly semanticScene: SemanticSceneState;
}

interface GoldenTranscript {
  readonly generation: number;
  readonly baseRevision: number;
  readonly events: readonly SemanticScenePatchEvent[];
  readonly prefixes: readonly GoldenPrefix[];
}

type UnknownRecord = Record<string, unknown>;

function fixtureFailure(
  code: SemanticSceneFixtureErrorCode,
  message: string
): never {
  throw new SemanticSceneFixtureError(code, message);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fixtureFailure("invalid_fixture", `${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fixtureFailure("invalid_fixture", `${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function requireExactKeys(
  value: UnknownRecord,
  expected: readonly string[],
  field: string
): void {
  const actual = Object.keys(value).sort();
  const required = [...expected].sort();
  if (
    actual.length !== required.length ||
    !actual.every((key, index) => key === required[index])
  ) {
    fixtureFailure(
      "invalid_fixture",
      `${field} must contain exactly ${required.join(", ")}`
    );
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "unknown validation error";
}

/** JSON-value equality with object-key order ignored and no field elision. */
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
        canonicalEqual(leftRecord[key], rightRecord[key])
    )
  );
}

function decodeGoldenTranscript(value: unknown): GoldenTranscript {
  const input = record(value, "semantic fixture transcript");
  requireExactKeys(
    input,
    ["v", "fixtureId", "componentId", "generation", "baseRevision", "events"],
    "semantic fixture transcript"
  );
  if (
    input.v !== 1 ||
    input.fixtureId !== "pythagorean-area-identity" ||
    input.componentId !== "areas" ||
    input.generation !== 1 ||
    input.baseRevision !== 0 ||
    !Array.isArray(input.events) ||
    input.events.length !== PYTHAGOREAN_ROLE_ORDER.length
  ) {
    fixtureFailure(
      "invalid_fixture",
      "semantic fixture transcript identity, base, or atom count is invalid"
    );
  }

  let scene = createSceneState({ revision: input.baseRevision, nodes: [] });
  let semanticScene = createSemanticSceneState({
    revision: input.baseRevision,
    components: [],
  });
  const events: SemanticScenePatchEvent[] = [];
  const prefixes: GoldenPrefix[] = [Object.freeze({ scene, semanticScene })];

  for (const [index, source] of input.events.entries()) {
    let decoded: SemanticSceneStreamEvent;
    try {
      decoded = decodeSemanticSceneStreamEvent(source);
    } catch (error) {
      fixtureFailure(
        "invalid_fixture",
        `semantic fixture atom ${index + 1} is invalid: ${errorMessage(error)}`
      );
    }
    if (
      decoded.type !== "semantic_scene_patch" ||
      decoded.generation !== input.generation ||
      decoded.attempt !== 1 ||
      decoded.sequence !== index + 1 ||
      decoded.baseRevision !== input.baseRevision + index ||
      decoded.resultRevision !== input.baseRevision + index + 1 ||
      decoded.semantic.componentId !== input.componentId ||
      decoded.semantic.role !== PYTHAGOREAN_ROLE_ORDER[index] ||
      decoded.semantic.atomOrdinal !== index + 1
    ) {
      fixtureFailure(
        "invalid_fixture",
        `semantic fixture atom ${index + 1} does not occupy its exact golden prefix position`
      );
    }

    try {
      const applied = applySemanticScenePatch(scene, semanticScene, decoded);
      scene = applied.scene;
      semanticScene = applied.semanticScene;
    } catch (error) {
      fixtureFailure(
        "invalid_fixture",
        `semantic fixture atom ${index + 1} cannot extend the golden prefix: ${errorMessage(error)}`
      );
    }
    events.push(decoded);
    prefixes.push(Object.freeze({ scene, semanticScene }));
  }

  return Object.freeze({
    generation: input.generation,
    baseRevision: input.baseRevision,
    events: Object.freeze(events),
    prefixes: Object.freeze(prefixes),
  });
}

// Decode and apply the backend-generated artifact once at the import boundary.
// A malformed or internally inconsistent checked-in fixture makes the module
// fail closed before it can masquerade as a verified visual stream.
const GOLDEN_TRANSCRIPT = decodeGoldenTranscript(goldenTranscriptValue);

function exactGoldenPrefix(request: SemanticSceneStreamRequest): {
  readonly prefix: GoldenPrefix;
  readonly nextAtomIndex: number;
} {
  let scene: SceneState;
  let semanticScene: SemanticSceneState;
  try {
    scene = createSceneState(request.baseScene);
    semanticScene = createSemanticSceneState(request.baseSemanticScene);
  } catch (error) {
    fixtureFailure(
      "base_mismatch",
      `fixture request base is invalid: ${errorMessage(error)}`
    );
  }

  if (
    !canonicalEqual(request.baseScene, scene) ||
    !canonicalEqual(request.baseSemanticScene, semanticScene)
  ) {
    fixtureFailure(
      "base_mismatch",
      "fixture request base must already be a canonical paired scene snapshot"
    );
  }
  if (scene.revision !== semanticScene.revision) {
    fixtureFailure(
      "base_mismatch",
      "fixture request low-level and semantic revisions must match"
    );
  }

  const nextAtomIndex = scene.revision - GOLDEN_TRANSCRIPT.baseRevision;
  const prefix = GOLDEN_TRANSCRIPT.prefixes[nextAtomIndex];
  if (
    !prefix ||
    !canonicalEqual(scene, prefix.scene) ||
    !canonicalEqual(semanticScene, prefix.semanticScene)
  ) {
    fixtureFailure(
      "base_mismatch",
      "fixture request must equal one exact low-level and semantic golden prefix"
    );
  }
  return { prefix, nextAtomIndex };
}

function decodedLifecycleEvent(value: unknown): SemanticSceneStreamEvent {
  return decodeSemanticSceneStreamEvent(value);
}

function adaptedAtom(
  source: SemanticScenePatchEvent,
  generation: number,
  sequence: number
): SemanticScenePatchEvent {
  // generation/attempt/stream sequence are transport envelope fields, not
  // compiler-certificate body fields. All patch, semantic, revision, receipt,
  // and certificate values stay byte-for-value identical to the golden atom.
  const decoded = decodeSemanticSceneStreamEvent({
    ...source,
    generation,
    attempt: 1,
    sequence,
  });
  if (decoded.type !== "semantic_scene_patch") {
    return fixtureFailure("invalid_fixture", "adapted fixture atom changed discriminator");
  }
  return decoded;
}

/**
 * Emit a server-shaped lifecycle around exactly the missing golden suffix.
 *
 * The fixture never rebases or recompiles certificates. A request must present
 * one exact paired prefix previously derived from this checked-in artifact.
 */
export function createSemanticSceneFixtureEvents(
  request: SemanticSceneStreamRequest
): readonly SemanticSceneStreamEvent[] {
  const { nextAtomIndex } = exactGoldenPrefix(request);
  const started = decodedLifecycleEvent({
    type: "scene_stream_started",
    generation: request.generation,
    attempt: 1,
    baseRevision: request.baseScene.revision,
  });
  const sourceSuffix = GOLDEN_TRANSCRIPT.events.slice(nextAtomIndex);

  // The production lifecycle contract requires at least one patch before a
  // completion event. A fully complete golden prefix therefore gets an honest,
  // non-retryable terminal event instead of a forged zero-patch completion.
  if (sourceSuffix.length === 0) {
    return Object.freeze([
      started,
      decodedLifecycleEvent({
        type: "scene_stream_failed",
        generation: request.generation,
        attempt: 1,
        code: "semantic_fixture_complete",
        message: "The verified fixture is already fully presented. Reset to teach it again.",
        lastAcceptedRevision: request.baseScene.revision,
        retryable: false,
      }),
    ]);
  }

  const atoms = sourceSuffix.map((source, index) =>
    adaptedAtom(source, request.generation, index + 1)
  );
  return Object.freeze([
    started,
    ...atoms,
    decodedLifecycleEvent({
      type: "scene_stream_completed",
      generation: request.generation,
      finalRevision: atoms.at(-1)!.resultRevision,
      patchCount: atoms.length,
      firstPatchMs: 40,
      totalMs: 40 + atoms.length * 35,
      repaired: false,
    }),
  ]);
}

function abortException(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("Aborted", "AbortError");
  }
  return Object.assign(new Error("Aborted"), { name: "AbortError" });
}

function wait(
  milliseconds: number,
  signal: AbortSignal,
  ignoreAbort: boolean
): Promise<void> {
  if (!ignoreAbort && signal.aborted) return Promise.reject(abortException());
  if (milliseconds <= 0) return Promise.resolve();
  if (ignoreAbort) {
    return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
  }
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(abortException());
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function splitFrame(frame: Uint8Array): readonly Uint8Array[] {
  const offsets = [
    1,
    13,
    Math.max(14, Math.floor(frame.length * 0.53)),
    frame.length - 3,
  ]
    .filter(
      (offset, index, values) =>
        offset > 0 && offset < frame.length && values.indexOf(offset) === index
    )
    .sort((left, right) => left - right);
  const chunks: Uint8Array[] = [];
  let start = 0;
  for (const end of [...offsets, frame.length]) {
    chunks.push(frame.slice(start, end));
    start = end;
  }
  return chunks;
}

function fixtureResponse(
  events: readonly SemanticSceneStreamEvent[],
  signal: AbortSignal,
  options: Required<
    Pick<SemanticSceneFixtureRunnerOptions, "eventDelayMs" | "chunkDelayMs">
  >,
  ignoreAbort: boolean
): Response {
  const encoder = new TextEncoder();
  let stopped = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      void (async () => {
        try {
          for (const [index, event] of events.entries()) {
            await wait(
              index === 0 ? Math.min(options.eventDelayMs, 80) : options.eventDelayMs,
              signal,
              ignoreAbort
            );
            if (stopped) return;
            if (!ignoreAbort && signal.aborted) throw abortException();
            const frame = encoder.encode(
              `id: semantic-fixture-${index + 1}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
            );
            for (const chunk of splitFrame(frame)) {
              if (stopped) return;
              if (!ignoreAbort && signal.aborted) throw abortException();
              controller.enqueue(chunk);
              await wait(options.chunkDelayMs, signal, ignoreAbort);
            }
          }
          if (!stopped) {
            stopped = true;
            controller.close();
          }
        } catch (error) {
          if (!stopped) {
            stopped = true;
            controller.error(error);
          }
        }
      })();
    },
    cancel() {
      stopped = true;
    },
  });

  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
  });
}

/**
 * Build a zero-network semantic runner that still traverses the production
 * UTF-8/SSE decoder. `stale` deliberately ignores AbortSignal so runtime token
 * rejection can be exercised without a provider, Firebase, or fetch call.
 */
export function createSemanticSceneFixtureRunner(
  options: SemanticSceneFixtureRunnerOptions = {}
): SemanticSceneStreamRunner {
  const mode = options.mode ?? "normal";
  const eventDelayMs = options.eventDelayMs ?? (mode === "stale" ? 520 : 220);
  const chunkDelayMs = options.chunkDelayMs ?? 3;
  return async ({ request, signal, onEvent }) => {
    const events = createSemanticSceneFixtureEvents(request);
    const response = fixtureResponse(
      events,
      signal,
      { eventDelayMs, chunkDelayMs },
      mode === "stale"
    );
    await consumeSemanticSceneStreamResponse(response, onEvent);
  };
}
