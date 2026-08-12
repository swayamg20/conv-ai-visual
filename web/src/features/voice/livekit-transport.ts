import {
  DataPacket_Kind,
  Room,
  RoomEvent,
  isAudioTrack,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from "livekit-client";

import type { VoiceSessionBootstrap } from "./session-api";

interface LiveKitVoiceTransportCallbacks {
  /** Generation guard owned by the session orchestrator. */
  readonly isCurrent: () => boolean;
  readonly onConnected: () => void;
  readonly onReconnecting: (attempt: number) => void;
  readonly onReconnected: () => void;
  readonly onDisconnected: () => void;
  readonly onTransportInput: (input: unknown) => void;
  readonly onInvalidEventChannel: () => void;
  readonly onMicrophoneUnavailable: (error: Error) => void;
  readonly onAudioPlaybackBlockedChange: (blocked: boolean) => void;
}

interface LiveKitVoiceTransportOptions {
  readonly voiceCallId: string;
  readonly ttsEnabled: boolean;
  readonly callbacks: LiveKitVoiceTransportCallbacks;
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
    await this.room.connect(assignment.server_url, assignment.participant_token, {
      autoSubscribe: true,
    });
    if (!this.isCurrent()) return;

    await this.room.localParticipant.setMicrophoneEnabled(true, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    if (!this.isCurrent()) return;
    this.options.callbacks.onAudioPlaybackBlockedChange(
      !this.room.canPlaybackAudio
    );
  }

  async setMicrophoneEnabled(enabled: boolean): Promise<void> {
    if (this.closed) return;
    await this.room.localParticipant.setMicrophoneEnabled(enabled);
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

    void this.room.localParticipant.setMicrophoneEnabled(false).catch(() => {
      // Disconnect remains authoritative if the capture track already ended.
    });
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
}
