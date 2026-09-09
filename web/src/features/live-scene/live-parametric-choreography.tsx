"use client";

import Link from "next/link";
import {
  ArrowLeft,
  FastForward,
  MessageCircleQuestion,
  Play,
  RotateCcw,
  Sparkles,
  StopCircle,
  Zap,
} from "lucide-react";
import {
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
import { Input } from "@/components/ui/input";
import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import type { ChoreographyLayout } from "@/lib/live-scene";
import {
  PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS,
  type ParametricCompletingSquareStateV1,
} from "@/lib/live-scene/parametric-choreography";
import { deriveCompletingSquareProblemValues } from "@/lib/live-scene/parametric-problem";
import { getAuthHeaders } from "@/lib/firebase";
import { cn } from "@/lib/utils";

import { ChoreographyCanvasBridge } from "./choreography-canvas-bridge";
import {
  createParametricChoreographySceneStreamRunner,
  type ParametricChoreographySceneStreamRunner,
} from "./parametric-choreography-model-stream";
import {
  ParametricChoreographyStreamRuntime,
  type ParametricChoreographyCommand,
  type ParametricChoreographyRuntimePhase,
} from "./parametric-choreography-stream-runtime";
import {
  LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT,
  LiveChoreographyStage,
} from "./live-choreography-stage";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_EQUATION = "x² + 8x = 20";
const DEFAULT_DIRECTOR_PROMPT =
  "Show the next idea in the clearest visual way, one chapter at a time.";
const EXAMPLE_EQUATIONS = [
  "x² + 2x = 80",
  DEFAULT_EQUATION,
  "x² + 16x = 17",
] as const;

const BUSY_PHASES = new Set<ParametricChoreographyRuntimePhase>([
  "connecting",
  "streaming",
  "repairing",
  "completing",
  "interrupting",
  "replaying",
]);

const PHASE_LABELS: Readonly<
  Record<ParametricChoreographyRuntimePhase, string>
> = {
  idle: "Ready for an equation",
  connecting: "Preparing the first visual",
  streaming: "Drawing verified checkpoints",
  repairing: "Refining the teaching move",
  completing: "Settling what you can see",
  completed: "Visual chapter complete",
  declined: "The board stayed unchanged",
  failed: "The lesson stopped safely",
  interrupting: "Settling the visible checkpoint",
  interrupted: "Paused on an exact checkpoint",
  replaying: "Replaying accepted work",
};

export interface LiveParametricChoreographyProps {
  readonly backHref?: string;
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly runStream?: ParametricChoreographySceneStreamRunner;
}

export const runAuthenticatedParametricChoreographyStream =
  createParametricChoreographySceneStreamRunner({
    apiUrl: API_BASE,
    endpoint: "product",
    getHeaders: async () => {
      const headers = await getAuthHeaders();
      if (!headers.Authorization) {
        throw new Error("Sign in again to start a live visual explanation.");
      }
      return headers;
    },
  });

function initialLayout(): ChoreographyLayout {
  return typeof globalThis.matchMedia === "function" &&
    globalThis.matchMedia("(max-width: 699px)").matches
    ? "compact"
    : "cinematic";
}

function currentComponent(
  components: readonly ParametricCompletingSquareStateV1[],
): ParametricCompletingSquareStateV1 | undefined {
  return components[0];
}

function mainCheckpointCount(
  component: ParametricCompletingSquareStateV1 | undefined,
): number {
  if (!component?.lastMainCheckpoint) return 0;
  return (
    PARAMETRIC_COMPLETING_SQUARE_MAIN_CHECKPOINTS.indexOf(
      component.lastMainCheckpoint,
    ) + 1
  );
}

interface ParametricLessonProps
  extends Omit<LiveParametricChoreographyProps, "layout" | "runStream"> {
  readonly layout: ChoreographyLayout;
  readonly runStream: ParametricChoreographySceneStreamRunner;
}

function ParametricLesson({
  backHref = "/dashboard",
  layout,
  reducedMotion = false,
  playbackRate = 1,
  runStream,
}: ParametricLessonProps) {
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const lifecycleRef = useRef<object | null>(null);
  const [renderer] = useState(() => new ChoreographyCanvasBridge());
  const [equation, setEquation] = useState(DEFAULT_EQUATION);
  const [directorPrompt, setDirectorPrompt] = useState(
    DEFAULT_DIRECTOR_PROMPT,
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [lastMode, setLastMode] = useState<"reflex" | "director">("reflex");
  const runtime = useMemo(
    () =>
      new ParametricChoreographyStreamRuntime({
        renderer,
        runStream,
        layout,
      }),
    [layout, renderer, runStream],
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
    return () => {
      renderer.attach(null);
      queueMicrotask(() => {
        if (lifecycleRef.current !== lifecycle) return;
        lifecycleRef.current = null;
        runtime.dispose();
      });
    };
  }, [renderer, runtime]);

  const component = currentComponent(snapshot.committedSemanticScene.components);
  const values = useMemo(
    () =>
      component
        ? deriveCompletingSquareProblemValues(component.problemSpec)
        : null,
    [component],
  );
  const settledMainCount = mainCheckpointCount(component);
  const isBusy = BUSY_PHASES.has(snapshot.phase);
  const editorLocked = snapshot.generation > 0;
  const isFinished = component?.lastMainCheckpoint === "solve_roots";
  const canReset = snapshot.generation > 0 || snapshot.accepted.length > 0;
  const canReplay = snapshot.accepted.length > 0 && !isBusy;
  const needsReset =
    snapshot.phase === "failed" &&
    (!snapshot.rendererTrusted || snapshot.error?.retryable === false);
  const canAdvance = !isBusy && !isFinished && !needsReset;
  const canClarifyCorner =
    !isBusy &&
    component?.lastMainCheckpoint === "missing_corner" &&
    !component.cornerClarified &&
    !needsReset;

  const start = useCallback(
    (command: ParametricChoreographyCommand) => {
      try {
        setFormError(null);
        runtime.start(command);
        setLastMode(command.routingMode);
      } catch (error) {
        setFormError(
          error instanceof Error
            ? error.message
            : "The visual lesson could not start.",
        );
      }
    },
    [runtime],
  );

  const problemText = snapshot.accepted.length === 0 ? equation : null;
  const advance = useCallback(() => {
    start({
      routingMode: "reflex",
      problemText,
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });
  }, [problemText, start]);
  const clarifyCorner = useCallback(() => {
    start({
      routingMode: "reflex",
      problemText: null,
      requestedRoute: { intent: "clarify_corner" },
    });
  }, [start]);
  const direct = useCallback(() => {
    start({
      routingMode: "director",
      problemText,
      prompt: directorPrompt,
    });
  }, [directorPrompt, problemText, start]);
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

  const cornerLabel = values
    ? `${values.halfCoefficient} × ${values.halfCoefficient} corner understood`
    : "Corner clarification settled";
  const sourceLabel =
    lastMode === "reflex"
      ? "Visual Reflex · zero model routing"
      : "Director · model routed";
  const retainedCaption = snapshot.accepted.at(-1)?.event.patch.narration;
  const stageCaption =
    (snapshot.phase === "declined" || snapshot.phase === "failed") &&
    retainedCaption
      ? retainedCaption
      : snapshot.narration;

  return (
    <div className="min-h-screen overflow-x-hidden bg-background">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_74%_12%,hsl(var(--amber)/0.065),transparent_28%),radial-gradient(circle_at_10%_82%,hsl(var(--sage)/0.045),transparent_32%)]" />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-background/95">
        <div className="mx-auto flex max-w-[1640px] items-center justify-between px-4 py-3.5 sm:px-6 lg:px-8">
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
                  Live equation studio
                </h1>
                <span className="rounded-full border border-sage/25 bg-sage/8 px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.16em] text-sage">
                  Gate 1.6
                </span>
              </div>
              <p className="hidden text-xs text-muted-foreground sm:block">
                Type the problem. Murmur composes the visual lesson live.
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1640px] gap-7 px-4 py-5 sm:px-6 lg:px-8 xl:grid-cols-[360px_minmax(0,1fr)] xl:items-start xl:gap-10 xl:py-8">
        <aside className="min-w-0 xl:sticky xl:top-8">
          <p className="font-mono text-[9px] uppercase tracking-[0.24em] text-sage">
            One equation, drawn as a story
          </p>
          <h2 className="mt-3 max-w-sm text-3xl font-semibold leading-[1.04] tracking-[-0.04em] sm:text-4xl">
            Make the algebra visible.
          </h2>
          <p className="mt-4 max-w-sm text-sm leading-6 text-muted-foreground">
            The board keeps its objects and its place when you pause. Continue
            from what is visible, or ask the Director to choose the next
            teaching move.
          </p>

          <section className="mt-7 border-y border-chalk-faint/20 py-5">
            <label
              htmlFor="parametric-equation"
              className="text-xs font-medium text-foreground"
            >
              Equation to teach
            </label>
            <Input
              id="parametric-equation"
              value={equation}
              disabled={editorLocked}
              aria-describedby="parametric-equation-note"
              onChange={(event) => setEquation(event.target.value)}
              className="mt-2 h-12 font-mono text-lg tracking-[-0.03em]"
              spellCheck={false}
              autoComplete="off"
            />
            <p
              id="parametric-equation-note"
              className="mt-2 text-[11px] leading-5 text-muted-foreground"
            >
              Supports x² + bx = c for a bounded set of exact integer lessons.
              Reset the board before changing problems.
            </p>

            <div className="mt-3 flex flex-wrap gap-2" aria-label="Example equations">
              {EXAMPLE_EQUATIONS.map((example) => (
                <button
                  key={example}
                  type="button"
                  disabled={editorLocked}
                  onClick={() => setEquation(example)}
                  className={cn(
                    "min-h-9 rounded-full border px-3 font-mono text-[10px] transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40",
                    equation === example
                      ? "border-amber/50 bg-amber/10 text-foreground"
                      : "border-chalk-faint/25 text-muted-foreground hover:border-chalk-faint/50 hover:text-foreground",
                  )}
                >
                  {example}
                </button>
              ))}
            </div>

            <div className="mt-5 flex items-center justify-between gap-3 border-t border-chalk-faint/15 pt-4">
              <div className="flex min-w-0 items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className={cn(
                    "h-2 w-2 shrink-0 rounded-full",
                    isBusy ? "bg-sage motion-safe:animate-pulse" : "bg-chalk-soft",
                  )}
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {PHASE_LABELS[snapshot.phase]}
                  </p>
                  <p className="truncate font-mono text-[9px] text-muted-foreground">
                    {settledMainCount}/{LIVE_CHOREOGRAPHY_MAIN_CHECKPOINT_COUNT} chapters · {sourceLabel}
                  </p>
                </div>
              </div>
              {lastMode === "reflex" && <Zap className="h-4 w-4 shrink-0 text-sage" />}
            </div>
          </section>

          <div className="mt-5 grid grid-cols-2 gap-2">
            {canAdvance && (
              <Button type="button" onClick={advance} className="col-span-2 min-h-11 gap-2">
                {snapshot.accepted.length === 0 ? (
                  <Sparkles className="h-4 w-4" />
                ) : (
                  <FastForward className="h-4 w-4" />
                )}
                {snapshot.accepted.length === 0
                  ? "Teach this equation"
                  : "Continue visually"}
              </Button>
            )}

            {isBusy && snapshot.phase !== "interrupting" && (
              <Button
                type="button"
                onClick={() => runtime.interrupt()}
                className="col-span-2 min-h-11 gap-2"
              >
                <StopCircle className="h-4 w-4" />
                Stop at this checkpoint
              </Button>
            )}

            {canClarifyCorner && (
              <Button
                type="button"
                variant="outline"
                onClick={clarifyCorner}
                className="col-span-2 min-h-11 gap-2"
              >
                <MessageCircleQuestion className="h-4 w-4" />
                Why is the corner {values?.cornerValue}?
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
              disabled={!canReset}
              onClick={reset}
              className="min-h-11 gap-2 text-muted-foreground"
            >
              <RotateCcw className="h-4 w-4" />
              Reset board
            </Button>
          </div>

          <details className="mt-6 border-t border-chalk-faint/20 pt-5">
            <summary className="min-h-11 cursor-pointer list-none text-sm font-medium text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Ask the Visual Director
              <span className="ml-2 font-mono text-[9px] uppercase tracking-[0.14em] text-lavender">
                model routed
              </span>
            </summary>
            <label
              htmlFor="director-prompt"
              className="mt-3 block text-[11px] leading-5 text-muted-foreground"
            >
              Describe the teaching move—not the answer or drawing coordinates.
            </label>
            <textarea
              id="director-prompt"
              value={directorPrompt}
              disabled={isBusy}
              onChange={(event) => setDirectorPrompt(event.target.value)}
              rows={4}
              className="mt-2 w-full resize-y rounded-xl border border-chalk-faint/25 bg-slate/45 px-3 py-3 text-sm leading-5 text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-45"
            />
            <Button
              type="button"
              variant="outline"
              disabled={isBusy || needsReset || directorPrompt.trim().length === 0}
              onClick={direct}
              className="mt-3 w-full gap-2"
            >
              <MessageCircleQuestion className="h-4 w-4" />
              Direct the next visual
            </Button>
          </details>

          {(snapshot.error || formError) && (
            <p className="mt-5 text-xs leading-5 text-ember" role="alert">
              {formError ?? snapshot.error?.message}
            </p>
          )}
          {snapshot.decline && !snapshot.error && (
            <p className="mt-5 text-xs leading-5 text-muted-foreground" role="status">
              {snapshot.narration}
            </p>
          )}
        </aside>

        <section className="min-w-0" aria-label="Live parametric lesson stage">
          <LiveChoreographyStage
            canvasRef={canvasRef}
            phase={snapshot.phase}
            layout={layout}
            checkpointId={component?.lastMainCheckpoint ?? undefined}
            visibleCheckpointId={snapshot.visibleCheckpointId}
            settledMainCount={settledMainCount}
            cornerClarified={component?.cornerClarified ?? false}
            cornerClarificationLabel={cornerLabel}
            caption={stageCaption}
            rendererTrusted={snapshot.rendererTrusted}
            reducedMotion={reducedMotion}
            playbackRate={playbackRate}
            className="w-full"
          />
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 px-1 text-[11px] leading-5 text-muted-foreground">
            <p className="max-w-[62ch]">
              Each visible chapter becomes the next request’s exact starting
              point. Replay redraws accepted work without another network call.
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

/** Authenticated Gate 1.6 product surface with one layout locked per session. */
export function LiveParametricChoreography({
  layout: requestedLayout,
  runStream = runAuthenticatedParametricChoreographyStream,
  ...props
}: LiveParametricChoreographyProps) {
  const [responsiveLayout, setResponsiveLayout] =
    useState<ChoreographyLayout | null>(requestedLayout ?? null);

  useEffect(() => {
    if (requestedLayout || responsiveLayout) return;
    let active = true;
    queueMicrotask(() => {
      if (active) setResponsiveLayout(initialLayout());
    });
    return () => {
      active = false;
    };
  }, [requestedLayout, responsiveLayout]);

  const layout = requestedLayout ?? responsiveLayout;
  if (!layout) {
    return (
      <main
        className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground"
        aria-busy="true"
      >
        Preparing the equation studio…
      </main>
    );
  }

  return (
    <ParametricLesson
      key={layout}
      {...props}
      layout={layout}
      runStream={runStream}
    />
  );
}
