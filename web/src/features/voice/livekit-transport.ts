import {
  createLocalAudioTrack,
  DataPacket_Kind,
  Room,
  RoomEvent,
  Track,
  isAudioTrack,
  type LocalAudioTrack,
  type LocalTrackPublication,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from "livekit-client";

import type { VoiceSessionBootstrap } from "./session-api";

/**
 * Immutable diagnostic snapshot captured after the exact microphone track is
 * published and before it can be activated by canonical agent readiness.
 *
 * `MediaStreamTrack.muted` is intentionally absent: that browser property
 * reports source availability, not application-controlled mute. The two valid
 * application signals are the native track's `enabled` flag and LiveKit's
 * local-track mute state.
 */
export interface LocalMicrophonePublicationObservation {
  readonly trackId: string;
  readonly observedAtMs: number;
  readonly mediaStreamTrackEnabled: boolean;
  readonly livekitMuted: boolean;
  readonly readyState: MediaStreamTrackState;
}

interface LiveKitVoiceTransportCallbacks {
  /** Generation guard owned by the session orchestrator. */
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
    observation: LocalMicrophonePublicationObservation
  ) => void;
}

interface LiveKitVoiceTransportOptions {
  readonly voiceCallId: string;
  readonly ttsEnabled: boolean;
  readonly callbacks: LiveKitVoiceTransportCallbacks;
}

interface Deferred<T> {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
  readonly reject: (error: Error) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  // Readiness may fail before Ready is observed. Keep that expected teardown
  // path handled while preserving rejection for a later activation waiter.
  void promise.catch(() => undefined);
  return { promise, resolve, reject };
}

/**
 * Owns one LiveKit Room and all browser media resources created for that room.
 * Session transitions and event reduction deliberately remain in the hook.
 */
export class LiveKitVoiceTransport {
  private readonly room: Room;
  private readonly remoteAudioTracks = new Set<RemoteAudioTrack>();
  private readonly audioElements = new Set<HTMLMediaElement>();
  private listenerCleanups: readonly (() => void)[] = [];
  private reconnectAttempt = 0;
  private ttsEnabled: boolean;
  private closed = false;
  private localAudioTrack: LocalAudioTrack | null = null;
  private microphonePublication: LocalTrackPublication | null = null;
  private readonly microphonePublicationReady = deferred<LocalTrackPublication>();
  private microphoneActivationPromise: Promise<void> | null = null;
  private microphoneActivatedAfterReady = false;
  private localMicrophoneTrack: MediaStreamTrack | null = null;

  constructor(private readonly options: LiveKitVoiceTransportOptions) {
    this.ttsEnabled = options.ttsEnabled;
    this.room = new Room({
      adaptiveStream: false,
      dynacast: false,
      disconnectOnPageLeave: true,
      stopLocalTrackOnUnpublish: true,
    });
  }

  /** Invoke during the click turn, before bootstrap, to satisfy autoplay policy. */
  primeAudioPlayback(): void {
    void this.room.startAudio().catch(() => {
      if (this.isCurrent()) {
        this.options.callbacks.onAudioPlaybackBlockedChange(true);
      }
    });
  }

  async connect(assignment: VoiceSessionBootstrap): Promise<void> {
    if (this.closed) throw new Error("Voice transport is already closed");
    this.attachListeners(assignment);
    let localAudioTrack: LocalAudioTrack | null = null;
    try {
      await this.room.connect(assignment.server_url, assignment.participant_token, {
        autoSubscribe: true,
      });
      if (!this.isCurrent()) {
        this.microphonePublicationReady.reject(
          new Error("Voice transport closed before microphone capture")
        );
        return;
      }

      localAudioTrack = await createLocalAudioTrack({
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      this.localAudioTrack = localAudioTrack;

      // Capture must remain alive so the worker can subscribe, but no voiced frame
      // may leave the browser before the authenticated agent_ready event. Muting
      // before publish makes that ordering an invariant rather than a timing hope.
      await localAudioTrack.mute();
      if (!this.isCurrent()) {
        localAudioTrack.stop();
        this.localAudioTrack = null;
        this.microphonePublicationReady.reject(
          new Error("Voice transport closed before microphone publication")
        );
        return;
      }

      this.microphonePublication =
        await this.room.localParticipant.publishTrack(localAudioTrack, {
          source: Track.Source.Microphone,
          stopMicTrackOnMute: false,
        });
      if (!this.isCurrent()) {
        localAudioTrack.stop();
        this.localAudioTrack = null;
        this.microphonePublication = null;
        this.microphonePublicationReady.reject(
          new Error("Voice transport closed during microphone publication")
        );
        return;
      }
      this.microphonePublicationReady.resolve(this.microphonePublication);
      const mediaStreamTrack = localAudioTrack.mediaStreamTrack;
      const publicationObservation = Object.freeze({
        trackId: mediaStreamTrack.id,
        observedAtMs: performance.now(),
        mediaStreamTrackEnabled: mediaStreamTrack.enabled,
        livekitMuted: this.microphonePublication.isMuted,
        readyState: mediaStreamTrack.readyState,
      } satisfies LocalMicrophonePublicationObservation);
      this.reportLocalMicrophoneTrack(mediaStreamTrack);
      this.reportLocalMicrophonePublication(
        mediaStreamTrack,
        publicationObservation
      );
      this.options.callbacks.onAudioPlaybackBlockedChange(
        !this.room.canPlaybackAudio
      );
    } catch (error) {
      localAudioTrack?.stop();
      this.localAudioTrack = null;
      this.microphonePublication = null;
      const failure =
        error instanceof Error ? error : new Error("Microphone setup failed");
      this.microphonePublicationReady.reject(failure);
      throw error;
    }
  }

  /**
   * Opens the already-published microphone only after canonical readiness.
   * Concurrent or duplicate Ready observations share one public SDK unmute.
   */
  activateMicrophoneAfterReady(): Promise<void> {
    if (this.closed) {
      return Promise.reject(new Error("Voice transport is already closed"));
    }
    if (this.microphoneActivatedAfterReady) return Promise.resolve();
    if (this.microphoneActivationPromise) return this.microphoneActivationPromise;

    const activation = (async () => {
      const publication = await this.microphonePublicationReady.promise;
      if (!this.isCurrent() || !this.localAudioTrack) {
        throw new Error("Voice transport became stale before microphone activation");
      }
      await publication.unmute();
      if (!this.isCurrent()) {
        await publication.mute().catch(() => undefined);
        throw new Error("Voice transport became stale during microphone activation");
      }
      this.microphoneActivatedAfterReady = true;
    })();
    this.microphoneActivationPromise = activation;
    return activation;
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    if (this.closed) return;
    const publication = this.microphonePublication;
    if (!publication || !this.microphoneActivatedAfterReady) {
      throw new Error("Microphone cannot be controlled before agent readiness");
    }
    if (enabled) await publication.unmute();
    else await publication.mute();
    if (!this.isCurrent()) return;
  }

  setTtsEnabled(enabled: boolean): void {
    this.ttsEnabled = enabled;
    for (const track of this.remoteAudioTracks) {
      track.setVolume(enabled ? 1 : 0);
    }
    if (!enabled || this.closed) return;

    void this.room.startAudio().then(
      () => {
        if (this.isCurrent()) {
          this.options.callbacks.onAudioPlaybackBlockedChange(false);
        }
      },
      () => {
        if (this.isCurrent()) {
          this.options.callbacks.onAudioPlaybackBlockedChange(true);
        }
      }
    );
  }

  async resumeAudio(): Promise<void> {
    if (this.closed) return;
    await this.room.startAudio();
    if (this.isCurrent()) {
      this.options.callbacks.onAudioPlaybackBlockedChange(false);
    }
  }

  disconnect(): Promise<void> {
    if (this.closed) return Promise.resolve();
    this.closed = true;

    const cleanups = this.listenerCleanups;
    this.listenerCleanups = [];
    for (const cleanup of cleanups) cleanup();

    for (const track of this.remoteAudioTracks) this.detachAudioTrack(track);
    this.remoteAudioTracks.clear();
    for (const element of this.audioElements) element.remove();
    this.audioElements.clear();
    const localAudioTrack = this.localAudioTrack;
    this.localAudioTrack = null;
    this.microphonePublication = null;
    this.microphoneActivationPromise = null;
    this.microphonePublicationReady.reject(
      new Error("Voice transport closed before microphone activation")
    );
    localAudioTrack?.stop();
    this.reportLocalMicrophoneTrack(null);

    return this.room.disconnect(true).catch(() => {
      // Teardown is idempotent; a concurrently closed room needs no recovery.
    });
  }

  private isCurrent(): boolean {
    return !this.closed && this.options.callbacks.isCurrent();
  }

  private attachListeners(assignment: VoiceSessionBootstrap): void {
    if (this.listenerCleanups.length > 0) {
      throw new Error("Voice transport listeners are already attached");
    }

    const onConnected = () => {
      if (!this.isCurrent()) return;
      this.reconnectAttempt = 0;
      this.options.callbacks.onConnected();
    };
    const onReconnecting = () => {
      if (!this.isCurrent()) return;
      this.reconnectAttempt += 1;
      this.options.callbacks.onReconnecting(this.reconnectAttempt);
    };
    const onReconnected = () => {
      if (this.isCurrent()) this.options.callbacks.onReconnected();
    };
    const onDisconnected = () => {
      if (this.isCurrent()) this.options.callbacks.onDisconnected();
    };
    const onParticipantDisconnected = (participant: RemoteParticipant) => {
      if (
        this.isCurrent() &&
        participant.isAgent === true &&
        participant.identity === assignment.agent_participant_identity
      ) {
        this.options.callbacks.onAgentDisconnected();
      }
    };
    const onDataReceived = (
      payload: Uint8Array,
      participant?: RemoteParticipant,
      kind?: DataPacket_Kind,
      topic?: string
    ) => {
      if (!this.isCurrent()) return;
      if (
        topic !== assignment.event_topic ||
        kind !== DataPacket_Kind.RELIABLE ||
        participant?.isAgent !== true ||
        participant.identity !== assignment.agent_participant_identity
      ) {
        this.options.callbacks.onInvalidEventChannel();
        return;
      }

      let input: unknown;
      try {
        input = JSON.parse(
          new TextDecoder("utf-8", { fatal: true }).decode(payload)
        );
      } catch {
        input = null;
      }
      this.options.callbacks.onTransportInput(input);
    };
    const onTrackSubscribed = (
      track: RemoteTrack,
      _publication: RemoteTrackPublication,
      participant: RemoteParticipant
    ) => {
      if (
        !this.isCurrent() ||
        participant.isAgent !== true ||
        participant.identity !== assignment.agent_participant_identity ||
        !isAudioTrack(track) ||
        track.isLocal
      ) {
        return;
      }

      const audioTrack = track as RemoteAudioTrack;
      audioTrack.setVolume(this.ttsEnabled ? 1 : 0);
      const element = audioTrack.attach();
      element.autoplay = true;
      element.setAttribute("data-murmur-voice-call", this.options.voiceCallId);
      element.className = "hidden";
      document.body.appendChild(element);
      this.remoteAudioTracks.add(audioTrack);
      this.audioElements.add(element);
    };
    const onTrackUnsubscribed = (track: RemoteTrack) => {
      if (this.isCurrent() && isAudioTrack(track) && !track.isLocal) {
        this.detachAudioTrack(track as RemoteAudioTrack);
      }
    };
    const onMediaDevicesError = (error: Error, kind?: MediaDeviceKind) => {
      if (!this.isCurrent() || (kind && kind !== "audioinput")) return;
      this.options.callbacks.onMicrophoneUnavailable(error);
    };
    const onAudioPlaybackChanged = (playing: boolean) => {
      if (this.isCurrent()) {
        this.options.callbacks.onAudioPlaybackBlockedChange(!playing);
      }
    };

    this.room.on(RoomEvent.Connected, onConnected);
    this.room.on(RoomEvent.Reconnecting, onReconnecting);
    this.room.on(RoomEvent.Reconnected, onReconnected);
    this.room.on(RoomEvent.Disconnected, onDisconnected);
    this.room.on(RoomEvent.ParticipantDisconnected, onParticipantDisconnected);
    this.room.on(RoomEvent.DataReceived, onDataReceived);
    this.room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    this.room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
    this.room.on(RoomEvent.MediaDevicesError, onMediaDevicesError);
    this.room.on(RoomEvent.AudioPlaybackStatusChanged, onAudioPlaybackChanged);
    this.listenerCleanups = [
      () => this.room.off(RoomEvent.Connected, onConnected),
      () => this.room.off(RoomEvent.Reconnecting, onReconnecting),
      () => this.room.off(RoomEvent.Reconnected, onReconnected),
      () => this.room.off(RoomEvent.Disconnected, onDisconnected),
      () =>
        this.room.off(
          RoomEvent.ParticipantDisconnected,
          onParticipantDisconnected
        ),
      () => this.room.off(RoomEvent.DataReceived, onDataReceived),
      () => this.room.off(RoomEvent.TrackSubscribed, onTrackSubscribed),
      () => this.room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed),
      () => this.room.off(RoomEvent.MediaDevicesError, onMediaDevicesError),
      () =>
        this.room.off(
          RoomEvent.AudioPlaybackStatusChanged,
          onAudioPlaybackChanged
        ),
    ];
  }

  private detachAudioTrack(track: RemoteAudioTrack): void {
    this.remoteAudioTracks.delete(track);
    for (const element of track.detach()) {
      this.audioElements.delete(element);
      element.remove();
    }
  }

  private reportLocalMicrophoneTrack(track: MediaStreamTrack | null): void {
    if (this.localMicrophoneTrack === track) return;
    this.localMicrophoneTrack = track;
    try {
      this.options.callbacks.onLocalMicrophoneTrack?.(track);
    } catch {
      // Diagnostics must never change production capture or teardown.
    }
  }

  private reportLocalMicrophonePublication(
    track: MediaStreamTrack,
    observation: LocalMicrophonePublicationObservation
  ): void {
    try {
      this.options.callbacks.onLocalMicrophonePublication?.(track, observation);
    } catch {
      // Diagnostics must never change production capture or readiness.
    }
  }
}
