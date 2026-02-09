"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { motion } from "framer-motion";
import { Mic, Settings, ChevronUp, Trash2 } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { ManimCanvas, type ManimCanvasHandle } from "@/components/manim-canvas";
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
  const [canvasMode, setCanvasMode] = useState(false);
  const [transcripts, setTranscripts] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const canvasRef = useRef<ManimCanvasHandle>(null);

  const handleCanvasUpdate = useCallback((operations: CanvasOperation[]) => {
    // Legacy canvas_update support (kept for backwards compat)
  }, []);

  const handleManimCommand = useCallback((command: any) => {
    canvasRef.current?.processCommand(command);
  }, []);

  const handleClearCanvas = useCallback(() => {
    canvasRef.current?.clear();
  }, []);

  // Chat mode hook
  const {
    messages: chatMessages,
    isLoading: chatLoading,
    sendMessage: sendChatMessage,
    clearChat,
  } = useChat({
    canvasMode,
    onCanvasUpdate: handleCanvasUpdate,
    onManimCommand: handleManimCommand,
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
    canvasMode,
    onTranscript: handleTranscript,
    onLLMResponse: handleLLMResponse,
    onCanvasUpdate: handleCanvasUpdate,
    onManimCommand: handleManimCommand,
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
    <div className="relative min-h-screen bg-background overflow-x-hidden">
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
            <div className="flex items-center gap-2.5">
              <span className="text-sm font-medium text-muted-foreground">Canvas</span>
              <Switch checked={canvasMode} onCheckedChange={setCanvasMode} />
            </div>
            <button
              className="p-2 rounded-lg hover:bg-white/10 transition-colors"
              aria-label="Settings"
            >
              <Settings className="h-5 w-5 text-muted-foreground" />
            </button>
          </div>
        </div>
      </nav>

      {/* Main Content Area */}
      <main
        className={cn(
          "pt-24 min-h-screen flex",
          appMode === "voice" ? "pb-32 items-center justify-center" : "pb-4",
          canvasMode && appMode === "voice" && "gap-8 px-8"
        )}
      >
        {appMode === "voice" ? (
          <>
            {/* Voice Interaction Hero Section */}
            <motion.section
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className={cn(
                "flex flex-col items-center gap-8 md:gap-12 px-6",
                canvasMode ? "w-1/4 min-w-[300px] max-w-[380px]" : "w-full max-w-2xl mx-auto"
              )}
            >
              {/* Voice Orb */}
              <motion.div
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
              >
                <VoiceOrb
                  state={voiceState}
                  size={canvasMode ? "md" : "xl"}
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

              {/* Demo Prompts */}
              {isConnected && transcripts.length === 0 && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 1 }}
                  className="w-full max-w-lg"
                >
                  <p className="text-sm text-muted-foreground text-center mb-4">
                    {canvasMode ? "Try these visual demos:" : "Try saying:"}
                  </p>
                  {canvasMode ? (
                    <div className="grid grid-cols-1 gap-2">
                      {[
                        "Explain the Pythagorean theorem with a diagram",
                        "Show me how derivatives work visually",
                        "Visualize the relationship between sine and cosine",
                        "Explain Euler's formula with animations",
                      ].map((prompt, i) => (
                        <button
                          key={i}
                          className="glass-card px-4 py-3 rounded-xl text-sm text-left hover:bg-white/10 transition-colors"
                        >
                          "{prompt}"
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="text-center text-muted-foreground">
                      "Tell me about your capabilities"
                    </p>
                  )}
                </motion.div>
              )}
            </motion.section>

            {/* Canvas Section (if enabled) */}
            {canvasMode && (
              <motion.section
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.3 }}
                className="flex-1 flex flex-col items-center justify-center gap-4"
              >
                {/* Canvas Toolbar */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleClearCanvas}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg glass-card hover:bg-white/10 hover:text-destructive transition-colors text-sm"
                    title="Clear canvas"
                  >
                    <Trash2 className="h-4 w-4" />
                    <span>Clear</span>
                  </button>
                </div>

                <GlassmorphicCard variant="elevated" shadow="lg" padding="sm" className="w-full">
                  <div className="relative w-full" style={{ aspectRatio: "16/9", maxHeight: "80vh" }}>
                    <ManimCanvas
                      ref={canvasRef}
                      width={1920}
                      height={1080}
                      backgroundColor="#1a1a2e"
                      className="w-full h-full"
                    />
                    {/* Canvas Placeholder - hidden when content exists */}
                    <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-muted-foreground opacity-0 transition-opacity duration-300" id="canvas-placeholder">
                      <Mic className="mb-3 h-10 w-10 opacity-20" />
                      <p className="text-sm opacity-50">Ask me to explain something</p>
                      <p className="text-xs opacity-30 mt-1">I'll animate while I talk</p>
                    </div>
                  </div>
                </GlassmorphicCard>
              </motion.section>
            )}
          </>
        ) : (
          /* Chat Mode */
          <motion.section
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className={cn(
              "flex-1 w-full flex",
              canvasMode ? "gap-6 px-6 max-w-7xl mx-auto" : "justify-center px-4"
            )}
          >
            <div className={cn(
              "glass-card rounded-2xl overflow-hidden flex flex-col",
              canvasMode ? "w-1/4 min-w-[300px] max-w-[380px] h-[calc(100vh-7rem)]" : "w-full max-w-3xl h-[calc(100vh-7rem)]"
            )}>
              <ChatInterface
                messages={chatMessages}
                isLoading={chatLoading}
                onSendMessage={sendChatMessage}
                onClearChat={clearChat}
              />
            </div>

            {/* Canvas Section for Chat Mode */}
            {canvasMode && (
              <div className="flex-1 flex flex-col items-center justify-center gap-4">
                {/* Canvas Toolbar */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleClearCanvas}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg glass-card hover:bg-white/10 hover:text-destructive transition-colors text-sm"
                    title="Clear canvas"
                  >
                    <Trash2 className="h-4 w-4" />
                    <span>Clear</span>
                  </button>
                </div>

                <GlassmorphicCard variant="elevated" shadow="lg" padding="sm" className="w-full">
                  <div className="relative w-full" style={{ aspectRatio: "16/9", maxHeight: "80vh" }}>
                    <ManimCanvas
                      ref={canvasRef}
                      width={1920}
                      height={1080}
                      backgroundColor="#1a1a2e"
                      className="w-full h-full"
                    />
                    <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-muted-foreground opacity-0 transition-opacity duration-300">
                      <Mic className="mb-3 h-10 w-10 opacity-20" />
                      <p className="text-sm opacity-50">Ask me to explain something</p>
                      <p className="text-xs opacity-30 mt-1">I'll animate while I talk</p>
                    </div>
                  </div>
                </GlassmorphicCard>
              </div>
            )}
          </motion.section>
        )}
      </main>

      {/* Technical Drawer Toggle (Bottom Fixed) - Voice Mode Only */}
      {appMode === "voice" && !drawerOpen && (
        <div className="fixed bottom-6 inset-x-0 z-20 flex justify-center pointer-events-none">
          <motion.button
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 1.2 }}
            onClick={() => setDrawerOpen(true)}
            className="pointer-events-auto glass-card px-6 py-3 rounded-full text-sm font-medium hover:bg-white/10 transition-all flex items-center gap-2 shadow-glass"
          >
            <span>View Metrics & Logs</span>
            <ChevronUp className="h-4 w-4" />
          </motion.button>
        </div>
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
