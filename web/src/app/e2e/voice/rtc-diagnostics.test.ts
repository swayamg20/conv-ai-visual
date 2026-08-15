/** @vitest-environment happy-dom */

import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  emptyBrowserRtcEvidence,
  installBrowserRtcDiagnostics,
  isCanonicalPrivateIpv4,
  observeBrowserMediaTrack,
  parseBrowserRtcNetworkMode,
  RELAY_GATEWAY_ATTESTATION_GLOBAL,
  summarizeRtcPeerConnections,
} from "./rtc-diagnostics";

const smallWebRtcSourcePath = path.join(
  process.cwd(),
  "node_modules/@pipecat-ai/small-webrtc-transport/dist/index.module.js"
);
const smallWebRtcPackagePath = path.join(
  process.cwd(),
  "node_modules/@pipecat-ai/small-webrtc-transport/package.json"
);

interface ConstructorObservation {
  readonly argumentsList: readonly unknown[];
  readonly newTarget: unknown;
}

function constructorHarness(
  configurationReader?: (configuration: RTCConfiguration) => RTCConfiguration,
  stats: readonly Record<string, unknown>[] = []
) {
  const observations: ConstructorObservation[] = [];
  const state = { closeCount: 0, statsReadCount: 0 };
  class NativePeerConnection {
    readonly connectionState = "connected";
    readonly iceConnectionState = "connected";
    readonly signalingState = "stable";
    readonly configuration: RTCConfiguration;

    constructor(...argumentsList: unknown[]) {
      observations.push({ argumentsList, newTarget: new.target });
      this.configuration = (argumentsList[0] ?? {}) as RTCConfiguration;
    }

    async getStats(): Promise<RTCStatsReport> {
      state.statsReadCount += 1;
      return report(stats);
    }

    getSenders(): RTCRtpSender[] {
      return [];
    }

    getReceivers(): RTCRtpReceiver[] {
      return [];
    }

    getConfiguration(): RTCConfiguration {
      return configurationReader?.(this.configuration) ?? this.configuration;
    }

    close(): void {
      state.closeCount += 1;
    }
  }
  const native = NativePeerConnection as unknown as typeof RTCPeerConnection;
  const target = { RTCPeerConnection: native };
  return { native, observations, state, target };
}

function report(records: readonly Record<string, unknown>[]): RTCStatsReport {
  const values = new Map(records.map((record) => [String(record.id), record]));
  return values as unknown as RTCStatsReport;
}

function relayStats(gatewayIpv4: string): readonly Record<string, unknown>[] {
  return [
    {
      id: "transport-1",
      type: "transport",
      selectedCandidatePairId: "pair-1",
    },
    {
      id: "pair-1",
      type: "candidate-pair",
      state: "succeeded",
      nominated: true,
      localCandidateId: "local-1",
      remoteCandidateId: "remote-1",
      bytesSent: 12_345,
      bytesReceived: 54_321,
    },
    {
      id: "local-1",
      type: "local-candidate",
      candidateType: "relay",
      protocol: "udp",
      relayProtocol: "tls",
      address: "127.0.0.1",
    },
    {
      id: "remote-1",
      type: "remote-candidate",
      candidateType: "host",
      protocol: "udp",
      address: gatewayIpv4,
    },
  ];
}

function peer(
  records: readonly Record<string, unknown>[],
  states: Partial<
    Pick<
      RTCPeerConnection,
      "connectionState" | "iceConnectionState" | "signalingState"
    >
  > = {},
  tracks: { readonly senderId?: string; readonly receiverId?: string } = {}
): RTCPeerConnection {
  return {
    connectionState: states.connectionState ?? "connected",
    iceConnectionState: states.iceConnectionState ?? "connected",
    signalingState: states.signalingState ?? "stable",
    getStats: async () => report(records),
    getSenders: () =>
      tracks.senderId
        ? [{ track: { id: tracks.senderId, kind: "audio" } }]
        : [],
    getReceivers: () =>
      tracks.receiverId
        ? [{ track: { id: tracks.receiverId, kind: "audio" } }]
        : [],
  } as RTCPeerConnection;
}

describe("RTC diagnostics", () => {
  it("accepts only the exact browser RTC network modes with a direct default", () => {
    expect(parseBrowserRtcNetworkMode(undefined)).toBe("direct");
    expect(parseBrowserRtcNetworkMode("direct")).toBe("direct");
    expect(parseBrowserRtcNetworkMode("relay-tls")).toBe("relay-tls");
    for (const value of ["", "relay", "DIRECT", "relay-tls ", null]) {
      expect(() =>
        parseBrowserRtcNetworkMode(value as unknown as string)
      ).toThrow("Browser RTC network mode is invalid");
    }

    const { target } = constructorHarness();
    expect(() =>
      installBrowserRtcDiagnostics("invalid" as "direct", target)
    ).toThrow("Browser RTC network mode is invalid");
    expect(target.RTCPeerConnection).toBeDefined();
  });

  it("accepts only canonical RFC1918 gateway addresses", () => {
    for (const value of ["10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.4.1"]) {
      expect(isCanonicalPrivateIpv4(value)).toBe(true);
    }
    for (const value of [
      "",
      "10.0.0.0",
      "10.0.0.255",
      "10.00.0.1",
      "172.15.0.1",
      "172.32.0.1",
      "192.169.0.1",
      "203.0.113.1",
      "256.0.0.1",
      null,
    ]) {
      expect(isCanonicalPrivateIpv4(value)).toBe(false);
    }
  });

  it("locks the pinned SmallWebRTC constructor to its one iceServers config", () => {
    const packageValue = JSON.parse(
      readFileSync(smallWebRtcPackagePath, "utf8")
    ) as { version?: unknown };
    expect(packageValue.version).toBe("1.10.6");

    const source = readFileSync(smallWebRtcSourcePath, "utf8");
    const methodStart = source.indexOf("    createPeerConnection() {");
    const listenerStart = source.indexOf("        pc.onicecandidate", methodStart);
    expect(methodStart).toBeGreaterThan(-1);
    expect(listenerStart).toBeGreaterThan(methodStart);
    expect(source.slice(methodStart, listenerStart)).toBe(
      "    createPeerConnection() {\n" +
        "        const config = {\n" +
        "            iceServers: this._iceServers\n" +
        "        };\n" +
        "        let pc = new RTCPeerConnection(config);\n"
    );
  });

  it("forwards direct constructor arguments and identity without changing shape", async () => {
    const { native, observations, target } = constructorHarness();
    const config = Object.freeze({
      iceServers: Object.freeze([{ urls: "stun:example.invalid" }]),
      bundlePolicy: "max-bundle" as RTCBundlePolicy,
    });
    const diagnostics = installBrowserRtcDiagnostics("direct", target);
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
    ).toBeUndefined();
    const capturingConstructor = target.RTCPeerConnection;

    const connection = Reflect.construct(target.RTCPeerConnection, [config]);

    expect(connection).toBeInstanceOf(native);
    expect(observations).toHaveLength(1);
    expect(observations[0]?.argumentsList).toHaveLength(1);
    expect(observations[0]?.argumentsList[0]).toBe(config);
    expect(observations[0]?.newTarget).toBe(capturingConstructor);
    const evidence = await diagnostics.read();
    expect(evidence).toMatchObject({ peer_connection_count: 1 });
    expect(Object.keys(evidence)).not.toContain("relay_policy_attested");

    diagnostics.restore();
    expect(target.RTCPeerConnection).toBe(native);
    diagnostics.restore();
    expect(target.RTCPeerConnection).toBe(native);
  });

  it("injects relay policy into a fresh config without mutating ICE material", async () => {
    const { native, observations, target } = constructorHarness();
    const iceServers = Object.freeze([]);
    const config = Object.freeze({ iceServers });
    const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
    const capturingConstructor = target.RTCPeerConnection;

    const connection = Reflect.construct(target.RTCPeerConnection, [config]);

    expect(connection).toBeInstanceOf(native);
    expect(observations).toHaveLength(1);
    expect(observations[0]?.newTarget).toBe(capturingConstructor);
    const forwarded = observations[0]?.argumentsList[0];
    expect(forwarded).not.toBe(config);
    expect(forwarded).toEqual({
      iceServers,
      iceTransportPolicy: "relay",
    });
    expect(Reflect.ownKeys(config)).toEqual(["iceServers"]);
    expect(Object.prototype.hasOwnProperty.call(config, "iceTransportPolicy")).toBe(
      false
    );
    await expect(diagnostics.read()).resolves.toMatchObject({
      peer_connection_count: 1,
      relay_policy_attested: true,
    });

    diagnostics.restore();
    expect(target.RTCPeerConnection).toBe(native);
  });

  it("fails relay closed on unexpected constructor shapes", () => {
    const { proxy: hostileProxy, revoke } = Proxy.revocable(
      { iceServers: [] },
      {
        ownKeys() {
          throw new Error("hostile config trap");
        },
      }
    );
    const invalidArguments: readonly (readonly unknown[])[] = [
      [],
      [null],
      [[]],
      [{ iceServers: [], bundlePolicy: "max-bundle" }],
      [Object.create(null, { iceServers: { enumerable: true, value: [] } })],
      [Object.defineProperty({}, "iceServers", { value: [] })],
      [hostileProxy],
      [{ iceServers: [] }, "unexpected-second-argument"],
    ];

    for (const argumentsList of invalidArguments) {
      const { observations, target } = constructorHarness();
      const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
      expect(() =>
        Reflect.construct(target.RTCPeerConnection, [...argumentsList])
      ).toThrow("Relay RTC constructor configuration is incompatible");
      expect(observations).toEqual([]);
      diagnostics.restore();
    }
    revoke();
  });

  it("rejects every caller-supplied relay policy as a conflicting shape", () => {
    for (const iceTransportPolicy of ["all", "relay"] as const) {
      const { observations, target } = constructorHarness();
      const input = { iceServers: [], iceTransportPolicy };
      const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
      expect(() => new target.RTCPeerConnection(input)).toThrow(
        "Relay RTC constructor configuration is incompatible"
      );
      expect(input.iceTransportPolicy).toBe(iceTransportPolicy);
      expect(observations).toEqual([]);
      diagnostics.restore();
    }
  });

  it("closes and rejects a peer when native relay policy attestation fails", async () => {
    for (const observedPolicy of [undefined, "all"] as const) {
      const { observations, state, target } = constructorHarness(() => ({
        iceServers: [],
        ...(observedPolicy === undefined
          ? {}
          : { iceTransportPolicy: observedPolicy }),
      }));
      const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);

      expect(() => new target.RTCPeerConnection({ iceServers: [] })).toThrow(
        "Relay RTC constructor configuration is incompatible"
      );
      expect(observations).toHaveLength(1);
      expect(state.closeCount).toBe(1);
      await expect(diagnostics.read()).resolves.toMatchObject({
        peer_connection_count: 0,
        relay_policy_attested: false,
      });
      diagnostics.restore();
    }
  });

  it("attests relay policy only for exactly one native-checked peer", async () => {
    const { target } = constructorHarness();
    const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);

    await expect(diagnostics.read()).resolves.toEqual({
      ...emptyBrowserRtcEvidence(),
      relay_policy_attested: false,
    });
    new target.RTCPeerConnection({ iceServers: [] });
    await expect(diagnostics.read()).resolves.toMatchObject({
      peer_connection_count: 1,
      relay_policy_attested: true,
    });
    new target.RTCPeerConnection({ iceServers: [] });
    await expect(diagnostics.read()).resolves.toMatchObject({
      peer_connection_count: 2,
      relay_policy_attested: false,
    });

    diagnostics.restore();
  });

  it("attests the live selected remote gateway without serializing its address", async () => {
    const gatewayIpv4 = "172.28.0.1";
    const stats = [...relayStats(gatewayIpv4)];
    const { state, target } = constructorHarness(undefined, stats);
    const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
    new target.RTCPeerConnection({ iceServers: [] });

    const descriptor = Object.getOwnPropertyDescriptor(
      target,
      RELAY_GATEWAY_ATTESTATION_GLOBAL
    );
    expect(descriptor).toMatchObject({
      configurable: true,
      enumerable: false,
      writable: false,
    });
    expect(Object.keys(target)).not.toContain(RELAY_GATEWAY_ATTESTATION_GLOBAL);
    const attest = descriptor?.value as (
      expectedGatewayIpv4: string
    ) => Promise<boolean>;
    expect(typeof attest).toBe("function");

    await expect(attest("172.28.00.1")).resolves.toBe(false);
    expect(state.statsReadCount).toBe(0);
    await expect(attest("172.28.0.2")).resolves.toBe(false);
    await expect(attest(gatewayIpv4)).resolves.toBe(true);
    const evidence = await diagnostics.read();
    expect(JSON.stringify(evidence)).not.toContain(gatewayIpv4);
    stats.push({
      id: "transport-2",
      type: "transport",
      selectedCandidatePairId: "pair-2",
    });
    await expect(attest(gatewayIpv4)).resolves.toBe(false);

    diagnostics.restore();
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
    ).toBeUndefined();
    await expect(attest(gatewayIpv4)).resolves.toBe(false);
    diagnostics.restore();
    expect(target.RTCPeerConnection).toBeDefined();
  });

  it("rejects a preowned gateway API before replacing the native constructor", () => {
    const { native, target } = constructorHarness();
    const preowned = () => Promise.resolve(true);
    Object.defineProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL, {
      configurable: true,
      value: preowned,
    });

    expect(() => installBrowserRtcDiagnostics("relay-tls", target)).toThrow(
      "Relay RTC gateway attestation API is incompatible"
    );
    expect(target.RTCPeerConnection).toBe(native);
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
        ?.value
    ).toBe(preowned);
    Reflect.deleteProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL);
  });

  it("rolls back the constructor when the gateway API cannot be installed", () => {
    const { native, target } = constructorHarness();
    const rejectingTarget = new Proxy(target, {
      defineProperty(owner, property, descriptor) {
        return property === RELAY_GATEWAY_ATTESTATION_GLOBAL
          ? false
          : Reflect.defineProperty(owner, property, descriptor);
      },
    });

    expect(() =>
      installBrowserRtcDiagnostics("relay-tls", rejectingTarget)
    ).toThrow("Relay RTC gateway attestation API is incompatible");
    expect(target.RTCPeerConnection).toBe(native);
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
    ).toBeUndefined();
  });

  it("revokes its owned API without deleting a hostile replacement", async () => {
    const gatewayIpv4 = "10.44.0.1";
    const { native, target } = constructorHarness(undefined, relayStats(gatewayIpv4));
    const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
    new target.RTCPeerConnection({ iceServers: [] });
    const owned = Object.getOwnPropertyDescriptor(
      target,
      RELAY_GATEWAY_ATTESTATION_GLOBAL
    )?.value as (expectedGatewayIpv4: string) => Promise<boolean>;
    await expect(owned(gatewayIpv4)).resolves.toBe(true);

    const hostileReplacement = () => Promise.resolve(true);
    Object.defineProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL, {
      configurable: true,
      enumerable: false,
      writable: false,
      value: hostileReplacement,
    });
    diagnostics.restore();

    expect(target.RTCPeerConnection).toBe(native);
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
        ?.value
    ).toBe(hostileReplacement);
    await expect(owned(gatewayIpv4)).resolves.toBe(false);
    diagnostics.restore();
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
        ?.value
    ).toBe(hostileReplacement);
    Reflect.deleteProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL);
  });

  it("cannot finish a gateway attestation after lifecycle revocation", async () => {
    const gatewayIpv4 = "192.168.44.1";
    const { target } = constructorHarness();
    const diagnostics = installBrowserRtcDiagnostics("relay-tls", target);
    const connection = new target.RTCPeerConnection({ iceServers: [] });
    let resolveStats: ((value: RTCStatsReport) => void) | undefined;
    Object.defineProperty(connection, "getStats", {
      value: () =>
        new Promise<RTCStatsReport>((resolve) => {
          resolveStats = resolve;
        }),
    });
    const attest = Object.getOwnPropertyDescriptor(
      target,
      RELAY_GATEWAY_ATTESTATION_GLOBAL
    )?.value as (expectedGatewayIpv4: string) => Promise<boolean>;

    const pending = attest(gatewayIpv4);
    diagnostics.restore();
    expect(resolveStats).toBeDefined();
    resolveStats?.(report(relayStats(gatewayIpv4)));
    await expect(pending).resolves.toBe(false);
    expect(
      Object.getOwnPropertyDescriptor(target, RELAY_GATEWAY_ATTESTATION_GLOBAL)
    ).toBeUndefined();
  });

  it("preserves the first disabled observation when the same track is enabled", () => {
    const track = {
      id: "microphone-track",
      kind: "audio",
      label: "fake microphone",
      enabled: false,
      muted: false,
      readyState: "live",
    } as MediaStreamTrack;
    const disabled = observeBrowserMediaTrack(track, 12.5, null);

    track.enabled = true;
    const enabled = observeBrowserMediaTrack(track, 44.5, disabled);

    expect(enabled).toMatchObject({
      id: "microphone-track",
      observed_at_ms: 12.5,
      enabled_at_observation: false,
      ready_state_at_observation: "live",
      media_stream_track_enabled: true,
      ready_state: "live",
    });
  });

  it("reports one selected pair and exact audio RTP counters without addresses", async () => {
    const evidence = await summarizeRtcPeerConnections([
      peer([
        {
          id: "transport-1",
          type: "transport",
          selectedCandidatePairId: "pair-1",
        },
        {
          id: "pair-1",
          type: "candidate-pair",
          state: "succeeded",
          nominated: true,
          localCandidateId: "local-1",
          remoteCandidateId: "remote-1",
          bytesSent: 12_345,
          bytesReceived: 54_321,
          currentRoundTripTime: 0.012,
        },
        {
          id: "local-1",
          type: "local-candidate",
          candidateType: "host",
          protocol: "udp",
          address: "192.0.2.10",
          port: 51_000,
        },
        {
          id: "remote-1",
          type: "remote-candidate",
          candidateType: "srflx",
          protocol: "udp",
          address: "198.51.100.20",
          port: 52_000,
        },
        {
          id: "outbound-1",
          type: "outbound-rtp",
          kind: "audio",
          bytesSent: 4_096,
          packetsSent: 32,
        },
        {
          id: "inbound-1",
          type: "inbound-rtp",
          mediaType: "audio",
          bytesReceived: 8_192,
          packetsReceived: 64,
        },
        {
          id: "outbound-video",
          type: "outbound-rtp",
          kind: "video",
          bytesSent: 999_999,
          packetsSent: 999,
        },
      ], {}, { senderId: "microphone-track", receiverId: "reply-track" }),
    ]);

    expect(evidence).toMatchObject({
      peer_connection_count: 1,
      open_peer_connection_count: 1,
      closed_peer_connection_count: 0,
      selected_candidate_pair_count: 1,
      outbound_audio: { stream_count: 1, bytes: 4_096, packets: 32 },
      inbound_audio: { stream_count: 1, bytes: 8_192, packets: 64 },
      peer_connections: [
        {
          connection_state: "connected",
          audio_sender_track_ids: ["microphone-track"],
          audio_receiver_track_ids: ["reply-track"],
          selected_candidate_pair: {
            state: "succeeded",
            nominated: true,
            bytes_sent: 12_345,
            bytes_received: 54_321,
            current_round_trip_time_seconds: 0.012,
            local: {
              candidate_type: "host",
              protocol: "udp",
              relay_protocol: null,
            },
            remote: {
              candidate_type: "srflx",
              protocol: "udp",
              relay_protocol: null,
            },
          },
        },
      ],
    });
    expect(JSON.stringify(evidence)).not.toContain("192.0.2.10");
    expect(JSON.stringify(evidence)).not.toContain("198.51.100.20");
  });

  it("uses the nominated succeeded fallback and preserves closed peers", async () => {
    const evidence = await summarizeRtcPeerConnections([
      peer(
        [
          {
            id: "pair-1",
            type: "candidate-pair",
            state: "succeeded",
            nominated: true,
            localCandidateId: "local-1",
            remoteCandidateId: "remote-1",
          },
          {
            id: "local-1",
            type: "local-candidate",
            candidateType: "relay",
            protocol: "udp",
            relayProtocol: "tls",
          },
          {
            id: "remote-1",
            type: "remote-candidate",
            candidateType: "host",
            protocol: "udp",
          },
        ],
        {
          connectionState: "closed",
          iceConnectionState: "closed",
          signalingState: "closed",
        }
      ),
    ]);

    expect(evidence.closed_peer_connection_count).toBe(1);
    expect(evidence.open_peer_connection_count).toBe(0);
    expect(
      evidence.peer_connections[0]?.selected_candidate_pair?.local
    ).toEqual({
      candidate_type: "relay",
      protocol: "udp",
      relay_protocol: "tls",
    });
  });

  it("contains a closed peer even when Chromium no longer exposes its stats", async () => {
    const connection = {
      connectionState: "closed",
      iceConnectionState: "closed",
      signalingState: "closed",
      getStats: async () => {
        throw new Error("peer is closed");
      },
      getSenders: () => [],
      getReceivers: () => [],
    } as unknown as RTCPeerConnection;

    await expect(summarizeRtcPeerConnections([connection])).resolves.toMatchObject({
      peer_connection_count: 1,
      closed_peer_connection_count: 1,
      peer_connections: [{ stats_available: false }],
    });
  });
});
