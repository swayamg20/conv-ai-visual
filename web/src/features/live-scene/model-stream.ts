import {
  decodeSemanticScenePatchEvent,
  type SceneState,
  type SemanticScenePatchEvent,
  type SemanticSceneState,
} from "@/lib/live-scene";
import {
  decodeScenePatchEvent,
  LIVE_SCENE_MAX_ACCEPTED_PATCHES,
  type ScenePatchEvent,
} from "@/lib/live-scene/patch";
import { LiveSceneSseDecoder } from "@/lib/live-scene/sse";

export interface SceneStreamStartedEvent {
  readonly type: "scene_stream_started";
  readonly generation: number;
  readonly attempt: number;
  readonly baseRevision: number;
}

export interface SceneStreamRepairingEvent {
  readonly type: "scene_stream_repairing";
  readonly generation: number;
  readonly fromAttempt: number;
  readonly toAttempt: number;
  readonly lastAcceptedRevision: number;
  readonly message: string;
}

export interface SceneStreamCompletedEvent {
  readonly type: "scene_stream_completed";
  readonly generation: number;
  readonly finalRevision: number;
  readonly patchCount: number;
  readonly firstPatchMs: number;
  readonly totalMs: number;
  readonly repaired: boolean;
}

export interface SceneStreamFailedEvent {
  readonly type: "scene_stream_failed";
  readonly generation: number;
  readonly attempt: number;
  readonly code: string;
  readonly message: string;
  readonly lastAcceptedRevision: number;
  readonly retryable: boolean;
}

export type SemanticSceneDeclineReason =
  | "unsupported_intent"
  | "no_forward_progress";

export interface SemanticSceneStreamDeclinedEvent {
  readonly type: "semantic_scene_stream_declined";
  readonly generation: number;
  readonly attempt: number;
  readonly finalRevision: number;
  readonly reasonCode: SemanticSceneDeclineReason;
  readonly message: string;
}

export type SceneStreamEvent =
  | SceneStreamStartedEvent
  | ScenePatchEvent
  | SceneStreamRepairingEvent
  | SceneStreamCompletedEvent
  | SceneStreamFailedEvent;

type SceneStreamLifecycleEvent =
  | SceneStreamStartedEvent
  | SceneStreamRepairingEvent
  | SceneStreamCompletedEvent
  | SceneStreamFailedEvent;

export type SemanticSceneStreamEvent =
  | SceneStreamStartedEvent
  | SemanticScenePatchEvent
  | SceneStreamRepairingEvent
  | SceneStreamCompletedEvent
  | SemanticSceneStreamDeclinedEvent
  | SceneStreamFailedEvent;

export interface SceneStreamRequest {
  readonly prompt: string;
  readonly generation: number;
  readonly baseScene: SceneState;
}

export interface SemanticSceneStreamRequest {
  readonly prompt: string;
  readonly generation: number;
  readonly baseScene: SceneState;
  readonly baseSemanticScene: SemanticSceneState;
}

export interface RunSceneStreamOptions {
  readonly apiUrl: string;
  readonly endpoint?: SceneStreamEndpoint;
  readonly request: SceneStreamRequest;
  readonly signal: AbortSignal;
  readonly onEvent: (event: SceneStreamEvent) => void;
  readonly headers?: Readonly<Record<string, string>>;
  readonly fetchImpl?: typeof fetch;
}

export interface SemanticSceneStreamRunInvocation {
  readonly request: SemanticSceneStreamRequest;
  readonly signal: AbortSignal;
  readonly onEvent: (event: SemanticSceneStreamEvent) => void;
}

export type SemanticSceneStreamRunner = (
  invocation: SemanticSceneStreamRunInvocation
) => Promise<void>;

export interface RunSemanticSceneStreamOptions
  extends SemanticSceneStreamRunInvocation {
  readonly apiUrl: string;
  readonly headers?: Readonly<Record<string, string>>;
  readonly fetchImpl?: typeof fetch;
}

export type SceneStreamEndpoint = "product" | "developmentLab";

const SCENE_STREAM_PATHS: Readonly<Record<SceneStreamEndpoint, string>> = {
  product: "/api/live-scenes/stream",
  developmentLab: "/api/live-scenes/lab/stream",
};
const SEMANTIC_SCENE_STREAM_PATH = "/api/live-scenes/lab/semantic/stream";

type UnknownRecord = Record<string, unknown>;

export class SceneModelStreamError extends Error {
  readonly code:
    | "http_error"
    | "missing_body"
    | "invalid_json"
    | "invalid_event";

  constructor(
    code: SceneModelStreamError["code"],
    message: string
  ) {
    super(message);
    this.name = "SceneModelStreamError";
    this.code = code;
  }
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new SceneModelStreamError("invalid_event", `${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new SceneModelStreamError("invalid_event", `${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  field: string
): void {
  const allowed = new Set(required);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) {
      throw new SceneModelStreamError(
        "invalid_event",
        `${field} contains unknown field ${key}`
      );
    }
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) {
      throw new SceneModelStreamError(
        "invalid_event",
        `${field} is missing field ${key}`
      );
    }
  }
}

function safeInteger(
  value: unknown,
  field: string,
  minimum: number,
  maximum = Number.MAX_SAFE_INTEGER
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    throw new SceneModelStreamError(
      "invalid_event",
      `${field} must be a safe integer between ${minimum} and ${maximum}`
    );
  }
  return value as number;
}

function milliseconds(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new SceneModelStreamError(
      "invalid_event",
      `${field} must be a finite non-negative number`
    );
  }
  return value;
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new SceneModelStreamError("invalid_event", `${field} must be a boolean`);
  }
  return value;
}

function boundedString(
  value: unknown,
  field: string,
  maximum: number,
  stripWhitespace = true
): string {
  if (typeof value !== "string") {
    throw new SceneModelStreamError(
      "invalid_event",
      `${field} must be a non-empty string of at most ${maximum} characters`
    );
  }
  const normalized = stripWhitespace ? value.trim() : value;
  if (normalized.trim().length === 0 || normalized.length > maximum) {
    throw new SceneModelStreamError(
      "invalid_event",
      `${field} must be a non-empty string of at most ${maximum} characters`
    );
  }
  return normalized;
}

function semanticSceneDeclineReason(value: unknown): SemanticSceneDeclineReason {
  const reason = boundedString(value, "declined reasonCode", 64, false);
  if (reason !== "unsupported_intent" && reason !== "no_forward_progress") {
    throw new SceneModelStreamError(
      "invalid_event",
      "declined reasonCode is unsupported"
    );
  }
  return reason;
}

function decodeSemanticSceneStreamDeclinedEvent(
  value: unknown
): SemanticSceneStreamDeclinedEvent {
  const input = record(value, "semantic declined event");
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "finalRevision",
      "reasonCode",
      "message",
    ],
    "semantic declined event"
  );
  if (input.type !== "semantic_scene_stream_declined") {
    throw new SceneModelStreamError(
      "invalid_event",
      "semantic declined event has an unsupported type"
    );
  }
  return Object.freeze({
    type: input.type,
    generation: safeInteger(input.generation, "declined generation", 1),
    attempt: safeInteger(input.attempt, "declined attempt", 1, 2),
    finalRevision: safeInteger(input.finalRevision, "declined finalRevision", 0),
    reasonCode: semanticSceneDeclineReason(input.reasonCode),
    message: boundedString(input.message, "declined message", 512),
  });
}

function decodeSceneStreamEventWithPatch<PatchEvent>(
  value: unknown,
  patchType: string,
  decodePatch: (input: unknown) => PatchEvent
): SceneStreamLifecycleEvent | PatchEvent {
  const input = record(value, "scene stream event");
  const type = input.type;

  if (type === patchType) {
    try {
      return decodePatch(input);
    } catch (error) {
      throw new SceneModelStreamError(
        "invalid_event",
        error instanceof Error ? error.message : "scene patch event is invalid"
      );
    }
  }

  if (type === "scene_stream_started") {
    exactKeys(input, ["type", "generation", "attempt", "baseRevision"], "started event");
    return Object.freeze({
      type,
      generation: safeInteger(input.generation, "started generation", 1),
      attempt: safeInteger(input.attempt, "started attempt", 1, 2),
      baseRevision: safeInteger(input.baseRevision, "started baseRevision", 0),
    });
  }

  if (type === "scene_stream_repairing") {
    exactKeys(
      input,
      [
        "type",
        "generation",
        "fromAttempt",
        "toAttempt",
        "lastAcceptedRevision",
        "message",
      ],
      "repairing event"
    );
    const fromAttempt = safeInteger(input.fromAttempt, "repairing fromAttempt", 1, 1);
    const toAttempt = safeInteger(input.toAttempt, "repairing toAttempt", 2, 2);
    if (toAttempt !== fromAttempt + 1) {
      throw new SceneModelStreamError(
        "invalid_event",
        "repairing toAttempt must follow fromAttempt"
      );
    }
    return Object.freeze({
      type,
      generation: safeInteger(input.generation, "repairing generation", 1),
      fromAttempt,
      toAttempt,
      lastAcceptedRevision: safeInteger(
        input.lastAcceptedRevision,
        "repairing lastAcceptedRevision",
        0
      ),
      message: boundedString(input.message, "repairing message", 512),
    });
  }

  if (type === "scene_stream_completed") {
    exactKeys(
      input,
      [
        "type",
        "generation",
        "finalRevision",
        "patchCount",
        "firstPatchMs",
        "totalMs",
        "repaired",
      ],
      "completed event"
    );
    const firstPatchMs = milliseconds(input.firstPatchMs, "completed firstPatchMs");
    const totalMs = milliseconds(input.totalMs, "completed totalMs");
    if (totalMs < firstPatchMs) {
      throw new SceneModelStreamError(
        "invalid_event",
        "completed totalMs must not precede firstPatchMs"
      );
    }
    return Object.freeze({
      type,
      generation: safeInteger(input.generation, "completed generation", 1),
      finalRevision: safeInteger(input.finalRevision, "completed finalRevision", 1),
      patchCount: safeInteger(
        input.patchCount,
        "completed patchCount",
        1,
        LIVE_SCENE_MAX_ACCEPTED_PATCHES
      ),
      firstPatchMs,
      totalMs,
      repaired: booleanValue(input.repaired, "completed repaired"),
    });
  }

  if (type === "scene_stream_failed") {
    exactKeys(
      input,
      [
        "type",
        "generation",
        "attempt",
        "code",
        "message",
        "lastAcceptedRevision",
        "retryable",
      ],
      "failed event"
    );
    const code = boundedString(input.code, "failed code", 64, false);
    if (!/^[a-z][a-z0-9_]*$/.test(code)) {
      throw new SceneModelStreamError("invalid_event", "failed code has an unsafe value");
    }
    return Object.freeze({
      type,
      generation: safeInteger(input.generation, "failed generation", 1),
      attempt: safeInteger(input.attempt, "failed attempt", 1, 2),
      code,
      message: boundedString(input.message, "failed message", 512),
      lastAcceptedRevision: safeInteger(
        input.lastAcceptedRevision,
        "failed lastAcceptedRevision",
        0
      ),
      retryable: booleanValue(input.retryable, "failed retryable"),
    });
  }

  throw new SceneModelStreamError("invalid_event", "event type is unsupported");
}

/** Decode one complete raw-patch SSE data value at the browser trust boundary. */
export function decodeSceneStreamEvent(value: unknown): SceneStreamEvent {
  return decodeSceneStreamEventWithPatch(
    value,
    "scene_patch",
    decodeScenePatchEvent
  );
}

/** Decode one complete compiler-certified semantic SSE data value. */
export function decodeSemanticSceneStreamEvent(
  value: unknown
): SemanticSceneStreamEvent {
  const input = record(value, "semantic scene stream event");
  if (input.type === "semantic_scene_stream_declined") {
    return decodeSemanticSceneStreamDeclinedEvent(input);
  }
  return decodeSceneStreamEventWithPatch(
    input,
    "semantic_scene_patch",
    decodeSemanticScenePatchEvent
  );
}

export function parseSceneStreamEvent(data: string): SceneStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    throw new SceneModelStreamError("invalid_json", "SSE data must be valid JSON");
  }
  return decodeSceneStreamEvent(value);
}

export function parseSemanticSceneStreamEvent(
  data: string
): SemanticSceneStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    throw new SceneModelStreamError("invalid_json", "SSE data must be valid JSON");
  }
  return decodeSemanticSceneStreamEvent(value);
}

async function consumeDecodedSceneStreamResponse<Event>(
  response: Response,
  onEvent: (event: Event) => void,
  parseEvent: (data: string) => Event
): Promise<void> {
  if (!response.ok) {
    throw new SceneModelStreamError(
      "http_error",
      `Scene stream request failed with HTTP ${response.status}`
    );
  }
  if (!response.body) {
    throw new SceneModelStreamError("missing_body", "Scene stream response had no body");
  }

  const decoder = new LiveSceneSseDecoder();
  const reader = response.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const event of decoder.push(value)) {
        onEvent(parseEvent(event.data));
      }
    }
    for (const event of decoder.finish()) {
      onEvent(parseEvent(event.data));
    }
  } catch (error) {
    try {
      await reader.cancel(error);
    } catch {
      // The stream/parse/callback failure is the error callers need.
    }
    throw error;
  } finally {
    reader.releaseLock();
  }
}

/** Consume a raw-patch response with a stateful UTF-8/SSE decoder. */
export async function consumeSceneStreamResponse(
  response: Response,
  onEvent: (event: SceneStreamEvent) => void
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseSceneStreamEvent
  );
}

/** Consume a semantic response through the same byte-safe SSE transport. */
export async function consumeSemanticSceneStreamResponse(
  response: Response,
  onEvent: (event: SemanticSceneStreamEvent) => void
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseSemanticSceneStreamEvent
  );
}

/** Start one authenticated provider stream without importing Firebase into fixture code. */
export async function runSceneModelStream(
  options: RunSceneStreamOptions
): Promise<void> {
  const requestFetch = options.fetchImpl ?? fetch;
  const endpointPath = SCENE_STREAM_PATHS[options.endpoint ?? "product"];
  const response = await requestFetch(
    `${options.apiUrl.replace(/\/$/, "")}${endpointPath}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      body: JSON.stringify(options.request),
      signal: options.signal,
    }
  );
  await consumeSceneStreamResponse(response, options.onEvent);
}

/** Start one compiler-certified semantic stream against the development lab. */
export async function runSemanticSceneModelStream(
  options: RunSemanticSceneStreamOptions
): Promise<void> {
  const requestFetch = options.fetchImpl ?? fetch;
  const response = await requestFetch(
    `${options.apiUrl.replace(/\/$/, "")}${SEMANTIC_SCENE_STREAM_PATH}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      body: JSON.stringify(options.request),
      signal: options.signal,
    }
  );
  await consumeSemanticSceneStreamResponse(response, options.onEvent);
}
