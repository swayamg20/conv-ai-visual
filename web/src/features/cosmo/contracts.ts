import type { AgentConfig, RealtimeTool, TranscriptDeltaEvent } from "cosmo-ai";
import type { QuadraticVisual } from "./quadratic";

export class CosmoLabError extends Error {}

export class CosmoCleanupError extends CosmoLabError {
  constructor() {
    super("Call cleanup failed. Reload this tab to release its microphone before reconnecting.");
  }
}

export type CallPhase =
  | "idle"
  | "connecting"
  | "ready"
  | "reconnecting"
  | "ending"
  | "ended"
  | "error";

export interface Thought {
  readonly id: string;
  readonly title: string;
  readonly body: string;
  readonly rendered: boolean;
  readonly quadratic?: QuadraticVisual;
}

export interface Transcript {
  readonly id: string;
  readonly role: "user" | "assistant";
  readonly text: string;
  readonly final: boolean;
}

export interface LabSnapshot {
  readonly phase: CallPhase;
  readonly agent: string;
  readonly muted: boolean;
  readonly audioBlocked: boolean;
  readonly sessionId: string | null;
  readonly error: string | null;
  readonly transcript: readonly Transcript[];
  readonly thoughts: readonly Thought[];
  readonly events: readonly { id: number; elapsedMs: number; text: string }[];
}

export interface CallOptions {
  readonly instructions: string;
  readonly provider: "default" | "gemini" | "openai";
  readonly interruption: "default" | "high" | "low";
  readonly turnTaking?: "patient" | "balanced" | "quick";
  readonly storeRecording: boolean;
  readonly microphone: boolean;
}

export interface SessionPort {
  end(): Promise<void>;
  setMuted(muted: boolean): Promise<void>;
  sendText(text: string): Promise<void>;
  resumeAudioPlayback(): Promise<void>;
}

export interface SessionObserver {
  transcript(event: TranscriptDeltaEvent): void;
  agent(state: string): void;
  media(muted: boolean, blocked: boolean): void;
  lifecycle(state: string): void;
  ready(sessionId: string): void;
  event(text: string): void;
  ended(): void;
  error(message: string): void;
}

export interface ConnectRequest {
  readonly options: CallOptions;
  readonly tools: RealtimeTool[];
  readonly observer: SessionObserver;
  readonly signal: AbortSignal;
}

export type SessionConnector = (
  request: ConnectRequest,
) => Promise<SessionPort>;

export const DEFAULT_INSTRUCTIONS = `You are Murmur, a thoughtful conversational partner. Help the user think through ideas through a natural, concise conversation. Listen carefully and respond in the language they use, including English or Hinglish. Give the user room to finish. Ask one useful question at a time.
To show a quadratic you MUST first invoke the show_quadratic function and wait for its rendered:true result. Speaking about a graph does not draw one. Never say a graph is visible or ask whether the user can see it before that tool succeeds. Pass the actual numeric coefficients of ax² + bx + c = 0. For a general request, select a simple example and call the tool immediately. The browser computes the curve, roots, and vertex; explain only those returned facts. Do not narrate your instructions, tool planning, or internal reasoning. Speak only the conversational answer.
Use pin_thought to capture other brief notes and read_thoughts before referring to existing board content. The board is temporary, not durable memory. Other diagram types and external research are not available in this lab. Listen through hesitations and unfinished phrases; do not treat a short pause as an invitation to answer. When interrupted, stop the old explanation and respond to the user's new point. Keep answers short enough for a back-and-forth conversation.`;

export function agentConfig(
  options: CallOptions,
  tools: RealtimeTool[],
): AgentConfig {
  const pacing = options.turnTaking ?? "patient";
  const modelOptions: AgentConfig["modelOptions"] =
    options.provider === "gemini"
      ? { provider: "gemini", includeThoughts: false, turnDetection: "cosmo_vad", cosmoVad: {
          pauseMs: pacing === "patient" ? 2000 : pacing === "balanced" ? 600 : 350,
          prefixMs: 400,
          maxHoldMs: pacing === "patient" ? 5000 : pacing === "balanced" ? 2500 : 1500,
        } }
      : options.provider === "openai"
        ? { provider: "openai", turnDetection: "semantic_vad", eagerness: pacing === "patient" ? "low" : pacing === "balanced" ? "medium" : "high" }
        : undefined;
  return {
    instructions: options.instructions,
    ...(modelOptions ? { modelOptions } : {}),
    interruptionSensitivity: options.interruption,
    greeting: "Hey, I'm here. What are we thinking through?",
    tools,
  };
}
