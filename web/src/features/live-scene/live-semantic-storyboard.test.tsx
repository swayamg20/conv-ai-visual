/** @vitest-environment happy-dom */

import { act, type ComponentProps, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PlannedCheckpointChoreography } from "@/lib/live-scene";
import {
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  type PairedProjectileComparisonSpecV1,
  type SemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import { decodeSemanticStoryboardSceneStreamEventV1 } from "@/lib/live-scene/semantic-storyboard-stream";

import type {
  ChoreographyExecutorObserver,
  ChoreographyPlayback,
} from "./choreography-executor";
import {
  LiveSemanticStoryboard,
  runAuthenticatedSemanticStoryboardStream,
} from "./live-semantic-storyboard";
import type {
  SemanticStoryboardSceneStreamRunInvocation,
  SemanticStoryboardSceneStreamRunner,
} from "./semantic-storyboard-model-stream";
import {
  createSemanticStoryboardFixtureBatch,
  createSemanticStoryboardFixtureRunner,
} from "./semantic-storyboard-scene-stream-fixture";
import type { SemanticStoryboardSessionSnapshot } from "./semantic-storyboard-session-controller";

const DEFAULT_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  anglesDeg: Object.freeze([30, 60]),
} as const satisfies PairedProjectileComparisonSpecV1);
const DEFAULT_PROMPT =
  "Trace both flights before comparing their landing ranges.";
const HIGHER_ARC_PROMPT =
  "Begin with the higher arc, then compare height and time.";
const CONTINUE_PROMPT = "Continue with exactly one new useful visual beat.";
const PREFIX_PROMPT =
  "Show one formula, then stop safely if later output is malformed.";
const ABSTAIN_PROMPT =
  "Do nothing if this request has no supported forward step.";
const ANCHOR_CAPTION =
  "Same launch speed, two angles. Watch how path, landing range, height, and flight time compare.";

const product = vi.hoisted(() => ({
  getAuthHeaders: vi.fn(),
}));

const canvas = vi.hoisted(() => ({
  cancelMotion: vi.fn(),
  clear: vi.fn(),
  materializeScene: vi.fn(),
  materializeViewport: vi.fn(),
  prepareReplayScene: vi.fn(),
  finishReplayScene: vi.fn(),
  playCheckpointChoreography: vi.fn(),
}));

vi.mock("@/lib/firebase", () => ({
  getAuthHeaders: product.getAuthHeaders,
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children?: ReactNode;
    href: string;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/murmur-doodles", () => ({
  MurmurLogoMark: ({
    animateOnMount,
    reducedMotion,
  }: {
    animateOnMount?: boolean;
    reducedMotion?: boolean;
  }) => (
    <span
      data-testid="logo"
      data-animate-on-mount={String(animateOnMount ?? true)}
      data-reduced-motion={String(reducedMotion ?? false)}
    />
  ),
}));

vi.mock("@/components/svg-canvas", async () => {
  const React = await import("react");
  return {
    SVGCanvas: React.forwardRef(function MockCanvas(
      props: { reducedMotion?: boolean; choreographyPlaybackRate?: number },
      ref,
    ) {
      React.useImperativeHandle(ref, () => ({
        cancelMotion: canvas.cancelMotion,
        clear: canvas.clear,
        materializeScene: canvas.materializeScene,
        materializeViewport: canvas.materializeViewport,
        prepareReplayScene: canvas.prepareReplayScene,
        finishReplayScene: canvas.finishReplayScene,
        playCheckpointChoreography: canvas.playCheckpointChoreography,
      }));
      return React.createElement("svg", {
        "data-testid": "semantic-storyboard-canvas",
        "data-reduced-motion": String(props.reducedMotion),
        "data-playback-rate": String(props.choreographyPlaybackRate),
      });
    }),
  };
});

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

interface MountedProduct {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

const mountedRoots = new Set<MountedProduct>();

function immediatePlayback(
  plan: PlannedCheckpointChoreography,
  observer?: ChoreographyExecutorObserver,
): ChoreographyPlayback {
  const finished = new Promise<{
    readonly status: "completed";
    readonly firstCuePresented: true;
  }>((resolve) => {
    queueMicrotask(() => {
      for (const cue of plan.choreographyPlan.phase.cues) {
        observer?.({ type: "cueStarted", cue: cue.cue });
      }
      observer?.({ type: "firstCuePresented" });
      observer?.({ type: "checkpointSettled", settlement: "completed" });
      resolve({ status: "completed", firstCuePresented: true });
    });
  });
  return {
    firstCuePresented: Promise.resolve(true),
    finished,
    cancel: vi.fn(),
  };
}

async function mount(
  props: Partial<ComponentProps<typeof LiveSemanticStoryboard>> = {},
): Promise<MountedProduct> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const mounted = { container, root };
  mountedRoots.add(mounted);
  await act(async () => {
    root.render(
      <LiveSemanticStoryboard layout="cinematic" reducedMotion {...props} />,
    );
  });
  return mounted;
}

async function unmount(mounted: MountedProduct): Promise<void> {
  if (!mountedRoots.delete(mounted)) return;
  await act(async () => mounted.root.unmount());
}

function root(container: HTMLElement): HTMLElement {
  const result = container.querySelector<HTMLElement>(
    '[data-testid="semantic-storyboard-product"]',
  );
  if (!result) throw new Error("Missing semantic storyboard product");
  return result;
}

function stage(container: HTMLElement): HTMLElement {
  const result = container.querySelector<HTMLElement>(
    '[data-testid="semantic-storyboard-stage"]',
  );
  if (!result) throw new Error("Missing semantic storyboard stage");
  return result;
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
  const result = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  if (!result) throw new Error(`Missing button: ${label}`);
  return result;
}

function promptInput(container: HTMLElement): HTMLTextAreaElement {
  const result = container.querySelector<HTMLTextAreaElement>(
    "#semantic-storyboard-prompt",
  );
  if (!result) throw new Error("Missing storyboard prompt");
  return result;
}

async function setPrompt(container: HTMLElement, value: string): Promise<void> {
  const input = promptInput(container);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function waitFor(
  predicate: () => boolean,
  description: string,
): Promise<void> {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    if (predicate()) return;
    await act(
      () => new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0)),
    );
  }
  throw new Error(`Timed out waiting for ${description}`);
}

function fixtureRunner(
  calls: SemanticStoryboardSceneStreamRunInvocation[],
): SemanticStoryboardSceneStreamRunner {
  const fixture = createSemanticStoryboardFixtureRunner({
    eventDelayMs: 0,
    chunkDelayMs: 0,
  });
  return async (invocation) => {
    calls.push(invocation);
    await fixture(invocation);
  };
}

function emptyReflexRequest(generation: number): SemanticStoryboardRequestV1 {
  return {
    protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    routingMode: "reflex",
    problemSpec: DEFAULT_PROBLEM,
    generation,
    baseScene: { revision: 0, nodes: [] },
    baseSemanticScene: { revision: 0, components: [] },
  };
}

describe("LiveSemanticStoryboard", () => {
  beforeEach(() => {
    product.getAuthHeaders.mockReset();
    canvas.cancelMotion.mockReset();
    canvas.clear.mockReset();
    canvas.materializeScene.mockReset();
    canvas.materializeViewport.mockReset();
    canvas.prepareReplayScene.mockReset();
    canvas.finishReplayScene.mockReset();
    canvas.playCheckpointChoreography
      .mockReset()
      .mockImplementation(immediatePlayback);
  });

  afterEach(async () => {
    for (const mounted of [...mountedRoots]) await unmount(mounted);
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it("settles the provider-free anchor before Director, locks the problem, and exposes ordered certified progress", async () => {
    const calls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const snapshots: SemanticStoryboardSessionSnapshot[] = [];
    const mounted = await mount({
      reducedMotion: false,
      runStream: fixtureRunner(calls),
      onSessionSnapshot: (snapshot) => snapshots.push(snapshot),
    });
    expect(
      mounted.container
        .querySelector('[data-testid="logo"]')
        ?.getAttribute("data-animate-on-mount"),
    ).toBe("false");
    expect(
      mounted.container
        .querySelector('[data-testid="logo"]')
        ?.getAttribute("data-reduced-motion"),
    ).toBe("false");

    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(
      () => root(mounted.container).dataset.sessionStatus === "paused",
      "completed fresh storyboard",
    );

    expect(calls).toHaveLength(2);
    expect(calls[0].request).toMatchObject({
      routingMode: "reflex",
      generation: 1,
      baseScene: { revision: 0 },
      baseSemanticScene: { revision: 0 },
    });
    expect(calls[1].request).toMatchObject({
      routingMode: "director",
      generation: 2,
      prompt: DEFAULT_PROMPT,
      baseScene: { revision: 1 },
      baseSemanticScene: { revision: 1 },
    });
    const final = snapshots.at(-1);
    expect(
      final?.runtime.accepted.map(
        (accepted) => accepted.event.transition.checkpoint.checkpointId,
      ),
    ).toEqual([
      "storyboard-anchor",
      "storyboard-checkpoint-trace-lower-angle",
      "storyboard-checkpoint-trace-higher-angle",
      "storyboard-checkpoint-relate-equal-range",
    ]);
    expect(
      mounted.container.querySelector<HTMLFieldSetElement>(
        '[data-testid="storyboard-speed-fieldset"]',
      )?.disabled,
    ).toBe(true);
    expect(
      mounted.container.querySelector<HTMLFieldSetElement>(
        '[data-testid="storyboard-angle-fieldset"]',
      )?.disabled,
    ).toBe(true);
    expect(promptInput(mounted.container).disabled).toBe(false);
    expect(button(mounted.container, "Continue from here").disabled).toBe(
      false,
    );
    expect(stage(mounted.container).dataset.settledBeatCount).toBe("3");
    expect(root(mounted.container)).toMatchObject({
      dataset: expect.objectContaining({
        acceptedAngles: "30:60",
        sceneRevision: "4",
        semanticRevision: "4",
        rendererTrusted: "true",
        lastRoute: "director",
      }),
    });
    expect(root(mounted.container).dataset.programSha256).toMatch(
      /^[0-9a-f]{64}$/,
    );
    expect(root(mounted.container).dataset.certificateHead).toBe(
      root(mounted.container).dataset.programSha256
        ? final?.runtime.committedSemanticScene.certificateHeadSha256
        : undefined,
    );
  });

  it("stops at the accepted beat, suppresses late stream events, and unlocks the prompt", async () => {
    const calls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const fixture = createSemanticStoryboardFixtureRunner({
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    let lateEventCount = 0;
    const runStream: SemanticStoryboardSceneStreamRunner = async (
      invocation,
    ) => {
      calls.push(invocation);
      if (invocation.request.routingMode === "reflex") {
        await fixture(invocation);
        return;
      }
      const batch = createSemanticStoryboardFixtureBatch(invocation.request);
      invocation.onEvent(batch.events[0]);
      invocation.onEvent(batch.events[1]);
      await new Promise<void>((resolve) => {
        if (invocation.signal.aborted) resolve();
        else
          invocation.signal.addEventListener("abort", () => resolve(), {
            once: true,
          });
      });
      for (const event of batch.events.slice(2)) {
        lateEventCount += 1;
        invocation.onEvent(event);
      }
    };
    const mounted = await mount({ runStream });

    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(
      () =>
        root(mounted.container).dataset.settledBeatCount === "1" &&
        root(mounted.container).dataset.sessionStatus === "directing",
      "one live Director beat",
    );
    expect(promptInput(mounted.container).disabled).toBe(true);
    expect(button(mounted.container, "Stop at this beat")).toBeTruthy();

    await act(async () =>
      button(mounted.container, "Stop at this beat").click(),
    );
    await waitFor(
      () => root(mounted.container).dataset.sessionStatus === "paused",
      "interrupted frontier",
    );
    await act(
      () => new Promise<void>((resolve) => globalThis.setTimeout(resolve, 10)),
    );

    expect(lateEventCount).toBeGreaterThan(0);
    expect(root(mounted.container).dataset.settledBeatCount).toBe("1");
    expect(root(mounted.container).dataset.sceneRevision).toBe("2");
    expect(promptInput(mounted.container).disabled).toBe(false);
    expect(calls).toHaveLength(2);
  });

  it("mount-locks transport across an active rerender and aborts it on unmount", async () => {
    const fixture = createSemanticStoryboardFixtureRunner({
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    const firstCalls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    let releaseAnchor!: () => void;
    const anchorGate = new Promise<void>((resolve) => {
      releaseAnchor = resolve;
    });
    const firstRunner: SemanticStoryboardSceneStreamRunner = async (
      invocation,
    ) => {
      firstCalls.push(invocation);
      if (firstCalls.length === 1) await anchorGate;
      if (firstCalls.length === 3) {
        await new Promise<void>((resolve) => {
          if (invocation.signal.aborted) resolve();
          else {
            invocation.signal.addEventListener("abort", () => resolve(), {
              once: true,
            });
          }
        });
        return;
      }
      await fixture(invocation);
    };
    const replacementRunner = vi.fn<SemanticStoryboardSceneStreamRunner>();
    const mounted = await mount({
      initialPrompt: HIGHER_ARC_PROMPT,
      runStream: firstRunner,
    });

    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(() => firstCalls.length === 1, "blocked anchor request");
    await act(async () => {
      mounted.root.render(
        <LiveSemanticStoryboard
          layout="cinematic"
          reducedMotion
          initialPrompt={HIGHER_ARC_PROMPT}
          runStream={replacementRunner}
        />,
      );
    });
    expect(root(mounted.container).dataset.sessionStatus).toBe("anchoring");
    expect(promptInput(mounted.container).disabled).toBe(true);
    expect(replacementRunner).not.toHaveBeenCalled();

    await act(async () => releaseAnchor());
    await waitFor(
      () => root(mounted.container).dataset.settledBeatCount === "4",
      "original runner completion after rerender",
    );
    expect(firstCalls).toHaveLength(2);
    expect(replacementRunner).not.toHaveBeenCalled();

    await setPrompt(mounted.container, CONTINUE_PROMPT);
    await act(async () =>
      button(mounted.container, "Continue from here").click(),
    );
    await waitFor(() => firstCalls.length === 3, "held continuation");
    const heldSignal = firstCalls[2].signal;
    expect(heldSignal.aborted).toBe(false);
    await unmount(mounted);
    await act(
      () => new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0)),
    );
    expect(heldSignal.aborted).toBe(true);
    expect(replacementRunner).not.toHaveBeenCalled();
  });

  it("continues from the exact accepted frontier without clearing living-board ink", async () => {
    const calls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const mounted = await mount({
      initialPrompt: HIGHER_ARC_PROMPT,
      runStream: fixtureRunner(calls),
    });

    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(
      () => root(mounted.container).dataset.settledBeatCount === "4",
      "four-beat initial story",
    );
    await setPrompt(mounted.container, CONTINUE_PROMPT);
    await act(async () =>
      button(mounted.container, "Continue from here").click(),
    );
    await waitFor(
      () => root(mounted.container).dataset.settledBeatCount === "5",
      "continued story",
    );

    expect(calls).toHaveLength(3);
    expect(calls[2].request).toMatchObject({
      routingMode: "director",
      prompt: CONTINUE_PROMPT,
      generation: 3,
      baseScene: { revision: 5 },
      baseSemanticScene: { revision: 5 },
    });
    expect(canvas.clear).not.toHaveBeenCalled();
    expect(root(mounted.container).dataset.sceneRevision).toBe("6");
  });

  it("replays without a stream and resets the complete session", async () => {
    const calls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const mounted = await mount({ runStream: fixtureRunner(calls) });

    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(
      () => root(mounted.container).dataset.sessionStatus === "paused",
      "initial storyboard",
    );
    const acceptedProgram = root(mounted.container).dataset.programSha256;

    await act(async () => button(mounted.container, "Replay").click());
    await waitFor(
      () =>
        root(mounted.container).dataset.sessionStatus === "paused" &&
        canvas.finishReplayScene.mock.calls.length === 1,
      "local Replay",
    );
    expect(calls).toHaveLength(2);
    expect(canvas.prepareReplayScene).toHaveBeenCalledOnce();
    expect(root(mounted.container).dataset.programSha256).toBe(acceptedProgram);

    await act(async () => button(mounted.container, "Reset").click());
    await waitFor(
      () => root(mounted.container).dataset.sessionStatus === "ready",
      "reset session",
    );
    expect(root(mounted.container).dataset.settledBeatCount).toBe("0");
    expect(root(mounted.container).dataset.acceptedAngles).toBe("none");
    expect(root(mounted.container).dataset.programSha256).toBe("none");
    expect(canvas.clear).toHaveBeenCalledOnce();
    expect(
      mounted.container.querySelector<HTMLFieldSetElement>(
        '[data-testid="storyboard-speed-fieldset"]',
      )?.disabled,
    ).toBe(false);
  });

  it("keeps the accepted caption through accepted-prefix, decline, and retryable failure terminals", async () => {
    const prefixCalls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const prefix = await mount({
      initialPrompt: PREFIX_PROMPT,
      runStream: fixtureRunner(prefixCalls),
    });
    await act(async () => button(prefix.container, "Make it visible").click());
    await waitFor(
      () => root(prefix.container).dataset.sessionStatus === "paused",
      "accepted prefix",
    );
    expect(root(prefix.container).dataset.completionReason).toBe(
      "accepted_prefix",
    );
    expect(root(prefix.container).dataset.completionDetail).toBe(
      "invalid_model_stream",
    );
    expect(stage(prefix.container).textContent).toContain(
      "range is controlled by",
    );
    expect(
      prefix.container.querySelector(
        '[data-testid="semantic-storyboard-accepted-prefix"]',
      ),
    ).toBeTruthy();
    await unmount(prefix);

    const declined = await mount({
      initialPrompt: ABSTAIN_PROMPT,
      runStream: fixtureRunner([]),
    });
    await act(async () =>
      button(declined.container, "Make it visible").click(),
    );
    await waitFor(
      () => root(declined.container).dataset.sessionStatus === "declined",
      "declined Director turn",
    );
    expect(stage(declined.container).textContent).toContain(ANCHOR_CAPTION);
    expect(stage(declined.container).textContent).not.toContain(
      "outside the supported storyboard vocabulary",
    );
    expect(
      declined.container.querySelector(
        '[data-testid="semantic-storyboard-decline"]',
      )?.textContent,
    ).toContain("outside the supported storyboard vocabulary");
    await unmount(declined);

    const fixture = createSemanticStoryboardFixtureRunner({
      eventDelayMs: 0,
      chunkDelayMs: 0,
    });
    const failedRunner: SemanticStoryboardSceneStreamRunner = async (
      invocation,
    ) => {
      if (invocation.request.routingMode === "reflex") {
        await fixture(invocation);
        return;
      }
      const baseRevision = invocation.request.baseScene.revision;
      invocation.onEvent(
        decodeSemanticStoryboardSceneStreamEventV1({
          type: "semantic_storyboard_scene_stream_started",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision,
        }),
      );
      invocation.onEvent(
        decodeSemanticStoryboardSceneStreamEventV1({
          type: "semantic_storyboard_scene_stream_failed",
          generation: invocation.request.generation,
          attempt: 1,
          baseRevision,
          code: "provider_error",
          message: "The Director connection stopped safely.",
          lastAcceptedRevision: baseRevision,
          retryable: true,
        }),
      );
    };
    const failed = await mount({ runStream: failedRunner });
    await act(async () => button(failed.container, "Make it visible").click());
    await waitFor(
      () => root(failed.container).dataset.sessionStatus === "failed",
      "retryable failure",
    );
    expect(stage(failed.container).textContent).toContain(ANCHOR_CAPTION);
    expect(
      failed.container.querySelector('[role="alert"]')?.textContent,
    ).toContain("Director connection stopped safely");
    expect(root(failed.container).dataset.rendererTrusted).toBe("true");
    expect(promptInput(failed.container).disabled).toBe(false);
    expect(button(failed.container, "Continue from here").disabled).toBe(false);
  });

  it("counts Unicode code points at 2000, rejects 2001, and does not truncate the textarea", async () => {
    const calls: SemanticStoryboardSceneStreamRunInvocation[] = [];
    const runStream: SemanticStoryboardSceneStreamRunner = async (
      invocation,
    ) => {
      calls.push(invocation);
      await new Promise<void>((resolve) => {
        if (invocation.signal.aborted) resolve();
        else
          invocation.signal.addEventListener("abort", () => resolve(), {
            once: true,
          });
      });
    };
    const mounted = await mount({ runStream });
    const input = promptInput(mounted.container);
    expect(input.hasAttribute("maxlength")).toBe(false);

    await setPrompt(mounted.container, "🌀".repeat(2_001));
    expect(mounted.container.textContent).toContain("2001/2000 code points");
    await act(async () => button(mounted.container, "Make it visible").click());
    expect(calls).toHaveLength(0);
    expect(
      mounted.container.querySelector('[role="alert"]')?.textContent,
    ).toContain("at most 2000 characters");

    await setPrompt(mounted.container, "🌀".repeat(2_000));
    expect(mounted.container.textContent).toContain("2000/2000 code points");
    await act(async () => button(mounted.container, "Make it visible").click());
    await waitFor(() => calls.length === 1, "valid 2000-code-point request");
    expect(calls[0].request).toMatchObject({
      routingMode: "reflex",
      problemSpec: DEFAULT_PROBLEM,
    });
  });

  it("uses explicit compact reduced-motion preferences with one polite region and no provider selector", async () => {
    const mounted = await mount({
      layout: "compact",
      reducedMotion: true,
      playbackRate: 16,
      runStream: vi.fn(),
      backHref: "/labs",
    });

    expect(root(mounted.container).dataset.layout).toBe("compact");
    expect(root(mounted.container).dataset.reducedMotion).toBe("true");
    expect(stage(mounted.container).dataset.layout).toBe("compact");
    expect(
      mounted.container
        .querySelector('[data-testid="semantic-storyboard-canvas"]')
        ?.getAttribute("data-reduced-motion"),
    ).toBe("true");
    expect(
      mounted.container
        .querySelector('[data-testid="semantic-storyboard-canvas"]')
        ?.getAttribute("data-playback-rate"),
    ).toBe("16");
    expect(
      mounted.container
        .querySelector('[data-testid="logo"]')
        ?.getAttribute("data-reduced-motion"),
    ).toBe("true");
    expect(
      mounted.container
        .querySelector('[data-testid="logo"]')
        ?.getAttribute("data-animate-on-mount"),
    ).toBe("false");
    expect(
      mounted.container.querySelectorAll('[aria-live="polite"]'),
    ).toHaveLength(1);
    expect(
      mounted.container
        .querySelector('a[aria-label="Back"]')
        ?.getAttribute("href"),
    ).toBe("/labs");
    expect(mounted.container.textContent?.toLowerCase()).not.toContain(
      "provider selector",
    );
    expect(product.getAuthHeaders).not.toHaveBeenCalled();
  });
});

describe("runAuthenticatedSemanticStoryboardStream", () => {
  it("resolves a fresh bearer for every product request and fails before fetch when signed out", async () => {
    product.getAuthHeaders
      .mockResolvedValueOnce({ Authorization: "Bearer storyboard-token-1" })
      .mockResolvedValueOnce({ Authorization: "Bearer storyboard-token-2" })
      .mockResolvedValueOnce({});
    const response = (generation: number) =>
      new Response(
        `data: ${JSON.stringify({
          type: "semantic_storyboard_scene_stream_started",
          generation,
          attempt: 1,
          baseRevision: 0,
        })}\n\n`,
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(1))
      .mockResolvedValueOnce(response(2));
    vi.stubGlobal("fetch", fetchImpl);

    await runAuthenticatedSemanticStoryboardStream({
      request: emptyReflexRequest(1),
      signal: new AbortController().signal,
      onEvent: vi.fn(),
    });
    await runAuthenticatedSemanticStoryboardStream({
      request: emptyReflexRequest(2),
      signal: new AbortController().signal,
      onEvent: vi.fn(),
    });

    expect(product.getAuthHeaders).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      "http://localhost:8000/api/live-scenes/choreography/stream",
    );
    expect(fetchImpl.mock.calls[0]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer storyboard-token-1",
    });
    expect(fetchImpl.mock.calls[1]?.[1]?.headers).toMatchObject({
      Authorization: "Bearer storyboard-token-2",
    });

    await expect(
      runAuthenticatedSemanticStoryboardStream({
        request: emptyReflexRequest(3),
        signal: new AbortController().signal,
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow("Sign in again to direct a live visual explanation.");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
