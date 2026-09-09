"use client";

import { useEffect, useState } from "react";

import {
  LiveParametricChoreography,
  type LiveParametricChoreographyProps,
} from "@/features/live-scene/live-parametric-choreography";
import {
  createParametricChoreographyFixtureRunner,
  type ParametricChoreographyFixtureMode,
} from "@/features/live-scene/parametric-choreography-scene-stream-fixture";
import type {
  ParametricChoreographySceneStreamRunInvocation,
  ParametricChoreographySceneStreamRunner,
} from "@/features/live-scene/parametric-choreography-model-stream";
import { decodeParametricChoreographySceneStreamEventV3 } from "@/lib/live-scene/parametric-choreography-stream";

import type { ParametricChoreographyE2EOptions } from "./options";

export const PARAMETRIC_CHOREOGRAPHY_E2E_BRIDGE_KEY =
  "__MURMUR_PARAMETRIC_CHOREOGRAPHY_E2E__" as const;

interface FixtureInvocationObservation {
  readonly ordinal: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly problemText: string | null;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly checkpointId: string | null;
  readonly cornerClarified: boolean;
  readonly requestedRoute: unknown;
}

export interface ParametricChoreographyE2EBridgeV1 {
  readonly version: 1;
  getState(): {
    readonly runnerCallCount: number;
    readonly calls: readonly FixtureInvocationObservation[];
  };
}

interface ParametricChoreographyE2ESession {
  readonly runner: ParametricChoreographySceneStreamRunner;
  readonly bridge: ParametricChoreographyE2EBridgeV1;
}

const FIXTURE_PROBLEMS = new Set([
  "x² + 2x = 80",
  "x² + 8x = 20",
  "x² + 16x = 17",
]);

function declineUnsupportedFixtureProblem(
  invocation: ParametricChoreographySceneStreamRunInvocation,
): boolean {
  const problemText = invocation.request.problemText;
  if (problemText === null || FIXTURE_PROBLEMS.has(problemText)) return false;

  invocation.onEvent(
    decodeParametricChoreographySceneStreamEventV3({
      type: "scene_stream_started",
      generation: invocation.request.generation,
      attempt: 1,
      baseRevision: invocation.request.baseScene.revision,
    }),
  );
  invocation.onEvent(
    decodeParametricChoreographySceneStreamEventV3({
      type: "parametric_choreography_scene_stream_declined",
      generation: invocation.request.generation,
      attempt: 1,
      finalRevision: invocation.request.baseScene.revision,
      reasonCode: "problem_unsupported",
      message:
        "This provider-free proof supports x² + 2x = 80, x² + 8x = 20, or x² + 16x = 17.",
    }),
  );
  return true;
}

function observeInvocation(
  invocation: ParametricChoreographySceneStreamRunInvocation,
  ordinal: number,
): FixtureInvocationObservation {
  const component = invocation.request.baseSemanticScene.components.find(
    (candidate) => candidate.kind === "completing_square_parametric",
  );
  return Object.freeze({
    ordinal,
    generation: invocation.request.generation,
    routingMode: invocation.request.routingMode,
    problemText: invocation.request.problemText,
    baseRevision: invocation.request.baseScene.revision,
    semanticRevision: invocation.request.baseSemanticScene.revision,
    certificateHeadSha256:
      invocation.request.baseSemanticScene.certificateHeadSha256 ?? null,
    checkpointId: component?.lastMainCheckpoint ?? null,
    cornerClarified: component?.cornerClarified ?? false,
    requestedRoute:
      invocation.request.routingMode === "reflex"
        ? invocation.request.requestedRoute
        : null,
  });
}

function createSession(
  mode: ParametricChoreographyFixtureMode,
  keyframeProof: boolean,
): ParametricChoreographyE2ESession {
  const fixtureRunner = createParametricChoreographyFixtureRunner(
    keyframeProof ? { mode, eventDelayMs: 1_200 } : { mode },
  );
  const calls: FixtureInvocationObservation[] = [];
  const runner: ParametricChoreographySceneStreamRunner = async (invocation) => {
    calls.push(observeInvocation(invocation, calls.length + 1));
    if (declineUnsupportedFixtureProblem(invocation)) return;
    await fixtureRunner(invocation);
  };
  return Object.freeze({
    runner,
    bridge: Object.freeze({
      version: 1 as const,
      getState: () =>
        Object.freeze({
          runnerCallCount: calls.length,
          calls: Object.freeze([...calls]),
        }),
    }),
  });
}

interface ParametricChoreographyE2EClientProps
  extends ParametricChoreographyE2EOptions {
  readonly backHref?: LiveParametricChoreographyProps["backHref"];
}

export function ParametricChoreographyE2EClient({
  layout,
  reducedMotion,
  flow,
  playbackRate,
  keyframeProof,
  backHref = "/",
}: ParametricChoreographyE2EClientProps) {
  const [session] = useState(() => createSession(flow, keyframeProof));

  useEffect(() => {
    const owner = window as typeof window & Record<string, unknown>;
    owner[PARAMETRIC_CHOREOGRAPHY_E2E_BRIDGE_KEY] = session.bridge;
    return () => {
      if (owner[PARAMETRIC_CHOREOGRAPHY_E2E_BRIDGE_KEY] === session.bridge) {
        delete owner[PARAMETRIC_CHOREOGRAPHY_E2E_BRIDGE_KEY];
      }
    };
  }, [session]);

  return (
    <LiveParametricChoreography
      backHref={backHref}
      layout={layout}
      reducedMotion={reducedMotion}
      playbackRate={playbackRate}
      runStream={session.runner}
    />
  );
}
