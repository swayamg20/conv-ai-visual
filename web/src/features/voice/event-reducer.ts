/** Deterministic Voice V2 event reduction, ordering, idempotency, and canvas gating. */

import {
  decodeVoiceEvent,
  type EventOf,
  type JsonObject,
  type VoiceEvent,
  type VoiceEventDecodeError,
  type VoiceEventType,
} from "./events";
import {
  createInitialVoiceSessionState,
  transitionVoiceSession,
  type VoiceSessionState,
} from "./session-machine";

export type VoiceTaskStatus =
  | "queued"
  | "working"
  | "needs_input"
  | "verified"
  | "failed"
  | "cancelled"
  | "superseded";

export interface VoiceTaskView {
  readonly taskId: string;
  readonly generation: number;
  readonly status: VoiceTaskStatus;
  readonly lastEventId: string;
}

export interface VoiceProducerCursor {
  readonly producerId: string;
  readonly sequence: number;
}

export interface PendingCanvasPatch {
  readonly eventId: string;
  readonly artifactId: string;
  readonly artifact: JsonObject;
  readonly baseRevision: number;
  readonly resultRevision: number;
  readonly taskId: string;
  readonly taskGeneration: number;
}

export interface CanvasRenderFailure {
  readonly eventId: string;
  readonly artifactId: string;
  readonly revision: number;
  readonly code: string;
  readonly message: string;
}

export interface AppliedCanvasPatch {
  readonly patchEventId: string;
  readonly acknowledgementEventId: string;
  readonly artifactId: string;
  readonly revision: number;
  readonly taskId: string;
  readonly taskGeneration: number;
  readonly firstVisibleEventId?: string;
}

export interface VoiceCanvasState {
  readonly appliedRevision: number;
  readonly visibleRevision: number;
  readonly pendingPatch?: PendingCanvasPatch;
  readonly lastAppliedPatch?: AppliedCanvasPatch;
  readonly lastRenderFailure?: CanvasRenderFailure;
}

export interface VoiceCompatibilityFailure {
  readonly code: string;
  readonly message: string;
  readonly eventId?: string;
  readonly eventType?: string;
}

export interface VoiceEventState {
  readonly voiceCallId?: string;
  readonly sessionId?: string;
  readonly session: VoiceSessionState;
  readonly seenEventIds: readonly string[];
  readonly producerCursors: readonly VoiceProducerCursor[];
  readonly lastLedgerSequence?: number;
  readonly lastAppliedEventId?: string;
  readonly tasks: readonly VoiceTaskView[];
  readonly canvas: VoiceCanvasState;
  /** Locks reduction after a schema/type/session compatibility failure. */
  readonly compatibilityFailure?: VoiceCompatibilityFailure;
}

export type VoiceEventEffect = {
  readonly type: "apply_canvas_patch";
  readonly event: EventOf<"canvas_patch">;
};

export type VoiceEventDisposition = "applied" | "duplicate" | "stale" | "rejected";

export type VoiceEventRejectionCode =
  | "compatibility_locked"
  | "voice_call_mismatch"
  | "session_mismatch"
  | "session_ended"
  | "producer_sequence_regression"
  | "ledger_sequence_regression"
  | "stale_task_generation"
  | "invalid_task_transition"
  | "stale_canvas_revision"
  | "canvas_patch_in_flight"
  | "unknown_canvas_patch"
  | "canvas_causation_mismatch"
  | "canvas_revision_ahead"
  | "task_not_publishable"
  | "protocol_state_violation";

export interface VoiceEventRejection {
  readonly code: VoiceEventRejectionCode | VoiceEventDecodeError["code"];
  readonly message: string;
  readonly eventId?: string;
  readonly eventType?: string;
}

export interface VoiceEventReduction {
  readonly state: VoiceEventState;
  readonly disposition: VoiceEventDisposition;
  readonly effects: readonly VoiceEventEffect[];
  readonly rejection?: VoiceEventRejection;
}

interface SemanticReduction {
  readonly tasks: readonly VoiceTaskView[];
  readonly canvas: VoiceCanvasState;
  readonly effects: readonly VoiceEventEffect[];
  readonly rejection?: VoiceEventRejection;
  readonly stale?: boolean;
}

interface TaskSemanticReduction {
  readonly tasks: readonly VoiceTaskView[];
  readonly rejection?: VoiceEventRejection;
  readonly stale?: boolean;
}

const taskStatusByEventType: Readonly<Partial<Record<VoiceEventType, VoiceTaskStatus>>> = {
  task_queued: "queued",
  task_working: "working",
  task_needs_input: "needs_input",
  task_verified: "verified",
  task_failed: "failed",
  task_cancelled: "cancelled",
  task_superseded: "superseded",
};

const terminalTaskStatuses: ReadonlySet<VoiceTaskStatus> = new Set([
  "verified",
  "failed",
  "cancelled",
  "superseded",
]);

const publishBlockingTaskStatuses: ReadonlySet<VoiceTaskStatus> = new Set([
  "failed",
  "cancelled",
  "superseded",
]);

const allowedTaskTransitions: Readonly<
  Record<VoiceTaskStatus, ReadonlySet<VoiceTaskStatus>>
> = {
  queued: new Set(["working", "needs_input", "failed", "cancelled", "superseded"]),
  working: new Set(["needs_input", "verified", "failed", "cancelled", "superseded"]),
  needs_input: new Set(["working", "failed", "cancelled", "superseded"]),
  verified: new Set(),
  failed: new Set(),
  cancelled: new Set(),
  superseded: new Set(),
};

export function createInitialVoiceEventState(
  sessionId?: string,
  voiceCallId?: string
): VoiceEventState {
  return {
    ...(voiceCallId ? { voiceCallId } : {}),
    ...(sessionId ? { sessionId } : {}),
    session: createInitialVoiceSessionState(),
    seenEventIds: [],
    producerCursors: [],
    tasks: [],
    canvas: {
      appliedRevision: 0,
      visibleRevision: 0,
    },
  };
}

function reject(
  state: VoiceEventState,
  disposition: Extract<VoiceEventDisposition, "stale" | "rejected">,
  rejection: VoiceEventRejection
): VoiceEventReduction {
  return { state, disposition, effects: [], rejection };
}

function decodeFailure(
  state: VoiceEventState,
  error: VoiceEventDecodeError
): VoiceEventReduction {
  const compatibilityFailure: VoiceCompatibilityFailure = {
    code: error.code,
    message: error.message,
    ...(error.event_id ? { eventId: error.event_id } : {}),
    ...(error.event_type ? { eventType: error.event_type } : {}),
  };
  const nextState: VoiceEventState = {
    ...state,
    session: transitionVoiceSession(state.session, {
      type: "compatibility_error",
      code: error.code,
      message: error.message,
    }),
    compatibilityFailure,
  };
  return {
    state: nextState,
    disposition: "rejected",
    effects: [],
    rejection: {
      code: error.code,
      message: error.message,
      ...(error.event_id ? { eventId: error.event_id } : {}),
      ...(error.event_type ? { eventType: error.event_type } : {}),
    },
  };
}

function compatibilityFailure(
  state: VoiceEventState,
  event: VoiceEvent,
  code: VoiceEventRejectionCode,
  message: string
): VoiceEventReduction {
  const failure: VoiceCompatibilityFailure = {
    code,
    message,
    eventId: event.event_id,
    eventType: event.event_type,
  };
  return {
    state: {
      ...state,
      session: transitionVoiceSession(state.session, {
        type: "compatibility_error",
        code,
        message,
      }),
      compatibilityFailure: failure,
    },
    disposition: "rejected",
    effects: [],
    rejection: {
      code,
      message,
      eventId: event.event_id,
      eventType: event.event_type,
    },
  };
}

function producerSequence(state: VoiceEventState, producerId: string): number | undefined {
  return state.producerCursors.find((cursor) => cursor.producerId === producerId)?.sequence;
}

function advanceProducerCursor(
  cursors: readonly VoiceProducerCursor[],
  event: VoiceEvent
): readonly VoiceProducerCursor[] {
  const index = cursors.findIndex((cursor) => cursor.producerId === event.producer_id);
  const nextCursor = {
    producerId: event.producer_id,
    sequence: event.producer_sequence,
  };
  if (index < 0) {
    return [...cursors, nextCursor];
  }
  return cursors.map((cursor, cursorIndex) =>
    cursorIndex === index ? nextCursor : cursor
  );
}

function replaceTask(
  tasks: readonly VoiceTaskView[],
  replacement: VoiceTaskView
): readonly VoiceTaskView[] {
  const index = tasks.findIndex((task) => task.taskId === replacement.taskId);
  if (index < 0) {
    return [...tasks, replacement];
  }
  return tasks.map((task, taskIndex) => (taskIndex === index ? replacement : task));
}

function validTaskTransition(previous: VoiceTaskStatus, next: VoiceTaskStatus): boolean {
  return allowedTaskTransitions[previous].has(next);
}

function reduceTaskEvent(
  tasks: readonly VoiceTaskView[],
  event: VoiceEvent
): TaskSemanticReduction | undefined {
  const status = taskStatusByEventType[event.event_type];
  if (!status) {
    return undefined;
  }

  const taskId = event.task_id;
  const generation = event.task_generation;
  if (taskId === undefined || generation === undefined) {
    return {
      tasks,
      rejection: {
        code: "invalid_task_transition",
        message: `${event.event_type} did not carry its required task identity`,
        eventId: event.event_id,
        eventType: event.event_type,
      },
    };
  }

  const existing = tasks.find((task) => task.taskId === taskId);
  if (existing && generation < existing.generation) {
    return {
      tasks,
      stale: true,
      rejection: {
        code: "stale_task_generation",
        message: `Task ${taskId} generation ${generation} is older than ${existing.generation}`,
        eventId: event.event_id,
        eventType: event.event_type,
      },
    };
  }
  if ((!existing || generation > existing.generation) && status !== "queued") {
    return {
      tasks,
      rejection: {
        code: "invalid_task_transition",
        message: `Task ${taskId} generation ${generation} must begin in queued`,
        eventId: event.event_id,
        eventType: event.event_type,
      },
    };
  }
  if (
    existing &&
    generation === existing.generation &&
    !validTaskTransition(existing.status, status)
  ) {
    return {
      tasks,
      stale: terminalTaskStatuses.has(existing.status),
      rejection: {
        code: "invalid_task_transition",
        message: `Task ${taskId} cannot transition from ${existing.status} to ${status}`,
        eventId: event.event_id,
        eventType: event.event_type,
      },
    };
  }

  return {
    tasks: replaceTask(tasks, {
      taskId,
      generation,
      status,
      lastEventId: event.event_id,
    }),
  };
}

function canvasRejection(
  tasks: readonly VoiceTaskView[],
  canvas: VoiceCanvasState,
  event: VoiceEvent,
  code: VoiceEventRejectionCode,
  message: string,
  stale = false
): SemanticReduction {
  return {
    tasks,
    canvas,
    effects: [],
    stale,
    rejection: {
      code,
      message,
      eventId: event.event_id,
      eventType: event.event_type,
    },
  };
}

function taskAllowsCanvasMutation(
  tasks: readonly VoiceTaskView[],
  event: Extract<
    VoiceEvent,
    {
      readonly event_type:
        | "canvas_patch"
        | "canvas_apply_ack"
        | "canvas_first_visible"
        | "canvas_animation_complete"
        | "canvas_render_failed";
    }
  >,
  taskId: string,
  taskGeneration: number
): VoiceEventRejection | undefined {
  const task = tasks.find((candidate) => candidate.taskId === taskId);
  if (!task) {
    return {
      code: "task_not_publishable",
      message: `${event.event_type} references unknown task ${taskId}`,
      eventId: event.event_id,
      eventType: event.event_type,
    };
  }
  if (taskGeneration < task.generation) {
    return {
      code: "stale_task_generation",
      message: `${event.event_type} belongs to stale task generation ${taskGeneration}`,
      eventId: event.event_id,
      eventType: event.event_type,
    };
  }
  if (taskGeneration > task.generation) {
    return {
      code: "task_not_publishable",
      message: `${event.event_type} generation ${taskGeneration} has not been observed for task ${task.taskId}`,
      eventId: event.event_id,
      eventType: event.event_type,
    };
  }
  if (
    taskGeneration === task.generation &&
    publishBlockingTaskStatuses.has(task.status)
  ) {
    return {
      code: "task_not_publishable",
      message: `${event.event_type} belongs to ${task.status} task ${task.taskId}`,
      eventId: event.event_id,
      eventType: event.event_type,
    };
  }
  return undefined;
}

function reduceCanvasEvent(
  tasks: readonly VoiceTaskView[],
  canvas: VoiceCanvasState,
  event: VoiceEvent
): SemanticReduction | undefined {
  const isCanvasEvent =
    event.event_type === "canvas_patch" ||
    event.event_type === "canvas_apply_ack" ||
    event.event_type === "canvas_first_visible" ||
    event.event_type === "canvas_animation_complete" ||
    event.event_type === "canvas_render_failed";
  if (!isCanvasEvent) {
    return undefined;
  }

  const taskId = event.task_id;
  const taskGeneration = event.task_generation;
  if (taskId === undefined || taskGeneration === undefined) {
    return canvasRejection(
      tasks,
      canvas,
      event,
      "task_not_publishable",
      `${event.event_type} is missing its task identity`
    );
  }

  const taskRejection = taskAllowsCanvasMutation(
    tasks,
    event,
    taskId,
    taskGeneration
  );
  if (taskRejection) {
    return {
      tasks,
      canvas,
      effects: [],
      stale: taskRejection.code === "stale_task_generation",
      rejection: taskRejection,
    };
  }

  switch (event.event_type) {
    case "canvas_patch": {
      const baseRevision = event.canvas_base_revision;
      const resultRevision = event.canvas_result_revision;
      if (baseRevision === undefined || resultRevision === undefined) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas patch is missing its revision boundary"
        );
      }
      if (baseRevision !== canvas.appliedRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "stale_canvas_revision",
          `Canvas patch base ${baseRevision} does not match applied revision ${canvas.appliedRevision}`,
          true
        );
      }
      if (canvas.pendingPatch) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_patch_in_flight",
          `Canvas patch ${canvas.pendingPatch.eventId} is still awaiting acknowledgement`
        );
      }
      const pendingPatch: PendingCanvasPatch = {
        eventId: event.event_id,
        artifactId: event.payload.artifact_id,
        artifact: event.payload.artifact,
        baseRevision,
        resultRevision,
        taskId,
        taskGeneration,
      };
      return {
        tasks,
        canvas: {
          ...canvas,
          pendingPatch,
          lastRenderFailure: undefined,
        },
        effects: [{ type: "apply_canvas_patch", event }],
      };
    }

    case "canvas_apply_ack": {
      const resultRevision = event.canvas_result_revision;
      if (resultRevision === undefined) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas acknowledgement is missing its result revision"
        );
      }
      const pending = canvas.pendingPatch;
      if (!pending) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          resultRevision <= canvas.appliedRevision
            ? "stale_canvas_revision"
            : "unknown_canvas_patch",
          "Canvas acknowledgement has no matching pending patch",
          resultRevision <= canvas.appliedRevision
        );
      }
      if (
        event.causation_id !== pending.eventId ||
        event.payload.artifact_id !== pending.artifactId ||
        resultRevision !== pending.resultRevision ||
        taskId !== pending.taskId ||
        taskGeneration !== pending.taskGeneration
      ) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_causation_mismatch",
          "Canvas acknowledgement does not identify the pending patch"
        );
      }
      return {
        tasks,
        canvas: {
          ...canvas,
          appliedRevision: pending.resultRevision,
          pendingPatch: undefined,
          lastAppliedPatch: {
            patchEventId: pending.eventId,
            acknowledgementEventId: event.event_id,
            artifactId: pending.artifactId,
            revision: pending.resultRevision,
            taskId: pending.taskId,
            taskGeneration: pending.taskGeneration,
          },
          lastRenderFailure: undefined,
        },
        effects: [],
      };
    }

    case "canvas_first_visible": {
      const resultRevision = event.canvas_result_revision;
      if (resultRevision === undefined) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas visibility event is missing its result revision"
        );
      }
      if (resultRevision <= canvas.visibleRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "stale_canvas_revision",
          `Visible revision ${resultRevision} is not newer than ${canvas.visibleRevision}`,
          true
        );
      }
      if (resultRevision < canvas.appliedRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "stale_canvas_revision",
          `Visible revision ${resultRevision} is older than applied revision ${canvas.appliedRevision}`,
          true
        );
      }
      if (resultRevision > canvas.appliedRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_revision_ahead",
          `Visible revision ${resultRevision} is ahead of applied revision ${canvas.appliedRevision}`
        );
      }
      const applied = canvas.lastAppliedPatch;
      if (
        !applied ||
        applied.revision !== resultRevision ||
        applied.artifactId !== event.payload.artifact_id ||
        applied.taskId !== taskId ||
        applied.taskGeneration !== taskGeneration ||
        (event.causation_id !== applied.patchEventId &&
          event.causation_id !== applied.acknowledgementEventId)
      ) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_causation_mismatch",
          "Canvas visibility event does not identify the applied patch"
        );
      }
      return {
        tasks,
        canvas: {
          ...canvas,
          visibleRevision: resultRevision,
          lastAppliedPatch: {
            ...applied,
            firstVisibleEventId: event.event_id,
          },
        },
        effects: [],
      };
    }

    case "canvas_animation_complete": {
      const resultRevision = event.canvas_result_revision;
      if (resultRevision === undefined) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas animation event is missing its result revision"
        );
      }
      if (resultRevision > canvas.appliedRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_revision_ahead",
          `Animation revision ${resultRevision} is ahead of applied revision ${canvas.appliedRevision}`
        );
      }
      if (resultRevision < canvas.appliedRevision) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "stale_canvas_revision",
          `Animation revision ${resultRevision} is older than applied revision ${canvas.appliedRevision}`,
          true
        );
      }
      const applied = canvas.lastAppliedPatch;
      const knownCausation =
        applied !== undefined &&
        (event.causation_id === applied.patchEventId ||
          event.causation_id === applied.acknowledgementEventId ||
          event.causation_id === applied.firstVisibleEventId);
      if (
        !applied ||
        applied.revision !== resultRevision ||
        applied.artifactId !== event.payload.artifact_id ||
        applied.taskId !== taskId ||
        applied.taskGeneration !== taskGeneration ||
        !knownCausation
      ) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "canvas_causation_mismatch",
          "Canvas animation event does not identify the applied patch"
        );
      }
      return { tasks, canvas, effects: [] };
    }

    case "canvas_render_failed": {
      const resultRevision = event.canvas_result_revision;
      if (resultRevision === undefined) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas render failure is missing its result revision"
        );
      }
      const pending = canvas.pendingPatch;
      if (
        !pending ||
        event.causation_id !== pending.eventId ||
        event.payload.artifact_id !== pending.artifactId ||
        resultRevision !== pending.resultRevision ||
        taskId !== pending.taskId ||
        taskGeneration !== pending.taskGeneration
      ) {
        return canvasRejection(
          tasks,
          canvas,
          event,
          "unknown_canvas_patch",
          "Canvas render failure has no matching pending patch"
        );
      }
      return {
        tasks,
        canvas: {
          ...canvas,
          pendingPatch: undefined,
          lastRenderFailure: {
            eventId: event.event_id,
            artifactId: event.payload.artifact_id,
            revision: resultRevision,
            code: event.payload.code,
            message: event.payload.message,
          },
        },
        effects: [],
      };
    }

    default:
      return undefined;
  }
}

function reduceSemantics(state: VoiceEventState, event: VoiceEvent): SemanticReduction {
  const taskReduction = reduceTaskEvent(state.tasks, event);
  if (taskReduction) {
    return {
      tasks: taskReduction.tasks,
      canvas: state.canvas,
      effects: [],
      ...(taskReduction.rejection ? { rejection: taskReduction.rejection } : {}),
      ...(taskReduction.stale !== undefined ? { stale: taskReduction.stale } : {}),
    };
  }
  return (
    reduceCanvasEvent(state.tasks, state.canvas, event) ?? {
      tasks: state.tasks,
      canvas: state.canvas,
      effects: [],
    }
  );
}

/**
 * Reduce one untrusted transport message. A caller must execute returned effects only
 * when disposition is `applied`; all rejected, stale, and duplicate events are effect-free.
 */
export function reduceVoiceEvent(
  state: VoiceEventState,
  input: unknown
): VoiceEventReduction {
  if (state.compatibilityFailure) {
    return reject(state, "rejected", {
      code: "compatibility_locked",
      message: "Voice events remain locked after a compatibility failure",
    });
  }

  const decoded = decodeVoiceEvent(input);
  if (!decoded.ok) {
    return decodeFailure(state, decoded.error);
  }
  const event = decoded.event;

  if (state.seenEventIds.includes(event.event_id)) {
    return { state, disposition: "duplicate", effects: [] };
  }

  const acceptsTerminalConfirmation =
    event.event_type === "session_ended" &&
    state.session.terminationStage !== "ended";
  if (state.session.phase === "ended" && !acceptsTerminalConfirmation) {
    return reject(state, "stale", {
      code: "session_ended",
      message: "Voice session has already ended",
      eventId: event.event_id,
      eventType: event.event_type,
    });
  }

  if (state.sessionId !== undefined && event.session_id !== state.sessionId) {
    return compatibilityFailure(
      state,
      event,
      "session_mismatch",
      `Event session ${event.session_id} does not match ${state.sessionId}`
    );
  }

  if (state.voiceCallId !== undefined && event.voice_call_id !== state.voiceCallId) {
    return compatibilityFailure(
      state,
      event,
      "voice_call_mismatch",
      `Event voice call ${event.voice_call_id} does not match ${state.voiceCallId}`
    );
  }

  const lastProducerSequence = producerSequence(state, event.producer_id);
  if (
    lastProducerSequence !== undefined &&
    event.producer_sequence <= lastProducerSequence
  ) {
    return reject(state, "stale", {
      code: "producer_sequence_regression",
      message: `Producer ${event.producer_id} sequence ${event.producer_sequence} is not newer than ${lastProducerSequence}`,
      eventId: event.event_id,
      eventType: event.event_type,
    });
  }

  if (
    event.ledger_sequence !== undefined &&
    state.lastLedgerSequence !== undefined &&
    event.ledger_sequence <= state.lastLedgerSequence
  ) {
    return reject(state, "stale", {
      code: "ledger_sequence_regression",
      message: `Ledger sequence ${event.ledger_sequence} is not newer than ${state.lastLedgerSequence}`,
      eventId: event.event_id,
      eventType: event.event_type,
    });
  }

  const nextSession = transitionVoiceSession(state.session, { type: "event", event });
  const protocolViolation =
    nextSession.phase === "unavailable" &&
    nextSession.unavailableReason?.code.startsWith("protocol_") === true;
  if (protocolViolation) {
    const nextState: VoiceEventState = {
      ...state,
      voiceCallId: state.voiceCallId ?? event.voice_call_id,
      sessionId: state.sessionId ?? event.session_id,
      session: nextSession,
      seenEventIds: [...state.seenEventIds, event.event_id],
      producerCursors: advanceProducerCursor(state.producerCursors, event),
      ...(event.ledger_sequence !== undefined
        ? { lastLedgerSequence: event.ledger_sequence }
        : {}),
      lastAppliedEventId: event.event_id,
    };
    return reject(nextState, "rejected", {
      code: "protocol_state_violation",
      message: nextSession.unavailableReason?.message ?? "Invalid voice session transition",
      eventId: event.event_id,
      eventType: event.event_type,
    });
  }

  const semantic = reduceSemantics(state, event);
  if (semantic.rejection) {
    return reject(
      state,
      semantic.stale ? "stale" : "rejected",
      semantic.rejection
    );
  }

  const nextState: VoiceEventState = {
    ...state,
    voiceCallId: state.voiceCallId ?? event.voice_call_id,
    sessionId: state.sessionId ?? event.session_id,
    session: nextSession,
    seenEventIds: [...state.seenEventIds, event.event_id],
    producerCursors: advanceProducerCursor(state.producerCursors, event),
    ...(event.ledger_sequence !== undefined
      ? { lastLedgerSequence: event.ledger_sequence }
      : {}),
    lastAppliedEventId: event.event_id,
    tasks: semantic.tasks,
    canvas: semantic.canvas,
  };

  return {
    state: nextState,
    disposition: "applied",
    effects: semantic.effects,
  };
}
