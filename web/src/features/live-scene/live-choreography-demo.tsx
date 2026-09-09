"use client";

import Link from "next/link";
import {
  ArrowLeft,
  Check,
  MessageCircleQuestion,
  Play,
  RotateCcw,
  Sparkles,
  StopCircle,
} from "lucide-react";
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { MurmurLogoMark } from "@/components/murmur-doodles";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
  type ViewportPoseV1,
} from "@/lib/live-scene";
import { cn } from "@/lib/utils";

import type { ChoreographySceneStreamRunner } from "./choreography-model-stream";
import type { ChoreographyEvidenceTraceEvent } from "./choreography-playback";
import { ChoreographyCanvasBridge } from "./choreography-canvas-bridge";
import { createChoreographySceneFixtureRunner } from "./choreography-scene-stream-fixture";
import { orderSceneNodesForSvgPaint } from "./svg-node-reconciler";
import {
  LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT,
  LiveChoreographyStage,
} from "./live-choreography-stage";
import {
  SceneStreamRuntime,
  type SceneStreamRuntimePhase,
} from "./stream-runtime";

const MAIN_PROMPT =
  "Solve x squared plus six x equals seven by completing the square.";
const CLARIFICATION_PROMPT = "Why is the missing corner nine?";
const CONTINUATION_PROMPT =
  "Continue the completing-square solution from the clarified corner.";
const EMPTY_CAPTION = "Can x² + 6x = 7 become one complete square?";

const BUSY_PHASES = new Set<SceneStreamRuntimePhase>([
  "connecting",
  "streaming",
  "repairing",
  "completing",
  "interrupting",
  "replaying",
]);

const PHASE_LABELS: Readonly<Record<SceneStreamRuntimePhase, string>> = {
  idle: "Ready to begin",
  connecting: "Preparing the first frame",
  streaming: "Teaching live",
  repairing: "Repairing the lesson",
  completing: "Settling the checkpoint",
  completed: "Checkpoint settled",
  declined: "The board stayed unchanged",
  failed: "The lesson stopped safely",
  interrupting: "Settling what you saw",
  interrupted: "Paused on an exact checkpoint",
  replaying: "Replaying the same explanation",
};

const MAIN_CHECKPOINTS = COMPLETING_SQUARE_CHECKPOINT_IDS.filter(
  (
    checkpoint,
  ): checkpoint is Exclude<CompletingSquareCheckpointId, "corner_detail"> =>
    checkpoint !== "corner_detail",
);

function browserInitialLayout(): ChoreographyLayout {
  return typeof globalThis.matchMedia === "function" &&
    globalThis.matchMedia("(max-width: 699px)").matches
    ? "compact"
    : "cinematic";
}

export type ChoreographyLessonPath = "full" | "ask_at_corner";

export type ChoreographyRunnerFactory = (
  path: ChoreographyLessonPath,
) => ChoreographySceneStreamRunner;

export interface LiveChoreographyCaptureInterruptRequest {
  readonly generation: number;
  readonly sequence: number;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
  readonly delayAfterPresentedMs: number;
}

export interface LiveChoreographyCaptureInterruptResult {
  readonly target: Omit<
    LiveChoreographyCaptureInterruptRequest,
    "delayAfterPresentedMs"
  >;
  readonly trigger: "firstCuePresented" | "afterFirstCuePresentedDelay";
  readonly delayAfterPresentedMs: number;
  readonly activeRevision: number;
  readonly requestedAtMs: number;
  readonly settledAtMs: number;
  readonly settleMs: number;
  readonly evidenceBefore: readonly ChoreographyEvidenceTraceEvent[];
  readonly evidenceAfter: readonly ChoreographyEvidenceTraceEvent[];
}

export interface LiveChoreographyReplayCheckpointObservation {
  readonly ordinal: number;
  readonly checkpointId: CompletingSquareCheckpointId;
  readonly certificateSha256: string;
  readonly caption: string;
  readonly viewport: ViewportPoseV1;
  readonly nodeIds: readonly string[];
  readonly domIdentity: Readonly<Record<string, number>>;
  readonly rendererTrusted: true;
  readonly cueTrace: readonly ChoreographyEvidenceTraceEvent[];
}

export interface LiveChoreographyReplayObservation {
  readonly checkpoints: readonly LiveChoreographyReplayCheckpointObservation[];
  readonly evidence: readonly ChoreographyEvidenceTraceEvent[];
}

export interface LiveChoreographyCaptureControl {
  interruptCheckpoint(
    request: LiveChoreographyCaptureInterruptRequest,
  ): Promise<LiveChoreographyCaptureInterruptResult>;
  replayAccepted(): Promise<LiveChoreographyReplayObservation>;
}

export interface LiveChoreographyDemoProps {
  readonly backHref?: string;
  readonly sourceLabel?: string;
  readonly scenarioControl?: ReactNode;
  readonly initialPath?: ChoreographyLessonPath;
  readonly pathLocked?: boolean;
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly stageOnly?: boolean;
  readonly autoStart?: boolean;
  readonly runnerFactory?: ChoreographyRunnerFactory;
  readonly onEvidenceChange?: (
    evidence: readonly ChoreographyEvidenceTraceEvent[],
  ) => void;
  /** Installed only by the environment-gated Playwright capture surface. */
  readonly onCaptureControlChange?: (
    control: LiveChoreographyCaptureControl | null,
  ) => void;
}

const fixtureRunnerFactory: ChoreographyRunnerFactory = (path) =>
  createChoreographySceneFixtureRunner({
    mode: path === "full" ? "main" : "adaptive",
  });

function mainCheckpointCount(
  checkpointIds: readonly CompletingSquareCheckpointId[],
): number {
  return checkpointIds.filter((checkpoint) => checkpoint !== "corner_detail")
    .length;
}

function lastMainCheckpoint(
  checkpointIds: readonly CompletingSquareCheckpointId[],
): Exclude<CompletingSquareCheckpointId, "corner_detail"> | undefined {
  return checkpointIds.findLast(
    (
      checkpoint,
    ): checkpoint is Exclude<CompletingSquareCheckpointId, "corner_detail"> =>
      checkpoint !== "corner_detail",
  );
}

interface ChoreographySessionProps extends Omit<
  LiveChoreographyDemoProps,
  "initialPath" | "layout" | "runnerFactory"
> {
  readonly path: ChoreographyLessonPath;
  readonly layout: ChoreographyLayout;
  readonly runStream: ChoreographySceneStreamRunner;
  readonly onPathChange: (path: ChoreographyLessonPath) => void;
}

function ChoreographySession({
  path,
  layout,
  runStream,
  onPathChange,
  backHref = "/",
  sourceLabel = "Generated fixture · $0",
  scenarioControl,
  pathLocked = false,
  reducedMotion = false,
  playbackRate = 1,
  stageOnly = false,
  autoStart = false,
  onEvidenceChange,
  onCaptureControlChange,
}: ChoreographySessionProps) {
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const lifecycleRef = useRef<object | null>(null);
  const autoStartedRef = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [renderer] = useState(() => new ChoreographyCanvasBridge());
  const [runtime] = useState(
    () =>
      new SceneStreamRuntime({
        protocol: "choreography",
        renderer,
        runStream,
        layout,
      }),
  );
  const snapshot = useSyncExternalStore(
    runtime.subscribe,
    runtime.getSnapshot,
    runtime.getSnapshot,
  );

  const captureControl = useMemo<LiveChoreographyCaptureControl>(() => {
    const copyEvidence = (
      evidence: readonly ChoreographyEvidenceTraceEvent[],
    ): readonly ChoreographyEvidenceTraceEvent[] =>
      Object.freeze(evidence.map((event) => Object.freeze({ ...event })));

    const matchesTarget = (
      event: ChoreographyEvidenceTraceEvent,
      request: LiveChoreographyCaptureInterruptRequest,
    ): boolean =>
      event.generation === request.generation &&
      event.sequence === request.sequence &&
      event.checkpointId === request.checkpointId &&
      event.certificateSha256 === request.certificateSha256;

    return Object.freeze({
      interruptCheckpoint: (
        request: LiveChoreographyCaptureInterruptRequest,
      ): Promise<LiveChoreographyCaptureInterruptResult> =>
        new Promise((resolve, reject) => {
          let triggered = false;
          let requestedAtMs = 0;
          let activeRevision = 0;
          let evidenceBefore: readonly ChoreographyEvidenceTraceEvent[] = [];
          let delay: ReturnType<typeof globalThis.setTimeout> | null = null;
          let unsubscribe = (): void => undefined;
          const deadline = globalThis.setTimeout(() => {
            unsubscribe();
            if (delay !== null) globalThis.clearTimeout(delay);
            reject(
              new Error(
                `Checkpoint ${request.checkpointId} was not interrupted before the capture deadline`,
              ),
            );
          }, 10_000);

          const fail = (message: string): void => {
            globalThis.clearTimeout(deadline);
            if (delay !== null) globalThis.clearTimeout(delay);
            unsubscribe();
            reject(new Error(message));
          };

          const interrupt = (): void => {
            delay = null;
            const current = runtime.getSnapshot();
            const evidence = current.choreography?.evidence ?? [];
            const alreadySettled = evidence.some(
              (event) =>
                matchesTarget(event, request) &&
                event.type === "checkpointSettled",
            );
            if (current.activeRevision === undefined || alreadySettled) {
              fail(
                `Checkpoint ${request.checkpointId} was no longer actively rendering`,
              );
              return;
            }
            if (current.activeRevision !== request.sequence) {
              fail(
                `Checkpoint ${request.checkpointId} did not own the active revision`,
              );
              return;
            }
            triggered = true;
            activeRevision = current.activeRevision;
            evidenceBefore = copyEvidence(evidence);
            requestedAtMs = globalThis.performance.now();
            if (!runtime.interrupt()) {
              fail(
                `Checkpoint ${request.checkpointId} could not be interrupted`,
              );
            }
          };

          const inspect = (): void => {
            const current = runtime.getSnapshot();
            const evidence = current.choreography?.evidence ?? [];
            if (!triggered) {
              if (current.phase === "failed") {
                fail(
                  current.error?.message ??
                    `Checkpoint ${request.checkpointId} failed before interruption`,
                );
                return;
              }
              const firstPresented = evidence.some(
                (event) =>
                  matchesTarget(event, request) &&
                  event.type === "firstCuePresented",
              );
              const alreadySettled = evidence.some(
                (event) =>
                  matchesTarget(event, request) &&
                  event.type === "checkpointSettled",
              );
              const mismatchedTarget = evidence.some(
                (event) =>
                  event.generation === request.generation &&
                  event.sequence === request.sequence &&
                  (event.checkpointId !== request.checkpointId ||
                    event.certificateSha256 !== request.certificateSha256),
              );
              if (mismatchedTarget) {
                fail(
                  `Checkpoint ${request.checkpointId} did not exact-match the active certified checkpoint`,
                );
                return;
              }
              if (!firstPresented || alreadySettled) return;
              if (request.delayAfterPresentedMs === 0) {
                interrupt();
              } else {
                if (delay !== null) return;
                delay = globalThis.setTimeout(
                  interrupt,
                  request.delayAfterPresentedMs,
                );
              }
              return;
            }

            if (current.phase === "failed") {
              fail(
                current.error?.message ??
                  `Checkpoint ${request.checkpointId} failed while interrupting`,
              );
              return;
            }
            if (current.phase !== "interrupted") return;
            const settledAtMs = globalThis.performance.now();
            globalThis.clearTimeout(deadline);
            unsubscribe();
            resolve(
              Object.freeze({
                target: Object.freeze({
                  generation: request.generation,
                  sequence: request.sequence,
                  checkpointId: request.checkpointId,
                  certificateSha256: request.certificateSha256,
                }),
                trigger:
                  request.delayAfterPresentedMs === 0
                    ? "firstCuePresented"
                    : "afterFirstCuePresentedDelay",
                delayAfterPresentedMs: request.delayAfterPresentedMs,
                activeRevision,
                requestedAtMs,
                settledAtMs,
                settleMs: settledAtMs - requestedAtMs,
                evidenceBefore,
                evidenceAfter: copyEvidence(
                  current.choreography?.evidence ?? [],
                ),
              }),
            );
          };

          unsubscribe = runtime.subscribe(inspect);
          inspect();
        }),
      replayAccepted: async (): Promise<LiveChoreographyReplayObservation> => {
        const expectedCount =
          runtime.getSnapshot().choreography?.accepted.length ?? 0;
        const checkpoints: LiveChoreographyReplayCheckpointObservation[] = [];
        let observedCount = 0;
        let observationError: Error | null = null;
        const observe = (): void => {
          if (observationError) return;
          const current = runtime.getSnapshot();
          if (current.phase !== "replaying" || !current.choreography) return;
          const records = current.choreography.accepted;
          if (records.length <= observedCount) return;
          if (records.length !== observedCount + 1) {
            observationError = new Error(
              "Replay skipped a presented checkpoint",
            );
            return;
          }
          const record = records.at(-1);
          const viewport = current.choreography.committedViewport;
          if (!record || !viewport) {
            observationError = new Error(
              "Replay did not expose a complete checkpoint frontier",
            );
            return;
          }
          const stage = globalThis.document.querySelector<HTMLElement>(
            '[data-testid="live-choreography-stage"]',
          );
          const svg = stage?.querySelector("svg");
          if (!stage || !svg) {
            observationError = new Error(
              "Replay checkpoint did not expose its rendered SVG frontier",
            );
            return;
          }
          interface IdentityRegistry {
            next: number;
            readonly tokens: WeakMap<Element, number>;
          }
          const owner = globalThis.window as typeof globalThis.window & {
            __MURMUR_CHOREOGRAPHY_DOM_IDENTITY__?: IdentityRegistry;
          };
          const registry = owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ ?? {
            next: 1,
            tokens: new WeakMap<Element, number>(),
          };
          owner.__MURMUR_CHOREOGRAPHY_DOM_IDENTITY__ = registry;
          const nodes = Array.from(
            svg.querySelectorAll<SVGElement>(":scope > [data-element-id]"),
          );
          const nodeIds = orderSceneNodesForSvgPaint(
            current.committedScene.nodes,
          ).map((node) => node.id);
          const renderedNodeIds = nodes.map(
            (node) => node.dataset.elementId ?? "",
          );
          if (
            nodeIds.length !== renderedNodeIds.length ||
            nodeIds.some((id, index) => id !== renderedNodeIds[index])
          ) {
            observationError = new Error(
              "Replay logical and rendered checkpoint frontiers diverged",
            );
            return;
          }
          const domIdentity = Object.fromEntries(
            nodes.map((node) => {
              let token = registry.tokens.get(node);
              if (token === undefined) {
                token = registry.next;
                registry.next += 1;
                registry.tokens.set(node, token);
              }
              return [node.dataset.elementId ?? "", token];
            }),
          );
          if (!current.choreography.rendererTrusted) {
            observationError = new Error(
              "Replay exposed an untrusted renderer checkpoint",
            );
            return;
          }
          const cueTrace = current.choreography.evidence.filter(
            (event) =>
              event.generation === record.event.generation &&
              event.sequence === record.event.sequence &&
              event.checkpointId === record.event.semantic.checkpointId &&
              event.certificateSha256 === record.presentation.certificateSha256,
          );
          checkpoints.push(
            Object.freeze({
              ordinal: records.length,
              checkpointId: record.event.semantic.checkpointId,
              certificateSha256: record.presentation.certificateSha256,
              caption: current.choreography.committedCaption,
              viewport: Object.freeze({ ...viewport }),
              nodeIds: Object.freeze(nodeIds),
              domIdentity: Object.freeze(domIdentity),
              rendererTrusted: true,
              cueTrace: copyEvidence(cueTrace),
            }),
          );
          observedCount = records.length;
        };
        const unsubscribe = runtime.subscribe(observe);
        try {
          await runtime.replayAccepted();
          observe();
          if (observationError) throw observationError;
          const current = runtime.getSnapshot();
          if (
            current.phase !== "completed" ||
            !current.choreography?.rendererTrusted ||
            checkpoints.length !== expectedCount
          ) {
            throw new Error(
              "Replay did not settle every accepted checkpoint on a trusted renderer",
            );
          }
          return Object.freeze({
            checkpoints: Object.freeze([...checkpoints]),
            evidence: copyEvidence(current.choreography?.evidence ?? []),
          });
        } finally {
          unsubscribe();
        }
      },
    });
  }, [runtime]);

  useEffect(() => {
    const lifecycle = {};
    lifecycleRef.current = lifecycle;
    renderer.attach(canvasRef.current);
    if (autoStart && !autoStartedRef.current) {
      autoStartedRef.current = true;
      runtime.start(MAIN_PROMPT);
    }
    return () => {
      renderer.attach(null);
      queueMicrotask(() => {
        if (lifecycleRef.current !== lifecycle) return;
        lifecycleRef.current = null;
        runtime.dispose();
      });
    };
  }, [autoStart, renderer, runtime]);

  useEffect(() => {
    onCaptureControlChange?.(captureControl);
    return () => onCaptureControlChange?.(null);
  }, [captureControl, onCaptureControlChange]);

  const choreography = snapshot.choreography;
  useEffect(() => {
    if (choreography) onEvidenceChange?.(choreography.evidence);
  }, [choreography, onEvidenceChange]);
  if (!choreography) {
    throw new Error("The choreography runtime did not expose its frontier.");
  }
  const checkpointIds = choreography.accepted.map(
    (record) => record.event.semantic.checkpointId,
  );
  const checkpointId = checkpointIds.at(-1);
  const visibleCheckpointId = choreography.visibleCheckpointId ?? checkpointId;
  const settledMainCount = mainCheckpointCount(checkpointIds);
  const settledMainCheckpoint = lastMainCheckpoint(checkpointIds);
  const cornerClarified = checkpointIds.includes("corner_detail");
  const isBusy = BUSY_PHASES.has(snapshot.phase);
  const canStop = isBusy && snapshot.phase !== "interrupting";
  const canReplay = choreography.accepted.length > 0 && !isBusy;
  const canReset = snapshot.generation > 0 || choreography.accepted.length > 0;
  const isFinished = settledMainCheckpoint === MAIN_CHECKPOINTS.at(-1);
  const waitingAtCorner =
    path === "ask_at_corner" &&
    settledMainCheckpoint === "missing_corner" &&
    !cornerClarified &&
    snapshot.phase === "streaming";
  const canAskCorner =
    waitingAtCorner ||
    (path === "ask_at_corner" &&
      settledMainCheckpoint === "missing_corner" &&
      !cornerClarified &&
      snapshot.phase === "interrupted");
  const canContinueAfterClarification =
    path === "ask_at_corner" &&
    cornerClarified &&
    checkpointId === "corner_detail" &&
    !isBusy;
  const canContinueFull =
    path === "full" &&
    snapshot.phase === "interrupted" &&
    settledMainCount < LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT;
  const unsupportedAdaptivePause =
    path === "ask_at_corner" &&
    snapshot.phase === "interrupted" &&
    !canAskCorner &&
    !canContinueAfterClarification &&
    !isFinished;
  const caption = choreography.visibleCaption || EMPTY_CAPTION;

  const start = useCallback(
    (prompt: string) => {
      try {
        setFormError(null);
        runtime.start(prompt);
      } catch (error) {
        setFormError(
          error instanceof Error
            ? error.message
            : "The live lesson could not start.",
        );
      }
    },
    [runtime],
  );

  const replay = useCallback(() => {
    setFormError(null);
    void runtime.replayAccepted().catch((error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Replay could not start.",
      );
    });
  }, [runtime]);

  const reset = useCallback(() => {
    setFormError(null);
    runtime.reset();
  }, [runtime]);

  const stage = (
    <LiveChoreographyStage
      canvasRef={canvasRef}
      phase={snapshot.phase}
      layout={layout}
      checkpointId={checkpointId}
      visibleCheckpointId={visibleCheckpointId}
      settledMainCount={settledMainCount}
      cornerClarified={cornerClarified}
      caption={caption}
      rendererTrusted={choreography.rendererTrusted}
      reducedMotion={reducedMotion}
      playbackRate={playbackRate}
      className={stageOnly ? "h-full w-full rounded-none border-0" : "w-full"}
    />
  );

  if (stageOnly) {
    return (
      <main
        className="fixed inset-0 overflow-hidden bg-void"
        aria-label="Live choreography capture"
      >
        {stage}
      </main>
    );
  }

  return (
    <div className="min-h-screen overflow-x-hidden bg-background">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_72%_18%,hsl(var(--amber)/0.07),transparent_30%),radial-gradient(circle_at_16%_78%,hsl(var(--lavender)/0.06),transparent_34%)]" />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-background/92">
        <div className="mx-auto flex max-w-[1580px] items-center justify-between px-4 py-3.5 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href={backHref}
              aria-label="Back"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted-foreground transition-colors duration-200 hover:bg-graphite hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <MurmurLogoMark className="hidden shrink-0 sm:block" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-sm font-semibold tracking-tight sm:text-lg">
                  Live visual choreography
                </h1>
                <span className="rounded-full border border-amber/25 bg-amber/8 px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.16em] text-amber">
                  Gate 1.5
                </span>
              </div>
              <p className="hidden text-xs text-muted-foreground sm:block">
                One idea transforms into the next—and waits when you interrupt.
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1580px] gap-7 px-4 py-5 sm:px-6 lg:px-8 xl:grid-cols-[330px_minmax(0,1fr)] xl:items-start xl:gap-10 xl:py-8">
        <aside className="min-w-0 xl:sticky xl:top-8">
          <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-amber">
            A live mathematical story
          </p>
          <h2 className="mt-3 max-w-xs text-3xl font-semibold leading-[1.04] tracking-[-0.04em] sm:text-4xl">
            Watch the algebra become geometry.
          </h2>
          <p className="mt-4 max-w-sm text-sm leading-6 text-muted-foreground">
            The same square and strips move through the proof. Pause on what you
            can actually see, ask the bounded corner question, then continue
            from that exact board.
          </p>

          <div className="mt-7 border-y border-chalk-faint/20 py-5">
            {scenarioControl}

            {!pathLocked && (
              <fieldset>
                <legend className="mb-3 text-xs font-medium text-foreground">
                  Choose the lesson path
                </legend>
                <div
                  className="grid grid-cols-2 gap-2"
                  data-testid="choreography-path-picker"
                >
                  {(
                    [
                      ["ask_at_corner", "Ask at the corner"],
                      ["full", "Watch straight through"],
                    ] as const
                  ).map(([value, label]) => (
                    <label
                      key={value}
                      className={cn(
                        "flex min-h-14 cursor-pointer items-center rounded-lg border px-3 py-2 text-left text-[11px] leading-4 transition-colors duration-200 focus-within:ring-2 focus-within:ring-ring",
                        path === value
                          ? "border-amber/45 bg-amber/8 text-foreground"
                          : "border-chalk-faint/20 text-muted-foreground hover:border-chalk-faint/40 hover:text-foreground",
                        canReset && "pointer-events-none opacity-55",
                      )}
                    >
                      <input
                        type="radio"
                        name="choreography-path"
                        value={value}
                        checked={path === value}
                        disabled={canReset}
                        onChange={() => onPathChange(value)}
                        className="sr-only"
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}

            <div className="mt-5 flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-2 w-2 shrink-0 rounded-full",
                    isBusy
                      ? "bg-sage motion-safe:animate-pulse"
                      : "bg-chalk-soft",
                  )}
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {waitingAtCorner
                      ? "Waiting for your question"
                      : PHASE_LABELS[snapshot.phase]}
                  </p>
                  <p className="truncate font-mono text-[9px] text-muted-foreground">
                    {settledMainCount}/8 main checkpoints · {sourceLabel}
                  </p>
                </div>
              </div>
              {cornerClarified && (
                <Check className="h-4 w-4 shrink-0 text-sage" />
              )}
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2">
            {!canReset && (
              <Button
                type="button"
                onClick={() => start(MAIN_PROMPT)}
                className="col-span-2 min-h-11 gap-2"
              >
                <Sparkles className="h-4 w-4" />
                Begin the lesson
              </Button>
            )}

            {canStop && (
              <Button
                type="button"
                onClick={() => runtime.interrupt()}
                className="col-span-2 min-h-11 gap-2"
              >
                <StopCircle className="h-4 w-4" />
                {waitingAtCorner
                  ? "Stop here and ask"
                  : "Stop on this checkpoint"}
              </Button>
            )}

            {canAskCorner && snapshot.phase === "interrupted" && (
              <Button
                type="button"
                onClick={() => start(CLARIFICATION_PROMPT)}
                className="col-span-2 min-h-11 gap-2"
              >
                <MessageCircleQuestion className="h-4 w-4" />
                Why is the corner 9?
              </Button>
            )}

            {canContinueAfterClarification && (
              <Button
                type="button"
                onClick={() => start(CONTINUATION_PROMPT)}
                className="col-span-2 min-h-11 gap-2"
              >
                <Play className="h-4 w-4" />
                Continue the solution
              </Button>
            )}

            {canContinueFull && (
              <Button
                type="button"
                onClick={() => start(MAIN_PROMPT)}
                className="col-span-2 min-h-11 gap-2"
              >
                <Play className="h-4 w-4" />
                Continue the lesson
              </Button>
            )}

            <Button
              type="button"
              variant="secondary"
              disabled={!canReplay}
              onClick={replay}
              className="min-h-11 gap-2"
            >
              <Play className="h-4 w-4" />
              Replay
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={!canReset || isBusy}
              onClick={reset}
              className="min-h-11 gap-2 text-muted-foreground"
            >
              <RotateCcw className="h-4 w-4" />
              Reset
            </Button>
          </div>

          {waitingAtCorner && (
            <p className="mt-4 border-l-2 border-amber/50 pl-3 text-xs leading-5 text-foreground/80">
              The teacher is deliberately holding here. Stop the lesson, then
              ask why the missing 3 × 3 corner contributes 9.
            </p>
          )}
          {unsupportedAdaptivePause && (
            <p
              className="mt-4 border-l-2 border-ember/50 pl-3 text-xs leading-5 text-ember"
              role="alert"
            >
              This bounded question path resumes only from the missing corner.
              Reset and let the lesson reach that checkpoint before
              interrupting.
            </p>
          )}
          {(snapshot.error || formError) && (
            <p className="mt-4 text-xs leading-5 text-ember" role="alert">
              {formError ?? snapshot.error?.message}
            </p>
          )}

          <div className="mt-7 flex flex-wrap gap-x-4 gap-y-2 font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
            <span>Stable objects</span>
            <span>Exact camera</span>
            <span>Post-paint commits</span>
          </div>
        </aside>

        <section
          className="min-w-0"
          aria-label="Completing the square lesson stage"
        >
          {stage}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 px-1 text-[11px] text-muted-foreground">
            <p>
              The stage introduces each caption with its first visible cue;
              board state still commits only at a whole checkpoint.
            </p>
            <p className="font-mono">
              {layout} · scene {snapshot.committedScene.revision}
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}

/** User-visible Gate 1.5 lesson with a layout locked once per mounted session. */
export function LiveChoreographyDemo({
  initialPath = "ask_at_corner",
  layout: requestedLayout,
  runnerFactory = fixtureRunnerFactory,
  ...props
}: LiveChoreographyDemoProps) {
  const [path, setPath] = useState<ChoreographyLessonPath>(initialPath);
  const [responsiveLayout, setResponsiveLayout] =
    useState<ChoreographyLayout | null>(requestedLayout ?? null);

  useEffect(() => {
    if (requestedLayout || responsiveLayout) return;
    let active = true;
    queueMicrotask(() => {
      if (active) setResponsiveLayout(browserInitialLayout());
    });
    return () => {
      active = false;
    };
  }, [requestedLayout, responsiveLayout]);

  const layout = requestedLayout ?? responsiveLayout;
  const runStream = useMemo(() => runnerFactory(path), [path, runnerFactory]);

  if (!layout) {
    return (
      <main
        className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground"
        aria-busy="true"
      >
        Preparing the visual stage…
      </main>
    );
  }

  return (
    <ChoreographySession
      key={`${path}:${layout}`}
      {...props}
      path={path}
      layout={layout}
      runStream={runStream}
      onPathChange={setPath}
    />
  );
}
