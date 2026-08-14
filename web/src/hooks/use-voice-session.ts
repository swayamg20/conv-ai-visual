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
import { type VoiceEvent } from "@/features/voice/events";
import {
  transitionVoiceSession,
  type VoiceSessionPhase,
} from "@/features/voice/session-machine";
import type { LocalMicrophonePublicationObservation } from "@/features/voice/livekit-transport";
import {
  bootstrapVoiceSession,
  VoiceSessionApiError,
  type VoiceAuthHeaderProvider,
  type VoiceSessionBootstrap,
} from "@/features/voice/session-api";
import {
  loadVoiceTransport,
  type VoiceLocalMicrophoneDiagnostic,
  type VoiceTransport,
  type VoiceTransportEventRejection,
  type VoiceTransportLoader,
} from "@/features/voice/voice-transport";

const BOOTSTRAP_TIMEOUT_MS = 15_000;
const TRANSPORT_LOAD_TIMEOUT_MS = 15_000;
const TRANSPORT_CONNECT_TIMEOUT_MS = 15_000;
const TRANSPORT_DISCONNECT_TIMEOUT_MS = 15_000;
const PREPARED_ASSIGNMENT_TIMEOUT_MS = 30_000;
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
  /** Narrow injection seam for deterministic lifecycle tests. */
  readonly transportLoader?: VoiceTransportLoader;
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

type BoundedSettlement<T> =
  | { readonly status: "fulfilled"; readonly value: T }
  | { readonly status: "rejected"; readonly reason: unknown }
  | { readonly status: "timed_out" };

/** Settle by a fixed deadline while still observing any late rejection. */
function settleBounded<T>(
  operation: Promise<T>,
  timeoutMs: number
): Promise<BoundedSettlement<T>> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: BoundedSettlement<T>) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => finish({ status: "timed_out" }), timeoutMs);
    operation.then(
      (value) => finish({ status: "fulfilled", value }),
      (reason: unknown) => finish({ status: "rejected", reason })
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
  readonly queuedEvents: VoiceEvent[];
}

interface VoiceTransportTeardown {
  releaseAssignment: boolean;
  promise: Promise<void> | null;
}

interface VoiceTransportLoad {
  readonly promise: Promise<VoiceTransport>;
  readonly settlement: Promise<BoundedSettlement<VoiceTransport>>;
  lateCleanupScheduled: boolean;
}

interface VoiceConnectOperation {
  readonly intent: number;
  promise: Promise<void>;
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
  const transportRef = useRef<VoiceTransport | null>(null);
  const transportLoadRef = useRef<VoiceTransportLoad | null>(null);
  const teardownRef = useRef<VoiceTransportTeardown | null>(null);
  const bootstrapAbortRef = useRef<AbortController | null>(null);
  const bootstrapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const transportConnectAbortRef = useRef<AbortController | null>(null);
  const transportConnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const preparedAssignmentTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const readyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionEndTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectOperationRef = useRef<VoiceConnectOperation | null>(null);
  const connectIntentRef = useRef(0);
  const localProducerSequenceRef = useRef(0);
  const localProducerIdRef = useRef<string>("browser:unassigned:0");
  const activeVoiceCallIdRef = useRef<string | null>(null);
  const activeBrowserTraceIdRef = useRef<string | null>(null);
  const localMicrophoneTrackRef = useRef<{
    readonly generation: number;
    readonly track: MediaStreamTrack;
  } | null>(null);
  const pendingReadyActivationRef = useRef<PendingReadyActivation | null>(null);
  const transportEventHandlerRef = useRef<
    ((event: VoiceEvent, generation: number) => void) | null
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

  const disconnectTransportWithinDeadline = useCallback(
    async (transport: VoiceTransport): Promise<void> => {
      const settlement = await settleBounded(
        Promise.resolve().then(() => transport.disconnect()),
        TRANSPORT_DISCONNECT_TIMEOUT_MS
      );
      if (settlement.status === "timed_out") {
        log("Voice transport cleanup timed out; releasing its assignment");
      } else if (settlement.status === "rejected") {
        log(`Voice transport cleanup failed: ${errorMessage(settlement.reason)}`);
      }
    },
    [log]
  );

  const scheduleLateTransportCleanup = useCallback(
    (transportLoad: VoiceTransportLoad) => {
      if (transportLoad.lateCleanupScheduled) return;
      transportLoad.lateCleanupScheduled = true;
      void transportLoad.promise
        .then((transport) => disconnectTransportWithinDeadline(transport))
        .catch(() => undefined);
    },
    [disconnectTransportWithinDeadline]
  );

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

  const clearPreparedAssignmentTimer = useCallback(() => {
    if (preparedAssignmentTimerRef.current !== null) {
      clearTimeout(preparedAssignmentTimerRef.current);
      preparedAssignmentTimerRef.current = null;
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

  const cleanupTransport = useCallback(
    (releaseAssignment = false): Promise<void> => {
      const existingTeardown = teardownRef.current;
      if (existingTeardown) {
        if (releaseAssignment) existingTeardown.releaseAssignment = true;
        return existingTeardown.promise ?? Promise.resolve();
      }

      generationRef.current += 1;
      clearBootstrapTimer();
      clearPreparedAssignmentTimer();
      clearTransportConnectTimer();
      clearReadyTimer();
      clearSessionEndTimer();
      bootstrapAbortRef.current?.abort();
      bootstrapAbortRef.current = null;
      transportConnectAbortRef.current?.abort();
      transportConnectAbortRef.current = null;
      pendingReadyActivationRef.current = null;

      const transport = transportRef.current;
      const transportLoad = transportLoadRef.current;
      const activeAssignment = assignmentRef.current;
      const assignmentLocator =
        activeAssignment ??
        (eventStateRef.current.sessionId && activeVoiceCallIdRef.current
          ? {
              session_id: eventStateRef.current.sessionId,
              voice_call_id: activeVoiceCallIdRef.current,
            }
          : null);
      transportRef.current = null;
      transportLoadRef.current = null;
      assignmentRef.current = null;
      if (mountedRef.current) {
        stateWriters.setAssignment(null);
        stateWriters.setIsMicMuted(true);
      }

      const teardown: VoiceTransportTeardown = {
        releaseAssignment,
        promise: null,
      };
      const teardownPromise = (async () => {
        // Publish the coalescing owner before a resource-free teardown can
        // settle, so a same-turn terminal caller can still upgrade release.
        await Promise.resolve();
        try {
          let ownedTransport = transport;
          if (!ownedTransport && transportLoad) {
            const settlement = await transportLoad.settlement;
            if (settlement.status === "fulfilled") {
              ownedTransport = settlement.value;
            } else if (settlement.status === "timed_out") {
              scheduleLateTransportCleanup(transportLoad);
            }
          }
          if (ownedTransport) {
            await disconnectTransportWithinDeadline(ownedTransport);
          }
        } catch (error) {
          log(`Voice transport cleanup failed: ${errorMessage(error)}`);
        } finally {
          if (teardown.releaseAssignment) {
            await releaseScheduler.release(assignmentLocator);
          }
        }
      })().finally(() => {
        if (teardownRef.current === teardown) teardownRef.current = null;
      });
      teardown.promise = teardownPromise;
      teardownRef.current = teardown;
      return teardownPromise;
    },
    [
      clearBootstrapTimer,
      clearPreparedAssignmentTimer,
      clearReadyTimer,
      clearSessionEndTimer,
      clearTransportConnectTimer,
      disconnectTransportWithinDeadline,
      log,
      releaseScheduler,
      scheduleLateTransportCleanup,
      stateWriters,
    ]
  );

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
      if (generationRef.current !== generation) return;
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

  const handleTransportEvent = useCallback(
    (event: VoiceEvent, generation: number) => {
      const transport = transportRef.current;
      const activeAssignment = assignmentRef.current;
      if (
        generationRef.current !== generation ||
        !transport ||
        !activeAssignment
      ) {
        return;
      }
      if (
        event.trace_id !== activeAssignment.trace_id ||
        event.session_id !== activeAssignment.session_id ||
        event.voice_call_id !== activeAssignment.voice_call_id
      ) {
        failActiveCall(
          generation,
          "event_scope_mismatch",
          "Voice received an event outside its assigned trace"
        );
        return;
      }

      const pendingReady = pendingReadyActivationRef.current;
      if (pendingReady?.generation === generation) {
        if (event.event_type === "agent_unavailable") {
          pendingReadyActivationRef.current = null;
        } else {
          if (event.event_type === "agent_ready") {
            log("Ignored duplicate agent readiness during microphone activation");
            return;
          }
          if (pendingReady.queuedEvents.length >= MAX_PENDING_READY_EVENTS) {
            pendingReadyActivationRef.current = null;
            failActiveCall(
              generation,
              "ready_event_buffer_overflow",
              "Voice received too many events before microphone activation completed"
            );
            return;
          }
          pendingReady.queuedEvents.push(event);
          return;
        }
      }

      if (event.event_type === "agent_ready") {
        const readyReduction = reduceVoiceEvent(eventStateRef.current, event);
        if (readyReduction.disposition === "applied") {
          const pending: PendingReadyActivation = {
            generation,
            event,
            reduction: readyReduction,
            queuedEvents: [],
          };
          pendingReadyActivationRef.current = pending;
          // The runtime loader owns canonical decoding. Passing this exact
          // immutable object preserves the adapter's Ready identity proof.
          void transport.activateMicrophoneAfterReady(event).then(
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
                optionsRef.current.onEvent?.(event);
              } catch (error) {
                log(`Voice event observer failed: ${errorMessage(error)}`);
              }
              runAppliedEvent(event, readyReduction.effects);
              for (const queuedEvent of pending.queuedEvents) {
                transportEventHandlerRef.current?.(queuedEvent, generation);
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

      const reduction = applyReduction(event);
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

      try {
        optionsRef.current.onEvent?.(event);
      } catch (error) {
        log(`Voice event observer failed: ${errorMessage(error)}`);
      }
      runAppliedEvent(event, reduction.effects);

      if (
        reduction.state.session.phase === "unavailable" ||
        (reduction.state.session.phase === "ended" &&
          reduction.state.session.terminationStage === "ended")
      ) {
        const mustReleaseAssignment =
          reduction.state.session.phase === "ended" ||
          event.event_type === "agent_unavailable" ||
          !reduction.state.session.unavailableReason?.retryable;
        if (mustReleaseAssignment) releaseCallIdentity();
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
    transportEventHandlerRef.current = handleTransportEvent;
    return () => {
      if (transportEventHandlerRef.current === handleTransportEvent) {
        transportEventHandlerRef.current = null;
      }
    };
  }, [handleTransportEvent]);

  const connect = useCallback(
    (overrides: VoiceSessionConnectOverrides = {}): Promise<void> => {
      const currentOperation = connectOperationRef.current;
      if (
        currentOperation &&
        currentOperation.intent === connectIntentRef.current
      ) {
        return currentOperation.promise;
      }

      const preparedTransport = transportRef.current;
      const preparedAssignment = assignmentRef.current;
      const requestedSessionId =
        overrides.sessionId ?? optionsRef.current.sessionId;
      if (
        preparedTransport &&
        preparedAssignment &&
        eventStateRef.current.session.phase === "awaiting_audio" &&
        preparedAssignment.session_id === requestedSessionId
      ) {
        const intent = connectIntentRef.current + 1;
        connectIntentRef.current = intent;
        const generation = generationRef.current;
        clearPreparedAssignmentTimer();

        const transportConnectAbort = new AbortController();
        let transportConnectTimedOut = false;
        transportConnectAbortRef.current = transportConnectAbort;
        transportConnectTimerRef.current = setTimeout(() => {
          transportConnectTimedOut = true;
          transportConnectAbort.abort();
        }, TRANSPORT_CONNECT_TIMEOUT_MS);

        let transportConnect: Promise<void>;
        try {
          // This is deliberately synchronous in the second user gesture. The
          // selected adapter already exists, so no bootstrap or import precedes
          // the browser's audio-unlock call.
          preparedTransport.primeAudioPlayback();
          publishState({
            ...eventStateRef.current,
            session: transitionVoiceSession(eventStateRef.current.session, {
              type: "connect_requested",
            }),
          });
          transportConnect = preparedTransport.connect();
        } catch (error) {
          transportConnect = Promise.reject(error);
        }

        const operation: VoiceConnectOperation = {
          intent,
          promise: Promise.resolve(),
        };
        const start = async () => {
          try {
            try {
              await settleOnAbort(
                transportConnect,
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
            }
            if (
              generationRef.current !== generation ||
              intent !== connectIntentRef.current ||
              transportRef.current !== preparedTransport
            ) {
              return;
            }
            log(
              `Connected assigned ${preparedAssignment.runtime} voice transport`
            );
          } catch (error) {
            if (generationRef.current !== generation || isAbortError(error)) {
              return;
            }
            failActiveCall(
              generation,
              "transport_connect_failed",
              `Voice transport failed: ${errorMessage(error)}`,
              { canRetry: true, retainCallIdentity: false }
            );
          } finally {
            clearTransportConnectTimer();
            if (transportConnectAbortRef.current === transportConnectAbort) {
              transportConnectAbortRef.current = null;
            }
          }
        };
        const promise = start().finally(() => {
          if (connectOperationRef.current === operation) {
            connectOperationRef.current = null;
          }
        });
        operation.promise = promise;
        connectOperationRef.current = operation;
        return promise;
      }

      const predecessor = currentOperation?.promise;
      const intent = connectIntentRef.current + 1;
      connectIntentRef.current = intent;

      const start = async () => {
        if (predecessor) await predecessor.catch(() => undefined);
        const pendingTeardown = teardownRef.current?.promise;
        if (pendingTeardown) await pendingTeardown;
        if (intent !== connectIntentRef.current || !mountedRef.current) return;
        if (!optionsRef.current.enabled) {
          log("Voice V2 is disabled for this runtime assignment");
          return;
        }
        if (transportRef.current || transportLoadRef.current) return;

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

        if (
          generationRef.current !== generation ||
          intent !== connectIntentRef.current
        ) {
          return;
        }
        if (
          bootstrap.agent_id !== optionsRef.current.agentId ||
          bootstrap.session_id !== sessionId ||
          bootstrap.voice_call_id !== activeVoiceCallId
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

        let transport: VoiceTransport | null = null;
        const isCurrentTransport = () =>
          generationRef.current === generation &&
          assignmentRef.current === bootstrap &&
          (transportRef.current === null || transportRef.current === transport);
        const failInvalidEvent = (rejection: VoiceTransportEventRejection) => {
          if (!isCurrentTransport()) return;
          const message =
            rejection.code === "invalid_event_channel"
              ? "Voice received data outside its authenticated reliable event channel"
              : rejection.code === "event_scope_mismatch"
                ? "Voice received an event outside its assigned trace"
                : rejection.message;
          const nextState: VoiceEventState = {
            ...eventStateRef.current,
            session: transitionVoiceSession(eventStateRef.current.session, {
              type: "compatibility_error",
              code: rejection.code,
              message,
            }),
            compatibilityFailure: {
              code: rejection.code,
              message,
              ...(rejection.event_id ? { eventId: rejection.event_id } : {}),
              ...(rejection.event_type
                ? { eventType: rejection.event_type }
                : {}),
            },
          };
          publishState(nextState);
          releaseCallIdentity();
          optionsRef.current.onError?.(message);
          void cleanupTransport(true);
        };
        const transportLoader =
          optionsRef.current.transportLoader ?? loadVoiceTransport;
        const transportLoadPromise = Promise.resolve().then(() =>
          transportLoader(bootstrap, {
            ttsEnabled: ttsEnabledRef.current,
            authHeaderProvider: optionsRef.current.authHeaderProvider,
            callbacks: {
              isCurrent: isCurrentTransport,
              onConnected: () => {
                if (!isCurrentTransport()) return;
                const reduction = applyLocalEvent("transport_connected", {});
                if (reduction.disposition !== "applied") return;
                log(
                  "Transport connected; waiting for genuine agent readiness"
                );
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
              onFreshCallRequired: () => {
                if (!isCurrentTransport()) return;
                failActiveCall(
                  generation,
                  "fresh_call_required",
                  "Voice transport requires a fresh call. Start voice again.",
                  { canRetry: true, retainCallIdentity: false }
                );
              },
              onEvent: (event) => {
                if (!isCurrentTransport()) return;
                handleTransportEvent(event, generation);
              },
              onInvalidEvent: failInvalidEvent,
              onTransportError: (error) => {
                if (!isCurrentTransport()) return;
                const reconnect = error.message.includes(
                  "reconnect requires a fresh call"
                );
                failActiveCall(
                  generation,
                  reconnect
                    ? "reconnect_not_supported"
                    : "transport_runtime_error",
                  reconnect
                    ? "Voice transport connection was interrupted. Start a fresh voice call."
                    : `Voice transport failed: ${error.message}`,
                  { canRetry: true, retainCallIdentity: false }
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
              onLocalMicrophoneDiagnostic: (
                track,
                diagnostic: VoiceLocalMicrophoneDiagnostic
              ) => {
                if (
                  !isCurrentTransport() ||
                  diagnostic.runtime !== "livekit_v2" ||
                  diagnostic.kind !== "publication"
                ) {
                  return;
                }
                optionsRef.current.onLocalMicrophonePublication?.(
                  track,
                  diagnostic.observation
                );
              },
            },
          })
        );
        const transportLoad: VoiceTransportLoad = {
          promise: transportLoadPromise,
          settlement: settleBounded(
            transportLoadPromise,
            TRANSPORT_LOAD_TIMEOUT_MS
          ),
          lateCleanupScheduled: false,
        };
        transportLoadRef.current = transportLoad;
        const transportLoadSettlement = await transportLoad.settlement;
        if (transportLoadSettlement.status !== "fulfilled") {
          if (generationRef.current !== generation) return;
          const timedOut = transportLoadSettlement.status === "timed_out";
          failActiveCall(
            generation,
            timedOut ? "transport_load_timeout" : "transport_load_failed",
            timedOut
              ? "Voice transport loading timed out. Start a fresh voice call."
              : `Voice transport could not be loaded: ${errorMessage(
                  transportLoadSettlement.reason
                )}`,
            { canRetry: true, retainCallIdentity: false }
          );
          return;
        }
        transport = transportLoadSettlement.value;
        if (transportLoadRef.current === transportLoad) {
          transportLoadRef.current = null;
        }

        if (!isCurrentTransport()) {
          const teardown = teardownRef.current?.promise;
          if (teardown) await teardown;
          return;
        }
        transportRef.current = transport;
        publishState({
          ...eventStateRef.current,
          session: transitionVoiceSession(eventStateRef.current.session, {
            type: "transport_prepared",
          }),
        });
        clearPreparedAssignmentTimer();
        preparedAssignmentTimerRef.current = setTimeout(() => {
          preparedAssignmentTimerRef.current = null;
          if (
            generationRef.current !== generation ||
            transportRef.current !== transport ||
            assignmentRef.current !== bootstrap ||
            eventStateRef.current.session.phase !== "awaiting_audio"
          ) {
            return;
          }
          failActiveCall(
            generation,
            "prepared_assignment_timeout",
            "Voice start confirmation timed out. Start a fresh voice call.",
            { canRetry: true, retainCallIdentity: false }
          );
        }, PREPARED_ASSIGNMENT_TIMEOUT_MS);
        log(`Prepared assigned ${bootstrap.runtime} voice transport`);
      };

      const operation: VoiceConnectOperation = {
        intent,
        promise: Promise.resolve(),
      };
      const promise = start().finally(() => {
        if (connectOperationRef.current === operation) {
          connectOperationRef.current = null;
        }
      });
      operation.promise = promise;
      connectOperationRef.current = operation;
      return promise;
    },
    [
      applyLocalEvent,
      cleanupTransport,
      clearBootstrapTimer,
      clearPreparedAssignmentTimer,
      clearTransportConnectTimer,
      clearReadyTimer,
      failActiveCall,
      handleTransportEvent,
      log,
      publishState,
      releaseCallIdentity,
      releaseScheduler,
      stateWriters,
    ]
  );

  const disconnect = useCallback((): Promise<void> => {
    connectIntentRef.current += 1;
    releaseCallIdentity();
    const nextState: VoiceEventState = {
      ...eventStateRef.current,
      session: transitionVoiceSession(eventStateRef.current.session, {
        type: "end_requested",
      }),
    };
    publishState(nextState);
    const teardown = cleanupTransport(true);
    log("Voice ended");
    return teardown;
  }, [cleanupTransport, log, publishState, releaseCallIdentity]);

  const cancelConnection = useCallback((): Promise<void> => {
    connectIntentRef.current += 1;
    releaseCallIdentity();
    const teardown = cleanupTransport(true);
    publishState(createInitialVoiceEventState(eventStateRef.current.sessionId));
    log("Voice connection cancelled");
    return teardown;
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
    if (
      !options.enabled &&
      (transportRef.current || transportLoadRef.current || assignmentRef.current)
    ) {
      connectIntentRef.current += 1;
      releaseCallIdentity();
      void cleanupTransport(true);
    }
  }, [cleanupTransport, options.enabled, releaseCallIdentity]);

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
