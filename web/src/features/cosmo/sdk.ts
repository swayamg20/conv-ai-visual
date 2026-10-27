import type { RealtimeSession } from "cosmo-ai";

import { mintCosmoToken } from "./api";
import {
  CosmoCleanupError,
  CosmoLabError,
  agentConfig,
  type SessionConnector,
  type SessionObserver,
} from "./contracts";

async function traceTurns(session: RealtimeSession, observer: SessionObserver, signal: AbortSignal) {
  const labels: Record<string, string> = {
    "user-started-speaking": "Speech detected",
    "user-stopped-speaking": "Speech stop detected",
    "bot-started-speaking": "Assistant audio started",
    "bot-stopped-speaking": "Assistant audio stopped",
    "bot-llm-started": "Assistant response started",
    "bot-llm-stopped": "Assistant response finished",
  };
  for await (const event of session) {
    if (signal.aborted) break;
    if (labels[event.type]) observer.event(labels[event.type]);
    if (event.type === "turn-complete") observer.event(`${event.role === "USER" ? "User" : "Assistant"} turn completed`);
  }
}

export const connectCosmo: SessionConnector = async ({
  options,
  tools,
  observer,
  signal,
}) => {
  const jwt = await mintCosmoToken(signal);
  signal.throwIfAborted();
  const sdk = await import("cosmo-ai");
  signal.throwIfAborted();
  const client = new sdk.RealtimeClient({ token: jwt });
  const subscriptions: (() => void)[] = [];
  let received: RealtimeSession | undefined;
  let started: Promise<RealtimeSession> | undefined;
  let closing: Promise<void> | null = null;
  let toolRejected = false;
  const end = (): Promise<void> => {
    if (!closing) {
      signal.removeEventListener("abort", abort);
      subscriptions.splice(0).forEach((unsubscribe) => unsubscribe());
      closing = Promise.resolve().then(async () => {
        // SDK 0.6.0 can join a late room after end() during its start POST.
        // Retain ownership until start settles, then close the final transport.
        await started?.catch(() => undefined);
        try {
          await received?.end();
        } catch {
          throw new CosmoCleanupError();
        }
      });
    }
    return closing;
  };
  const abort = () => {
    void end().catch(() => undefined);
  };
  signal.addEventListener("abort", abort, { once: true });
  try {
    started = client.agent(agentConfig(options, tools)).start({
      storeRecording: options.storeRecording,
      publishMicrophone: options.microphone,
      onStateChange: (state) => {
        if (!signal.aborted) observer.lifecycle(state.kind);
      },
      onSession: (session) => {
        received = session;
        if (signal.aborted) return;
        void traceTurns(session, observer, signal).catch(() => {
          if (!signal.aborted) observer.event("Detailed turn events are unavailable for this session.");
        });
        // Attach before connect: only ready/lifecycle replay for late listeners.
        subscriptions.push(
          session.on("transcript", observer.transcript),
          session.on("agent_state", observer.agent),
          session.on("media_state", (state) =>
            observer.media(state.mic === "muted", state.output === "blocked"),
          ),
          session.on("ready", (event) => {
            toolRejected = event.rejectedTools.length > 0;
            observer.ready(event.sessionId);
          }),
          session.on("tool_call", (event) =>
            observer.event(`Tool requested: ${event.name.slice(0, 80)}`),
          ),
          session.on("tool_result", (event) =>
            observer.event(event.ok ? "Tool returned" : "Tool failed"),
          ),
          session.on("session_ending_soon", () =>
            observer.event("This session is ending soon."),
          ),
          session.on("session_ended", observer.ended),
          session.on("error", (error) => {
            if (error)
              observer.error("Cosmo reported a connection or media error. End the call and retry.");
          }),
        );
      },
    });
    const session = await started;
    signal.throwIfAborted();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        session.waitUntilReady(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new CosmoLabError("Cosmo connected but did not become ready. Try again.")), 45000);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
    signal.throwIfAborted();
    if (toolRejected)
      throw new CosmoLabError("Cosmo rejected the thought-board tools. Check the workspace configuration.");
    const snapshot = session.getSnapshot();
    observer.agent(snapshot.agentState);
    observer.media(snapshot.mediaState.mic === "muted", snapshot.mediaState.output === "blocked");
    return {
      end,
      setMuted: (muted) => session.setMuted(muted),
      sendText: (text) => session.sendText(text),
      resumeAudioPlayback: () => session.resumeAudioPlayback(),
    };
  } catch (error) {
    await end();
    if (error instanceof sdk.SessionConfigError)
      throw new CosmoLabError("Cosmo rejected the conversation settings or tools. Check the lab configuration.");
    if (error instanceof sdk.SessionBusyError)
      throw new CosmoLabError("Cosmo's concurrent session limit was reached. End another session or retry shortly.");
    if (error instanceof sdk.SessionEntitlementError)
      throw new CosmoLabError("Cosmo declined this session. Check the workspace's provider access and session allowance.");
    if (error instanceof sdk.SessionStartTransportError)
      throw new CosmoLabError("Could not reach Cosmo. Check the network connection and try again.");
    throw error;
  }
};
