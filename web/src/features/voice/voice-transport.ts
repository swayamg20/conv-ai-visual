import {
  decodeVoiceEvent,
  type EventOf,
  type VoiceEvent,
  type VoiceEventDecodeError,
} from "./events";
import type { LocalMicrophonePublicationObservation } from "./livekit-transport";
import type {
  PipecatAudioTrackObservation,
  PipecatEventRejection,
  PipecatVoiceTransportConnection,
} from "./pipecat-transport";
import type {
  PipecatSignalingPort,
  PipecatSignalingPortOptions,
} from "./pipecat-signaling-api";
import type {
  LiveKitVoiceSessionBootstrap,
  PipecatBrowserVoiceAssignment,
  VoiceAuthHeaderProvider,
  VoiceSessionBootstrap,
} from "./session-api";

export type VoiceTransportRuntime = VoiceSessionBootstrap["runtime"];

export type VoiceTransportEventRejection =
  | VoiceEventDecodeError
  | {
      readonly code:
        | "conflicting_agent_ready"
        | "event_scope_mismatch"
        | "invalid_event_channel"
        | "ready_profile_mismatch";
      readonly message: string;
      readonly event_id?: string;
      readonly event_type?: string;
    };

export type VoiceLocalMicrophoneDiagnostic =
  | {
      readonly runtime: "livekit_v2";
      readonly kind: "publication";
      readonly observation: LocalMicrophonePublicationObservation;
    }
  | {
      readonly runtime: "pipecat_smallwebrtc_v1";
      readonly kind: "track";
      readonly observation: PipecatAudioTrackObservation;
    };

export interface VoiceTransportCallbacks {
  readonly isCurrent: () => boolean;
  readonly onConnected: () => void;
  readonly onDisconnected: () => void;
  readonly onAgentDisconnected: () => void;
  /** Milestone 1 treats reconnect as terminal and starts a new call identity. */
  readonly onFreshCallRequired: () => void;
  /** The exact immutable object accepted by the runtime-specific decoder. */
  readonly onEvent: (event: VoiceEvent) => void;
  readonly onInvalidEvent: (rejection: VoiceTransportEventRejection) => void;
  readonly onTransportError: (error: Error) => void;
  readonly onMicrophoneUnavailable: (error: Error) => void;
  readonly onAudioPlaybackBlockedChange: (blocked: boolean) => void;
  readonly onLocalMicrophoneTrack?: (track: MediaStreamTrack | null) => void;
  readonly onLocalMicrophoneDiagnostic?: (
    track: MediaStreamTrack,
    diagnostic: VoiceLocalMicrophoneDiagnostic,
  ) => void;
}

export interface VoiceTransport {
  readonly runtime: VoiceTransportRuntime;
  primeAudioPlayback(): void;
  connect(): Promise<void>;
  activateMicrophoneAfterReady(event: EventOf<"agent_ready">): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  setTtsEnabled(enabled: boolean): void;
  resumeAudio(): Promise<void>;
  disconnect(): Promise<void>;
}

export interface LoadVoiceTransportOptions {
  readonly ttsEnabled: boolean;
  readonly callbacks: VoiceTransportCallbacks;
  readonly authHeaderProvider?: VoiceAuthHeaderProvider;
}

export type VoiceTransportLoader = (
  assignment: VoiceSessionBootstrap,
  options: LoadVoiceTransportOptions,
) => Promise<VoiceTransport>;

interface LiveKitAdapterCallbacks {
  readonly isCurrent: () => boolean;
  readonly onConnected: () => void;
  readonly onReconnecting: (attempt: number) => void;
  readonly onReconnected: () => void;
  readonly onDisconnected: () => void;
  readonly onAgentDisconnected: () => void;
  readonly onTransportInput: (input: unknown) => void;
  readonly onInvalidEventChannel: () => void;
  readonly onMicrophoneUnavailable: (error: Error) => void;
  readonly onAudioPlaybackBlockedChange: (blocked: boolean) => void;
  readonly onLocalMicrophoneTrack?: (track: MediaStreamTrack | null) => void;
  readonly onLocalMicrophonePublication?: (
    track: MediaStreamTrack,
    observation: LocalMicrophonePublicationObservation,
  ) => void;
}

interface LiveKitAdapterOptions {
  readonly voiceCallId: string;
  readonly ttsEnabled: boolean;
  readonly callbacks: LiveKitAdapterCallbacks;
}

interface LiveKitAdapter {
  primeAudioPlayback(): void;
  connect(assignment: LiveKitVoiceSessionBootstrap): Promise<void>;
  activateMicrophoneAfterReady(): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  setTtsEnabled(enabled: boolean): void;
  resumeAudio(): Promise<void>;
  disconnect(): Promise<void>;
}

interface PipecatAdapterCallbacks {
  readonly isCurrent: () => boolean;
  readonly onConnected: () => void;
  readonly onDisconnected: () => void;
  readonly onAgentDisconnected: () => void;
  readonly onFreshCallRequired?: () => void;
  readonly onEvent: (event: VoiceEvent) => void;
  readonly onInvalidEvent: (rejection: PipecatEventRejection) => void;
  readonly onTransportError: (error: Error) => void;
  readonly onMicrophoneUnavailable: (error: Error) => void;
  readonly onAudioPlaybackBlockedChange: (blocked: boolean) => void;
  readonly onLocalMicrophoneTrack?: (
    track: MediaStreamTrack | null,
    observation: PipecatAudioTrackObservation | null,
  ) => void;
}

interface PipecatAdapterOptions {
  readonly callbacks: PipecatAdapterCallbacks;
  readonly outputEnabled?: boolean;
  readonly signalingPortFactory: (signalingUrl: string) => PipecatSignalingPort;
}

interface PipecatAdapter {
  primeAudioPlayback(): void;
  connect(connection: PipecatVoiceTransportConnection): Promise<void>;
  acceptCanonicalReady(event: EventOf<"agent_ready">): Promise<void>;
  setMicrophoneEnabled(enabled: boolean): Promise<void>;
  setTtsEnabled(enabled: boolean): void;
  resumeAudio(): Promise<void>;
  disconnect(): Promise<void>;
}

type AdapterConstructor<Options, Adapter> = new (options: Options) => Adapter;

type PipecatSignalingPortCreator = (
  signalingUrl: string,
  options?: PipecatSignalingPortOptions,
) => PipecatSignalingPort;

export interface VoiceTransportModuleImporters {
  readonly loadLiveKit: () => Promise<{
    readonly LiveKitVoiceTransport: AdapterConstructor<
      LiveKitAdapterOptions,
      LiveKitAdapter
    >;
  }>;
  readonly loadPipecat: () => Promise<{
    readonly PipecatVoiceTransport: AdapterConstructor<
      PipecatAdapterOptions,
      PipecatAdapter
    >;
    readonly createPipecatSignalingPort: PipecatSignalingPortCreator;
  }>;
}

const defaultImporters: VoiceTransportModuleImporters = Object.freeze({
  loadLiveKit: async () => {
    const { LiveKitVoiceTransport } = await import("./livekit-transport");
    return { LiveKitVoiceTransport };
  },
  loadPipecat: async () => {
    const [{ PipecatVoiceTransport }, { createPipecatSignalingPort }] =
      await Promise.all([
        import("./pipecat-transport"),
        import("./pipecat-signaling-api"),
      ]);
    return { PipecatVoiceTransport, createPipecatSignalingPort };
  },
});

interface BoundEventOwner {
  readonly assignment: VoiceSessionBootstrap;
  readonly callbacks: VoiceTransportCallbacks;
  readyEvent: EventOf<"agent_ready"> | null;
  readyFingerprint: string | null;
}

function notifySafely<Arguments extends unknown[]>(
  callback: ((...arguments_: Arguments) => void) | undefined,
  ...arguments_: Arguments
): void {
  try {
    callback?.(...arguments_);
  } catch {
    // Consumer diagnostics and state observers cannot corrupt media ownership.
  }
}

function isCurrent(callbacks: VoiceTransportCallbacks): boolean {
  try {
    return callbacks.isCurrent();
  } catch {
    return false;
  }
}

function eventRejection(
  code: Extract<
    VoiceTransportEventRejection,
    { readonly code: string }
  >["code"],
  message: string,
  event?: VoiceEvent,
): VoiceTransportEventRejection {
  return Object.freeze({
    code,
    message,
    ...(event
      ? { event_id: event.event_id, event_type: event.event_type }
      : {}),
  });
}

function deliverBoundEvent(owner: BoundEventOwner, event: VoiceEvent): void {
  if (!isCurrent(owner.callbacks)) return;
  const assignment = owner.assignment;
  if (
    event.trace_id !== assignment.trace_id ||
    event.session_id !== assignment.session_id ||
    event.voice_call_id !== assignment.voice_call_id
  ) {
    notifySafely(
      owner.callbacks.onInvalidEvent,
      eventRejection(
        "event_scope_mismatch",
        "Voice event does not match the assigned call",
        event,
      ),
    );
    return;
  }
  if (
    event.event_type === "agent_ready" &&
    event.payload.profile_id !== assignment.profile_id
  ) {
    notifySafely(
      owner.callbacks.onInvalidEvent,
      eventRejection(
        "ready_profile_mismatch",
        "Voice readiness does not match the assigned profile",
        event,
      ),
    );
    return;
  }

  if (event.event_type === "agent_ready") {
    const fingerprint = JSON.stringify(event);
    if (owner.readyEvent) {
      if (owner.readyFingerprint === fingerprint) return;
      notifySafely(
        owner.callbacks.onInvalidEvent,
        eventRejection(
          "conflicting_agent_ready",
          "Voice emitted more than one canonical Ready",
          event,
        ),
      );
      return;
    }
    owner.readyEvent = event;
    owner.readyFingerprint = fingerprint;
  }
  notifySafely(owner.callbacks.onEvent, event);
}

function decodeAndDeliverLiveKitEvent(
  owner: BoundEventOwner,
  input: unknown,
): void {
  const decoded = decodeVoiceEvent(input);
  if (!decoded.ok) {
    notifySafely(owner.callbacks.onInvalidEvent, decoded.error);
    return;
  }
  deliverBoundEvent(owner, decoded.event);
}

function createFreshCallSignal(
  callbacks: VoiceTransportCallbacks,
  disconnect: () => Promise<void>,
): (error?: Error) => void {
  let signaled = false;
  return (error) => {
    if (signaled) return;
    signaled = true;
    const closing = disconnect();
    void closing.catch(() => undefined);
    if (!isCurrent(callbacks)) return;
    if (error) notifySafely(callbacks.onTransportError, error);
    notifySafely(callbacks.onFreshCallRequired);
  };
}

function createCoalescedDisconnect(
  getAdapter: () => { disconnect(): Promise<void> },
): () => Promise<void> {
  let disconnectPromise: Promise<void> | null = null;
  return () => {
    if (disconnectPromise) return disconnectPromise;
    try {
      disconnectPromise = getAdapter().disconnect();
    } catch (error) {
      disconnectPromise = Promise.reject(error);
    }
    return disconnectPromise;
  };
}

function requireCanonicalReady(
  owner: BoundEventOwner,
  event: EventOf<"agent_ready">,
): void {
  if (event !== owner.readyEvent) {
    throw new Error(
      "Microphone activation requires this transport's canonical Ready",
    );
  }
}

function mapPipecatConnection(
  assignment: PipecatBrowserVoiceAssignment,
): PipecatVoiceTransportConnection {
  const iceServers = Object.freeze(
    assignment.ice_servers.map((server) => {
      const urls = [...server.urls];
      Object.freeze(urls);
      const snapshot: RTCIceServer & {
        readonly credentialType: "password";
      } = {
        urls,
        credentialType: server.credentialType,
        ...(server.username === null ? {} : { username: server.username }),
        ...(server.credential === null
          ? {}
          : { credential: server.credential }),
      };
      return Object.freeze(snapshot);
    }),
  );
  return Object.freeze({
    runtime: assignment.runtime,
    eventProtocol: assignment.event_protocol,
    webrtcUrl: assignment.webrtc_url,
    peerReservationId: assignment.peer_reservation_id,
    eventScope: Object.freeze({
      traceId: assignment.trace_id,
      voiceCallId: assignment.voice_call_id,
      sessionId: assignment.session_id,
      profileId: assignment.profile_id,
    }),
    iceServers,
  });
}

async function loadLiveKitTransport(
  assignment: LiveKitVoiceSessionBootstrap,
  options: LoadVoiceTransportOptions,
  importers: VoiceTransportModuleImporters,
): Promise<VoiceTransport> {
  const adapterModule = await importers.loadLiveKit();
  if (typeof adapterModule.LiveKitVoiceTransport !== "function") {
    throw new Error("LiveKit voice adapter is unavailable");
  }
  const eventOwner: BoundEventOwner = {
    assignment,
    callbacks: options.callbacks,
    readyEvent: null,
    readyFingerprint: null,
  };
  let adapter: LiveKitAdapter | null = null;
  const disconnect = createCoalescedDisconnect(() => {
    if (!adapter) throw new Error("LiveKit voice adapter is unavailable");
    return adapter;
  });
  const requireFreshCall = createFreshCallSignal(options.callbacks, disconnect);
  adapter = new adapterModule.LiveKitVoiceTransport({
    voiceCallId: assignment.voice_call_id,
    ttsEnabled: options.ttsEnabled,
    callbacks: {
      isCurrent: () => isCurrent(options.callbacks),
      onConnected: () => notifySafely(options.callbacks.onConnected),
      onReconnecting: () =>
        requireFreshCall(
          new Error("Voice transport reconnect requires a fresh call"),
        ),
      onReconnected: () =>
        requireFreshCall(
          new Error("Voice transport reconnect requires a fresh call"),
        ),
      onDisconnected: () => notifySafely(options.callbacks.onDisconnected),
      onAgentDisconnected: () =>
        notifySafely(options.callbacks.onAgentDisconnected),
      onTransportInput: (input) =>
        decodeAndDeliverLiveKitEvent(eventOwner, input),
      onInvalidEventChannel: () =>
        notifySafely(
          options.callbacks.onInvalidEvent,
          eventRejection(
            "invalid_event_channel",
            "Voice received data outside its authenticated event channel",
          ),
        ),
      onMicrophoneUnavailable: (error) =>
        notifySafely(options.callbacks.onMicrophoneUnavailable, error),
      onAudioPlaybackBlockedChange: (blocked) =>
        notifySafely(options.callbacks.onAudioPlaybackBlockedChange, blocked),
      onLocalMicrophoneTrack: (track) =>
        notifySafely(options.callbacks.onLocalMicrophoneTrack, track),
      onLocalMicrophonePublication: (track, observation) =>
        notifySafely(
          options.callbacks.onLocalMicrophoneDiagnostic,
          track,
          Object.freeze({
            runtime: "livekit_v2",
            kind: "publication",
            observation,
          }),
        ),
    },
  });
  let connectPromise: Promise<void> | null = null;

  return Object.freeze({
    runtime: assignment.runtime,
    primeAudioPlayback: () => adapter?.primeAudioPlayback(),
    connect: () => {
      if (connectPromise) return connectPromise;
      connectPromise = adapter!.connect(assignment);
      return connectPromise;
    },
    activateMicrophoneAfterReady: async (event: EventOf<"agent_ready">) => {
      requireCanonicalReady(eventOwner, event);
      await adapter!.activateMicrophoneAfterReady();
    },
    setMicrophoneEnabled: (enabled: boolean) =>
      adapter!.setMicrophoneEnabled(enabled),
    setTtsEnabled: (enabled: boolean) => adapter!.setTtsEnabled(enabled),
    resumeAudio: () => adapter!.resumeAudio(),
    disconnect,
  });
}

async function loadPipecatTransport(
  assignment: PipecatBrowserVoiceAssignment,
  options: LoadVoiceTransportOptions,
  importers: VoiceTransportModuleImporters,
): Promise<VoiceTransport> {
  const adapterModule = await importers.loadPipecat();
  if (
    typeof adapterModule.PipecatVoiceTransport !== "function" ||
    typeof adapterModule.createPipecatSignalingPort !== "function"
  ) {
    throw new Error("Pipecat voice adapter is unavailable");
  }
  const eventOwner: BoundEventOwner = {
    assignment,
    callbacks: options.callbacks,
    readyEvent: null,
    readyFingerprint: null,
  };
  let adapter: PipecatAdapter | null = null;
  const disconnect = createCoalescedDisconnect(() => {
    if (!adapter) throw new Error("Pipecat voice adapter is unavailable");
    return adapter;
  });
  const requireFreshCall = createFreshCallSignal(options.callbacks, disconnect);
  adapter = new adapterModule.PipecatVoiceTransport({
    outputEnabled: options.ttsEnabled,
    signalingPortFactory: (signalingUrl) =>
      adapterModule.createPipecatSignalingPort(signalingUrl, {
        authHeaderProvider: options.authHeaderProvider,
      }),
    callbacks: {
      isCurrent: () => isCurrent(options.callbacks),
      onConnected: () => notifySafely(options.callbacks.onConnected),
      onDisconnected: () => notifySafely(options.callbacks.onDisconnected),
      onAgentDisconnected: () =>
        notifySafely(options.callbacks.onAgentDisconnected),
      onFreshCallRequired: () => requireFreshCall(),
      onEvent: (event) => deliverBoundEvent(eventOwner, event),
      onInvalidEvent: (rejection) =>
        notifySafely(options.callbacks.onInvalidEvent, rejection),
      onTransportError: (error) =>
        notifySafely(options.callbacks.onTransportError, error),
      onMicrophoneUnavailable: (error) =>
        notifySafely(options.callbacks.onMicrophoneUnavailable, error),
      onAudioPlaybackBlockedChange: (blocked) =>
        notifySafely(options.callbacks.onAudioPlaybackBlockedChange, blocked),
      onLocalMicrophoneTrack: (track, observation) => {
        notifySafely(options.callbacks.onLocalMicrophoneTrack, track);
        if (!track || !observation) return;
        notifySafely(
          options.callbacks.onLocalMicrophoneDiagnostic,
          track,
          Object.freeze({
            runtime: "pipecat_smallwebrtc_v1",
            kind: "track",
            observation,
          }),
        );
      },
    },
  });
  const connection = mapPipecatConnection(assignment);
  let connectPromise: Promise<void> | null = null;

  return Object.freeze({
    runtime: assignment.runtime,
    primeAudioPlayback: () => adapter?.primeAudioPlayback(),
    connect: () => {
      if (connectPromise) return connectPromise;
      connectPromise = adapter!.connect(connection);
      return connectPromise;
    },
    activateMicrophoneAfterReady: async (event: EventOf<"agent_ready">) => {
      requireCanonicalReady(eventOwner, event);
      await adapter!.acceptCanonicalReady(event);
    },
    setMicrophoneEnabled: (enabled: boolean) =>
      adapter!.setMicrophoneEnabled(enabled),
    setTtsEnabled: (enabled: boolean) => adapter!.setTtsEnabled(enabled),
    resumeAudio: () => adapter!.resumeAudio(),
    disconnect,
  });
}

function unsupportedRuntime(assignment: never): never {
  const runtime = (assignment as { readonly runtime?: unknown }).runtime;
  throw new Error(
    typeof runtime === "string"
      ? "Voice assignment selected an unsupported runtime"
      : "Voice assignment is invalid",
  );
}

export function createVoiceTransportLoader(
  importers: VoiceTransportModuleImporters = defaultImporters,
): VoiceTransportLoader {
  const ownedImporters = Object.freeze({ ...importers });
  return async (assignment, options) => {
    switch (assignment.runtime) {
      case "livekit_v2":
        return loadLiveKitTransport(assignment, options, ownedImporters);
      case "pipecat_smallwebrtc_v1":
        return loadPipecatTransport(assignment, options, ownedImporters);
      default:
        return unsupportedRuntime(assignment);
    }
  };
}

export const loadVoiceTransport: VoiceTransportLoader =
  createVoiceTransportLoader();
