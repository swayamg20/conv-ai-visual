/** Transport-neutral Voice V2 event contracts and boundary validation. */

export const VOICE_EVENT_SCHEMA_VERSION = 1 as const;

export const VOICE_EVENT_TYPES = [
  "session_starting",
  "session_started",
  "transport_connected",
  "transport_reconnecting",
  "transport_disconnected",
  "agent_ready",
  "agent_unavailable",
  "transcript_segment",
  "turn_committed",
  "turn_resumed",
  "assistant_speech_started",
  "assistant_speech_stopped",
  "task_queued",
  "task_working",
  "task_needs_input",
  "task_verified",
  "task_failed",
  "task_cancelled",
  "task_superseded",
  "artifact_proposed",
  "artifact_accepted",
  "artifact_rejected",
  "canvas_patch",
  "canvas_apply_ack",
  "canvas_first_visible",
  "canvas_animation_complete",
  "canvas_render_failed",
  "usage_recorded",
  "session_ending",
  "session_ended",
] as const;

export type VoiceEventType = (typeof VOICE_EVENT_TYPES)[number];

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | readonly JsonValue[];
export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export const REQUIRED_VOICE_READY_COMPONENTS = [
  "worker",
  "input",
  "output",
  "event_channel",
] as const;

export interface VoiceProviderModel {
  readonly component: string;
  readonly provider: string;
  readonly model: string;
}

export interface VoiceEventPayloads {
  readonly session_starting: Readonly<Record<string, never>>;
  readonly session_started: Readonly<Record<string, never>>;
  readonly transport_connected: {
    readonly connection_id?: string;
  };
  readonly transport_reconnecting: {
    readonly attempt: number;
    readonly reason?: string;
  };
  readonly transport_disconnected: {
    readonly recoverable: boolean;
    readonly reason?: string;
  };
  readonly agent_ready: {
    readonly profile_id: string;
    readonly required_components: readonly string[];
    readonly ready_components: readonly string[];
    /** Omitted only by legacy schema-v1 producers. Current workers always publish it. */
    readonly profile_config_hash?: string;
    /** Omitted only by legacy schema-v1 producers. Current workers always publish it. */
    readonly provider_models?: readonly VoiceProviderModel[];
  };
  readonly agent_unavailable: {
    readonly code: string;
    readonly message: string;
    readonly retryable: boolean;
  };
  readonly transcript_segment: {
    readonly segment_id: string;
    readonly text: string;
    /** Segment finality is not end-of-turn finality. */
    readonly is_final: boolean;
  };
  readonly turn_committed: {
    readonly text: string;
  };
  readonly turn_resumed: {
    readonly reason?: string;
  };
  readonly assistant_speech_started: {
    readonly speech_id: string;
    readonly text?: string;
  };
  readonly assistant_speech_stopped: {
    readonly speech_id: string;
    readonly reason: "completed" | "interrupted" | "cancelled" | "error";
  };
  readonly task_queued: {
    readonly label?: string;
  };
  readonly task_working: {
    readonly message?: string;
    readonly progress?: number;
  };
  readonly task_needs_input: {
    readonly prompt: string;
  };
  readonly task_verified: {
    readonly result_id: string;
  };
  readonly task_failed: {
    readonly code: string;
    readonly message: string;
    readonly retryable: boolean;
  };
  readonly task_cancelled: {
    readonly reason?: string;
  };
  readonly task_superseded: {
    readonly reason?: string;
    readonly superseded_by_task_id?: string;
  };
  readonly artifact_proposed: {
    readonly artifact_id: string;
    readonly artifact_kind: string;
  };
  readonly artifact_accepted: {
    readonly artifact_id: string;
  };
  readonly artifact_rejected: {
    readonly artifact_id: string;
    readonly code: string;
    readonly message: string;
  };
  readonly canvas_patch: {
    readonly artifact_id: string;
    /** Milestone 0 deliberately treats renderer data as opaque validated JSON. */
    readonly artifact: JsonObject;
  };
  readonly canvas_apply_ack: {
    readonly artifact_id: string;
  };
  readonly canvas_first_visible: {
    readonly artifact_id: string;
  };
  readonly canvas_animation_complete: {
    readonly artifact_id: string;
  };
  readonly canvas_render_failed: {
    readonly artifact_id: string;
    readonly code: string;
    readonly message: string;
  };
  readonly usage_recorded: {
    readonly usage_id: string;
    readonly category: string;
    readonly quantity: number;
    readonly unit: string;
    readonly estimated_cost_usd?: number;
  };
  readonly session_ending: {
    readonly reason?: string;
  };
  readonly session_ended: {
    readonly reason?: string;
  };
}

export interface VoiceEventEnvelope<
  Type extends VoiceEventType,
  Payload extends VoiceEventPayloads[Type] = VoiceEventPayloads[Type],
> {
  readonly schema_version: typeof VOICE_EVENT_SCHEMA_VERSION;
  readonly event_id: string;
  readonly event_type: Type;
  readonly trace_id: string;
  readonly voice_call_id: string;
  readonly session_id: string;
  readonly turn_id?: string;
  readonly task_id?: string;
  readonly producer_id: string;
  /** Strictly increasing within one producer. Producers are ordered independently. */
  readonly producer_sequence: number;
  readonly causation_id?: string;
  readonly correlation_id?: string;
  /** Present only after the authoritative durable ledger ingests the event. */
  readonly ledger_sequence?: number;
  readonly task_generation?: number;
  readonly canvas_base_revision?: number;
  readonly canvas_result_revision?: number;
  /** Audit metadata only. Reducers never use wall-clock time for ordering. */
  readonly emitted_at: string;
  readonly payload: Payload;
}

export type VoiceEvent = {
  readonly [Type in VoiceEventType]: VoiceEventEnvelope<Type>;
}[VoiceEventType];

export type EventOf<Type extends VoiceEventType> = Extract<
  VoiceEvent,
  { readonly event_type: Type }
>;

export type VoiceEventDecodeErrorCode =
  | "invalid_envelope"
  | "unsupported_schema_version"
  | "unknown_event_type"
  | "invalid_payload";

export interface VoiceEventDecodeError {
  readonly code: VoiceEventDecodeErrorCode;
  readonly message: string;
  readonly event_id?: string;
  readonly event_type?: string;
  readonly schema_version?: string | number | null;
}

export type VoiceEventDecodeResult =
  | { readonly ok: true; readonly event: VoiceEvent }
  | { readonly ok: false; readonly error: VoiceEventDecodeError };

const eventTypeSet: ReadonlySet<string> = new Set(VOICE_EVENT_TYPES);
const speechStopReasons = new Set(["completed", "interrupted", "cancelled", "error"]);
const contractIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const profileConfigHashPattern = /^[0-9a-f]{64}$/;

const envelopeKeys: ReadonlySet<string> = new Set([
  "schema_version",
  "event_id",
  "event_type",
  "trace_id",
  "voice_call_id",
  "session_id",
  "turn_id",
  "task_id",
  "producer_id",
  "producer_sequence",
  "causation_id",
  "correlation_id",
  "ledger_sequence",
  "task_generation",
  "canvas_base_revision",
  "canvas_result_revision",
  "emitted_at",
  "payload",
]);

const turnScopedEventTypes: ReadonlySet<VoiceEventType> = new Set([
  "turn_committed",
  "turn_resumed",
  "assistant_speech_started",
  "assistant_speech_stopped",
]);

const taskEventTypes: ReadonlySet<VoiceEventType> = new Set([
  "task_queued",
  "task_working",
  "task_needs_input",
  "task_verified",
  "task_failed",
  "task_cancelled",
  "task_superseded",
]);

const artifactEventTypes: ReadonlySet<VoiceEventType> = new Set([
  "artifact_proposed",
  "artifact_accepted",
  "artifact_rejected",
]);

const canvasResultEventTypes: ReadonlySet<VoiceEventType> = new Set([
  "canvas_apply_ack",
  "canvas_first_visible",
  "canvas_animation_complete",
  "canvas_render_failed",
]);

const taskScopedEventTypes: ReadonlySet<VoiceEventType> = new Set([
  ...taskEventTypes,
  ...artifactEventTypes,
  "canvas_patch",
  ...canvasResultEventTypes,
]);

const payloadKeysByEventType: Readonly<{
  readonly [Type in VoiceEventType]: readonly (keyof VoiceEventPayloads[Type])[];
}> = {
  session_starting: [],
  session_started: [],
  transport_connected: ["connection_id"],
  transport_reconnecting: ["attempt", "reason"],
  transport_disconnected: ["recoverable", "reason"],
  agent_ready: [
    "profile_id",
    "required_components",
    "ready_components",
    "profile_config_hash",
    "provider_models",
  ],
  agent_unavailable: ["code", "message", "retryable"],
  transcript_segment: ["segment_id", "text", "is_final"],
  turn_committed: ["text"],
  turn_resumed: ["reason"],
  assistant_speech_started: ["speech_id", "text"],
  assistant_speech_stopped: ["speech_id", "reason"],
  task_queued: ["label"],
  task_working: ["message", "progress"],
  task_needs_input: ["prompt"],
  task_verified: ["result_id"],
  task_failed: ["code", "message", "retryable"],
  task_cancelled: ["reason"],
  task_superseded: ["reason", "superseded_by_task_id"],
  artifact_proposed: ["artifact_id", "artifact_kind"],
  artifact_accepted: ["artifact_id"],
  artifact_rejected: ["artifact_id", "code", "message"],
  canvas_patch: ["artifact_id", "artifact"],
  canvas_apply_ack: ["artifact_id"],
  canvas_first_visible: ["artifact_id"],
  canvas_animation_complete: ["artifact_id"],
  canvas_render_failed: ["artifact_id", "code", "message"],
  usage_recorded: [
    "usage_id",
    "category",
    "quantity",
    "unit",
    "estimated_cost_usd",
  ],
  session_ending: ["reason"],
  session_ended: ["reason"],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isContractId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 128 &&
    contractIdPattern.test(value)
  );
}

function isOptionalContractId(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || isContractId(value);
}

function isSafeIntegerAtLeast(value: unknown, minimum: number): value is number {
  return Number.isSafeInteger(value) && (value as number) >= minimum;
}

function isFiniteNumberAtLeast(value: unknown, minimum: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum;
}

function isContractIdArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every(isContractId);
}

function hasUniqueValues(values: readonly string[]): boolean {
  return new Set(values).size === values.length;
}

function isJsonValue(value: unknown): value is JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return true;
  }
  if (typeof value === "number") {
    return Number.isFinite(value);
  }
  if (Array.isArray(value)) {
    return value.every(isJsonValue);
  }
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function isJsonObject(value: unknown): value is JsonObject {
  return isRecord(value) && Object.values(value).every(isJsonValue);
}

function cloneAndFreezeJsonValue(value: JsonValue): JsonValue {
  if (Array.isArray(value)) {
    return Object.freeze(value.map(cloneAndFreezeJsonValue));
  }
  if (value !== null && typeof value === "object") {
    return Object.freeze(
      Object.fromEntries(
        Object.entries(value).map(([key, child]) => [
          key,
          cloneAndFreezeJsonValue(child),
        ])
      )
    );
  }
  return value;
}

function isIsoTimestamp(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }

  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/i.exec(
      value
    );
  if (!match) {
    return false;
  }

  const [, yearText, monthText, dayText, hourText, minuteText, secondText] =
    match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const offsetHour = match[7] === undefined ? 0 : Number(match[7]);
  const offsetMinute = match[8] === undefined ? 0 : Number(match[8]);

  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    offsetHour > 23 ||
    offsetMinute > 59
  ) {
    return false;
  }

  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [
    31,
    leapYear ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ];
  return day >= 1 && day <= daysInMonth[month - 1];
}

function isPayloadText(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.trim().length <= 16_000
  );
}

function isTranscriptText(value: unknown): value is string {
  return typeof value === "string" && value.length <= 64_000;
}

function hasOptionalPayloadText(
  record: Record<string, unknown>,
  key: string
): boolean {
  return (
    record[key] === undefined ||
    isPayloadText(record[key])
  );
}

function hasOptionalContractId(
  record: Record<string, unknown>,
  key: string
): boolean {
  return (
    record[key] === undefined ||
    isContractId(record[key])
  );
}

function hasFailurePayload(payload: JsonObject): boolean {
  return (
    isContractId(payload.code) &&
    isPayloadText(payload.message) &&
    typeof payload.retryable === "boolean"
  );
}

function hasArtifactId(payload: JsonObject): boolean {
  return isContractId(payload.artifact_id);
}

function hasOnlyKeys(
  record: Readonly<Record<string, unknown>>,
  allowedKeys: readonly string[]
): boolean {
  return Object.keys(record).every((key) => allowedKeys.includes(key));
}

function isProviderModel(value: unknown): value is VoiceProviderModel {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["component", "provider", "model"]) &&
    isContractId(value.component) &&
    typeof value.provider === "string" &&
    value.provider.trim().length > 0 &&
    value.provider.length <= 256 &&
    typeof value.model === "string" &&
    value.model.trim().length > 0 &&
    value.model.length <= 256
  );
}

function isReadyPayload(payload: JsonObject): boolean {
  if (
    !isContractId(payload.profile_id) ||
    !isContractIdArray(payload.required_components) ||
    !isContractIdArray(payload.ready_components) ||
    !hasUniqueValues(payload.required_components) ||
    !hasUniqueValues(payload.ready_components)
  ) {
    return false;
  }

  const required = new Set(payload.required_components);
  const ready = new Set(payload.ready_components);
  if (
    !REQUIRED_VOICE_READY_COMPONENTS.every((component) => required.has(component)) ||
    !payload.required_components.every((component) => ready.has(component))
  ) {
    return false;
  }

  const hasConfigHash = payload.profile_config_hash !== undefined;
  const hasProviderModels = payload.provider_models !== undefined;
  if (hasConfigHash !== hasProviderModels) {
    return false;
  }
  if (!hasConfigHash) {
    return true;
  }
  if (
    typeof payload.profile_config_hash !== "string" ||
    !profileConfigHashPattern.test(payload.profile_config_hash) ||
    !Array.isArray(payload.provider_models) ||
    !payload.provider_models.every(isProviderModel)
  ) {
    return false;
  }
  const providerComponents = payload.provider_models.map(
    (descriptor) => descriptor.component
  );
  return (
    hasUniqueValues(providerComponents) &&
    providerComponents.every((component) => ready.has(component))
  );
}

function isValidPayload(eventType: VoiceEventType, value: unknown): value is JsonObject {
  if (!isJsonObject(value)) {
    return false;
  }
  if (!hasOnlyKeys(value, payloadKeysByEventType[eventType])) {
    return false;
  }

  switch (eventType) {
    case "session_starting":
    case "session_started":
      return Object.keys(value).length === 0;
    case "transport_connected":
      return hasOptionalContractId(value, "connection_id");
    case "transport_reconnecting":
      return (
        isSafeIntegerAtLeast(value.attempt, 1) &&
        hasOptionalPayloadText(value, "reason")
      );
    case "transport_disconnected":
      return (
        typeof value.recoverable === "boolean" &&
        hasOptionalPayloadText(value, "reason")
      );
    case "agent_ready":
      return isReadyPayload(value);
    case "agent_unavailable":
    case "task_failed":
      return hasFailurePayload(value);
    case "transcript_segment":
      return (
        isContractId(value.segment_id) &&
        isTranscriptText(value.text) &&
        typeof value.is_final === "boolean"
      );
    case "turn_committed":
      return isPayloadText(value.text);
    case "turn_resumed":
    case "task_cancelled":
    case "session_ending":
    case "session_ended":
      return hasOptionalPayloadText(value, "reason");
    case "assistant_speech_started":
      return isContractId(value.speech_id) && hasOptionalPayloadText(value, "text");
    case "assistant_speech_stopped":
      return isContractId(value.speech_id) && speechStopReasons.has(String(value.reason));
    case "task_queued":
      return hasOptionalPayloadText(value, "label");
    case "task_working":
      return (
        hasOptionalPayloadText(value, "message") &&
        (value.progress === undefined ||
          (isFiniteNumberAtLeast(value.progress, 0) && value.progress <= 1))
      );
    case "task_needs_input":
      return isPayloadText(value.prompt);
    case "task_verified":
      return isContractId(value.result_id);
    case "task_superseded":
      return (
        hasOptionalPayloadText(value, "reason") &&
        hasOptionalContractId(value, "superseded_by_task_id")
      );
    case "artifact_proposed":
      return hasArtifactId(value) && isContractId(value.artifact_kind);
    case "artifact_accepted":
      return hasArtifactId(value);
    case "artifact_rejected":
      return (
        hasArtifactId(value) &&
        isContractId(value.code) &&
        isPayloadText(value.message)
      );
    case "canvas_patch":
      return hasArtifactId(value) && isJsonObject(value.artifact);
    case "canvas_apply_ack":
    case "canvas_first_visible":
    case "canvas_animation_complete":
      return hasArtifactId(value);
    case "canvas_render_failed":
      return (
        hasArtifactId(value) &&
        isContractId(value.code) &&
        isPayloadText(value.message)
      );
    case "usage_recorded":
      return (
        isContractId(value.usage_id) &&
        isContractId(value.category) &&
        isFiniteNumberAtLeast(value.quantity, 0) &&
        isContractId(value.unit) &&
        (value.estimated_cost_usd === undefined ||
          isFiniteNumberAtLeast(value.estimated_cost_usd, 0))
      );
  }
}

function optionalContractIdField(
  record: Record<string, unknown>,
  key: string
): string | undefined {
  const value = record[key];
  return isContractId(value) ? value : undefined;
}

function optionalIntegerField(
  record: Record<string, unknown>,
  key: string,
  minimum: number
): number | undefined {
  const value = record[key];
  return isSafeIntegerAtLeast(value, minimum) ? value : undefined;
}

function invalidEnvelope(
  record: Record<string, unknown>,
  message: string
): VoiceEventDecodeResult {
  return {
    ok: false,
    error: {
      code: "invalid_envelope",
      message,
      ...(isNonEmptyString(record.event_id) ? { event_id: record.event_id } : {}),
      ...(isNonEmptyString(record.event_type) ? { event_type: record.event_type } : {}),
    },
  };
}

function validateEventSpecificEnvelope(
  eventType: VoiceEventType,
  record: Record<string, unknown>
): string | undefined {
  const taskId = optionalContractIdField(record, "task_id");
  const taskGeneration = optionalIntegerField(record, "task_generation", 1);
  const baseRevision = optionalIntegerField(record, "canvas_base_revision", 0);
  const resultRevision = optionalIntegerField(record, "canvas_result_revision", 1);

  if (taskGeneration !== undefined && taskId === undefined) {
    return "task_generation requires task_id";
  }
  if (taskScopedEventTypes.has(eventType) && (!taskId || taskGeneration === undefined)) {
    return `${eventType} requires task_id and task_generation`;
  }
  if (turnScopedEventTypes.has(eventType) && !optionalContractIdField(record, "turn_id")) {
    return `${eventType} requires turn_id`;
  }
  if ((baseRevision !== undefined || resultRevision !== undefined) && !taskId) {
    return "canvas revisions require task_id";
  }
  if (eventType === "canvas_patch") {
    if (baseRevision === undefined || resultRevision === undefined) {
      return "canvas_patch requires base and result revisions";
    }
    if (resultRevision !== baseRevision + 1) {
      return "canvas_patch result revision must be exactly one greater than its base";
    }
  }
  if (canvasResultEventTypes.has(eventType)) {
    if (resultRevision === undefined) {
      return `${eventType} requires canvas_result_revision`;
    }
    if (!optionalContractIdField(record, "causation_id")) {
      return `${eventType} requires causation_id`;
    }
  }
  return undefined;
}

/**
 * Decode an untrusted transport value into the current discriminated event union.
 * Unknown schemas and types are explicit errors rather than forward-compatible no-ops.
 */
export function decodeVoiceEvent(input: unknown): VoiceEventDecodeResult {
  if (!isRecord(input)) {
    return {
      ok: false,
      error: { code: "invalid_envelope", message: "Voice event must be an object" },
    };
  }

  if (input.schema_version !== VOICE_EVENT_SCHEMA_VERSION) {
    const schemaVersion = input.schema_version;
    return {
      ok: false,
      error: {
        code: "unsupported_schema_version",
        message: `Unsupported voice event schema version: ${String(schemaVersion)}`,
        ...(typeof schemaVersion === "string" ||
        typeof schemaVersion === "number" ||
        schemaVersion === null
          ? { schema_version: schemaVersion }
          : {}),
        ...(isNonEmptyString(input.event_id) ? { event_id: input.event_id } : {}),
        ...(isNonEmptyString(input.event_type) ? { event_type: input.event_type } : {}),
      },
    };
  }

  if (!isNonEmptyString(input.event_type) || !eventTypeSet.has(input.event_type)) {
    return {
      ok: false,
      error: {
        code: "unknown_event_type",
        message: `Unknown voice event type: ${String(input.event_type)}`,
        ...(isNonEmptyString(input.event_id) ? { event_id: input.event_id } : {}),
        ...(isNonEmptyString(input.event_type) ? { event_type: input.event_type } : {}),
        schema_version: VOICE_EVENT_SCHEMA_VERSION,
      },
    };
  }

  const eventType = input.event_type as VoiceEventType;
  const unexpectedKey = Object.keys(input).find((key) => !envelopeKeys.has(key));
  if (unexpectedKey) {
    return invalidEnvelope(input, `Unknown voice event envelope field: ${unexpectedKey}`);
  }

  if (
    !isContractId(input.event_id) ||
    !isContractId(input.trace_id) ||
    !isContractId(input.voice_call_id) ||
    !isContractId(input.session_id) ||
    !isContractId(input.producer_id) ||
    !isSafeIntegerAtLeast(input.producer_sequence, 1) ||
    !isIsoTimestamp(input.emitted_at)
  ) {
    return invalidEnvelope(input, "Voice event is missing a required envelope field");
  }

  for (const key of ["turn_id", "task_id", "causation_id", "correlation_id"] as const) {
    if (!isOptionalContractId(input[key])) {
      return invalidEnvelope(input, `${key} must be a valid contract ID when present`);
    }
  }

  for (const [key, minimum] of [
    ["ledger_sequence", 1],
    ["task_generation", 1],
    ["canvas_base_revision", 0],
    ["canvas_result_revision", 1],
  ] as const) {
    if (input[key] !== undefined && input[key] !== null && !isSafeIntegerAtLeast(input[key], minimum)) {
      return invalidEnvelope(input, `${key} must be an integer greater than or equal to ${minimum}`);
    }
  }

  const specificError = validateEventSpecificEnvelope(eventType, input);
  if (specificError) {
    return invalidEnvelope(input, specificError);
  }

  if (!isValidPayload(eventType, input.payload)) {
    return {
      ok: false,
      error: {
        code: "invalid_payload",
        message: `Invalid payload for voice event type ${eventType}`,
        event_id: input.event_id,
        event_type: eventType,
      },
    };
  }

  const payload = cloneAndFreezeJsonValue(
    input.payload
  ) as VoiceEventPayloads[typeof eventType];
  const event = Object.freeze({
    schema_version: VOICE_EVENT_SCHEMA_VERSION,
    event_id: input.event_id,
    event_type: eventType,
    trace_id: input.trace_id,
    voice_call_id: input.voice_call_id,
    session_id: input.session_id,
    producer_id: input.producer_id,
    producer_sequence: input.producer_sequence,
    emitted_at: input.emitted_at,
    payload,
    ...(optionalContractIdField(input, "turn_id") ? { turn_id: input.turn_id as string } : {}),
    ...(optionalContractIdField(input, "task_id") ? { task_id: input.task_id as string } : {}),
    ...(optionalContractIdField(input, "causation_id")
      ? { causation_id: input.causation_id as string }
      : {}),
    ...(optionalContractIdField(input, "correlation_id")
      ? { correlation_id: input.correlation_id as string }
      : {}),
    ...(input.ledger_sequence !== undefined && input.ledger_sequence !== null
      ? { ledger_sequence: input.ledger_sequence as number }
      : {}),
    ...(input.task_generation !== undefined && input.task_generation !== null
      ? { task_generation: input.task_generation as number }
      : {}),
    ...(input.canvas_base_revision !== undefined && input.canvas_base_revision !== null
      ? { canvas_base_revision: input.canvas_base_revision as number }
      : {}),
    ...(input.canvas_result_revision !== undefined && input.canvas_result_revision !== null
      ? { canvas_result_revision: input.canvas_result_revision as number }
      : {}),
  }) as VoiceEvent;

  return { ok: true, event };
}

export function isVoiceEvent(input: unknown): input is VoiceEvent {
  return decodeVoiceEvent(input).ok;
}
