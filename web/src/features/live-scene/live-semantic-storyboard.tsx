"use client";

import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
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
import { SUPPORTED_PROJECTILE_SPEEDS_MPS } from "@/lib/live-scene/projectile-motion";
import type {
  PairedProjectileComparisonSpecV1,
  ProjectileStoryboardStateV1,
} from "@/lib/live-scene/semantic-storyboard";
import { cn } from "@/lib/utils";

import { CertifiedChoreographyStage } from "./certified-choreography-stage";
import { ChoreographyCanvasBridge } from "./choreography-canvas-bridge";
import {
  useCertifiedPresentationPreferences,
  type CertifiedPresentationPreferences,
} from "./certified-presentation-preferences";
import {
  createSemanticStoryboardSceneStreamRunner,
  type SemanticStoryboardSceneStreamRunner,
} from "./semantic-storyboard-model-stream";
import {
  SemanticStoryboardSessionController,
  type SemanticStoryboardSessionSnapshot,
  type SemanticStoryboardSessionStatus,
} from "./semantic-storyboard-session-controller";
import { SemanticStoryboardStreamRuntime } from "./semantic-storyboard-stream-runtime";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEFAULT_PROBLEM = Object.freeze({
  v: 1,
  speedMps: 20,
  anglesDeg: Object.freeze([30, 60]),
} as const satisfies PairedProjectileComparisonSpecV1);
const DEFAULT_PROMPT =
  "Trace both flights before comparing their landing ranges.";
const ANGLE_PAIRS = Object.freeze([
  Object.freeze([30, 45] as const),
  Object.freeze([30, 60] as const),
  Object.freeze([45, 60] as const),
] as const satisfies readonly PairedProjectileComparisonSpecV1["anglesDeg"][]);

const SESSION_LABELS = Object.freeze({
  ready: "The board is ready for a direction.",
  anchoring: "Drawing a certified launch frame.",
  director_handoff: "The launch frame is settled. Director is taking over.",
  directing: "Director is composing the next visible beat.",
  interrupting: "Settling the visual beat already in motion.",
  replaying: "Replaying the accepted board locally.",
  paused: "The certified frontier is ready to continue.",
  declined: "No safe new visual beat was added.",
  failed: "The story stopped at its last safe visual beat.",
}) satisfies Readonly<Record<SemanticStoryboardSessionStatus, string>>;

export interface LiveSemanticStoryboardProps {
  readonly backHref?: string;
  readonly initialProblemSpec?: PairedProjectileComparisonSpecV1;
  readonly initialPrompt?: string;
  readonly layout?: ChoreographyLayout;
  readonly reducedMotion?: boolean;
  readonly playbackRate?: ChoreographyPlaybackRate;
  readonly runStream?: SemanticStoryboardSceneStreamRunner;
  /** Provider-free E2E observer; production callers leave this unset. */
  readonly onSessionSnapshot?: (
    snapshot: SemanticStoryboardSessionSnapshot,
  ) => void;
}

/** Product transport resolves a fresh Firebase bearer immediately before POST. */
export const runAuthenticatedSemanticStoryboardStream =
  createSemanticStoryboardSceneStreamRunner({
    apiUrl: API_BASE,
    endpoint: "product",
    getHeaders: async () => {
      // Keep Firebase out of the provider-free lab module graph. The product
      // route resolves auth only when it actually starts a network request.
      const { getAuthHeaders } = await import("@/lib/firebase");
      const headers = await getAuthHeaders();
      if (!headers.Authorization) {
        throw new Error("Sign in again to direct a live visual explanation.");
      }
      return headers;
    },
  });

function problemSpec(
  speedMps: PairedProjectileComparisonSpecV1["speedMps"],
  anglesDeg: PairedProjectileComparisonSpecV1["anglesDeg"],
): PairedProjectileComparisonSpecV1 {
  return Object.freeze({
    v: 1,
    speedMps,
    anglesDeg: Object.freeze([...anglesDeg]) as readonly [
      (typeof anglesDeg)[0],
      (typeof anglesDeg)[1],
    ],
  });
}

function currentComponent(
  snapshot: SemanticStoryboardSessionSnapshot,
): ProjectileStoryboardStateV1 | undefined {
  return snapshot.runtime.committedSemanticScene.components[0];
}

function checkpointLabel(snapshot: SemanticStoryboardSessionSnapshot): string {
  const visible = snapshot.runtime.visibleCheckpointId;
  if (!visible) return "Awaiting the first mark";
  if (visible === "storyboard-anchor") return "Launch comparison anchored";
  return (
    snapshot.progress.recentCertifiedLabels.at(-1) ??
    visible.replace(/^storyboard-checkpoint-/, "").replaceAll("-", " ")
  );
}

function anglePairKey(
  angles: PairedProjectileComparisonSpecV1["anglesDeg"],
): string {
  return `${angles[0]}:${angles[1]}`;
}

interface StoryboardStudioProps extends Omit<
  LiveSemanticStoryboardProps,
  "layout" | "reducedMotion" | "runStream"
> {
  readonly preferences: CertifiedPresentationPreferences;
  readonly runStream: SemanticStoryboardSceneStreamRunner;
}

function StoryboardStudio({
  backHref = "/dashboard",
  initialProblemSpec = DEFAULT_PROBLEM,
  initialPrompt = DEFAULT_PROMPT,
  playbackRate = 1,
  preferences,
  runStream,
  onSessionSnapshot,
}: StoryboardStudioProps) {
  const { layout, reducedMotion } = preferences;
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const lifecycleRef = useRef<object | null>(null);
  const [renderer] = useState(() => new ChoreographyCanvasBridge());
  // Transport identity is part of a session. A parent rerender must not leave
  // an old active runtime alive while attaching its renderer to a new one.
  const [lockedRunStream] = useState(() => runStream);
  const [initialProblem] = useState(() => initialProblemSpec);
  const [desiredSpeed, setDesiredSpeed] = useState(initialProblem.speedMps);
  const [desiredAngles, setDesiredAngles] = useState(initialProblem.anglesDeg);
  const [prompt, setPrompt] = useState(initialPrompt);
  const [formError, setFormError] = useState<string | null>(null);
  const runtime = useMemo(
    () =>
      new SemanticStoryboardStreamRuntime({
        renderer,
        runStream: lockedRunStream,
        layout,
      }),
    [layout, lockedRunStream, renderer],
  );
  const controller = useMemo(
    () => new SemanticStoryboardSessionController({ runtime }),
    [runtime],
  );
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );

  useEffect(() => {
    if (!onSessionSnapshot) return;
    onSessionSnapshot(controller.getSnapshot());
    return controller.subscribe(() =>
      onSessionSnapshot(controller.getSnapshot()),
    );
  }, [controller, onSessionSnapshot]);

  useEffect(() => {
    const lifecycle = {};
    lifecycleRef.current = lifecycle;
    renderer.attach(canvasRef.current);
    return () => {
      renderer.attach(null);
      queueMicrotask(() => {
        if (lifecycleRef.current !== lifecycle) return;
        lifecycleRef.current = null;
        controller.dispose();
      });
    };
  }, [controller, renderer]);

  const component = currentComponent(snapshot);
  const desiredProblem = useMemo(
    () => problemSpec(desiredSpeed, desiredAngles),
    [desiredAngles, desiredSpeed],
  );
  const promptCodePoints = [...prompt].length;
  const problemLocked = snapshot.problemSpec !== null;
  const promptLocked =
    snapshot.status === "anchoring" ||
    snapshot.status === "director_handoff" ||
    snapshot.status === "directing" ||
    snapshot.status === "interrupting" ||
    snapshot.status === "replaying";
  const retainedCaption =
    snapshot.runtime.accepted.at(-1)?.event.patch.narration;
  const stageCaption =
    (snapshot.status === "failed" || snapshot.status === "declined") &&
    retainedCaption
      ? retainedCaption
      : snapshot.runtime.narration;
  const visibleError =
    formError ??
    snapshot.orchestrationError?.message ??
    snapshot.runtime.error?.message ??
    null;
  const isSettling = snapshot.status === "interrupting";
  const canStop = snapshot.controls.canInterrupt;
  const hasFrontier = Boolean(component);
  const primaryLabel = isSettling
    ? "Settling visible beat"
    : canStop
      ? "Stop at this beat"
      : hasFrontier
        ? "Continue from here"
        : "Make it visible";
  const primaryDisabled = isSettling
    ? true
    : canStop
      ? false
      : hasFrontier
        ? !snapshot.controls.canContinue
        : !snapshot.controls.canStartFresh;
  const completionMetadata = snapshot.runtime.completion?.metadata;
  const acceptedPrefix = completionMetadata?.reasonCode === "accepted_prefix";
  const latestCertificate =
    snapshot.runtime.accepted.at(-1)?.event.transition.checkpoint.certificate;
  const programSha256 = latestCertificate?.body.resultProgramSha256 ?? "none";
  const certificateHead =
    snapshot.runtime.committedSemanticScene.certificateHeadSha256 ?? "none";
  const acceptedAngles = component
    ? anglePairKey(component.problemSpec.anglesDeg)
    : "none";

  const runPrimary = useCallback(() => {
    try {
      setFormError(null);
      if (controller.getSnapshot().controls.canInterrupt) {
        controller.interrupt();
        return;
      }
      const current = controller.getSnapshot();
      if (current.problemSpec) controller.continueWithPrompt(prompt);
      else {
        controller.startFresh({
          problemSpec: desiredProblem,
          prompt,
        });
      }
    } catch (error) {
      setFormError(
        error instanceof Error
          ? error.message
          : "The visual story could not start.",
      );
    }
  }, [controller, desiredProblem, prompt]);

  const replay = useCallback(() => {
    setFormError(null);
    void controller.replayAccepted().catch((error: unknown) => {
      setFormError(
        error instanceof Error ? error.message : "Replay could not start.",
      );
    });
  }, [controller]);

  const reset = useCallback(() => {
    try {
      setFormError(null);
      controller.reset();
    } catch (error) {
      setFormError(
        error instanceof Error ? error.message : "The board could not reset.",
      );
    }
  }, [controller]);

  const angleLabel = component
    ? `${component.problemSpec.anglesDeg[0]}° + ${component.problemSpec.anglesDeg[1]}°`
    : `${desiredAngles[0]}° + ${desiredAngles[1]}°`;

  return (
    <div
      className="dark min-h-screen overflow-x-hidden bg-void text-chalk"
      data-testid="semantic-storyboard-product"
      data-session-status={snapshot.status}
      data-runtime-phase={snapshot.runtime.phase}
      data-generation={snapshot.runtime.generation}
      data-settled-beat-count={snapshot.progress.settledBeatCount}
      data-scene-revision={snapshot.runtime.committedScene.revision}
      data-semantic-revision={snapshot.runtime.committedSemanticScene.revision}
      data-renderer-trusted={snapshot.runtime.rendererTrusted}
      data-last-route={snapshot.lastRoute ?? "none"}
      data-pending-director={snapshot.pendingDirector}
      data-frontier-status={snapshot.progress.frontierStatus}
      data-layout={layout}
      data-reduced-motion={reducedMotion}
      data-program-sha256={programSha256}
      data-certificate-head={certificateHead}
      data-accepted-angles={acceptedAngles}
      data-completion-reason={completionMetadata?.reasonCode ?? "none"}
      data-completion-detail={completionMetadata?.detailCode ?? "none"}
      data-decline-reason={snapshot.runtime.decline?.reasonCode ?? "none"}
      data-error-code={
        snapshot.orchestrationError?.code ??
        snapshot.runtime.error?.code ??
        "none"
      }
    >
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 opacity-35 [background-image:radial-gradient(circle_at_82%_4%,hsl(var(--amber)/0.13),transparent_26%),radial-gradient(circle_at_10%_84%,hsl(var(--lavender)/0.07),transparent_29%)]"
      />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-void/95">
        <div className="mx-auto flex max-w-[1760px] items-center justify-between gap-3 px-3 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-2.5 sm:gap-3">
            <Link
              href={backHref}
              aria-label="Back"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-chalk-soft transition-[background-color,color,transform] duration-150 hover:bg-graphite hover:text-chalk active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <MurmurLogoMark className="hidden shrink-0 sm:block" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-base font-semibold tracking-[-0.025em] sm:text-lg">
                  Live storyboard
                </h1>
                <span className="border border-amber/30 px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-[0.18em] text-amber sm:px-2 sm:text-[9px]">
                  Gate 1.8
                </span>
              </div>
              <p className="hidden text-xs text-chalk-soft sm:block">
                One living blackboard, composed beat by certified beat.
              </p>
            </div>
          </div>
          <p className="hidden shrink-0 font-mono text-[9px] uppercase tracking-[0.2em] text-chalk-soft md:block">
            no drag · g = 10 m/s²
          </p>
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1760px] gap-5 px-3 py-4 sm:px-6 sm:py-6 lg:grid-cols-[minmax(17rem,19rem)_minmax(0,1fr)] lg:items-start lg:gap-8 lg:px-8 lg:py-8 xl:grid-cols-[20rem_minmax(0,1fr)] xl:gap-10">
        <aside
          className="min-w-0 border-l border-lavender/30 bg-[repeating-linear-gradient(to_bottom,transparent_0,transparent_31px,hsl(var(--chalk-faint)/0.07)_32px)] pl-4 sm:pl-5 lg:sticky lg:top-8"
          aria-label="Storyboard Director"
        >
          <p className="font-mono text-[9px] uppercase tracking-[0.28em] text-lavender">
            Director margin / 01
          </p>
          <h2 className="mt-3 max-w-sm text-3xl font-semibold leading-[1.02] tracking-[-0.05em] sm:text-4xl lg:text-[2.55rem]">
            Say what should become visible.
          </h2>
          <p className="mt-4 max-w-[38rem] text-sm leading-6 text-chalk-soft">
            The model chooses the teaching order. Certified geometry keeps every
            mark true, interruptible, and ready to continue.
          </p>

          <section className="mt-6 border-y border-chalk-faint/20 bg-void/60 py-5">
            <fieldset
              disabled={problemLocked}
              data-testid="storyboard-speed-fieldset"
            >
              <legend className="flex w-full items-center justify-between gap-3 text-xs font-medium text-chalk">
                Launch speed
                <span className="font-mono text-[10px] text-amber">
                  {desiredSpeed} m/s
                </span>
              </legend>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {SUPPORTED_PROJECTILE_SPEEDS_MPS.map((speed) => (
                  <button
                    key={speed}
                    type="button"
                    aria-label={`Set both launches to ${speed} metres per second`}
                    aria-pressed={desiredSpeed === speed}
                    data-testid={`storyboard-speed-${speed}`}
                    onClick={() => setDesiredSpeed(speed)}
                    className={cn(
                      "min-h-11 rounded-full border px-2 font-mono text-xs tabular-nums transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber disabled:cursor-not-allowed disabled:opacity-45",
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

            <fieldset
              disabled={problemLocked}
              className="mt-5"
              data-testid="storyboard-angle-fieldset"
            >
              <legend className="flex w-full items-center justify-between gap-3 text-xs font-medium text-chalk">
                Angle pair
                <span className="font-mono text-[10px] text-lavender">
                  {desiredAngles[0]}° / {desiredAngles[1]}°
                </span>
              </legend>
              <div className="mt-3 grid grid-cols-3 gap-2">
                {ANGLE_PAIRS.map((angles) => {
                  const selected =
                    anglePairKey(desiredAngles) === anglePairKey(angles);
                  return (
                    <button
                      key={anglePairKey(angles)}
                      type="button"
                      aria-label={`Compare ${angles[0]} and ${angles[1]} degree launches`}
                      aria-pressed={selected}
                      data-testid={`storyboard-angles-${angles[0]}-${angles[1]}`}
                      onClick={() => setDesiredAngles(angles)}
                      className={cn(
                        "min-h-11 rounded-full border px-1 font-mono text-[11px] tabular-nums transition-[background-color,border-color,color,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lavender disabled:cursor-not-allowed disabled:opacity-45",
                        selected
                          ? "border-lavender/65 bg-lavender/12 text-lavender"
                          : "border-chalk-faint/30 text-chalk-soft hover:border-chalk-faint/65 hover:text-chalk active:scale-95",
                      )}
                    >
                      {angles[0]}° · {angles[1]}°
                    </button>
                  );
                })}
              </div>
            </fieldset>

            <div className="mt-5 border-t border-chalk-faint/15 pt-4">
              <label
                htmlFor="semantic-storyboard-prompt"
                className="text-xs font-medium text-chalk"
              >
                What should the board explain next?
              </label>
              <textarea
                id="semantic-storyboard-prompt"
                data-testid="semantic-storyboard-prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                disabled={promptLocked}
                aria-describedby="semantic-storyboard-prompt-count"
                className="mt-2 min-h-28 w-full resize-y rounded-xl border border-chalk-faint/25 bg-void/85 px-3 py-3 text-sm leading-6 text-chalk outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-chalk-faint focus:border-amber/55 focus:ring-2 focus:ring-amber/20 disabled:cursor-not-allowed disabled:opacity-55"
              />
              <p
                id="semantic-storyboard-prompt-count"
                className={cn(
                  "mt-1.5 text-right font-mono text-[9px] tabular-nums",
                  promptCodePoints > 2_000 ? "text-ember" : "text-chalk-soft",
                )}
              >
                {promptCodePoints}/2000 code points
              </p>
            </div>

            <div className="mt-4 flex items-start gap-2.5 border-t border-chalk-faint/15 pt-4">
              <span
                aria-hidden="true"
                className={cn(
                  "mt-1 h-2 w-2 shrink-0 rounded-full",
                  snapshot.progress.frontierStatus === "live"
                    ? "bg-sage motion-safe:animate-pulse"
                    : "bg-chalk-soft",
                )}
              />
              <div className="min-w-0">
                <p
                  className="text-sm font-medium leading-5 text-chalk"
                  data-testid="semantic-storyboard-status"
                >
                  {SESSION_LABELS[snapshot.status]}
                </p>
                <p className="mt-1 font-mono text-[9px] uppercase tracking-[0.12em] text-chalk-soft">
                  {snapshot.progress.settledBeatCount} certified beats ·{" "}
                  {angleLabel}
                </p>
              </div>
            </div>
          </section>

          <div className="mt-5 grid grid-cols-2 gap-2">
            <Button
              type="button"
              onClick={runPrimary}
              disabled={primaryDisabled}
              className="col-span-2 min-h-11 gap-2"
              data-testid="semantic-storyboard-primary"
            >
              {isSettling ? (
                <CheckCircle2 className="h-4 w-4" />
              ) : canStop ? (
                <StopCircle className="h-4 w-4" />
              ) : (
                <Sparkles className="h-4 w-4" />
              )}
              {primaryLabel}
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={!snapshot.controls.canReplay}
              onClick={replay}
              className="min-h-11 gap-2"
              data-testid="semantic-storyboard-replay"
            >
              <Play className="h-4 w-4" />
              Replay
            </Button>
            <Button
              type="button"
              variant="ghost"
              disabled={!snapshot.controls.canReset || canStop || isSettling}
              onClick={reset}
              className="min-h-11 gap-2 text-chalk-soft"
              data-testid="semantic-storyboard-reset"
            >
              <RotateCcw className="h-4 w-4" />
              Reset
            </Button>
          </div>

          {acceptedPrefix && !visibleError && (
            <p
              className="mt-4 text-xs leading-5 text-sage"
              data-testid="semantic-storyboard-accepted-prefix"
            >
              The verified prefix stays on the board; an unsafe later beat was
              discarded.
            </p>
          )}
          {snapshot.status === "declined" && !visibleError && (
            <p
              className="mt-4 text-xs leading-5 text-chalk-soft"
              data-testid="semantic-storyboard-decline"
            >
              {snapshot.runtime.narration}
            </p>
          )}
          {visibleError && (
            <p
              className="mt-4 text-xs leading-5 text-ember"
              role="alert"
              data-testid="semantic-storyboard-error"
            >
              {visibleError}
            </p>
          )}
        </aside>

        <section className="min-w-0" aria-label="Living storyboard blackboard">
          <CertifiedChoreographyStage
            canvasRef={canvasRef}
            phase={snapshot.runtime.phase}
            layout={layout}
            subjectLabel="Same speed · two launch angles"
            checkpointLabel={checkpointLabel(snapshot)}
            progress={snapshot.progress}
            caption={stageCaption}
            rendererTrusted={snapshot.runtime.rendererTrusted}
            reducedMotion={reducedMotion}
            playbackRate={playbackRate}
            className="w-full shadow-[0_30px_100px_hsl(var(--void)/0.7)]"
            testId="semantic-storyboard-stage"
            dataAttributes={{
              "data-session-status": snapshot.status,
              "data-visible-checkpoint-id":
                snapshot.runtime.visibleCheckpointId ?? "none",
              "data-accepted-speed": component?.problemSpec.speedMps ?? "none",
              "data-accepted-angle-pair": component
                ? anglePairKey(component.problemSpec.anglesDeg)
                : "none",
              "data-accepted-angles": acceptedAngles,
              "data-program-sha256": programSha256,
              "data-certificate-head": certificateHead,
              "data-scene-revision": snapshot.runtime.committedScene.revision,
              "data-semantic-revision":
                snapshot.runtime.committedSemanticScene.revision,
              "data-generation": snapshot.runtime.generation,
              "data-last-route": snapshot.lastRoute ?? "none",
              "data-completion-reason":
                completionMetadata?.reasonCode ?? "none",
              "data-completion-detail":
                completionMetadata?.detailCode ?? "none",
            }}
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 px-1 text-[11px] leading-5 text-chalk-soft">
            <p className="max-w-[70ch]">
              Interrupt on any visible beat. Continue grows from that exact
              certified frontier; Replay makes no model request.
            </p>
            <p className="font-mono uppercase tracking-[0.12em]">
              {layout} · scene {snapshot.runtime.committedScene.revision} ·
              motion {reducedMotion ? "reduced" : "full"}
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}

/** Authenticated Gate 1.8 surface with presentation preferences locked per mount. */
export function LiveSemanticStoryboard({
  layout,
  reducedMotion,
  runStream = runAuthenticatedSemanticStoryboardStream,
  ...props
}: LiveSemanticStoryboardProps) {
  const preferences = useCertifiedPresentationPreferences({
    layout,
    reducedMotion,
  });
  if (!preferences) {
    return (
      <main
        className="dark flex min-h-screen items-center justify-center bg-void px-4 text-center text-sm text-chalk-soft"
        aria-busy="true"
      >
        Preparing the living blackboard…
      </main>
    );
  }

  return (
    <StoryboardStudio
      key={`${preferences.layout}:${preferences.reducedMotion}`}
      {...props}
      preferences={preferences}
      runStream={runStream}
    />
  );
}
