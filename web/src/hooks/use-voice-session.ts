"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AssignmentReleaseScheduler } from "@/features/voice/assignment-release";
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
  LiveKitVoiceTransport,
  type LocalMicrophonePublicationObservation,
} from "@/features/voice/livekit-transport";
import {
  bootstrapVoiceSession,
  VOICE_V2_EVENT_TOPIC,
  VoiceSessionApiError,
  type VoiceAuthHeaderProvider,
  type VoiceSessionBootstrap,
} from "@/features/voice/session-api";

const BOOTSTRAP_TIMEOUT_MS = 15_000;
const TRANSPORT_CONNECT_TIMEOUT_MS = 15_000;
const AGENT_READY_TIMEOUT_MS = 15_000;
const SESSION_END_TIMEOUT_MS = 5_000;
const MAX_PENDING_READY_EVENTS = 128;

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
  readonly onEvent?: (event: VoiceEvent) => void;
  readonly onLocalMicrophoneTrack?: (track: MediaStreamTrack | null) => void;
  readonly onLocalMicrophonePublication?: (
    track: MediaStreamTrack,
    observation: LocalMicrophonePublicationObservation
  ) => void;
  readonly onError?: (message: string) => void;
  readonly onLog?: (message: string) => void;
  readonly onPhaseChange?: (phase: VoiceSessionPhase) => void;
  readonly authHeaderProvider?: VoiceAuthHeaderProvider;
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

interface VoiceCallIdentity {
  readonly voiceCallId: string;
  /** Used only when no server assignment exists, such as bootstrap failure. */
  readonly browserTraceId: string;
}

interface ActiveCallFailureOptions {
  readonly canRetry?: boolean;
  readonly retainCallIdentity?: boolean;
}

interface PendingReadyActivation {
  readonly generation: number;
  readonly event: VoiceEvent;
  readonly reduction: VoiceEventReduction;
  readonly queuedInputs: unknown[];
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
  const [isMicMuted, setIsMicMuted] = useState(true);
  const [isTTSEnabled, setIsTTSEnabled] = useState(true);
  const [audioPlaybackBlocked, setAudioPlaybackBlocked] = useState(false);
  // Group the stable React writers behind one immutable owner for transport
  // callbacks and the session orchestrator.
  const [stateWriters] = useState(() => ({
    setAssignment,
    setAudioPlaybackBlocked,
    setCallIdentity,
    setEventState,
    setIsMicMuted,
    setIsTTSEnabled,
  }));

  const eventStateRef = useRef(eventState);
  const assignmentRef = useRef<VoiceSessionBootstrap | null>(null);
  const mountedRef = useRef(true);
  const generationRef = useRef(0);
  const transportRef = useRef<LiveKitVoiceTransport | null>(null);
  const bootstrapAbortRef = useRef<AbortController | null>(null);
  const bootstrapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transportConnectAbortRef = useRef<AbortController | null>(null);
  const transportConnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionEndTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectPromiseRef = useRef<Promise<void> | null>(null);
  const localProducerSequenceRef = useRef(0);
  const localProducerIdRef = useRef<string>("browser:unassigned:0");
  const activeVoiceCallIdRef = useRef<string | null>(null);
  const activeBrowserTraceIdRef = useRef<string | null>(null);
  const localMicrophoneTrackRef = useRef<{
    readonly generation: number;
    readonly track: MediaStreamTrack;
  } | null>(null);
  const pendingReadyActivationRef = useRef<PendingReadyActivation | null>(null);
  const transportInputHandlerRef = useRef<
    ((input: unknown, generation: number) => void) | null
  >(null);
  const ttsEnabledRef = useRef(true);
  const [releaseScheduler] = useState(
    () =>
      new AssignmentReleaseScheduler({
        apiUrl: options.apiUrl,
        authHeaderProvider: options.authHeaderProvider,
        onLog: options.onLog,
      })
  );

  const log = useCallback((message: string) => {
    optionsRef.current.onLog?.(message);
  }, []);

  const releaseCallIdentity = useCallback(() => {
    callIdentityRef.current = null;
    if (mountedRef.current) stateWriters.setCallIdentity(null);
  }, [stateWriters]);

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

  const clearTransportConnectTimer = useCallback(() => {
    if (transportConnectTimerRef.current !== null) {
      clearTimeout(transportConnectTimerRef.current);
      transportConnectTimerRef.current = null;
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
    stateWriters.setEventState(nextState);
    if (nextState.session.phase !== previousPhase) {
      optionsRef.current.onPhaseChange?.(nextState.session.phase);
    }
  }, [stateWriters]);

  const cleanupTransport = useCallback((releaseAssignment = false): Promise<void> => {
    generationRef.current += 1;
    clearBootstrapTimer();
    clearTransportConnectTimer();
    clearReadyTimer();
    clearSessionEndTimer();
    bootstrapAbortRef.current?.abort();
    bootstrapAbortRef.current = null;
    transportConnectAbortRef.current?.abort();
    transportConnectAbortRef.current = null;
    pendingReadyActivationRef.current = null;
    connectPromiseRef.current = null;

    const transport = transportRef.current;
    const activeAssignment = assignmentRef.current;
    const assignmentLocator = activeAssignment ?? (
      eventStateRef.current.sessionId && activeVoiceCallIdRef.current
        ? {
            session_id: eventStateRef.current.sessionId,
            voice_call_id: activeVoiceCallIdRef.current,
          }
        : null
    );
    transportRef.current = null;
    assignmentRef.current = null;
    if (releaseAssignment) releaseScheduler.release(assignmentLocator);
    if (mountedRef.current) {
      stateWriters.setAssignment(null);
      stateWriters.setIsMicMuted(true);
    }
    return transport?.disconnect() ?? Promise.resolve();
  }, [
    clearBootstrapTimer,
    clearTransportConnectTimer,
    clearReadyTimer,
    clearSessionEndTimer,
    releaseScheduler,
    stateWriters,
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
        releaseCallIdentity();
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
      if (generationRef.current !== generation || !transportRef.current) return;
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
      if (generationRef.current !== generation || !transportRef.current) return;
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

      const pendingReady = pendingReadyActivationRef.current;
      if (pendingReady?.generation === generation) {
        if (decoded.ok && decoded.event.event_type === "agent_unavailable") {
          pendingReadyActivationRef.current = null;
        } else {
          if (decoded.ok && decoded.event.event_type === "agent_ready") {
            log("Ignored duplicate agent readiness during microphone activation");
            return;
          }
          if (pendingReady.queuedInputs.length >= MAX_PENDING_READY_EVENTS) {
            pendingReadyActivationRef.current = null;
            failActiveCall(
              generation,
              "ready_event_buffer_overflow",
              "Voice received too many events before microphone activation completed"
            );
            return;
          }
          pendingReady.queuedInputs.push(input);
          return;
        }
      }

      if (decoded.ok && decoded.event.event_type === "agent_ready") {
        const readyReduction = reduceVoiceEvent(eventStateRef.current, input);
        if (readyReduction.disposition === "applied") {
          const transport = transportRef.current;
          const pending: PendingReadyActivation = {
            generation,
            event: decoded.event,
            reduction: readyReduction,
            queuedInputs: [],
          };
          pendingReadyActivationRef.current = pending;
          void transport.activateMicrophoneAfterReady().then(
            () => {
              if (
                generationRef.current !== generation ||
                transportRef.current !== transport ||
                pendingReadyActivationRef.current !== pending
              ) {
                return;
              }
              pendingReadyActivationRef.current = null;
              publishState(readyReduction.state);
              if (mountedRef.current) stateWriters.setIsMicMuted(false);
              try {
                optionsRef.current.onEvent?.(decoded.event);
              } catch (error) {
                log(`Voice event observer failed: ${errorMessage(error)}`);
              }
              runAppliedEvent(decoded.event, readyReduction.effects);
              for (const queuedInput of pending.queuedInputs) {
                transportInputHandlerRef.current?.(queuedInput, generation);
              }
            },
            (error: unknown) => {
              if (
                generationRef.current !== generation ||
                transportRef.current !== transport ||
                pendingReadyActivationRef.current !== pending
              ) {
                return;
              }
              pendingReadyActivationRef.current = null;
              failActiveCall(
                generation,
                "microphone_activation_failed",
                `Could not activate microphone after agent readiness: ${errorMessage(error)}`,
                { canRetry: true, retainCallIdentity: false }
              );
            }
          );
          return;
        }
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
        try {
          optionsRef.current.onEvent?.(decoded.event);
        } catch (error) {
          log(`Voice event observer failed: ${errorMessage(error)}`);
        }
        runAppliedEvent(decoded.event, reduction.effects);
      }

      if (
        reduction.state.session.phase === "unavailable" ||
        (reduction.state.session.phase === "ended" &&
          reduction.state.session.terminationStage === "ended")
      ) {
        const mustReleaseAssignment =
          reduction.state.session.phase === "ended" ||
          (decoded.ok && decoded.event.event_type === "agent_unavailable") ||
          !reduction.state.session.unavailableReason?.retryable;
        if (mustReleaseAssignment) {
          releaseCallIdentity();
        }
        void cleanupTransport(mustReleaseAssignment);
      }
    },
    [
      applyReduction,
      cleanupTransport,
      failActiveCall,
      log,
      publishState,
      releaseCallIdentity,
      runAppliedEvent,
      stateWriters,
    ]
  );

  useEffect(() => {
    transportInputHandlerRef.current = handleTransportInput;
    return () => {
      if (transportInputHandlerRef.current === handleTransportInput) {
        transportInputHandlerRef.current = null;
      }
    };
  }, [handleTransportInput]);

  const connect = useCallback(
    (overrides: VoiceSessionConnectOverrides = {}): Promise<void> => {
      if (connectPromiseRef.current) return connectPromiseRef.current;

      const start = async () => {
        if (!optionsRef.current.enabled) {
          log("Voice V2 is disabled for this runtime assignment");
          return;
        }
        if (transportRef.current) return;

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
          stateWriters.setCallIdentity(activeCallIdentity);
        }
        if (activeCallIdentity === null) {
          activeCallIdentity = createVoiceCallIdentity();
          callIdentityRef.current = activeCallIdentity;
          stateWriters.setCallIdentity(activeCallIdentity);
        }
        const activeVoiceCallId = activeCallIdentity.voiceCallId;
        activeVoiceCallIdRef.current = activeVoiceCallId;
        activeBrowserTraceIdRef.current = activeCallIdentity.browserTraceId;
        await cleanupTransport();
        const generation = generationRef.current + 1;
        generationRef.current = generation;
        localProducerSequenceRef.current = 0;
        localProducerIdRef.current = `browser:${activeVoiceCallId}:${generation}`;

        const initial = createInitialVoiceEventState(sessionId, activeVoiceCallId);
        publishState({
          ...initial,
          session: transitionVoiceSession(initial.session, { type: "connect_requested" }),
        });
        stateWriters.setAssignment(null);
        assignmentRef.current = null;
        stateWriters.setIsMicMuted(true);
        stateWriters.setAudioPlaybackBlocked(false);
        log("Requesting Voice V2 assignment");

        let transport: LiveKitVoiceTransport;
        const isCurrentTransport = () =>
          generationRef.current === generation &&
          transportRef.current === transport;
        transport = new LiveKitVoiceTransport({
          voiceCallId: activeVoiceCallId,
          ttsEnabled: ttsEnabledRef.current,
          callbacks: {
            isCurrent: isCurrentTransport,
            onConnected: () => {
              if (!isCurrentTransport()) return;
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
            },
            onReconnecting: (attempt) => {
              if (!isCurrentTransport()) return;
              clearReadyTimer();
              failActiveCall(
                generation,
                "reconnect_not_supported",
                `Voice transport connection was interrupted (attempt ${attempt}). Start a fresh voice call.`,
                { canRetry: true, retainCallIdentity: false }
              );
            },
            onReconnected: () => {
              if (!isCurrentTransport()) return;
              failActiveCall(
                generation,
                "reconnect_not_supported",
                "Voice transport reconnected, but this runtime cannot safely restore the event stream yet. Start a fresh voice call.",
                { canRetry: true, retainCallIdentity: false }
              );
            },
            onDisconnected: () => {
              if (!isCurrentTransport()) return;
              failActiveCall(
                generation,
                "transport_unavailable",
                "Voice transport disconnected",
                { canRetry: true, retainCallIdentity: false }
              );
            },
            onAgentDisconnected: () => {
              if (!isCurrentTransport()) return;
              failActiveCall(
                generation,
                "agent_disconnected",
                "Voice agent disconnected. Start a fresh voice call.",
                { canRetry: true, retainCallIdentity: false }
              );
            },
            onTransportInput: (input) => {
              if (!isCurrentTransport()) return;
              handleTransportInput(input, generation);
            },
            onInvalidEventChannel: () => {
              if (!isCurrentTransport()) return;
              failActiveCall(
                generation,
                "invalid_event_channel",
                "Voice received data outside its authenticated reliable event channel"
              );
            },
            onMicrophoneUnavailable: (error) => {
              if (!isCurrentTransport()) return;
              failActiveCall(
                generation,
                "microphone_unavailable",
                `Microphone unavailable: ${error.message}`,
                { canRetry: true, retainCallIdentity: false }
              );
            },
            onAudioPlaybackBlockedChange: (blocked) => {
              if (isCurrentTransport() && mountedRef.current) {
                stateWriters.setAudioPlaybackBlocked(blocked);
              }
            },
            onLocalMicrophoneTrack: (track) => {
              const currentTrack = localMicrophoneTrackRef.current;
              if (track) {
                if (!isCurrentTransport()) return;
                localMicrophoneTrackRef.current = { generation, track };
                optionsRef.current.onLocalMicrophoneTrack?.(track);
                return;
              }
              if (currentTrack?.generation !== generation) return;
              localMicrophoneTrackRef.current = null;
              optionsRef.current.onLocalMicrophoneTrack?.(null);
            },
            onLocalMicrophonePublication: (track, observation) => {
              if (!isCurrentTransport()) return;
              optionsRef.current.onLocalMicrophonePublication?.(
                track,
                observation
              );
            },
          },
        });
        transportRef.current = transport;

        // Invoke this before the first awaited bootstrap step so the connect click
        // can satisfy restrictive browser audio-playback policies.
        transport.primeAudioPlayback();

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
              {
                apiUrl: optionsRef.current.apiUrl,
                signal: abortController.signal,
                authHeaderProvider: optionsRef.current.authHeaderProvider,
              }
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

        if (!isCurrentTransport()) return;
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
        releaseScheduler.markAssigned(bootstrap.voice_call_id);
        if (mountedRef.current) stateWriters.setAssignment(bootstrap);

        try {
          const transportConnectAbort = new AbortController();
          let transportConnectTimedOut = false;
          transportConnectAbortRef.current = transportConnectAbort;
          transportConnectTimerRef.current = setTimeout(() => {
            transportConnectTimedOut = true;
            transportConnectAbort.abort();
          }, TRANSPORT_CONNECT_TIMEOUT_MS);
          try {
            await settleOnAbort(
              transport.connect(bootstrap),
              transportConnectAbort.signal
            );
          } catch (error) {
            if (generationRef.current !== generation) return;
            if (transportConnectTimedOut) {
              failActiveCall(
                generation,
                "transport_connect_timeout",
                "Voice transport and microphone setup timed out. Start a fresh voice call.",
                { canRetry: true, retainCallIdentity: false }
              );
              return;
            }
            if (transportConnectAbort.signal.aborted) return;
            throw error;
          } finally {
            clearTransportConnectTimer();
            if (transportConnectAbortRef.current === transportConnectAbort) {
              transportConnectAbortRef.current = null;
            }
          }
          if (!isCurrentTransport()) return;
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
      clearTransportConnectTimer,
      clearReadyTimer,
      failActiveCall,
      handleTransportInput,
      log,
      publishState,
      releaseScheduler,
      stateWriters,
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
    const transport = transportRef.current;
    if (!transport || !eventStateRef.current.session.voiceReady) return;
    const generation = generationRef.current;
    const nextMuted = !isMicMuted;
    try {
      await transport.setMicrophoneEnabled(!nextMuted);
      if (
        generationRef.current === generation &&
        transportRef.current === transport
      ) {
        stateWriters.setIsMicMuted(nextMuted);
      }
    } catch (error) {
      failActiveCall(
        generation,
        "microphone_control_failed",
        `Could not ${nextMuted ? "mute" : "unmute"} microphone: ${errorMessage(error)}`,
        { canRetry: true, retainCallIdentity: false }
      );
    }
  }, [failActiveCall, isMicMuted, stateWriters]);

  const toggleTTS = useCallback(() => {
    const nextEnabled = !ttsEnabledRef.current;
    ttsEnabledRef.current = nextEnabled;
    stateWriters.setIsTTSEnabled(nextEnabled);
    transportRef.current?.setTtsEnabled(nextEnabled);
  }, [stateWriters]);

  const resumeAudio = useCallback(async () => {
    const transport = transportRef.current;
    if (!transport) return;
    try {
      await transport.resumeAudio();
    } catch (error) {
      optionsRef.current.onError?.(`Audio playback remains blocked: ${errorMessage(error)}`);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    releaseScheduler.mount();
    return () => {
      mountedRef.current = false;
      releaseScheduler.dispose();
      void cleanupTransport(true);
    };
  }, [cleanupTransport, releaseScheduler]);

  useEffect(() => {
    optionsRef.current = options;
    releaseScheduler.configure({
      apiUrl: options.apiUrl,
      authHeaderProvider: options.authHeaderProvider,
      onLog: options.onLog,
    });
  }, [options, releaseScheduler]);

  useEffect(() => {
    if (!options.enabled && transportRef.current) {
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
