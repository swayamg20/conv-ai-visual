import { describe, expect, it, vi } from "vitest";

import {
  endStepTimelineSequence,
  killStepTimelines,
  type SDLStepTimelineMap,
} from "./sequence-lifecycle";

function timeline() {
  return {
    kill: vi.fn(),
    play: vi.fn(),
  };
}

describe("SDL step timeline lifecycle", () => {
  it("kills running and queued timelines without playing future steps when interrupted", () => {
    const running = timeline();
    const queued = timeline();
    const unrelated = timeline();
    const entries = new Map([
      ["sequence-1:0", { tl: running, started: true }],
      ["sequence-1:1", { tl: queued, started: false }],
      ["sequence-2:0", { tl: unrelated, started: false }],
    ]) as unknown as SDLStepTimelineMap;

    endStepTimelineSequence(entries, "sequence-1", "interrupted");

    expect(running.kill).toHaveBeenCalledOnce();
    expect(queued.kill).toHaveBeenCalledOnce();
    expect(queued.play).not.toHaveBeenCalled();
    expect(unrelated.kill).not.toHaveBeenCalled();
    expect(entries.has("sequence-1:0")).toBe(false);
    expect(entries.has("sequence-1:1")).toBe(false);
    expect(entries.has("sequence-2:0")).toBe(true);
  });

  it("preserves the legacy queued-step fallback on normal completion", () => {
    const running = timeline();
    const queued = timeline();
    const entries = new Map([
      ["sequence-1:0", { tl: running, started: true }],
      ["sequence-1:1", { tl: queued, started: false }],
    ]) as unknown as SDLStepTimelineMap;

    endStepTimelineSequence(entries, "sequence-1", "completed");

    expect(running.kill).not.toHaveBeenCalled();
    expect(running.play).not.toHaveBeenCalled();
    expect(queued.kill).not.toHaveBeenCalled();
    expect(queued.play).toHaveBeenCalledOnce();
    expect(entries.size).toBe(0);
  });

  it("kills every prior timeline before the owner replaces its map", () => {
    const first = timeline();
    const second = timeline();
    const entries = new Map([
      ["sequence-1:0", { tl: first, started: true }],
      ["sequence-1:1", { tl: second, started: false }],
    ]) as unknown as SDLStepTimelineMap;

    killStepTimelines(entries);

    expect(first.kill).toHaveBeenCalledOnce();
    expect(second.kill).toHaveBeenCalledOnce();
    expect(entries.size).toBe(0);
  });
});
