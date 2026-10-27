import { afterEach, describe, expect, it, vi } from "vitest";

import { ThoughtBoard } from "./thought-board";
import { foldTranscript } from "./transcript";

afterEach(() => vi.useRealTimers());

describe("thought-board render authority", () => {
  it("acceptance stays pending until its exact thought is rendered", async () => {
    const abort = new AbortController();
    const changed = vi.fn();
    const board = new ThoughtBoard(abort.signal, changed);
    const done = vi.fn();
    const pending = board.pin("Idea", "Start small").then(done);
    const thought = changed.mock.calls[0][0][0];
    board.acknowledge(["unrelated"]);
    await Promise.resolve();
    expect(done).not.toHaveBeenCalled();
    board.acknowledge([thought.id, thought.id]);
    await pending;
    expect(done).toHaveBeenCalledExactlyOnceWith({
      ...thought,
      rendered: true,
    });
    abort.abort();
  });

  it("cancels pending notes on end and rejects a late receipt", async () => {
    const abort = new AbortController();
    const changed = vi.fn();
    const board = new ThoughtBoard(abort.signal, changed);
    const pending = board.pin("Idea", "Start small");
    const rejected = expect(pending).rejects.toThrow("call ended");
    const id = changed.mock.calls[0][0][0].id;
    abort.abort();
    board.acknowledge([id]);
    await rejected;
    expect(changed.mock.lastCall?.[0]).toEqual([]);
    await expect(board.pin("Late", "Too late")).rejects.toThrow("call ended");
  });

  it("removes an unrendered note after a bounded wait", async () => {
    vi.useFakeTimers();
    const abort = new AbortController();
    const changed = vi.fn();
    const board = new ThoughtBoard(abort.signal, changed);
    const rejected = expect(board.pin("Idea", "Start small")).rejects.toThrow(
      "did not confirm",
    );
    await vi.advanceTimersByTimeAsync(8000);
    await rejected;
    expect(changed.mock.lastCall?.[0]).toEqual([]);
    abort.abort();
  });
});

it("folds fragments and replaces them with the cumulative final", () => {
  const common = {
    id: "a",
    role: "assistant" as const,
    turnId: "t1",
    append: true,
  };
  const first = foldTranscript([], { ...common, text: "Hel", isFinal: false });
  const second = foldTranscript(first, {
    ...common,
    id: "b",
    text: "lo",
    isFinal: false,
  });
  expect(second[0].text).toBe("Hello");
  const final = foldTranscript(second, {
    ...common,
    id: "c",
    text: "Hello!",
    isFinal: true,
  });
  expect(final[0].text).toBe("Hello!");
  expect(final[0].final).toBe(true);
});
