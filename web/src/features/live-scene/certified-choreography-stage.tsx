"use client";

import type { RefObject } from "react";

import { SVGCanvas } from "@/components/svg-canvas";
import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import type { ChoreographyLayout } from "@/lib/live-scene";
import { cn } from "@/lib/utils";

export type CertifiedChoreographyStageDataAttributes = Readonly<
  Partial<
    Record<`data-${string}`, string | number | boolean | null | undefined>
  >
>;

export interface CertifiedChoreographyBoundedProgress {
  readonly kind: "bounded";
  readonly settledMainCount: number;
  readonly totalMainCount: number;
  readonly settledDetailLabels?: readonly string[];
  readonly progressAriaLabel?: string;
}

export type CertifiedChoreographyOpenFrontierStatus = "live" | "paused";

export interface CertifiedChoreographyOpenProgress {
  readonly kind: "open";
  readonly settledBeatCount: number;
  readonly frontierStatus: CertifiedChoreographyOpenFrontierStatus;
  readonly recentCertifiedLabels?: readonly string[];
  readonly progressAriaLabel?: string;
}

export type CertifiedChoreographyStageProgress =
  CertifiedChoreographyBoundedProgress | CertifiedChoreographyOpenProgress;

interface CertifiedChoreographyStageBaseProps {
  readonly canvasRef: RefObject<SVGCanvasHandle | null>;
  readonly phase: string;
  readonly layout: ChoreographyLayout;
  readonly subjectLabel: string;
  readonly checkpointLabel: string;
  readonly caption: string;
  readonly rendererTrusted: boolean;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly className?: string;
  readonly testId?: string;
  readonly dataAttributes?: CertifiedChoreographyStageDataAttributes;
}

interface CertifiedChoreographyStageProgressProps {
  readonly progress: CertifiedChoreographyStageProgress;
  readonly settledMainCount?: never;
  readonly totalMainCount?: never;
  readonly settledDetailLabels?: never;
  readonly progressAriaLabel?: never;
}

/** Compatibility surface for the bounded Gate 1.5-1.7 callers. */
interface CertifiedChoreographyStageLegacyBoundedProgressProps {
  readonly progress?: undefined;
  readonly settledMainCount: number;
  readonly totalMainCount: number;
  readonly settledDetailLabels?: readonly string[];
  readonly progressAriaLabel?: string;
}

export type CertifiedChoreographyStageProps =
  CertifiedChoreographyStageBaseProps &
    (
      | CertifiedChoreographyStageProgressProps
      | CertifiedChoreographyStageLegacyBoundedProgressProps
    );

function stageProgress(
  props: CertifiedChoreographyStageProps,
): CertifiedChoreographyStageProgress {
  if (props.progress) return props.progress;
  return {
    kind: "bounded",
    settledMainCount: props.settledMainCount,
    totalMainCount: props.totalMainCount,
    settledDetailLabels: props.settledDetailLabels,
    progressAriaLabel: props.progressAriaLabel,
  };
}

/**
 * Protocol-neutral presentation shell for one certified visual lesson.
 * Transport, controls, and semantic checkpoint policy stay with its caller.
 */
export function CertifiedChoreographyStage(
  props: CertifiedChoreographyStageProps,
) {
  const {
    canvasRef,
    phase,
    layout,
    subjectLabel,
    checkpointLabel,
    caption,
    rendererTrusted,
    reducedMotion = false,
    playbackRate = 1,
    className,
    testId = "certified-choreography-stage",
    dataAttributes,
  } = props;
  const progress = stageProgress(props);
  const boundedMainCount =
    progress.kind === "bounded"
      ? Math.min(
          Math.max(progress.settledMainCount, 0),
          progress.totalMainCount,
        )
      : 0;
  const settledBeatCount =
    progress.kind === "open" ? Math.max(progress.settledBeatCount, 0) : 0;

  return (
    <figure
      {...dataAttributes}
      className={cn(
        "relative isolate overflow-hidden border border-chalk-faint/25 bg-void",
        layout === "cinematic"
          ? "aspect-video rounded-[1.35rem]"
          : "min-h-[31rem] rounded-2xl",
        className,
      )}
      data-testid={testId}
      data-phase={phase}
      data-settled-main-count={
        progress.kind === "bounded" ? boundedMainCount : undefined
      }
      {...(progress.kind === "open"
        ? { "data-settled-beat-count": settledBeatCount }
        : {})}
      data-layout={layout}
      data-renderer-trusted={rendererTrusted ? "true" : "false"}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 z-10 h-28 bg-gradient-to-b from-void via-void/72 to-transparent"
      />

      <div className="absolute inset-x-0 top-0 z-20 flex items-start justify-between gap-2 px-4 pt-4 sm:gap-4 sm:px-6 sm:pt-5">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-chalk-soft">
            {subjectLabel}
          </p>
          <p className="mt-1 truncate text-xs font-medium capitalize text-chalk/80 sm:text-sm">
            {checkpointLabel}
          </p>
        </div>

        {progress.kind === "bounded" ? (
          <div
            className="shrink-0"
            aria-label={
              progress.progressAriaLabel ??
              `${boundedMainCount} of ${progress.totalMainCount} main checkpoints settled`
            }
          >
            <div
              className="flex items-center justify-end gap-1.5"
              aria-hidden="true"
            >
              {Array.from({ length: progress.totalMainCount }, (_, index) => (
                <span
                  key={index}
                  className={cn(
                    "h-1 w-3 rounded-full transition-[background-color,opacity] duration-300 motion-reduce:transition-none sm:w-5",
                    index < boundedMainCount
                      ? "bg-amber opacity-100"
                      : "bg-chalk-faint opacity-35",
                  )}
                />
              ))}
            </div>
            {(progress.settledDetailLabels ?? []).map((label, index) => (
              <p
                key={`${label}-${index}`}
                className="mt-2 text-right font-mono text-[8px] uppercase tracking-[0.18em] text-sage"
              >
                {label}
              </p>
            ))}
          </div>
        ) : (
          <div
            className="shrink-0 text-right"
            aria-label={
              progress.progressAriaLabel ??
              `${settledBeatCount} certified ${settledBeatCount === 1 ? "beat" : "beats"} settled; frontier ${progress.frontierStatus}`
            }
            data-testid="open-choreography-progress"
          >
            <div
              className="flex items-center justify-end gap-2 font-mono text-[8px] uppercase tracking-[0.18em]"
              aria-hidden="true"
            >
              <span className="text-chalk-soft">
                {settledBeatCount} {settledBeatCount === 1 ? "beat" : "beats"}{" "}
                settled
              </span>
              <span
                className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  progress.frontierStatus === "live"
                    ? "bg-sage"
                    : "bg-chalk-faint",
                )}
              />
              <span
                className={
                  progress.frontierStatus === "live"
                    ? "text-sage"
                    : "text-chalk-soft"
                }
              >
                Frontier {progress.frontierStatus}
              </span>
            </div>
            {(progress.recentCertifiedLabels ?? []).map((label, index) => (
              <p
                key={`${label}-${index}`}
                className="mt-2 font-mono text-[8px] uppercase tracking-[0.18em] text-sage"
                data-certified-label="true"
              >
                {label}
              </p>
            ))}
          </div>
        )}
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
