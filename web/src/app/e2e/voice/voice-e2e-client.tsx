"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { VoiceEvent } from "@/features/voice/events";
import type { LocalMicrophonePublicationObservation } from "@/features/voice/livekit-transport";
import { useVoiceSession } from "@/hooks/use-voice-session";

const SAMPLE_INTERVAL_MS = 16;
const SNAPSHOT_INTERVAL_MS = 80;

interface VoiceE2EClientProps {
  readonly agentId: string;
  readonly apiUrl: string;
  readonly sessionId: string;
}

interface AudioSample {
  readonly t_ms: number;
  readonly rms: number;
}

interface ObservedEvent {
  readonly t_ms: number;
  readonly event: VoiceEvent;
}

interface TrackEvidence {
  readonly id: string;
  readonly kind: string;
  readonly label: string;
  readonly ready_state: MediaStreamTrackState;
}

interface MicrophonePublicationEvidence {
  readonly exact_track_id: string;
  readonly observed_at_ms: number;
  readonly media_stream_track_enabled: boolean;
  readonly livekit_muted: boolean;
  readonly ready_state: MediaStreamTrackState;
}

interface HookState {
  readonly assignmentPresent: boolean;
  readonly phase: string;
  readonly voiceCallId: string;
}

interface Probe {
  readonly analyser: AnalyserNode;
  readonly source: AudioNode;
}

interface RemoteProbe extends Probe {
  readonly element: HTMLMediaElement;
  readonly sink: GainNode;
}

interface AssignmentEvidence {
  readonly trace_id: string;
  readonly voice_call_id: string;
  readonly session_id: string;
  readonly agent_id: string;
  readonly room_name: string;
  readonly dispatch_id: string;
  readonly profile_id: string;
  readonly worker_name: string;
}

interface VoiceE2ESnapshot {
  readonly schema_version: 1;
  readonly status:
    | "idle"
    | "connecting"
    | "observing"
    | "disconnecting"
    | "disconnected"
    | "error";
  readonly phase: string;
  readonly voice_call_id: string;
  readonly assignment: AssignmentEvidence | null;
  readonly local_track: TrackEvidence | null;
  readonly microphone_publication: MicrophonePublicationEvidence | null;
  readonly local_track_released: boolean;
  readonly remote_audio_element_attached: boolean;
  readonly remote_audio_element_count: number;
  readonly local_samples: readonly AudioSample[];
  readonly remote_samples: readonly AudioSample[];
  readonly events: readonly ObservedEvent[];
  readonly errors: readonly string[];
  readonly logs: readonly string[];
  readonly disconnect_requested: boolean;
  readonly hook_assignment_cleared: boolean;
}

const INITIAL_SNAPSHOT: VoiceE2ESnapshot = {
  schema_version: 1,
  status: "idle",
  phase: "idle",
  voice_call_id: "",
  assignment: null,
  local_track: null,
  microphone_publication: null,
  local_track_released: false,
  remote_audio_element_attached: false,
  remote_audio_element_count: 0,
  local_samples: [],
  remote_samples: [],
  events: [],
  errors: [],
  logs: [],
  disconnect_requested: false,
  hook_assignment_cleared: true,
};

function rms(analyser: AnalyserNode, buffer: Float32Array<ArrayBuffer>): number {
  analyser.getFloatTimeDomainData(buffer);
  let squared = 0;
  for (const sample of buffer) squared += sample * sample;
  return Math.sqrt(squared / buffer.length);
}

function rounded(value: number, places = 6): number {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
}

function createAnalyser(context: AudioContext): AnalyserNode {
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0;
  return analyser;
}

export function VoiceE2EClient({
  agentId,
  apiUrl,
  sessionId,
}: VoiceE2EClientProps) {
  const measurementOriginRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const localProbeRef = useRef<Probe | null>(null);
  const remoteProbeRef = useRef<RemoteProbe | null>(null);
  const localBufferRef = useRef<Float32Array<ArrayBuffer> | null>(null);
  const remoteBufferRef = useRef<Float32Array<ArrayBuffer> | null>(null);
  const samplerTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const snapshotTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const localSamplesRef = useRef<AudioSample[]>([]);
  const remoteSamplesRef = useRef<AudioSample[]>([]);
  const eventsRef = useRef<ObservedEvent[]>([]);
  const errorsRef = useRef<string[]>([]);
  const logsRef = useRef<string[]>([]);
  const assignmentEvidenceRef = useRef<AssignmentEvidence | null>(null);
  const localTrackEvidenceRef = useRef<TrackEvidence | null>(null);
  const microphonePublicationEvidenceRef =
    useRef<MicrophonePublicationEvidence | null>(null);
  const localTrackRef = useRef<MediaStreamTrack | null>(null);
  const localTrackReleasedRef = useRef(false);
  const remoteAttachedRef = useRef(false);
  const consumedRemoteElementsRef = useRef(new WeakSet<HTMLMediaElement>());
  const disconnectRequestedRef = useRef(false);
  const statusRef = useRef<VoiceE2ESnapshot["status"]>("idle");
  const hookStateRef = useRef<HookState>({
    assignmentPresent: false,
    phase: "idle",
    voiceCallId: "",
  });

  const elapsed = useCallback(() => {
    const origin = measurementOriginRef.current;
    return rounded(origin === null ? 0 : performance.now() - origin, 3);
  }, []);

  const e2eAuthHeader = useCallback(
    async () => ({ Authorization: "Bearer voice-e2e" }),
    []
  );

  const observeEvent = useCallback(
    (event: VoiceEvent) => {
      eventsRef.current.push({ t_ms: elapsed(), event });
    },
    [elapsed]
  );
  const observeError = useCallback((message: string) => {
    errorsRef.current.push(message);
    statusRef.current = "error";
  }, []);
  const observeLog = useCallback((message: string) => {
    logsRef.current.push(message);
  }, []);

  const ensureAudioContext = useCallback(async (): Promise<AudioContext> => {
    let context = audioContextRef.current;
    if (!context) {
      context = new AudioContext({ latencyHint: "interactive" });
      audioContextRef.current = context;
    }
    if (context.state === "suspended") await context.resume();
    return context;
  }, []);

  const releaseLocalProbe = useCallback(() => {
    localProbeRef.current?.source.disconnect();
    localProbeRef.current = null;
    localBufferRef.current = null;
  }, []);

  const observeLocalTrack = useCallback(
    (track: MediaStreamTrack | null) => {
      releaseLocalProbe();
      if (!track) {
        const publishedTrack = localTrackRef.current;
        localTrackReleasedRef.current = publishedTrack?.readyState === "ended";
        return;
      }

      localTrackRef.current = track;
      localTrackReleasedRef.current = false;
      localTrackEvidenceRef.current = {
        id: track.id,
        kind: track.kind,
        label: track.label,
        ready_state: track.readyState,
      };
      const context = audioContextRef.current;
      if (!context) {
        observeError("The exact published microphone track arrived before audio setup");
        return;
      }
      const analyser = createAnalyser(context);
      const source = context.createMediaStreamSource(new MediaStream([track]));
      source.connect(analyser);
      localProbeRef.current = { analyser, source };
      localBufferRef.current = new Float32Array(analyser.fftSize);
    },
    [observeError, releaseLocalProbe]
  );

  const observeMicrophonePublication = useCallback(
    (
      track: MediaStreamTrack,
      observation: LocalMicrophonePublicationObservation
    ) => {
      const origin = measurementOriginRef.current;
      if (origin === null) {
        observeError("Microphone publication was observed before measurement setup");
        return;
      }
      if (localTrackRef.current !== track || observation.trackId !== track.id) {
        observeError("Microphone publication did not reference the exact observed track");
        return;
      }
      const observedAtMs = rounded(observation.observedAtMs - origin, 3);
      if (!Number.isFinite(observedAtMs) || observedAtMs < 0) {
        observeError("Microphone publication observation timestamp was invalid");
        return;
      }
      microphonePublicationEvidenceRef.current = Object.freeze({
        exact_track_id: observation.trackId,
        observed_at_ms: observedAtMs,
        media_stream_track_enabled: observation.mediaStreamTrackEnabled,
        livekit_muted: observation.livekitMuted,
        ready_state: observation.readyState,
      });
    },
    [observeError]
  );

  const voice = useVoiceSession({
    enabled: true,
    apiUrl,
    agentId,
    sessionId,
    authHeaderProvider: e2eAuthHeader,
    onEvent: observeEvent,
    onError: observeError,
    onLog: observeLog,
    onLocalMicrophoneTrack: observeLocalTrack,
    onLocalMicrophonePublication: observeMicrophonePublication,
  });
  const voiceAssignment = voice.assignment;
  const voiceCallId = voice.voiceCallId;
  const voicePhase = voice.phase;
  const cancelVoiceConnection = voice.cancelConnection;

  const attachRemoteElement = useCallback(async () => {
    if (remoteProbeRef.current) return;
    const element = Array.from(
      document.querySelectorAll<HTMLMediaElement>("audio[data-murmur-voice-call]")
    ).find((candidate) => !consumedRemoteElementsRef.current.has(candidate));
    if (!element) return;

    const assignment = assignmentEvidenceRef.current;
    if (!assignment) return;
    if (element.dataset.murmurVoiceCall !== assignment.voice_call_id) {
      observeError("Remote audio element does not match the accepted assignment");
      return;
    }

    const context = await ensureAudioContext();
    const analyser = createAnalyser(context);
    const stream = element.srcObject;
    if (!(stream instanceof MediaStream)) {
      observeError("Remote audio element has no MediaStream source");
      return;
    }
    const remoteTrack = stream.getAudioTracks()[0];
    if (!remoteTrack || remoteTrack.readyState !== "live") {
      observeError("Remote audio element has no live decoded audio track");
      return;
    }
    const source = context.createMediaStreamSource(
      new MediaStream([remoteTrack])
    );
    const sink = context.createGain();
    sink.gain.value = 0;
    source.connect(analyser);
    analyser.connect(sink);
    sink.connect(context.destination);
    consumedRemoteElementsRef.current.add(element);
    remoteProbeRef.current = { analyser, source, element, sink };
    remoteBufferRef.current = new Float32Array(analyser.fftSize);
    remoteAttachedRef.current = true;
    await element.play();
  }, [ensureAudioContext, observeError]);

  const releaseRemoteProbe = useCallback(() => {
    const probe = remoteProbeRef.current;
    if (!probe) return;
    probe.source.disconnect();
    probe.analyser.disconnect();
    probe.sink.disconnect();
    remoteProbeRef.current = null;
    remoteBufferRef.current = null;
  }, []);

  const sampleAudio = useCallback(() => {
    void attachRemoteElement().catch((error: unknown) => {
      observeError(
        error instanceof Error ? error.message : "Could not analyse remote audio"
      );
    });

    const now = elapsed();
    const publishedTrack = localTrackRef.current;
    if (publishedTrack?.readyState === "ended") {
      localTrackReleasedRef.current = true;
    }
    const localProbe = localProbeRef.current;
    const localBuffer = localBufferRef.current;
    if (localProbe && localBuffer) {
      localSamplesRef.current.push({
        t_ms: now,
        rms: rounded(rms(localProbe.analyser, localBuffer)),
      });
    }
    const remoteProbe = remoteProbeRef.current;
    const remoteBuffer = remoteBufferRef.current;
    if (remoteProbe && remoteBuffer) {
      remoteSamplesRef.current.push({
        t_ms: now,
        rms: rounded(rms(remoteProbe.analyser, remoteBuffer)),
      });
      if (!remoteProbe.element.isConnected) releaseRemoteProbe();
    }
  }, [attachRemoteElement, elapsed, observeError, releaseRemoteProbe]);

  const buildSnapshot = useCallback((): VoiceE2ESnapshot => {
    const hook = hookStateRef.current;
    const remoteElementCount = document.querySelectorAll(
      "audio[data-murmur-voice-call]"
    ).length;
    const disconnected =
      disconnectRequestedRef.current &&
      localTrackReleasedRef.current &&
      remoteElementCount === 0 &&
      !hook.assignmentPresent;
    if (disconnected) statusRef.current = "disconnected";

    return {
      schema_version: 1,
      status: statusRef.current,
      phase: hook.phase,
      voice_call_id:
        assignmentEvidenceRef.current?.voice_call_id ?? hook.voiceCallId,
      assignment: assignmentEvidenceRef.current,
      local_track: localTrackEvidenceRef.current,
      microphone_publication: microphonePublicationEvidenceRef.current,
      local_track_released: localTrackReleasedRef.current,
      remote_audio_element_attached: remoteAttachedRef.current,
      remote_audio_element_count: remoteElementCount,
      local_samples: [...localSamplesRef.current],
      remote_samples: [...remoteSamplesRef.current],
      events: [...eventsRef.current],
      errors: [...errorsRef.current],
      logs: [...logsRef.current],
      disconnect_requested: disconnectRequestedRef.current,
      hook_assignment_cleared: !hook.assignmentPresent,
    };
  }, []);

  const [snapshot, setSnapshot] =
    useState<VoiceE2ESnapshot>(INITIAL_SNAPSHOT);

  const stopTimers = useCallback(() => {
    if (samplerTimerRef.current !== null) {
      clearInterval(samplerTimerRef.current);
      samplerTimerRef.current = null;
    }
    if (snapshotTimerRef.current !== null) {
      clearInterval(snapshotTimerRef.current);
      snapshotTimerRef.current = null;
    }
  }, []);

  const startTimers = useCallback(() => {
    stopTimers();
    samplerTimerRef.current = setInterval(sampleAudio, SAMPLE_INTERVAL_MS);
    snapshotTimerRef.current = setInterval(() => {
      setSnapshot(buildSnapshot());
    }, SNAPSHOT_INTERVAL_MS);
  }, [buildSnapshot, sampleAudio, stopTimers]);

  const start = useCallback(async () => {
    measurementOriginRef.current = performance.now();
    localSamplesRef.current = [];
    remoteSamplesRef.current = [];
    eventsRef.current = [];
    errorsRef.current = [];
    logsRef.current = [];
    assignmentEvidenceRef.current = null;
    localTrackEvidenceRef.current = null;
    microphonePublicationEvidenceRef.current = null;
    localTrackRef.current = null;
    localTrackReleasedRef.current = false;
    remoteAttachedRef.current = false;
    disconnectRequestedRef.current = false;
    statusRef.current = "connecting";
    await ensureAudioContext();
    startTimers();
    setSnapshot(buildSnapshot());
    await voice.connect();
    if (errorsRef.current.length === 0) statusRef.current = "observing";
    setSnapshot(buildSnapshot());
  }, [buildSnapshot, ensureAudioContext, startTimers, voice]);

  const end = useCallback(() => {
    disconnectRequestedRef.current = true;
    statusRef.current = "disconnecting";
    voice.disconnect();
    setSnapshot(buildSnapshot());
  }, [buildSnapshot, voice]);

  useEffect(() => {
    hookStateRef.current = {
      assignmentPresent: voiceAssignment !== null,
      phase: voicePhase,
      voiceCallId,
    };
    if (voiceAssignment && voiceAssignment.runtime !== "livekit_v2") {
      assignmentEvidenceRef.current = null;
      observeError("The isolated LiveKit RTC proof received a different runtime");
      cancelVoiceConnection();
      return;
    }
    if (voiceAssignment) {
      assignmentEvidenceRef.current = {
        trace_id: voiceAssignment.trace_id,
        voice_call_id: voiceAssignment.voice_call_id,
        session_id: voiceAssignment.session_id,
        agent_id: voiceAssignment.agent_id,
        room_name: voiceAssignment.room_name,
        dispatch_id: voiceAssignment.dispatch_id,
        profile_id: voiceAssignment.profile_id,
        worker_name: voiceAssignment.worker_name,
      };
    }
  }, [
    cancelVoiceConnection,
    observeError,
    voiceAssignment,
    voiceCallId,
    voicePhase,
  ]);

  useEffect(() => {
    if (snapshot.status !== "disconnected") return;
    stopTimers();
    releaseLocalProbe();
    releaseRemoteProbe();
    void audioContextRef.current?.close();
  }, [releaseLocalProbe, releaseRemoteProbe, snapshot.status, stopTimers]);

  useEffect(
    () => () => {
      stopTimers();
      releaseLocalProbe();
      releaseRemoteProbe();
      void audioContextRef.current?.close();
    },
    [releaseLocalProbe, releaseRemoteProbe, stopTimers]
  );

  return (
    <main className="min-h-screen bg-zinc-950 p-8 text-zinc-100">
      <section className="mx-auto max-w-3xl space-y-5">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-emerald-400">
          Murmur Voice V2 isolated RTC proof
        </p>
        <h1 className="text-2xl font-semibold">Browser media harness</h1>
        <p data-testid="voice-e2e-status" className="font-mono text-sm">
          {snapshot.status} / {snapshot.phase}
        </p>
        <div className="flex gap-3">
          <button
            type="button"
            data-testid="voice-e2e-start"
            disabled={snapshot.status !== "idle"}
            onClick={() => void start()}
            className="rounded bg-emerald-500 px-4 py-2 font-medium text-zinc-950 disabled:opacity-40"
          >
            Start RTC proof
          </button>
          <button
            type="button"
            data-testid="voice-e2e-end"
            disabled={
              snapshot.status === "idle" ||
              snapshot.status === "disconnecting" ||
              snapshot.status === "disconnected"
            }
            onClick={end}
            className="rounded border border-zinc-600 px-4 py-2 disabled:opacity-40"
          >
            Disconnect
          </button>
        </div>
        <pre
          data-testid="voice-e2e-snapshot"
          className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded border border-zinc-800 bg-zinc-900 p-4 text-xs"
        >
          {JSON.stringify(snapshot)}
        </pre>
      </section>
    </main>
  );
}
