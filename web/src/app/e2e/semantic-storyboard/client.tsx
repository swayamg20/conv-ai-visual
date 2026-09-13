"use client";

import { useEffect, useState } from "react";

import {
  LiveSemanticStoryboard,
  type LiveSemanticStoryboardProps,
} from "@/features/live-scene/live-semantic-storyboard";
import {
  createSemanticStoryboardFixtureRunner,
  type SemanticStoryboardFixtureRunnerOptions,
} from "@/features/live-scene/semantic-storyboard-scene-stream-fixture";
import type {
  SemanticStoryboardSceneStreamRunInvocation,
  SemanticStoryboardSceneStreamRunner,
} from "@/features/live-scene/semantic-storyboard-model-stream";
import type { SemanticStoryboardSessionSnapshot } from "@/features/live-scene/semantic-storyboard-session-controller";
import type { PairedProjectileComparisonSpecV1 } from "@/lib/live-scene/semantic-storyboard";
import type { SemanticStoryboardSceneStreamEventV1 } from "@/lib/live-scene/semantic-storyboard-stream";

import type { SemanticStoryboardE2EOptions } from "./options";

export const SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY =
  "__MURMUR_SEMANTIC_STORYBOARD_E2E__" as const;

export interface SemanticStoryboardFixtureInvocationObservation {
  readonly ordinal: number;
  readonly observedAtMs: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly prompt: string | null;
  readonly problemSpec: PairedProjectileComparisonSpecV1;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly acceptedRecordCount: number;
}

export interface SemanticStoryboardFixtureEventObservation {
  readonly ordinal: number;
  readonly observedAtMs: number;
  readonly type: SemanticStoryboardSceneStreamEventV1["type"];
  readonly generation: number;
  readonly sequence: number | null;
  readonly resultRevision: number | null;
  readonly checkpointId: string | null;
}

export interface SemanticStoryboardSessionObservation {
  readonly observedAtMs: number;
  readonly snapshot: SemanticStoryboardSessionSnapshot;
}

export interface SemanticStoryboardE2EBridgeV1 {
  readonly version: 1;
  getState(): {
    readonly runnerCallCount: number;
    readonly calls: readonly SemanticStoryboardFixtureInvocationObservation[];
  };
  getEventHistory(): readonly SemanticStoryboardFixtureEventObservation[];
  getSessionObservation(): SemanticStoryboardSessionObservation | null;
  getSessionObservationHistory(): readonly SemanticStoryboardSessionObservation[];
}

export interface SemanticStoryboardE2ESession {
  readonly runner: SemanticStoryboardSceneStreamRunner;
  readonly bridge: SemanticStoryboardE2EBridgeV1;
  readonly observeSessionSnapshot: (
    snapshot: SemanticStoryboardSessionSnapshot,
  ) => void;
}

function copyProblemSpec(
  problemSpec: PairedProjectileComparisonSpecV1,
): PairedProjectileComparisonSpecV1 {
  return Object.freeze({
    ...problemSpec,
    anglesDeg: Object.freeze([...problemSpec.anglesDeg]) as readonly [
      (typeof problemSpec.anglesDeg)[0],
      (typeof problemSpec.anglesDeg)[1],
    ],
  });
}

function observeInvocation(
  invocation: SemanticStoryboardSceneStreamRunInvocation,
  ordinal: number,
  observedAtMs: number,
): SemanticStoryboardFixtureInvocationObservation {
  const request = invocation.request;
  const component = request.baseSemanticScene.components[0];
  return Object.freeze({
    ordinal,
    observedAtMs,
    generation: request.generation,
    routingMode: request.routingMode,
    prompt: request.routingMode === "director" ? request.prompt : null,
    problemSpec: copyProblemSpec(request.problemSpec),
    baseRevision: request.baseScene.revision,
    semanticRevision: request.baseSemanticScene.revision,
    certificateHeadSha256:
      request.baseSemanticScene.certificateHeadSha256 ?? null,
    acceptedRecordCount: component?.acceptedRecords.length ?? 0,
  });
}

function observeEvent(
  event: SemanticStoryboardSceneStreamEventV1,
  ordinal: number,
  observedAtMs: number,
): SemanticStoryboardFixtureEventObservation {
  return Object.freeze({
    ordinal,
    observedAtMs,
    type: event.type,
    generation: event.generation,
    sequence:
      event.type === "semantic_storyboard_scene_checkpoint"
        ? event.sequence
        : null,
    resultRevision:
      event.type === "semantic_storyboard_scene_checkpoint"
        ? event.resultRevision
        : null,
    checkpointId:
      event.type === "semantic_storyboard_scene_checkpoint"
        ? event.transition.checkpoint.checkpointId
        : null,
  });
}

/** Create one provider-free fixture session for a single route mount. */
export function createSemanticStoryboardE2ESession(
  keyframeProof: boolean,
): SemanticStoryboardE2ESession {
  const fixtureOptions: SemanticStoryboardFixtureRunnerOptions | undefined =
    keyframeProof ? { eventDelayMs: 1_200 } : undefined;
  const fixtureRunner = createSemanticStoryboardFixtureRunner(fixtureOptions);
  const calls: SemanticStoryboardFixtureInvocationObservation[] = [];
  const events: SemanticStoryboardFixtureEventObservation[] = [];
  let sessionObservation: SemanticStoryboardSessionObservation | null = null;
  const sessionObservationHistory: SemanticStoryboardSessionObservation[] = [];

  const runner: SemanticStoryboardSceneStreamRunner = async (invocation) => {
    calls.push(
      observeInvocation(invocation, calls.length + 1, performance.now()),
    );
    await fixtureRunner({
      ...invocation,
      onEvent: (event) => {
        events.push(observeEvent(event, events.length + 1, performance.now()));
        invocation.onEvent(event);
      },
    });
  };

  return Object.freeze({
    runner,
    observeSessionSnapshot: (snapshot: SemanticStoryboardSessionSnapshot) => {
      sessionObservation = Object.freeze({
        observedAtMs: performance.now(),
        snapshot,
      });
      sessionObservationHistory.push(sessionObservation);
    },
    bridge: Object.freeze({
      version: 1 as const,
      getState: () =>
        Object.freeze({
          runnerCallCount: calls.length,
          calls: Object.freeze([...calls]),
        }),
      getEventHistory: () => Object.freeze([...events]),
      getSessionObservation: () => sessionObservation,
      getSessionObservationHistory: () =>
        Object.freeze([...sessionObservationHistory]),
    }),
  });
}

interface SemanticStoryboardE2EClientProps extends SemanticStoryboardE2EOptions {
  readonly backHref?: LiveSemanticStoryboardProps["backHref"];
}

export function SemanticStoryboardE2EClient({
  layout,
  reducedMotion,
  playbackRate,
  keyframeProof,
  backHref = "/",
}: SemanticStoryboardE2EClientProps) {
  const [session] = useState(() =>
    createSemanticStoryboardE2ESession(keyframeProof),
  );

  useEffect(() => {
    const owner = window as typeof window & Record<string, unknown>;
    owner[SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY] = session.bridge;
    return () => {
      if (owner[SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY] === session.bridge) {
        delete owner[SEMANTIC_STORYBOARD_E2E_BRIDGE_KEY];
      }
    };
  }, [session]);

  return (
    <LiveSemanticStoryboard
      backHref={backHref}
      layout={layout}
      reducedMotion={reducedMotion}
      playbackRate={playbackRate}
      runStream={session.runner}
      onSessionSnapshot={session.observeSessionSnapshot}
    />
  );
}
