export interface RuntimePresentationMetricsSnapshot {
  readonly requestToFirstPresentedMs?: number;
  readonly requestToSettledMs?: number;
  readonly interruptToSettledMs?: number;
  readonly replayDurationMs?: number;
}

export interface RuntimePresentationMetricsState {
  readonly snapshot?: RuntimePresentationMetricsSnapshot;
  readonly requestStartedAt?: number;
  readonly interruptStartedAt?: number;
  readonly replayStartedAt?: number;
}

const EMPTY_STATE: RuntimePresentationMetricsState = Object.freeze({});

function assertTimestamp(value: number): void {
  if (!Number.isFinite(value)) {
    throw new RangeError("Presentation timing requires a finite timestamp");
  }
}

function elapsed(startedAt: number, settledAt: number): number {
  assertTimestamp(settledAt);
  return Math.max(0, settledAt - startedAt);
}

function freezeSnapshot(
  snapshot: RuntimePresentationMetricsSnapshot
): RuntimePresentationMetricsSnapshot {
  return Object.freeze({ ...snapshot });
}

function freezeState(
  state: RuntimePresentationMetricsState
): RuntimePresentationMetricsState {
  return Object.freeze(state);
}

export function createRuntimePresentationMetricsState(): RuntimePresentationMetricsState {
  return EMPTY_STATE;
}

export function beginPresentationRequest(
  at: number
): RuntimePresentationMetricsState {
  assertTimestamp(at);
  return freezeState({
    snapshot: freezeSnapshot({}),
    requestStartedAt: at,
  });
}

export function markFirstPresented(
  state: RuntimePresentationMetricsState,
  at: number
): RuntimePresentationMetricsState {
  if (
    state.requestStartedAt === undefined ||
    state.snapshot?.requestToFirstPresentedMs !== undefined
  ) {
    return state;
  }
  return freezeState({
    ...state,
    snapshot: freezeSnapshot({
      ...state.snapshot,
      requestToFirstPresentedMs: elapsed(state.requestStartedAt, at),
    }),
  });
}

export function beginPresentationInterrupt(
  state: RuntimePresentationMetricsState,
  at: number
): RuntimePresentationMetricsState {
  if (state.interruptStartedAt !== undefined) return state;
  assertTimestamp(at);
  return freezeState({
    ...state,
    snapshot: state.snapshot ?? freezeSnapshot({}),
    interruptStartedAt: at,
  });
}

export function beginPresentationReplay(
  state: RuntimePresentationMetricsState,
  at: number
): RuntimePresentationMetricsState {
  assertTimestamp(at);
  return freezeState({
    ...state,
    snapshot: state.snapshot ?? freezeSnapshot({}),
    replayStartedAt: at,
  });
}

/** Close whichever browser-observed action is active at a true runtime terminal. */
export function settlePresentationMetrics(
  state: RuntimePresentationMetricsState,
  at: number
): RuntimePresentationMetricsState {
  const requestStartedAt = state.requestStartedAt;
  const interruptStartedAt = state.interruptStartedAt;
  const replayStartedAt = state.replayStartedAt;
  if (
    requestStartedAt === undefined &&
    interruptStartedAt === undefined &&
    replayStartedAt === undefined
  ) {
    return state;
  }

  const snapshot = freezeSnapshot({
    ...state.snapshot,
    ...(requestStartedAt !== undefined
      ? { requestToSettledMs: elapsed(requestStartedAt, at) }
      : {}),
    ...(interruptStartedAt !== undefined
      ? { interruptToSettledMs: elapsed(interruptStartedAt, at) }
      : {}),
    ...(replayStartedAt !== undefined
      ? { replayDurationMs: elapsed(replayStartedAt, at) }
      : {}),
  });
  return freezeState({ snapshot });
}

export function resetPresentationMetrics(): RuntimePresentationMetricsState {
  return EMPTY_STATE;
}
