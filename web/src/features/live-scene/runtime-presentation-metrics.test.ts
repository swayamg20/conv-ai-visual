import { describe, expect, it } from "vitest";

import {
  beginPresentationInterrupt,
  beginPresentationReplay,
  beginPresentationRequest,
  createRuntimePresentationMetricsState,
  markFirstPresented,
  resetPresentationMetrics,
  settlePresentationMetrics,
} from "./runtime-presentation-metrics";

describe("runtime presentation metrics", () => {
  it("measures a request from invocation through first post-paint acceptance and terminal", () => {
    const started = beginPresentationRequest(100);
    expect(Object.isFrozen(started)).toBe(true);
    expect(Object.isFrozen(started.snapshot)).toBe(true);

    const presented = markFirstPresented(started, 145);
    expect(presented.snapshot).toEqual({ requestToFirstPresentedMs: 45 });
    expect(markFirstPresented(presented, 180)).toBe(presented);

    const settled = settlePresentationMetrics(presented, 260);
    expect(settled).toEqual({
      snapshot: {
        requestToFirstPresentedMs: 45,
        requestToSettledMs: 160,
      },
    });
    expect(Object.isFrozen(settled)).toBe(true);
    expect(Object.isFrozen(settled.snapshot)).toBe(true);
  });

  it("keeps repeated interruption idempotent and closes request and stop together", () => {
    const requested = beginPresentationRequest(10);
    const interrupting = beginPresentationInterrupt(requested, 30);
    expect(beginPresentationInterrupt(interrupting, 45)).toBe(interrupting);

    expect(settlePresentationMetrics(interrupting, 70)).toEqual({
      snapshot: {
        requestToSettledMs: 60,
        interruptToSettledMs: 40,
      },
    });
  });

  it("retains request evidence while measuring a later local replay", () => {
    const requestSettled = settlePresentationMetrics(
      markFirstPresented(beginPresentationRequest(0), 20),
      80
    );
    const replaySettled = settlePresentationMetrics(
      beginPresentationReplay(requestSettled, 100),
      155
    );

    expect(replaySettled.snapshot).toEqual({
      requestToFirstPresentedMs: 20,
      requestToSettledMs: 80,
      replayDurationMs: 55,
    });
  });

  it("clamps a regressing clock and resets all timing state", () => {
    const settled = settlePresentationMetrics(
      beginPresentationRequest(100),
      90
    );
    expect(settled.snapshot).toEqual({ requestToSettledMs: 0 });
    expect(resetPresentationMetrics()).toBe(
      createRuntimePresentationMetricsState()
    );
    expect(resetPresentationMetrics()).toEqual({});
  });
});
