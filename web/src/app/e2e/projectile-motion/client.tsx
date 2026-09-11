"use client";

import { useEffect, useState } from "react";

import {
  LiveProjectileChoreography,
  type LiveProjectileChoreographyProps,
} from "@/features/live-scene/live-projectile-choreography";
import {
  createProjectileChoreographyFixtureRunner,
  type ProjectileChoreographyFixtureMode,
} from "@/features/live-scene/projectile-choreography-scene-stream-fixture";
import type {
  ProjectileChoreographySceneStreamRunInvocation,
  ProjectileChoreographySceneStreamRunner,
} from "@/features/live-scene/projectile-choreography-model-stream";
import type {
  ProjectileMotionProblemSpecV1,
  ProjectileMotionRouteV1,
} from "@/lib/live-scene/projectile-motion";

import type { ProjectileMotionE2EOptions } from "./options";

export const PROJECTILE_MOTION_E2E_BRIDGE_KEY =
  "__MURMUR_PROJECTILE_MOTION_E2E__" as const;

export interface ProjectileMotionFixtureInvocationObservation {
  readonly ordinal: number;
  readonly generation: number;
  readonly routingMode: "reflex" | "director";
  readonly problemSpec: ProjectileMotionProblemSpecV1;
  readonly baseRevision: number;
  readonly semanticRevision: number;
  readonly certificateHeadSha256: string | null;
  readonly checkpointId: string | null;
  readonly clarifiedTopics: readonly string[];
  readonly activeClarification: string | null;
  readonly requestedRoute: ProjectileMotionRouteV1 | null;
}

export interface ProjectileMotionE2EBridgeV1 {
  readonly version: 1;
  getState(): {
    readonly runnerCallCount: number;
    readonly calls: readonly ProjectileMotionFixtureInvocationObservation[];
  };
}

interface ProjectileMotionE2ESession {
  readonly runner: ProjectileChoreographySceneStreamRunner;
  readonly bridge: ProjectileMotionE2EBridgeV1;
}

function observeInvocation(
  invocation: ProjectileChoreographySceneStreamRunInvocation,
  ordinal: number,
): ProjectileMotionFixtureInvocationObservation {
  const component = invocation.request.baseSemanticScene.components.find(
    (candidate) => candidate.kind === "projectile_motion",
  );
  return Object.freeze({
    ordinal,
    generation: invocation.request.generation,
    routingMode: invocation.request.routingMode,
    problemSpec: Object.freeze({ ...invocation.request.problemSpec }),
    baseRevision: invocation.request.baseScene.revision,
    semanticRevision: invocation.request.baseSemanticScene.revision,
    certificateHeadSha256:
      invocation.request.baseSemanticScene.certificateHeadSha256 ?? null,
    checkpointId: component?.lastMainCheckpoint ?? null,
    clarifiedTopics: Object.freeze([...(component?.clarifiedTopics ?? [])]),
    activeClarification: component?.activeClarification ?? null,
    requestedRoute:
      invocation.request.routingMode === "reflex"
        ? invocation.request.requestedRoute
        : null,
  });
}

export function createProjectileMotionE2ESession(
  mode: ProjectileChoreographyFixtureMode,
  keyframeProof: boolean,
): ProjectileMotionE2ESession {
  const fixtureRunner = createProjectileChoreographyFixtureRunner(
    keyframeProof ? { mode, eventDelayMs: 1_200 } : { mode },
  );
  const calls: ProjectileMotionFixtureInvocationObservation[] = [];
  const runner: ProjectileChoreographySceneStreamRunner = async (
    invocation,
  ) => {
    calls.push(observeInvocation(invocation, calls.length + 1));
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

interface ProjectileMotionE2EClientProps extends ProjectileMotionE2EOptions {
  readonly backHref?: LiveProjectileChoreographyProps["backHref"];
}

export function ProjectileMotionE2EClient({
  layout,
  reducedMotion,
  flow,
  playbackRate,
  keyframeProof,
  backHref = "/",
}: ProjectileMotionE2EClientProps) {
  const [session] = useState(() =>
    createProjectileMotionE2ESession(flow, keyframeProof),
  );

  useEffect(() => {
    const owner = window as typeof window & Record<string, unknown>;
    owner[PROJECTILE_MOTION_E2E_BRIDGE_KEY] = session.bridge;
    return () => {
      if (owner[PROJECTILE_MOTION_E2E_BRIDGE_KEY] === session.bridge) {
        delete owner[PROJECTILE_MOTION_E2E_BRIDGE_KEY];
      }
    };
  }, [session]);

  return (
    <LiveProjectileChoreography
      backHref={backHref}
      layout={layout}
      reducedMotion={reducedMotion}
      playbackRate={playbackRate}
      runStream={session.runner}
    />
  );
}
