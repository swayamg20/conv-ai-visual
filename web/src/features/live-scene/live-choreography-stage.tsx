"use client";

import type { RefObject } from "react";

import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
} from "@/lib/live-scene";

import { CertifiedChoreographyStage } from "./certified-choreography-stage";
import type { SceneStreamRuntimePhase } from "./stream-runtime";

const MAIN_CHECKPOINTS = COMPLETING_SQUARE_CHECKPOINT_IDS.filter(
  (
    checkpoint,
  ): checkpoint is Exclude<CompletingSquareCheckpointId, "corner_detail"> =>
    checkpoint !== "corner_detail",
);

export const LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT = MAIN_CHECKPOINTS.length;

export interface LiveChoreographyStageProps {
  readonly canvasRef: RefObject<SVGCanvasHandle | null>;
  readonly phase: SceneStreamRuntimePhase;
  readonly layout: ChoreographyLayout;
  readonly checkpointId?: CompletingSquareCheckpointId;
  readonly visibleCheckpointId?: CompletingSquareCheckpointId;
  readonly settledMainCount: number;
  readonly cornerClarified: boolean;
  readonly cornerClarificationLabel?: string;
  readonly caption: string;
  readonly rendererTrusted: boolean;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly className?: string;
}

function checkpointLabel(
  checkpointId: CompletingSquareCheckpointId | undefined,
): string {
  if (!checkpointId) return "Ready";
  if (checkpointId === "corner_detail") return "Corner clarified";
  return checkpointId.replaceAll("_", " ");
}

/**
 * The capture-safe surface: one certified board, one cue-synchronous caption,
 * and one compact progress rail. It intentionally owns no transport or
 * controls.
 */
export function LiveChoreographyStage({
  canvasRef,
  phase,
  layout,
  checkpointId,
  visibleCheckpointId,
  settledMainCount,
  cornerClarified,
  cornerClarificationLabel = "3 × 3 corner understood",
  caption,
  rendererTrusted,
  reducedMotion = false,
  playbackRate = 1,
  className,
}: LiveChoreographyStageProps) {
  return (
    <CertifiedChoreographyStage
      canvasRef={canvasRef}
      phase={phase}
      layout={layout}
      subjectLabel="Completing the square"
      checkpointLabel={checkpointLabel(visibleCheckpointId ?? checkpointId)}
      settledMainCount={settledMainCount}
      totalMainCount={LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT}
      settledDetailLabels={
        cornerClarified ? [cornerClarificationLabel] : undefined
      }
      caption={caption}
      rendererTrusted={rendererTrusted}
      reducedMotion={reducedMotion}
      playbackRate={playbackRate}
      className={className}
      testId="live-choreography-stage"
      dataAttributes={{
        "data-checkpoint-id": checkpointId ?? "none",
        "data-visible-checkpoint-id":
          visibleCheckpointId ?? checkpointId ?? "none",
        "data-corner-clarified": cornerClarified ? "true" : "false",
      }}
    />
  );
}
