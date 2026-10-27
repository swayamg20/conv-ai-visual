import {
  CosmoLabError,
  CosmoCleanupError,
  type CallOptions,
  type LabSnapshot,
  type SessionConnector,
  type SessionObserver,
  type SessionPort,
} from "./contracts";
import { ThoughtBoard } from "./thought-board";
import { foldTranscript } from "./transcript";

export const INITIAL_LAB: LabSnapshot = {
  phase: "idle",
  agent: "idle",
  muted: false,
  audioBlocked: false,
  sessionId: null,
  error: null,
  transcript: [],
  thoughts: [],
  events: [],
};

interface Call {
  abort: AbortController;
  board: ThoughtBoard;
  pending: Promise<SessionPort>;
  teardown: Promise<void> | null;
  ready: boolean;
}

function connectionError(error: unknown): string {
  if (error instanceof CosmoLabError) return error.message;
  if (
    error instanceof Error &&
    (error.name === "NotAllowedError" ||
      ("code" in error && error.code === "mic_denied"))
  ) {
    return "Microphone access was denied. Allow it in your browser or turn off the microphone option.";
  }
  return "Could not connect to Cosmo. Check the provider configuration and microphone permission, then retry.";
}

/** One connection attempt at a time, with call-scoped callbacks and teardown. */
export class CosmoLabController {
  private snapshot: LabSnapshot = INITIAL_LAB;
  private listeners = new Set<() => void>();
  private active: Call | null = null;
  private eventId = 0;
  private startedAt = 0;

  constructor(private readonly connector: SessionConnector) {}

  getSnapshot = (): LabSnapshot => this.snapshot;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private update(patch: Partial<LabSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private log(text: string): void {
    this.update({
      events: [...this.snapshot.events, { id: ++this.eventId, elapsedMs: Math.max(0, performance.now() - this.startedAt), text }].slice(
        -120,
      ),
    });
  }

  async start(options: CallOptions): Promise<void> {
    if (this.active) return;
    const abort = new AbortController();
    const board = new ThoughtBoard(abort.signal, (thoughts) => {
      if (this.active === call) this.update({ thoughts });
    });
    const call: Call = {
      abort,
      board,
      pending: Promise.resolve().then(() =>
        this.connector({
          options,
          tools: board.tools(),
          observer,
          signal: abort.signal,
        }),
      ),
      teardown: null,
      ready: false,
    };
    this.active = call;
    this.startedAt = performance.now();
    this.update({
      ...INITIAL_LAB,
      phase: "connecting",
      muted: !options.microphone,
    });
    this.log("Connecting to Cosmo");
    this.log(`${options.provider}; pauses: ${options.provider === "default" ? "workspace default" : options.turnTaking ?? "patient"}; interruption: ${options.interruption}`);
    const current = () => this.active === call && !abort.signal.aborted;
    const seenTranscriptIds = new Set<string>();
    const observer: SessionObserver = {
      transcript: (event) => {
        const identity = `${event.role}:${event.id}`;
        if (!current() || seenTranscriptIds.has(identity)) return;
        seenTranscriptIds.add(identity);
        if (seenTranscriptIds.size > 1000)
          seenTranscriptIds.delete(seenTranscriptIds.values().next().value!);
        this.update({
          transcript: foldTranscript(this.snapshot.transcript, event),
        });
      },
      agent: (agent) => {
        if (current()) this.update({ agent });
      },
      media: (muted, audioBlocked) => {
        if (current())
          this.update({ muted: !options.microphone || muted, audioBlocked });
      },
      lifecycle: (state) => {
        if (!current() || !call.ready) return;
        if (state === "reconnecting") this.update({ phase: "reconnecting" });
        if (state === "connected" && this.snapshot.phase === "reconnecting")
          this.update({ phase: "ready" });
        if (state === "disconnected") {
          this.log("Cosmo disconnected");
          void this.stop();
        }
      },
      ready: (sessionId) => {
        if (current()) this.update({ sessionId });
      },
      event: (text) => {
        if (current()) this.log(text);
      },
      ended: () => {
        if (current()) void this.stop();
      },
      error: (error) => {
        if (current()) this.update({ error });
      },
    };
    try {
      await call.pending;
      if (current()) {
        call.ready = true;
        this.update({ phase: "ready" });
        this.log("Voice session ready");
      } else {
        await this.release(call);
      }
    } catch (error) {
      if (this.active === call && error instanceof CosmoCleanupError) {
        abort.abort();
        this.update({ phase: "ending", error: error.message });
        return;
      }
      if (current()) {
        this.update({ phase: "error", error: connectionError(error) });
        abort.abort();
        await this.release(call);
        if (this.active === call) this.active = null;
      }
    }
  }

  private release(call: Call): Promise<void> {
    if (!call.teardown) {
      call.teardown = (async () => {
        const session = await call.pending.catch((error: unknown) => {
          if (error instanceof CosmoCleanupError) throw error;
          return null;
        });
        if (session) await session.end();
      })();
    }
    return call.teardown;
  }

  async stop(): Promise<void> {
    const call = this.active;
    if (!call) return;
    this.update({ phase: "ending" });
    call.abort.abort();
    try {
      await this.release(call);
      if (this.active === call) {
        this.active = null;
        this.update({
          phase: "ended",
          agent: "idle",
          muted: true,
          audioBlocked: false,
        });
        this.log("Call ended");
      }
    } catch {
      if (this.active === call) {
        // Keep the call owned; a new call cannot overlap uncertain teardown.
        this.update({
          phase: "ending",
          error:
            "Call cleanup failed. Reload this tab to release its microphone before reconnecting.",
        });
      }
    }
  }

  acknowledgeThoughts(ids: readonly string[]): void {
    this.active?.board.acknowledge(ids);
  }

  private async action(
    run: (session: SessionPort) => Promise<void>,
    message: string,
  ): Promise<boolean> {
    const call = this.active;
    if (!call || this.snapshot.phase !== "ready" || call.abort.signal.aborted)
      return false;
    try {
      const session = await call.pending;
      if (this.active !== call || call.abort.signal.aborted) return false;
      await run(session);
      return this.active === call && !call.abort.signal.aborted;
    } catch {
      if (this.active === call && !call.abort.signal.aborted)
        this.update({ error: message });
      return false;
    }
  }

  async toggleMicrophone(): Promise<void> {
    const muted = !this.snapshot.muted;
    await this.action(async (session) => {
      await session.setMuted(muted);
    }, "Could not change the microphone. End the call and retry.");
  }

  async sendText(text: string): Promise<boolean> {
    if (!text.trim() || text.length > 2000) return false;
    return this.action(
      (session) => session.sendText(text.trim()),
      "The message was not sent. Try again.",
    );
  }

  async resumeAudio(): Promise<void> {
    await this.action(
      (session) => session.resumeAudioPlayback(),
      "Audio playback is still blocked. Check your browser settings.",
    );
  }
}
