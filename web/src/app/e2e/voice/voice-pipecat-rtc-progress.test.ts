import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import { RELAY_GATEWAY_ATTESTATION_GLOBAL } from "./rtc-diagnostics";

vi.mock("@playwright/test", () => {
  const playwrightTest = Object.assign(vi.fn(), { use: vi.fn() });
  return { expect: vi.fn(), test: playwrightTest };
});

import {
  browserRelayGatewayAttested,
  buildProofTimeoutProgressCapsule,
  chromiumSpkiPinAttested,
  PIPECAT_PROOF_WAIT_TIMEOUT_MS,
  PIPECAT_RUNNER_WATCHDOG_CONTRACT_MS,
  PIPECAT_SPEC_SETUP_MARGIN_MS,
  PIPECAT_SPEC_TIMEOUT_MS,
  PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS,
  PROOF_TIMEOUT_PROGRESS_MAX_BYTES,
  PROOF_TIMEOUT_PROGRESS_PREFIX,
  relayCandidatePairAttested,
  serializeProofTimeoutProgressCapsule,
  signalingSecretLabels,
} from "../../../../e2e/voice-pipecat-rtc.spec";

function timeoutSnapshot(): Parameters<typeof buildProofTimeoutProgressCapsule>[0] {
  return {
    schema_version: 1,
    status: "observing",
    phase: "ready",
    voice_call_id: "raw-call-id",
    assignment: {
      runtime: "pipecat_smallwebrtc_v1",
      trace_id: "raw-trace-id",
      voice_call_id: "raw-call-id",
      session_id: "raw-session-id",
      agent_id: "raw-agent-id",
      profile_id: "pipecat-fake-rtc-v1",
      peer_reservation_id: "raw-reservation-id",
      event_protocol: "rtvi-murmur-v2",
    },
    local_track: {
      id: "raw-local-track-id",
      kind: "audio",
      label: "raw-local-track-label",
      observed_at_ms: 1,
      enabled_at_observation: false,
      muted_at_observation: false,
      ready_state_at_observation: "live",
      media_stream_track_enabled: true,
      muted: false,
      ready_state: "live",
    },
    remote_track: {
      id: "raw-remote-track-id",
      kind: "audio",
      label: "raw-remote-track-label",
      observed_at_ms: 3,
      enabled_at_observation: true,
      muted_at_observation: false,
      ready_state_at_observation: "live",
      media_stream_track_enabled: true,
      muted: false,
      ready_state: "live",
    },
    microphone_publication: null,
    local_track_released: false,
    remote_track_released: false,
    remote_audio_element_attached: true,
    remote_audio_element_count: 1,
    local_samples: [
      { t_ms: 10, rms: 0.1 },
      { t_ms: 20, rms: 0.1 },
      { t_ms: 30, rms: 0.1 },
    ],
    remote_samples: [
      { t_ms: 5, rms: 0.1 },
      { t_ms: 40, rms: 0 },
    ],
    events: [
      {
        t_ms: 2,
        event: {
          schema_version: 1,
          event_id: "raw-event-id",
          event_type: "agent_ready",
          trace_id: "raw-trace-id",
          voice_call_id: "raw-call-id",
          session_id: "raw-session-id",
          producer_id: "raw-producer-id",
          producer_sequence: 1,
          payload: { secret: "candidate:raw-secret" },
        },
      },
    ],
    errors: ["Bearer raw-secret https://secret.invalid/path"],
    logs: ["raw-secret-log"],
    connection_gestures: [
      { sequence: 1, action: "prepare" },
      { sequence: 2, action: "activate" },
    ],
    audio_clock: {
      schema_version: 1,
      worklet_loaded: false,
      sample_rate_hz: 48_000,
      quantum_frames: 128,
      disposed: false,
      local: {
        attached: false,
        processed_block_count: 3,
        active_region_count: 1,
        stale_frame_correction_pending: false,
        failure_code: null,
      },
      remote: {
        attached: false,
        processed_block_count: 4,
        active_region_count: 1,
        stale_frame_correction_pending: false,
        failure_code: null,
      },
    },
    rtc: {
      peer_connection_count: 1,
      selected_candidate_pair_count: 1,
      outbound_audio: { stream_count: 1, bytes: 10, packets: 2 },
      inbound_audio: { stream_count: 1, bytes: 11, packets: 3 },
    },
    disconnect_requested: false,
    hook_assignment_cleared: false,
  } as unknown as Parameters<typeof buildProofTimeoutProgressCapsule>[0];
}

describe("Pipecat proof timeout progress capsule", () => {
  it("keeps nested waits below the exact spec and runner watchdog contracts", () => {
    expect({
      proofWaitMs: PIPECAT_PROOF_WAIT_TIMEOUT_MS,
      terminalCleanupMs: PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS,
      setupMarginMs: PIPECAT_SPEC_SETUP_MARGIN_MS,
      specTimeoutMs: PIPECAT_SPEC_TIMEOUT_MS,
      runnerWatchdogMs: PIPECAT_RUNNER_WATCHDOG_CONTRACT_MS,
    }).toEqual({
      proofWaitMs: 45_000,
      terminalCleanupMs: 20_000,
      setupMarginMs: 30_000,
      specTimeoutMs: 110_000,
      runnerWatchdogMs: 120_000,
    });
    expect(
      PIPECAT_PROOF_WAIT_TIMEOUT_MS +
        PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS +
        PIPECAT_SPEC_SETUP_MARGIN_MS
    ).toBeLessThan(PIPECAT_SPEC_TIMEOUT_MS);
    expect(PIPECAT_SPEC_TIMEOUT_MS).toBeLessThan(
      PIPECAT_RUNNER_WATCHDOG_CONTRACT_MS
    );

    const specSource = readFileSync(
      new URL("../../../../e2e/voice-pipecat-rtc.spec.ts", import.meta.url),
      "utf8"
    );
    const exactTestStart = specSource.indexOf(
      'test("real browser media crosses Pipecat SmallWebRTC and cleans one peer"'
    );
    expect(exactTestStart).toBeGreaterThanOrEqual(0);
    const exactTestSource = specSource.slice(exactTestStart);
    expect(exactTestSource.match(/\ntest\(/g) ?? []).toHaveLength(0);
    for (const runtimeWiring of [
      "test.setTimeout(PIPECAT_SPEC_TIMEOUT_MS);",
      "waitForProof(page, PIPECAT_PROOF_WAIT_TIMEOUT_MS)",
      "timeout: PIPECAT_TERMINAL_CLEANUP_TIMEOUT_MS,",
    ]) {
      expect(specSource.split(runtimeWiring)).toHaveLength(2);
      expect(exactTestSource).toContain(runtimeWiring);
    }
  });

  it("emits one exact bounded allowlisted schema without raw browser evidence", () => {
    const snapshot = timeoutSnapshot();
    const capsule = buildProofTimeoutProgressCapsule(snapshot);

    expect(capsule).toEqual({
      schema_version: 1,
      kind: "pipecat_proof_wait_timeout",
      snapshot: {
        assignment_present: true,
        harness_error_count: 1,
        connection_gesture_count: 2,
        local_track_present: true,
        remote_track_present: true,
        remote_audio_attached: true,
      },
      events: {
        total: 1,
        agent_ready: 1,
        turn_committed: 0,
        speech_started: 0,
        speech_stopped: 0,
        speech_stopped_interrupted: 0,
        speech_stopped_completed: 0,
      },
      clock: {
        bracket_status: "failed",
        bracket_failure: "clock_not_prepared",
        worklet_loaded: false,
        local_attached: false,
        remote_attached: false,
        local_processed_blocks: 3,
        remote_processed_blocks: 4,
        local_active_regions: 1,
        remote_active_regions: 1,
        local_correction_pending: false,
        remote_correction_pending: false,
        local_failed: false,
        remote_failed: false,
      },
      pcm: {
        local_sample_count: 3,
        remote_sample_count: 2,
        local_active_region_count: 1,
        second_local_region_present: false,
        remote_silence_present: false,
        remote_audio_before_second_local: false,
      },
      rtc: {
        peer_connection_count: 1,
        selected_candidate_pair_count: 1,
        outbound_bytes_present: true,
        outbound_packets_present: true,
        inbound_bytes_present: true,
        inbound_packets_present: true,
      },
      gates: {
        local_disabled_at_observation: true,
        local_live_at_observation: true,
        local_precedes_ready: true,
        first_event_agent_ready: true,
        first_reply_interrupted: false,
        second_turn_present: false,
        second_reply_started: false,
        second_reply_after_silence: false,
        second_reply_completed: false,
        attribution_observation_complete: false,
        stale_audio_detected: false,
        proof_ready: false,
      },
    });

    const rendered = serializeProofTimeoutProgressCapsule(snapshot);
    expect(rendered.startsWith(PROOF_TIMEOUT_PROGRESS_PREFIX)).toBe(true);
    expect(new TextEncoder().encode(rendered).byteLength).toBeLessThan(
      PROOF_TIMEOUT_PROGRESS_MAX_BYTES
    );
    expect(rendered).not.toMatch(
      /raw-|https?:\/\/|Bearer|candidate:|voice_call_id|trace_id|peer_reservation_id|local_samples|remote_samples|transitions|sdp|ice_servers/i
    );
  });
});

describe("Pipecat relay browser evidence contract", () => {
  const selectedRelayPair = {
    state: "succeeded",
    nominated: true,
    bytes_sent: 123,
    bytes_received: 456,
    current_round_trip_time_seconds: 0.01,
    local: {
      candidate_type: "relay",
      protocol: "udp",
      relay_protocol: "tls",
    },
    remote: {
      candidate_type: "host",
      protocol: "udp",
      relay_protocol: null,
    },
  } as const;

  it("accepts only the exact bidirectional relay/TLS candidate pair", () => {
    expect(relayCandidatePairAttested(selectedRelayPair)).toBe(true);
    expect(
      relayCandidatePairAttested({
        ...selectedRelayPair,
        bytes_received: 0,
      })
    ).toBe(false);
    expect(
      relayCandidatePairAttested({
        ...selectedRelayPair,
        local: { ...selectedRelayPair.local, relay_protocol: "tcp" },
      })
    ).toBe(false);
    expect(
      relayCandidatePairAttested({
        ...selectedRelayPair,
        remote: {
          ...selectedRelayPair.remote,
          candidate_type: "srflx",
        },
      })
    ).toBe(false);
  });

  it("attests one exact Chromium pin argument without returning its value", () => {
    const pin = Buffer.alloc(32, 0x5a).toString("base64");
    const exact = `--ignore-certificate-errors-spki-list=${pin}`;

    expect(chromiumSpkiPinAttested(["--headless", exact], pin)).toBe(true);
    for (const argumentsList of [
      ["--headless"],
      [exact, exact],
      [exact, "--ignore-certificate-errors"],
      [exact, "--allow-insecure-localhost"],
    ]) {
      expect(chromiumSpkiPinAttested(argumentsList, pin)).toBe(false);
    }
  });

  it("accepts only the owned nonenumerable gateway API lifecycle", async () => {
    const gatewayIpv4 = "172.28.0.1";
    const propertyName = RELAY_GATEWAY_ATTESTATION_GLOBAL;
    const page = {
      evaluate: async (
        callback: (argument: {
          propertyName: string;
          expectedIpv4: string;
        }) => Promise<boolean>,
        argument: { propertyName: string; expectedIpv4: string }
      ) => callback(argument),
    } as unknown as Parameters<typeof browserRelayGatewayAttested>[0];
    const owned = Object.freeze((expected: string) =>
      Promise.resolve(expected === gatewayIpv4)
    );
    Object.defineProperty(globalThis, propertyName, {
      configurable: true,
      enumerable: false,
      writable: false,
      value: owned,
    });

    await expect(browserRelayGatewayAttested(page, gatewayIpv4)).resolves.toBe(
      true
    );
    await expect(
      browserRelayGatewayAttested(page, "172.28.00.1")
    ).resolves.toBe(false);

    Object.defineProperty(globalThis, propertyName, {
      configurable: true,
      enumerable: true,
      writable: false,
      value: owned,
    });
    await expect(browserRelayGatewayAttested(page, gatewayIpv4)).resolves.toBe(
      false
    );

    Object.defineProperty(globalThis, propertyName, {
      configurable: true,
      enumerable: false,
      writable: false,
      value: Object.freeze(() =>
        Promise.reject(new Error("hostile raw gateway detail"))
      ),
    });
    await expect(browserRelayGatewayAttested(page, gatewayIpv4)).resolves.toBe(
      false
    );
    Reflect.deleteProperty(globalThis, propertyName);
  });

  it("detects every standard STUN and TURN URI without requiring slashes", () => {
    for (const uri of [
      "stun:relay.invalid:3478",
      "stuns:relay.invalid:5349",
      "turn:relay.invalid:3478?transport=udp",
      "turns:relay.invalid:5349?transport=tcp",
    ]) {
      expect(signalingSecretLabels({ uri })).toContain("network URL");
    }
    expect(signalingSecretLabels({ gateway: "172.28.0.1" })).toContain(
      "raw IPv4 address"
    );
  });
});
