import {
  PipecatClient,
  type Participant,
  type RTVIEventCallbacks,
  type RTVIMessage,
  type Tracks,
  type TransportState,
} from "@pipecat-ai/client-js";
import {
  SmallWebRTCTransport,
  type SmallWebRTCTransportConnectionOptions,
} from "@pipecat-ai/small-webrtc-transport";
import DailyIframe, { type DailyCall } from "@daily-co/daily-js";

import {
  decodeVoiceEvent,
  type EventOf,
  type VoiceEvent,
  type VoiceEventDecodeError,
} from "./events";

export const PIPECAT_VOICE_RUNTIME = "pipecat_smallwebrtc_v1" as const;
export const PIPECAT_EVENT_PROTOCOL = "rtvi-murmur-v2" as const;

export interface PipecatVoiceEventScope {
  readonly traceId: string;
  readonly voiceCallId: string;
  readonly sessionId: string;
  readonly profileId: string;
}

/**
 * Already-authorized browser locator. Bootstrap, Firebase authentication, and
 * reservation release deliberately remain outside this media adapter.
 */
export interface PipecatVoiceTransportConnection {
  readonly runtime: typeof PIPECAT_VOICE_RUNTIME;
  readonly eventProtocol: typeof PIPECAT_EVENT_PROTOCOL;
  readonly webrtcUrl: string;
  readonly peerReservationId: string;
  readonly eventScope: PipecatVoiceEventScope;
  readonly iceServers?: readonly RTCIceServer[];
}

export interface PipecatAudioTrackObservation {
  readonly direction: "local" | "remote";
  readonly trackId: string;
  readonly observedAtMs: number;
  readonly enabled: boolean;
  readonly muted: boolean;
  readonly readyState: MediaStreamTrackState;
}

export type PipecatEventRejection =
  | VoiceEventDecodeError
  | {
      readonly code:
        | "event_scope_mismatch"
        | "ready_profile_mismatch"
        | "conflicting_agent_ready";
      readonly message: string;
      readonly event_id?: string;
      readonly event_type?: string;
    };

export interface PipecatVoiceTransportCallbacks {
  /** Generation ownership remains with the session orchestrator. */
  readonly isCurrent: () => boolean;
  readonly onConnected: () => void;
  readonly onDisconnected: () => void;
  readonly onAgentDisconnected: () => void;
  readonly onTransportStateChanged?: (state: TransportState) => void;
  /** Called only with an immutable envelope accepted by the strict decoder. */
  readonly onEvent: (event: VoiceEvent) => void;
  readonly onInvalidEvent: (rejection: PipecatEventRejection) => void;
  readonly onTransportError: (error: Error) => void;
  readonly onMicrophoneUnavailable: (error: Error) => void;
  readonly onAudioPlaybackBlockedChange: (blocked: boolean) => void;
  readonly onLocalMicrophoneTrack?: (
    track: MediaStreamTrack | null,
    observation: PipecatAudioTrackObservation | null,
  ) => void;
  readonly onRemoteAudioTrack?: (
    track: MediaStreamTrack | null,
    observation: PipecatAudioTrackObservation | null,
  ) => void;
}

interface PipecatClientPort {
  connect(connectParams?: unknown): Promise<unknown>;
  disconnect(): Promise<void>;
  enableMic(enabled: boolean): void;
  tracks(): Tracks;
  /** Latch transport-owned delayed reconnect work before adapter callbacks run. */
  stopReconnectAttempts?(): void;
}

export type PipecatClientFactory = (
  callbacks: RTVIEventCallbacks,
) => PipecatClientPort;

export interface PipecatVoiceTransportOptions {
  readonly callbacks: PipecatVoiceTransportCallbacks;
  readonly outputEnabled?: boolean;
  readonly disconnectTimeoutMs?: number;
  /** Test seam; production uses the exact package-pinned SDK pair below. */
  readonly clientFactory?: PipecatClientFactory;
  readonly audioElementFactory?: () => HTMLAudioElement;
  readonly now?: () => number;
}

const DEFAULT_DISCONNECT_TIMEOUT_MS = 2_000;
const contractIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const DAILY_MEDIA_MANAGER_EVENT_NAMES = Object.freeze([
  "track-started",
  "track-stopped",
  "available-devices-updated",
  "selected-devices-updated",
  "camera-error",
  "local-audio-level",
] as const);

type DailyMediaManagerEventName =
  (typeof DAILY_MEDIA_MANAGER_EVENT_NAMES)[number];

interface DailyListenerRegistration {
  readonly eventName: DailyMediaManagerEventName;
  readonly listener: unknown;
}

interface DailyListenerCapture {
  readonly call: DailyCall;
  complete(): readonly DailyListenerRegistration[];
  rollback(): void;
}

function isDailyMediaManagerEventName(
  value: unknown,
): value is DailyMediaManagerEventName {
  return (
    typeof value === "string" &&
    DAILY_MEDIA_MANAGER_EVENT_NAMES.some((eventName) => eventName === value)
  );
}

function destroyDailyCallWithoutLeakingRejection(call: DailyCall): void {
  try {
    void call.destroy().catch(() => undefined);
  } catch {
    // Construction rollback must preserve the original compatibility error.
  }
}

/**
 * DailyMediaManager 1.10.6 binds its six call listeners inline and does not
 * retain the bound functions. Capture those exact listener identities while
 * its constructor runs so teardown can detach only what this transport owns.
 */
function beginDailyListenerCapture(): DailyListenerCapture {
  const call = DailyIframe.createCallObject();
  const originalOn: unknown = Reflect.get(call, "on");
  if (typeof originalOn !== "function") {
    destroyDailyCallWithoutLeakingRejection(call);
    throw new Error("Pinned Daily call does not expose its listener API");
  }

  const registrations: DailyListenerRegistration[] = [];
  let restored = false;
  let rolledBack = false;
  const restore = (): void => {
    if (restored) return;
    if (!Reflect.set(call, "on", originalOn)) {
      throw new Error("Pinned Daily call listener API could not be restored");
    }
    restored = true;
  };
  const capturingOn = (...arguments_: unknown[]): unknown => {
    const [eventName, listener] = arguments_;
    if (isDailyMediaManagerEventName(eventName)) {
      if (typeof listener !== "function") {
        throw new Error(
          "Pinned Daily media manager registered an invalid listener",
        );
      }
      registrations.push({ eventName, listener });
    }
    const result = Reflect.apply(originalOn, call, arguments_);
    if (registrations.length === DAILY_MEDIA_MANAGER_EVENT_NAMES.length) {
      restore();
    }
    return result;
  };
  if (!Reflect.set(call, "on", capturingOn)) {
    destroyDailyCallWithoutLeakingRejection(call);
    throw new Error("Pinned Daily call listener API could not be observed");
  }

  const rollback = (): void => {
    if (rolledBack) return;
    rolledBack = true;
    try {
      restore();
    } catch {
      // Continue detaching and destroying the failed partial owner.
    }
    try {
      detachDailyListeners(call, registrations);
    } catch {
      // Destroy still releases the singleton if listener removal is unavailable.
    }
    destroyDailyCallWithoutLeakingRejection(call);
  };

  return {
    call,
    rollback,
    complete: () => {
      restore();
      const observedEventNames = registrations.map(
        (registration) => registration.eventName,
      );
      const hasExactListenerGraph =
        registrations.length === DAILY_MEDIA_MANAGER_EVENT_NAMES.length &&
        DAILY_MEDIA_MANAGER_EVENT_NAMES.every(
          (eventName) =>
            observedEventNames.filter((observed) => observed === eventName)
              .length === 1,
        );
      if (!hasExactListenerGraph) {
        throw new Error(
          "Pinned Daily media manager listener graph is incompatible with cleanup",
        );
      }
      return Object.freeze([...registrations]);
    },
  };
}

function detachDailyListeners(
  call: DailyCall,
  registrations: readonly DailyListenerRegistration[],
): void {
  const off: unknown = Reflect.get(call, "off");
  if (typeof off !== "function") {
    throw new Error("Pinned Daily call does not expose listener removal");
  }
  const failures: unknown[] = [];
  for (const registration of registrations) {
    try {
      Reflect.apply(off, call, [registration.eventName, registration.listener]);
    } catch (error) {
      failures.push(error);
    }
  }
  if (failures.length > 0) {
    throw new AggregateError(
      failures,
      "Pinned Daily media listener cleanup failed",
    );
  }
}

type SmallWebRTCReconnectAttempt = (
  this: SmallWebRTCTransport,
  recreatePeerConnection?: boolean,
) => Promise<void>;

function isSmallWebRTCReconnectAttempt(
  value: unknown,
): value is SmallWebRTCReconnectAttempt {
  return typeof value === "function";
}

function pinnedSmallWebRTCReconnectAttempt(): SmallWebRTCReconnectAttempt {
  const candidate: unknown = Reflect.get(
    SmallWebRTCTransport.prototype,
    "attemptReconnection",
  );
  if (!isSmallWebRTCReconnectAttempt(candidate)) {
    throw new Error(
      "Pinned SmallWebRTC reconnect containment is incompatible with this package",
    );
  }
  return candidate;
}

const sdkAttemptReconnection = pinnedSmallWebRTCReconnectAttempt();

/**
 * SmallWebRTC 1.10.6 schedules anonymous reconnect callbacks which `stop()`
 * cannot cancel. Those callbacks dispatch through `attemptReconnection`, so a
 * permanent adapter-owned latch makes them inert after teardown. The guarded
 * prototype lookup deliberately fails closed if the exact pinned shape moves.
 */
class DisconnectContainedSmallWebRTCTransport extends SmallWebRTCTransport {
  private reconnectAttemptsStopped: boolean;
  private disconnectPromise: Promise<void> | null;
  private readonly ownedDailyCall: DailyCall;
  private readonly ownedDailyListeners: readonly DailyListenerRegistration[];

  constructor() {
    if (DailyIframe.getCallInstance()) {
      throw new Error(
        "Pinned SmallWebRTC media cannot share an existing Daily call instance",
      );
    }
    const listenerCapture = beginDailyListenerCapture();
    // The pre-created call is now Daily's singleton, so SmallWebRTC's pinned
    // default DailyMediaManager adopts it. This preserves its local-track to
    // peer-sender replacement callbacks while capturing its listener graph.
    try {
      super();
      this.reconnectAttemptsStopped = false;
      this.disconnectPromise = null;
      this.ownedDailyCall = listenerCapture.call;
      this.ownedDailyListeners = listenerCapture.complete();
    } catch (error) {
      listenerCapture.rollback();
      throw error;
    }
  }

  stopReconnectAttempts(): void {
    this.reconnectAttemptsStopped = true;
  }

  attemptReconnection(recreatePeerConnection: boolean = false): Promise<void> {
    if (this.reconnectAttemptsStopped) return Promise.resolve();
    return sdkAttemptReconnection.call(this, recreatePeerConnection);
  }

  override disconnect(): Promise<void> {
    if (this.disconnectPromise) return this.disconnectPromise;
    this.stopReconnectAttempts();
    const disconnect = (async () => {
      try {
        await super.disconnect();
      } catch {
        // PipecatClient fire-and-forgets disconnect on connection failure. Keep
        // this owned cleanup non-rejecting while the remaining releases run.
      }

      try {
        // Daily destroy does not remove application-owned event listeners.
        // Detach the exact six bound by DailyMediaManager before destroying the
        // call so the manager/transport callback graph becomes unreachable.
        detachDailyListeners(this.ownedDailyCall, this.ownedDailyListeners);
      } catch {
        // Daily destruction below remains the final singleton-release attempt.
      }

      try {
        // DailyMediaManager 1.10.6 does not await leave or destroy its call,
        // and SmallWebRTC.stop() can skip it when no peer was constructed.
        // Destroy releases the owned call/media and unregisters the singleton.
        await this.ownedDailyCall.destroy();
      } catch {
        // Teardown is best effort and must never become an unhandled rejection.
      }
    })();
    this.disconnectPromise = disconnect;
    return disconnect;
  }
}

function createDefaultClient(callbacks: RTVIEventCallbacks): PipecatClientPort {
  const transport = new DisconnectContainedSmallWebRTCTransport();
  const client = new PipecatClient({
    transport,
    callbacks,
    enableMic: false,
    enableCam: false,
    disconnectOnBotDisconnect: false,
  });
  return Object.assign(client, {
    stopReconnectAttempts: () => transport.stopReconnectAttempts(),
  });
}

function notifySafely<TArguments extends unknown[]>(
  callback: ((...args: TArguments) => void) | undefined,
  ...args: TArguments
): void {
  try {
    callback?.(...args);
  } catch {
    // Adapter consumers cannot be allowed to corrupt media or teardown state.
  }
}

function asError(value: unknown, fallback: string): Error {
  if (value instanceof Error) return value;
  if (typeof value === "object" && value !== null && "data" in value) {
    const data = value.data;
    if (
      typeof data === "object" &&
      data !== null &&
      "message" in data &&
      typeof data.message === "string" &&
      data.message.trim()
    ) {
      return new Error(data.message);
    }
  }
  return new Error(fallback);
}

function isContractId(value: string): boolean {
  return value.length <= 128 && contractIdPattern.test(value);
}

function isLoopbackHost(hostname: string): boolean {
  return (
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "[::1]" ||
    hostname === "::1"
  );
}

function validateConnection(connection: PipecatVoiceTransportConnection): void {
  if (
    connection.runtime !== PIPECAT_VOICE_RUNTIME ||
    connection.eventProtocol !== PIPECAT_EVENT_PROTOCOL
  ) {
    throw new Error(
      "Pipecat transport received an incompatible runtime assignment",
    );
  }
  if (!isContractId(connection.peerReservationId)) {
    throw new Error("Pipecat transport received an invalid reservation ID");
  }
  for (const [name, value] of Object.entries(connection.eventScope)) {
    if (!isContractId(value)) {
      throw new Error(`Pipecat transport received an invalid ${name}`);
    }
  }

  let locator: URL;
  try {
    locator = new URL(connection.webrtcUrl);
  } catch {
    throw new Error("Pipecat transport received an invalid WebRTC locator");
  }
  const allowedScheme =
    locator.protocol === "https:" ||
    (locator.protocol === "http:" && isLoopbackHost(locator.hostname));
  if (
    !allowedScheme ||
    locator.username ||
    locator.password ||
    locator.search ||
    locator.hash ||
    locator.pathname === "/"
  ) {
    throw new Error("Pipecat transport received an unsafe WebRTC locator");
  }
}

function freezeIceServers(
  iceServers: readonly RTCIceServer[] | undefined,
): readonly RTCIceServer[] | undefined {
  if (!iceServers) return undefined;
  return Object.freeze(
    iceServers.map((server) => {
      const urls: string | string[] = Array.isArray(server.urls)
        ? [...server.urls]
        : server.urls;
      if (Array.isArray(urls)) Object.freeze(urls);
      const snapshot: RTCIceServer = {
        ...server,
        urls,
      };
      return Object.freeze(snapshot);
    }),
  );
}

function copyIceServersForSdk(
  iceServers: readonly RTCIceServer[] | undefined,
): RTCIceServer[] | undefined {
  return iceServers?.map((server) => ({
    ...server,
    urls: Array.isArray(server.urls) ? [...server.urls] : server.urls,
  }));
}

function snapshotConnection(
  connection: PipecatVoiceTransportConnection,
): PipecatVoiceTransportConnection {
  const eventScope = Object.freeze({
    traceId: connection.eventScope.traceId,
    voiceCallId: connection.eventScope.voiceCallId,
    sessionId: connection.eventScope.sessionId,
    profileId: connection.eventScope.profileId,
  });
  const iceServers = freezeIceServers(connection.iceServers);
  return Object.freeze({
    runtime: connection.runtime,
    eventProtocol: connection.eventProtocol,
    webrtcUrl: connection.webrtcUrl,
    peerReservationId: connection.peerReservationId,
    eventScope,
    ...(iceServers ? { iceServers } : {}),
  });
}

function bounded(
  operation: Promise<unknown>,
  timeoutMs: number,
): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(finish, timeoutMs);
    operation.then(finish, finish);
  });
}

/**
 * Owns one PipecatClient, one SmallWebRTC peer, and its browser media objects.
 * It does not bootstrap, release, reconnect, or reduce product session state.
 */
export class PipecatVoiceTransport {
  private readonly callbacks: PipecatVoiceTransportCallbacks;
  private readonly client: PipecatClientPort;
  private readonly disconnectTimeoutMs: number;
  private readonly audioElementFactory: () => HTMLAudioElement;
  private readonly now: () => number;
  private readonly localTracks = new Set<MediaStreamTrack>();
  private readonly remoteTracks = new Set<MediaStreamTrack>();
  private remotePlaybackTrack: MediaStreamTrack | null = null;
  private connection: PipecatVoiceTransportConnection | null = null;
  private connectPromise: Promise<void> | null = null;
  private disconnectPromise: Promise<void> | null = null;
  private audioElement: HTMLAudioElement | null = null;
  private outputEnabled: boolean;
  private closed = false;
  private readyEvent: EventOf<"agent_ready"> | null = null;
  private readyFingerprint: string | null = null;
  private microphoneActivationPromise: Promise<void> | null = null;
  private microphoneAuthorized = false;
  private microphoneEnabledRequested = false;

  constructor(options: PipecatVoiceTransportOptions) {
    const timeout =
      options.disconnectTimeoutMs ?? DEFAULT_DISCONNECT_TIMEOUT_MS;
    if (
      isinstanceBoolean(timeout) ||
      !Number.isFinite(timeout) ||
      timeout <= 0 ||
      timeout > 10_000
    ) {
      throw new Error(
        "Pipecat disconnect timeout must be between 0 and 10000 ms",
      );
    }
    this.callbacks = options.callbacks;
    this.disconnectTimeoutMs = timeout;
    this.outputEnabled = options.outputEnabled ?? true;
    this.audioElementFactory =
      options.audioElementFactory ?? (() => document.createElement("audio"));
    this.now =
      options.now ??
      (() =>
        typeof performance === "undefined" ? Date.now() : performance.now());
    this.client = (options.clientFactory ?? createDefaultClient)(
      this.createSdkCallbacks(),
    );

    // Defense in depth for injected clients and SDK behavior changes. The
    // production client also receives enableMic:false at construction.
    this.client.enableMic(false);
  }

  /** Invoke in the user gesture before any authenticated bootstrap await. */
  primeAudioPlayback(): void {
    if (this.closed) return;
    const element = this.ensureAudioElement();
    void this.playAudioElement(element);
  }

  connect(connection: PipecatVoiceTransportConnection): Promise<void> {
    if (this.closed) {
      return Promise.reject(
        new Error("Pipecat voice transport is already closed"),
      );
    }
    if (this.connectPromise) return this.connectPromise;
    let ownedConnection: PipecatVoiceTransportConnection;
    try {
      ownedConnection = snapshotConnection(connection);
      validateConnection(ownedConnection);
    } catch (error) {
      return Promise.reject(error);
    }
    this.connection = ownedConnection;
    const element = this.ensureAudioElement();
    element.dataset.murmurVoiceCall = ownedConnection.eventScope.voiceCallId;

    const connectParams: SmallWebRTCTransportConnectionOptions = {
      webrtcRequestParams: { endpoint: ownedConnection.webrtcUrl },
      ...(ownedConnection.iceServers
        ? {
            iceConfig: {
              iceServers: copyIceServersForSdk(ownedConnection.iceServers),
            },
          }
        : {}),
    };
    const operation = (async () => {
      try {
        await this.client.connect(connectParams);
        this.captureLocalTrack();
        this.forceMicrophoneDisabled();
        if (!this.isCurrent()) {
          throw new Error(
            "Pipecat voice transport became stale during connection",
          );
        }
      } catch (error) {
        await this.disconnect();
        throw error;
      }
    })();
    this.connectPromise = operation;
    return operation;
  }

  /**
   * Accept only the exact strict Ready envelope previously delivered to this
   * caller. Repeated acceptance shares one activation; fabricated or stale
   * envelopes cannot open the microphone.
   */
  acceptCanonicalReady(event: EventOf<"agent_ready">): Promise<void> {
    if (this.closed) {
      return Promise.reject(
        new Error("Pipecat voice transport is already closed"),
      );
    }
    if (!this.isCurrent()) {
      return Promise.reject(
        new Error("Pipecat voice transport generation is stale"),
      );
    }
    if (!this.readyEvent || event !== this.readyEvent) {
      return Promise.reject(
        new Error(
          "Microphone activation requires this transport's canonical Ready",
        ),
      );
    }
    if (this.microphoneAuthorized) return Promise.resolve();
    if (this.microphoneActivationPromise) {
      return this.microphoneActivationPromise;
    }

    const activation = (async () => {
      await this.connectPromise;
      if (!this.isCurrent()) {
        throw new Error(
          "Pipecat voice transport became stale before activation",
        );
      }
      const track = this.captureLocalTrack();
      if (!track || track.readyState === "ended") {
        throw new Error("Pipecat microphone track is unavailable after Ready");
      }
      this.microphoneAuthorized = true;
      this.microphoneEnabledRequested = true;
      try {
        this.client.enableMic(true);
        const currentTrack = this.captureLocalTrack() ?? track;
        currentTrack.enabled = true;
        if (!this.isCurrent()) {
          throw new Error(
            "Pipecat voice transport became stale during activation",
          );
        }
        this.reportLocalTrack(currentTrack);
      } catch (error) {
        this.microphoneAuthorized = false;
        this.microphoneEnabledRequested = false;
        this.forceMicrophoneDisabled();
        throw error;
      }
    })();
    this.microphoneActivationPromise = activation;
    return activation;
  }

  setMicrophoneEnabled(enabled: boolean): Promise<void> {
    if (this.closed) return Promise.resolve();
    if (!this.microphoneAuthorized) {
      return Promise.reject(
        new Error("Microphone cannot be controlled before canonical Ready"),
      );
    }
    if (!this.isCurrent()) {
      return Promise.reject(
        new Error("Pipecat voice transport generation is stale"),
      );
    }
    const previousRequest = this.microphoneEnabledRequested;
    const existingTrack = this.captureLocalTrack();
    if (!existingTrack || existingTrack.readyState === "ended") {
      return Promise.reject(
        new Error("Pipecat microphone track is unavailable"),
      );
    }
    this.microphoneEnabledRequested = enabled;
    try {
      this.client.enableMic(enabled);
      const currentTrack = this.captureLocalTrack() ?? existingTrack;
      if (currentTrack.readyState === "ended") {
        throw new Error("Pipecat microphone track is unavailable");
      }
      currentTrack.enabled = this.shouldEnableMicrophone();
      this.reportLocalTrack(currentTrack);
      return Promise.resolve();
    } catch (error) {
      this.microphoneEnabledRequested = previousRequest;
      this.applyRequestedMicrophoneState();
      return Promise.reject(error);
    }
  }

  setOutputEnabled(enabled: boolean): void {
    this.outputEnabled = enabled;
    if (!this.audioElement) return;
    this.audioElement.muted = !enabled;
    if (enabled && !this.closed) void this.playAudioElement(this.audioElement);
  }

  /** Compatibility name for the existing Voice V2 control surface. */
  setTtsEnabled(enabled: boolean): void {
    this.setOutputEnabled(enabled);
  }

  async resumeAudio(): Promise<void> {
    if (this.closed) return;
    await this.playAudioElement(this.ensureAudioElement());
  }

  disconnect(): Promise<void> {
    if (this.disconnectPromise) return this.disconnectPromise;
    const ownedGenerationAtStart = this.isCurrent();
    this.closed = true;
    this.microphoneAuthorized = false;
    this.microphoneEnabledRequested = false;
    try {
      this.client.stopReconnectAttempts?.();
    } catch {
      // Continue local teardown even if an injected client cannot latch retries.
    }

    const sdkDisconnect = Promise.resolve().then(() =>
      this.client.disconnect(),
    );
    this.disconnectPromise = bounded(sdkDisconnect, this.disconnectTimeoutMs);
    this.forceMicrophoneDisabled();

    for (const track of [...this.localTracks, ...this.remoteTracks]) {
      if (track.readyState !== "ended") track.stop();
    }
    this.localTracks.clear();
    this.remoteTracks.clear();
    this.remotePlaybackTrack = null;
    if (ownedGenerationAtStart) {
      notifySafely(this.callbacks.onLocalMicrophoneTrack, null, null);
      notifySafely(this.callbacks.onRemoteAudioTrack, null, null);
    }

    const element = this.audioElement;
    this.audioElement = null;
    if (element) {
      element.pause();
      element.srcObject = null;
      element.remove();
    }

    return this.disconnectPromise;
  }

  private createSdkCallbacks(): RTVIEventCallbacks {
    return {
      onConnected: () => {
        if (!this.isCurrent()) return;
        this.captureLocalTrack();
        this.forceMicrophoneDisabled();
        notifySafely(this.callbacks.onConnected);
      },
      onDisconnected: () => {
        if (this.isCurrent()) notifySafely(this.callbacks.onDisconnected);
      },
      onBotDisconnected: () => {
        if (this.isCurrent()) notifySafely(this.callbacks.onAgentDisconnected);
      },
      onTransportStateChanged: (state) => {
        if (this.isCurrent()) {
          notifySafely(this.callbacks.onTransportStateChanged, state);
        }
      },
      onServerMessage: (input: unknown) => this.handleServerMessage(input),
      onTrackStarted: (track, participant) =>
        this.handleTrackStarted(track, participant),
      onTrackStopped: (track, participant) =>
        this.handleTrackStopped(track, participant),
      onDeviceError: (error) => {
        if (this.isCurrent()) {
          notifySafely(this.callbacks.onMicrophoneUnavailable, error);
        }
      },
      onError: (message: RTVIMessage) => {
        if (this.isCurrent()) {
          notifySafely(
            this.callbacks.onTransportError,
            asError(message, "Pipecat transport reported an error"),
          );
        }
      },
      onMessageError: (message: RTVIMessage) => {
        if (this.isCurrent()) {
          notifySafely(
            this.callbacks.onTransportError,
            asError(message, "Pipecat event channel reported an error"),
          );
        }
      },
    };
  }

  private handleServerMessage(input: unknown): void {
    if (!this.isCurrent() || !this.connection) return;
    const decoded = decodeVoiceEvent(input);
    if (!decoded.ok) {
      notifySafely(this.callbacks.onInvalidEvent, decoded.error);
      return;
    }
    const event = decoded.event;
    const scope = this.connection.eventScope;
    if (
      event.trace_id !== scope.traceId ||
      event.voice_call_id !== scope.voiceCallId ||
      event.session_id !== scope.sessionId
    ) {
      notifySafely(this.callbacks.onInvalidEvent, {
        code: "event_scope_mismatch",
        message: "Pipecat event does not match the assigned Murmur call",
        event_id: event.event_id,
        event_type: event.event_type,
      });
      return;
    }

    if (event.event_type === "agent_ready") {
      if (event.payload.profile_id !== scope.profileId) {
        notifySafely(this.callbacks.onInvalidEvent, {
          code: "ready_profile_mismatch",
          message: "Pipecat Ready does not match the assigned profile",
          event_id: event.event_id,
          event_type: event.event_type,
        });
        return;
      }
      const fingerprint = JSON.stringify(event);
      if (this.readyEvent) {
        if (fingerprint === this.readyFingerprint) return;
        notifySafely(this.callbacks.onInvalidEvent, {
          code: "conflicting_agent_ready",
          message: "Pipecat emitted more than one canonical Ready",
          event_id: event.event_id,
          event_type: event.event_type,
        });
        return;
      }
      this.readyEvent = event;
      this.readyFingerprint = fingerprint;
    }
    notifySafely(this.callbacks.onEvent, event);
  }

  private handleTrackStarted(
    track: MediaStreamTrack,
    participant?: Participant,
  ): void {
    if (track.kind !== "audio") return;
    const localTrack = this.currentLocalTrack();
    const isLocal = participant?.local === true || track === localTrack;
    if (!this.isCurrent()) {
      if (isLocal) track.enabled = false;
      return;
    }
    if (isLocal) {
      this.disableSupersededLocalTracks(track);
      track.enabled = this.shouldEnableMicrophone();
      this.localTracks.add(track);
      this.reportLocalTrack(track);
      return;
    }

    this.remoteTracks.add(track);
    this.remotePlaybackTrack = track;
    const element = this.ensureAudioElement();
    element.srcObject = new MediaStream([track]);
    element.muted = !this.outputEnabled;
    this.reportRemoteTrack(track);
    void this.playAudioElement(element);
  }

  private handleTrackStopped(
    track: MediaStreamTrack,
    participant?: Participant,
  ): void {
    if (track.kind !== "audio") return;
    if (participant?.local === true || this.localTracks.has(track)) {
      track.enabled = false;
      this.localTracks.delete(track);
      if (this.isCurrent()) this.reportLocalTrack(track);
      return;
    }
    if (!this.isCurrent()) return;
    if (!this.remoteTracks.has(track)) return;
    if (this.remotePlaybackTrack !== track) return;
    this.remotePlaybackTrack = null;
    this.reportRemoteTrack(track);
    if (this.audioElement) {
      this.audioElement.pause();
      this.audioElement.srcObject = null;
    }
  }

  private currentLocalTrack(): MediaStreamTrack | undefined {
    try {
      return this.client.tracks().local.audio;
    } catch {
      return undefined;
    }
  }

  private captureLocalTrack(): MediaStreamTrack | undefined {
    const track = this.currentLocalTrack();
    if (!track) return undefined;
    this.disableSupersededLocalTracks(track);
    track.enabled = this.shouldEnableMicrophone();
    this.localTracks.add(track);
    this.reportLocalTrack(track);
    return track;
  }

  private forceMicrophoneDisabled(): void {
    try {
      this.client.enableMic(false);
    } catch {
      // Native tracks are still disabled below even if the SDK is mid-teardown.
    }
    for (const track of this.localTracks) track.enabled = false;
    const current = this.currentLocalTrack();
    if (current) {
      current.enabled = false;
      this.localTracks.add(current);
    }
  }

  private shouldEnableMicrophone(): boolean {
    return this.microphoneAuthorized && this.microphoneEnabledRequested;
  }

  private applyRequestedMicrophoneState(): void {
    const enabled = this.shouldEnableMicrophone();
    const current = this.currentLocalTrack();
    for (const track of this.localTracks) {
      track.enabled = track === current ? enabled : false;
    }
    if (current) {
      current.enabled = enabled;
      this.localTracks.add(current);
    }
  }

  private disableSupersededLocalTracks(current: MediaStreamTrack): void {
    for (const track of this.localTracks) {
      if (track !== current) track.enabled = false;
    }
  }

  private reportLocalTrack(track: MediaStreamTrack): void {
    if (!this.isCurrent()) return;
    notifySafely(
      this.callbacks.onLocalMicrophoneTrack,
      track,
      this.observeTrack(track, "local"),
    );
  }

  private reportRemoteTrack(track: MediaStreamTrack): void {
    if (!this.isCurrent()) return;
    notifySafely(
      this.callbacks.onRemoteAudioTrack,
      track,
      this.observeTrack(track, "remote"),
    );
  }

  private observeTrack(
    track: MediaStreamTrack,
    direction: "local" | "remote",
  ): PipecatAudioTrackObservation {
    return Object.freeze({
      direction,
      trackId: track.id,
      observedAtMs: this.now(),
      enabled: track.enabled,
      muted: track.muted,
      readyState: track.readyState,
    });
  }

  private ensureAudioElement(): HTMLAudioElement {
    if (this.audioElement) return this.audioElement;
    const element = this.audioElementFactory();
    element.autoplay = true;
    element.setAttribute("playsinline", "");
    element.muted = !this.outputEnabled;
    element.className = "hidden";
    document.body.appendChild(element);
    this.audioElement = element;
    return element;
  }

  private async playAudioElement(element: HTMLAudioElement): Promise<void> {
    try {
      await element.play();
      if (this.isCurrent()) {
        notifySafely(this.callbacks.onAudioPlaybackBlockedChange, false);
      }
    } catch {
      if (this.isCurrent()) {
        notifySafely(this.callbacks.onAudioPlaybackBlockedChange, true);
      }
    }
  }

  private isCurrent(): boolean {
    if (this.closed) return false;
    try {
      return this.callbacks.isCurrent();
    } catch {
      return false;
    }
  }
}

function isinstanceBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}
