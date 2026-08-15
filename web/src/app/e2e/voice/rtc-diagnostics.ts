export interface RtcRtpEvidence {
  readonly stream_count: number;
  readonly bytes: number;
  readonly packets: number;
}

export interface RtcIceCandidateEvidence {
  readonly candidate_type: string;
  readonly protocol: string;
  readonly relay_protocol: string | null;
}

export interface RtcSelectedCandidatePairEvidence {
  readonly state: string;
  readonly nominated: boolean;
  readonly bytes_sent: number;
  readonly bytes_received: number;
  readonly current_round_trip_time_seconds: number | null;
  readonly local: RtcIceCandidateEvidence;
  readonly remote: RtcIceCandidateEvidence;
}

export interface RtcPeerConnectionEvidence {
  readonly sequence: number;
  readonly connection_state: string;
  readonly ice_connection_state: string;
  readonly signaling_state: string;
  readonly stats_available: boolean;
  readonly audio_sender_track_ids: readonly string[];
  readonly audio_receiver_track_ids: readonly string[];
  readonly selected_candidate_pair: RtcSelectedCandidatePairEvidence | null;
  readonly outbound_audio: RtcRtpEvidence;
  readonly inbound_audio: RtcRtpEvidence;
}

export interface BrowserRtcEvidence {
  readonly peer_connection_count: number;
  readonly open_peer_connection_count: number;
  readonly closed_peer_connection_count: number;
  readonly selected_candidate_pair_count: number;
  readonly outbound_audio: RtcRtpEvidence;
  readonly inbound_audio: RtcRtpEvidence;
  readonly peer_connections: readonly RtcPeerConnectionEvidence[];
}

export interface BrowserRtcDiagnostics {
  readonly read: () => Promise<BrowserRtcEvidence>;
  readonly restore: () => void;
}

export interface BrowserMediaTrackEvidence {
  readonly id: string;
  readonly kind: string;
  readonly label: string;
  readonly observed_at_ms: number;
  readonly enabled_at_observation: boolean;
  readonly muted_at_observation: boolean;
  readonly ready_state_at_observation: MediaStreamTrackState;
  readonly media_stream_track_enabled: boolean;
  readonly muted: boolean;
  readonly ready_state: MediaStreamTrackState;
}

interface RtcConstructorOwner {
  RTCPeerConnection: typeof RTCPeerConnection;
}

type StatsRecord = Readonly<Record<string, unknown>> & {
  readonly id?: unknown;
  readonly type?: unknown;
};

const EMPTY_RTP: RtcRtpEvidence = Object.freeze({
  stream_count: 0,
  bytes: 0,
  packets: 0,
});

/** Keep immutable first-seen state while refreshing the same track's live state. */
export function observeBrowserMediaTrack(
  track: MediaStreamTrack,
  observedAtMs: number,
  previous: BrowserMediaTrackEvidence | null,
): BrowserMediaTrackEvidence {
  const current = {
    media_stream_track_enabled: track.enabled,
    muted: track.muted,
    ready_state: track.readyState,
  };
  if (previous?.id === track.id) {
    return { ...previous, ...current };
  }
  return {
    id: track.id,
    kind: track.kind,
    label: track.label,
    observed_at_ms: observedAtMs,
    enabled_at_observation: track.enabled,
    muted_at_observation: track.muted,
    ready_state_at_observation: track.readyState,
    ...current,
  };
}

export function emptyBrowserRtcEvidence(): BrowserRtcEvidence {
  return {
    peer_connection_count: 0,
    open_peer_connection_count: 0,
    closed_peer_connection_count: 0,
    selected_candidate_pair_count: 0,
    outbound_audio: EMPTY_RTP,
    inbound_audio: EMPTY_RTP,
    peer_connections: [],
  };
}

function finiteNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function optionalFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function text(value: unknown, fallback = "unknown"): string {
  return typeof value === "string" && value ? value : fallback;
}

function statsRecords(report: RTCStatsReport): readonly StatsRecord[] {
  const records: StatsRecord[] = [];
  report.forEach((value: unknown) => {
    if (typeof value === "object" && value !== null) {
      records.push(value as StatsRecord);
    }
  });
  return records;
}

function summarizeRtp(
  records: readonly StatsRecord[],
  type: "outbound-rtp" | "inbound-rtp"
): RtcRtpEvidence {
  const streams = records.filter((record) => {
    if (record.type !== type || Reflect.get(record, "isRemote") === true) {
      return false;
    }
    const kind = Reflect.get(record, "kind") ?? Reflect.get(record, "mediaType");
    return kind === "audio";
  });
  return {
    stream_count: streams.length,
    bytes: streams.reduce(
      (total, record) =>
        total +
        finiteNumber(
          Reflect.get(record, type === "outbound-rtp" ? "bytesSent" : "bytesReceived")
        ),
      0
    ),
    packets: streams.reduce(
      (total, record) =>
        total +
        finiteNumber(
          Reflect.get(
            record,
            type === "outbound-rtp" ? "packetsSent" : "packetsReceived"
          )
        ),
      0
    ),
  };
}

function candidateEvidence(record: StatsRecord): RtcIceCandidateEvidence {
  return {
    candidate_type: text(Reflect.get(record, "candidateType")),
    protocol: text(Reflect.get(record, "protocol")),
    relay_protocol:
      typeof Reflect.get(record, "relayProtocol") === "string"
        ? (Reflect.get(record, "relayProtocol") as string)
        : null,
  };
}

function selectedPair(
  records: readonly StatsRecord[]
): RtcSelectedCandidatePairEvidence | null {
  const byId = new Map<string, StatsRecord>();
  for (const record of records) {
    if (typeof record.id === "string") byId.set(record.id, record);
  }

  let pair: StatsRecord | undefined;
  for (const record of records) {
    if (record.type !== "transport") continue;
    const pairId = Reflect.get(record, "selectedCandidatePairId");
    if (typeof pairId === "string") {
      pair = byId.get(pairId);
      if (pair) break;
    }
  }
  pair ??= records.find(
    (record) =>
      record.type === "candidate-pair" &&
      (Reflect.get(record, "selected") === true ||
        (Reflect.get(record, "nominated") === true &&
          Reflect.get(record, "state") === "succeeded"))
  );
  if (!pair) return null;

  const localId = Reflect.get(pair, "localCandidateId");
  const remoteId = Reflect.get(pair, "remoteCandidateId");
  const local = typeof localId === "string" ? byId.get(localId) : undefined;
  const remote = typeof remoteId === "string" ? byId.get(remoteId) : undefined;
  if (!local || !remote) return null;

  return {
    state: text(Reflect.get(pair, "state")),
    nominated: Reflect.get(pair, "nominated") === true,
    bytes_sent: finiteNumber(Reflect.get(pair, "bytesSent")),
    bytes_received: finiteNumber(Reflect.get(pair, "bytesReceived")),
    current_round_trip_time_seconds: optionalFiniteNumber(
      Reflect.get(pair, "currentRoundTripTime")
    ),
    local: candidateEvidence(local),
    remote: candidateEvidence(remote),
  };
}

async function summarizeConnection(
  connection: RTCPeerConnection,
  sequence: number
): Promise<RtcPeerConnectionEvidence> {
  let records: readonly StatsRecord[] = [];
  let statsAvailable = true;
  try {
    records = statsRecords(await connection.getStats());
  } catch {
    // A closed browser peer may stop exposing stats. Its terminal state still
    // remains useful cleanup evidence and the last pre-close sample is emitted
    // by the harness before teardown.
    statsAvailable = false;
  }
  const audioSenderTrackIds = connection
    .getSenders()
    .map((sender) => sender.track)
    .filter(
      (track): track is MediaStreamTrack => track?.kind === "audio"
    )
    .map((track) => track.id);
  const audioReceiverTrackIds = connection
    .getReceivers()
    .map((receiver) => receiver.track)
    .filter((track) => track.kind === "audio")
    .map((track) => track.id);
  return {
    sequence,
    connection_state: connection.connectionState,
    ice_connection_state: connection.iceConnectionState,
    signaling_state: connection.signalingState,
    stats_available: statsAvailable,
    audio_sender_track_ids: audioSenderTrackIds,
    audio_receiver_track_ids: audioReceiverTrackIds,
    selected_candidate_pair: selectedPair(records),
    outbound_audio: summarizeRtp(records, "outbound-rtp"),
    inbound_audio: summarizeRtp(records, "inbound-rtp"),
  };
}

function totalRtp(
  peers: readonly RtcPeerConnectionEvidence[],
  direction: "outbound_audio" | "inbound_audio"
): RtcRtpEvidence {
  return peers.reduce<RtcRtpEvidence>(
    (total, peer) => ({
      stream_count: total.stream_count + peer[direction].stream_count,
      bytes: total.bytes + peer[direction].bytes,
      packets: total.packets + peer[direction].packets,
    }),
    EMPTY_RTP
  );
}

export async function summarizeRtcPeerConnections(
  connections: readonly RTCPeerConnection[]
): Promise<BrowserRtcEvidence> {
  const peers = await Promise.all(
    connections.map((connection, index) => summarizeConnection(connection, index + 1))
  );
  return {
    peer_connection_count: peers.length,
    open_peer_connection_count: peers.filter(
      (peer) => peer.connection_state !== "closed"
    ).length,
    closed_peer_connection_count: peers.filter(
      (peer) => peer.connection_state === "closed"
    ).length,
    selected_candidate_pair_count: peers.filter(
      (peer) => peer.selected_candidate_pair !== null
    ).length,
    outbound_audio: totalRtp(peers, "outbound_audio"),
    inbound_audio: totalRtp(peers, "inbound_audio"),
    peer_connections: peers,
  };
}

/** Install before either runtime's dynamically imported SDK constructs a peer. */
export function installBrowserRtcDiagnostics(
  target: RtcConstructorOwner = globalThis
): BrowserRtcDiagnostics {
  const NativePeerConnection = target.RTCPeerConnection;
  if (typeof NativePeerConnection !== "function") {
    throw new Error("RTCPeerConnection is unavailable in this browser");
  }
  const previousDescriptor = Object.getOwnPropertyDescriptor(
    target,
    "RTCPeerConnection"
  );
  const connections: RTCPeerConnection[] = [];
  const CapturingPeerConnection = new Proxy(NativePeerConnection, {
    construct(constructor, argumentsList, newTarget) {
      const connection = Reflect.construct(
        constructor,
        argumentsList,
        newTarget
      ) as RTCPeerConnection;
      connections.push(connection);
      return connection;
    },
  });
  Object.defineProperty(target, "RTCPeerConnection", {
    configurable: true,
    writable: true,
    value: CapturingPeerConnection,
  });

  let restored = false;
  return {
    read: () => summarizeRtcPeerConnections(connections),
    restore: () => {
      if (restored || target.RTCPeerConnection !== CapturingPeerConnection) return;
      restored = true;
      if (previousDescriptor) {
        Object.defineProperty(target, "RTCPeerConnection", previousDescriptor);
      } else {
        Reflect.deleteProperty(target, "RTCPeerConnection");
      }
    },
  };
}
