/** @vitest-environment happy-dom */

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const testState = vi.hoisted(() => ({
  cancelConnection: vi.fn(),
  disconnect: vi.fn(),
  endSession: vi.fn(),
  fetchAgent: vi.fn(),
  routerPush: vi.fn(),
  voice: {} as Record<string, unknown>,
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ agentId: "90bd1253-90a6-459a-bf37-365bc3039a76" }),
  useRouter: () => ({ push: testState.routerPush }),
  useSearchParams: () => ({
    get: (key: string) =>
      key === "session" ? "a4f4328e-185e-4c65-b3f7-101e04a37578" : null,
  }),
}));

vi.mock("framer-motion", () => {
  const Passthrough = ({ children }: { children?: ReactNode }) => children;
  return {
    motion: {
      button: Passthrough,
      div: Passthrough,
      p: Passthrough,
      section: Passthrough,
    },
  };
});

vi.mock("gsap", () => ({ gsap: {} }));

vi.mock("@/features/voice/session-view", () => ({
  resolveVoiceRuntimeAssignment: () => "livekit_v2",
}));

vi.mock("@/features/voice/session-runtime-controller", () => ({
  SessionVoiceRuntimeController: ({
    children,
  }: {
    children: (voice: Record<string, unknown>) => ReactNode;
  }) => children(testState.voice),
}));

vi.mock("@/features/voice/voice-fallback-panel", () => ({
  VoiceFallbackPanel: ({
    onContinueInText,
  }: {
    onContinueInText: () => void;
  }) => <button onClick={onContinueInText}>Continue in text</button>,
}));

vi.mock("@/components/mode-toggle", () => ({
  ModeToggle: ({
    onChange,
    disabled,
  }: {
    onChange: (mode: "voice" | "chat") => void;
    disabled?: boolean;
  }) => (
    <div>
      <button disabled={disabled} onClick={() => onChange("voice")}>
        Voice
      </button>
      <button disabled={disabled} onClick={() => onChange("chat")}>
        Text
      </button>
    </div>
  ),
}));

vi.mock("@/components/voice-orb", () => ({
  VoiceOrb: ({
    disabled,
    onClick,
  }: {
    disabled?: boolean;
    onClick?: () => void;
  }) => (
    <button data-testid="voice-orb" disabled={disabled} onClick={onClick}>
      Voice orb
    </button>
  ),
}));

vi.mock("@/components/chat-interface", () => ({
  ChatInterface: () => <div data-testid="chat-interface">Chat</div>,
}));

vi.mock("@/components/svg-canvas", () => ({ SVGCanvas: () => <div /> }));
vi.mock("@/components/status-indicator", () => ({
  StatusIndicator: () => null,
}));
vi.mock("@/components/ui/floating-button", () => ({
  FloatingButton: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/ui/glassmorphic-card", () => ({
  GlassmorphicCard: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/technical-drawer", () => ({ TechnicalDrawer: () => null }));
vi.mock("@/components/control-buttons", () => ({ ControlButtons: () => null }));
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }));
vi.mock("@/components/murmur-doodles", () => ({
  BackgroundDoodles: () => null,
  WaveformToSketch: () => null,
}));

vi.mock("@/hooks/use-chat", () => ({
  useChat: () => ({
    messages: [],
    isLoading: false,
    sendMessage: vi.fn(),
    clearChat: vi.fn(),
  }),
}));

vi.mock("@/lib/scene-kit", () => ({
  compileScene: () => ({ steps: [] }),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "https://api.example.test",
  createSession: vi.fn(),
  endSession: testState.endSession,
  fetchAgent: testState.fetchAgent,
}));

import AgentSessionPage from "./page";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

interface MountedPage {
  readonly container: HTMLDivElement;
  readonly root: Root;
}

function voiceRuntime(overrides: Record<string, unknown> = {}) {
  return {
    runtime: "livekit_v2",
    isConnected: false,
    isVoiceReady: false,
    isConnecting: false,
    canStartVoice: true,
    terminal: false,
    unavailableReason: undefined,
    voiceState: "idle",
    indicatorState: "idle",
    statusLabel: "Start voice",
    pipelineState: "idle",
    isMicMuted: false,
    isTTSEnabled: true,
    audioPlaybackBlocked: false,
    connect: vi.fn(async () => undefined),
    disconnect: testState.disconnect,
    cancelConnection: testState.cancelConnection,
    toggleMicMute: vi.fn(),
    toggleTTS: vi.fn(),
    resumeAudio: vi.fn(async () => undefined),
    ...overrides,
  };
}

async function mountPage(): Promise<MountedPage> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<AgentSessionPage />);
    await Promise.resolve();
  });
  return { container, root };
}

function buttonWithText(container: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent === text
  );
  if (!button) throw new Error(`Missing button: ${text}`);
  return button;
}

describe("AgentSessionPage voice lifecycle", () => {
  beforeEach(() => {
    testState.cancelConnection.mockReset();
    testState.disconnect.mockReset();
    testState.endSession.mockReset().mockResolvedValue({
      id: "a4f4328e-185e-4c65-b3f7-101e04a37578",
      summary: "Gravity recap",
      mastery_count: 1,
      status: "ended",
    });
    testState.fetchAgent.mockReset().mockResolvedValue({
      id: "90bd1253-90a6-459a-bf37-365bc3039a76",
      name: "Newton",
      icon: "N",
      description: null,
      subject: "Physics",
      level: "High school",
      goals: null,
      learning_style: null,
      system_prompt: null,
      is_default: false,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z",
    });
    testState.voice = voiceRuntime();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("disconnects voice before requesting the user-visible session summary", async () => {
    testState.voice = voiceRuntime({
      isConnected: true,
      isVoiceReady: true,
      canStartVoice: false,
      voiceState: "listening",
      indicatorState: "connected",
    });
    const mounted = await mountPage();

    await act(async () => {
      mounted.container.querySelector<HTMLButtonElement>("[data-testid='voice-orb']")?.click();
      await Promise.resolve();
    });

    expect(testState.disconnect).toHaveBeenCalledTimes(1);
    expect(testState.endSession).toHaveBeenCalledTimes(1);
    expect(testState.disconnect.mock.invocationCallOrder[0]).toBeLessThan(
      testState.endSession.mock.invocationCallOrder[0]
    );
    await act(async () => mounted.root.unmount());
  });

  it("cancels an active call before the Voice to Text toggle switches views", async () => {
    testState.voice = voiceRuntime({
      isConnected: true,
      isVoiceReady: true,
      canStartVoice: false,
    });
    const mounted = await mountPage();
    testState.cancelConnection.mockImplementation(() => {
      expect(mounted.container.querySelector("[data-testid='chat-interface']")).toBeNull();
    });

    act(() => buttonWithText(mounted.container, "Text").click());

    expect(testState.cancelConnection).toHaveBeenCalledTimes(1);
    expect(mounted.container.querySelector("[data-testid='chat-interface']")).not.toBeNull();
    await act(async () => mounted.root.unmount());
  });

  it("abandons a retry-retained call before Continue in text switches views", async () => {
    testState.voice = voiceRuntime({
      canStartVoice: true,
      unavailableReason: {
        code: "bootstrap_unavailable",
        message: "Voice is temporarily unavailable.",
        retryable: true,
      },
      voiceState: "error",
      indicatorState: "error",
    });
    const mounted = await mountPage();
    testState.cancelConnection.mockImplementation(() => {
      expect(mounted.container.querySelector("[data-testid='chat-interface']")).toBeNull();
    });

    act(() => buttonWithText(mounted.container, "Continue in text").click());

    expect(testState.cancelConnection).toHaveBeenCalledTimes(1);
    expect(mounted.container.querySelector("[data-testid='chat-interface']")).not.toBeNull();
    await act(async () => mounted.root.unmount());
  });
});
