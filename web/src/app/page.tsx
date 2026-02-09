"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import { Mic, Settings, ChevronUp } from "lucide-react";
// Switch removed - canvas is always enabled
import { SVGCanvas, type SVGCanvasHandle, type AnimationOperation, type LatexOperation, type TeachingSequence } from "@/components/svg-canvas";
import { useWebRTC, type TranscriptEvent, type CanvasOperation, type PipelineState } from "@/hooks/use-webrtc";
import { useChat } from "@/hooks/use-chat";
import { cn } from "@/lib/utils";
import { VoiceOrb, type VoiceState } from "@/components/voice-orb";
import { StatusIndicator } from "@/components/status-indicator";
import { FloatingButton } from "@/components/ui/floating-button";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import { TechnicalDrawer } from "@/components/technical-drawer";
import { ControlButtons } from "@/components/control-buttons";
import { ModeToggle, type AppMode } from "@/components/mode-toggle";
import { ChatInterface } from "@/components/chat-interface";

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

  const handleAnimation = useCallback((animation: AnimationOperation) => {
    canvasRef.current?.animate(animation);
  }, []);

  const handleLatex = useCallback((latex: LatexOperation) => {
    canvasRef.current?.renderLatex(latex);
  }, []);

  const handleTeachingSequence = useCallback((sequence: TeachingSequence) => {
    canvasRef.current?.createSequence(sequence);
  }, []);

  // Dispatch animation events from SSE to the correct SVGCanvas method
  const handleAnimationEvent = useCallback((data: any) => {
    const tool = data.tool;
    if (tool === "animate_element" && data.animation_command) {
      handleAnimation(data.animation_command);
    } else if (tool === "render_latex" && data.element) {
      handleLatex(data.element);
    } else if (tool === "create_teaching_sequence" && data.timeline) {
      handleTeachingSequence(data.timeline);
    } else if (tool === "plot_function" && data.graph) {
      // Plot function data goes to render as canvas operations
      canvasRef.current?.render([data.graph]);
    }
  }, [handleAnimation, handleLatex, handleTeachingSequence]);

  // Chat mode hook
  const {
    messages: chatMessages,
    isLoading: chatLoading,
    sendMessage: sendChatMessage,
    clearChat,
  } = useChat({
    canvasMode: true,
    onCanvasUpdate: handleCanvasUpdate,
    onAnimationEvent: handleAnimationEvent,
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
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-white/10">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Mic className="h-6 w-6 text-primary" />
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Voice AI</h1>
              <p className="text-xs text-muted-foreground">Real-time assistant</p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <ModeToggle
              mode={appMode}
              onChange={setAppMode}
              disabled={isConnected || isConnecting}
            />
            <button
              className="p-2 rounded-lg hover:bg-white/10 transition-colors"
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
              className="w-2/5 min-w-[400px] flex flex-col items-center justify-center gap-8 md:gap-12"
            >
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
                    Connect to Voice AI
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
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-20 glass-card px-6 py-3 rounded-full text-sm font-medium hover:bg-white/10 transition-all flex items-center gap-2 shadow-glass"
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
