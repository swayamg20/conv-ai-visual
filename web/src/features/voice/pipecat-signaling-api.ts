import { getAuthHeaders } from "@/lib/firebase";

export const DEFAULT_PIPECAT_SIGNALING_DEADLINE_MS = 10_000;

const MAX_SIGNALING_URL_LENGTH = 2_048;
const MAX_SDP_LENGTH = 1_000_000;
const MAX_PC_ID_LENGTH = 256;
const MAX_CANDIDATE_LENGTH = 8_192;
const MAX_SDP_MID_LENGTH = 128;
const MAX_CANDIDATES_PER_PATCH = 128;
const MAX_AUTHORIZATION_LENGTH = 16_384;
const MAX_DEADLINE_MS = 60_000;

export type PipecatSignalingErrorCode =
  | "aborted"
  | "authentication_failed"
  | "authentication_required"
  | "invalid_configuration"
  | "invalid_request"
  | "invalid_response"
  | "request_failed"
  | "timed_out";

export class PipecatSignalingApiError extends Error {
  readonly code: PipecatSignalingErrorCode;
  readonly status?: number;

  constructor(
    message: string,
    code: PipecatSignalingErrorCode,
    status?: number,
  ) {
    super(message);
    this.name = "PipecatSignalingApiError";
    this.code = code;
    this.status = status;
  }
}

export interface PipecatOffer {
  readonly sdp: string;
  readonly type: "offer";
  readonly pcId?: string | null;
}

export interface PipecatAnswer {
  readonly sdp: string;
  readonly type: "answer";
  readonly pc_id: string;
}

/** Browser-native candidate names; the port owns the snake-case wire mapping. */
export interface PipecatIceCandidate {
  readonly candidate: string;
  readonly sdpMid: string;
  readonly sdpMLineIndex: number;
}

export interface PipecatCandidateBatch {
  readonly pcId: string;
  readonly candidates: readonly PipecatIceCandidate[];
}

export interface PipecatSignalingOperationOptions {
  readonly signal?: AbortSignal;
}

export type PipecatAuthHeaderProvider = () => Promise<
  Readonly<Record<string, string>>
>;

export type PipecatSignalingFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export interface PipecatSignalingPortOptions {
  readonly deadlineMs?: number;
  readonly authHeaderProvider?: PipecatAuthHeaderProvider;
  readonly fetcher?: PipecatSignalingFetch;
}

export interface PipecatSignalingPort {
  offer(
    offer: PipecatOffer,
    options?: PipecatSignalingOperationOptions,
  ): Promise<PipecatAnswer>;
  patchCandidates(
    batch: PipecatCandidateBatch,
    options?: PipecatSignalingOperationOptions,
  ): Promise<void>;
  deletePeer(
    pcId: string | null,
    options?: PipecatSignalingOperationOptions,
  ): Promise<void>;
}

interface OfferWireBody {
  readonly sdp: string;
  readonly type: "offer";
  readonly pc_id: string | null;
  readonly restart_pc: false;
}

interface CandidateWireBody {
  readonly candidate: string;
  readonly sdp_mid: string;
  readonly sdp_mline_index: number;
}

interface PatchWireBody {
  readonly pc_id: string;
  readonly candidates: readonly CandidateWireBody[];
}

interface DeleteWireBody {
  readonly pc_id: string | null;
}

class SignalingOperationAborted extends Error {}

function invalidConfiguration(): PipecatSignalingApiError {
  return new PipecatSignalingApiError(
    "Pipecat signaling configuration is invalid",
    "invalid_configuration",
  );
}

function invalidRequest(): PipecatSignalingApiError {
  return new PipecatSignalingApiError(
    "Pipecat signaling request is invalid",
    "invalid_request",
  );
}

function invalidResponse(): PipecatSignalingApiError {
  return new PipecatSignalingApiError(
    "Pipecat signaling response is invalid",
    "invalid_response",
  );
}

function requestFailed(status?: number): PipecatSignalingApiError {
  return new PipecatSignalingApiError(
    "Pipecat signaling request failed",
    "request_failed",
    status,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value);
  return (
    actual.length === expected.length &&
    actual.every((key) => expected.includes(key))
  );
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
    octets.every(
      (octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255,
    )
  );
}

/**
 * Bootstrap already validates this locator. Repeat only the security-critical
 * origin constraints before attaching a Firebase bearer to it.
 */
function snapshotSignalingUrl(value: string): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_SIGNALING_URL_LENGTH ||
    value !== value.trim() ||
    /[\\\u0000-\u001f\u007f]/.test(value)
  ) {
    throw invalidConfiguration();
  }

  try {
    const parsed = new URL(value);
    if (
      !parsed.hostname ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== "" ||
      parsed.pathname === "" ||
      parsed.pathname === "/" ||
      !(
        parsed.protocol === "https:" ||
        (parsed.protocol === "http:" && isLoopbackHostname(parsed.hostname))
      )
    ) {
      throw invalidConfiguration();
    }
  } catch (error) {
    if (error instanceof PipecatSignalingApiError) throw error;
    throw invalidConfiguration();
  }

  return value;
}

function snapshotPcId(value: unknown, allowNull: boolean): string | null {
  if (allowNull && value === null) return null;
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > MAX_PC_ID_LENGTH ||
    value !== value.trim() ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    throw invalidRequest();
  }
  return value;
}

function snapshotOffer(value: PipecatOffer): OfferWireBody {
  if (
    !isRecord(value) ||
    typeof value.sdp !== "string" ||
    value.sdp.length === 0 ||
    value.sdp.length > MAX_SDP_LENGTH ||
    value.type !== "offer"
  ) {
    throw invalidRequest();
  }
  const pcId = snapshotPcId(value.pcId ?? null, true);
  return Object.freeze({
    sdp: value.sdp,
    type: "offer",
    pc_id: pcId,
    restart_pc: false,
  });
}

function snapshotCandidate(value: PipecatIceCandidate): CandidateWireBody {
  if (
    !isRecord(value) ||
    typeof value.candidate !== "string" ||
    value.candidate.length > MAX_CANDIDATE_LENGTH ||
    typeof value.sdpMid !== "string" ||
    value.sdpMid.length === 0 ||
    value.sdpMid.length > MAX_SDP_MID_LENGTH ||
    !Number.isInteger(value.sdpMLineIndex) ||
    value.sdpMLineIndex < 0 ||
    value.sdpMLineIndex > 128
  ) {
    throw invalidRequest();
  }
  return Object.freeze({
    candidate: value.candidate,
    sdp_mid: value.sdpMid,
    sdp_mline_index: value.sdpMLineIndex,
  });
}

function snapshotPatch(value: PipecatCandidateBatch): PatchWireBody {
  if (
    !isRecord(value) ||
    !Array.isArray(value.candidates) ||
    value.candidates.length === 0 ||
    value.candidates.length > MAX_CANDIDATES_PER_PATCH
  ) {
    throw invalidRequest();
  }
  const candidates = value.candidates.map(snapshotCandidate);
  return Object.freeze({
    pc_id: snapshotPcId(value.pcId, false) as string,
    candidates: Object.freeze(candidates),
  });
}

function snapshotDelete(pcId: string | null): DeleteWireBody {
  return Object.freeze({ pc_id: snapshotPcId(pcId, true) });
}

function decodeAnswer(value: unknown): PipecatAnswer {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["sdp", "type", "pc_id"]) ||
    typeof value.sdp !== "string" ||
    value.sdp.length === 0 ||
    value.sdp.length > MAX_SDP_LENGTH ||
    value.type !== "answer"
  ) {
    throw invalidResponse();
  }

  let pcId: string;
  try {
    pcId = snapshotPcId(value.pc_id, false) as string;
  } catch {
    throw invalidResponse();
  }
  return Object.freeze({ sdp: value.sdp, type: "answer", pc_id: pcId });
}

function snapshotDeadline(value: number | undefined): number {
  const deadline = value ?? DEFAULT_PIPECAT_SIGNALING_DEADLINE_MS;
  if (
    !Number.isInteger(deadline) ||
    deadline <= 0 ||
    deadline > MAX_DEADLINE_MS
  ) {
    throw invalidConfiguration();
  }
  return deadline;
}

function authorizationHeader(
  headers: Readonly<Record<string, string>>,
): string {
  const authorization = headers.Authorization;
  if (
    typeof authorization !== "string" ||
    authorization.length > MAX_AUTHORIZATION_LENGTH ||
    !/^Bearer [^\s\u0000-\u001f\u007f]+$/.test(authorization)
  ) {
    throw new PipecatSignalingApiError(
      "Authentication is required for Pipecat signaling",
      "authentication_required",
      401,
    );
  }
  return authorization;
}

function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new SignalingOperationAborted();
}

/** Race providers or test doubles which do not observe AbortSignal. */
function settleOnAbort<T>(
  operation: Promise<T>,
  signal: AbortSignal,
): Promise<T> {
  if (signal.aborted) return Promise.reject(new SignalingOperationAborted());

  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      reject(new SignalingOperationAborted());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

function withinDeadline<T>(
  operation: (signal: AbortSignal) => Promise<T>,
  deadlineMs: number,
  callerSignal?: AbortSignal,
): Promise<T> {
  if (callerSignal?.aborted) {
    return Promise.reject(
      new PipecatSignalingApiError(
        "Pipecat signaling request was aborted",
        "aborted",
      ),
    );
  }

  const controller = new AbortController();
  let timedOut = false;
  const onCallerAbort = () => controller.abort();
  callerSignal?.addEventListener("abort", onCallerAbort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, deadlineMs);

  let pending: Promise<T>;
  try {
    pending = operation(controller.signal);
  } catch (error) {
    pending = Promise.reject(error);
  }

  return settleOnAbort(pending, controller.signal)
    .catch((error: unknown) => {
      if (timedOut) {
        throw new PipecatSignalingApiError(
          "Pipecat signaling request timed out",
          "timed_out",
        );
      }
      if (callerSignal?.aborted || error instanceof SignalingOperationAborted) {
        throw new PipecatSignalingApiError(
          "Pipecat signaling request was aborted",
          "aborted",
        );
      }
      if (error instanceof PipecatSignalingApiError) throw error;
      throw requestFailed();
    })
    .finally(() => {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", onCallerAbort);
    });
}

/**
 * Bind one bootstrap-validated opaque URL to a fresh-auth, bounded HTTP port.
 * No locator, bearer, SDP, candidate, TURN credential, or peer ID is logged or
 * copied into an error.
 */
export function createPipecatSignalingPort(
  signalingUrl: string,
  options: PipecatSignalingPortOptions = {},
): PipecatSignalingPort {
  const ownedUrl = snapshotSignalingUrl(signalingUrl);
  const deadlineMs = snapshotDeadline(options.deadlineMs);
  const authHeaderProvider = options.authHeaderProvider ?? getAuthHeaders;
  const fetcher: PipecatSignalingFetch =
    options.fetcher ?? ((input, init) => globalThis.fetch(input, init));
  let mutationTail: Promise<void> = Promise.resolve();

  const authenticatedRequest = async (
    method: "POST" | "PATCH" | "DELETE",
    body: OfferWireBody | PatchWireBody | DeleteWireBody,
    signal: AbortSignal,
  ): Promise<Response> => {
    throwIfAborted(signal);
    let authHeaders: Readonly<Record<string, string>>;
    try {
      authHeaders = await settleOnAbort(authHeaderProvider(), signal);
    } catch {
      throwIfAborted(signal);
      throw new PipecatSignalingApiError(
        "Pipecat signaling authentication failed",
        "authentication_failed",
      );
    }
    throwIfAborted(signal);
    const authorization = authorizationHeader(authHeaders);

    let response: Response;
    try {
      response = await settleOnAbort(
        fetcher(ownedUrl, {
          method,
          headers: {
            "Content-Type": "application/json",
            Authorization: authorization,
          },
          body: JSON.stringify(body),
          signal,
          credentials: "omit",
          cache: "no-store",
          referrerPolicy: "no-referrer",
        }),
        signal,
      );
    } catch {
      throwIfAborted(signal);
      throw requestFailed();
    }
    throwIfAborted(signal);
    if (!response.ok) throw requestFailed(response.status);
    return response;
  };

  const serializeMutation = <T>(operation: () => Promise<T>): Promise<T> => {
    const result = mutationTail.then(operation, operation);
    mutationTail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  };

  const port: PipecatSignalingPort = {
    offer: (offer, operationOptions = {}) => {
      let body: OfferWireBody;
      try {
        body = snapshotOffer(offer);
      } catch (error) {
        return Promise.reject(
          error instanceof PipecatSignalingApiError ? error : invalidRequest(),
        );
      }
      return withinDeadline(async (signal) => {
        const response = await authenticatedRequest("POST", body, signal);
        let value: unknown;
        try {
          value = await settleOnAbort(response.json(), signal);
        } catch {
          throwIfAborted(signal);
          throw invalidResponse();
        }
        throwIfAborted(signal);
        return decodeAnswer(value);
      }, deadlineMs, operationOptions.signal);
    },

    patchCandidates: (batch, operationOptions = {}) => {
      let body: PatchWireBody;
      try {
        body = snapshotPatch(batch);
      } catch (error) {
        return Promise.reject(
          error instanceof PipecatSignalingApiError ? error : invalidRequest(),
        );
      }
      return withinDeadline(
        (signal) =>
          serializeMutation(async () => {
            await authenticatedRequest("PATCH", body, signal);
          }),
        deadlineMs,
        operationOptions.signal,
      );
    },

    deletePeer: (pcId, operationOptions = {}) => {
      let body: DeleteWireBody;
      try {
        body = snapshotDelete(pcId);
      } catch (error) {
        return Promise.reject(
          error instanceof PipecatSignalingApiError ? error : invalidRequest(),
        );
      }
      return withinDeadline(
        (signal) =>
          serializeMutation(async () => {
            await authenticatedRequest("DELETE", body, signal);
          }),
        deadlineMs,
        operationOptions.signal,
      );
    },
  };

  return Object.freeze(port);
}
