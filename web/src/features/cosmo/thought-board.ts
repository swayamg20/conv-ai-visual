import type { RealtimeTool } from "cosmo-ai";
import { tool } from "cosmo-ai/tool";

import type { Thought } from "./contracts";
import { quadraticSummary, quadraticVisual, type QuadraticVisual } from "./quadratic";

const CANCELLED = "The thought was not added because this call ended.";

/** Owns pending render receipts for one call. No model result is a render receipt. */
export class ThoughtBoard {
  private thoughts: Thought[] = [];
  private pending = new Map<
    string,
    {
      resolve: (thought: Thought) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();

  constructor(
    private readonly signal: AbortSignal,
    private readonly onChange: (thoughts: readonly Thought[]) => void,
  ) {
    signal.addEventListener("abort", () => this.cancel(), { once: true });
  }

  pin(title: string, body: string, quadratic?: QuadraticVisual): Promise<Thought> {
    if (this.signal.aborted) return Promise.reject(new Error(CANCELLED));
    if (
      !title.trim() ||
      title.length > 80 ||
      !body.trim() ||
      body.length > 600
    ) {
      return Promise.reject(
        new Error("Use a short title and a note of at most 600 characters."),
      );
    }
    if (this.thoughts.length >= 12) {
      return Promise.reject(
        new Error(
          "The board is full. Start a new conversation for a fresh board.",
        ),
      );
    }
    const thought: Thought = {
      id: crypto.randomUUID(),
      title,
      body,
      rendered: false,
      ...(quadratic ? { quadratic } : {}),
    };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(thought.id);
        this.thoughts = this.thoughts.filter((item) => item.id !== thought.id);
        this.onChange(this.thoughts);
        reject(
          new Error(
            "The board did not confirm the note. It has not been saved.",
          ),
        );
      }, 8000);
      this.pending.set(thought.id, { resolve, reject, timer });
      this.thoughts = [...this.thoughts, thought];
      this.onChange(this.thoughts);
    });
  }

  acknowledge(ids: readonly string[]): void {
    if (this.signal.aborted) return;
    let changed = false;
    for (const id of ids) {
      const pending = this.pending.get(id);
      const thought = this.thoughts.find((item) => item.id === id);
      if (!pending || !thought) continue;
      clearTimeout(pending.timer);
      this.pending.delete(id);
      const rendered = { ...thought, rendered: true };
      this.thoughts = this.thoughts.map((item) =>
        item.id === id ? rendered : item,
      );
      pending.resolve(rendered);
      changed = true;
    }
    if (changed) this.onChange(this.thoughts);
  }

  private cancel(): void {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error(CANCELLED));
    }
    this.pending.clear();
    this.thoughts = this.thoughts.filter((item) => item.rendered);
    this.onChange(this.thoughts);
  }

  tools(): RealtimeTool[] {
    return [
      tool({
        name: "show_quadratic",
        description: "Draw ax² + bx + c = 0 and its actual graph, roots, and vertex on Murmur's visual board. You MUST call this function for a request to show a quadratic. For a general example use a=1, b=-5, c=6. Use numeric coefficients between -100 and 100, with |a| at least 0.1. Wait for rendered:true before describing a visible graph.",
        parameters: {
          type: "object",
          properties: { a: { type: "number", minimum: -100, maximum: 100 }, b: { type: "number", minimum: -100, maximum: 100 }, c: { type: "number", minimum: -100, maximum: 100 } },
          required: ["a", "b", "c"],
        },
        handler: async (args) => {
          if (this.signal.aborted) throw new Error(CANCELLED);
          const visual = quadraticVisual(args.a, args.b, args.c);
          const thought = await this.pin("Quadratic equation", quadraticSummary(visual), visual);
          if (this.signal.aborted) throw new Error(CANCELLED);
          return { id: thought.id, rendered: true, equation: `${visual.expression} = 0`, roots: [...visual.roots], vertex: [...visual.vertex], summary: `The graph is now visible. ${thought.body}` };
        },
      }),
      tool({
        background: true,
        name: "pin_thought",
        description:
          "Pin a concise thought on the user's temporary board. Completes only when the browser has rendered it. This is not durable storage.",
        parameters: {
          type: "object",
          properties: {
            title: { type: "string", maxLength: 80 },
            body: { type: "string", maxLength: 600 },
          },
          required: ["title", "body"],
        },
        handler: async (args, job) => {
          if (this.signal.aborted) throw new Error(CANCELLED);
          if (typeof args.title !== "string" || typeof args.body !== "string") {
            throw new Error("The note needs a title and body.");
          }
          job.ack("I'll pin that thought.");
          try {
            const thought = await this.pin(args.title, args.body);
            if (this.signal.aborted) throw new Error(CANCELLED);
            await job.complete({
              result: { id: thought.id, rendered: true },
              summary: "The thought is now visible on the board.",
            });
          } catch {
            await job.fail({
              error:
                "The thought was not confirmed on the board. Do not say it was saved.",
            });
          }
        },
      }),
      tool({
        name: "read_thoughts",
        description:
          "Read the thoughts currently visible on this conversation's temporary board.",
        parameters: {
          type: "object",
          properties: {},
        },
        handler: async () => {
          if (this.signal.aborted) throw new Error(CANCELLED);
          return {
            thoughts: this.thoughts
              .filter((item) => item.rendered)
              .map(({ id, title, body }) => ({ id, title, body })),
          };
        },
      }),
    ];
  }
}
