"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import { Mic, Settings, ChevronUp, BarChart3 } from "lucide-react";
import Link from "next/link";
// Switch removed - canvas is always enabled
import { gsap } from "gsap";
import { SVGCanvas, type SVGCanvasHandle, type TeachingSequence } from "@/components/svg-canvas";
import { useWebRTC, type TranscriptEvent, type CanvasOperation, type PipelineState } from "@/hooks/use-webrtc";
import { useChat } from "@/hooks/use-chat";
import { compileScene, type SDLScene, type RenderPlan } from "@/lib/scene-kit";
import { cn } from "@/lib/utils";
import { VoiceOrb, type VoiceState } from "@/components/voice-orb";
import { StatusIndicator } from "@/components/status-indicator";
import { FloatingButton } from "@/components/ui/floating-button";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import { TechnicalDrawer } from "@/components/technical-drawer";
import { ControlButtons } from "@/components/control-buttons";
import { ModeToggle, type AppMode } from "@/components/mode-toggle";
import { ThemeToggle } from "@/components/theme-toggle";
import { ChatInterface } from "@/components/chat-interface";
import { MurmurLogoMark, WaveformToSketch, BackgroundDoodles } from "@/components/murmur-doodles";

export default function Home() {
  const [appMode, setAppMode] = useState<AppMode>("voice");
  // Canvas is always enabled for math tutor
  const [transcripts, setTranscripts] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const canvasRef = useRef<SVGCanvasHandle>(null);

  const handleCanvasUpdate = useCallback((operations: CanvasOperation[]) => {
    canvasRef.current?.render(operations);
  }, []);

  // SDL scene handler — compiles SDL to render commands and feeds to SVGCanvas (chat mode / legacy)
  const handleSDLScene = useCallback((sdl: SDLScene) => {
    const plan = compileScene(sdl, { width: 800, height: 600 });
    for (const step of plan.steps) {
      if (step.commands.length > 0) {
        canvasRef.current?.createSequence({ steps: step.commands as TeachingSequence["steps"] });
      }
    }
  }, []);

  // ── Step-pipelined voice+visual sync ──
  // Tracks per-step paused timelines keyed by "sequenceId:stepIndex"
  const stepTimelinesRef = useRef<Map<string, { tl: gsap.core.Timeline; started: boolean }>>(new Map());

  const handleSDLStart = useCallback((sdl: SDLScene, sequenceId: string, totalSteps: number) => {
    // Compile entire scene once (preserves cross-step layout positioning)
    const plan = compileScene(sdl, { width: 800, height: 600 });

    // Create a paused timeline for each step
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
      // Speed up animation if it's running slower than the audio
      const elapsed = entry.tl.time();
      const animRemaining = entry.tl.duration() - elapsed;
      const audioTotalSec = audioDurationMs / 1000;

      if (animRemaining > 0 && audioTotalSec > 0) {
        const scale = animRemaining / audioTotalSec;
        // Only speed up (don't slow down animations that are already faster than audio)
        if (scale > 1.0) {
          entry.tl.timeScale(Math.min(3.0, scale));
        }
      }
    }
    stepTimelinesRef.current.delete(key);
  }, []);

  const handleSDLComplete = useCallback((sequenceId: string) => {
    // Kill all timelines for this sequence (cleanup on completion or interruption)
    const keys = Array.from(stepTimelinesRef.current.keys());
    for (const key of keys) {
      if (key.startsWith(`${sequenceId}:`)) {
        const entry = stepTimelinesRef.current.get(key);
        if (entry) {
          if (!entry.started) {
            entry.tl.play(); // Play any un-started steps on normal completion
          }
        }
        stepTimelinesRef.current.delete(key);
      }
    }
  }, []);

  // Chat mode hook
  const {
    messages: chatMessages,
    isLoading: chatLoading,
    sendMessage: sendChatMessage,
    clearChat,
  } = useChat({
    canvasMode: true,
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
    handleLog(`State → ${state}`);
  }, [handleLog]);

  const handlePipelineMetrics = useCallback((metrics: Record<string, any>) => {
    const parts = [];
    if (metrics.latency_stt_ms) parts.push(`STT:${metrics.latency_stt_ms}ms`);
    if (metrics.latency_llm_ms) parts.push(`LLM:${metrics.latency_llm_ms}ms`);
    if (metrics.latency_tool_ms) parts.push(`Tool:${metrics.latency_tool_ms}ms`);
    if (metrics.latency_tts_ms) parts.push(`TTS:${metrics.latency_tts_ms}ms`);
    if (metrics.latency_total_ms) parts.push(`Total:${metrics.latency_total_ms}ms`);
    if (metrics.tts_interrupted) parts.push("(interrupted)");
    handleLog(`Pipeline: ${parts.join(" | ")}`);
  }, [handleLog]);

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

  const handleConnect = useCallback(() => {
    initAudio();
    connect();
  }, [initAudio, connect]);

  const isConnected = status === "connected";
  const isConnecting = status === "connecting";

  // Map pipeline state to voice orb state
  const voiceState: VoiceState = useMemo(() => {
    if (status === "error") return "error";
    if (status === "connecting") return "connecting";
    if (status === "idle" || status === "disconnected") return "idle";
    // Map pipeline states directly
    if (pipelineState === "listening") return "listening";
    if (pipelineState === "processing") return "processing";
    if (pipelineState === "speaking") return "speaking";
    return "listening";
  }, [status, pipelineState]);

  // Get latest non-empty transcript
  const latestTranscript = useMemo(() => {
    const nonEmpty = transcripts.filter(t => t.trim() !== "" && !t.startsWith("Assistant:"));
    return nonEmpty[nonEmpty.length - 1] || "";
  }, [transcripts]);

  // Status label
  const statusLabel = useMemo(() => {
    if (status === "idle") return "Ready";
    if (status === "connecting") return "Connecting...";
    if (status === "connected") return "Connected";
    if (status === "error") return "Error";
    return "Disconnected";
  }, [status]);

  return (
    <div className="relative min-h-screen bg-background">
      {/* Top Navigation Bar */}
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-chalk-faint/30">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <MurmurLogoMark />
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Murmur</h1>
              <p className="text-xs text-muted-foreground">Real-time assistant</p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <ModeToggle
              mode={appMode}
              onChange={setAppMode}
              disabled={isConnected || isConnecting}
            />
            <Link
              href="/dashboard"
              className="p-2 rounded-lg hover:bg-graphite transition-colors"
              aria-label="Dashboard"
            >
              <BarChart3 className="h-5 w-5 text-muted-foreground" />
            </Link>
            <ThemeToggle />
            <button
              className="p-2 rounded-lg hover:bg-graphite transition-colors"
              aria-label="Settings"
            >
              <Settings className="h-5 w-5 text-muted-foreground" />
            </button>
          </div>
        </div>
      </nav>

      {/* Main Content Area - Always Split Screen */}
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
              {/* Background math doodles */}
              <BackgroundDoodles className="opacity-40" />
              {/* Voice Orb */}
              <motion.div
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
              >
                <VoiceOrb
                  state={voiceState}
                  size="lg"
                  audioLevel={0.3}
                  onClick={isConnected ? disconnect : handleConnect}
                  disabled={isConnecting}
                />
              </motion.div>

              {/* Status Badge */}
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

              {/* Control Buttons (when connected) */}
              {isConnected && (
                <ControlButtons
                  isMicMuted={isMicMuted}
                  isTTSEnabled={isTTSEnabled}
                  onToggleMic={toggleMicMute}
                  onToggleTTS={toggleTTS}
                  disabled={false}
                />
              )}

              {/* Waveform-to-sketch illustration (idle state) */}
              {!isConnected && !isConnecting && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.5 }}
                >
                  <WaveformToSketch />
                </motion.div>
              )}

              {/* Connection Button (if not connected) */}
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
                    Connect to Murmur
                  </FloatingButton>
                </motion.div>
              )}

              {/* Latest Transcript Preview */}
              {isConnected && latestTranscript && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.8 }}
                  className="w-full max-w-md"
                >
                  <GlassmorphicCard
                    variant="elevated"
                    padding="lg"
                    className="text-center"
                  >
                    <p className="text-base md:text-lg text-foreground/90 leading-relaxed">
                      {latestTranscript.replace(/^\d{2}:\d{2}:\d{2}\s+\[.*?\]\s+/, "")}
                    </p>
                  </GlassmorphicCard>
                </motion.div>
              )}

              {/* Prompt Suggestion */}
              {isConnected && transcripts.length === 0 && (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 1 }}
                  className="text-sm text-muted-foreground text-center"
                >
                  Try saying: "Tell me about your capabilities"
                </motion.p>
              )}
            </motion.section>

            {/* Math Whiteboard (Always Visible) */}
            <motion.section
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.3 }}
              className="flex-1 flex items-center justify-center"
            >
              <GlassmorphicCard variant="elevated" shadow="lg" padding="xl" className="w-full h-full max-w-[900px]">
                <SVGCanvas
                  ref={canvasRef}
                  width={800}
                  height={600}
                  className="w-full h-full"
                />
              </GlassmorphicCard>
            </motion.section>
          </>
        ) : (
          /* Chat Mode - Always Split Screen */
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

            {/* Math Whiteboard for Chat Mode */}
            <motion.section
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.2 }}
              className="flex-1 flex items-center justify-center"
            >
              <GlassmorphicCard variant="elevated" shadow="lg" padding="xl" className="w-full h-full max-w-[900px]">
                <SVGCanvas
                  ref={canvasRef}
                  width={800}
                  height={600}
                  className="w-full h-full"
                />
              </GlassmorphicCard>
            </motion.section>
          </>
        )}
      </main>

      {/* Technical Drawer Toggle (Bottom Fixed) - Voice Mode Only */}
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

      {/* Technical Drawer - Voice Mode Only */}
      {appMode === "voice" && (
        <TechnicalDrawer
          isOpen={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          transcripts={transcripts}
          logs={logs}
          pipelineState={pipelineState}
        />
      )}
    </div>
  );
}
