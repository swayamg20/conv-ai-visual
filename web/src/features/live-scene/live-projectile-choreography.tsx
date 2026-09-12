"use client";

import Link from "next/link";
import {
  ArrowLeft,
  Check,
  FastForward,
  Gauge,
  MessageCircleQuestion,
  Play,
  RotateCcw,
  Sparkles,
  StopCircle,
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
import { Button } from "@/components/ui/button";
import type {
  ChoreographyPlaybackRate,
  SVGCanvasHandle,
} from "@/features/canvas/types";
import type { ChoreographyLayout } from "@/lib/live-scene";
import { getAuthHeaders } from "@/lib/firebase";
import {
  PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
  PROJECTILE_MOTION_CLARIFICATION_TOPICS,
  PROJECTILE_MOTION_MAIN_CHECKPOINTS,
  SUPPORTED_PROJECTILE_ANGLES_DEG,
  SUPPORTED_PROJECTILE_SPEEDS_MPS,
  sameProjectileMotionProblem,
  type ProjectileAngleDeg,
  type ProjectileMotionCheckpointId,
  type ProjectileMotionClarificationTopic,
  type ProjectileMotionProblemSpecV1,
  type ProjectileMotionStateV1,
  type ProjectileSpeedMps,
} from "@/lib/live-scene/projectile-motion";
import { cn } from "@/lib/utils";

import { CertifiedChoreographyStage } from "./certified-choreography-stage";
import { ChoreographyCanvasBridge } from "./choreography-canvas-bridge";
import {
  createProjectileChoreographySceneStreamRunner,
  type ProjectileChoreographySceneStreamRunner,
} from "./projectile-choreography-model-stream";
import {
  ProjectileChoreographyStreamRuntime,
  type ProjectileChoreographyCommand,
  type ProjectileChoreographyRuntimePhase,
  type ProjectileChoreographyRuntimeSnapshot,
} from "./projectile-choreography-stream-runtime";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_SPEED_MPS: ProjectileSpeedMps = 20;
const DEFAULT_ANGLE_DEG: ProjectileAngleDeg = 45;

const BUSY_PHASES = new Set<ProjectileChoreographyRuntimePhase>([
  "connecting",
  "streaming",
  "repairing",
  "completing",
  "interrupting",
  "replaying",
]);

const PHASE_LABELS: Readonly<
  Record<ProjectileChoreographyRuntimePhase, string>
> = {
  idle: "Launch ready",
  connecting: "Preparing the first mark",
  streaming: "Drawing the flight",
  repairing: "Checking the next teaching move",
  completing: "Settling what is visible",
  completed: "Checkpoint certified",
  declined: "The board stayed unchanged",
  failed: "The lesson stopped safely",
  interrupting: "Landing on a safe checkpoint",
  interrupted: "Paused on an exact moment",
  replaying: "Replaying accepted motion",
};

const CHECKPOINT_LABELS: Readonly<
  Record<ProjectileMotionCheckpointId, string>
> = {
  setup: "Set the launch",
  decompose_velocity: "Split the velocity",
  trace_ascent: "Trace the ascent",
  apex_state: "Read the apex",
  trace_descent: "Follow the descent",
  summary: "Time, height & range",
  horizontal_velocity_detail: "Horizontal motion clarified",
  apex_acceleration_detail: "Apex acceleration clarified",
  flight_symmetry_detail: "Flight symmetry clarified",
  parameters_retargeted: "Launch parameters morphed",
};

const CLARIFICATION_COPY = Object.freeze({
  horizontal_velocity: {
    button: "Why does horizontal speed stay constant?",
    settled: "horizontal speed explained",
  },
  apex_acceleration: {
    button: "At the apex, why is acceleration still down?",
    settled: "apex acceleration explained",
  },
  flight_symmetry: {
    button: "Why do rise and fall take the same time?",
    settled: "flight symmetry explained",
  },
}) satisfies Readonly<
  Record<
    ProjectileMotionClarificationTopic,
    { readonly button: string; readonly settled: string }
  >
>;

export interface LiveProjectileChoreographyProps {
  readonly backHref?: string;
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly runStream?: ProjectileChoreographySceneStreamRunner;
  /** Provider-free E2E observer; production callers leave this unset. */
  readonly onRuntimeSnapshot?: (
    snapshot: ProjectileChoreographyRuntimeSnapshot,
  ) => void;
}

export const runAuthenticatedProjectileChoreographyStream =
  createProjectileChoreographySceneStreamRunner({
    apiUrl: API_BASE,
    endpoint: "product",
    getHeaders: async () => {
      const headers = await getAuthHeaders();
      if (!headers.Authorization) {
        throw new Error("Sign in again to launch a live visual lesson.");
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
  components: readonly ProjectileMotionStateV1[],
): ProjectileMotionStateV1 | undefined {
  return components[0];
}

function settledMainCount(
  component: ProjectileMotionStateV1 | undefined,
): number {
  if (!component?.lastMainCheckpoint) return 0;
  return (
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf(component.lastMainCheckpoint) + 1
  );
}

function clarificationAvailable(
  component: ProjectileMotionStateV1,
  topic: ProjectileMotionClarificationTopic,
): boolean {
  if (
    !component.lastMainCheckpoint ||
    component.clarifiedTopics.includes(topic)
  ) {
    return false;
  }
  return (
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf(component.lastMainCheckpoint) >=
    PROJECTILE_MOTION_MAIN_CHECKPOINTS.indexOf(
      PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic],
    )
  );
}

function problem(
  speedMps: ProjectileSpeedMps,
  angleDeg: ProjectileAngleDeg,
): ProjectileMotionProblemSpecV1 {
  return { v: 1, speedMps, angleDeg };
}

interface ProjectileLessonProps extends Omit<
  LiveProjectileChoreographyProps,
  "layout" | "runStream"
> {
  readonly layout: ChoreographyLayout;
  readonly runStream: ProjectileChoreographySceneStreamRunner;
}

function ProjectileLesson({
  backHref = "/dashboard",
  layout,
  reducedMotion = false,
  playbackRate = 1,
  runStream,
  onRuntimeSnapshot,
}: ProjectileLessonProps) {
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const lifecycleRef = useRef<object | null>(null);
  const [renderer] = useState(() => new ChoreographyCanvasBridge());
  const [desiredSpeed, setDesiredSpeed] =
    useState<ProjectileSpeedMps>(DEFAULT_SPEED_MPS);
  const [desiredAngle, setDesiredAngle] =
    useState<ProjectileAngleDeg>(DEFAULT_ANGLE_DEG);
  const [formError, setFormError] = useState<string | null>(null);
  const runtime = useMemo(
    () =>
      new ProjectileChoreographyStreamRuntime({
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
    if (!onRuntimeSnapshot) return;
    onRuntimeSnapshot(runtime.getSnapshot());
    return runtime.subscribe(() => onRuntimeSnapshot(runtime.getSnapshot()));
  }, [onRuntimeSnapshot, runtime]);

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

  const component = currentComponent(
    snapshot.committedSemanticScene.components,
  );
  const desiredProblem = useMemo(
    () => problem(desiredSpeed, desiredAngle),
    [desiredAngle, desiredSpeed],
  );
  const mainCount = settledMainCount(component);
  const isBusy = BUSY_PHASES.has(snapshot.phase);
  const isFinished = component?.lastMainCheckpoint === "summary";
  const needsReset =
    snapshot.phase === "failed" &&
    (!snapshot.rendererTrusted || snapshot.error?.retryable === false);
  const desiredDiffers = component
    ? !sameProjectileMotionProblem(component.problemSpec, desiredProblem)
    : false;
  const canReplay = snapshot.accepted.length > 0 && !isBusy;
  const canReset = snapshot.generation > 0 || snapshot.accepted.length > 0;
  const canTeach =
    !isBusy && !needsReset && (!component || desiredDiffers || !isFinished);
  const availableClarifications = component
    ? PROJECTILE_MOTION_CLARIFICATION_TOPICS.filter((topic) =>
        clarificationAvailable(component, topic),
      )
    : [];

  const start = useCallback(
    (command: ProjectileChoreographyCommand) => {
      try {
        setFormError(null);
        runtime.start(command);
      } catch (error) {
        setFormError(
          error instanceof Error
            ? error.message
            : "The projectile lesson could not start.",
        );
      }
    },
    [runtime],
  );

  const teach = () => {
    if (!component) {
      start({
        routingMode: "reflex",
        problemSpec: desiredProblem,
        requestedRoute: { intent: "advance", targetStage: "solve" },
      });
      return;
    }
    if (desiredDiffers) {
      start({
        routingMode: "reflex",
        problemSpec: component.problemSpec,
        requestedRoute: {
          intent: "retarget",
          targetProblemSpec: desiredProblem,
        },
      });
      return;
    }
    start({
      routingMode: "reflex",
      problemSpec: component.problemSpec,
      requestedRoute: { intent: "advance", targetStage: "solve" },
    });
  };

  const clarify = (topic: ProjectileMotionClarificationTopic) => {
    if (!component) return;
    start({
      routingMode: "reflex",
      problemSpec: component.problemSpec,
      requestedRoute: { intent: "clarify", topic },
    });
  };

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

  const visibleCheckpoint =
    snapshot.visibleCheckpointId ?? component?.lastMainCheckpoint;
  const checkpointLabel = visibleCheckpoint
    ? CHECKPOINT_LABELS[visibleCheckpoint]
    : "Ready for a launch";
  const retainedCaption = snapshot.accepted.at(-1)?.event.patch.narration;
  const stageCaption =
    (snapshot.phase === "declined" || snapshot.phase === "failed") &&
    retainedCaption
      ? retainedCaption
      : snapshot.narration;
  const primaryLabel = !component
    ? "Draw this launch"
    : desiredDiffers
      ? `Morph to ${desiredSpeed} m/s · ${desiredAngle}°`
      : "Continue the flight";
  const acceptedLabel = component
    ? `${component.problemSpec.speedMps} m/s · ${component.problemSpec.angleDeg}°`
    : "No launch on the board";

  return (
    <div
      className="dark min-h-screen overflow-x-hidden bg-void text-chalk"
      data-testid="projectile-choreography-product"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 opacity-30 [background-image:linear-gradient(hsl(var(--chalk-faint)/0.08)_1px,transparent_1px),linear-gradient(90deg,hsl(var(--chalk-faint)/0.08)_1px,transparent_1px)] [background-size:48px_48px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_78%_8%,hsl(var(--amber)/0.08),transparent_27%),radial-gradient(circle_at_12%_76%,hsl(var(--sage)/0.055),transparent_31%)]"
      />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-void/95">
        <div className="mx-auto flex max-w-[1720px] items-center justify-between px-4 py-3.5 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href={backHref}
              aria-label="Back to dashboard"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-chalk-soft transition-[background-color,color,transform] duration-150 hover:bg-graphite hover:text-chalk active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <MurmurLogoMark className="hidden shrink-0 sm:block" />
            <div className="min-w-0">
              <div className="flex items-center gap-2.5">
                <h1 className="truncate text-base font-semibold tracking-[-0.025em] sm:text-lg">
                  Projectile motion studio
                </h1>
                <span className="border border-sage/30 px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.18em] text-sage">
                  Gate 1.7
                </span>
              </div>
              <p className="hidden text-xs text-chalk-soft sm:block">
                Interruptible physics, composed one certified moment at a time.
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3 sm:gap-5">
            <Link
              href="/canvas/generate"
              className="flex min-h-11 items-center text-xs font-medium text-chalk-soft underline-offset-4 transition-colors duration-150 hover:text-chalk hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber"
            >
              Equation studio
            </Link>
            <p className="hidden font-mono text-[9px] uppercase tracking-[0.2em] text-chalk-soft md:block">
              no drag · g = 10 m/s²
            </p>
          </div>
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1720px] gap-6 px-4 py-5 sm:px-6 lg:grid-cols-[minmax(17rem,20rem)_minmax(0,1fr)] lg:items-start lg:gap-8 lg:px-8 lg:py-8 xl:grid-cols-[21rem_minmax(0,1fr)] xl:gap-10">
        <aside
          className="min-w-0 lg:sticky lg:top-8"
          aria-label="Launch controls"
        >
          <p className="font-mono text-[9px] uppercase tracking-[0.26em] text-sage">
            Field note 01 / ground to ground
          </p>
          <h2 className="mt-3 max-w-sm text-3xl font-semibold leading-[1.03] tracking-[-0.045em] sm:text-4xl lg:text-[2.65rem]">
            Throw an idea. Watch gravity answer.
          </h2>
          <p className="mt-4 max-w-[36rem] text-sm leading-6 text-chalk-soft">
            Choose a launch, then interrupt anywhere. The board keeps its exact
            place while the next visual grows from what is already true.
          </p>

          <section className="mt-6 border-y border-chalk-faint/20 py-5">
            <fieldset disabled={isBusy}>
              <legend className="flex w-full items-center justify-between gap-3 text-xs font-medium text-chalk">
                Launch speed
                <span className="font-mono text-[10px] text-amber">
                  v₀ = {desiredSpeed} m/s
                </span>
              </legend>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {SUPPORTED_PROJECTILE_SPEEDS_MPS.map((speed) => (
                  <button
                    key={speed}
                    type="button"
                    aria-label={`Set launch speed to ${speed} metres per second`}
                    aria-pressed={desiredSpeed === speed}
                    onClick={() => setDesiredSpeed(speed)}
                    className={cn(
                      "min-h-11 rounded-full border px-3 font-mono text-xs tabular-nums transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber disabled:cursor-not-allowed disabled:opacity-40",
                      desiredSpeed === speed
                        ? "border-amber/65 bg-amber/12 text-amber"
                        : "border-chalk-faint/30 text-chalk-soft hover:border-chalk-faint/65 hover:text-chalk active:scale-95",
                    )}
                  >
                    {speed}
                  </button>
                ))}
              </div>
            </fieldset>

            <fieldset disabled={isBusy} className="mt-5">
              <legend className="flex w-full items-center justify-between gap-3 text-xs font-medium text-chalk">
                Launch angle
                <span className="font-mono text-[10px] text-lavender">
                  θ = {desiredAngle}°
                </span>
              </legend>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {SUPPORTED_PROJECTILE_ANGLES_DEG.map((angle) => (
                  <button
                    key={angle}
                    type="button"
                    aria-label={`Set launch angle to ${angle} degrees`}
                    aria-pressed={desiredAngle === angle}
                    onClick={() => setDesiredAngle(angle)}
                    className={cn(
                      "min-h-11 rounded-full border px-3 font-mono text-xs tabular-nums transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber disabled:cursor-not-allowed disabled:opacity-40",
                      desiredAngle === angle
                        ? "border-lavender/65 bg-lavender/12 text-lavender"
                        : "border-chalk-faint/30 text-chalk-soft hover:border-chalk-faint/65 hover:text-chalk active:scale-95",
                    )}
                  >
                    {angle}°
                  </button>
                ))}
              </div>
            </fieldset>

            <div
              id="projectile-problem-status"
              className="mt-5 flex items-start justify-between gap-4 border-t border-chalk-faint/15 pt-4"
            >
              <div className="min-w-0">
                <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-chalk-soft">
                  Board
                </p>
                <p className="mt-1 truncate text-sm font-medium tabular-nums text-chalk">
                  {acceptedLabel}
                </p>
              </div>
              <div className="min-w-0 text-right">
                <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-chalk-soft">
                  {desiredDiffers ? "Next morph" : "Selected"}
                </p>
                <p
                  className={cn(
                    "mt-1 truncate text-sm font-medium tabular-nums",
                    desiredDiffers ? "text-amber" : "text-sage",
                  )}
                >
                  {desiredSpeed} m/s · {desiredAngle}°
                </p>
              </div>
            </div>

            <div className="mt-4 flex items-center gap-2.5">
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
                <p className="truncate text-sm font-medium text-chalk">
                  {PHASE_LABELS[snapshot.phase]}
                </p>
                <p className="truncate font-mono text-[9px] uppercase tracking-[0.12em] text-chalk-soft">
                  {mainCount}/{PROJECTILE_MOTION_MAIN_CHECKPOINTS.length}{" "}
                  moments · verified reflex
                </p>
              </div>
            </div>
          </section>

          <div className="mt-5 grid grid-cols-2 gap-2">
            {canTeach && (
              <Button
                type="button"
                onClick={teach}
                className="col-span-2 min-h-11 gap-2"
                aria-describedby="projectile-problem-status"
              >
                {!component ? (
                  <Sparkles className="h-4 w-4" />
                ) : desiredDiffers ? (
                  <Gauge className="h-4 w-4" />
                ) : (
                  <FastForward className="h-4 w-4" />
                )}
                {primaryLabel}
              </Button>
            )}

            {isBusy && (
              <Button
                type="button"
                onClick={() => runtime.interrupt()}
                disabled={snapshot.phase === "interrupting"}
                className="col-span-2 min-h-11 gap-2"
              >
                <StopCircle className="h-4 w-4" />
                {snapshot.phase === "interrupting"
                  ? "Settling visible moment"
                  : "Stop at this moment"}
              </Button>
            )}

            {!isBusy && isFinished && !desiredDiffers && !needsReset && (
              <div className="col-span-2 flex min-h-11 items-center gap-2 border-y border-sage/20 px-1 text-sm text-sage">
                <Check className="h-4 w-4 shrink-0" />
                Flight explained. Change a parameter to compare it.
              </div>
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
              className="min-h-11 gap-2 text-chalk-soft"
            >
              <RotateCcw className="h-4 w-4" />
              Reset board
            </Button>
          </div>

          {availableClarifications.length > 0 && !needsReset && (
            <section className="mt-6 border-t border-chalk-faint/20 pt-5">
              <div className="flex items-center gap-2">
                <MessageCircleQuestion className="h-4 w-4 text-lavender" />
                <h3 className="text-sm font-medium text-chalk">
                  Inspect what just happened
                </h3>
              </div>
              <div className="mt-3 grid gap-2">
                {availableClarifications.map((topic) => (
                  <button
                    key={topic}
                    type="button"
                    disabled={isBusy}
                    onClick={() => clarify(topic)}
                    className="min-h-11 rounded-xl border border-chalk-faint/25 px-3 py-2.5 text-left text-xs leading-5 text-chalk-soft transition-[background-color,border-color,color,transform] duration-150 hover:border-lavender/45 hover:bg-lavender/5 hover:text-chalk active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lavender disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {CLARIFICATION_COPY[topic].button}
                  </button>
                ))}
              </div>
            </section>
          )}

          {(snapshot.error || formError) && (
            <p className="mt-5 text-xs leading-5 text-ember" role="alert">
              {formError ?? snapshot.error?.message}
            </p>
          )}
          {snapshot.decline && !snapshot.error && (
            <p className="mt-5 text-xs leading-5 text-chalk-soft" role="status">
              {snapshot.narration}
            </p>
          )}
        </aside>

        <section className="min-w-0" aria-label="Live projectile lesson stage">
          <CertifiedChoreographyStage
            canvasRef={canvasRef}
            phase={snapshot.phase}
            layout={layout}
            subjectLabel="Projectile motion"
            checkpointLabel={checkpointLabel}
            settledMainCount={mainCount}
            totalMainCount={PROJECTILE_MOTION_MAIN_CHECKPOINTS.length}
            settledDetailLabels={component?.clarifiedTopics.map(
              (topic) => CLARIFICATION_COPY[topic].settled,
            )}
            progressAriaLabel={`${mainCount} of ${PROJECTILE_MOTION_MAIN_CHECKPOINTS.length} projectile moments settled`}
            caption={stageCaption}
            rendererTrusted={snapshot.rendererTrusted}
            reducedMotion={reducedMotion}
            playbackRate={playbackRate}
            className="w-full"
            testId="projectile-choreography-stage"
            dataAttributes={{
              "data-visible-checkpoint-id": visibleCheckpoint ?? "none",
              "data-accepted-speed": component?.problemSpec.speedMps ?? "none",
              "data-accepted-angle": component?.problemSpec.angleDeg ?? "none",
            }}
          />
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 px-1 text-[11px] leading-5 text-chalk-soft">
            <p className="max-w-[66ch]">
              Every visible moment becomes the exact starting point for the next
              one. Replay redraws the certified history without a request.
            </p>
            <p className="font-mono uppercase tracking-[0.12em]">
              {layout} · scene {snapshot.committedScene.revision} · no audio
              required
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}

/** Authenticated Gate 1.7 product surface with one layout locked per session. */
export function LiveProjectileChoreography({
  layout: requestedLayout,
  runStream = runAuthenticatedProjectileChoreographyStream,
  ...props
}: LiveProjectileChoreographyProps) {
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
        className="dark flex min-h-screen items-center justify-center bg-void text-sm text-chalk-soft"
        aria-busy="true"
      >
        Preparing the projectile studio…
      </main>
    );
  }

  return (
    <ProjectileLesson
      key={layout}
      {...props}
      layout={layout}
      runStream={runStream}
    />
  );
}
