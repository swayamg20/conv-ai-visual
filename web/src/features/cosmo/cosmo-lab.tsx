"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  ArrowLeft,
  ArrowUp,
  AudioLines,
  Mic,
  MicOff,
  PhoneOff,
  Pin,
  Volume2,
} from "lucide-react";

import { signInWithGoogle, useAuth } from "@/hooks/use-auth";

import {
  DEFAULT_INSTRUCTIONS,
  type CallOptions,
  type LabSnapshot,
  type Thought,
} from "./contracts";
import { CosmoLabController, INITIAL_LAB } from "./controller";
import { connectCosmo } from "./sdk";
import { QuadraticCard } from "./quadratic-card";

const control =
  "min-h-11 rounded-xl border border-border bg-background px-4 py-2 text-sm transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lavender";
const field =
  "w-full rounded-lg border border-border bg-background p-3 text-sm outline-none focus:ring-2 focus:ring-lavender disabled:opacity-50";
const phaseLabels: Record<LabSnapshot["phase"], string> = {
  idle: "Ready when you are",
  connecting: "Connecting…",
  ready: "Connected",
  reconnecting: "Reconnecting…",
  ending: "Ending the call…",
  ended: "Call ended",
  error: "Connection needs attention",
};

function ThoughtCard({
  thought,
  acknowledge,
}: {
  thought: Thought;
  acknowledge: (id: string) => void;
}) {
  if (thought.quadratic) return <QuadraticCard thought={thought} visual={thought.quadratic} acknowledge={acknowledge} />;
  return <NoteCard thought={thought} acknowledge={acknowledge} />;
}

function NoteCard({ thought, acknowledge }: { thought: Thought; acknowledge: (id: string) => void }) {
  useEffect(() => {
    acknowledge(thought.id);
  }, [thought.id, acknowledge]);
  return (
    <article
      className="rounded-xl border border-lavender/25 bg-lavender/5 p-5"
      data-thought-id={thought.id}
    >
      <div className="mb-3 flex items-start gap-2">
        <Pin size={15} className="mt-1 shrink-0 text-lavender" />
        <h3 className="break-words font-medium">{thought.title}</h3>
      </div>
      <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
        {thought.body}
      </p>
    </article>
  );
}

export function CosmoLab() {
  const { user, isLoading } = useAuth();
  const [controller] = useState(() => new CosmoLabController(connectCosmo));
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    () => INITIAL_LAB,
  );
  const [options, setOptions] = useState<CallOptions>({
    instructions: DEFAULT_INSTRUCTIONS,
    provider: "gemini",
    interruption: "high",
    turnTaking: "patient",
    storeRecording: false,
    microphone: true,
  });
  const [draft, setDraft] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const transcriptEnd = useRef<HTMLDivElement>(null);
  const ownerId = useRef<string | null>(null);
  const active = ["connecting", "ready", "reconnecting", "ending"].includes(
    state.phase,
  );
  const ready = state.phase === "ready";

  useEffect(
    () => () => {
      void controller.stop();
    },
    [controller],
  );
  useEffect(() => {
    const id = user?.id ?? null;
    if (ownerId.current && ownerId.current !== id) void controller.stop();
    ownerId.current = id;
  }, [controller, user?.id]);
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [state.transcript]);

  const acknowledge = useState(
    () => (id: string) => controller.acknowledgeThoughts([id]),
  )[0];
  const connect = () => {
    if (user && options.instructions.trim()) void controller.start(options);
  };

  return (
    <main className="min-h-screen bg-background px-5 py-7 text-foreground sm:px-9 lg:px-14">
      <div className="mx-auto max-w-6xl">
        <header className="mb-10 flex items-center justify-between gap-4 border-b border-border pb-5">
          <Link
            href="/dashboard"
            className="flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft size={16} /> Murmur
          </Link>
          <span className="font-mono text-xs tracking-wider text-muted-foreground">
            LAB / COSMO VOICE
          </span>
        </header>
        <div className="mb-9 max-w-2xl">
          <p className="mb-3 text-xs font-medium uppercase tracking-[0.18em] text-lavender">
            A space to think out loud
          </p>
          <h1 className="text-4xl font-medium tracking-tight sm:text-5xl">
            Start with a thought.
          </h1>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            Talk it through, interrupt, change direction. Ask Murmur to pin the
            ideas you want to keep in view.
          </p>
        </div>

        {!user && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-muted/30 p-5">
            <p className="text-sm text-muted-foreground">
              {isLoading
                ? "Checking your sign-in…"
                : "Sign in to start a conversation."}
            </p>
            {!isLoading && (
              <div className="flex gap-2">
                <button
                  className={control}
                  onClick={() => {
                    void signInWithGoogle().catch(() =>
                      setAuthError(
                        "Sign-in did not finish. Try again or use email sign-in.",
                      ),
                    );
                  }}
                >
                  Continue with Google
                </button>
                <Link className={`${control} flex items-center`} href="/login">
                  Email sign-in
                </Link>
              </div>
            )}
          </div>
        )}
        {(state.error || authError) && (
          <p
            role="alert"
            className="mb-6 rounded-xl border border-ember/30 bg-ember/5 p-4 text-sm"
          >
            {state.error || authError}
          </p>
        )}

        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section
            aria-label="Conversation"
            className="overflow-hidden rounded-2xl border border-border"
          >
            <div className="flex items-center gap-3 border-b border-border bg-muted/20 p-5">
              <span
                className={`flex h-11 w-11 items-center justify-center rounded-full ${ready ? "bg-lavender/15 text-lavender" : "bg-muted text-muted-foreground"}`}
              >
                <AudioLines size={22} aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <p role="status" className="text-sm font-medium">
                  {phaseLabels[state.phase]}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {ready
                    ? `${state.agent === "speaking" ? "Murmur is speaking" : state.agent === "thinking" ? "Thinking…" : "Listening"}${state.muted ? " · microphone off" : ""}`
                    : "Voice powered by Cosmo"}
                </p>
              </div>
            </div>
            <div
              aria-label="Live transcript"
              className="h-[340px] space-y-5 overflow-y-auto p-5 sm:h-[400px]"
            >
              {state.transcript.length === 0 ? (
                <div className="flex h-full flex-col justify-center px-3 text-center">
                  <p className="font-handwriting text-3xl text-muted-foreground">
                    What’s on your mind?
                  </p>
                  <p className="mx-auto mt-4 max-w-xs text-sm leading-relaxed text-muted-foreground">
                    Try “Help me think through an idea,” or “Pin this thought:
                    start small and test it.”
                  </p>
                </div>
              ) : (
                state.transcript.map((item) => (
                  <div
                    key={item.id}
                    data-transcript-role={item.role}
                    className={
                      item.role === "user"
                        ? "ml-8 rounded-xl bg-muted/50 p-4"
                        : "mr-6 py-1"
                    }
                  >
                    <p className="mb-2 text-xs font-medium text-muted-foreground">
                      {item.role === "user" ? "You" : "Murmur"}
                    </p>
                    <p
                      className={`whitespace-pre-wrap break-words text-sm leading-relaxed ${item.final ? "" : "opacity-70"}`}
                    >
                      {item.text}
                    </p>
                  </div>
                ))
              )}
              <div ref={transcriptEnd} />
            </div>
            <div className="space-y-3 border-t border-border p-4">
              <div className="flex flex-wrap gap-2">
                {active ? (
                  <button
                    className={`${control} flex items-center gap-2 text-ember`}
                    disabled={state.phase === "ending"}
                    onClick={() => {
                      void controller.stop();
                    }}
                  >
                    <PhoneOff size={16} />
                    {state.phase === "connecting" ? "Cancel" : "End call"}
                  </button>
                ) : (
                  <button
                    className={`${control} flex items-center gap-2 border-lavender/40 bg-lavender/10`}
                    disabled={!user || !options.instructions.trim()}
                    onClick={connect}
                  >
                    <AudioLines size={17} />
                    Start conversation
                  </button>
                )}
                <button
                  className={`${control} flex items-center gap-2`}
                  disabled={!ready || !options.microphone}
                  onClick={() => {
                    void controller.toggleMicrophone();
                  }}
                >
                  {state.muted ? <MicOff size={16} /> : <Mic size={16} />}
                  {state.muted ? "Unmute" : "Mute"}
                </button>
                {state.audioBlocked && (
                  <button
                    className={`${control} flex items-center gap-2`}
                    onClick={() => {
                      void controller.resumeAudio();
                    }}
                  >
                    <Volume2 size={16} />
                    Enable audio
                  </button>
                )}
              </div>
              <form
                className="flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  if (!draft.trim() || sending) return;
                  setSending(true);
                  void controller
                    .sendText(draft)
                    .then((sent) => {
                      if (sent) setDraft("");
                    })
                    .finally(() => setSending(false));
                }}
              >
                <input
                  aria-label="Message to Murmur"
                  className={field}
                  value={draft}
                  maxLength={2000}
                  disabled={!ready || sending}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Or type a thought…"
                />
                <button
                  aria-label="Send message"
                  className={control}
                  disabled={!ready || !draft.trim() || sending}
                >
                  <ArrowUp size={18} />
                </button>
              </form>
            </div>
          </section>

          <section
            aria-label="Thought board"
            className="min-h-[320px] rounded-2xl border border-dashed border-border p-5 sm:p-6"
          >
            <div className="mb-6 flex items-center justify-between">
              <h2 className="text-sm font-medium">Thought board</h2>
              <span className="text-xs text-muted-foreground">
                {state.thoughts.length} / 12
              </span>
            </div>
            {state.thoughts.length === 0 ? (
              <div className="flex min-h-[260px] flex-col items-center justify-center text-center">
                <Pin className="mb-4 text-chalk-faint" size={25} />
                <p className="font-handwriting text-2xl text-muted-foreground">
                  Give an idea a place to land.
                </p>
                <p className="mt-3 max-w-xs text-sm leading-relaxed text-muted-foreground">
                  Ask to pin a thought. It will stay here while you keep
                  talking.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {state.thoughts.map((thought) => (
                  <ThoughtCard
                    key={thought.id}
                    thought={thought}
                    acknowledge={acknowledge}
                  />
                ))}
              </div>
            )}
            <p className="mt-6 text-xs leading-relaxed text-muted-foreground">
              Temporary notes for this conversation. Starting another call
              clears this board.
            </p>
          </section>
        </div>

        <details className="mt-7 rounded-xl border border-border p-5">
          <summary className="cursor-pointer text-sm font-medium">
            Conversation settings
          </summary>
          <fieldset
            disabled={active}
            className="mt-5 space-y-5 disabled:opacity-60"
          >
            <div className="grid gap-5 sm:grid-cols-2">
              <label className="space-y-2 text-sm">
                <span>Voice provider</span>
                <select
                  aria-label="Voice provider"
                  className={field}
                  value={options.provider}
                  onChange={(e) =>
                    setOptions({
                      ...options,
                      provider: e.target.value as CallOptions["provider"],
                    })
                  }
                >
                  <option value="default">Workspace default</option>
                  <option value="gemini">Gemini</option>
                  <option value="openai">OpenAI</option>
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span>Interruption sensitivity</span>
                <select
                  aria-label="Interruption sensitivity"
                  className={field}
                  value={options.interruption}
                  onChange={(e) =>
                    setOptions({
                      ...options,
                      interruption: e.target
                        .value as CallOptions["interruption"],
                    })
                  }
                >
                  <option value="default">Default</option>
                  <option value="high">High — yield quickly</option>
                  <option value="low">Low — allow short interjections</option>
                </select>
              </label>
            </div>
            <label className="block space-y-2 text-sm">
              <span>Pause before replying</span>
              <select aria-label="Pause before replying" className={field} value={options.turnTaking} disabled={options.provider === "default"} onChange={(event) => setOptions({ ...options, turnTaking: event.target.value as CallOptions["turnTaking"] })}>
                <option value="patient">Patient — give me time to think</option>
                <option value="balanced">Balanced</option>
                <option value="quick">Quick — respond sooner</option>
              </select>
              <span className="block text-xs text-muted-foreground">{options.provider === "default" ? "Choose Gemini or OpenAI to tune pauses." : options.provider === "gemini" && options.turnTaking === "patient" ? "Allows 2 seconds of silence before checking whether you're finished. Replies may start more slowly. Applies to your next call." : "Pauses and interruptions are separate. These settings apply to the next call."}</span>
            </label>
            <label className="block space-y-2 text-sm">
              <span>Instructions</span>
              <textarea
                aria-label="Instructions"
                className={`${field} min-h-40 leading-relaxed`}
                value={options.instructions}
                maxLength={12000}
                onChange={(e) =>
                  setOptions({ ...options, instructions: e.target.value })
                }
              />
            </label>
            <div className="flex flex-wrap gap-6 text-sm">
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={options.microphone}
                  onChange={(e) =>
                    setOptions({ ...options, microphone: e.target.checked })
                  }
                />
                Use microphone
              </label>
              <label className="flex min-h-11 items-center gap-2">
                <input
                  type="checkbox"
                  checked={options.storeRecording}
                  onChange={(e) =>
                    setOptions({ ...options, storeRecording: e.target.checked })
                  }
                />
                Store this session’s recording with Cosmo
              </label>
            </div>
          </fieldset>
        </details>
        <details className="mt-4 rounded-xl border border-border p-5">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Session activity
          </summary>
          <div className="mt-4 space-y-2 font-mono text-xs text-muted-foreground">
            {state.sessionId && (
              <p className="break-all">Session {state.sessionId}</p>
            )}
            {state.events.length ? (
              state.events.map((event) => <p key={event.id}>+{(event.elapsedMs / 1000).toFixed(2)}s · {event.text}</p>)
            ) : (
              <p>No session started.</p>
            )}
          </div>
        </details>
        <footer className="mt-8 pb-4 text-xs leading-relaxed text-muted-foreground">
          Cosmo voice experiment · Quadratic graphs use Murmur’s canvas renderer.
          This board lasts for one conversation.
        </footer>
      </div>
    </main>
  );
}
