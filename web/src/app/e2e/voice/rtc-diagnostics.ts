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
  /** Present only for the forced-relay harness. */
  readonly relay_policy_attested?: boolean;
}

export interface BrowserRtcDiagnostics {
  readonly read: () => Promise<BrowserRtcEvidence>;
  readonly restore: () => void;
}

export type BrowserRtcNetworkMode = "direct" | "relay-tls";

export const RELAY_GATEWAY_ATTESTATION_GLOBAL =
  "__murmurE2ERelayGatewayAttestationV1";

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

type RelayGatewayAttestation = (
  expectedGatewayIpv4: string
) => Promise<boolean>;

type StatsRecord = Readonly<Record<string, unknown>> & {
  readonly id?: unknown;
  readonly type?: unknown;
};

interface SelectedPairRecords {
  readonly pair: StatsRecord;
  readonly local: StatsRecord;
  readonly remote: StatsRecord;
}

const EMPTY_RTP: RtcRtpEvidence = Object.freeze({
  stream_count: 0,
  bytes: 0,
  packets: 0,
});

const RELAY_CONSTRUCTOR_ERROR =
  "Relay RTC constructor configuration is incompatible";
const RELAY_GATEWAY_API_ERROR =
  "Relay RTC gateway attestation API is incompatible";

export function parseBrowserRtcNetworkMode(
  value: string | undefined
): BrowserRtcNetworkMode {
  if (value === undefined) return "direct";
  if (value === "direct" || value === "relay-tls") return value;
  throw new Error("Browser RTC network mode is invalid");
}

export function isCanonicalPrivateIpv4(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const parts = value.split(".");
  if (parts.length !== 4) return false;
  const octets: number[] = [];
  for (const part of parts) {
    if (!/^(?:0|[1-9]\d{0,2})$/.test(part)) return false;
    const octet = Number(part);
    if (!Number.isInteger(octet) || octet > 255) return false;
    octets.push(octet);
  }
  const [first, second, , fourth] = octets;
  const privateRange =
    first === 10 ||
    (first === 172 && second !== undefined && second >= 16 && second <= 31) ||
    (first === 192 && second === 168);
  return privateRange && fourth !== undefined && fourth > 0 && fourth < 255;
}

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

export function emptyBrowserRtcEvidence(
  network: BrowserRtcNetworkMode = "direct"
): BrowserRtcEvidence {
  const evidence: BrowserRtcEvidence = {
    peer_connection_count: 0,
    open_peer_connection_count: 0,
    closed_peer_connection_count: 0,
    selected_candidate_pair_count: 0,
    outbound_audio: EMPTY_RTP,
    inbound_audio: EMPTY_RTP,
    peer_connections: [],
  };
  return network === "relay-tls"
    ? { ...evidence, relay_policy_attested: false }
    : evidence;
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

function authoritativeSelectedPairRecords(
  records: readonly StatsRecord[]
): SelectedPairRecords | null {
  const byId = new Map<string, StatsRecord>();
  for (const record of records) {
    if (typeof record.id === "string") byId.set(record.id, record);
  }
  const selectedPairIds = new Set<string>();
  for (const record of records) {
    if (record.type !== "transport") continue;
    const pairId = Reflect.get(record, "selectedCandidatePairId");
    if (typeof pairId === "string") selectedPairIds.add(pairId);
  }
  if (selectedPairIds.size !== 1) return null;
  const [pairId] = selectedPairIds;
  if (pairId === undefined) return null;
  const pair = byId.get(pairId);
  if (!pair || pair.type !== "candidate-pair") return null;
  const localId = Reflect.get(pair, "localCandidateId");
  const remoteId = Reflect.get(pair, "remoteCandidateId");
  const local = typeof localId === "string" ? byId.get(localId) : undefined;
  const remote = typeof remoteId === "string" ? byId.get(remoteId) : undefined;
  return local && remote ? { pair, local, remote } : null;
}

async function attestRelayGateway(
  connection: RTCPeerConnection,
  expectedGatewayIpv4: string
): Promise<boolean> {
  try {
    const selected = authoritativeSelectedPairRecords(
      statsRecords(await connection.getStats())
    );
    if (!selected) return false;
    const { pair, local, remote } = selected;
    return (
      Reflect.get(pair, "state") === "succeeded" &&
      Reflect.get(pair, "nominated") === true &&
      finiteNumber(Reflect.get(pair, "bytesSent")) > 0 &&
      finiteNumber(Reflect.get(pair, "bytesReceived")) > 0 &&
      local.type === "local-candidate" &&
      Reflect.get(local, "candidateType") === "relay" &&
      Reflect.get(local, "protocol") === "udp" &&
      Reflect.get(local, "relayProtocol") === "tls" &&
      remote.type === "remote-candidate" &&
      Reflect.get(remote, "candidateType") === "host" &&
      Reflect.get(remote, "protocol") === "udp" &&
      Reflect.get(remote, "relayProtocol") === undefined &&
      Reflect.get(remote, "address") === expectedGatewayIpv4
    );
  } catch {
    return false;
  }
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
  network: BrowserRtcNetworkMode = "direct",
  target: RtcConstructorOwner = globalThis
): BrowserRtcDiagnostics {
  if (network !== "direct" && network !== "relay-tls") {
    throw new Error("Browser RTC network mode is invalid");
  }
  const NativePeerConnection = target.RTCPeerConnection;
  if (typeof NativePeerConnection !== "function") {
    throw new Error("RTCPeerConnection is unavailable in this browser");
  }
  const previousDescriptor = Object.getOwnPropertyDescriptor(
    target,
    "RTCPeerConnection"
  );
  let previousGatewayDescriptor: PropertyDescriptor | undefined;
  if (network === "relay-tls") {
    try {
      previousGatewayDescriptor = Object.getOwnPropertyDescriptor(
        target,
        RELAY_GATEWAY_ATTESTATION_GLOBAL
      );
    } catch {
      throw new Error(RELAY_GATEWAY_API_ERROR);
    }
    if (previousGatewayDescriptor !== undefined) {
      throw new Error(RELAY_GATEWAY_API_ERROR);
    }
  }
  const connections: RTCPeerConnection[] = [];
  let relayPolicyAttestedConnectionCount = 0;
  let relayGatewayAttestationActive = network === "relay-tls";
  const relayGatewayAttestation: RelayGatewayAttestation = Object.freeze(
    async (expectedGatewayIpv4: string) => {
      if (
        !relayGatewayAttestationActive ||
        !isCanonicalPrivateIpv4(expectedGatewayIpv4) ||
        connections.length !== 1 ||
        relayPolicyAttestedConnectionCount !== 1
      ) {
        return false;
      }
      const [connection] = connections;
      if (connection === undefined) return false;
      const attested = await attestRelayGateway(
        connection,
        expectedGatewayIpv4
      );
      return (
        attested &&
        relayGatewayAttestationActive &&
        connections.length === 1 &&
        relayPolicyAttestedConnectionCount === 1
      );
    }
  );
  const CapturingPeerConnection = new Proxy(NativePeerConnection, {
    construct(constructor, argumentsList, newTarget) {
      if (network === "direct") {
        const connection = Reflect.construct(
          constructor,
          argumentsList,
          newTarget
        ) as RTCPeerConnection;
        connections.push(connection);
        return connection;
      }

      let relayConfig: RTCConfiguration;
      try {
        if (argumentsList.length !== 1) {
          throw new Error(RELAY_CONSTRUCTOR_ERROR);
        }
        const config = argumentsList[0];
        if (
          typeof config !== "object" ||
          config === null ||
          Array.isArray(config) ||
          Object.getPrototypeOf(config) !== Object.prototype ||
          Reflect.ownKeys(config).length !== 1 ||
          !Object.prototype.hasOwnProperty.call(config, "iceServers")
        ) {
          throw new Error(RELAY_CONSTRUCTOR_ERROR);
        }
        const descriptor = Object.getOwnPropertyDescriptor(config, "iceServers");
        if (
          descriptor === undefined ||
          !("value" in descriptor) ||
          descriptor.enumerable !== true
        ) {
          throw new Error(RELAY_CONSTRUCTOR_ERROR);
        }
        relayConfig = {
          iceServers: descriptor.value,
          iceTransportPolicy: "relay",
        };
      } catch {
        throw new Error(RELAY_CONSTRUCTOR_ERROR);
      }

      let connection: RTCPeerConnection | undefined;
      try {
        connection = Reflect.construct(
          constructor,
          [relayConfig],
          newTarget
        ) as RTCPeerConnection;
        if (connection.getConfiguration().iceTransportPolicy !== "relay") {
          throw new Error(RELAY_CONSTRUCTOR_ERROR);
        }
        relayPolicyAttestedConnectionCount += 1;
      } catch {
        try {
          connection?.close();
        } catch {
          // The fixed contract error below remains the only surfaced detail.
        }
        throw new Error(RELAY_CONSTRUCTOR_ERROR);
      }
      if (connection === undefined) throw new Error(RELAY_CONSTRUCTOR_ERROR);
      connections.push(connection);
      return connection;
    },
  });
  Object.defineProperty(target, "RTCPeerConnection", {
    configurable: true,
    writable: true,
    value: CapturingPeerConnection,
  });
  if (network === "relay-tls") {
    try {
      Object.defineProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL, {
        configurable: true,
        enumerable: false,
        writable: false,
        value: relayGatewayAttestation,
      });
      const installedGatewayDescriptor = Object.getOwnPropertyDescriptor(
        target,
        RELAY_GATEWAY_ATTESTATION_GLOBAL
      );
      if (
        installedGatewayDescriptor === undefined ||
        !("value" in installedGatewayDescriptor) ||
        installedGatewayDescriptor.configurable !== true ||
        installedGatewayDescriptor.enumerable !== false ||
        installedGatewayDescriptor.writable !== false ||
        installedGatewayDescriptor.value !== relayGatewayAttestation
      ) {
        throw new Error(RELAY_GATEWAY_API_ERROR);
      }
    } catch {
      relayGatewayAttestationActive = false;
      const installedGatewayDescriptor = Object.getOwnPropertyDescriptor(
        target,
        RELAY_GATEWAY_ATTESTATION_GLOBAL
      );
      if (
        installedGatewayDescriptor !== undefined &&
        "value" in installedGatewayDescriptor &&
        installedGatewayDescriptor.value === relayGatewayAttestation
      ) {
        Reflect.deleteProperty(target, RELAY_GATEWAY_ATTESTATION_GLOBAL);
      }
      if (target.RTCPeerConnection === CapturingPeerConnection) {
        if (previousDescriptor) {
          Object.defineProperty(target, "RTCPeerConnection", previousDescriptor);
        } else {
          Reflect.deleteProperty(target, "RTCPeerConnection");
        }
      }
      throw new Error(RELAY_GATEWAY_API_ERROR);
    }
  }

  let rtcRestored = false;
  let gatewayApiRestored = network === "direct";
  return {
    read: async () => {
      const evidence = await summarizeRtcPeerConnections(connections);
      return network === "relay-tls"
        ? {
            ...evidence,
            relay_policy_attested:
              connections.length === 1 &&
              relayPolicyAttestedConnectionCount === 1,
          }
        : evidence;
    },
    restore: () => {
      relayGatewayAttestationActive = false;
      if (!gatewayApiRestored) {
        const currentGatewayDescriptor = Object.getOwnPropertyDescriptor(
          target,
          RELAY_GATEWAY_ATTESTATION_GLOBAL
        );
        if (currentGatewayDescriptor === undefined) {
          gatewayApiRestored = true;
        } else if (
          "value" in currentGatewayDescriptor &&
          currentGatewayDescriptor.value === relayGatewayAttestation
        ) {
          gatewayApiRestored = Reflect.deleteProperty(
            target,
            RELAY_GATEWAY_ATTESTATION_GLOBAL
          );
        }
      }
      if (!rtcRestored && target.RTCPeerConnection === CapturingPeerConnection) {
        rtcRestored = true;
        if (previousDescriptor) {
          Object.defineProperty(target, "RTCPeerConnection", previousDescriptor);
        } else {
          Reflect.deleteProperty(target, "RTCPeerConnection");
        }
      }
    },
  };
}
