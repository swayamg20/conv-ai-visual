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
import type { SVGCanvasHandle } from "@/features/canvas/types";
import {
  COMPLETING_SQUARE_CHECKPOINT_IDS,
  type ChoreographyLayout,
  type CompletingSquareCheckpointId,
} from "@/lib/live-scene";
import { cn } from "@/lib/utils";

import type { ChoreographySceneStreamRunner } from "./choreography-model-stream";
import { createChoreographySceneFixtureRunner } from "./choreography-scene-stream-fixture";
import type { ChoreographySceneStreamRenderer } from "./choreography-stream-runtime";
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

export interface LiveChoreographyDemoProps {
  readonly backHref?: string;
  readonly sourceLabel?: string;
  readonly scenarioControl?: ReactNode;
  readonly initialPath?: ChoreographyLessonPath;
  readonly pathLocked?: boolean;
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
  readonly stageOnly?: boolean;
  readonly autoStart?: boolean;
  readonly runnerFactory?: ChoreographyRunnerFactory;
}

const fixtureRunnerFactory: ChoreographyRunnerFactory = (path) =>
  createChoreographySceneFixtureRunner({
    mode: path === "full" ? "main" : "adaptive",
  });

class ChoreographyCanvasBridge implements ChoreographySceneStreamRenderer {
  private handle: SVGCanvasHandle | null = null;

  readonly attach = (handle: SVGCanvasHandle | null): void => {
    this.handle = handle;
  };

  playCheckpointChoreography: ChoreographySceneStreamRenderer["playCheckpointChoreography"] =
    (plan, observer) => {
      if (!this.handle) throw new Error("The visual stage is not ready yet.");
      return this.handle.playCheckpointChoreography(plan, observer);
    };

  materializeScene: ChoreographySceneStreamRenderer["materializeScene"] = (
    scene,
  ) => {
    if (!this.handle) throw new Error("The visual stage is not ready yet.");
    this.handle.materializeScene(scene);
  };

  materializeViewport: ChoreographySceneStreamRenderer["materializeViewport"] =
    (pose) => {
      if (!this.handle) throw new Error("The visual stage is not ready yet.");
      this.handle.materializeViewport(pose);
    };

  cancelMotion = (): void => {
    this.handle?.cancelMotion();
  };

  clear = (): void => {
    this.handle?.clear();
  };
}

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
  stageOnly = false,
  autoStart = false,
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

  const choreography = snapshot.choreography;
  if (!choreography) {
    throw new Error("The choreography runtime did not expose its frontier.");
  }
  const checkpointIds = choreography.accepted.map(
    (record) => record.event.semantic.checkpointId,
  );
  const checkpointId = checkpointIds.at(-1);
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
  const caption = choreography.committedCaption || EMPTY_CAPTION;

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
      settledMainCount={settledMainCount}
      cornerClarified={cornerClarified}
      caption={caption}
      reducedMotion={reducedMotion}
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
              The board commits only whole presented checkpoints; narration
              shown on stage is always the last settled caption.
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
