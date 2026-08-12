import { getAuthHeaders } from "@/lib/firebase";
import { API_BASE } from "@/lib/api";

export const VOICE_V2_EVENT_TOPIC = "murmur.voice.v2.events" as const;

export interface VoiceSessionBootstrapRequest {
  readonly session_id: string;
  readonly voice_call_id: string;
}

export interface VoiceSessionBootstrap {
  readonly runtime: "livekit_v2";
  readonly trace_id: string;
  readonly profile_id: string;
  readonly server_url: string;
  readonly room_name: string;
  readonly participant_token: string;
  readonly participant_identity: string;
  readonly agent_participant_identity: string;
  readonly session_id: string;
  readonly agent_id: string;
  readonly voice_call_id: string;
  readonly dispatch_id: string;
  readonly worker_name: string;
  readonly event_topic: typeof VOICE_V2_EVENT_TOPIC;
  readonly expires_at: string;
}

export type VoiceAuthHeaderProvider = () => Promise<Record<string, string>>;

export interface BootstrapVoiceSessionOptions {
  readonly apiUrl?: string;
  readonly signal?: AbortSignal;
  readonly authHeaderProvider?: VoiceAuthHeaderProvider;
}

export interface EndVoiceSessionOptions {
  readonly apiUrl?: string;
  readonly signal?: AbortSignal;
  readonly authHeaderProvider?: VoiceAuthHeaderProvider;
}

export class VoiceSessionApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "VoiceSessionApiError";
    this.status = status;
  }
}

const responseKeys = new Set([
  "runtime",
  "trace_id",
  "profile_id",
  "server_url",
  "room_name",
  "participant_token",
  "participant_identity",
  "agent_participant_identity",
  "session_id",
  "agent_id",
  "voice_call_id",
  "dispatch_id",
  "worker_name",
  "event_topic",
  "expires_at",
]);

const contractIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const canonicalUuidV4Pattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isContractId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 128 &&
    contractIdPattern.test(value)
  );
}

function isCanonicalUuidV4(value: unknown): value is string {
  return typeof value === "string" && canonicalUuidV4Pattern.test(value);
}

function isNonEmptyString(value: unknown, maxLength = 16_000): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maxLength
  );
}

function isLiveKitServerUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return (
      (url.protocol === "ws:" || url.protocol === "wss:") &&
      url.username === "" &&
      url.password === ""
    );
  } catch {
    return false;
  }
}

function isUtcTimestamp(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    !/(?:Z|\+00:00)$/i.test(value)
  ) {
    return false;
  }
  return Number.isFinite(Date.parse(value));
}

function decodeBootstrapResponse(value: unknown): VoiceSessionBootstrap {
  if (!isRecord(value)) {
    throw new VoiceSessionApiError("Voice session bootstrap returned a non-object response");
  }

  const unknownKey = Object.keys(value).find((key) => !responseKeys.has(key));
  if (unknownKey || Object.keys(value).length !== responseKeys.size) {
    throw new VoiceSessionApiError("Voice session bootstrap response has an incompatible schema");
  }

  if (
    value.runtime !== "livekit_v2" ||
    !isCanonicalUuidV4(value.trace_id) ||
    !isContractId(value.profile_id) ||
    !isLiveKitServerUrl(value.server_url) ||
    !isContractId(value.room_name) ||
    !isNonEmptyString(value.participant_token) ||
    !isContractId(value.participant_identity) ||
    !isContractId(value.agent_participant_identity) ||
    !isCanonicalUuidV4(value.session_id) ||
    !isCanonicalUuidV4(value.agent_id) ||
    !isCanonicalUuidV4(value.voice_call_id) ||
    !isContractId(value.dispatch_id) ||
    !isContractId(value.worker_name) ||
    value.event_topic !== VOICE_V2_EVENT_TOPIC ||
    !isUtcTimestamp(value.expires_at)
  ) {
    throw new VoiceSessionApiError("Voice session bootstrap response has invalid fields");
  }

  return Object.freeze({
    runtime: "livekit_v2",
    trace_id: value.trace_id,
    profile_id: value.profile_id,
    server_url: value.server_url,
    room_name: value.room_name,
    participant_token: value.participant_token,
    participant_identity: value.participant_identity,
    agent_participant_identity: value.agent_participant_identity,
    session_id: value.session_id,
    agent_id: value.agent_id,
    voice_call_id: value.voice_call_id,
    dispatch_id: value.dispatch_id,
    worker_name: value.worker_name,
    event_topic: VOICE_V2_EVENT_TOPIC,
    expires_at: value.expires_at,
  });
}

async function responseError(response: Response): Promise<string> {
  const fallback = `Voice session bootstrap failed (${response.status})`;
  try {
    const body: unknown = await response.json();
    if (!isRecord(body)) return fallback;
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
    if (typeof body.message === "string" && body.message.trim()) return body.message;
    if (typeof body.error === "string" && body.error.trim()) return body.error;
  } catch {
    // Use the status-only fallback for a non-JSON response.
  }
  return fallback;
}

export async function bootstrapVoiceSession(
  request: VoiceSessionBootstrapRequest,
  options: BootstrapVoiceSessionOptions = {}
): Promise<VoiceSessionBootstrap> {
  if (
    !isCanonicalUuidV4(request.session_id) ||
    !isCanonicalUuidV4(request.voice_call_id)
  ) {
    throw new VoiceSessionApiError("Voice session bootstrap requires valid session and call IDs");
  }

  const authHeaders = await (options.authHeaderProvider ?? getAuthHeaders)();
  if (!authHeaders.Authorization) {
    throw new VoiceSessionApiError("Authentication is required to start voice", 401);
  }

  const response = await fetch(`${options.apiUrl ?? API_BASE}/api/voice/session`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
    },
    body: JSON.stringify({
      session_id: request.session_id,
      voice_call_id: request.voice_call_id,
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new VoiceSessionApiError(await responseError(response), response.status);
  }

  const bootstrap = decodeBootstrapResponse(await response.json());
  if (
    bootstrap.session_id !== request.session_id ||
    bootstrap.voice_call_id !== request.voice_call_id
  ) {
    throw new VoiceSessionApiError("Voice session bootstrap identity does not match the request");
  }
  // LiveKit validates token expiry against server time. The browser wall clock
  // is not authoritative and may be skewed enough to reject a valid assignment.
  return bootstrap;
}

/** Release the server-owned room and dispatch for one authenticated call intent. */
export async function endVoiceSession(
  request: VoiceSessionBootstrapRequest,
  options: EndVoiceSessionOptions = {}
): Promise<void> {
  if (
    !isCanonicalUuidV4(request.session_id) ||
    !isCanonicalUuidV4(request.voice_call_id)
  ) {
    throw new VoiceSessionApiError("Ending voice requires valid session and call IDs");
  }

  const authHeaders = await (options.authHeaderProvider ?? getAuthHeaders)();
  if (!authHeaders.Authorization) {
    throw new VoiceSessionApiError("Authentication is required to end voice", 401);
  }

  const response = await fetch(
    `${options.apiUrl ?? API_BASE}/api/voice/session/end`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders,
      },
      body: JSON.stringify(request),
      signal: options.signal,
      keepalive: true,
    }
  );
  if (!response.ok) {
    throw new VoiceSessionApiError(await responseError(response), response.status);
  }
}
