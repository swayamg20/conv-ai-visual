import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  decodeCompiledCheckpointV2,
  type CheckpointCompilerCertificateV2,
  type CheckpointVerificationReceiptV2,
  type CompiledCheckpointV2,
  type CompletingSquareCheckpointId,
} from "@/lib/live-scene/checkpoint";
import type {
  ChoreographyPlanV1,
  PresentationCheckpointV1,
  RoutedChoreographyBeatV2,
} from "@/lib/live-scene/choreography";
import type { ScenePatchDraft } from "@/lib/live-scene/patch";
import type { PythagoreanAreaIdentityState } from "@/lib/live-scene/semantic";
import type { SceneState } from "@/lib/live-scene/types";

import {
  consumeDecodedSceneStreamResponse,
  decodeSceneStreamEventWithPatch,
  SceneModelStreamError,
  type SceneStreamCompletedEvent,
  type SceneStreamEndpoint,
  type SceneStreamFailedEvent,
  type SceneStreamRepairingEvent,
  type SceneStreamStartedEvent,
} from "./model-stream";

export const COMPLETING_SQUARE_MAIN_CHECKPOINTS =
  COMPLETING_SQUARE_CHECKPOINT_IDS.filter(
    (checkpoint): checkpoint is CompletingSquareMainCheckpoint =>
      checkpoint !== "corner_detail",
  );

export type CompletingSquareMainCheckpoint = Exclude<
  CompletingSquareCheckpointId,
  "corner_detail"
>;

export interface CompletingSquareStateV2 {
  readonly kind: "completing_square";
  readonly id: string;
  readonly lastMainCheckpoint: CompletingSquareMainCheckpoint | null;
  readonly cornerClarified: boolean;
}

export interface ChoreographySemanticSceneState {
  readonly revision: number;
  readonly components: readonly (
    PythagoreanAreaIdentityState | CompletingSquareStateV2
  )[];
  readonly certificateHeadSha256?: string | null;
}

export interface CheckpointSemanticMetadataV2 {
  readonly beat: RoutedChoreographyBeatV2;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly resultComponent: CompletingSquareStateV2;
  readonly semanticBaseRevision: number;
  readonly semanticResultRevision: number;
  readonly receipt: CheckpointVerificationReceiptV2;
  readonly presentation: PresentationCheckpointV1;
  readonly choreography: ChoreographyPlanV1;
  readonly certificate: CheckpointCompilerCertificateV2;
}

export interface ChoreographySceneCheckpointEvent {
  readonly type: "choreography_scene_checkpoint";
  readonly generation: number;
  readonly attempt: number;
  readonly sequence: number;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly patch: ScenePatchDraft;
  readonly semantic: CheckpointSemanticMetadataV2;
}

export interface ChoreographySceneStreamDeclinedEvent {
  readonly type: "choreography_scene_stream_declined";
  readonly generation: number;
  readonly attempt: number;
  readonly finalRevision: number;
  readonly reasonCode: "unsupported_intent" | "no_forward_progress";
  readonly message: string;
}

export type ChoreographySceneStreamEvent =
  | SceneStreamStartedEvent
  | ChoreographySceneCheckpointEvent
  | ChoreographySceneStreamDeclinedEvent
  | SceneStreamRepairingEvent
  | SceneStreamCompletedEvent
  | SceneStreamFailedEvent;

export interface ChoreographySceneStreamRequest {
  readonly prompt: string;
  readonly generation: number;
  readonly baseScene: SceneState;
  readonly baseSemanticScene: ChoreographySemanticSceneState;
}

export interface ChoreographySceneStreamRunInvocation {
  readonly request: ChoreographySceneStreamRequest;
  readonly signal: AbortSignal;
  readonly onEvent: (event: ChoreographySceneStreamEvent) => void;
}

export type ChoreographySceneStreamRunner = (
  invocation: ChoreographySceneStreamRunInvocation,
) => Promise<void>;

export interface RunChoreographySceneStreamOptions extends ChoreographySceneStreamRunInvocation {
  readonly apiUrl: string;
  readonly endpoint: SceneStreamEndpoint;
  readonly headers?: Readonly<Record<string, string>>;
  readonly fetchImpl?: typeof fetch;
}

type UnknownRecord = Record<string, unknown>;

const MAX_SAFE_SEQUENCE = Number.MAX_SAFE_INTEGER;
const MAX_CHECKPOINTS = 8;
const ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;

function fail(message: string): never {
  throw new SceneModelStreamError("invalid_event", `choreography ${message}`);
}

function record(value: unknown, field: string): UnknownRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(`${field} must be an object`);
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    fail(`${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function exactKeys(
  value: UnknownRecord,
  required: readonly string[],
  field: string,
): void {
  const allowed = new Set(required);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(`${field} contains unknown field ${key}`);
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`${field} is missing field ${key}`);
  }
}

function integer(
  value: unknown,
  field: string,
  minimum: number,
  maximum = MAX_SAFE_SEQUENCE,
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  ) {
    fail(`${field} must be a safe integer between ${minimum} and ${maximum}`);
  }
  return value as number;
}

function identifier(value: unknown, field: string): string {
  if (typeof value !== "string" || !ID_PATTERN.test(value)) {
    fail(`${field} has an unsafe identifier`);
  }
  return value;
}

function boundedString(value: unknown, field: string): string {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    [...value].length > 512
  ) {
    fail(`${field} must be a non-empty string of at most 512 characters`);
  }
  return value.trim();
}

function booleanValue(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") fail(`${field} must be a boolean`);
  return value;
}

function decodeResultComponent(value: unknown): CompletingSquareStateV2 {
  const input = record(value, "resultComponent");
  exactKeys(
    input,
    ["kind", "id", "lastMainCheckpoint", "cornerClarified"],
    "resultComponent",
  );
  if (input.kind !== "completing_square") {
    fail("resultComponent kind must equal completing_square");
  }
  const lastMainCheckpoint =
    input.lastMainCheckpoint === null
      ? null
      : COMPLETING_SQUARE_MAIN_CHECKPOINTS.find(
          (checkpoint) => checkpoint === input.lastMainCheckpoint,
        );
  if (lastMainCheckpoint === undefined) {
    fail("resultComponent lastMainCheckpoint is unsupported");
  }
  const cornerClarified = booleanValue(
    input.cornerClarified,
    "resultComponent cornerClarified",
  );
  if (
    cornerClarified &&
    (lastMainCheckpoint === null ||
      COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(lastMainCheckpoint) <
        COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf("missing_corner"))
  ) {
    fail(
      "resultComponent cornerClarified requires the missing_corner frontier",
    );
  }
  return Object.freeze({
    kind: "completing_square",
    id: identifier(input.id, "resultComponent id"),
    lastMainCheckpoint,
    cornerClarified,
  });
}

function decodeSemanticMetadata(
  value: unknown,
  patch: unknown,
): CheckpointSemanticMetadataV2 {
  const input = record(value, "semantic metadata");
  exactKeys(
    input,
    [
      "beat",
      "checkpointId",
      "resultComponent",
      "semanticBaseRevision",
      "semanticResultRevision",
      "receipt",
      "presentation",
      "choreography",
      "certificate",
    ],
    "semantic metadata",
  );
  const compiled: CompiledCheckpointV2 = decodeCompiledCheckpointV2({
    beat: input.beat,
    checkpointId: input.checkpointId,
    patch,
    receipt: input.receipt,
    presentation: input.presentation,
    choreography: input.choreography,
    certificate: input.certificate,
  });
  const resultComponent = decodeResultComponent(input.resultComponent);
  const semanticBaseRevision = integer(
    input.semanticBaseRevision,
    "semantic metadata semanticBaseRevision",
    0,
  );
  const semanticResultRevision = integer(
    input.semanticResultRevision,
    "semantic metadata semanticResultRevision",
    1,
  );
  if (semanticResultRevision !== semanticBaseRevision + 1) {
    fail("semantic metadata revisions must advance exactly once");
  }
  if (resultComponent.id !== compiled.beat.componentId) {
    fail("resultComponent id must match routed beat componentId");
  }
  if (compiled.checkpointId === "corner_detail") {
    if (
      resultComponent.lastMainCheckpoint !== "missing_corner" ||
      !resultComponent.cornerClarified
    ) {
      fail("corner_detail must settle the clarified missing_corner frontier");
    }
  } else if (resultComponent.lastMainCheckpoint !== compiled.checkpointId) {
    fail("main checkpoint must match resultComponent lastMainCheckpoint");
  }
  const body = compiled.certificate.body;
  if (
    body.baseRevision !== semanticBaseRevision ||
    body.resultRevision !== semanticResultRevision
  ) {
    fail("certificate revisions must match semantic revisions");
  }
  return Object.freeze({
    beat: compiled.beat,
    checkpointId: compiled.checkpointId,
    resultComponent,
    semanticBaseRevision,
    semanticResultRevision,
    receipt: compiled.receipt,
    presentation: compiled.presentation,
    choreography: compiled.choreography,
    certificate: compiled.certificate,
  });
}

function decodeCheckpointEvent(
  value: unknown,
): ChoreographySceneCheckpointEvent {
  const input = record(value, "checkpoint event");
  exactKeys(
    input,
    [
      "type",
      "generation",
      "attempt",
      "sequence",
      "baseRevision",
      "resultRevision",
      "patch",
      "semantic",
    ],
    "checkpoint event",
  );
  if (input.type !== "choreography_scene_checkpoint") {
    fail("checkpoint event type is unsupported");
  }
  const baseRevision = integer(
    input.baseRevision,
    "checkpoint baseRevision",
    0,
  );
  const resultRevision = integer(
    input.resultRevision,
    "checkpoint resultRevision",
    1,
  );
  if (resultRevision !== baseRevision + 1) {
    fail("checkpoint revisions must advance exactly once");
  }
  const semantic = decodeSemanticMetadata(input.semantic, input.patch);
  if (
    semantic.semanticBaseRevision !== baseRevision ||
    semantic.semanticResultRevision !== resultRevision
  ) {
    fail("semantic and low-level checkpoint revisions must match");
  }
  return Object.freeze({
    type: "choreography_scene_checkpoint",
    generation: integer(input.generation, "checkpoint generation", 1),
    attempt: integer(input.attempt, "checkpoint attempt", 1, 2),
    sequence: integer(
      input.sequence,
      "checkpoint sequence",
      1,
      MAX_CHECKPOINTS,
    ),
    baseRevision,
    resultRevision,
    patch: decodeCompiledCheckpointV2({
      beat: semantic.beat,
      checkpointId: semantic.checkpointId,
      patch: input.patch,
      receipt: semantic.receipt,
      presentation: semantic.presentation,
      choreography: semantic.choreography,
      certificate: semantic.certificate,
    }).patch,
    semantic,
  });
}

function decodeDeclinedEvent(
  value: unknown,
): ChoreographySceneStreamDeclinedEvent {
  const input = record(value, "declined event");
  exactKeys(
    input,
    ["type", "generation", "attempt", "finalRevision", "reasonCode", "message"],
    "declined event",
  );
  if (input.type !== "choreography_scene_stream_declined") {
    fail("declined event type is unsupported");
  }
  if (
    input.reasonCode !== "unsupported_intent" &&
    input.reasonCode !== "no_forward_progress"
  ) {
    fail("declined reasonCode is unsupported");
  }
  return Object.freeze({
    type: "choreography_scene_stream_declined",
    generation: integer(input.generation, "declined generation", 1),
    attempt: integer(input.attempt, "declined attempt", 1, 2),
    finalRevision: integer(input.finalRevision, "declined finalRevision", 0),
    reasonCode: input.reasonCode,
    message: boundedString(input.message, "declined message"),
  });
}

/** Decode one complete V2 checkpoint-stream event at the browser boundary. */
export function decodeChoreographySceneStreamEvent(
  value: unknown,
): ChoreographySceneStreamEvent {
  const input = record(value, "scene stream event");
  if (input.type === "choreography_scene_stream_declined") {
    return decodeDeclinedEvent(input);
  }
  return decodeSceneStreamEventWithPatch(
    input,
    "choreography_scene_checkpoint",
    decodeCheckpointEvent,
  );
}

export function parseChoreographySceneStreamEvent(
  data: string,
): ChoreographySceneStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(data);
  } catch {
    throw new SceneModelStreamError(
      "invalid_json",
      "SSE data must be valid JSON",
    );
  }
  return decodeChoreographySceneStreamEvent(value);
}

export async function consumeChoreographySceneStreamResponse(
  response: Response,
  onEvent: (event: ChoreographySceneStreamEvent) => void,
): Promise<void> {
  await consumeDecodedSceneStreamResponse(
    response,
    onEvent,
    parseChoreographySceneStreamEvent,
  );
}

const CHOREOGRAPHY_STREAM_PATHS: Readonly<Record<SceneStreamEndpoint, string>> =
  {
    product: "/api/live-scenes/choreography/stream",
    developmentLab: "/api/live-scenes/lab/choreography/stream",
  };

/** Start one compiler-certified choreography stream at its separate endpoint. */
export async function runChoreographySceneModelStream(
  options: RunChoreographySceneStreamOptions,
): Promise<void> {
  const requestFetch = options.fetchImpl ?? fetch;
  const endpointPath = CHOREOGRAPHY_STREAM_PATHS[options.endpoint];
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
    },
  );
  await consumeChoreographySceneStreamResponse(response, options.onEvent);
}
