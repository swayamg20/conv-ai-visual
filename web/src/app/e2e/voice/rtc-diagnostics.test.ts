/** @vitest-environment happy-dom */

import { describe, expect, it } from "vitest";

import {
  observeBrowserMediaTrack,
  summarizeRtcPeerConnections,
} from "./rtc-diagnostics";

function report(records: readonly Record<string, unknown>[]): RTCStatsReport {
  const values = new Map(records.map((record) => [String(record.id), record]));
  return values as unknown as RTCStatsReport;
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
