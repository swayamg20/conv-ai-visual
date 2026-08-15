import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { interruptionAttribution } from "../src/app/e2e/voice/proof";

const EXPECTED_AGENT_ID = "90bd1253-90a6-459a-bf37-365bc3039a76";
const EXPECTED_SESSION_ID = "a4f4328e-185e-4c65-b3f7-101e04a37578";
const EXPECTED_TRANSCRIPTS = ["Hello tutor.", "Actually, stop."] as const;
const LOCAL_ACTIVE_RMS = 0.005;
const REMOTE_ACTIVE_RMS = 0.02;
const REMOTE_SILENCE_RMS = 0.012;
const MAX_INTERRUPTION_TO_SILENCE_MS = 250;
const REQUIRED_SILENCE_MS = 200;
const REMOTE_ATTRIBUTION_TOLERANCE_MS = 100;

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
  readonly payload: Record<string, unknown>;
}

interface ObservedEvent {
  readonly t_ms: number;
  readonly event: EventEnvelope;
}

interface Assignment {
  readonly runtime: "livekit_v2";
  readonly trace_id: string;
  readonly voice_call_id: string;
  readonly session_id: string;
  readonly agent_id: string;
  readonly room_name: string;
  readonly dispatch_id: string;
  readonly profile_id: string;
  readonly worker_name: string;
}

interface BrowserSnapshot {
  readonly schema_version: number;
  readonly status: string;
  readonly phase: string;
  readonly voice_call_id: string;
  readonly assignment: Assignment | null;
  readonly local_track: {
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
  } | null;
  readonly remote_track: {
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
  } | null;
  readonly microphone_publication: {
    readonly exact_track_id: string;
    readonly observed_at_ms: number;
    readonly media_stream_track_enabled: boolean;
    readonly livekit_muted: boolean;
    readonly ready_state: string;
  } | null;
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
  readonly rtc: RtcEvidence;
  readonly disconnect_requested: boolean;
  readonly hook_assignment_cleared: boolean;
}

interface ActiveRegion {
  readonly start_ms: number;
  readonly end_ms: number;
  readonly active_samples: number;
}

interface RtpEvidence {
  readonly stream_count: number;
  readonly bytes: number;
  readonly packets: number;
}

interface CandidateEvidence {
  readonly candidate_type: string;
  readonly protocol: string;
  readonly relay_protocol: string | null;
}

interface CandidatePairEvidence {
  readonly state: string;
  readonly nominated: boolean;
  readonly bytes_sent: number;
  readonly bytes_received: number;
  readonly current_round_trip_time_seconds: number | null;
  readonly local: CandidateEvidence;
  readonly remote: CandidateEvidence;
}

interface RtcEvidence {
  readonly peer_connection_count: number;
  readonly open_peer_connection_count: number;
  readonly closed_peer_connection_count: number;
  readonly selected_candidate_pair_count: number;
  readonly outbound_audio: RtpEvidence;
  readonly inbound_audio: RtpEvidence;
  readonly peer_connections: readonly {
    readonly sequence: number;
    readonly connection_state: string;
    readonly ice_connection_state: string;
    readonly signaling_state: string;
    readonly stats_available: boolean;
    readonly audio_sender_track_ids: readonly string[];
    readonly audio_receiver_track_ids: readonly string[];
    readonly selected_candidate_pair: CandidatePairEvidence | null;
    readonly outbound_audio: RtpEvidence;
    readonly inbound_audio: RtpEvidence;
  }[];
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

async function waitForProof(page: Page, timeoutMs: number): Promise<BrowserSnapshot> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await assertNoHarnessError(page);
    const snapshot = await readSnapshot(page);
    if (
      proofReady(snapshot) &&
      snapshot.rtc.outbound_audio.bytes > 0 &&
      snapshot.rtc.outbound_audio.packets > 0 &&
      snapshot.rtc.inbound_audio.bytes > 0 &&
      snapshot.rtc.inbound_audio.packets > 0 &&
      snapshot.rtc.selected_candidate_pair_count > 0
    ) {
      return snapshot;
    }
    await page.waitForTimeout(100);
  }
  throw new Error(
    "Timed out waiting for two real microphone turns, outbound RTP, remote PCM, and interruption"
  );
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
    if (final && final.t_ms - first.t_ms >= REQUIRED_SILENCE_MS) {
      return first.t_ms;
    }
  }
  return undefined;
}

function eventsOf(snapshot: BrowserSnapshot, eventType: string): readonly ObservedEvent[] {
  return snapshot.events.filter(({ event }) => event.event_type === eventType);
}

function proofReady(snapshot: BrowserSnapshot): boolean {
  if (snapshot.errors.length > 0 || snapshot.assignment === null) return false;
  const publication = snapshot.microphone_publication;
  const firstReady = eventsOf(snapshot, "agent_ready")[0];
  if (
    !publication ||
    !firstReady ||
    publication.exact_track_id !== snapshot.local_track?.id ||
    publication.media_stream_track_enabled ||
    !publication.livekit_muted ||
    publication.ready_state !== "live" ||
    publication.observed_at_ms >= firstReady.t_ms
  ) {
    return false;
  }
  const localRegions = activeRegions(snapshot.local_samples, LOCAL_ACTIVE_RMS, 500);
  if (localRegions.length !== 2) return false;
  const secondOnset = localRegions[1]?.start_ms;
  if (secondOnset === undefined) return false;
  const silenceStart = sustainedSilenceStart(snapshot.remote_samples, secondOnset);
  if (
    silenceStart === undefined ||
    silenceStart - secondOnset > MAX_INTERRUPTION_TO_SILENCE_MS
  ) {
    return false;
  }
  const turns = eventsOf(snapshot, "turn_committed");
  const firstTurnId = turns[0]?.event.turn_id;
  const secondTurnId = turns[1]?.event.turn_id;
  const interrupted = eventsOf(snapshot, "assistant_speech_stopped").find(
    ({ event }) =>
      event.payload.reason === "interrupted" && event.turn_id === firstTurnId
  );
  if (!interrupted || !secondTurnId || interrupted.t_ms < secondOnset) return false;
  const nextSpeechStart = eventsOf(snapshot, "assistant_speech_started").find(
    ({ event, t_ms }) => event.turn_id === secondTurnId && t_ms > interrupted.t_ms
  );
  if (
    !nextSpeechStart ||
    nextSpeechStart.t_ms < silenceStart + REQUIRED_SILENCE_MS
  ) {
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
    requiredSilenceMs: REQUIRED_SILENCE_MS,
    samplingToleranceMs: REMOTE_ATTRIBUTION_TOLERANCE_MS,
  });
  return (
    snapshot.remote_audio_element_attached &&
    snapshot.events[0]?.event.event_type === "agent_ready" &&
    turns.length >= 2 &&
    snapshot.remote_samples.some(
      (sample) => sample.t_ms < secondOnset && sample.rms >= REMOTE_ACTIVE_RMS
    ) &&
    nextSpeechStop !== undefined &&
    attribution.observation_complete &&
    !attribution.stale_audio_detected
  );
}

function requiredAbsoluteEnv(name: string): string {
  const value = process.env[name];
  if (!value || !path.isAbsolute(value)) {
    throw new Error(`${name} must be an absolute path`);
  }
  return value;
}

function writeResultAtomically(resultPath: string, result: object): void {
  const temporary = `${resultPath}.${process.pid}.tmp`;
  fs.mkdirSync(path.dirname(resultPath), { recursive: true });
  fs.writeFileSync(temporary, `${JSON.stringify(result, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  fs.renameSync(temporary, resultPath);
  JSON.parse(fs.readFileSync(resultPath, "utf8"));
}

test("real browser media crosses LiveKit and barge-in stops the first reply", async ({
  page,
}) => {
  const resultPath = requiredAbsoluteEnv("VOICE_E2E_RESULT_PATH");

  await page.goto("/e2e/voice");
  const cdp = await page.context().newCDPSession(page);
  const browserCommandLine = await cdp.send("Browser.getBrowserCommandLine");
  expect(browserCommandLine.arguments).not.toContain("--mute-audio");
  await cdp.detach();
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
  expect(prepared.rtc.peer_connection_count).toBe(0);
  await expect(activationButton).toBeEnabled();
  await activationButton.click();

  const proof = await waitForProof(page, 30_000);
  const assignment = proof.assignment;
  expect(assignment).not.toBeNull();
  if (!assignment) throw new Error("Accepted Voice V2 assignment disappeared");

  expect(proof.schema_version).toBe(1);
  expect(proof.errors).toEqual([]);
  expect(proof.connection_gestures).toEqual([
    { sequence: 1, action: "prepare" },
    { sequence: 2, action: "activate" },
  ]);
  expect(assignment.runtime).toBe("livekit_v2");
  expect(assignment.agent_id).toBe(EXPECTED_AGENT_ID);
  expect(assignment.session_id).toBe(EXPECTED_SESSION_ID);
  expect(assignment.profile_id).toBe("fake-rtc-v1");
  expect(proof.voice_call_id).toBe(assignment.voice_call_id);
  expect(proof.local_track).toMatchObject({ kind: "audio", ready_state: "live" });
  const microphonePublication = proof.microphone_publication;
  expect(microphonePublication).toMatchObject({
    exact_track_id: proof.local_track?.id,
    media_stream_track_enabled: false,
    livekit_muted: true,
    ready_state: "live",
  });
  if (!microphonePublication) {
    throw new Error("Exact microphone publication state was not observed");
  }
  expect(proof.remote_audio_element_attached).toBe(true);
  expect(proof.remote_track).toMatchObject({ kind: "audio", ready_state: "live" });

  expect(proof.events[0]?.event.event_type).toBe("agent_ready");
  const firstAgentReady = eventsOf(proof, "agent_ready")[0];
  expect(firstAgentReady).toBeDefined();
  if (!firstAgentReady) throw new Error("Canonical agent readiness was not observed");
  expect(microphonePublication.observed_at_ms).toBeGreaterThanOrEqual(0);
  expect(microphonePublication.observed_at_ms).toBeLessThan(firstAgentReady.t_ms);
  const eventIds = new Set<string>();
  const producerSequences = new Map<string, number>();
  for (const { event } of proof.events) {
    expect(event.schema_version).toBe(1);
    expect(event.trace_id).toBe(assignment.trace_id);
    expect(event.voice_call_id).toBe(assignment.voice_call_id);
    expect(event.session_id).toBe(assignment.session_id);
    expect(event.event_id).not.toBe("");
    expect(eventIds.has(event.event_id)).toBe(false);
    eventIds.add(event.event_id);
    const previousSequence = producerSequences.get(event.producer_id) ?? 0;
    expect(event.producer_sequence).toBeGreaterThan(previousSequence);
    producerSequences.set(event.producer_id, event.producer_sequence);
  }

  const finalTranscripts = eventsOf(proof, "transcript_segment")
    .filter(({ event }) => event.payload.is_final === true)
    .map(({ event }) => event.payload.text);
  expect(finalTranscripts).toEqual(EXPECTED_TRANSCRIPTS);

  const turns = eventsOf(proof, "turn_committed");
  expect(turns).toHaveLength(2);
  expect(turns.map(({ event }) => event.payload.text)).toEqual(EXPECTED_TRANSCRIPTS);
  const turnIds = turns.map(({ event }) => event.turn_id);
  expect(turnIds.every((turnId) => typeof turnId === "string" && turnId.length > 0)).toBe(
    true
  );
  expect(new Set(turnIds).size).toBe(2);

  const localRegions = activeRegions(proof.local_samples, LOCAL_ACTIVE_RMS, 500);
  expect(localRegions).toHaveLength(2);
  const secondOnset = localRegions[1]?.start_ms;
  expect(secondOnset).toBeDefined();
  if (secondOnset === undefined) throw new Error("Second microphone onset was not measured");

  const speechStarts = eventsOf(proof, "assistant_speech_started");
  const interrupted = eventsOf(proof, "assistant_speech_stopped").find(
    ({ event }) => event.payload.reason === "interrupted"
  );
  expect(speechStarts.length).toBeGreaterThan(0);
  expect(interrupted).toBeDefined();
  if (!interrupted) throw new Error("No canonical interrupted speech event was accepted");
  const matchingStart = speechStarts.find(
    ({ event }) => event.payload.speech_id === interrupted.event.payload.speech_id
  );
  expect(matchingStart).toBeDefined();
  expect(interrupted.event.turn_id).toBe(matchingStart?.event.turn_id);
  expect(interrupted.event.turn_id).toBe(turnIds[0]);
  expect(interrupted.t_ms).toBeGreaterThanOrEqual(secondOnset);

  const nextSpeechStart = speechStarts.find(
    ({ event, t_ms }) => event.turn_id === turnIds[1] && t_ms > interrupted.t_ms
  );
  expect(nextSpeechStart).toBeDefined();
  if (!nextSpeechStart) {
    throw new Error("No canonical second assistant speech start was accepted");
  }
  const nextSpeechStop = eventsOf(proof, "assistant_speech_stopped").find(
    ({ event, t_ms }) =>
      event.turn_id === turnIds[1] &&
      event.payload.speech_id === nextSpeechStart.event.payload.speech_id &&
      event.payload.reason === "completed" &&
      t_ms >= nextSpeechStart.t_ms
  );
  expect(nextSpeechStop).toBeDefined();
  if (!nextSpeechStop) {
    throw new Error("No matching completed second assistant speech was accepted");
  }

  const firstReplySamples = proof.remote_samples.filter(
    (sample) =>
      sample.t_ms >= (matchingStart?.t_ms ?? 0) &&
      sample.t_ms < secondOnset &&
      sample.rms >= REMOTE_ACTIVE_RMS
  );
  expect(firstReplySamples.length).toBeGreaterThan(2);
  const preBargeInAudible = proof.remote_samples.some(
    (sample) =>
      sample.t_ms >= secondOnset - 250 &&
      sample.t_ms <= secondOnset + 50 &&
      sample.rms >= REMOTE_ACTIVE_RMS
  );
  expect(preBargeInAudible).toBe(true);

  const silenceStart = sustainedSilenceStart(proof.remote_samples, secondOnset);
  expect(silenceStart).toBeDefined();
  if (silenceStart === undefined) {
    throw new Error("Remote PCM did not reach sustained silence after barge-in");
  }
  const interruptionToSilenceMs = silenceStart - secondOnset;
  expect(interruptionToSilenceMs).toBeGreaterThanOrEqual(0);
  expect(interruptionToSilenceMs).toBeLessThanOrEqual(MAX_INTERRUPTION_TO_SILENCE_MS);
  expect(nextSpeechStart.t_ms).toBeGreaterThanOrEqual(
    silenceStart + REQUIRED_SILENCE_MS
  );
  const attribution = interruptionAttribution({
    samples: proof.remote_samples,
    silenceStartMs: silenceStart,
    nextAssistantSpeechStartMs: nextSpeechStart.t_ms,
    activeRms: REMOTE_ACTIVE_RMS,
    requiredSilenceMs: REQUIRED_SILENCE_MS,
    samplingToleranceMs: REMOTE_ATTRIBUTION_TOLERANCE_MS,
  });
  expect(attribution.observation_complete).toBe(true);
  expect(attribution.stale_audio_detected).toBe(false);

  const localPeak = Math.max(...proof.local_samples.map((sample) => sample.rms));
  const remotePeak = Math.max(...proof.remote_samples.map((sample) => sample.rms));
  expect(localPeak).toBeGreaterThanOrEqual(LOCAL_ACTIVE_RMS);
  expect(remotePeak).toBeGreaterThanOrEqual(REMOTE_ACTIVE_RMS);
  expect(proof.rtc.peer_connection_count).toBeGreaterThan(0);
  expect(proof.rtc.selected_candidate_pair_count).toBeGreaterThan(0);
  expect(proof.rtc.outbound_audio.stream_count).toBeGreaterThan(0);
  expect(proof.rtc.outbound_audio.bytes).toBeGreaterThan(0);
  expect(proof.rtc.outbound_audio.packets).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.stream_count).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.bytes).toBeGreaterThan(0);
  expect(proof.rtc.inbound_audio.packets).toBeGreaterThan(0);
  expect(
    proof.rtc.peer_connections.some((peer) =>
      peer.audio_sender_track_ids.includes(proof.local_track?.id ?? "")
    )
  ).toBe(true);
  expect(
    proof.rtc.peer_connections.some((peer) =>
      peer.audio_receiver_track_ids.includes(proof.remote_track?.id ?? "")
    )
  ).toBe(true);

  await page.getByTestId("voice-e2e-end").click();
  await expect
    .poll(async () => (await readSnapshot(page)).status, {
      message: "real hook disconnect must release its mic, remote element, and assignment",
    })
    .toBe("disconnected");

  const cleaned = await readSnapshot(page);
  expect(cleaned.disconnect_requested).toBe(true);
  expect(cleaned.local_track_released).toBe(true);
  expect(cleaned.local_track?.ready_state).toBe("ended");
  expect(cleaned.remote_audio_element_count).toBe(0);
  expect(cleaned.hook_assignment_cleared).toBe(true);
  expect(cleaned.rtc.open_peer_connection_count).toBe(0);
  expect(cleaned.rtc.closed_peer_connection_count).toBe(
    cleaned.rtc.peer_connection_count
  );

  writeResultAtomically(resultPath, {
    schema_version: 1,
    status: "passed",
    completed_at: new Date().toISOString(),
    room_name: assignment.room_name,
    dispatch_id: assignment.dispatch_id,
    voice_call_id: assignment.voice_call_id,
    trace_id: assignment.trace_id,
    browser_evidence: {
      exact_local_track_id: proof.local_track?.id,
      pre_ready_microphone_publication: {
        ...microphonePublication,
        first_agent_ready_observed_at_ms: firstAgentReady.t_ms,
        observation_preceded_agent_ready:
          microphonePublication.observed_at_ms < firstAgentReady.t_ms,
      },
      local_peak_rms: localPeak,
      outbound_bytes_sent: proof.rtc.outbound_audio.bytes,
      outbound_packets_sent: proof.rtc.outbound_audio.packets,
      inbound_bytes_received: proof.rtc.inbound_audio.bytes,
      inbound_packets_received: proof.rtc.inbound_audio.packets,
      peer_connection_count: proof.rtc.peer_connection_count,
      selected_candidate_pairs: proof.rtc.peer_connections
        .map((peer) => peer.selected_candidate_pair)
        .filter((pair) => pair !== null),
      remote_peak_rms: remotePeak,
      first_user_onset_ms: localRegions[0]?.start_ms,
      second_user_onset_ms: secondOnset,
      remote_silence_start_ms: silenceStart,
      interruption_to_silence_ms: interruptionToSilenceMs,
      sustained_silence_ms: REQUIRED_SILENCE_MS,
      no_stale_audio_guard_start_ms: attribution.guard_start_ms,
      no_stale_audio_guard_end_ms: attribution.guard_end_ms,
      remote_attribution_tolerance_ms: REMOTE_ATTRIBUTION_TOLERANCE_MS,
      second_reply_started_ms: nextSpeechStart.t_ms,
      second_reply_completed_speech_id: nextSpeechStop.event.payload.speech_id,
      first_turn_id: turnIds[0],
      second_turn_id: turnIds[1],
      interrupted_speech_id: interrupted.event.payload.speech_id,
      canonical_event_count: proof.events.length,
    },
    browser_cleanup_observed: true,
  });
});
