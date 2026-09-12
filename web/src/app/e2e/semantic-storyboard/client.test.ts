/** @vitest-environment happy-dom */

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
  decodeSemanticStoryboardRequestV1,
} from "@/lib/live-scene/semantic-storyboard";
import type { SemanticStoryboardSceneStreamEventV1 } from "@/lib/live-scene/semantic-storyboard-stream";
import type { SemanticStoryboardSessionSnapshot } from "@/features/live-scene/semantic-storyboard-session-controller";

const proof = vi.hoisted(() => ({
  fixtureOptions: [] as unknown[],
  liveProps: null as Record<string, unknown> | null,
}));

vi.mock(
  "@/features/live-scene/semantic-storyboard-scene-stream-fixture",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@/features/live-scene/semantic-storyboard-scene-stream-fixture")
      >();
    return {
      ...actual,
      createSemanticStoryboardFixtureRunner: (
        options?: Parameters<
          typeof actual.createSemanticStoryboardFixtureRunner
        >[0],
      ) => {
        proof.fixtureOptions.push(options);
        return actual.createSemanticStoryboardFixtureRunner(options);
      },
    };
  },
);

vi.mock("@/features/live-scene/live-semantic-storyboard", () => ({
  LiveSemanticStoryboard: (props: Record<string, unknown>) => {
    proof.liveProps = props;
    return null;
  },
}));

import {
  SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY,
  SemanticStoryboardE2EClient,
  createSemanticStoryboardE2ESession,
  type SemanticStoryboardE2EBridgeV1,
} from "./client";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

const PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  anglesDeg: Object.freeze([30, 60]),
} as const);
const DIRECTOR_PROMPT =
  "Begin with the higher arc, then compare height and time.";
const CONTINUE_PROMPT = "Continue with exactly one new useful visual beat.";

let root: Root | null = null;
let container: HTMLDivElement | null = null;

function bridgeOwner(): typeof window & Record<string, unknown> {
  return window as typeof window & Record<string, unknown>;
}

function installedBridge(): SemanticStoryboardE2EBridgeV1 | undefined {
  return bridgeOwner()[SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY] as
    SemanticStoryboardE2EBridgeV1 | undefined;
}

function checkpoint(
  events: readonly SemanticStoryboardSceneStreamEventV1[],
  ordinal = 0,
) {
  const checkpoints = events.filter(
    (event) => event.type === "semantic_storyboard_scene_checkpoint",
  );
  const value = checkpoints[ordinal];
  if (!value)
    throw new Error(`Missing semantic storyboard checkpoint ${ordinal}`);
  return value;
}

beforeEach(() => {
  proof.fixtureOptions.length = 0;
  proof.liveProps = null;
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = null;
  container?.remove();
  container = null;
  delete bridgeOwner()[SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("semantic-storyboard e2e client session", () => {
  it("records exact request frontiers while the real fixture runner stays provider-free", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const session = createSemanticStoryboardE2ESession(false);

    const anchorEvents: SemanticStoryboardSceneStreamEventV1[] = [];
    await session.runner({
      request: decodeSemanticStoryboardRequestV1({
        protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
        routingMode: "reflex",
        problemSpec: PROBLEM,
        generation: 1,
        baseScene: { revision: 0, nodes: [] },
        baseSemanticScene: { revision: 0, components: [] },
      }),
      signal: new AbortController().signal,
      onEvent: (event) => anchorEvents.push(event),
    });

    const anchor = checkpoint(anchorEvents);
    const directorEvents: SemanticStoryboardSceneStreamEventV1[] = [];
    await session.runner({
      request: decodeSemanticStoryboardRequestV1({
        protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
        routingMode: "director",
        prompt: DIRECTOR_PROMPT,
        problemSpec: PROBLEM,
        generation: 2,
        baseScene: anchor.transition.resultScene,
        baseSemanticScene: anchor.transition.resultSemanticScene,
      }),
      signal: new AbortController().signal,
      onEvent: (event) => directorEvents.push(event),
    });

    const firstBeat = checkpoint(directorEvents);
    await session.runner({
      request: decodeSemanticStoryboardRequestV1({
        protocol: PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
        routingMode: "director",
        prompt: CONTINUE_PROMPT,
        problemSpec: PROBLEM,
        generation: 3,
        baseScene: firstBeat.transition.resultScene,
        baseSemanticScene: firstBeat.transition.resultSemanticScene,
      }),
      signal: new AbortController().signal,
      onEvent: () => undefined,
    });

    const state = session.bridge.getState();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(proof.fixtureOptions).toEqual([undefined]);
    expect(state).toEqual({
      runnerCallCount: 3,
      calls: [
        {
          ordinal: 1,
          generation: 1,
          routingMode: "reflex",
          prompt: null,
          problemSpec: PROBLEM,
          baseRevision: 0,
          semanticRevision: 0,
          certificateHeadSha256: null,
          acceptedRecordCount: 0,
        },
        {
          ordinal: 2,
          generation: 2,
          routingMode: "director",
          prompt: DIRECTOR_PROMPT,
          problemSpec: PROBLEM,
          baseRevision: 1,
          semanticRevision: 1,
          certificateHeadSha256:
            anchor.transition.resultSemanticScene.certificateHeadSha256,
          acceptedRecordCount: 0,
        },
        {
          ordinal: 3,
          generation: 3,
          routingMode: "director",
          prompt: CONTINUE_PROMPT,
          problemSpec: PROBLEM,
          baseRevision: 2,
          semanticRevision: 2,
          certificateHeadSha256:
            firstBeat.transition.resultSemanticScene.certificateHeadSha256,
          acceptedRecordCount: 1,
        },
      ],
    });
    expect(Object.isFrozen(session.bridge)).toBe(true);
    expect(Object.isFrozen(state)).toBe(true);
    expect(Object.isFrozen(state.calls)).toBe(true);
    expect(Object.isFrozen(state.calls[0])).toBe(true);
    expect(Object.isFrozen(state.calls[0].problemSpec)).toBe(true);
    expect(Object.isFrozen(state.calls[0].problemSpec.anglesDeg)).toBe(true);
    expect(session.bridge.getSessionObservation()).toBeNull();
    expect(session.bridge.getSessionObservationHistory()).toEqual([]);
  });

  it("installs one keyframe session bridge, forwards locked props, and removes it", async () => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    const props = {
      layout: "compact" as const,
      reducedMotion: true,
      playbackRate: 16 as const,
      keyframeProof: true,
    };

    await act(async () =>
      root?.render(createElement(SemanticStoryboardE2EClient, props)),
    );

    expect(proof.fixtureOptions).toEqual([{ eventDelayMs: 1_200 }]);
    expect(installedBridge()?.version).toBe(1);
    expect(proof.liveProps).toMatchObject({
      backHref: "/",
      layout: "compact",
      reducedMotion: true,
      playbackRate: 16,
    });
    expect(proof.liveProps?.runStream).toBeTypeOf("function");
    expect(proof.liveProps?.onSessionSnapshot).toBeTypeOf("function");

    const initialRunner = proof.liveProps?.runStream;
    await act(async () =>
      root?.render(createElement(SemanticStoryboardE2EClient, props)),
    );
    expect(proof.fixtureOptions).toHaveLength(1);
    expect(proof.liveProps?.runStream).toBe(initialRunner);

    vi.spyOn(performance, "now")
      .mockReturnValueOnce(12.5)
      .mockReturnValueOnce(25);
    const firstSnapshot = Object.freeze({
      status: "ready",
    }) as unknown as SemanticStoryboardSessionSnapshot;
    const secondSnapshot = Object.freeze({
      status: "paused",
    }) as unknown as SemanticStoryboardSessionSnapshot;
    const observe = proof.liveProps?.onSessionSnapshot as (
      snapshot: SemanticStoryboardSessionSnapshot,
    ) => void;
    observe(firstSnapshot);
    observe(secondSnapshot);

    const bridge = installedBridge();
    expect(bridge?.getSessionObservation()).toEqual({
      observedAtMs: 25,
      snapshot: secondSnapshot,
    });
    const history = bridge?.getSessionObservationHistory();
    expect(history).toEqual([
      { observedAtMs: 12.5, snapshot: firstSnapshot },
      { observedAtMs: 25, snapshot: secondSnapshot },
    ]);
    expect(Object.isFrozen(history)).toBe(true);
    expect(history?.every(Object.isFrozen)).toBe(true);

    await act(async () => root?.unmount());
    root = null;
    expect(installedBridge()).toBeUndefined();
  });
});
