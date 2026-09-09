"use client";

import type { RefObject } from "react";

import { SVGCanvas } from "@/components/svg-canvas";
import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
} from "@/lib/live-scene";
import { cn } from "@/lib/utils";

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
  const boundedMainCount = Math.min(
    Math.max(settledMainCount, 0),
    LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT,
  );

  return (
    <figure
      className={cn(
        "relative isolate overflow-hidden border border-chalk-faint/25 bg-void",
        layout === "cinematic"
          ? "aspect-video rounded-[1.35rem]"
          : "min-h-[31rem] rounded-2xl",
        className,
      )}
      data-testid="live-choreography-stage"
      data-phase={phase}
      data-checkpoint-id={checkpointId ?? "none"}
      data-visible-checkpoint-id={visibleCheckpointId ?? checkpointId ?? "none"}
      data-settled-main-count={boundedMainCount}
      data-corner-clarified={cornerClarified ? "true" : "false"}
      data-layout={layout}
      data-renderer-trusted={rendererTrusted ? "true" : "false"}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 z-10 h-28 bg-gradient-to-b from-void via-void/72 to-transparent"
      />

      <div className="absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-4 px-4 pt-4 sm:px-6 sm:pt-5">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-chalk-soft">
            Completing the square
          </p>
          <p className="mt-1 truncate text-xs font-medium capitalize text-chalk/80 sm:text-sm">
            {checkpointLabel(visibleCheckpointId ?? checkpointId)}
          </p>
        </div>

        <div
          className="shrink-0"
          aria-label={`${boundedMainCount} of 8 main checkpoints settled`}
        >
          <div
            className="flex items-center justify-end gap-1.5"
            aria-hidden="true"
          >
            {MAIN_CHECKPOINTS.map((checkpoint, index) => (
              <span
                key={checkpoint}
                className={cn(
                  "h-1 w-4 rounded-full transition-[background-color,opacity] duration-300 motion-reduce:transition-none sm:w-5",
                  index < boundedMainCount
                    ? "bg-amber opacity-100"
                    : "bg-chalk-faint opacity-35",
                )}
              />
            ))}
          </div>
          {cornerClarified && (
            <p className="mt-2 text-right font-mono text-[8px] uppercase tracking-[0.18em] text-sage">
              {cornerClarificationLabel}
            </p>
          )}
        </div>
      </div>

      <div
        data-testid="live-choreography-board"
        className={cn(
          "absolute inset-x-0 top-0 transition-opacity duration-150 motion-reduce:transition-none",
          !rendererTrusted && "pointer-events-none opacity-0",
          layout === "cinematic"
            ? "bottom-[5.75rem] [&>div]:h-full [&>div>svg]:h-full [&>div>svg]:w-full [&>div>svg]:rounded-none [&>div>svg]:border-0"
            : "bottom-0 flex items-center px-2 pt-14 pb-24 [&>div]:w-full [&>div>svg]:h-auto [&>div>svg]:w-full",
        )}
      >
        <SVGCanvas
          ref={canvasRef}
          width={800}
          height={600}
          showGrid={false}
          viewportInteractionLocked
          reducedMotion={reducedMotion}
          choreographyPlaybackRate={playbackRate}
          className="h-full w-full"
        />
      </div>

      {!rendererTrusted && (
        <div
          className="absolute inset-0 z-30 grid place-items-center bg-void px-6 text-center"
          role="alert"
        >
          <div className="max-w-md rounded-2xl border border-rose-400/30 bg-rose-400/5 px-6 py-5">
            <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-rose-300">
              Board quarantined
            </p>
            <p className="mt-2 text-sm leading-relaxed text-chalk/80">
              This visual state could not be verified. Reset the board before
              continuing.
            </p>
          </div>
        </div>
      )}

      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-0 z-10",
          layout === "cinematic"
            ? "h-[5.75rem] border-t border-chalk-faint/10 bg-void"
            : "h-36 bg-gradient-to-t from-void via-void/88 to-transparent",
        )}
      />
      <figcaption
        className={cn(
          "absolute inset-x-0 bottom-0 z-20 px-4 sm:px-6",
          layout === "cinematic"
            ? "flex h-[5.75rem] items-center justify-center py-3"
            : "pb-4 sm:pb-5",
        )}
      >
        <p
          className="mx-auto max-w-4xl text-balance text-center text-sm font-medium leading-relaxed text-chalk sm:text-base"
          aria-live="polite"
        >
          {caption}
        </p>
      </figcaption>
    </figure>
  );
}
