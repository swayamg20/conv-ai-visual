import {
  COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  type ChoreographySceneCheckpointEvent,
  type ChoreographySceneStreamRunner,
  type CompletingSquareMainCheckpoint,
} from "@/features/live-scene/choreography-model-stream";
import {
  createChoreographySceneFixtureEvents,
  createChoreographySceneFixtureRunner,
} from "@/features/live-scene/choreography-scene-stream-fixture";
import type { ChoreographyEvidenceTraceEvent } from "@/features/live-scene/choreography-playback";
import type {
  LiveChoreographyCaptureControl,
  LiveChoreographyCaptureInterruptRequest,
  LiveChoreographyCaptureInterruptResult,
  LiveChoreographyReplayObservation,
} from "@/features/live-scene/live-choreography-demo";

import type { ChoreographyCapturePace } from "./capture-options";

export const CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION = 1 as const;
export const CHOREOGRAPHY_CAPTURE_BRIDGE_KEY =
  "__MURMUR_CHOREOGRAPHY_CAPTURE__" as const;

export interface ChoreographyCaptureCheckpoint {
  readonly generation: number;
  readonly sequence: number;
  readonly checkpointId: CompletingSquareMainCheckpoint;
}

export interface ChoreographyCaptureWaitingCheckpoint extends ChoreographyCaptureCheckpoint {
  readonly openedAtMs: number;
}

interface ChoreographyCaptureGateState {
  readonly waitingFor: ChoreographyCaptureWaitingCheckpoint | null;
  readonly acknowledgedThrough: number;
}

export interface ChoreographyCaptureStateV1 extends ChoreographyCaptureGateState {
  readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
}

export interface ChoreographyCaptureBridgeV1 {
  readonly version: typeof CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION;
  readonly pace: ChoreographyCapturePace;
  getState(): ChoreographyCaptureStateV1;
  acknowledgeCheckpoint(value: unknown): void;
  interruptCheckpoint(
    value: unknown,
  ): Promise<LiveChoreographyCaptureInterruptResult>;
  replayAccepted(): Promise<LiveChoreographyReplayObservation>;
}

export interface ChoreographyCaptureSession {
  readonly runner: ChoreographySceneStreamRunner;
  readonly bridge: ChoreographyCaptureBridgeV1;
  readonly updateEvidence: (
    evidence: readonly ChoreographyEvidenceTraceEvent[],
  ) => void;
  readonly attachControl: (
    control: LiveChoreographyCaptureControl | null,
  ) => void;
}

interface PendingCheckpoint {
  readonly expected: ChoreographyCaptureCheckpoint;
  readonly openedAtMs: number;
  readonly signal: AbortSignal;
  readonly onAbort: () => void;
  readonly resolve: () => void;
}

export interface ChoreographyCaptureRendezvous {
  getState(): ChoreographyCaptureGateState;
  acknowledgeCheckpoint(value: unknown): void;
  waitForCheckpoint(
    expected: ChoreographyCaptureCheckpoint,
    signal: AbortSignal,
    openedAtMs?: number,
  ): Promise<void>;
}

type UnknownRecord = Record<string, unknown>;

function abortError(): DOMException {
  return new DOMException(
    "The capture checkpoint wait was aborted",
    "AbortError",
  );
}

function record(value: unknown, field: string): UnknownRecord {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    (Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null)
  ) {
    throw new TypeError(`${field} must be a plain object`);
  }
  return value as UnknownRecord;
}

function decodeCheckpoint(
  value: unknown,
  field = "checkpoint acknowledgement",
): ChoreographyCaptureCheckpoint {
  const input = record(value, field);
  const keys = Object.keys(input).sort();
  if (keys.join(",") !== "checkpointId,generation,sequence") {
    throw new TypeError(
      `${field} must contain exactly checkpointId, generation, sequence`,
    );
  }
  if (
    !Number.isSafeInteger(input.generation) ||
    (input.generation as number) < 1
  ) {
    throw new TypeError(`${field} generation must be a positive safe integer`);
  }
  if (
    !Number.isSafeInteger(input.sequence) ||
    (input.sequence as number) < 1 ||
    (input.sequence as number) > COMPLETING_SQUARE_MAIN_CHECKPOINTS.length
  ) {
    throw new TypeError(
      `${field} sequence must be between 1 and ${COMPLETING_SQUARE_MAIN_CHECKPOINTS.length}`,
    );
  }
  const checkpointId = COMPLETING_SQUARE_MAIN_CHECKPOINTS.find(
    (candidate) => candidate === input.checkpointId,
  );
  if (!checkpointId) {
    throw new TypeError(`${field} checkpointId is not a main checkpoint`);
  }
  return Object.freeze({
    generation: input.generation as number,
    sequence: input.sequence as number,
    checkpointId,
  });
}

function decodeInterruptRequest(
  value: unknown,
): LiveChoreographyCaptureInterruptRequest {
  const field = "checkpoint interruption";
  const input = record(value, field);
  const keys = Object.keys(input).sort();
  if (
    keys.join(",") !==
    "certificateSha256,checkpointId,delayAfterPresentedMs,generation,sequence"
  ) {
    throw new TypeError(
      `${field} must contain exactly certificateSha256, checkpointId, delayAfterPresentedMs, generation, sequence`,
    );
  }
  const checkpoint = decodeCheckpoint(
    {
      generation: input.generation,
      sequence: input.sequence,
      checkpointId: input.checkpointId,
    },
    field,
  );
  if (
    typeof input.certificateSha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(input.certificateSha256)
  ) {
    throw new TypeError(`${field} certificateSha256 must be a SHA-256 digest`);
  }
  if (
    !Number.isSafeInteger(input.delayAfterPresentedMs) ||
    (input.delayAfterPresentedMs as number) < 0 ||
    (input.delayAfterPresentedMs as number) > 1_000
  ) {
    throw new TypeError(
      `${field} delayAfterPresentedMs must be between 0 and 1000`,
    );
  }
  return Object.freeze({
    ...checkpoint,
    certificateSha256: input.certificateSha256,
    delayAfterPresentedMs: input.delayAfterPresentedMs as number,
  });
}

function sameCheckpoint(
  left: ChoreographyCaptureCheckpoint,
  right: ChoreographyCaptureCheckpoint,
): boolean {
  return (
    left.generation === right.generation &&
    left.sequence === right.sequence &&
    left.checkpointId === right.checkpointId
  );
}

function checkpointTuple(
  event: ChoreographySceneCheckpointEvent,
): ChoreographyCaptureCheckpoint {
  return decodeCheckpoint(
    {
      generation: event.generation,
      sequence: event.sequence,
      checkpointId: event.semantic.checkpointId,
    },
    "fixture checkpoint",
  );
}

/** One in-order checkpoint wait. Early, duplicate, and mismatched acks fail closed. */
export function createChoreographyCaptureRendezvous(): ChoreographyCaptureRendezvous {
  let pending: PendingCheckpoint | null = null;
  let acknowledgedThrough = 0;

  const getState = (): ChoreographyCaptureGateState =>
    Object.freeze({
      waitingFor: pending
        ? Object.freeze({ ...pending.expected, openedAtMs: pending.openedAtMs })
        : null,
      acknowledgedThrough,
    });

  const acknowledgeCheckpoint = (value: unknown): void => {
    const acknowledged = decodeCheckpoint(value);
    const current = pending;
    if (!current) {
      throw new Error("No capture checkpoint is awaiting acknowledgement");
    }
    if (!sameCheckpoint(acknowledged, current.expected)) {
      throw new Error(
        "Checkpoint acknowledgement does not match the pending tuple",
      );
    }
    pending = null;
    acknowledgedThrough = acknowledged.sequence;
    current.signal.removeEventListener("abort", current.onAbort);
    current.resolve();
  };

  const waitForCheckpoint = (
    expectedValue: ChoreographyCaptureCheckpoint,
    signal: AbortSignal,
    openedAtMs = globalThis.performance.now(),
  ): Promise<void> => {
    if (pending) {
      throw new Error(
        "A capture checkpoint is already awaiting acknowledgement",
      );
    }
    const expected = decodeCheckpoint(expectedValue, "expected checkpoint");
    if (expected.sequence !== acknowledgedThrough + 1) {
      throw new Error("Capture checkpoints must open in exact sequence order");
    }
    if (!Number.isFinite(openedAtMs) || openedAtMs < 0) {
      throw new TypeError(
        "capture checkpoint openedAtMs must be nonnegative and finite",
      );
    }
    if (signal.aborted) return Promise.reject(abortError());

    return new Promise<void>((resolve, reject) => {
      const onAbort = (): void => {
        if (pending?.onAbort !== onAbort) return;
        pending = null;
        reject(abortError());
      };
      pending = Object.freeze({
        expected,
        openedAtMs,
        signal,
        onAbort,
        resolve,
      });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  };

  return Object.freeze({ getState, acknowledgeCheckpoint, waitForCheckpoint });
}

/** Emit the exact main fixture, pausing after each checkpoint before its successor. */
export function createStepChoreographyCaptureRunner(
  rendezvous: ChoreographyCaptureRendezvous,
): ChoreographySceneStreamRunner {
  return async ({ request, signal, onEvent }) => {
    const events = createChoreographySceneFixtureEvents(request, "main");
    for (const event of events) {
      if (signal.aborted) throw abortError();
      const openedAtMs = globalThis.performance.now();
      onEvent(event);
      if (event.type === "choreography_scene_checkpoint") {
        await rendezvous.waitForCheckpoint(
          checkpointTuple(event),
          signal,
          openedAtMs,
        );
      }
    }
  };
}

/** Compose the route-only runner and its immutable automation bridge. */
export function createChoreographyCaptureSession(
  pace: ChoreographyCapturePace,
): ChoreographyCaptureSession {
  if (pace !== "auto" && pace !== "step") {
    throw new TypeError("capture pace must be auto or step");
  }
  let evidence: readonly ChoreographyEvidenceTraceEvent[] = Object.freeze([]);
  let control: LiveChoreographyCaptureControl | null = null;
  const attachControl = (next: LiveChoreographyCaptureControl | null): void => {
    control = next;
  };
  const requireControl = (): LiveChoreographyCaptureControl => {
    if (!control) {
      throw new Error("The choreography capture control is unavailable");
    }
    return control;
  };
  const controlBridge = Object.freeze({
    interruptCheckpoint: (
      value: unknown,
    ): Promise<LiveChoreographyCaptureInterruptResult> => {
      const request = decodeInterruptRequest(value);
      return requireControl().interruptCheckpoint(request);
    },
    replayAccepted: (): Promise<LiveChoreographyReplayObservation> =>
      requireControl().replayAccepted(),
  });
  const updateEvidence = (
    next: readonly ChoreographyEvidenceTraceEvent[],
  ): void => {
    evidence = Object.freeze(
      next.map((event) => Object.freeze({ ...event })),
    ) as readonly ChoreographyEvidenceTraceEvent[];
  };
  const withEvidence = (
    state: ChoreographyCaptureGateState,
  ): ChoreographyCaptureStateV1 => Object.freeze({ ...state, evidence });

  if (pace === "auto") {
    return Object.freeze({
      runner: createChoreographySceneFixtureRunner({ mode: "main" }),
      updateEvidence,
      attachControl,
      bridge: Object.freeze({
        version: CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION,
        pace,
        getState: () =>
          withEvidence(
            Object.freeze({ waitingFor: null, acknowledgedThrough: 0 }),
          ),
        acknowledgeCheckpoint: (_value: unknown): void => {
          throw new Error(
            "Automatic capture does not accept checkpoint acknowledgements",
          );
        },
        ...controlBridge,
      }),
    });
  }

  const rendezvous = createChoreographyCaptureRendezvous();
  return Object.freeze({
    runner: createStepChoreographyCaptureRunner(rendezvous),
    updateEvidence,
    attachControl,
    bridge: Object.freeze({
      version: CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION,
      pace,
      getState: () => withEvidence(rendezvous.getState()),
      acknowledgeCheckpoint: rendezvous.acknowledgeCheckpoint,
      ...controlBridge,
    }),
  });
}
