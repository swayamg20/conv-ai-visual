interface FixtureSseEvent {
  readonly type: string;
}

export interface FixtureSseResponseOptions {
  readonly eventDelayMs: number;
  readonly chunkDelayMs: number;
  readonly idPrefix: string;
  readonly ignoreAbort?: boolean;
  /** Keep the decoded event source pending until the owning runtime interrupts it. */
  readonly holdOpenUntilAbort?: boolean;
}

function abortException(): Error {
  if (typeof DOMException !== "undefined") {
    return new DOMException("Aborted", "AbortError");
  }
  return Object.assign(new Error("Aborted"), { name: "AbortError" });
}

function wait(
  milliseconds: number,
  signal: AbortSignal,
  ignoreAbort: boolean,
): Promise<void> {
  if (!ignoreAbort && signal.aborted) return Promise.reject(abortException());
  if (milliseconds <= 0) return Promise.resolve();
  if (ignoreAbort) {
    return new Promise((resolve) =>
      globalThis.setTimeout(resolve, milliseconds),
    );
  }
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      reject(abortException());
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function splitFrame(frame: Uint8Array): readonly Uint8Array[] {
  const offsets = [
    1,
    13,
    Math.max(14, Math.floor(frame.length * 0.53)),
    frame.length - 3,
  ]
    .filter(
      (offset, index, values) =>
        offset > 0 && offset < frame.length && values.indexOf(offset) === index,
    )
    .sort((left, right) => left - right);
  const chunks: Uint8Array[] = [];
  let start = 0;
  for (const end of [...offsets, frame.length]) {
    chunks.push(frame.slice(start, end));
    start = end;
  }
  return chunks;
}

function waitForAbort(signal: AbortSignal): Promise<never> {
  if (signal.aborted) return Promise.reject(abortException());
  return new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(abortException()), {
      once: true,
    });
  });
}

/**
 * Exercise the production byte-stream decoder with deterministic awkward SSE
 * and UTF-8 chunk boundaries, without opening a network connection.
 */
export function createFixtureSseResponse<Event extends FixtureSseEvent>(
  events: readonly Event[],
  signal: AbortSignal,
  options: FixtureSseResponseOptions,
): Response {
  const ignoreAbort = options.ignoreAbort ?? false;
  const encoder = new TextEncoder();
  let stopped = false;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      void (async () => {
        try {
          for (const [index, event] of events.entries()) {
            await wait(
              index === 0
                ? Math.min(options.eventDelayMs, 80)
                : options.eventDelayMs,
              signal,
              ignoreAbort,
            );
            if (stopped) return;
            if (!ignoreAbort && signal.aborted) throw abortException();
            const frame = encoder.encode(
              `id: ${options.idPrefix}-${index + 1}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
            );
            for (const chunk of splitFrame(frame)) {
              if (stopped) return;
              if (!ignoreAbort && signal.aborted) throw abortException();
              controller.enqueue(chunk);
              await wait(options.chunkDelayMs, signal, ignoreAbort);
            }
          }
          if (options.holdOpenUntilAbort) {
            if (ignoreAbort) {
              throw new Error("A stale fixture cannot hold its stream open");
            }
            await waitForAbort(signal);
          }
          if (!stopped) {
            stopped = true;
            controller.close();
          }
        } catch (error) {
          if (!stopped) {
            stopped = true;
            controller.error(error);
          }
        }
      })();
    },
    cancel() {
      stopped = true;
    },
  });

  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream; charset=utf-8" },
  });
}
