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

import type { ChoreographyCapturePace } from "./capture-options";

export const CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION = 1 as const;
export const CHOREOGRAPHY_CAPTURE_BRIDGE_KEY =
  "__MURMUR_CHOREOGRAPHY_CAPTURE__" as const;

export interface ChoreographyCaptureCheckpoint {
  readonly generation: number;
  readonly sequence: number;
  readonly checkpointId: CompletingSquareMainCheckpoint;
}

export interface ChoreographyCaptureBridgeV1 {
  readonly version: typeof CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION;
  readonly pace: ChoreographyCapturePace;
  acknowledgeCheckpoint(value: unknown): void;
}

export interface ChoreographyCaptureSession {
  readonly runner: ChoreographySceneStreamRunner;
  readonly bridge: ChoreographyCaptureBridgeV1;
}

interface PendingCheckpoint {
  readonly expected: ChoreographyCaptureCheckpoint;
  readonly signal: AbortSignal;
  readonly onAbort: () => void;
  readonly resolve: () => void;
}

export interface ChoreographyCaptureRendezvous {
  acknowledgeCheckpoint(value: unknown): void;
  waitForCheckpoint(
    expected: ChoreographyCaptureCheckpoint,
    signal: AbortSignal,
  ): Promise<void>;
}

type UnknownRecord = Record<string, unknown>;

function abortError(): DOMException {
  return new DOMException(
    "The capture checkpoint wait was aborted",
    "AbortError",
  );
}

function record(value: unknown): UnknownRecord {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    (Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null)
  ) {
    throw new TypeError("checkpoint acknowledgement must be a plain object");
  }
  return value as UnknownRecord;
}

function decodeCheckpoint(
  value: unknown,
  field = "checkpoint acknowledgement",
): ChoreographyCaptureCheckpoint {
  const input = record(value);
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
    current.signal.removeEventListener("abort", current.onAbort);
    current.resolve();
  };

  const waitForCheckpoint = (
    expectedValue: ChoreographyCaptureCheckpoint,
    signal: AbortSignal,
  ): Promise<void> => {
    if (pending) {
      throw new Error(
        "A capture checkpoint is already awaiting acknowledgement",
      );
    }
    const expected = decodeCheckpoint(expectedValue, "expected checkpoint");
    if (signal.aborted) return Promise.reject(abortError());

    return new Promise<void>((resolve, reject) => {
      const onAbort = (): void => {
        if (pending?.onAbort !== onAbort) return;
        pending = null;
        reject(abortError());
      };
      pending = Object.freeze({ expected, signal, onAbort, resolve });
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  };

  return Object.freeze({ acknowledgeCheckpoint, waitForCheckpoint });
}

/** Emit the exact main fixture, pausing after each checkpoint before its successor. */
export function createStepChoreographyCaptureRunner(
  rendezvous: ChoreographyCaptureRendezvous,
): ChoreographySceneStreamRunner {
  return async ({ request, signal, onEvent }) => {
    const events = createChoreographySceneFixtureEvents(request, "main");
    for (const event of events) {
      if (signal.aborted) throw abortError();
      onEvent(event);
      if (event.type === "choreography_scene_checkpoint") {
        await rendezvous.waitForCheckpoint(checkpointTuple(event), signal);
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
  if (pace === "auto") {
    return Object.freeze({
      runner: createChoreographySceneFixtureRunner({ mode: "main" }),
      bridge: Object.freeze({
        version: CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION,
        pace,
        acknowledgeCheckpoint: (_value: unknown): void => {
          throw new Error(
            "Automatic capture does not accept checkpoint acknowledgements",
          );
        },
      }),
    });
  }

  const rendezvous = createChoreographyCaptureRendezvous();
  return Object.freeze({
    runner: createStepChoreographyCaptureRunner(rendezvous),
    bridge: Object.freeze({
      version: CHOREOGRAPHY_CAPTURE_BRIDGE_VERSION,
      pace,
      acknowledgeCheckpoint: rendezvous.acknowledgeCheckpoint,
    }),
  });
}
