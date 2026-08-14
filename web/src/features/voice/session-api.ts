import { getAuthHeaders } from "@/lib/firebase";
import { API_BASE } from "@/lib/api";

export const VOICE_V2_EVENT_TOPIC = "murmur.voice.v2.events" as const;
export const PIPECAT_VOICE_EVENT_PROTOCOL = "rtvi-murmur-v2" as const;

export interface VoiceSessionBootstrapRequest {
  readonly session_id: string;
  readonly voice_call_id: string;
}

export interface LiveKitVoiceSessionBootstrap {
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

export interface PipecatBrowserIceServer {
  readonly urls: readonly string[];
  readonly username: string | null;
  readonly credential: string | null;
  readonly credentialType: "password";
}

export interface PipecatBrowserVoiceAssignment {
  readonly runtime: "pipecat_smallwebrtc_v1";
  readonly profile_id: string;
  readonly event_protocol: typeof PIPECAT_VOICE_EVENT_PROTOCOL;
  readonly expires_at: string;
  readonly session_id: string;
  readonly agent_id: string;
  readonly voice_call_id: string;
  readonly trace_id: string;
  readonly webrtc_url: string;
  readonly peer_reservation_id: string;
  readonly ice_servers: readonly PipecatBrowserIceServer[];
}

export type VoiceSessionBootstrap =
  | LiveKitVoiceSessionBootstrap
  | PipecatBrowserVoiceAssignment;

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

const liveKitResponseKeys = new Set([
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

const pipecatResponseKeys = new Set([
  "runtime",
  "profile_id",
  "event_protocol",
  "expires_at",
  "session_id",
  "agent_id",
  "voice_call_id",
  "trace_id",
  "webrtc_url",
  "peer_reservation_id",
  "ice_servers",
]);

const pipecatIceServerKeys = new Set([
  "urls",
  "username",
  "credential",
  "credentialType",
]);

const contractIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const canonicalUuidV4Pattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const liveKitOriginPattern = /^wss?:\/\/[^/?#]+\/?$/i;
const iceUriPattern =
  /^(stun|turn|turns):([^/?#:@]+)(?::([0-9]{1,5}))?(?:\?transport=(udp|tcp))?$/;
const dnsLabelPattern = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;

type IceScheme = "stun" | "turn" | "turns";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: ReadonlySet<string>): boolean {
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.size && actualKeys.every((key) => keys.has(key));
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

function isStrictSecretString(value: unknown, maxLength = 4_096): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value === value.trim() &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function isLiveKitServerUrl(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value !== value.trim() ||
    /[\\\u0000-\u001f\u007f]/.test(value) ||
    !liveKitOriginPattern.test(value)
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    return (
      (url.protocol === "ws:" || url.protocol === "wss:") &&
      url.hostname !== "" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/" &&
      url.search === "" &&
      url.hash === ""
    );
  } catch {
    return false;
  }
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  if (normalized === "localhost" || normalized === "[::1]" || normalized === "::1") {
    return true;
  }
  const octets = normalized.split(".");
  return (
    octets.length === 4 &&
    octets[0] === "127" &&
    octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
  );
}

function isPipecatSignalingUrl(value: unknown): value is string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 2_048 ||
    value !== value.trim() ||
    /[\\\u0000-\u001f\u007f]/.test(value)
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    if (
      !url.hostname ||
      url.username !== "" ||
      url.password !== "" ||
      url.search !== "" ||
      url.hash !== "" ||
      url.pathname === "" ||
      url.pathname === "/"
    ) {
      return false;
    }
    return (
      url.protocol === "https:" ||
      (url.protocol === "http:" && isLoopbackHostname(url.hostname))
    );
  } catch {
    return false;
  }
}

function pipecatSignalingUsesLoopback(value: string): boolean {
  return isLoopbackHostname(new URL(value).hostname);
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

function isIpv4Address(hostname: string): boolean {
  const octets = hostname.split(".");
  return (
    octets.length === 4 &&
    octets.every(
      (octet) =>
        /^(?:0|[1-9]\d{0,2})$/.test(octet) && Number(octet) <= 255
    )
  );
}

function isIceHostname(hostname: string): boolean {
  if (isIpv4Address(hostname)) return true;
  if (hostname.length > 253 || hostname.endsWith(".")) return false;
  const labels = hostname.split(".");
  return labels.length >= 2 && labels.every((label) => dnsLabelPattern.test(label));
}

function decodeIceScheme(value: unknown): IceScheme | undefined {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 2_048 ||
    value !== value.trim() ||
    value.includes("%") ||
    /\s/.test(value) ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return undefined;
  }
  const match = iceUriPattern.exec(value);
  if (!match) return undefined;

  const scheme = match[1] as IceScheme;
  const hostname = match[2];
  const rawPort = match[3];
  const transport = match[4];
  if (
    !hostname ||
    !isIceHostname(hostname) ||
    (rawPort !== undefined && (Number(rawPort) < 1 || Number(rawPort) > 65_535)) ||
    (scheme === "stun" && transport !== undefined) ||
    (scheme === "turns" && transport !== undefined && transport !== "tcp")
  ) {
    return undefined;
  }
  return scheme;
}

function decodePipecatIceServers(
  value: unknown
): readonly PipecatBrowserIceServer[] | undefined {
  if (!Array.isArray(value) || value.length > 8) return undefined;

  const decoded: PipecatBrowserIceServer[] = [];
  const allUrls = new Set<string>();
  let stunUrlCount = 0;
  let turnUrlCount = 0;

  for (const entry of value) {
    if (!isRecord(entry) || !hasExactKeys(entry, pipecatIceServerKeys)) {
      return undefined;
    }
    if (
      !Array.isArray(entry.urls) ||
      entry.urls.length === 0 ||
      entry.urls.length > 8 ||
      entry.credentialType !== "password"
    ) {
      return undefined;
    }

    const urls: string[] = [];
    const schemes = new Set<IceScheme>();
    for (const candidate of entry.urls) {
      const scheme = decodeIceScheme(candidate);
      if (!scheme || allUrls.has(candidate)) return undefined;
      allUrls.add(candidate);
      urls.push(candidate);
      schemes.add(scheme);
      if (scheme === "stun") stunUrlCount += 1;
      else turnUrlCount += 1;
    }

    const username = entry.username;
    const credential = entry.credential;
    const hasStunOnly = schemes.size === 1 && schemes.has("stun");
    const hasTurnOnly = !schemes.has("stun");
    let decodedUsername: string | null;
    let decodedCredential: string | null;
    if (hasStunOnly && username === null && credential === null) {
      decodedUsername = null;
      decodedCredential = null;
    } else if (
      hasTurnOnly &&
      isStrictSecretString(username) &&
      isStrictSecretString(credential)
    ) {
      decodedUsername = username;
      decodedCredential = credential;
    } else {
      return undefined;
    }

    decoded.push(
      Object.freeze({
        urls: Object.freeze(urls),
        username: decodedUsername,
        credential: decodedCredential,
        credentialType: "password" as const,
      })
    );
  }

  if (stunUrlCount > 1 || turnUrlCount > 1) return undefined;
  return Object.freeze(decoded);
}

function hasTurnIceServer(iceServers: readonly PipecatBrowserIceServer[]): boolean {
  return iceServers.some((server) =>
    server.urls.some((url) => url.startsWith("turn:") || url.startsWith("turns:"))
  );
}

function decodeLiveKitBootstrapResponse(
  value: Record<string, unknown>
): LiveKitVoiceSessionBootstrap {
  if (!hasExactKeys(value, liveKitResponseKeys)) {
    throw new VoiceSessionApiError("Voice session bootstrap response has an incompatible schema");
  }

  if (
    value.runtime !== "livekit_v2" ||
    !isCanonicalUuidV4(value.trace_id) ||
    !isContractId(value.profile_id) ||
    !isLiveKitServerUrl(value.server_url) ||
    !isContractId(value.room_name) ||
    !isStrictSecretString(value.participant_token, 16_000) ||
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

function decodePipecatBootstrapResponse(
  value: Record<string, unknown>
): PipecatBrowserVoiceAssignment {
  if (!hasExactKeys(value, pipecatResponseKeys)) {
    throw new VoiceSessionApiError("Voice session bootstrap response has an incompatible schema");
  }

  const iceServers = decodePipecatIceServers(value.ice_servers);
  const webrtcUrl = value.webrtc_url;
  if (
    value.runtime !== "pipecat_smallwebrtc_v1" ||
    !isContractId(value.profile_id) ||
    value.event_protocol !== PIPECAT_VOICE_EVENT_PROTOCOL ||
    !isUtcTimestamp(value.expires_at) ||
    !isCanonicalUuidV4(value.session_id) ||
    !isCanonicalUuidV4(value.agent_id) ||
    !isCanonicalUuidV4(value.voice_call_id) ||
    !isCanonicalUuidV4(value.trace_id) ||
    !isPipecatSignalingUrl(webrtcUrl) ||
    !isContractId(value.peer_reservation_id) ||
    iceServers === undefined
  ) {
    throw new VoiceSessionApiError("Voice session bootstrap response has invalid fields");
  }
  if (!pipecatSignalingUsesLoopback(webrtcUrl) && !hasTurnIceServer(iceServers)) {
    throw new VoiceSessionApiError("Voice session bootstrap response has invalid fields");
  }

  return Object.freeze({
    runtime: "pipecat_smallwebrtc_v1",
    profile_id: value.profile_id,
    event_protocol: PIPECAT_VOICE_EVENT_PROTOCOL,
    expires_at: value.expires_at,
    session_id: value.session_id,
    agent_id: value.agent_id,
    voice_call_id: value.voice_call_id,
    trace_id: value.trace_id,
    webrtc_url: webrtcUrl,
    peer_reservation_id: value.peer_reservation_id,
    ice_servers: iceServers,
  });
}

function decodeBootstrapResponse(value: unknown): VoiceSessionBootstrap {
  if (!isRecord(value)) {
    throw new VoiceSessionApiError("Voice session bootstrap returned a non-object response");
  }
  if (value.runtime === "livekit_v2") {
    return decodeLiveKitBootstrapResponse(value);
  }
  if (value.runtime === "pipecat_smallwebrtc_v1") {
    return decodePipecatBootstrapResponse(value);
  }
  throw new VoiceSessionApiError("Voice session bootstrap response has invalid fields");
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
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
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
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    }
  );
  if (!response.ok) {
    throw new VoiceSessionApiError(await responseError(response), response.status);
  }
}
