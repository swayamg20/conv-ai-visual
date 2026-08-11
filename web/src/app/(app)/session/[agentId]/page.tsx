"use client";

import { useState, useRef, useCallback, useMemo, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import { Mic, ArrowLeft, ChevronUp, Loader2 } from "lucide-react";
import { gsap } from "gsap";
import { SVGCanvas } from "@/components/svg-canvas";
import type {
  CanvasOperation,
  SVGCanvasHandle,
  TeachingSequence,
} from "@/features/canvas/types";
import { useWebRTC, type TranscriptEvent, type PipelineState } from "@/hooks/use-webrtc";
import { useChat } from "@/hooks/use-chat";
import { compileScene, type SDLScene } from "@/lib/scene-kit";
import { VoiceOrb, type VoiceState } from "@/components/voice-orb";
import { StatusIndicator } from "@/components/status-indicator";
import { FloatingButton } from "@/components/ui/floating-button";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import { TechnicalDrawer } from "@/components/technical-drawer";
import { ControlButtons } from "@/components/control-buttons";
import { ModeToggle, type AppMode } from "@/components/mode-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { ChatInterface } from "@/components/chat-interface";
import { BackgroundDoodles, WaveformToSketch } from "@/components/murmur-doodles";
import { fetchAgent, createSession, endSession, API_BASE } from "@/lib/api";
import type { Agent, Session, SessionEndResponse } from "@/lib/types";

export default function AgentSessionPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const agentId = params.agentId as string;
  const existingSessionId = searchParams.get("session");

  const [agent, setAgent] = useState<Agent | null>(null);
  const [agentLoading, setAgentLoading] = useState(true);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(existingSessionId);
  const sessionIdRef = useRef<string | null>(existingSessionId);
  const sessionInitPromiseRef = useRef<Promise<Session> | null>(null);
  const [sessionEndResult, setSessionEndResult] = useState<SessionEndResponse | null>(null);
  const [isEndingSession, setIsEndingSession] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAgent(agentId)
      .then((a) => { if (!cancelled) setAgent(a); })
      .catch((err) => { if (!cancelled) setAgentError(err.message); })
      .finally(() => { if (!cancelled) setAgentLoading(false); });
    return () => { cancelled = true; };
  }, [agentId]);

  // Create a new session if no session param was provided
  useEffect(() => {
    if (existingSessionId) return; // Already have a session to resume
    let cancelled = false;
    const sessionPromise = createSession(agentId);
    sessionInitPromiseRef.current = sessionPromise;
    sessionPromise
      .then((session) => {
        if (!cancelled && !sessionIdRef.current) {
          setSessionId(session.id);
          sessionIdRef.current = session.id;
        }
      })
      .catch(() => {
        // Non-blocking — session tracking is optional
      })
      .finally(() => {
        if (sessionInitPromiseRef.current === sessionPromise) {
          sessionInitPromiseRef.current = null;
        }
      });
    return () => { cancelled = true; };
  }, [agentId, existingSessionId]);

  // End session on unmount or tab close
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (sessionIdRef.current) {
        // Use sendBeacon for reliability on tab close
        const url = `${API_BASE}/api/sessions/${sessionIdRef.current}/end`;
        navigator.sendBeacon(url);
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      // Fire end-session on component unmount (navigation away)
      if (sessionIdRef.current) {
        void endSession(sessionIdRef.current).catch(() => {});
      }
    };
  }, []);

  const [appMode, setAppMode] = useState<AppMode>("voice");
  const [transcripts, setTranscripts] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const canvasRef = useRef<SVGCanvasHandle>(null);

  const handleCanvasUpdate = useCallback((operations: CanvasOperation[]) => {
    canvasRef.current?.render(operations);
  }, []);

  const handleSDLScene = useCallback((sdl: SDLScene) => {
    const plan = compileScene(sdl, { width: 800, height: 600 });
    for (const step of plan.steps) {
      if (step.commands.length > 0) {
        canvasRef.current?.createSequence({ steps: step.commands as TeachingSequence["steps"] });
      }
    }
  }, []);

  const stepTimelinesRef = useRef<Map<string, { tl: gsap.core.Timeline; started: boolean }>>(new Map());

  const handleSDLStart = useCallback((sdl: SDLScene, sequenceId: string, _totalSteps: number) => {
    const plan = compileScene(sdl, { width: 800, height: 600 });
    stepTimelinesRef.current.clear();
    for (let i = 0; i < plan.steps.length; i++) {
      const step = plan.steps[i];
      if (step.commands.length > 0) {
        const tl = canvasRef.current?.createPausedSequence({
          steps: step.commands as TeachingSequence["steps"],
        });
        if (tl) {
          stepTimelinesRef.current.set(`${sequenceId}:${i}`, { tl, started: false });
        }
      }
    }
  }, []);

  const handleSDLStepAudioStart = useCallback((sequenceId: string, stepIndex: number) => {
    const key = `${sequenceId}:${stepIndex}`;
    const entry = stepTimelinesRef.current.get(key);
    if (entry && !entry.started) {
      entry.started = true;
      entry.tl.play();
    }
  }, []);

  const handleSDLStepComplete = useCallback((sequenceId: string, stepIndex: number, audioDurationMs: number) => {
    const key = `${sequenceId}:${stepIndex}`;
    const entry = stepTimelinesRef.current.get(key);
    if (entry && audioDurationMs > 0) {
      const elapsed = entry.tl.time();
      const animRemaining = entry.tl.duration() - elapsed;
      const audioTotalSec = audioDurationMs / 1000;
      if (animRemaining > 0 && audioTotalSec > 0) {
        const scale = animRemaining / audioTotalSec;
        if (scale > 1.0) {
          entry.tl.timeScale(Math.min(3.0, scale));
        }
      }
    }
    stepTimelinesRef.current.delete(key);
  }, []);

  const handleSDLComplete = useCallback((sequenceId: string) => {
    const keys = Array.from(stepTimelinesRef.current.keys());
    for (const key of keys) {
      if (key.startsWith(`${sequenceId}:`)) {
        const entry = stepTimelinesRef.current.get(key);
        if (entry && !entry.started) {
          entry.tl.play();
        }
        stepTimelinesRef.current.delete(key);
      }
    }
  }, []);

  // Kill any in-flight GSAP timelines on unmount to prevent memory leaks
  useEffect(() => {
    const stepTimelines = stepTimelinesRef.current;
    return () => {
      stepTimelines.forEach(({ tl }) => tl.kill());
      stepTimelines.clear();
    };
  }, []);

  const {
    messages: chatMessages,
    isLoading: chatLoading,
    sendMessage: sendChatMessage,
    clearChat,
  } = useChat({
    canvasMode: true,
    agentId,
    sessionId,
    onCanvasUpdate: handleCanvasUpdate,
    onSDLScene: handleSDLScene,
  });

  const handleTranscript = useCallback((event: TranscriptEvent) => {
    const time = new Date().toLocaleTimeString();
    const prefix = event.isFinal ? "[final]" : "[...]";
    setTranscripts((prev) => [...prev, `${time}  ${prefix} ${event.text}`]);
  }, []);

  const handleLLMResponse = useCallback((text: string) => {
    setTranscripts((prev) => [...prev, "", `Assistant: ${text}`, ""]);
  }, []);

  const handleLog = useCallback((message: string) => {
    const time = new Date().toISOString().slice(11, 19);
    setLogs((prev) => [...prev, `${time}  ${message}`]);
  }, []);

  const handleError = useCallback((message: string) => {
    handleLog(`Error: ${message}`);
  }, [handleLog]);

  const handleStateChange = useCallback((state: PipelineState) => {
    handleLog(`State -> ${state}`);
  }, [handleLog]);

  const handlePipelineMetrics = useCallback((metrics: Record<string, unknown>) => {
    const parts = [];
    if (metrics.latency_stt_ms) parts.push(`STT:${metrics.latency_stt_ms}ms`);
    if (metrics.latency_llm_ms) parts.push(`LLM:${metrics.latency_llm_ms}ms`);
    if (metrics.latency_tool_ms) parts.push(`Tool:${metrics.latency_tool_ms}ms`);
    if (metrics.latency_tts_ms) parts.push(`TTS:${metrics.latency_tts_ms}ms`);
    if (metrics.latency_total_ms) parts.push(`Total:${metrics.latency_total_ms}ms`);
    if (metrics.tts_interrupted) parts.push("(interrupted)");
    handleLog(`Pipeline: ${parts.join(" | ")}`);
  }, [handleLog]);

  const handleVoiceSessionReady = useCallback((nextSessionId: string) => {
    if (!nextSessionId) {
      return;
    }
    sessionIdRef.current = nextSessionId;
    setSessionId((current) => (current === nextSessionId ? current : nextSessionId));
  }, []);

  const ensureSessionId = useCallback(async (): Promise<string | null> => {
    if (sessionIdRef.current) {
      return sessionIdRef.current;
    }

    let sessionPromise = sessionInitPromiseRef.current;
    if (!sessionPromise) {
      sessionPromise = createSession(agentId);
      sessionInitPromiseRef.current = sessionPromise;
    }

    try {
      const session = await sessionPromise;
      sessionIdRef.current = session.id;
      setSessionId((current) => (current === session.id ? current : session.id));
      return session.id;
    } catch {
      return null;
    } finally {
      if (sessionInitPromiseRef.current === sessionPromise) {
        sessionInitPromiseRef.current = null;
      }
    }
  }, [agentId]);

  const {
    status,
    pipelineState,
    connect,
    disconnect,
    initAudio,
    isMicMuted,
    isTTSEnabled,
    toggleMicMute,
    toggleTTS,
  } = useWebRTC({
    agentId,
    sessionId: sessionId ?? undefined,
    onSessionReady: handleVoiceSessionReady,
    onTranscript: handleTranscript,
    onLLMResponse: handleLLMResponse,
    onCanvasUpdate: handleCanvasUpdate,
    onSDLScene: handleSDLScene,
    onSDLStart: handleSDLStart,
    onSDLStepAudioStart: handleSDLStepAudioStart,
    onSDLStepComplete: handleSDLStepComplete,
    onSDLComplete: handleSDLComplete,
    onPipelineMetrics: handlePipelineMetrics,
    onError: handleError,
    onLog: handleLog,
    onStateChange: handleStateChange,
  });

  const handleConnect = useCallback(async () => {
    initAudio();
    const ensuredSessionId = await ensureSessionId();
    connect({ agentId, sessionId: ensuredSessionId ?? undefined });
  }, [agentId, connect, ensureSessionId, initAudio]);

  const handleEndSession = useCallback(async () => {
    if (isEndingSession) return;
    setIsEndingSession(true);
    try {
      if (sessionIdRef.current) {
        const result = await endSession(sessionIdRef.current);
        setSessionEndResult(result);
        sessionIdRef.current = null;
        setSessionId(null);
      }
    } catch {
      setSessionEndResult({
        id: sessionIdRef.current ?? "",
        summary: null,
        mastery_count: 0,
        status: "ended",
      });
    } finally {
      disconnect();
      setIsEndingSession(false);
    }
  }, [disconnect, isEndingSession]);

  const isConnected = status === "connected";
  const isConnecting = status === "connecting";

  const voiceState: VoiceState = useMemo(() => {
    if (status === "error") return "error";
    if (status === "connecting") return "connecting";
    if (status === "idle" || status === "disconnected") return "idle";
    if (pipelineState === "listening") return "listening";
    if (pipelineState === "processing") return "processing";
    if (pipelineState === "speaking") return "speaking";
    return "listening";
  }, [status, pipelineState]);

  const latestTranscript = useMemo(() => {
    const nonEmpty = transcripts.filter(t => t.trim() !== "" && !t.startsWith("Assistant:"));
    return nonEmpty[nonEmpty.length - 1] || "";
  }, [transcripts]);

  const statusLabel = useMemo(() => {
    if (status === "idle") return "Ready";
    if (status === "connecting") return "Connecting...";
    if (status === "connected") return "Connected";
    if (status === "error") return "Error";
    return "Disconnected";
  }, [status]);

  // Loading state for agent
  if (agentLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-chalk-soft" />
      </div>
    );
  }

  if (agentError || !agent) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center px-6">
        <div className="glass-card rounded-2xl p-8 text-center max-w-sm">
          <p className="text-ember mb-4">{agentError || "Agent not found"}</p>
          <button
            onClick={() => router.push("/dashboard")}
            className="text-amber hover:underline font-medium text-sm"
          >
            Back to Dashboard
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen bg-background">
      {/* Top Navigation Bar */}
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-chalk-faint/30">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => router.push("/dashboard")}
              className="p-2 -ml-2 rounded-lg hover:bg-graphite transition-colors"
              aria-label="Back to dashboard"
            >
              <ArrowLeft className="h-5 w-5 text-muted-foreground" />
            </button>
            <span className="text-2xl">{agent.icon}</span>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">{agent.name}</h1>
              <p className="text-xs text-muted-foreground">
                {[agent.subject, agent.level].filter(Boolean).join(" / ")}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <ModeToggle
              mode={appMode}
              onChange={setAppMode}
              disabled={isConnected || isConnecting}
            />
            <ThemeToggle />
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="pt-24 min-h-screen flex gap-8 px-8 pb-4">
        {appMode === "voice" ? (
          <>
            {/* Voice Interaction Section */}
            <motion.section
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="w-2/5 min-w-[400px] flex flex-col items-center justify-center gap-8 md:gap-12 relative"
            >
              <BackgroundDoodles className="opacity-40" />
              <motion.div
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
              >
                <VoiceOrb
                  state={voiceState}
                  size="lg"
                  audioLevel={0.3}
                  onClick={isConnected ? handleEndSession : handleConnect}
                  disabled={isConnecting || isEndingSession}
                />
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4 }}
              >
                <StatusIndicator
                  status={status === "connected" ? "connected" : status === "connecting" ? "connecting" : status === "error" ? "error" : "idle"}
                  label={statusLabel}
                  pulse={status === "connected"}
                  showDot
                />
              </motion.div>

              {isConnected && (
                <ControlButtons
                  isMicMuted={isMicMuted}
                  isTTSEnabled={isTTSEnabled}
                  onToggleMic={toggleMicMute}
                  onToggleTTS={toggleTTS}
                  disabled={false}
                />
              )}

              {!isConnected && !isConnecting && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.5 }}
                >
                  <WaveformToSketch />
                </motion.div>
              )}

              {!isConnected && !isConnecting && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.6 }}
                >
                  <FloatingButton
                    variant="primary"
                    size="lg"
                    onClick={handleConnect}
                    icon={<Mic className="h-5 w-5" />}
                  >
                    Start talking to {agent.name}
                  </FloatingButton>
                </motion.div>
              )}

              {isConnected && latestTranscript && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.8 }}
                  className="w-full max-w-md"
                >
                  <GlassmorphicCard variant="elevated" padding="lg" className="text-center">
                    <p className="text-base md:text-lg text-foreground/90 leading-relaxed">
                      {latestTranscript.replace(/^\d{2}:\d{2}:\d{2}\s+\[.*?\]\s+/, "")}
                    </p>
                  </GlassmorphicCard>
                </motion.div>
              )}

              {isConnected && transcripts.length === 0 && (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 1 }}
                  className="text-sm text-muted-foreground text-center"
                >
                  Try asking {agent.name} about {agent.subject || "a topic"}
                </motion.p>
              )}
            </motion.section>

            {/* Canvas */}
            <motion.section
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.3 }}
              className="flex-1 flex items-center justify-center"
            >
              <GlassmorphicCard variant="elevated" shadow="lg" padding="xl" className="w-full h-full max-w-[900px]">
                <SVGCanvas ref={canvasRef} width={800} height={600} className="w-full h-full" />
              </GlassmorphicCard>
            </motion.section>
          </>
        ) : (
          <>
            <motion.section
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              className="w-2/5 min-w-[400px] flex flex-col h-[calc(100vh-7rem)]"
            >
              <div className="glass-card rounded-2xl overflow-hidden flex flex-col h-full">
                <ChatInterface
                  messages={chatMessages}
                  isLoading={chatLoading}
                  onSendMessage={sendChatMessage}
                  onClearChat={clearChat}
                />
              </div>
            </motion.section>

            <motion.section
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.2 }}
              className="flex-1 flex items-center justify-center"
            >
              <GlassmorphicCard variant="elevated" shadow="lg" padding="xl" className="w-full h-full max-w-[900px]">
                <SVGCanvas ref={canvasRef} width={800} height={600} className="w-full h-full" />
              </GlassmorphicCard>
            </motion.section>
          </>
        )}
      </main>

      {/* Technical Drawer Toggle */}
      {appMode === "voice" && !drawerOpen && (
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1.2 }}
          onClick={() => setDrawerOpen(true)}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-20 glass-card px-6 py-3 rounded-full text-sm font-medium hover:bg-graphite transition-all flex items-center gap-2"
        >
          <span>View Metrics & Logs</span>
          <ChevronUp className="h-4 w-4" />
        </motion.button>
      )}

      {appMode === "voice" && (
        <TechnicalDrawer
          isOpen={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          transcripts={transcripts}
          logs={logs}
          pipelineState={pipelineState}
        />
      )}

      {sessionEndResult && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="fixed inset-0 z-40 flex items-center justify-center bg-background/70 px-6 backdrop-blur-sm"
        >
          <motion.div
            initial={{ y: 18, scale: 0.98 }}
            animate={{ y: 0, scale: 1 }}
            transition={{ type: "spring", stiffness: 220, damping: 24 }}
            className="w-full max-w-xl"
          >
            <GlassmorphicCard variant="elevated" shadow="lg" padding="xl" className="space-y-5 border border-chalk-faint/40">
              <div className="space-y-2">
                <p className="text-xs uppercase tracking-[0.24em] text-muted-foreground">Session complete</p>
                <h2 className="text-2xl font-semibold tracking-tight">Summary for {agent.name}</h2>
              </div>
              <div className="rounded-2xl bg-graphite/40 p-5 text-sm leading-relaxed text-foreground/90">
                {sessionEndResult.summary || "No summary was generated for this session."}
              </div>
              {sessionEndResult.summary && sessionEndResult.mastery_count > 0 && (
                <p className="text-xs text-muted-foreground">
                  {sessionEndResult.mastery_count} learning signal{sessionEndResult.mastery_count === 1 ? "" : "s"} saved for future tutoring.
                </p>
              )}
              <div className="flex justify-end">
                <button
                  onClick={() => router.push("/dashboard")}
                  className="rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background transition-opacity hover:opacity-90"
                >
                  Back to dashboard
                </button>
              </div>
            </GlassmorphicCard>
          </motion.div>
        </motion.div>
      )}
    </div>
  );
}
