"use client";

import Link from "next/link";
import {
  ArrowLeft,
  CircleHelp,
  Clock3,
  Play,
  RotateCcw,
  Sparkles,
  Square,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { SVGCanvas } from "@/components/svg-canvas";
import { MurmurLogoMark } from "@/components/murmur-doodles";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import type { SVGCanvasHandle } from "@/features/canvas/types";
import {
  materializeSceneTransition,
  planSceneTransition,
  type SceneState,
} from "@/lib/live-scene";
import { cn } from "@/lib/utils";

import {
  createRightAngleExplanationScene,
  PYTHAGORAS_EMPTY_SCENE,
  PYTHAGORAS_FOUNDATION_SCENE,
  PYTHAGORAS_THEOREM_SCENE,
} from "./pythagoras-lesson";

const AUTO_INTERRUPT_DELAY_MS = 700;
const THEOREM_REVEAL_DELAY_MS = 2_050;

type DemoPhase =
  | "idle"
  | "playing"
  | "answering"
  | "interrupted"
  | "completed"
  | "replaying"
  | "error";

type MotionPlayback = ReturnType<SVGCanvasHandle["playMotionPlan"]>;

interface AcceptedScene {
  readonly scene: SceneState;
  readonly generation: number;
  readonly label: string;
}

interface ActiveTransition {
  readonly playback: MotionPlayback;
  readonly previous: SceneState;
  readonly target: SceneState;
  readonly acceptedIndex: number;
}

const PHASE_LABELS: Record<DemoPhase, string> = {
  idle: "Ready",
  playing: "Teaching",
  answering: "Revising",
  interrupted: "Interrupted branch",
  completed: "Lesson complete",
  replaying: "Replaying",
  error: "Needs reset",
};

const PHASE_DOT: Record<DemoPhase, string> = {
  idle: "bg-chalk-soft",
  playing: "bg-sage",
  answering: "bg-amber",
  interrupted: "bg-lavender",
  completed: "bg-sage",
  replaying: "bg-amber",
  error: "bg-ember",
};

function acceptedLabel(record: AcceptedScene): string {
  return `g${record.generation} · r${record.scene.revision}`;
}

export function PythagorasDemo() {
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const currentSceneRef = useRef<SceneState>(PYTHAGORAS_EMPTY_SCENE);
  const acceptedRef = useRef<readonly AcceptedScene[]>([]);
  const activePlaybackRef = useRef<MotionPlayback | null>(null);
  const activeTransitionRef = useRef<ActiveTransition | null>(null);
  const timersRef = useRef<Set<number>>(new Set());
  const epochRef = useRef(0);
  const generationRef = useRef(0);

  const [phase, setPhase] = useState<DemoPhase>("idle");
  const [generation, setGeneration] = useState(0);
  const [revision, setRevision] = useState(0);
  const [accepted, setAccepted] = useState<readonly AcceptedScene[]>([]);
  const [teacherNote, setTeacherNote] = useState(
    "Play the hand-authored lesson, then interrupt it while the triangle is still being drawn."
  );

  const cancelTimers = useCallback(() => {
    for (const timer of timersRef.current) window.clearTimeout(timer);
    timersRef.current.clear();
  }, []);

  const schedule = useCallback((callback: () => void, delayMs: number) => {
    const timer = window.setTimeout(() => {
      timersRef.current.delete(timer);
      callback();
    }, delayMs);
    timersRef.current.add(timer);
    return timer;
  }, []);

  const retainMaterializedTransition = useCallback(
    (transition: ActiveTransition, appliedStepIds: readonly string[], label: string) => {
      const materialized = materializeSceneTransition(
        transition.previous,
        transition.target,
        appliedStepIds
      );
      const nextAccepted = [...acceptedRef.current];
      const previousRecord = nextAccepted[transition.acceptedIndex];
      if (previousRecord) {
        nextAccepted[transition.acceptedIndex] = {
          ...previousRecord,
          scene: materialized,
          label,
        };
      }
      currentSceneRef.current = materialized;
      acceptedRef.current = nextAccepted;
      setAccepted(nextAccepted);
      setRevision(materialized.revision);
    },
    []
  );

  const cancelOwnedMotion = useCallback(() => {
    const playback = activePlaybackRef.current;
    const transition = activeTransitionRef.current;
    const outcome = playback?.cancel();

    if (transition && outcome?.status === "cancelled") {
      retainMaterializedTransition(
        transition,
        outcome.appliedStepIds,
        "Interrupted construction"
      );
    }

    activePlaybackRef.current = null;
    activeTransitionRef.current = null;
    canvasRef.current?.cancelMotion();
  }, [retainMaterializedTransition]);

  const stopOwnedWork = useCallback(() => {
    cancelTimers();
    cancelOwnedMotion();
  }, [cancelOwnedMotion, cancelTimers]);

  useEffect(
    () => () => {
      epochRef.current += 1;
      cancelTimers();
      activePlaybackRef.current?.cancel();
      canvasRef.current?.cancelMotion();
    },
    [cancelTimers]
  );

  const playAcceptedTransition = useCallback(
    (
      nextScene: SceneState,
      nextGeneration: number,
      label: string,
      staggerMs = 95
    ): MotionPlayback | null => {
      const previousScene = currentSceneRef.current;
      try {
        const plan = planSceneTransition(previousScene, nextScene);
        const record: AcceptedScene = {
          scene: nextScene,
          generation: nextGeneration,
          label,
        };
        const acceptedIndex = acceptedRef.current.length;
        const nextAccepted = [...acceptedRef.current, record];

        currentSceneRef.current = nextScene;
        acceptedRef.current = nextAccepted;
        setAccepted(nextAccepted);
        setRevision(nextScene.revision);

        const playback = canvasRef.current?.playMotionPlan(plan, { staggerMs });
        if (!playback) {
          throw new Error("The visual board is not ready yet.");
        }
        const transition: ActiveTransition = {
          playback,
          previous: previousScene,
          target: nextScene,
          acceptedIndex,
        };
        activePlaybackRef.current = playback;
        activeTransitionRef.current = transition;
        void playback.finished.then((outcome) => {
          if (outcome.status === "failed") {
            retainMaterializedTransition(
              transition,
              outcome.appliedStepIds,
              "Renderer stopped early"
            );
            epochRef.current += 1;
            setPhase("error");
            setTeacherNote(outcome.error ?? "The scene transition could not be rendered.");
          }
          if (activePlaybackRef.current === playback) activePlaybackRef.current = null;
          if (activeTransitionRef.current === transition) activeTransitionRef.current = null;
        });
        return playback;
      } catch (error) {
        setPhase("error");
        setTeacherNote(
          error instanceof Error ? error.message : "The scene transition could not be rendered."
        );
        return null;
      }
    },
    [retainMaterializedTransition]
  );

  const interruptLesson = useCallback(() => {
    if (currentSceneRef.current.revision === 0) return;

    const epoch = ++epochRef.current;
    stopOwnedWork();
    const nextGeneration = generationRef.current + 1;
    generationRef.current = nextGeneration;
    setGeneration(nextGeneration);
    setPhase("answering");
    setTeacherNote(
      "Good interruption. I kept the board exactly where it was and cancelled the unshown theorem."
    );

    const focusedScene = createRightAngleExplanationScene(currentSceneRef.current);
    const playback = playAcceptedTransition(
      focusedScene,
      nextGeneration,
      "Focused on the right angle",
      70
    );

    if (!playback) return;
    void playback.finished.then((outcome) => {
      if (epochRef.current !== epoch || outcome.status !== "completed") return;
      canvasRef.current?.emphasizeElement(
        "triangle-right-angle",
        "hsl(var(--amber))"
      );
      setPhase("interrupted");
      setTeacherNote(
        "The two legs are horizontal and vertical, so they meet perpendicularly. No stale future step was allowed onto the board."
      );
    });
  }, [playAcceptedTransition, stopOwnedWork]);

  const startLesson = useCallback(
    (autoInterrupt: boolean) => {
      if (currentSceneRef.current.revision !== 0 || phase !== "idle") return;

      stopOwnedWork();
      const epoch = ++epochRef.current;
      generationRef.current = 1;
      setGeneration(1);
      setPhase("playing");
      setTeacherNote(
        autoInterrupt
          ? "The interruption is armed for 700 ms. Watch the unfinished motion freeze before the question is answered."
          : "I am constructing the triangle first. You can interrupt before the theorem appears."
      );

      const playback = playAcceptedTransition(
        PYTHAGORAS_FOUNDATION_SCENE,
        1,
        "Built the triangle",
        70
      );
      if (!playback) return;

      schedule(() => {
        if (epochRef.current !== epoch || currentSceneRef.current.revision !== 1) return;
        setTeacherNote("Now the relationship between the three sides can appear.");
        const theoremPlayback = playAcceptedTransition(
          PYTHAGORAS_THEOREM_SCENE,
          1,
          "Revealed the theorem",
          90
        );
        if (!theoremPlayback) return;
        void theoremPlayback.finished.then((outcome) => {
            if (epochRef.current !== epoch || outcome.status !== "completed") return;
            setPhase("completed");
            setTeacherNote(
              "The uninterrupted branch completed. Replay uses the same accepted semantic revisions, not a recorded video."
            );
          });
      }, THEOREM_REVEAL_DELAY_MS);

      if (autoInterrupt) {
        schedule(() => {
          if (epochRef.current === epoch) interruptLesson();
        }, AUTO_INTERRUPT_DELAY_MS);
      }
    },
    [interruptLesson, phase, playAcceptedTransition, schedule, stopOwnedWork]
  );

  const replayAccepted = useCallback(() => {
    if (acceptedRef.current.length === 0 || phase === "replaying") return;

    const epoch = ++epochRef.current;
    stopOwnedWork();
    const replayScenes = [...acceptedRef.current];
    canvasRef.current?.clear();
    currentSceneRef.current = PYTHAGORAS_EMPTY_SCENE;
    setRevision(0);
    setPhase("replaying");
    setTeacherNote(
      `Replaying ${replayScenes.length} accepted revision${replayScenes.length === 1 ? "" : "s"} from semantic state.`
    );

    void (async () => {
      for (const record of replayScenes) {
        if (epochRef.current !== epoch) return;
        const plan = planSceneTransition(currentSceneRef.current, record.scene);
        currentSceneRef.current = record.scene;
        setRevision(record.scene.revision);
        const playback = canvasRef.current?.playMotionPlan(plan, { staggerMs: 75 });
        if (!playback) {
          setPhase("error");
          setTeacherNote("The visual board was unavailable during replay.");
          return;
        }
        activePlaybackRef.current = playback;
        const outcome = await playback.finished;
        if (outcome.status !== "completed") {
          if (outcome.status === "failed") {
            setPhase("error");
            setTeacherNote(outcome.error ?? "Replay stopped before it completed.");
          }
          return;
        }
      }

      if (epochRef.current !== epoch) return;
      activePlaybackRef.current = null;
      const interrupted = replayScenes.some((record) => record.generation > 1);
      setPhase(interrupted ? "interrupted" : "completed");
      setTeacherNote(
        interrupted
          ? "Replay arrived at the same interruption branch with the same stable object identities."
          : "Replay arrived at the same completed theorem scene with the same stable object identities."
      );
    })();
  }, [phase, stopOwnedWork]);

  const resetDemo = useCallback(() => {
    epochRef.current += 1;
    stopOwnedWork();
    canvasRef.current?.clear();
    currentSceneRef.current = PYTHAGORAS_EMPTY_SCENE;
    acceptedRef.current = [];
    generationRef.current = 0;
    setAccepted([]);
    setGeneration(0);
    setRevision(0);
    setPhase("idle");
    setTeacherNote(
      "Play the hand-authored lesson, then interrupt it while the triangle is still being drawn."
    );
  }, [stopOwnedWork]);

  const canInterrupt = phase === "playing";
  const canStart = phase === "idle" && revision === 0;
  const canReplay = accepted.length > 0 && phase !== "replaying" && phase !== "answering";

  return (
    <div className="min-h-screen overflow-hidden bg-background">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_18%_12%,hsl(var(--lavender)/0.12),transparent_34%),radial-gradient(circle_at_82%_74%,hsl(var(--amber)/0.08),transparent_32%)]" />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-background/75 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1480px] items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href="/canvas"
              aria-label="Back to canvas"
              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-graphite hover:text-foreground"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <MurmurLogoMark className="shrink-0" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-base font-semibold sm:text-lg">Live scene lab</h1>
                <span className="rounded-full border border-amber/25 bg-amber/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-amber">
                  Gate 0
                </span>
              </div>
              <p className="hidden text-xs text-muted-foreground sm:block">
                Deterministic board state · interruptible motion
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1480px] gap-5 px-4 py-5 sm:px-6 lg:px-8 xl:grid-cols-[340px_minmax(0,1fr)] xl:gap-7 xl:py-7">
        <aside className="flex flex-col gap-4 xl:max-h-[calc(100vh-7.5rem)]">
          <GlassmorphicCard variant="elevated" shadow="lg" padding="lg">
            <div className="mb-5 flex items-start justify-between gap-3">
              <div>
                <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  Pythagorean theorem
                </p>
                <h2 className="text-xl font-semibold tracking-tight">Interrupt the teacher</h2>
              </div>
              <Sparkles className="mt-1 h-5 w-5 shrink-0 text-amber" />
            </div>

            <div className="mb-5 rounded-xl border border-chalk-faint/20 bg-void/55 p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <span
                    className={cn("h-2 w-2 rounded-full", PHASE_DOT[phase], {
                      "animate-pulse": phase === "playing" || phase === "answering" || phase === "replaying",
                    })}
                  />
                  <span className="text-xs font-medium">{PHASE_LABELS[phase]}</span>
                </div>
                <span className="font-mono text-[10px] text-muted-foreground">
                  g{generation} / r{revision}
                </span>
              </div>
              <p className="min-h-[4.5rem] text-sm leading-6 text-foreground/85" aria-live="polite">
                {teacherNote}
              </p>
            </div>

            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              <Button
                onClick={() => startLesson(false)}
                disabled={!canStart}
                className="justify-start gap-2"
              >
                <Play className="h-4 w-4" />
                Play lesson
              </Button>
              <Button
                onClick={interruptLesson}
                disabled={!canInterrupt}
                variant="outline"
                className="justify-start gap-2 border-amber/30 text-foreground hover:bg-amber/10"
              >
                <CircleHelp className="h-4 w-4 text-amber" />
                Ask why 90°
              </Button>
              <Button
                onClick={() => startLesson(true)}
                disabled={!canStart}
                variant="secondary"
                className="justify-start gap-2"
              >
                <Clock3 className="h-4 w-4" />
                Auto-interrupt
              </Button>
              <Button
                onClick={replayAccepted}
                disabled={!canReplay}
                variant="secondary"
                className="justify-start gap-2"
              >
                <RotateCcw className="h-4 w-4" />
                Replay accepted
              </Button>
              <Button
                onClick={resetDemo}
                disabled={phase === "idle" && accepted.length === 0}
                variant="ghost"
                className="justify-start gap-2 text-muted-foreground sm:col-span-2 xl:col-span-1"
              >
                <Square className="h-3.5 w-3.5" />
                Reset board
              </Button>
            </div>
          </GlassmorphicCard>

          <GlassmorphicCard padding="lg" className="min-h-0 xl:flex-1">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-medium">Accepted scene ledger</h3>
              <span className="font-mono text-[10px] text-muted-foreground">
                {accepted.length} commit{accepted.length === 1 ? "" : "s"}
              </span>
            </div>
            {accepted.length === 0 ? (
              <p className="text-xs leading-5 text-muted-foreground">
                Only accepted semantic revisions appear here. Scheduled future visuals do not.
              </p>
            ) : (
              <ol className="max-h-44 space-y-2 overflow-y-auto pr-1 xl:max-h-full">
                {accepted.map((record, index) => (
                  <li
                    key={`${record.generation}-${record.scene.revision}-${record.label}`}
                    className="flex items-start gap-3 rounded-lg border border-chalk-faint/15 bg-void/45 px-3 py-2.5"
                  >
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-lavender/15 font-mono text-[9px] text-lavender">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium">{record.label}</p>
                      <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                        {acceptedLabel(record)} · {record.scene.nodes.length} objects
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </GlassmorphicCard>
        </aside>

        <section className="min-w-0">
          <GlassmorphicCard
            variant="elevated"
            shadow="lg"
            padding="none"
            className="overflow-hidden"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-chalk-faint/20 px-4 py-3 sm:px-5">
              <div>
                <p className="text-sm font-medium">Persistent semantic board</p>
                <p className="text-xs text-muted-foreground">
                  SVG + GSAP · no video render · no model call
                </p>
              </div>
              <div className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
                <span className="rounded-md border border-chalk-faint/20 bg-void/60 px-2 py-1">
                  generation {generation}
                </span>
                <span className="rounded-md border border-chalk-faint/20 bg-void/60 px-2 py-1">
                  revision {revision}
                </span>
              </div>
            </div>

            <div className="relative bg-void/35 p-2 sm:p-4 lg:p-6">
              <SVGCanvas
                ref={canvasRef}
                width={800}
                height={600}
                className="mx-auto aspect-[4/3] w-full max-w-[1000px] [&>svg]:h-auto [&>svg]:w-full"
              />
              {revision === 0 && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-8">
                  <div className="max-w-sm rounded-2xl border border-chalk-faint/20 bg-background/80 px-6 py-5 text-center shadow-glass backdrop-blur-xl">
                    <p className="mb-1 text-sm font-medium">The board is waiting.</p>
                    <p className="text-xs leading-5 text-muted-foreground">
                      Start normally, or choose auto-interrupt to exercise cancellation at a repeatable point.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </GlassmorphicCard>

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {[
              ["Stable identity", "The triangle keeps the same semantic IDs across revisions."],
              ["Branch, do not redraw", "The question evolves the retained board in place."],
              ["Stale work blocked", "Cancelled timers cannot reveal the queued theorem later."],
            ].map(([title, body]) => (
              <div
                key={title}
                className="rounded-xl border border-chalk-faint/15 bg-slate/25 px-4 py-3"
              >
                <p className="text-xs font-medium">{title}</p>
                <p className="mt-1 text-[11px] leading-4 text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
