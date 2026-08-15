import fs from "node:fs";
import path from "node:path";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  AUDIO_CLOCK_MAX_INTERRUPTION_MS,
  AUDIO_CLOCK_MAX_TRANSITIONS,
  AUDIO_CLOCK_QUANTUM_FRAMES,
  AUDIO_CLOCK_REQUIRED_SILENCE_MS,
  audioClockCleanupComplete,
  audioClockFailureMessage,
  interruptionClockBracket,
  type AudioClockBracketFailure,
  type AudioClockEvidence,
} from "../src/app/e2e/voice/audio-clock-diagnostics";
import { interruptionAttribution } from "../src/app/e2e/voice/proof";
import {
  isCanonicalPrivateIpv4,
  parseBrowserRtcNetworkMode,
  RELAY_GATEWAY_ATTESTATION_GLOBAL,
  type BrowserRtcEvidence,
  type RtcSelectedCandidatePairEvidence,
} from "../src/app/e2e/voice/rtc-diagnostics";

const EXPECTED_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76";
const EXPECTED_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578";
const EXPECTED_PROFILE_ID = "pipecat-fake-rtc-v1";
const EXPECTED_TRANSCRIPTS = ["Hello tutor.", "Actually, stop."] as const;
const PIPECAT_API_URL = "http://127.0.0.1:8101";
const LOCAL_ACTIVE_RMS = 0.005;
const LOCAL_REGION_BRIDGE_MS = 500;
const REMOTE_ACTIVE_RMS = 0.02;
const REMOTE_SILENCE_RMS = 0.012;
const REMOTE_ATTRIBUTION_TOLERANCE_MS = 100;
const PIPECAT_NETWORK_MODE = parseBrowserRtcNetworkMode(
  process.env.VOICE_E2E_NETWORK
);
const COTURN_SPKI_ENV = "VOICE_E2E_COTURN_SPKI_SHA256_B64";
const COTURN_GATEWAY_ENV = "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4";
const CALL_ID_ENV = "VOICE_E2E_CALL_ID";
const SPKI_ARGUMENT_PREFIX = "--ignore-certificate-errors-spki-list=";
const SPKI_SHA256_B64 = /^[A-Za-z0-9+/]{43}=$/;
const CANONICAL_UUID4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const PIPECAT_PROOF_WAIT_TIMEOUT_MS = 45_000;
export const PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS = 20_000;
export const PIPECAT_SPEC_SETUP_MARGIN_MS = 30_000;
export const PIPECAT_SPEC_TIMEOUT_MS = 110_000;
// Mirrors the outer runner contract without changing its watchdog behavior.
export const PIPECAT_RUNNER_WATCHDOG_CONTRACT_MS = 120_000;
export const PROOF_TIMEOUT_PROGRESS_PREFIX =
  "VOICE_PIPECAT_PROOF_TIMEOUT_PROGRESS=";
export const PROOF_TIMEOUT_PROGRESS_MAX_BYTES = 2_048;
const PROOF_TIMEOUT_PROGRESS_MAX_COUNTER = 2_147_483_647;

const EXPECTED_RELAY_CALL_ID = (() => {
  const configured = process.env[CALL_ID_ENV];
  if (PIPECAT_NETWORK_MODE === "direct") {
    if (configured !== undefined) {
      throw new Error("Direct Pipecat proof does not accept a fixed call identity");
    }
    return null;
  }
  if (configured === undefined || !CANONICAL_UUID4.test(configured)) {
    throw new Error("Relay Pipecat proof call identity is unavailable");
  }
  return configured;
})();

// Network traces retain the bearer locator, Authorization header, SDP, and ICE
// bodies on failure. This proof emits only its deliberately sanitized JSON.
test.use({ trace: "off", video: "off", screenshot: "off" });

interface AudioSample {
  readonly t_ms: number;
  readonly rms: number;
}

interface EventEnvelope {
  readonly schema_version: number;
  readonly event_id: string;
  readonly event_type: string;
  readonly trace_id: string;
  readonly voice_call_id: string;
  readonly session_id: string;
  readonly turn_id?: string;
  readonly producer_id: string;
  readonly producer_sequence: number;
  readonly causation_id?: string;
  readonly payload: Record<string, unknown>;
}

interface ObservedEvent {
  readonly t_ms: number;
  readonly event: EventEnvelope;
}

interface TrackEvidence {
  readonly id: string;
  readonly kind: string;
  readonly label: string;
  readonly observed_at_ms: number;
  readonly enabled_at_observation: boolean;
  readonly muted_at_observation: boolean;
  readonly ready_state_at_observation: string;
  readonly media_stream_track_enabled: boolean;
  readonly muted: boolean;
  readonly ready_state: string;
}

interface PipecatAssignmentEvidence {
  readonly runtime: "pipecat_smallwebrtc_v1";
  readonly trace_id: string;
  readonly voice_call_id: string;
  readonly session_id: string;
  readonly agent_id: string;
  readonly profile_id: string;
  readonly peer_reservation_id: string;
  readonly event_protocol: "rtvi-murmur-v2";
}

interface BrowserSnapshot {
  readonly schema_version: number;
  readonly status: string;
  readonly phase: string;
  readonly voice_call_id: string;
  readonly assignment: PipecatAssignmentEvidence | null;
  readonly local_track: TrackEvidence | null;
  readonly remote_track: TrackEvidence | null;
  readonly microphone_publication: unknown;
  readonly local_track_released: boolean;
  readonly remote_track_released: boolean;
  readonly remote_audio_element_attached: boolean;
  readonly remote_audio_element_count: number;
  readonly local_samples: readonly AudioSample[];
  readonly remote_samples: readonly AudioSample[];
  readonly events: readonly ObservedEvent[];
  readonly errors: readonly string[];
  readonly logs: readonly string[];
  readonly connection_gestures: readonly {
    readonly sequence: number;
    readonly action: "prepare" | "activate";
  }[];
  readonly audio_clock: AudioClockEvidence;
  readonly rtc: BrowserRtcEvidence;
  readonly disconnect_requested: boolean;
  readonly hook_assignment_cleared: boolean;
}

interface ActiveRegion {
  readonly start_ms: number;
  readonly end_ms: number;
  readonly active_samples: number;
}

export interface ProofTimeoutProgressCapsule {
  readonly schema_version: 1;
  readonly kind: "pipecat_proof_wait_timeout";
  readonly snapshot: {
    readonly assignment_present: boolean;
    readonly harness_error_count: number;
    readonly connection_gesture_count: number;
    readonly local_track_present: boolean;
    readonly remote_track_present: boolean;
    readonly remote_audio_attached: boolean;
  };
  readonly events: {
    readonly total: number;
    readonly agent_ready: number;
    readonly turn_committed: number;
    readonly speech_started: number;
    readonly speech_stopped: number;
    readonly speech_stopped_interrupted: number;
    readonly speech_stopped_completed: number;
  };
  readonly clock: {
    readonly bracket_status: "pending" | "passed" | "failed";
    readonly bracket_failure: AudioClockBracketFailure | "none";
    readonly worklet_loaded: boolean;
    readonly local_attached: boolean;
    readonly remote_attached: boolean;
    readonly local_processed_blocks: number;
    readonly remote_processed_blocks: number;
    readonly local_active_regions: number;
    readonly remote_active_regions: number;
    readonly local_correction_pending: boolean;
    readonly remote_correction_pending: boolean;
    readonly local_failed: boolean;
    readonly remote_failed: boolean;
  };
  readonly pcm: {
    readonly local_sample_count: number;
    readonly remote_sample_count: number;
    readonly local_active_region_count: number;
    readonly second_local_region_present: boolean;
    readonly remote_silence_present: boolean;
    readonly remote_audio_before_second_local: boolean;
  };
  readonly rtc: {
    readonly peer_connection_count: number;
    readonly selected_candidate_pair_count: number;
    readonly outbound_bytes_present: boolean;
    readonly outbound_packets_present: boolean;
    readonly inbound_bytes_present: boolean;
    readonly inbound_packets_present: boolean;
  };
  readonly gates: {
    readonly local_disabled_at_observation: boolean;
    readonly local_live_at_observation: boolean;
    readonly local_precedes_ready: boolean;
    readonly first_event_agent_ready: boolean;
    readonly first_reply_interrupted: boolean;
    readonly second_turn_present: boolean;
    readonly second_reply_started: boolean;
    readonly second_reply_after_silence: boolean;
    readonly second_reply_completed: boolean;
    readonly attribution_observation_complete: boolean;
    readonly stale_audio_detected: boolean;
    readonly proof_ready: boolean;
  };
}

interface PipecatTerminalStatus {
  readonly schema_version: 1;
  readonly status: "pending" | "passed";
  readonly runtime: "pipecat_smallwebrtc_v1";
  readonly profile_id: string;
  readonly session_id: string;
  readonly voice_call_id: string;
  readonly reservation: {
    readonly state: "reserved" | "negotiating" | "active" | "terminal";
    readonly cleanup_complete: boolean;
    readonly terminal_reason: string | null;
    readonly retryable: boolean | null;
  };
  readonly control_plane: {
    readonly bootstrap_active_assignment_count: number;
    readonly bootstrap_active_lock_count: number;
    readonly signaling_active_call_count: number;
    readonly runtime_handle_retained: boolean;
    readonly cleanup_retry_pending: boolean;
    readonly runtime_observer_pending: boolean;
    readonly expiry_pending: boolean;
    readonly trusted_release_pending: boolean;
  };
  readonly fake_media: {
    readonly input_frame_count: number;
    readonly final_transcripts: readonly string[];
    readonly llm_response_count: number;
    readonly tts_frame_count: number;
    readonly tts_cancelled_count: number;
    readonly cleaned_processors: readonly string[];
    readonly media_contract_satisfied: boolean;
  };
}

interface SanitizedRequestTrace {
  bootstrapPosts: number;
  bootstrapCallIdAttested: boolean | null;
  signalingPosts: number;
  authenticatedSignalingPosts: number;
  signalingPatches: number;
  authenticatedSignalingPatches: number;
  signalingDeletes: number;
  authenticatedSignalingDeletes: number;
  signalingRequestsWithCookies: number;
  sessionEndPosts: number;
  terminalOrder: ("peer_delete" | "session_end")[];
}

async function readSnapshot(page: Page): Promise<BrowserSnapshot> {
  const text = await page.getByTestId("voice-e2e-snapshot").textContent();
  if (!text) throw new Error("Voice E2E snapshot is empty");
  return JSON.parse(text) as BrowserSnapshot;
}

async function assertNoHarnessError(page: Page): Promise<void> {
  const snapshot = await readSnapshot(page);
  if (snapshot.errors.length > 0) {
    throw new Error(`Voice E2E harness failed: ${snapshot.errors.join(" | ")}`);
  }
}

function activeRegions(
  samples: readonly AudioSample[],
  threshold: number,
  bridgeSilenceMs: number
): readonly ActiveRegion[] {
  const active = samples.filter((sample) => sample.rms >= threshold);
  const regions: ActiveRegion[] = [];
  for (const sample of active) {
    const previous = regions.at(-1);
    if (!previous || sample.t_ms - previous.end_ms > bridgeSilenceMs) {
      regions.push({
        start_ms: sample.t_ms,
        end_ms: sample.t_ms,
        active_samples: 1,
      });
      continue;
    }
    regions[regions.length - 1] = {
      ...previous,
      end_ms: sample.t_ms,
      active_samples: previous.active_samples + 1,
    };
  }
  return regions.filter((region) => region.active_samples >= 3);
}

function sustainedSilenceStart(
  samples: readonly AudioSample[],
  afterMs: number
): number | undefined {
  for (let start = 0; start < samples.length; start += 1) {
    const first = samples[start];
    if (!first || first.t_ms < afterMs || first.rms > REMOTE_SILENCE_RMS) continue;
    let end = start;
    while (end + 1 < samples.length) {
      const current = samples[end];
      const next = samples[end + 1];
      if (!current || !next || next.t_ms - current.t_ms > 60) break;
      if (next.rms > REMOTE_SILENCE_RMS) break;
      end += 1;
    }
    const final = samples[end];
    if (final && final.t_ms - first.t_ms >= AUDIO_CLOCK_REQUIRED_SILENCE_MS) {
      return first.t_ms;
    }
  }
  return undefined;
}

function eventsOf(
  snapshot: BrowserSnapshot,
  eventType: string
): readonly ObservedEvent[] {
  return snapshot.events.filter(({ event }) => event.event_type === eventType);
}

function proofReady(snapshot: BrowserSnapshot): boolean {
  if (snapshot.errors.length > 0 || snapshot.assignment === null) return false;
  const localTrack = snapshot.local_track;
  const remoteTrack = snapshot.remote_track;
  const firstReady = eventsOf(snapshot, "agent_ready")[0];
  if (
    !localTrack ||
    !remoteTrack ||
    !firstReady ||
    localTrack.enabled_at_observation ||
    localTrack.ready_state_at_observation !== "live" ||
    localTrack.observed_at_ms >= firstReady.t_ms
  ) {
    return false;
  }
  if (interruptionClockBracket(snapshot.audio_clock).status !== "passed") {
    return false;
  }
  const localPcmRegions = activeRegions(snapshot.local_samples, LOCAL_ACTIVE_RMS, 500);
  const secondPcmOnset = localPcmRegions[1]?.start_ms;
  if (secondPcmOnset === undefined) return false;
  const silenceStart = sustainedSilenceStart(snapshot.remote_samples, secondPcmOnset);
  if (silenceStart === undefined) return false;
  const turns = eventsOf(snapshot, "turn_committed");
  const firstTurnId = turns[0]?.event.turn_id;
  const secondTurnId = turns[1]?.event.turn_id;
  const interrupted = eventsOf(snapshot, "assistant_speech_stopped").find(
    ({ event }) => event.payload.reason === "interrupted" && event.turn_id === firstTurnId
  );
  if (!interrupted || !secondTurnId) return false;
  const nextSpeechStart = eventsOf(snapshot, "assistant_speech_started").find(
    ({ event, t_ms }) => event.turn_id === secondTurnId && t_ms > interrupted.t_ms
  );
  if (!nextSpeechStart || nextSpeechStart.t_ms < silenceStart + AUDIO_CLOCK_REQUIRED_SILENCE_MS) {
    return false;
  }
  const nextSpeechStop = eventsOf(snapshot, "assistant_speech_stopped").find(
    ({ event, t_ms }) =>
      event.turn_id === secondTurnId &&
      event.payload.speech_id === nextSpeechStart.event.payload.speech_id &&
      event.payload.reason === "completed" &&
      t_ms >= nextSpeechStart.t_ms
  );
  const attribution = interruptionAttribution({
    samples: snapshot.remote_samples,
    silenceStartMs: silenceStart,
    nextAssistantSpeechStartMs: nextSpeechStart.t_ms,
    activeRms: REMOTE_ACTIVE_RMS,
    requiredSilenceMs: AUDIO_CLOCK_REQUIRED_SILENCE_MS,
    samplingToleranceMs: REMOTE_ATTRIBUTION_TOLERANCE_MS,
  });
  return (
    snapshot.remote_audio_element_attached &&
    snapshot.events[0]?.event.event_type === "agent_ready" &&
    turns.length >= 2 &&
    snapshot.remote_samples.some(
      (sample) => sample.t_ms < secondPcmOnset && sample.rms >= REMOTE_ACTIVE_RMS
    ) &&
    nextSpeechStop !== undefined &&
    snapshot.rtc.peer_connection_count === 1 &&
    snapshot.rtc.selected_candidate_pair_count === 1 &&
    snapshot.rtc.outbound_audio.bytes > 0 &&
    snapshot.rtc.outbound_audio.packets > 0 &&
    snapshot.rtc.inbound_audio.bytes > 0 &&
    snapshot.rtc.inbound_audio.packets > 0 &&
    (PIPECAT_NETWORK_MODE === "direct" || relayRtcEvidenceAttested(snapshot.rtc)) &&
    attribution.observation_complete &&
    !attribution.stale_audio_detected
  );
}

export function relayCandidatePairAttested(
  pair: RtcSelectedCandidatePairEvidence | null
): boolean {
  return (
    pair !== null &&
    pair.state === "succeeded" &&
    pair.nominated === true &&
    pair.bytes_sent > 0 &&
    pair.bytes_received > 0 &&
    pair.local.candidate_type === "relay" &&
    pair.local.protocol === "udp" &&
    pair.local.relay_protocol === "tls" &&
    pair.remote.candidate_type === "host" &&
    pair.remote.protocol === "udp" &&
    pair.remote.relay_protocol === null
  );
}

export function relayRtcEvidenceAttested(rtc: BrowserRtcEvidence): boolean {
  return (
    rtc.relay_policy_attested === true &&
    rtc.peer_connection_count === 1 &&
    rtc.selected_candidate_pair_count === 1 &&
    relayCandidatePairAttested(
      rtc.peer_connections[0]?.selected_candidate_pair ?? null
    )
  );
}

function canonicalSpkiPin(value: unknown): value is string {
  if (typeof value !== "string" || !SPKI_SHA256_B64.test(value)) return false;
  const decoded = Buffer.from(value, "base64");
  return decoded.length === 32 && decoded.toString("base64") === value;
}

function requiredCoturnSpkiPin(): string {
  const value = process.env[COTURN_SPKI_ENV];
  if (!canonicalSpkiPin(value)) {
    throw new Error("Pipecat relay Chromium SPKI contract is invalid");
  }
  return value;
}

function requiredCoturnGatewayIpv4(): string {
  const value = process.env[COTURN_GATEWAY_ENV];
  if (!isCanonicalPrivateIpv4(value)) {
    throw new Error("Pipecat relay gateway contract is invalid");
  }
  return value;
}

function certificateBypassArgument(argument: string): boolean {
  return (
    argument.startsWith("--ignore-certificate-errors") ||
    argument.startsWith("--ignore-ssl-errors") ||
    argument.startsWith("--allow-insecure-localhost")
  );
}

export function chromiumSpkiPinAttested(
  argumentsList: readonly string[],
  expectedPin: string
): boolean {
  if (!canonicalSpkiPin(expectedPin)) return false;
  const exactArgument = `${SPKI_ARGUMENT_PREFIX}${expectedPin}`;
  const certificateArguments = argumentsList.filter(certificateBypassArgument);
  return (
    certificateArguments.length === 1 &&
    certificateArguments[0] === exactArgument
  );
}

export async function browserRelayGatewayAttested(
  page: Page,
  expectedGatewayIpv4: string
): Promise<boolean> {
  if (!isCanonicalPrivateIpv4(expectedGatewayIpv4)) return false;
  try {
    return await page.evaluate(
      async ({ propertyName, expectedIpv4 }) => {
        const descriptor = Object.getOwnPropertyDescriptor(
          globalThis,
          propertyName
        );
        if (
          descriptor === undefined ||
          !("value" in descriptor) ||
          descriptor.configurable !== true ||
          descriptor.enumerable !== false ||
          descriptor.writable !== false ||
          typeof descriptor.value !== "function" ||
          !Object.isFrozen(descriptor.value)
        ) {
          return false;
        }
        try {
          return (
            (await Reflect.apply(descriptor.value, undefined, [expectedIpv4])) ===
            true
          );
        } catch {
          return false;
        }
      },
      {
        propertyName: RELAY_GATEWAY_ATTESTATION_GLOBAL,
        expectedIpv4: expectedGatewayIpv4,
      }
    );
  } catch {
    return false;
  }
}

function boundedProgressCounter(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) return 0;
  return Math.min(value, PROOF_TIMEOUT_PROGRESS_MAX_COUNTER);
}

export function buildProofTimeoutProgressCapsule(
  snapshot: BrowserSnapshot
): ProofTimeoutProgressCapsule {
  const firstReady = eventsOf(snapshot, "agent_ready")[0];
  const turns = eventsOf(snapshot, "turn_committed");
  const speechStarts = eventsOf(snapshot, "assistant_speech_started");
  const speechStops = eventsOf(snapshot, "assistant_speech_stopped");
  const firstTurnId = turns[0]?.event.turn_id;
  const secondTurnId = turns[1]?.event.turn_id;
  const interrupted = speechStops.find(
    ({ event }) => event.payload.reason === "interrupted" && event.turn_id === firstTurnId
  );
  const localPcmRegions = activeRegions(snapshot.local_samples, LOCAL_ACTIVE_RMS, 500);
  const secondPcmOnset = localPcmRegions[1]?.start_ms;
  const silenceStart =
    secondPcmOnset === undefined
      ? undefined
      : sustainedSilenceStart(snapshot.remote_samples, secondPcmOnset);
  const nextSpeechStart =
    interrupted && secondTurnId
      ? speechStarts.find(
          ({ event, t_ms }) => event.turn_id === secondTurnId && t_ms > interrupted.t_ms
        )
      : undefined;
  const nextSpeechStop =
    nextSpeechStart && secondTurnId
      ? speechStops.find(
          ({ event, t_ms }) =>
            event.turn_id === secondTurnId &&
            event.payload.speech_id === nextSpeechStart.event.payload.speech_id &&
            event.payload.reason === "completed" &&
            t_ms >= nextSpeechStart.t_ms
        )
      : undefined;
  const attribution =
    silenceStart === undefined || nextSpeechStart === undefined
      ? undefined
      : interruptionAttribution({
          samples: snapshot.remote_samples,
          silenceStartMs: silenceStart,
          nextAssistantSpeechStartMs: nextSpeechStart.t_ms,
          activeRms: REMOTE_ACTIVE_RMS,
          requiredSilenceMs: AUDIO_CLOCK_REQUIRED_SILENCE_MS,
          samplingToleranceMs: REMOTE_ATTRIBUTION_TOLERANCE_MS,
        });
  const clockBracket = interruptionClockBracket(snapshot.audio_clock);
  const localTrack = snapshot.local_track;

  return {
    schema_version: 1,
    kind: "pipecat_proof_wait_timeout",
    snapshot: {
      assignment_present: snapshot.assignment !== null,
      harness_error_count: boundedProgressCounter(snapshot.errors.length),
      connection_gesture_count: boundedProgressCounter(snapshot.connection_gestures.length),
      local_track_present: localTrack !== null,
      remote_track_present: snapshot.remote_track !== null,
      remote_audio_attached: snapshot.remote_audio_element_attached,
    },
    events: {
      total: boundedProgressCounter(snapshot.events.length),
      agent_ready: boundedProgressCounter(eventsOf(snapshot, "agent_ready").length),
      turn_committed: boundedProgressCounter(turns.length),
      speech_started: boundedProgressCounter(speechStarts.length),
      speech_stopped: boundedProgressCounter(speechStops.length),
      speech_stopped_interrupted: boundedProgressCounter(
        speechStops.filter(({ event }) => event.payload.reason === "interrupted").length
      ),
      speech_stopped_completed: boundedProgressCounter(
        speechStops.filter(({ event }) => event.payload.reason === "completed").length
      ),
    },
    clock: {
      bracket_status: clockBracket.status,
      bracket_failure: clockBracket.failure_code ?? "none",
      worklet_loaded: snapshot.audio_clock.worklet_loaded,
      local_attached: snapshot.audio_clock.local.attached,
      remote_attached: snapshot.audio_clock.remote.attached,
      local_processed_blocks: boundedProgressCounter(
        snapshot.audio_clock.local.processed_block_count
      ),
      remote_processed_blocks: boundedProgressCounter(
        snapshot.audio_clock.remote.processed_block_count
      ),
      local_active_regions: boundedProgressCounter(
        snapshot.audio_clock.local.active_region_count
      ),
      remote_active_regions: boundedProgressCounter(
        snapshot.audio_clock.remote.active_region_count
      ),
      local_correction_pending: snapshot.audio_clock.local.stale_frame_correction_pending,
      remote_correction_pending: snapshot.audio_clock.remote.stale_frame_correction_pending,
      local_failed: snapshot.audio_clock.local.failure_code !== null,
      remote_failed: snapshot.audio_clock.remote.failure_code !== null,
    },
    pcm: {
      local_sample_count: boundedProgressCounter(snapshot.local_samples.length),
      remote_sample_count: boundedProgressCounter(snapshot.remote_samples.length),
      local_active_region_count: boundedProgressCounter(localPcmRegions.length),
      second_local_region_present: secondPcmOnset !== undefined,
      remote_silence_present: silenceStart !== undefined,
      remote_audio_before_second_local:
        secondPcmOnset !== undefined &&
        snapshot.remote_samples.some(
          (sample) => sample.t_ms < secondPcmOnset && sample.rms >= REMOTE_ACTIVE_RMS
        ),
    },
    rtc: {
      peer_connection_count: boundedProgressCounter(snapshot.rtc.peer_connection_count),
      selected_candidate_pair_count: boundedProgressCounter(
        snapshot.rtc.selected_candidate_pair_count
      ),
      outbound_bytes_present: snapshot.rtc.outbound_audio.bytes > 0,
      outbound_packets_present: snapshot.rtc.outbound_audio.packets > 0,
      inbound_bytes_present: snapshot.rtc.inbound_audio.bytes > 0,
      inbound_packets_present: snapshot.rtc.inbound_audio.packets > 0,
    },
    gates: {
      local_disabled_at_observation: localTrack?.enabled_at_observation === false,
      local_live_at_observation: localTrack?.ready_state_at_observation === "live",
      local_precedes_ready:
        localTrack !== null && firstReady !== undefined && localTrack.observed_at_ms < firstReady.t_ms,
      first_event_agent_ready: snapshot.events[0]?.event.event_type === "agent_ready",
      first_reply_interrupted: interrupted !== undefined,
      second_turn_present: secondTurnId !== undefined,
      second_reply_started: nextSpeechStart !== undefined,
      second_reply_after_silence:
        nextSpeechStart !== undefined &&
        silenceStart !== undefined &&
        nextSpeechStart.t_ms >= silenceStart + AUDIO_CLOCK_REQUIRED_SILENCE_MS,
      second_reply_completed: nextSpeechStop !== undefined,
      attribution_observation_complete: attribution?.observation_complete === true,
      stale_audio_detected: attribution?.stale_audio_detected === true,
      proof_ready: proofReady(snapshot),
    },
  };
}

export function serializeProofTimeoutProgressCapsule(snapshot: BrowserSnapshot): string {
  const rendered = `${PROOF_TIMEOUT_PROGRESS_PREFIX}${JSON.stringify(
    buildProofTimeoutProgressCapsule(snapshot)
  )}`;
  if (new TextEncoder().encode(rendered).byteLength >= PROOF_TIMEOUT_PROGRESS_MAX_BYTES) {
    throw new Error("Pipecat proof timeout progress capsule exceeded its fixed size bound");
  }
  return rendered;
}

async function waitForProof(page: Page, timeoutMs: number): Promise<BrowserSnapshot> {
  const deadline = Date.now() + timeoutMs;
  let lastSnapshot: BrowserSnapshot | null = null;
  while (Date.now() < deadline) {
    await assertNoHarnessError(page);
    const snapshot = await readSnapshot(page);
    lastSnapshot = snapshot;
    const clockBracket = interruptionClockBracket(snapshot.audio_clock);
    if (clockBracket.status === "failed") {
      throw new Error(
        audioClockFailureMessage(snapshot.audio_clock, clockBracket.failure_code)
      );
    }
    if (proofReady(snapshot)) return snapshot;
    await page.waitForTimeout(100);
  }
  const snapshot = lastSnapshot ?? (await readSnapshot(page));
  throw new Error(serializeProofTimeoutProgressCapsule(snapshot));
}

function requiredAbsoluteEnv(name: string): string {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path`);
  }
  return value;
}

function requiredPipecatApiUrl(): string {
  const value = process.env.VOICE_E2E_API_URL;
  if (value !== PIPECAT_API_URL) {
    throw new Error(`VOICE_E2E_API_URL must be ${PIPECAT_API_URL}`);
  }
  return value;
}

export function signalingSecretLabels(value: unknown): readonly string[] {
  const serialized = JSON.stringify(value);
  return [
    { label: "signaling locator", pattern: /\/api\/voice\/pipecat\/signal\//i },
    {
      label: "network URL",
      pattern: /(?:(?:https?|wss?):\/\/|(?:stuns?|turns?):)/i,
    },
    { label: "authorization value", pattern: /Bearer\s+/i },
    { label: "authorization field", pattern: /"authorization"/i },
    { label: "raw SDP field", pattern: /"sdp"/i },
    { label: "raw ICE field", pattern: /"ice_servers"|ice-(?:ufrag|pwd):/i },
    { label: "raw ICE candidate", pattern: /candidate:/i },
    { label: "raw peer ID field", pattern: /"pc_id"/i },
    { label: "raw IPv4 address", pattern: /\b(?:\d{1,3}\.){3}\d{1,3}\b/ },
  ]
    .filter(({ pattern }) => pattern.test(serialized))
    .map(({ label }) => label);
}

function expectNoSignalingSecrets(value: unknown): void {
  const forbidden = signalingSecretLabels(value);
  expect(forbidden).toEqual([]);
}

function writeResultAtomically(resultPath: string, result: object): void {
  const temporary = `${resultPath}.${process.pid}.tmp`;
  fs.mkdirSync(path.dirname(resultPath), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(result, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  fs.renameSync(temporary, resultPath);
  expectNoSignalingSecrets(JSON.parse(fs.readFileSync(resultPath, "utf8")));
}

function observeSanitizedRequests(page: Page): SanitizedRequestTrace {
  const trace: SanitizedRequestTrace = {
    bootstrapPosts: 0,
    bootstrapCallIdAttested: null,
    signalingPosts: 0,
    authenticatedSignalingPosts: 0,
    signalingPatches: 0,
    authenticatedSignalingPatches: 0,
    signalingDeletes: 0,
    authenticatedSignalingDeletes: 0,
    signalingRequestsWithCookies: 0,
    sessionEndPosts: 0,
    terminalOrder: [],
  };
  page.on("request", (request) => {
    let parsed: URL;
    try {
      parsed = new URL(request.url());
    } catch {
      return;
    }
    if (parsed.origin !== PIPECAT_API_URL) return;
    const method = request.method();
    if (parsed.pathname === "/api/voice/session" && method === "POST") {
      trace.bootstrapPosts += 1;
      if (EXPECTED_RELAY_CALL_ID !== null) {
        let attested = false;
        try {
          const body = request.postDataJSON() as unknown;
          attested =
            typeof body === "object" &&
            body !== null &&
            "session_id" in body &&
            body.session_id === EXPECTED_SESSION_ID &&
            "voice_call_id" in body &&
            body.voice_call_id === EXPECTED_RELAY_CALL_ID;
        } catch {
          attested = false;
        }
        trace.bootstrapCallIdAttested =
          trace.bootstrapCallIdAttested !== false && attested;
      }
      return;
    }
    if (parsed.pathname === "/api/voice/session/end" && method === "POST") {
      trace.sessionEndPosts += 1;
      trace.terminalOrder.push("session_end");
      return;
    }
    if (!parsed.pathname.startsWith("/api/voice/pipecat/signal/")) return;
    const headers = request.headers();
    if (typeof headers.cookie === "string" && headers.cookie.length > 0) {
      trace.signalingRequestsWithCookies += 1;
    }
    if (method === "POST") {
      trace.signalingPosts += 1;
      if (headers.authorization === "Bearer voice-e2e") {
        trace.authenticatedSignalingPosts += 1;
      }
    } else if (method === "PATCH") {
      trace.signalingPatches += 1;
      if (headers.authorization === "Bearer voice-e2e") {
        trace.authenticatedSignalingPatches += 1;
      }
    } else if (method === "DELETE") {
      trace.signalingDeletes += 1;
      if (headers.authorization === "Bearer voice-e2e") {
        trace.authenticatedSignalingDeletes += 1;
      }
      trace.terminalOrder.push("peer_delete");
    }
  });
  return trace;
}

async function readTerminalStatus(
  request: APIRequestContext,
  apiUrl: string,
  assignment: PipecatAssignmentEvidence
): Promise<PipecatTerminalStatus> {
  const response = await request.post(`${apiUrl}/_e2e/pipecat/status`, {
    headers: { Authorization: "Bearer voice-e2e" },
    data: {
      session_id: assignment.session_id,
      voice_call_id: assignment.voice_call_id,
    },
  });
  if (response.status() !== 200) {
    throw new Error(`Pipecat E2E status returned HTTP ${response.status()}`);
  }
  return (await response.json()) as PipecatTerminalStatus;
}

test("real browser media crosses Pipecat SmallWebRTC and cleans one peer", async ({
  page,
  request,
}) => {
  test.setTimeout(PIPECAT_SPEC_TIMEOUT_MS);
  const resultPath = requiredAbsoluteEnv("VOICE_E2E_RESULT_PATH");
  const apiUrl = requiredPipecatApiUrl();
  const requestTrace = observeSanitizedRequests(page);

  await page.goto("/e2e/voice");
  const cdp = await page.context().newCDPSession(page);
  const browserCommandLine = await cdp.send("Browser.getBrowserCommandLine");
  let tlsSpkiPinAttested = false;
  let gatewayAttested = false;
  if (PIPECAT_NETWORK_MODE === "relay-tls") {
    if (browserCommandLine.arguments.includes("--mute-audio")) {
      throw new Error("Pipecat relay Chromium audio launch attestation failed");
    }
    tlsSpkiPinAttested = chromiumSpkiPinAttested(
      browserCommandLine.arguments,
      requiredCoturnSpkiPin()
    );
  } else {
    expect(browserCommandLine.arguments).not.toContain("--mute-audio");
  }
  await cdp.detach();
  if (PIPECAT_NETWORK_MODE === "relay-tls" && !tlsSpkiPinAttested) {
    throw new Error("Pipecat relay Chromium SPKI attestation failed");
  }
  await expect(page.getByRole("heading", { name: "Browser media harness" })).toBeVisible();

  const activationButton = page.getByTestId("voice-e2e-activate");
  await expect(activationButton).toBeDisabled();
  await page.getByTestId("voice-e2e-start").click();
  await expect
    .poll(async () => {
      const prepared = await readSnapshot(page);
      return { phase: prepared.phase, status: prepared.status };
    })
    .toEqual({ phase: "awaiting_audio", status: "awaiting_audio" });

  const prepared = await readSnapshot(page);
  expect(prepared.connection_gestures).toEqual([
    { sequence: 1, action: "prepare" },
  ]);
  expect(prepared.assignment).toMatchObject({
    runtime: "pipecat_smallwebrtc_v1",
    profile_id: EXPECTED_PROFILE_ID,
  });
  if (EXPECTED_RELAY_CALL_ID === null) {
    expect(requestTrace.bootstrapCallIdAttested).toBeNull();
  } else {
    expect(prepared.voice_call_id).toBe(EXPECTED_RELAY_CALL_ID);
    expect(prepared.assignment?.voice_call_id).toBe(EXPECTED_RELAY_CALL_ID);
    expect(requestTrace.bootstrapCallIdAttested).toBe(true);
  }
  expect(prepared.local_track).toBeNull();
  expect(prepared.remote_track).toBeNull();
  expect(prepared.audio_clock).toMatchObject({
    schema_version: 1,
    worklet_loaded: true,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
    disposed: false,
    local: { attached: false },
    remote: { attached: false },
  });
  expect(prepared.audio_clock.sample_rate_hz).toBeGreaterThan(0);
  expect(prepared.rtc.peer_connection_count).toBe(0);
  expect(requestTrace.bootstrapPosts).toBe(1);
  expect(requestTrace.signalingPosts).toBe(0);
  expect(requestTrace.signalingPatches).toBe(0);
  expect(requestTrace.signalingDeletes).toBe(0);

  await expect(activationButton).toBeEnabled();
  await activationButton.click();
  const proof = await waitForProof(page, PIPECAT_PROOF_WAIT_TIMEOUT_MS);
  const assignment = proof.assignment;
  expect(assignment).not.toBeNull();
  if (!assignment) throw new Error("Accepted Pipecat assignment disappeared");
  if (EXPECTED_RELAY_CALL_ID !== null) {
    expect(proof.voice_call_id).toBe(EXPECTED_RELAY_CALL_ID);
    expect(assignment.voice_call_id).toBe(EXPECTED_RELAY_CALL_ID);
  }

  expect(proof.schema_version).toBe(1);
  expect(proof.errors).toEqual([]);
  expect(proof.connection_gestures).toEqual([
    { sequence: 1, action: "prepare" },
    { sequence: 2, action: "activate" },
  ]);
  expect(assignment).toMatchObject({
    runtime: "pipecat_smallwebrtc_v1",
    agent_id: EXPECTED_AGENT_ID,
    session_id: EXPECTED_SESSION_ID,
    profile_id: EXPECTED_PROFILE_ID,
    event_protocol: "rtvi-murmur-v2",
  });
  expect(Object.keys(assignment).sort()).toEqual(
    [
      "agent_id",
      "event_protocol",
      "peer_reservation_id",
      "profile_id",
      "runtime",
      "session_id",
      "trace_id",
      "voice_call_id",
    ].sort()
  );
  expect(proof.voice_call_id).toBe(assignment.voice_call_id);
  expect(proof.microphone_publication).toBeNull();

  const firstReady = eventsOf(proof, "agent_ready")[0];
  expect(firstReady).toBeDefined();
  if (!firstReady) throw new Error("Canonical Pipecat readiness was not observed");
  expect(proof.events[0]?.event.event_type).toBe("agent_ready");
  expect(eventsOf(proof, "agent_ready")).toHaveLength(1);
  expect(proof.local_track).toMatchObject({
    kind: "audio",
    enabled_at_observation: false,
    ready_state_at_observation: "live",
    ready_state: "live",
  });
  expect(proof.local_track?.observed_at_ms).toBeLessThan(firstReady.t_ms);
  expect(proof.remote_track).toMatchObject({ kind: "audio", ready_state: "live" });
  expect(proof.remote_audio_element_attached).toBe(true);

  const eventIds = new Set<string>();
  let producerSequence = 0;
  for (const { event } of proof.events) {
    expect(event.schema_version).toBe(1);
    expect(event.trace_id).toBe(assignment.trace_id);
    expect(event.voice_call_id).toBe(assignment.voice_call_id);
    expect(event.session_id).toBe(assignment.session_id);
    expect(event.producer_id).toBe(`pipecat-${assignment.voice_call_id}`);
    expect(event.event_id).toMatch(/^event-[a-f0-9]+$/);
    expect(eventIds.has(event.event_id)).toBe(false);
    eventIds.add(event.event_id);
    expect(event.producer_sequence).toBeGreaterThan(producerSequence);
    producerSequence = event.producer_sequence;
  }

  const finalTranscripts = eventsOf(proof, "transcript_segment").filter(
    ({ event }) => event.payload.is_final === true
  );
  expect(finalTranscripts.map(({ event }) => event.payload.text)).toEqual(
    EXPECTED_TRANSCRIPTS
  );
  const turns = eventsOf(proof, "turn_committed");
  expect(turns).toHaveLength(2);
  expect(turns.map(({ event }) => event.payload.text)).toEqual(EXPECTED_TRANSCRIPTS);
  const turnIds = turns.map(({ event }) => event.turn_id);
  expect(turnIds.every((turnId) => typeof turnId === "string" && turnId.length > 0)).toBe(
    true
  );
  expect(new Set(turnIds).size).toBe(2);
  for (const [index, transcript] of finalTranscripts.entries()) {
    const turn = turns[index];
    expect(turn).toBeDefined();
    expect(transcript.event.producer_sequence).toBeLessThan(
      turn?.event.producer_sequence ?? 0
    );
    expect(transcript.t_ms).toBeLessThanOrEqual(turn?.t_ms ?? -1);
  }

  const clockBracket = interruptionClockBracket(proof.audio_clock);
  expect(clockBracket).toMatchObject({
    status: "passed",
    failure_code: null,
    sample_rate_hz: proof.audio_clock.sample_rate_hz,
    quantum_frames: AUDIO_CLOCK_QUANTUM_FRAMES,
  });
  expect(clockBracket.second_local_active_block_start_frame).not.toBeNull();
  expect(clockBracket.remote_silence_transition_block_end_frame).not.toBeNull();
  expect(clockBracket.interruption_upper_bound_frames).not.toBeNull();
  expect(clockBracket.interruption_upper_bound_ms).not.toBeNull();
  expect(clockBracket.interruption_upper_bound_ms).toBeGreaterThanOrEqual(0);
  expect(clockBracket.interruption_upper_bound_ms).toBeLessThanOrEqual(
    AUDIO_CLOCK_MAX_INTERRUPTION_MS
  );
  const localClockStart = clockBracket.second_local_active_block_start_frame;
  const remoteClockEnd = clockBracket.remote_silence_transition_block_end_frame;
  if (localClockStart === null || remoteClockEnd === null) {
    throw new Error("Audio sample-clock bracket was incomplete");
  }
  expect(clockBracket.interruption_upper_bound_frames).toBe(remoteClockEnd - localClockStart);
  expect(clockBracket.interruption_upper_bound_ms).toBe(
    Math.ceil(((remoteClockEnd - localClockStart) * 1_000_000) / proof.audio_clock.sample_rate_hz) /
      1_000
  );
  expect(proof.audio_clock.local).toMatchObject({
    attached: true,
    exact_track_id: proof.local_track?.id,
    threshold_rms: LOCAL_ACTIVE_RMS,
    active_region_count: 2,
    overflow: false,
    failure_code: null,
  });
  expect(proof.audio_clock.remote).toMatchObject({
    attached: true,
    exact_track_id: proof.remote_track?.id,
    threshold_rms: REMOTE_SILENCE_RMS,
    overflow: false,
    failure_code: null,
  });
  expect(proof.audio_clock.local.silence_hold_frames).toBe(
    Math.ceil(
      (LOCAL_REGION_BRIDGE_MS * proof.audio_clock.sample_rate_hz) /
        1000 /
        AUDIO_CLOCK_QUANTUM_FRAMES
    ) * AUDIO_CLOCK_QUANTUM_FRAMES
  );
  expect(proof.audio_clock.remote.silence_hold_frames).toBe(
    clockBracket.required_silence_frames
  );
  expect(proof.audio_clock.local.transitions.length).toBeLessThanOrEqual(
    AUDIO_CLOCK_MAX_TRANSITIONS
  );
  expect(proof.audio_clock.remote.transitions.length).toBeLessThanOrEqual(
    AUDIO_CLOCK_MAX_TRANSITIONS
  );
  expect(proof.audio_clock.local.processed_block_count).toBeGreaterThan(0);
  expect(proof.audio_clock.remote.processed_block_count).toBeGreaterThan(0);

  const localPcmRegions = activeRegions(proof.local_samples, LOCAL_ACTIVE_RMS, 500);
  expect(localPcmRegions).toHaveLength(2);
  const secondPcmOnset = localPcmRegions[1]?.start_ms;
  expect(secondPcmOnset).toBeDefined();
  if (secondPcmOnset === undefined) {
    throw new Error("Second microphone PCM region was not observed");
  }

  const speechStarts = eventsOf(proof, "assistant_speech_started");
  const interrupted = eventsOf(proof, "assistant_speech_stopped").find(
    ({ event }) =>
      event.turn_id === turnIds[0] && event.payload.reason === "interrupted"
  );
  expect(interrupted).toBeDefined();
  if (!interrupted) throw new Error("First Pipecat reply was not interrupted");
  const firstSpeechStart = speechStarts.find(
    ({ event }) =>
      event.turn_id === turnIds[0] &&
      event.payload.speech_id === interrupted.event.payload.speech_id
  );
  expect(firstSpeechStart).toBeDefined();
  expect(interrupted.t_ms).toBeGreaterThanOrEqual(secondPcmOnset);

  const secondSpeechStart = speechStarts.find(
    ({ event, t_ms }) => event.turn_id === turnIds[1] && t_ms > interrupted.t_ms
  );
  expect(secondSpeechStart).toBeDefined();
  if (!secondSpeechStart) throw new Error("Second Pipecat reply did not start");
  const secondSpeechStop = eventsOf(proof, "assistant_speech_stopped").find(
    ({ event, t_ms }) =>
      event.turn_id === turnIds[1] &&
      event.payload.speech_id === secondSpeechStart.event.payload.speech_id &&
      event.payload.reason === "completed" &&
      t_ms >= secondSpeechStart.t_ms
  );
  expect(secondSpeechStop).toBeDefined();
  if (!secondSpeechStop) throw new Error("Second Pipecat reply did not complete");

  const firstReplySamples = proof.remote_samples.filter(
    (sample) =>
      sample.t_ms >= (firstSpeechStart?.t_ms ?? 0) &&
      sample.t_ms < secondPcmOnset &&
      sample.rms >= REMOTE_ACTIVE_RMS
  );
  expect(firstReplySamples.length).toBeGreaterThan(2);
  const silenceStart = sustainedSilenceStart(proof.remote_samples, secondPcmOnset);
  expect(silenceStart).toBeDefined();
  if (silenceStart === undefined) {
    throw new Error("Pipecat remote PCM did not become silent after interruption");
  }
  expect(secondSpeechStart.t_ms).toBeGreaterThanOrEqual(
    silenceStart + AUDIO_CLOCK_REQUIRED_SILENCE_MS
  );
  const attribution = interruptionAttribution({
    samples: proof.remote_samples,
    silenceStartMs: silenceStart,
    nextAssistantSpeechStartMs: secondSpeechStart.t_ms,
    activeRms: REMOTE_ACTIVE_RMS,
    requiredSilenceMs: AUDIO_CLOCK_REQUIRED_SILENCE_MS,
    samplingToleranceMs: REMOTE_ATTRIBUTION_TOLERANCE_MS,
  });
  expect(attribution.observation_complete).toBe(true);
  expect(attribution.stale_audio_detected).toBe(false);

  expect(proof.rtc.peer_connection_count).toBe(1);
  expect(proof.rtc.open_peer_connection_count).toBe(1);
  expect(proof.rtc.selected_candidate_pair_count).toBe(1);
  const peer = proof.rtc.peer_connections[0];
  expect(peer).toBeDefined();
  if (!peer) throw new Error("Pipecat RTCPeerConnection was not captured");
  expect(peer.connection_state).toBe("connected");
  expect(peer.audio_sender_track_ids).toEqual([proof.local_track?.id]);
  expect(peer.audio_receiver_track_ids).toContain(proof.remote_track?.id);
  expect(proof.rtc.outbound_audio).toMatchObject({
    stream_count: expect.any(Number),
    bytes: expect.any(Number),
    packets: expect.any(Number),
  });
  expect(proof.rtc.outbound_audio.stream_count).toBeGreaterThan(0);
  expect(proof.rtc.outbound_audio.bytes).toBeGreaterThan(0);
  expect(proof.rtc.outbound_audio.packets).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.stream_count).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.bytes).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.packets).toBeGreaterThan(0);

  const selectedPair = peer.selected_candidate_pair;
  expect(selectedPair).not.toBeNull();
  if (!selectedPair) throw new Error("Selected Pipecat candidate pair was not captured");
  expect(selectedPair.state).toBe("succeeded");
  expect(selectedPair.nominated).toBe(true);
  expect(selectedPair.bytes_sent).toBeGreaterThan(0);
  expect(selectedPair.bytes_received).toBeGreaterThan(0);
  if (PIPECAT_NETWORK_MODE === "relay-tls") {
    expect(proof.rtc.relay_policy_attested).toBe(true);
    expect(relayCandidatePairAttested(selectedPair)).toBe(true);
    gatewayAttested = await browserRelayGatewayAttested(
      page,
      requiredCoturnGatewayIpv4()
    );
    if (!gatewayAttested) {
      throw new Error("Pipecat relay gateway attestation failed");
    }
  } else {
    expect(selectedPair.local.candidate_type).not.toBe("relay");
    expect(selectedPair.remote.candidate_type).not.toBe("relay");
    expect(selectedPair.local.protocol).toBe("udp");
    expect(selectedPair.remote.protocol).toBe("udp");
  }
  expect(Object.keys(selectedPair.local).sort()).toEqual(
    ["candidate_type", "protocol", "relay_protocol"].sort()
  );
  expect(Object.keys(selectedPair.remote).sort()).toEqual(
    ["candidate_type", "protocol", "relay_protocol"].sort()
  );
  expect(JSON.stringify(proof)).not.toContain("/api/voice/pipecat/signal/");
  expect(JSON.stringify(proof)).not.toContain('"ice_servers"');
  expect(JSON.stringify(selectedPair)).not.toMatch(/address|port|foundation|priority/i);
  expect(requestTrace.signalingPosts).toBe(1);
  expect(requestTrace.authenticatedSignalingPosts).toBe(1);
  expect(requestTrace.signalingPatches).toBeGreaterThan(0);
  expect(requestTrace.authenticatedSignalingPatches).toBe(
    requestTrace.signalingPatches
  );
  expect(requestTrace.signalingRequestsWithCookies).toBe(0);
  expectNoSignalingSecrets(proof);

  const localPeak = Math.max(...proof.local_samples.map((sample) => sample.rms));
  const remotePeak = Math.max(...proof.remote_samples.map((sample) => sample.rms));
  expect(localPeak).toBeGreaterThanOrEqual(LOCAL_ACTIVE_RMS);
  expect(remotePeak).toBeGreaterThanOrEqual(REMOTE_ACTIVE_RMS);

  const sessionEndObserved = page.waitForRequest(
    (request) => {
      try {
        const parsed = new URL(request.url());
        return (
          parsed.origin === PIPECAT_API_URL &&
          parsed.pathname === "/api/voice/session/end" &&
          request.method() === "POST"
        );
      } catch {
        return false;
      }
    },
    { timeout: PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS }
  );
  await page.getByTestId("voice-e2e-end").click();
  // The browser hook awaits this release, but Playwright delivers its request
  // observation over a separate protocol turn. Await that exact observation
  // before asserting the sanitized request ledger.
  await sessionEndObserved;
  await expect
    .poll(
      async () => {
        await assertNoHarnessError(page);
        const terminal = await readSnapshot(page);
        return {
          audioClockCleanupComplete: audioClockCleanupComplete(
            terminal.audio_clock
          ),
          status: terminal.status,
        };
      },
      {
        message: "Pipecat disconnect must close tracks, audio, peer, and assignment",
      }
    )
    .toEqual({ audioClockCleanupComplete: true, status: "disconnected" });

  const cleaned = await readSnapshot(page);
  expect(cleaned.disconnect_requested).toBe(true);
  expect(cleaned.local_track_released).toBe(true);
  expect(cleaned.local_track?.ready_state).toBe("ended");
  expect(cleaned.remote_track_released).toBe(true);
  expect(cleaned.remote_track?.ready_state).toBe("ended");
  expect(cleaned.remote_audio_element_count).toBe(0);
  expect(cleaned.hook_assignment_cleared).toBe(true);
  expect(cleaned.errors).toEqual([]);
  expect(cleaned.audio_clock.disposed).toBe(true);
  expect(cleaned.audio_clock.local.failure_code).toBeNull();
  expect(cleaned.audio_clock.remote.failure_code).toBeNull();
  expect(audioClockCleanupComplete(cleaned.audio_clock)).toBe(true);
  expect(cleaned.rtc.peer_connection_count).toBe(1);
  expect(cleaned.rtc.open_peer_connection_count).toBe(0);
  expect(cleaned.rtc.closed_peer_connection_count).toBe(1);
  expect(requestTrace.signalingDeletes).toBe(1);
  expect(requestTrace.authenticatedSignalingDeletes).toBe(1);
  expect(requestTrace.signalingRequestsWithCookies).toBe(0);
  expect(requestTrace.sessionEndPosts).toBe(1);
  expect(requestTrace.terminalOrder).toEqual(["peer_delete", "session_end"]);
  expectNoSignalingSecrets(cleaned);

  const terminalCleanupPollOptions = {
    timeout: PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS,
    message: "dedicated Pipecat app must finish exact terminal cleanup",
  };
  if (PIPECAT_NETWORK_MODE === "relay-tls") {
    await expect
      .poll(
        async () => {
          const status = await readTerminalStatus(request, apiUrl, assignment);
          return {
            status: status.status,
            reservation_state: status.reservation.state,
            cleanup_complete: status.reservation.cleanup_complete,
            signaling_active_call_count:
              status.control_plane.signaling_active_call_count,
            runtime_handle_retained:
              status.control_plane.runtime_handle_retained,
          };
        },
        terminalCleanupPollOptions
      )
      .toEqual({
        status: "pending",
        reservation_state: "terminal",
        cleanup_complete: true,
        signaling_active_call_count: 0,
        runtime_handle_retained: false,
      });
  } else {
    await expect
      .poll(
        async () => {
          const status = await readTerminalStatus(request, apiUrl, assignment);
          return status.status;
        },
        terminalCleanupPollOptions
      )
      .toBe("passed");
  }
  const terminalStatus = await readTerminalStatus(request, apiUrl, assignment);
  expect(terminalStatus).toMatchObject({
    schema_version: 1,
    status: PIPECAT_NETWORK_MODE === "relay-tls" ? "pending" : "passed",
    runtime: "pipecat_smallwebrtc_v1",
    profile_id: EXPECTED_PROFILE_ID,
    session_id: assignment.session_id,
    voice_call_id: assignment.voice_call_id,
    reservation: {
      state: "terminal",
      cleanup_complete: true,
    },
    fake_media: {
      final_transcripts: EXPECTED_TRANSCRIPTS,
      llm_response_count: 2,
      tts_cancelled_count: 1,
      media_contract_satisfied: true,
    },
  });
  expect([
    { terminal_reason: "user_ended", retryable: false },
    { terminal_reason: "client_disconnected", retryable: true },
  ]).toContainEqual({
    terminal_reason: terminalStatus.reservation.terminal_reason,
    retryable: terminalStatus.reservation.retryable,
  });
  expect(terminalStatus.control_plane).toEqual({
    bootstrap_active_assignment_count: 0,
    bootstrap_active_lock_count: 0,
    signaling_active_call_count: 0,
    runtime_handle_retained: false,
    cleanup_retry_pending: false,
    runtime_observer_pending: false,
    expiry_pending: false,
    trusted_release_pending: false,
  });
  expect(terminalStatus.fake_media.input_frame_count).toBeGreaterThan(0);
  expect(terminalStatus.fake_media.tts_frame_count).toBeGreaterThan(0);
  expect(terminalStatus.fake_media.cleaned_processors.length).toBeGreaterThan(0);

  const result = {
    schema_version: 1,
    status: "passed",
    completed_at: new Date().toISOString(),
    runtime: assignment.runtime,
    profile_id: assignment.profile_id,
    peer_reservation_id: assignment.peer_reservation_id,
    voice_call_id: assignment.voice_call_id,
    trace_id: assignment.trace_id,
    browser_evidence: {
      ...(PIPECAT_NETWORK_MODE === "relay-tls"
        ? {
            relay_policy_attested:
              proof.rtc.relay_policy_attested === true,
            tls_spki_pin_attested: tlsSpkiPinAttested,
            gateway_attested: gatewayAttested,
          }
        : {}),
      exact_local_track_id: proof.local_track?.id,
      exact_remote_track_id: proof.remote_track?.id,
      connection_gestures: proof.connection_gestures,
      pre_ready_microphone_track: {
        observed_at_ms: proof.local_track?.observed_at_ms,
        enabled_at_observation: proof.local_track?.enabled_at_observation,
        ready_state_at_observation: proof.local_track?.ready_state_at_observation,
        first_agent_ready_observed_at_ms: firstReady.t_ms,
      },
      peer_connection_count: proof.rtc.peer_connection_count,
      outbound_bytes_sent: proof.rtc.outbound_audio.bytes,
      outbound_packets_sent: proof.rtc.outbound_audio.packets,
      inbound_bytes_received: proof.rtc.inbound_audio.bytes,
      inbound_packets_received: proof.rtc.inbound_audio.packets,
      selected_candidate_pair: selectedPair,
      signaling_request_counts: {
        post: requestTrace.signalingPosts,
        authenticated_post: requestTrace.authenticatedSignalingPosts,
        patch: requestTrace.signalingPatches,
        authenticated_patch: requestTrace.authenticatedSignalingPatches,
        delete: requestTrace.signalingDeletes,
        authenticated_delete: requestTrace.authenticatedSignalingDeletes,
        with_cookies: requestTrace.signalingRequestsWithCookies,
      },
      local_peak_rms: localPeak,
      remote_peak_rms: remotePeak,
      audio_sample_clock: {
        evidence: proof.audio_clock,
        interruption_bracket: clockBracket,
      },
      first_user_pcm_region_start_ms: localPcmRegions[0]?.start_ms,
      second_user_pcm_region_start_ms: secondPcmOnset,
      remote_pcm_silence_attribution_start_ms: silenceStart,
      sustained_pcm_silence_ms: AUDIO_CLOCK_REQUIRED_SILENCE_MS,
      no_stale_audio_guard_start_ms: attribution.guard_start_ms,
      no_stale_audio_guard_end_ms: attribution.guard_end_ms,
      remote_attribution_tolerance_ms: REMOTE_ATTRIBUTION_TOLERANCE_MS,
      first_turn_id: turnIds[0],
      second_turn_id: turnIds[1],
      interrupted_speech_id: interrupted.event.payload.speech_id,
      second_reply_completed_speech_id: secondSpeechStop.event.payload.speech_id,
      canonical_event_count: proof.events.length,
    },
    browser_cleanup_observed: true,
    terminal_cleanup: terminalStatus,
  };
  expectNoSignalingSecrets(result);
  writeResultAtomically(resultPath, result);
});
