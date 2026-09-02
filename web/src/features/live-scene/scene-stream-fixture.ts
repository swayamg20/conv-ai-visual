import type { SceneNode } from "@/lib/live-scene";
import type { ScenePatchEvent, ScenePatchOperation } from "@/lib/live-scene/patch";

import {
  consumeSceneStreamResponse,
  type SceneStreamEvent,
  type SceneStreamRequest,
} from "./model-stream";
import type { SceneStreamRunner } from "./stream-runtime";

export const SCENE_FIXTURE_MODES = [
  "normal",
  "repair",
  "failure",
  "stale",
] as const;

export type SceneFixtureMode = (typeof SCENE_FIXTURE_MODES)[number];

export interface SceneFixtureRunnerOptions {
  readonly mode: SceneFixtureMode;
  /** Delay between lifecycle events. Keep non-zero in the lab to prove progressive rendering. */
  readonly eventDelayMs?: number;
  /** Delay between deliberately awkward transport chunks. */
  readonly chunkDelayMs?: number;
}

const PRESENTATION = Object.freeze({ enter: "draw", exit: "fade" } as const);
const TEXT_PRESENTATION = Object.freeze({ enter: "fade", exit: "fade" } as const);
const CHALK = "hsl(var(--chalk))";
const CHALK_SOFT = "hsl(var(--chalk-soft))";
const AMBER = "hsl(var(--amber))";
const LAVENDER = "hsl(var(--lavender))";
const SAGE = "hsl(var(--sage))";

function line(id: string, start: readonly [number, number], end: readonly [number, number]): SceneNode {
  return Object.freeze({
    id,
    kind: "line",
    presentation: PRESENTATION,
    points: Object.freeze([start, end]) as readonly [
      readonly [number, number],
      readonly [number, number],
    ],
    style: Object.freeze({ stroke: CHALK, strokeWidth: 4, opacity: 1, roughness: 1.4 }),
  });
}

function path(id: string, points: readonly (readonly [number, number])[], color = AMBER): SceneNode {
  return Object.freeze({
    id,
    kind: "path",
    presentation: PRESENTATION,
    points: Object.freeze(points),
    closed: false,
    style: Object.freeze({
      stroke: color,
      strokeWidth: 4,
      opacity: 1,
      roughness: 1.1,
      fill: "none",
    }),
  });
}

function text(
  id: string,
  x: number,
  y: number,
  value: string,
  options: { readonly color?: string; readonly fontSize?: number; readonly anchor?: "start" | "middle" | "end" } = {}
): SceneNode {
  return Object.freeze({
    id,
    kind: "text",
    presentation: TEXT_PRESENTATION,
    x,
    y,
    text: value,
    style: Object.freeze({
      color: options.color ?? CHALK,
      fontSize: options.fontSize ?? 24,
      opacity: 1,
      anchor: options.anchor ?? "start",
    }),
  });
}

function latex(id: string, x: number, y: number, value: string): SceneNode {
  return Object.freeze({
    id,
    kind: "latex",
    presentation: TEXT_PRESENTATION,
    x,
    y,
    latex: value,
    style: Object.freeze({ color: AMBER, fontSize: 32, opacity: 1 }),
  });
}

function rect(id: string, x: number, y: number, width: number, height: number): SceneNode {
  return Object.freeze({
    id,
    kind: "rect",
    presentation: Object.freeze({ enter: "scale", exit: "fade" } as const),
    x,
    y,
    width,
    height,
    style: Object.freeze({
      stroke: LAVENDER,
      strokeWidth: 2,
      opacity: 0.9,
      roughness: 1.2,
      fill: "transparent",
    }),
  });
}

function put(node: SceneNode): ScenePatchOperation {
  return Object.freeze({ op: "put", node });
}

function patch(
  request: SceneStreamRequest,
  attempt: number,
  sequence: number,
  operations: readonly ScenePatchOperation[],
  narration: string
): ScenePatchEvent {
  const baseRevision = request.baseScene.revision + sequence - 1;
  return Object.freeze({
    type: "scene_patch",
    generation: request.generation,
    attempt,
    sequence,
    baseRevision,
    resultRevision: baseRevision + 1,
    patch: Object.freeze({
      v: 1,
      patchId: `fixtureG${request.generation}P${sequence}`,
      narration,
      operations: Object.freeze([...operations]),
    }),
  });
}

function authoredPatches(request: SceneStreamRequest, attempt: number): readonly ScenePatchEvent[] {
  const suffix = `G${request.generation}`;
  return Object.freeze([
    patch(
      request,
      attempt,
      1,
      [
        put(text(`title${suffix}`, 400, 64, "Why does a² + b² = c²?", { fontSize: 30, anchor: "middle" })),
        put(line(`legA${suffix}`, [190, 450], [520, 450])),
        put(line(`legB${suffix}`, [520, 450], [520, 170])),
        put(line(`hypotenuse${suffix}`, [190, 450], [520, 170])),
      ],
      "First, I’m keeping only the right triangle on the board."
    ),
    patch(
      request,
      attempt,
      2,
      [
        put(path(`rightAngle${suffix}`, [[488, 450], [488, 418], [520, 418]], AMBER)),
        put(text(`labelA${suffix}`, 350, 482, "a", { color: SAGE, fontSize: 27, anchor: "middle" })),
        put(text(`labelB${suffix}`, 550, 320, "b", { color: SAGE, fontSize: 27 })),
        put(text(`labelC${suffix}`, 337, 286, "c", { color: LAVENDER, fontSize: 27, anchor: "middle" })),
      ],
      "The marked corner makes a and b perpendicular, so c is the hypotenuse."
    ),
    patch(
      request,
      attempt,
      3,
      [
        put(rect(`equationFrame${suffix}`, 555, 210, 205, 105)),
        // The renderer gives LaTeX a fixed 500px foreignObject; offset its
        // origin so KaTeX's centered content lands inside the visible frame.
        put(latex(`equation${suffix}`, 407, 205, "a^2 + b^2 = c^2")),
      ],
      "Now the relationship appears beside the geometry instead of replacing it."
    ),
    patch(
      request,
      attempt,
      4,
      [
        put(text(`areaNote${suffix}`, 555, 360, "areas on the legs", { color: CHALK_SOFT, fontSize: 18 })),
        put(text(`equalsNote${suffix}`, 555, 394, "combine into the area", { color: CHALK_SOFT, fontSize: 18 })),
        put(text(`hypNote${suffix}`, 555, 428, "on the hypotenuse", { color: AMBER, fontSize: 18 })),
      ],
      "Read the equation as an area statement: the two smaller squares combine into the largest one."
    ),
  ]);
}

/** Build deterministic server-shaped events; the model draft is never trusted with lifecycle fields. */
export function createSceneFixtureEvents(
  request: SceneStreamRequest,
  mode: SceneFixtureMode
): readonly SceneStreamEvent[] {
  const started: SceneStreamEvent = Object.freeze({
    type: "scene_stream_started",
    generation: request.generation,
    attempt: 1,
    baseRevision: request.baseScene.revision,
  });

  if (mode === "failure") {
    return Object.freeze([
      started,
      Object.freeze({
        type: "scene_stream_repairing",
        generation: request.generation,
        fromAttempt: 1,
        toAttempt: 2,
        lastAcceptedRevision: request.baseScene.revision,
        message: "The first draft was invalid. Repairing once from the safe board…",
      }),
      Object.freeze({
        type: "scene_stream_failed",
        generation: request.generation,
        attempt: 2,
        code: "invalid_model_output",
        message: "Couldn’t update the board. Your last accepted scene is safe.",
        lastAcceptedRevision: request.baseScene.revision,
        retryable: true,
      }),
    ]);
  }

  const attempt = mode === "repair" ? 2 : 1;
  const patches = authoredPatches(request, attempt);
  const repairing: readonly SceneStreamEvent[] =
    mode === "repair"
      ? [
          Object.freeze({
            type: "scene_stream_repairing",
            generation: request.generation,
            fromAttempt: 1,
            toAttempt: 2,
            lastAcceptedRevision: request.baseScene.revision,
            message: "The first draft missed the scene contract. Repairing once…",
          }),
        ]
      : [];
  return Object.freeze([
    started,
    ...repairing,
    ...patches,
    Object.freeze({
      type: "scene_stream_completed",
      generation: request.generation,
      finalRevision: request.baseScene.revision + patches.length,
      patchCount: patches.length,
      firstPatchMs: 340,
      totalMs: 1_460,
      repaired: mode === "repair",
    }),
  ]);
}

function wait(milliseconds: number): Promise<void> {
  if (milliseconds <= 0) return Promise.resolve();
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function abortException(): Error {
  if (typeof DOMException !== "undefined") return new DOMException("Aborted", "AbortError");
  return Object.assign(new Error("Aborted"), { name: "AbortError" });
}

function splitFrame(frame: Uint8Array): readonly Uint8Array[] {
  // Fixed byte offsets deliberately split JSON tokens and occasionally a UTF-8 code point.
  const offsets = [1, 11, Math.max(12, Math.floor(frame.length * 0.57)), frame.length - 3]
    .filter((offset, index, values) => offset > 0 && offset < frame.length && values.indexOf(offset) === index)
    .sort((left, right) => left - right);
  const chunks: Uint8Array[] = [];
  let start = 0;
  for (const end of [...offsets, frame.length]) {
    chunks.push(frame.slice(start, end));
    start = end;
  }
  return chunks;
}

function fixtureResponse(
  events: readonly SceneStreamEvent[],
  signal: AbortSignal,
  options: Required<Pick<SceneFixtureRunnerOptions, "eventDelayMs" | "chunkDelayMs">>,
  ignoreAbort: boolean
): Response {
  const encoder = new TextEncoder();
  let stopped = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      void (async () => {
        try {
          for (const [index, event] of events.entries()) {
            await wait(index === 0 ? Math.min(options.eventDelayMs, 80) : options.eventDelayMs);
            if (signal.aborted && !ignoreAbort) throw abortException();
            const frame = encoder.encode(
              `id: fixture-${index + 1}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`
            );
            for (const chunk of splitFrame(frame)) {
              if (signal.aborted && !ignoreAbort) throw abortException();
              controller.enqueue(chunk);
              await wait(options.chunkDelayMs);
            }
          }
          stopped = true;
          controller.close();
        } catch (error) {
          stopped = true;
          controller.error(error);
        }
      })();
    },
    cancel() {
      stopped = true;
    },
  });

  signal.addEventListener(
    "abort",
    () => {
      // The stale fixture intentionally keeps producing to prove token rejection.
      if (!ignoreAbort && !stopped) stopped = true;
    },
    { once: true }
  );

  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
  });
}

/**
 * Create an auth-free lab source that still traverses the production UTF-8/SSE
 * parser. `stale` intentionally ignores AbortSignal so the runtime must reject
 * old-generation events itself.
 */
export function createSceneFixtureRunner(options: SceneFixtureRunnerOptions): SceneStreamRunner {
  const eventDelayMs = options.eventDelayMs ?? (options.mode === "stale" ? 520 : 260);
  const chunkDelayMs = options.chunkDelayMs ?? 3;
  return async ({ request, signal, onEvent }) => {
    const events = createSceneFixtureEvents(request, options.mode);
    const response = fixtureResponse(
      events,
      signal,
      { eventDelayMs, chunkDelayMs },
      options.mode === "stale"
    );
    await consumeSceneStreamResponse(response, onEvent);
  };
}
