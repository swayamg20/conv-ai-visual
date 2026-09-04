"use client";

import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Play,
  Radio,
  RotateCcw,
  Send,
  Sparkles,
  Square,
  StopCircle,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { MurmurLogoMark } from "@/components/murmur-doodles";
import { SVGCanvas } from "@/components/svg-canvas";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import type { SVGCanvasHandle } from "@/features/canvas/types";
import { cn } from "@/lib/utils";

import type { SemanticSceneStreamRunner } from "./model-stream";
import {
  SceneStreamRuntime,
  type SceneStreamRenderer,
  type SceneStreamRunner,
  type SceneStreamRuntimePhase,
} from "./stream-runtime";

const DEFAULT_PROMPT =
  "Teach me why the Pythagorean theorem works using a right triangle and areas.";

const DEFAULT_SUGGESTIONS = [
  "Explain a binary search tree",
  "Show how gradient descent walks downhill",
  "Why is the derivative a slope?",
] as const;

const PHASE_LABELS: Record<SceneStreamRuntimePhase, string> = {
  idle: "Ready",
  connecting: "Connecting",
  streaming: "Drawing live",
  repairing: "Repairing draft",
  completing: "Finishing motion",
  completed: "Explanation complete",
  declined: "No visual change",
  failed: "Stream stopped",
  interrupting: "Settling visible work",
  interrupted: "Interrupted safely",
  replaying: "Replaying accepted work",
};

const PHASE_DOTS: Record<SceneStreamRuntimePhase, string> = {
  idle: "bg-chalk-soft",
  connecting: "bg-lavender",
  streaming: "bg-sage",
  repairing: "bg-amber",
  completing: "bg-sage",
  completed: "bg-sage",
  declined: "bg-chalk-soft",
  failed: "bg-ember",
  interrupting: "bg-lavender",
  interrupted: "bg-lavender",
  replaying: "bg-amber",
};

const BUSY_PHASES = new Set<SceneStreamRuntimePhase>([
  "connecting",
  "streaming",
  "repairing",
  "completing",
  "interrupting",
  "replaying",
]);

interface ModelSceneDemoCommonProps {
  readonly sourceLabel?: string;
  readonly startLabel?: string;
  readonly defaultPrompt?: string;
  readonly suggestions?: readonly string[];
  readonly scenarioControl?: ReactNode;
  readonly backHref?: string;
}

export type ModelSceneDemoProps = ModelSceneDemoCommonProps &
  (
    | {
        readonly protocol?: "raw";
        readonly runStream: SceneStreamRunner;
      }
    | {
        readonly protocol: "semantic";
        readonly runStream: SemanticSceneStreamRunner;
      }
  );

class CanvasRendererBridge implements SceneStreamRenderer {
  private handle: SVGCanvasHandle | null = null;

  readonly attach = (handle: SVGCanvasHandle | null): void => {
    this.handle = handle;
  };

  playMotionPlan: SceneStreamRenderer["playMotionPlan"] = (plan, options) => {
    if (!this.handle) throw new Error("The visual board is not ready yet.");
    return this.handle.playMotionPlan(plan, options);
  };

  cancelMotion = (): void => {
    this.handle?.cancelMotion();
  };

  clear = (): void => {
    this.handle?.clear();
  };
}

function generationLabel(generation: number, attempt: number): string {
  if (generation === 0) return "no generation yet";
  return `generation ${generation}${attempt > 0 ? ` · attempt ${attempt}` : ""}`;
}

function readableSemanticRole(role: string): string {
  return role.replaceAll("_", " ").replace(/\b([abc])2\b/g, "$1²");
}

function shortCertificate(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-4)}`;
}

export function ModelSceneDemo(props: ModelSceneDemoProps) {
  const {
    sourceLabel = "Live model",
    startLabel,
    defaultPrompt = DEFAULT_PROMPT,
    suggestions = DEFAULT_SUGGESTIONS,
    scenarioControl,
    backHref = "/canvas",
  } = props;
  const canvasRef = useRef<SVGCanvasHandle>(null);
  const lifecycleRef = useRef<object | null>(null);
  const [prompt, setPrompt] = useState(defaultPrompt);
  const [formError, setFormError] = useState<string | null>(null);
  const [renderer] = useState(() => new CanvasRendererBridge());
  // Callers remount this component when changing stream sources (the lab keys by scenario).
  // Keep the rendered trust vocabulary pinned to that same mounted protocol.
  const [{ runtime, protocol: runtimeProtocol }] = useState(() => {
    if (props.protocol === "semantic") {
      return {
        protocol: "semantic" as const,
        runtime: new SceneStreamRuntime({
          renderer,
          protocol: "semantic",
          runStream: props.runStream,
          staggerMs: 72,
        }),
      };
    }
    return {
      protocol: "raw" as const,
      runtime: new SceneStreamRuntime({
        renderer,
        protocol: "raw",
        runStream: props.runStream,
        staggerMs: 72,
      }),
    };
  });
  const snapshot = useSyncExternalStore(
    runtime.subscribe,
    runtime.getSnapshot,
    runtime.getSnapshot
  );

  useEffect(() => {
    const lifecycle = {};
    lifecycleRef.current = lifecycle;
    renderer.attach(canvasRef.current);
    return () => {
      renderer.attach(null);
      // React Strict Mode performs a same-tick setup/cleanup/setup cycle in
      // development. Dispose only when no replacement setup claimed the owner.
      queueMicrotask(() => {
        if (lifecycleRef.current !== lifecycle) return;
        lifecycleRef.current = null;
        runtime.dispose();
      });
    };
  }, [renderer, runtime]);

  const isSemantic = runtimeProtocol === "semantic";
  const semanticSnapshot = isSemantic ? snapshot.semantic : undefined;
  const acceptedCount = semanticSnapshot?.accepted.length ?? snapshot.accepted.length;
  const phaseLabel =
    isSemantic && snapshot.phase === "idle"
      ? "Ready for verified acts"
      : isSemantic && snapshot.phase === "streaming"
        ? "Presenting verified acts"
        : isSemantic && snapshot.phase === "completing"
          ? "Settling final act"
          : isSemantic && snapshot.phase === "completed"
            ? "Verified acts presented"
            : isSemantic && snapshot.phase === "interrupted"
              ? "Stopped at presented frontier"
              : isSemantic && snapshot.phase === "replaying"
                ? "Replaying presented acts"
                : PHASE_LABELS[snapshot.phase];
  const isBusy = BUSY_PHASES.has(snapshot.phase);
  const canInterrupt = isBusy && snapshot.phase !== "interrupting";
  const canReplay = acceptedCount > 0 && !isBusy;
  const isEmpty = snapshot.committedScene.nodes.length === 0;
  const requiresReset = snapshot.phase === "failed" && snapshot.error?.retryable === false;
  const resolvedStartLabel =
    startLabel ?? (isSemantic ? "Present verified acts" : "Generate live");

  const startGeneration = useCallback(
    (event?: FormEvent) => {
      event?.preventDefault();
      const nextPrompt = prompt.trim();
      if (!nextPrompt) {
        setFormError("Describe what you want the board to explain.");
        return;
      }
      try {
        setFormError(null);
        runtime.start(nextPrompt);
      } catch (error) {
        setFormError(
          error instanceof Error ? error.message : "The live explanation could not start."
        );
      }
    },
    [prompt, runtime]
  );

  const replay = useCallback(() => {
    setFormError(null);
    void runtime.replayAccepted().catch((error: unknown) => {
      setFormError(error instanceof Error ? error.message : "Replay could not start.");
    });
  }, [runtime]);

  const reset = useCallback(() => {
    setFormError(null);
    runtime.reset();
  }, [runtime]);

  return (
    <div className="min-h-screen overflow-x-hidden bg-background">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_78%_16%,hsl(var(--lavender)/0.11),transparent_31%),radial-gradient(circle_at_12%_82%,hsl(var(--amber)/0.08),transparent_34%)]" />

      <header className="relative z-20 border-b border-chalk-faint/20 bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1540px] items-center justify-between px-4 py-3.5 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href={backHref}
              aria-label="Back to canvas"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-graphite hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <MurmurLogoMark className="hidden shrink-0 sm:block" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-sm font-semibold tracking-tight sm:text-lg">
                  {isSemantic ? "Verified-act board" : "Model-authored board"}
                </h1>
                <span className="rounded-full border border-amber/25 bg-amber/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.16em] text-amber">
                  <span className="sm:hidden">{isSemantic ? "G1.1" : "G1"}</span>
                  <span className="hidden sm:inline">
                    {isSemantic ? "Gate 1.1" : "Gate 1"}
                  </span>
                </span>
              </div>
              <p className="hidden text-xs text-muted-foreground sm:block">
                {isSemantic
                  ? "One deterministic visual act at a time, committed after presentation."
                  : "Useful ink appears before the model finishes thinking."}
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="relative z-10 mx-auto grid max-w-[1540px] gap-5 px-4 py-5 sm:px-6 lg:px-8 xl:grid-cols-[370px_minmax(0,1fr)] xl:gap-8 xl:py-7">
        <aside className="min-w-0 border-chalk-faint/20 xl:border-r xl:pr-8">
          <div className="mb-5 flex items-start justify-between gap-4">
            <div>
              <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                {isSemantic ? "Board controls" : "Teacher's desk"}
              </p>
              <h2 className="text-2xl font-semibold tracking-[-0.025em]">
                {isSemantic ? "Direct the visual proof" : "Shape the explanation"}
              </h2>
            </div>
            <Sparkles className="mt-1 h-5 w-5 shrink-0 text-amber" />
          </div>

          <div className="mb-5 flex items-center justify-between border-y border-chalk-faint/20 py-3">
            <div className="flex min-w-0 items-center gap-2.5">
              <span
                aria-hidden="true"
                className={cn("h-2.5 w-2.5 shrink-0 rounded-full", PHASE_DOTS[snapshot.phase], {
                  "animate-pulse": isBusy,
                })}
              />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{phaseLabel}</p>
                <p className="truncate font-mono text-[10px] text-muted-foreground">
                  {generationLabel(snapshot.generation, snapshot.attempt)}
                </p>
              </div>
            </div>
            <span className="ml-2 shrink-0 whitespace-nowrap rounded-md border border-chalk-faint/20 bg-slate/45 px-2 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
              {sourceLabel}
            </span>
          </div>

          {scenarioControl}

          <form onSubmit={startGeneration} className="space-y-4">
            <div>
              <label htmlFor="scene-prompt" className="mb-2 block text-sm font-medium">
                What should the board teach?
              </label>
              <textarea
                id="scene-prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                disabled={isBusy}
                maxLength={2_000}
                rows={5}
                className="min-h-32 w-full resize-y rounded-xl border border-chalk-faint/30 bg-void/60 px-3.5 py-3 text-sm leading-6 text-foreground shadow-inner outline-none transition focus:border-amber/60 focus:ring-2 focus:ring-amber/20 disabled:cursor-not-allowed disabled:opacity-65"
                placeholder="Example: Show why the angle is 90 degrees…"
              />
              <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
                <span>
                  {isSemantic
                    ? "Stop after the current act; anything queued stays off the board."
                    : "Interrupt whenever the explanation changes direction."}
                </span>
                <span className="shrink-0 font-mono">{prompt.length}/2000</span>
              </div>
            </div>

            <div className="flex flex-wrap gap-2" aria-label="Example prompts">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={isBusy}
                  onClick={() => setPrompt(suggestion)}
                  className="rounded-full border border-chalk-faint/25 bg-slate/30 px-3 py-2 text-left text-[11px] leading-4 text-muted-foreground transition hover:border-lavender/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                >
                  {suggestion}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <Button
                type={requiresReset ? "button" : "submit"}
                onClick={requiresReset ? reset : undefined}
                disabled={isBusy || prompt.trim().length === 0}
                className="min-h-11 gap-1.5 px-2 text-xs sm:gap-2 sm:px-5 sm:text-sm"
              >
                {requiresReset ? (
                  <Square className="hidden h-3.5 w-3.5 sm:block" />
                ) : snapshot.phase === "failed" ? (
                  <RotateCcw className="hidden h-4 w-4 sm:block" />
                ) : (
                  <Send className="hidden h-4 w-4 sm:block" />
                )}
                {requiresReset
                  ? "Reset to continue"
                  : snapshot.phase === "failed"
                    ? isSemantic
                      ? "Retry act stream"
                      : "Try again"
                    : resolvedStartLabel}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!canInterrupt}
                onClick={() => runtime.interrupt()}
                className="min-h-11 gap-1.5 border-amber/35 px-2 text-xs hover:bg-amber/10 sm:gap-2 sm:px-5 sm:text-sm"
              >
                <StopCircle className="hidden h-4 w-4 text-amber sm:block" />
                {isSemantic ? "Stop after this act" : "Interrupt"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                disabled={!canReplay}
                onClick={replay}
                className="min-h-11 gap-1.5 px-2 text-xs sm:gap-2 sm:px-5 sm:text-sm"
              >
                <Play className="hidden h-4 w-4 sm:block" />
                {isSemantic ? "Replay presented" : "Replay accepted"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={snapshot.generation === 0 && snapshot.accepted.length === 0}
                onClick={reset}
                className="min-h-11 gap-1.5 px-2 text-xs text-muted-foreground sm:gap-2 sm:px-5 sm:text-sm"
              >
                <Square className="hidden h-3.5 w-3.5 sm:block" />
                {isSemantic ? "Wipe board" : "Reset board"}
              </Button>
            </div>
          </form>

          <div className="mt-5 min-h-[5.5rem] border-l-2 border-lavender/45 pl-4" aria-live="polite">
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-lavender">
              {isSemantic ? "Narration · not fact-checked" : "Live narration"}
            </p>
            <p className="text-sm leading-6 text-foreground/85">{snapshot.narration}</p>
            {(snapshot.error || formError) && (
              <p className="mt-2 flex items-start gap-2 text-xs leading-5 text-ember" role="alert">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                {formError ?? snapshot.error?.message}
              </p>
            )}
            {requiresReset && (
              <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                Reset the board before starting another generation.
              </p>
            )}
          </div>

          <section className="mt-6" aria-labelledby="accepted-ledger-title">
            <div className="mb-3 flex items-end justify-between gap-3">
              <div>
                <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                  {isSemantic ? "Presentation frontier" : "Semantic history"}
                </p>
                <h3 id="accepted-ledger-title" className="mt-0.5 text-sm font-medium">
                  {isSemantic ? "Presented act ledger" : "Accepted patch ledger"}
                </h3>
              </div>
              <span className="font-mono text-[10px] text-muted-foreground">
                {acceptedCount} {isSemantic ? "presented" : "accepted"}
              </span>
            </div>

            {acceptedCount === 0 ? (
              <p className="text-xs leading-5 text-muted-foreground">
                {isSemantic
                  ? "An act lands here only after the browser acknowledges its post-paint settlement."
                  : "Only validated, visible revisions land here. Future and rejected work never does."}
              </p>
            ) : isSemantic && semanticSnapshot ? (
              <ol
                className="max-h-80 space-y-4 overflow-y-auto border-l border-chalk-faint/25 pl-4 pr-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring xl:max-h-[42vh]"
                aria-label="Presented visual acts"
                tabIndex={0}
              >
                {semanticSnapshot.accepted.map((record, index) => {
                  const semantic = record.event.semantic;
                  return (
                    <li
                      key={`${record.event.generation}-${semantic.atomId}`}
                      className="relative pb-4 last:pb-0"
                    >
                      <span className="absolute -left-[1.19rem] top-1 flex h-2.5 w-2.5 rounded-full border-2 border-background bg-sage" />
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-xs font-semibold capitalize">
                            {readableSemanticRole(semantic.role)}
                          </p>
                          <p className="mt-0.5 truncate font-mono text-[9px] text-muted-foreground">
                            atom {semantic.atomId}
                          </p>
                        </div>
                        <span className="shrink-0 font-mono text-[9px] text-muted-foreground">
                          act {index + 1} · s{record.semanticScene.revision}
                        </span>
                      </div>

                      <div className="mt-2 space-y-2 border-t border-chalk-faint/15 pt-2 text-[10px] leading-4 text-muted-foreground">
                        <div>
                          <p className="font-medium text-foreground/80">Server verifier claim</p>
                          <p>{semantic.receipt.obligationCodes.join(" · ")}</p>
                        </div>
                        <p className="font-mono" title={semantic.certificate.certificateSha256}>
                          compiler certificate {shortCertificate(semantic.certificate.certificateSha256)}
                        </p>
                        <div>
                          <p className="font-medium text-foreground/80">
                            Browser post-paint acknowledgement · {record.presentation.settlement}
                          </p>
                          <p className="truncate font-mono">node {record.presentation.nodeId}</p>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            ) : (
              <ol className="max-h-52 overflow-y-auto border-l border-chalk-faint/25 pl-4 pr-1 xl:max-h-[30vh]">
                {snapshot.accepted.map((record, index) => (
                  <li key={`${record.generation}-${record.attempt}-${record.patchId}`} className="relative pb-4 last:pb-0">
                    <span className="absolute -left-[1.19rem] top-1 flex h-2.5 w-2.5 rounded-full border-2 border-background bg-lavender" />
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-xs font-medium">{record.patchId}</p>
                      <span className="shrink-0 font-mono text-[9px] text-muted-foreground">
                        g{record.generation} · r{record.scene.revision}
                      </span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                      {record.narration}
                    </p>
                    {record.materialized && (
                      <span className="mt-1 inline-flex rounded-full bg-lavender/10 px-1.5 py-0.5 font-mono text-[9px] text-lavender">
                        retained at interruption
                      </span>
                    )}
                    <span className="sr-only">Accepted patch {index + 1}</span>
                  </li>
                ))}
              </ol>
            )}

            {isSemantic && (
              <div
                className="mt-4 border-t border-chalk-faint/20 pt-3 text-[10px] leading-4 text-muted-foreground"
                aria-label="Verified act trust boundary"
              >
                Server compiler certificates and verifier obligations are claims received by
                the browser. The browser separately acknowledges the exact node after its
                presentation settles; it does not re-run cryptography or geometry. Narration
                remains explanatory copy and is not fact-checked by this gate.
              </div>
            )}
          </section>
        </aside>

        <section className="min-w-0" aria-label="Live visual board">
          <div className="overflow-hidden rounded-2xl border border-chalk-faint/25 bg-slate/25 shadow-glass">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-chalk-faint/20 px-4 py-3.5 sm:px-5">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-lavender/12 text-lavender">
                  <Radio className={cn("h-4 w-4", { "animate-pulse": isBusy })} />
                </span>
                <div>
                  <p className="text-sm font-medium">
                    {isSemantic ? "Exact presentation frontier" : "Persistent live board"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {isSemantic
                      ? "Server-issued atoms · stable roles · post-paint acknowledgement"
                      : "Streamed patches · stable object identity · SVG motion"}
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-muted-foreground">
                <span className="rounded-md border border-chalk-faint/20 bg-void/60 px-2 py-1">
                  {isSemantic ? "scene" : "revision"} {snapshot.committedScene.revision}
                </span>
                {semanticSnapshot && (
                  <span className="rounded-md border border-sage/25 bg-sage/8 px-2 py-1 text-sage">
                    semantic {semanticSnapshot.committedScene.revision}
                  </span>
                )}
                {snapshot.activeRevision !== undefined && (
                  <span className="rounded-md border border-amber/25 bg-amber/8 px-2 py-1 text-amber">
                    drawing r{snapshot.activeRevision}
                  </span>
                )}
                {snapshot.queuedPatchCount > 0 && (
                  <span className="rounded-md border border-lavender/25 bg-lavender/8 px-2 py-1 text-lavender">
                    {snapshot.queuedPatchCount} queued
                  </span>
                )}
              </div>
            </div>

            <div className="relative bg-void/40 p-2 sm:p-4 lg:p-6">
              <SVGCanvas
                ref={canvasRef}
                width={800}
                height={600}
                className="mx-auto aspect-[4/3] w-full max-w-[1050px] [&>svg]:h-auto [&>svg]:w-full"
              />
              {isEmpty && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-8">
                  <div className="max-w-sm rounded-2xl border border-chalk-faint/25 bg-background/88 px-6 py-5 text-center shadow-glass backdrop-blur-xl">
                    {isBusy ? (
                      <Clock3 className="mx-auto mb-3 h-5 w-5 animate-pulse text-lavender" />
                    ) : (
                      <Sparkles className="mx-auto mb-3 h-5 w-5 text-amber" />
                    )}
                    <p className="mb-1 text-sm font-medium">
                      {isBusy
                        ? isSemantic
                          ? "Waiting for the first presented act…"
                          : "Waiting for the first accepted patch…"
                        : isSemantic
                          ? "Give the board a proof to compose."
                          : "Ask for a visual explanation."}
                    </p>
                    <p className="text-xs leading-5 text-muted-foreground">
                      {isBusy
                        ? isSemantic
                          ? "Each atom joins the committed frontier only after its presentation crosses the browser paint barrier."
                          : "The first useful idea will appear before the full answer is complete."
                        : isSemantic
                          ? "Murmur will place one deterministic visual act at a time; stop after any act."
                          : "Murmur will grow this board piece by piece, and you can interrupt at any moment."}
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {isSemantic ? (
              <>
                <div className="border-l-2 border-sage/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <CheckCircle2 className="h-3.5 w-3.5 text-sage" />
                    Server claim
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    Compiler certificate and verifier obligations arrive with each atom.
                  </p>
                </div>
                <div className="border-l-2 border-lavender/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <Radio className="h-3.5 w-3.5 text-lavender" />
                    Browser acknowledgement
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    The exact node commits after presentation settles, independently of the
                    server claim.
                  </p>
                </div>
                <div className="border-l-2 border-amber/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <AlertTriangle className="h-3.5 w-3.5 text-amber" />
                    Narration boundary
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    Teaching text is not fact-checked by Gate 1.1.
                  </p>
                </div>
              </>
            ) : (
              <>
                <div className="border-l-2 border-sage/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <CheckCircle2 className="h-3.5 w-3.5 text-sage" />
                    Accepted state
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    {snapshot.committedScene.nodes.length} stable object
                    {snapshot.committedScene.nodes.length === 1 ? "" : "s"} on the board.
                  </p>
                </div>
                <div className="border-l-2 border-lavender/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <RotateCcw className="h-3.5 w-3.5 text-lavender" />
                    Interruption-safe
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    Visible ink is retained; old-generation work is rejected.
                  </p>
                </div>
                <div className="border-l-2 border-amber/45 px-3 py-1">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <Clock3 className="h-3.5 w-3.5 text-amber" />
                    Stream timing
                  </div>
                  <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                    {snapshot.completion
                      ? `First patch ${Math.round(snapshot.completion.firstPatchMs)} ms · total ${Math.round(snapshot.completion.totalMs)} ms.`
                      : "Measured from request to accepted model patch."}
                  </p>
                </div>
              </>
            )}
          </div>

          <details className="mt-4 rounded-xl border border-chalk-faint/15 bg-slate/20 px-4 py-3 text-xs text-muted-foreground">
            <summary className="cursor-pointer select-none font-medium text-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Stream diagnostics
            </summary>
            <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 font-mono text-[10px] sm:grid-cols-4">
              <div><dt className="text-muted-foreground">generation</dt><dd className="mt-0.5 text-foreground">{snapshot.generation}</dd></div>
              <div><dt className="text-muted-foreground">attempt</dt><dd className="mt-0.5 text-foreground">{snapshot.attempt}</dd></div>
              <div><dt className="text-muted-foreground">sequence</dt><dd className="mt-0.5 text-foreground">{snapshot.sequence}</dd></div>
              <div><dt className="text-muted-foreground">provisional</dt><dd className="mt-0.5 text-foreground">r{snapshot.provisionalScene.revision}</dd></div>
              {semanticSnapshot && (
                <>
                  <div><dt className="text-muted-foreground">semantic</dt><dd className="mt-0.5 text-foreground">s{semanticSnapshot.committedScene.revision}</dd></div>
                  <div><dt className="text-muted-foreground">presented acts</dt><dd className="mt-0.5 text-foreground">{semanticSnapshot.accepted.length}</dd></div>
                  <div><dt className="text-muted-foreground">frontier</dt><dd className="mt-0.5 truncate text-foreground">{semanticSnapshot.commitFrontier?.atomId ?? "none"}</dd></div>
                </>
              )}
            </dl>
          </details>
        </section>
      </main>
    </div>
  );
}
