"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

import {
  createInitialVoiceEventState,
  reduceVoiceEvent,
  type VoiceEventEffect,
  type VoiceEventReduction,
  type VoiceEventState,
} from "@/features/voice/event-reducer";
import { decodeVoiceEvent, type VoiceEvent } from "@/features/voice/events";
import {
  transitionVoiceSession,
  type VoiceSessionPhase,
} from "@/features/voice/session-machine";
import {
  bootstrapVoiceSession,
  endVoiceSession,
  VOICE_V2_EVENT_TOPIC,
  VoiceSessionApiError,
  type VoiceSessionBootstrap,
} from "@/features/voice/session-api";

const BOOTSTRAP_TIMEOUT_MS = 15_000;
const AGENT_READY_TIMEOUT_MS = 15_000;
const SESSION_END_TIMEOUT_MS = 5_000;
const RELEASE_MAX_ATTEMPTS = 3;
const RELEASE_ATTEMPT_TIMEOUT_MS = 5_000;
const RELEASE_RETRY_DELAYS_MS = [100, 250] as const;

type LocalTransportEventType =
  | "transport_connected"
  | "transport_reconnecting"
  | "transport_disconnected"
  | "agent_unavailable";

export interface VoiceSessionTranscriptEvent {
  readonly text: string;
  readonly isFinal: boolean;
  readonly speechFinal: false;
}

export interface UseVoiceSessionOptions {
  readonly enabled?: boolean;
  readonly apiUrl?: string;
  readonly agentId: string;
  readonly sessionId?: string;
  readonly onTranscript?: (event: VoiceSessionTranscriptEvent) => void;
  readonly onAssistantSpeech?: (text: string) => void;
  readonly onEffect?: (effect: VoiceEventEffect) => void;
  readonly onError?: (message: string) => void;
  readonly onLog?: (message: string) => void;
  readonly onPhaseChange?: (phase: VoiceSessionPhase) => void;
}

export interface VoiceSessionConnectOverrides {
  readonly sessionId?: string;
}

function randomId(): string {
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    throw new Error("This browser cannot create a secure voice call ID");
  }
  return crypto.randomUUID();
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : "Voice connection failed";
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function createAbortError(): Error {
  const error = new Error("Voice bootstrap was aborted");
  error.name = "AbortError";
  return error;
}

/** Reject on abort even when pre-fetch work does not observe the signal. */
function settleOnAbort<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(createAbortError());

  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      reject(createAbortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    operation.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      }
    );
  });
}

function isRetryableReleaseFailure(error: unknown): boolean {
  if (!(error instanceof VoiceSessionApiError)) return true;
  if (error.status === undefined) return true;
  // Release conflicts encode a durable scope/state mismatch; rate limits do not.
  return error.status === 429 || error.status >= 500;
}

interface VoiceCallIdentity {
  readonly voiceCallId: string;
  /** Used only when no server assignment exists, such as bootstrap failure. */
  readonly browserTraceId: string;
}

interface ActiveCallFailureOptions {
  readonly canRetry?: boolean;
  readonly retainCallIdentity?: boolean;
}

export interface VoiceBootstrapFailurePolicy {
  readonly code: string;
  readonly canRetry: boolean;
  readonly retainCallIdentity: boolean;
}

/**
 * Retry is a UI decision; identity retention is an idempotency decision.
 * A 409 explicitly invalidates the attempted assignment, while transient
 * failures retry the same call intent.
 */
export function classifyVoiceBootstrapFailure(
  error: unknown
): VoiceBootstrapFailurePolicy {
  if (isAbortError(error)) {
    return {
      code: "bootstrap_timeout",
      canRetry: true,
      retainCallIdentity: true,
    };
  }

  if (error instanceof VoiceSessionApiError) {
    if (error.status === 409) {
      return {
        code: "bootstrap_assignment_conflict",
        canRetry: true,
        retainCallIdentity: false,
      };
    }
    if (error.status === 429) {
      return {
        code: "bootstrap_rate_limited",
        canRetry: true,
        retainCallIdentity: true,
      };
    }
    if (error.status !== undefined && error.status >= 500) {
      return {
        code: "bootstrap_unavailable",
        canRetry: true,
        retainCallIdentity: true,
      };
    }
    return {
      code:
        error.status === 401
          ? "bootstrap_unauthenticated"
          : error.status === 403
            ? "bootstrap_forbidden"
            : error.status === 404
              ? "bootstrap_session_not_found"
              : "bootstrap_rejected",
      canRetry: false,
      retainCallIdentity: false,
    };
  }

  // fetch() network failures (and auth-token acquisition failures) have no
  // HTTP response. They may safely retry the same idempotent call intent.
  return {
    code: "bootstrap_network_error",
    canRetry: true,
    retainCallIdentity: true,
  };
}

function createVoiceCallIdentity(): VoiceCallIdentity {
  return {
    voiceCallId: randomId(),
    browserTraceId: randomId(),
  };
}

export function useVoiceSession(options: UseVoiceSessionOptions) {
  const optionsRef = useRef(options);
  const [callIdentity, setCallIdentity] = useState<VoiceCallIdentity | null>(null);
  const callIdentityRef = useRef<VoiceCallIdentity | null>(null);
  const voiceCallId = callIdentity?.voiceCallId;

  const [eventState, setEventState] = useState<VoiceEventState>(() =>
    createInitialVoiceEventState(options.sessionId)
  );
  const [assignment, setAssignment] = useState<VoiceSessionBootstrap | null>(null);
  const [isMicMuted, setIsMicMuted] = useState(false);
  const [isTTSEnabled, setIsTTSEnabled] = useState(true);
  const [audioPlaybackBlocked, setAudioPlaybackBlocked] = useState(false);

  const eventStateRef = useRef(eventState);
  const assignmentRef = useRef<VoiceSessionBootstrap | null>(null);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const roomRef = useRef<Room | null>(null);
  const listenerCleanupRef = useRef<readonly (() => void)[]>([]);
  const bootstrapAbortRef = useRef<AbortController | null>(null);
  const bootstrapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionEndTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectPromiseRef = useRef<Promise<void> | null>(null);
  const localProducerSequenceRef = useRef(0);
  const localProducerIdRef = useRef<string>("browser:unassigned:0");
  const activeVoiceCallIdRef = useRef<string | null>(null);
  const activeBrowserTraceIdRef = useRef<string | null>(null);
  const reconnectAttemptRef = useRef(0);
  const remoteAudioTracksRef = useRef<Set<RemoteAudioTrack>>(new Set());
  const audioElementsRef = useRef<Set<HTMLMediaElement>>(new Set());
  const ttsEnabledRef = useRef(true);
  const releasedCallIdsRef = useRef<Set<string>>(new Set());
  const releaseRetryTimersRef = useRef<
    Map<string, ReturnType<typeof setTimeout>>
  >(new Map());
  const releaseAttemptTimersRef = useRef<
    Map<string, ReturnType<typeof setTimeout>>
  >(new Map());
  const releaseAbortControllersRef = useRef<Map<string, AbortController>>(new Map());

  const log = useCallback((message: string) => {
    optionsRef.current.onLog?.(message);
  }, []);

  const releaseCallIdentity = useCallback(() => {
    callIdentityRef.current = null;
    if (mountedRef.current) setCallIdentity(null);
  }, []);

  const clearReleaseWork = useCallback(() => {
    for (const timer of releaseRetryTimersRef.current.values()) {
      clearTimeout(timer);
    }
    releaseRetryTimersRef.current.clear();
    for (const timer of releaseAttemptTimersRef.current.values()) {
      clearTimeout(timer);
    }
    releaseAttemptTimersRef.current.clear();
    for (const controller of releaseAbortControllersRef.current.values()) {
      controller.abort();
    }
    releaseAbortControllersRef.current.clear();
  }, []);

  const releaseServerAssignment = useCallback(
    (assignment: Pick<VoiceSessionBootstrap, "session_id" | "voice_call_id"> | null) => {
      if (!assignment || releasedCallIdsRef.current.has(assignment.voice_call_id)) return;
      const callId = assignment.voice_call_id;
      releasedCallIdsRef.current.add(callId);

      if (!mountedRef.current) {
        // keepalive owns best-effort page-exit delivery; no timers may outlive the hook.
        void endVoiceSession(
          { session_id: assignment.session_id, voice_call_id: callId },
          { apiUrl: optionsRef.current.apiUrl }
        ).catch(() => undefined);
        return;
      }

      const attemptRelease = (attempt: number) => {
        const handleFailure = (error: unknown) => {
          if (!mountedRef.current) return;
          const retryable = isRetryableReleaseFailure(error);
          const canRetry =
            attempt < RELEASE_MAX_ATTEMPTS && retryable;
          if (!canRetry) {
            if (retryable) releasedCallIdsRef.current.delete(callId);
            log(`Voice assignment release failed: ${errorMessage(error)}`);
            return;
          }

          const delay =
            RELEASE_RETRY_DELAYS_MS[attempt - 1] ??
            RELEASE_RETRY_DELAYS_MS[RELEASE_RETRY_DELAYS_MS.length - 1];
          const timer = setTimeout(() => {
            releaseRetryTimersRef.current.delete(callId);
            if (mountedRef.current) attemptRelease(attempt + 1);
          }, delay);
          releaseRetryTimersRef.current.set(callId, timer);
          log(
            `Voice assignment release failed; retrying ${attempt + 1}/${RELEASE_MAX_ATTEMPTS}`
          );
        };

        const abortController = new AbortController();
        releaseAbortControllersRef.current.set(callId, abortController);
        const attemptTimer = setTimeout(() => {
          abortController.abort();
        }, RELEASE_ATTEMPT_TIMEOUT_MS);
        releaseAttemptTimersRef.current.set(callId, attemptTimer);

        const finishAttempt = () => {
          if (releaseAbortControllersRef.current.get(callId) === abortController) {
            releaseAbortControllersRef.current.delete(callId);
          }
          if (releaseAttemptTimersRef.current.get(callId) === attemptTimer) {
            clearTimeout(attemptTimer);
            releaseAttemptTimersRef.current.delete(callId);
          }
        };

        try {
          void settleOnAbort(
            endVoiceSession(
              {
                session_id: assignment.session_id,
                voice_call_id: callId,
              },
              { apiUrl: optionsRef.current.apiUrl, signal: abortController.signal }
            ),
            abortController.signal
          ).then(finishAttempt, (error: unknown) => {
            finishAttempt();
            handleFailure(error);
          });
        } catch (error) {
          finishAttempt();
          handleFailure(error);
        }
      };

      attemptRelease(1);
    },
    [log]
  );

  const clearBootstrapTimer = useCallback(() => {
    if (bootstrapTimerRef.current !== null) {
      clearTimeout(bootstrapTimerRef.current);
      bootstrapTimerRef.current = null;
    }
  }, []);

  const clearReadyTimer = useCallback(() => {
    if (readyTimerRef.current !== null) {
      clearTimeout(readyTimerRef.current);
      readyTimerRef.current = null;
    }
  }, []);

  const clearSessionEndTimer = useCallback(() => {
    if (sessionEndTimerRef.current !== null) {
      clearTimeout(sessionEndTimerRef.current);
      sessionEndTimerRef.current = null;
    }
  }, []);

  const publishState = useCallback((nextState: VoiceEventState) => {
    const previousPhase = eventStateRef.current.session.phase;
    eventStateRef.current = nextState;
    if (!mountedRef.current) return;
    setEventState(nextState);
    if (nextState.session.phase !== previousPhase) {
      optionsRef.current.onPhaseChange?.(nextState.session.phase);
    }
  }, []);

  const detachAudioTrack = useCallback((track: RemoteAudioTrack) => {
    remoteAudioTracksRef.current.delete(track);
    for (const element of track.detach()) {
      audioElementsRef.current.delete(element);
      element.remove();
    }
  }, []);

  const cleanupTransport = useCallback((releaseAssignment = false): Promise<void> => {
    generationRef.current += 1;
    clearBootstrapTimer();
    clearReadyTimer();
    clearSessionEndTimer();
    bootstrapAbortRef.current?.abort();
    bootstrapAbortRef.current = null;
    connectPromiseRef.current = null;

    const cleanups = listenerCleanupRef.current;
    listenerCleanupRef.current = [];
    for (const cleanup of cleanups) cleanup();

    for (const track of remoteAudioTracksRef.current) {
      detachAudioTrack(track);
    }
    remoteAudioTracksRef.current.clear();
    for (const element of audioElementsRef.current) element.remove();
    audioElementsRef.current.clear();

    const room = roomRef.current;
    const activeAssignment = assignmentRef.current;
    const assignmentLocator = activeAssignment ?? (
      eventStateRef.current.sessionId && activeVoiceCallIdRef.current
        ? {
            session_id: eventStateRef.current.sessionId,
            voice_call_id: activeVoiceCallIdRef.current,
          }
        : null
    );
    roomRef.current = null;
    assignmentRef.current = null;
    if (releaseAssignment) releaseServerAssignment(assignmentLocator);
    if (mountedRef.current) setAssignment(null);
    reconnectAttemptRef.current = 0;
    if (!room) return Promise.resolve();
    void room.localParticipant.setMicrophoneEnabled(false).catch(() => {
      // Disconnect remains authoritative if the capture track already ended.
    });
    return room.disconnect(true).catch(() => {
      // Teardown is idempotent; a concurrently closed room needs no recovery.
    });
  }, [
    clearBootstrapTimer,
    clearReadyTimer,
    clearSessionEndTimer,
    detachAudioTrack,
    releaseServerAssignment,
  ]);

  const applyReduction = useCallback(
    (input: unknown): VoiceEventReduction => {
      const reduction = reduceVoiceEvent(eventStateRef.current, input);
      if (reduction.state !== eventStateRef.current) {
        publishState(reduction.state);
      }
      return reduction;
    },
    [publishState]
  );

  const createLocalEvent = useCallback(
    (eventType: LocalTransportEventType, payload: Record<string, unknown>): unknown => {
      localProducerSequenceRef.current += 1;
      return {
        schema_version: 1,
        event_id: randomId(),
        event_type: eventType,
        trace_id:
          assignmentRef.current?.trace_id ?? activeBrowserTraceIdRef.current,
        voice_call_id: activeVoiceCallIdRef.current,
        session_id: eventStateRef.current.sessionId,
        producer_id: localProducerIdRef.current,
        producer_sequence: localProducerSequenceRef.current,
        emitted_at: new Date().toISOString(),
        payload,
      };
    },
    []
  );

  const applyLocalEvent = useCallback(
    (eventType: LocalTransportEventType, payload: Record<string, unknown>) =>
      applyReduction(createLocalEvent(eventType, payload)),
    [applyReduction, createLocalEvent]
  );

  const runAppliedEvent = useCallback(
    (event: VoiceEvent, effects: readonly VoiceEventEffect[]) => {
      if (event.event_type === "agent_ready") {
        clearReadyTimer();
        log(`Agent ready with profile ${event.payload.profile_id}`);
      } else if (event.event_type === "transcript_segment") {
        optionsRef.current.onTranscript?.({
          text: event.payload.text,
          isFinal: event.payload.is_final,
          speechFinal: false,
        });
      } else if (
        event.event_type === "assistant_speech_started" &&
        event.payload.text
      ) {
        optionsRef.current.onAssistantSpeech?.(event.payload.text);
      } else if (event.event_type === "agent_unavailable") {
        clearReadyTimer();
        if (!event.payload.retryable) releaseCallIdentity();
        optionsRef.current.onError?.(event.payload.message);
      } else if (event.event_type === "session_ending") {
        clearSessionEndTimer();
        sessionEndTimerRef.current = setTimeout(() => {
          sessionEndTimerRef.current = null;
          releaseCallIdentity();
          void cleanupTransport(true);
        }, SESSION_END_TIMEOUT_MS);
      } else if (event.event_type === "session_ended") {
        clearSessionEndTimer();
        releaseCallIdentity();
      }

      for (const effect of effects) {
        optionsRef.current.onEffect?.(effect);
      }
    },
    [
      clearReadyTimer,
      clearSessionEndTimer,
      cleanupTransport,
      log,
      releaseCallIdentity,
    ]
  );

  const failActiveCall = useCallback(
    (
      generation: number,
      code: string,
      message: string,
      options: ActiveCallFailureOptions = {}
    ) => {
      if (generationRef.current !== generation || !roomRef.current) return;
      const canRetry = options.canRetry ?? false;
      if (!(options.retainCallIdentity ?? false)) releaseCallIdentity();
      const reduction = applyLocalEvent("agent_unavailable", {
        code,
        message,
        retryable: canRetry,
      });
      if (reduction.disposition === "applied") {
        const event = reduction.state.lastAppliedEventId;
        log(`Voice unavailable (${code})${event ? ` at ${event}` : ""}`);
        optionsRef.current.onError?.(message);
      }
      void cleanupTransport(!(options.retainCallIdentity ?? false));
    },
    [applyLocalEvent, cleanupTransport, log, releaseCallIdentity]
  );

  const handleTransportInput = useCallback(
    (input: unknown, generation: number) => {
      if (generationRef.current !== generation || !roomRef.current) return;
      const decoded = decodeVoiceEvent(input);
      const expectedTraceId = assignmentRef.current?.trace_id;
      if (
        decoded.ok &&
        (!expectedTraceId || decoded.event.trace_id !== expectedTraceId)
      ) {
        failActiveCall(
          generation,
          "event_trace_mismatch",
          "Voice received an event outside its assigned trace"
        );
        return;
      }
      const reduction = applyReduction(input);
      if (reduction.disposition !== "applied") {
        if (reduction.rejection) {
          log(
            `Ignored voice event (${reduction.disposition}): ${reduction.rejection.code}`
          );
        }
        if (
          reduction.state.compatibilityFailure ||
          reduction.state.session.phase === "unavailable"
        ) {
          const message =
            reduction.state.compatibilityFailure?.message ??
            reduction.state.session.unavailableReason?.message ??
            "Voice event contract is incompatible";
          if (!reduction.state.session.unavailableReason?.retryable) {
            releaseCallIdentity();
          }
          optionsRef.current.onError?.(message);
          void cleanupTransport(
            reduction.state.session.unavailableReason?.retryable !== true
          );
        }
        return;
      }

      if (decoded.ok) {
        runAppliedEvent(decoded.event, reduction.effects);
      }

      if (
        reduction.state.session.phase === "unavailable" ||
        (reduction.state.session.phase === "ended" &&
          reduction.state.session.terminationStage === "ended")
      ) {
        if (
          reduction.state.session.phase === "ended" ||
          !reduction.state.session.unavailableReason?.retryable
        ) {
          releaseCallIdentity();
        }
        void cleanupTransport(
          reduction.state.session.phase === "ended" ||
            reduction.state.session.unavailableReason?.retryable !== true
        );
      }
    },
    [
      applyReduction,
      cleanupTransport,
      failActiveCall,
      log,
      releaseCallIdentity,
      runAppliedEvent,
    ]
  );

  const connect = useCallback(
    (overrides: VoiceSessionConnectOverrides = {}): Promise<void> => {
      if (connectPromiseRef.current) return connectPromiseRef.current;

      const start = async () => {
        if (!optionsRef.current.enabled) {
          log("Voice V2 is disabled for this runtime assignment");
          return;
        }
        if (roomRef.current) return;

        const sessionId = overrides.sessionId ?? optionsRef.current.sessionId;
        if (!sessionId) {
          const nextState = {
            ...eventStateRef.current,
            session: transitionVoiceSession(eventStateRef.current.session, {
              type: "compatibility_error",
              code: "missing_session",
              message: "An owned session is required before starting Voice V2",
            }),
          };
          publishState(nextState);
          optionsRef.current.onError?.("Create a session before starting voice");
          return;
        }

        const requiresNewCallIdentity =
          callIdentityRef.current === null ||
          (eventStateRef.current.sessionId !== undefined &&
            eventStateRef.current.sessionId !== sessionId);
        let activeCallIdentity = callIdentityRef.current;
        if (requiresNewCallIdentity) {
          activeCallIdentity = createVoiceCallIdentity();
          callIdentityRef.current = activeCallIdentity;
          setCallIdentity(activeCallIdentity);
        }
        if (activeCallIdentity === null) {
          activeCallIdentity = createVoiceCallIdentity();
          callIdentityRef.current = activeCallIdentity;
          setCallIdentity(activeCallIdentity);
        }
        const activeVoiceCallId = activeCallIdentity.voiceCallId;
        activeVoiceCallIdRef.current = activeVoiceCallId;
        activeBrowserTraceIdRef.current = activeCallIdentity.browserTraceId;
        await cleanupTransport();
        const generation = generationRef.current + 1;
        generationRef.current = generation;
        localProducerSequenceRef.current = 0;
        localProducerIdRef.current = `browser:${activeVoiceCallId}:${generation}`;
        reconnectAttemptRef.current = 0;

        const initial = createInitialVoiceEventState(sessionId, activeVoiceCallId);
        publishState({
          ...initial,
          session: transitionVoiceSession(initial.session, { type: "connect_requested" }),
        });
        setAssignment(null);
        assignmentRef.current = null;
        setIsMicMuted(false);
        setAudioPlaybackBlocked(false);
        log("Requesting Voice V2 assignment");

        const room = new Room({
          adaptiveStream: false,
          dynacast: false,
          disconnectOnPageLeave: true,
          stopLocalTrackOnUnpublish: true,
        });
        roomRef.current = room;

        // Invoke this before the first awaited bootstrap step so the connect click
        // can satisfy restrictive browser audio-playback policies.
        void room.startAudio().catch(() => {
          if (generationRef.current === generation && mountedRef.current) {
            setAudioPlaybackBlocked(true);
          }
        });

        const abortController = new AbortController();
        bootstrapAbortRef.current = abortController;
        bootstrapTimerRef.current = setTimeout(() => {
          abortController.abort();
        }, BOOTSTRAP_TIMEOUT_MS);

        let bootstrap: VoiceSessionBootstrap;
        try {
          bootstrap = await settleOnAbort(
            bootstrapVoiceSession(
              { session_id: sessionId, voice_call_id: activeVoiceCallId },
              { apiUrl: optionsRef.current.apiUrl, signal: abortController.signal }
            ),
            abortController.signal
          );
        } catch (error) {
          clearBootstrapTimer();
          if (generationRef.current !== generation) return;
          const policy = classifyVoiceBootstrapFailure(error);
          failActiveCall(
            generation,
            policy.code,
            isAbortError(error)
              ? "Voice assignment timed out. Continue in text mode."
              : errorMessage(error),
            {
              canRetry: policy.canRetry,
              retainCallIdentity: policy.retainCallIdentity,
            }
          );
          return;
        } finally {
          clearBootstrapTimer();
          if (bootstrapAbortRef.current === abortController) {
            bootstrapAbortRef.current = null;
          }
        }

        if (generationRef.current !== generation || roomRef.current !== room) return;
        if (
          bootstrap.agent_id !== optionsRef.current.agentId ||
          bootstrap.session_id !== sessionId ||
          bootstrap.voice_call_id !== activeVoiceCallId ||
          bootstrap.event_topic !== VOICE_V2_EVENT_TOPIC
        ) {
          failActiveCall(
            generation,
            "assignment_identity_mismatch",
            "Voice assignment does not match this agent, session, or call"
          );
          return;
        }

        assignmentRef.current = bootstrap;
        releasedCallIdsRef.current.delete(bootstrap.voice_call_id);
        if (mountedRef.current) setAssignment(bootstrap);

        const onConnected = () => {
          if (generationRef.current !== generation || roomRef.current !== room) return;
          reconnectAttemptRef.current = 0;
          const reduction = applyLocalEvent("transport_connected", {});
          if (reduction.disposition !== "applied") return;
          log("Transport connected; waiting for genuine agent readiness");
          clearReadyTimer();
          readyTimerRef.current = setTimeout(() => {
            readyTimerRef.current = null;
            failActiveCall(
              generation,
              "agent_ready_timeout",
              "The voice agent did not become ready in time. Continue in text mode.",
              { canRetry: true, retainCallIdentity: false }
            );
          }, AGENT_READY_TIMEOUT_MS);
        };

        const onReconnecting = () => {
          if (generationRef.current !== generation || roomRef.current !== room) return;
          clearReadyTimer();
          reconnectAttemptRef.current += 1;
          applyLocalEvent("transport_reconnecting", {
            attempt: reconnectAttemptRef.current,
            reason: "livekit_reconnecting",
          });
          log("Voice transport reconnecting");
        };

        const onReconnected = () => {
          if (generationRef.current !== generation || roomRef.current !== room) return;
          failActiveCall(
            generation,
            "reconnect_not_supported",
            "Voice transport reconnected, but this runtime cannot safely restore the event stream yet. Start a fresh voice call.",
            { canRetry: true, retainCallIdentity: false }
          );
        };

        const onDisconnected = () => {
          if (generationRef.current !== generation || roomRef.current !== room) return;
          failActiveCall(
            generation,
            "transport_unavailable",
            "Voice transport disconnected",
            { canRetry: true, retainCallIdentity: false }
          );
        };

        const onDataReceived = (
          payload: Uint8Array,
          participant?: RemoteParticipant,
          kind?: DataPacket_Kind,
          topic?: string
        ) => {
          if (generationRef.current !== generation || roomRef.current !== room) return;
          if (
            topic !== bootstrap.event_topic ||
            kind !== DataPacket_Kind.RELIABLE ||
            participant?.isAgent !== true ||
            participant.identity !== bootstrap.agent_participant_identity
          ) {
            failActiveCall(
              generation,
              "invalid_event_channel",
              "Voice received data outside its authenticated reliable event channel"
            );
            return;
          }

          let input: unknown;
          try {
            input = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(payload));
          } catch {
            input = null;
          }
          handleTransportInput(input, generation);
        };

        const onTrackSubscribed = (
          track: RemoteTrack,
          _publication: RemoteTrackPublication,
          participant: RemoteParticipant
        ) => {
          if (
            generationRef.current !== generation ||
            roomRef.current !== room ||
            participant.isAgent !== true ||
            participant.identity !== bootstrap.agent_participant_identity ||
            !isAudioTrack(track) ||
            track.isLocal
          ) {
            return;
          }
          const audioTrack = track as RemoteAudioTrack;
          audioTrack.setVolume(ttsEnabledRef.current ? 1 : 0);
          const element = audioTrack.attach();
          element.autoplay = true;
          element.setAttribute("data-murmur-voice-call", activeVoiceCallId);
          element.className = "hidden";
          document.body.appendChild(element);
          remoteAudioTracksRef.current.add(audioTrack);
          audioElementsRef.current.add(element);
        };

        const onTrackUnsubscribed = (track: RemoteTrack) => {
          if (isAudioTrack(track) && !track.isLocal) {
            detachAudioTrack(track as RemoteAudioTrack);
          }
        };

        const onMediaDevicesError = (error: Error, kind?: MediaDeviceKind) => {
          if (kind && kind !== "audioinput") return;
          failActiveCall(
            generation,
            "microphone_unavailable",
            `Microphone unavailable: ${error.message}`,
            { canRetry: true, retainCallIdentity: false }
          );
        };

        const onAudioPlaybackChanged = (playing: boolean) => {
          if (generationRef.current === generation && mountedRef.current) {
            setAudioPlaybackBlocked(!playing);
          }
        };

        room.on(RoomEvent.Connected, onConnected);
        room.on(RoomEvent.Reconnecting, onReconnecting);
        room.on(RoomEvent.Reconnected, onReconnected);
        room.on(RoomEvent.Disconnected, onDisconnected);
        room.on(RoomEvent.DataReceived, onDataReceived);
        room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
        room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
        room.on(RoomEvent.MediaDevicesError, onMediaDevicesError);
        room.on(RoomEvent.AudioPlaybackStatusChanged, onAudioPlaybackChanged);
        listenerCleanupRef.current = [
          () => room.off(RoomEvent.Connected, onConnected),
          () => room.off(RoomEvent.Reconnecting, onReconnecting),
          () => room.off(RoomEvent.Reconnected, onReconnected),
          () => room.off(RoomEvent.Disconnected, onDisconnected),
          () => room.off(RoomEvent.DataReceived, onDataReceived),
          () => room.off(RoomEvent.TrackSubscribed, onTrackSubscribed),
          () => room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed),
          () => room.off(RoomEvent.MediaDevicesError, onMediaDevicesError),
          () => room.off(RoomEvent.AudioPlaybackStatusChanged, onAudioPlaybackChanged),
        ];

        try {
          await room.connect(bootstrap.server_url, bootstrap.participant_token, {
            autoSubscribe: true,
          });
          if (generationRef.current !== generation || roomRef.current !== room) return;
          await room.localParticipant.setMicrophoneEnabled(true, {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          });
          if (generationRef.current !== generation || roomRef.current !== room) return;
          setIsMicMuted(false);
          setAudioPlaybackBlocked(!room.canPlaybackAudio);
          log(`Joined assigned room ${bootstrap.room_name}`);
        } catch (error) {
          if (generationRef.current !== generation || isAbortError(error)) return;
          failActiveCall(
            generation,
            "transport_connect_failed",
            `Voice transport failed: ${errorMessage(error)}`,
            { canRetry: true, retainCallIdentity: false }
          );
        }
      };

      const promise = start().finally(() => {
        if (connectPromiseRef.current === promise) {
          connectPromiseRef.current = null;
        }
      });
      connectPromiseRef.current = promise;
      return promise;
    },
    [
      applyLocalEvent,
      cleanupTransport,
      clearBootstrapTimer,
      clearReadyTimer,
      detachAudioTrack,
      failActiveCall,
      handleTransportInput,
      log,
      publishState,
    ]
  );

  const disconnect = useCallback(() => {
    releaseCallIdentity();
    const nextState: VoiceEventState = {
      ...eventStateRef.current,
      session: transitionVoiceSession(eventStateRef.current.session, {
        type: "end_requested",
      }),
    };
    publishState(nextState);
    void cleanupTransport(true);
    log("Voice ended");
  }, [cleanupTransport, log, publishState, releaseCallIdentity]);

  const cancelConnection = useCallback(() => {
    releaseCallIdentity();
    void cleanupTransport(true);
    publishState(createInitialVoiceEventState(eventStateRef.current.sessionId));
    log("Voice connection cancelled");
  }, [cleanupTransport, log, publishState, releaseCallIdentity]);

  const toggleMicMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room || !eventStateRef.current.session.voiceReady) return;
    const generation = generationRef.current;
    const nextMuted = !isMicMuted;
    try {
      await room.localParticipant.setMicrophoneEnabled(!nextMuted);
      if (generationRef.current === generation && roomRef.current === room) {
        setIsMicMuted(nextMuted);
      }
    } catch (error) {
      failActiveCall(
        generation,
        "microphone_control_failed",
        `Could not ${nextMuted ? "mute" : "unmute"} microphone: ${errorMessage(error)}`,
        { canRetry: true, retainCallIdentity: false }
      );
    }
  }, [failActiveCall, isMicMuted]);

  const toggleTTS = useCallback(() => {
    const nextEnabled = !ttsEnabledRef.current;
    ttsEnabledRef.current = nextEnabled;
    setIsTTSEnabled(nextEnabled);
    for (const track of remoteAudioTracksRef.current) {
      track.setVolume(nextEnabled ? 1 : 0);
    }
    if (nextEnabled && roomRef.current) {
      void roomRef.current.startAudio().then(
        () => {
          if (mountedRef.current) setAudioPlaybackBlocked(false);
        },
        () => {
          if (mountedRef.current) setAudioPlaybackBlocked(true);
        }
      );
    }
  }, []);

  const resumeAudio = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    try {
      await room.startAudio();
      if (roomRef.current === room && mountedRef.current) {
        setAudioPlaybackBlocked(false);
      }
    } catch (error) {
      optionsRef.current.onError?.(`Audio playback remains blocked: ${errorMessage(error)}`);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearReleaseWork();
      void cleanupTransport(true);
    };
  }, [cleanupTransport, clearReleaseWork]);

  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  useEffect(() => {
    if (!options.enabled && roomRef.current) {
      void cleanupTransport(true);
    }
  }, [cleanupTransport, options.enabled]);

  return {
    voiceCallId: voiceCallId ?? "",
    phase: eventState.session.phase,
    session: eventState.session,
    eventState,
    assignment,
    connect,
    disconnect,
    cancelConnection,
    isMicMuted,
    isTTSEnabled,
    toggleMicMute,
    toggleTTS,
    audioPlaybackBlocked,
    resumeAudio,
  };
}
