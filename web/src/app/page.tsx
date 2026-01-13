"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { CanvasRenderer, type CanvasRendererHandle } from "@/components/canvas-renderer";
import { useWebRTC, type TranscriptEvent, type CanvasOperation, type LatencyMetrics } from "@/hooks/use-webrtc";
import { useChat } from "@/hooks/use-chat";
import { Paintbrush, Mic, MessageSquare, Trash2, Activity } from "lucide-react";
import { cn } from "@/lib/utils";

export default function Home() {
  const [canvasMode, setCanvasMode] = useState(false);
  const [activeTab, setActiveTab] = useState<"voice" | "chat">("voice");
  const [transcripts, setTranscripts] = useState<string[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [isInterrupted, setIsInterrupted] = useState(false);
  const [latencyMetrics, setLatencyMetrics] = useState<LatencyMetrics | null>(null);

  const canvasRef = useRef<CanvasRendererHandle>(null);
  const transcriptsEndRef = useRef<HTMLDivElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const handleCanvasUpdate = useCallback((operations: CanvasOperation[]) => {
    canvasRef.current?.render(operations);
  }, []);

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

  const handleInterruption = useCallback((message: string) => {
    setIsInterrupted(true);
    handleLog(`Interruption: ${message}`);
  }, [handleLog]);

  const handleTTSCancelled = useCallback((chunksSent: number) => {
    handleLog(`TTS cancelled after ${chunksSent} chunks`);
    // Clear interruption flag after a brief moment
    setTimeout(() => setIsInterrupted(false), 1500);
  }, [handleLog]);

  const handleLatencyUpdate = useCallback((metrics: LatencyMetrics) => {
    setLatencyMetrics(metrics);
    handleLog(`Pipeline: VAD=${metrics.vadLatency?.toFixed(1) || '-'}ms, STT=${metrics.sttFinalTranscript?.toFixed(0)}ms, LLM=${metrics.llmComplete?.toFixed(0)}ms, TTS=${metrics.ttsComplete?.toFixed(0)}ms, Total=${metrics.totalPipeline?.toFixed(0)}ms`);
  }, [handleLog]);

  const { status, connect, disconnect, initAudio } = useWebRTC({
    canvasMode,
    onTranscript: handleTranscript,
    onLLMResponse: handleLLMResponse,
    onCanvasUpdate: handleCanvasUpdate,
    onError: handleError,
    onLog: handleLog,
    onInterruptionDetected: handleInterruption,
    onTTSCancelled: handleTTSCancelled,
    onLatencyUpdate: handleLatencyUpdate,
  });

  const { messages, isLoading, sendMessage, clearChat } = useChat({
    canvasMode,
    onCanvasUpdate: handleCanvasUpdate,
  });

  // Auto-scroll effects
  useEffect(() => {
    transcriptsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcripts]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleConnect = useCallback(() => {
    initAudio();
    connect();
  }, [initAudio, connect]);

  const handleSendMessage = useCallback(async () => {
    if (!chatInput.trim() || isLoading) return;
    const text = chatInput;
    setChatInput("");
    await sendMessage(text);
  }, [chatInput, isLoading, sendMessage]);

  const handleClearChat = useCallback(async () => {
    await clearChat();
    canvasRef.current?.clear();
  }, [clearChat]);

  const handleClearCanvas = useCallback(() => {
    canvasRef.current?.clear();
  }, []);

  const isConnected = status === "connected";
  const isConnecting = status === "connecting";

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <div
        className={cn(
          "flex flex-col border-r border-border bg-card transition-all",
          canvasMode ? "w-[400px] min-w-[360px]" : "w-full max-w-[680px] mx-auto border-none"
        )}
      >
        {/* Brand Header */}
        <div className="border-b border-border p-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h1 className="text-[15px] font-semibold tracking-tight">Voice AI</h1>
              <p className="mt-0.5 text-xs text-muted-foreground">Real-time assistant</p>
            </div>
            <div className="flex items-center gap-2.5">
              <span className="text-xs font-medium text-muted-foreground">Canvas</span>
              <Switch checked={canvasMode} onCheckedChange={setCanvasMode} />
            </div>
          </div>

          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "voice" | "chat")}>
            <TabsList>
              <TabsTrigger value="voice">
                <Mic className="mr-2 h-3.5 w-3.5" />
                Voice
              </TabsTrigger>
              <TabsTrigger value="chat">
                <MessageSquare className="mr-2 h-3.5 w-3.5" />
                Chat
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Voice Panel */}
          {activeTab === "voice" && (
            <div className="space-y-6">
              {/* Controls */}
              <div className="flex items-center gap-2.5">
                <Button
                  onClick={handleConnect}
                  disabled={isConnected || isConnecting}
                >
                  Connect
                </Button>
                <Button
                  variant="outline"
                  onClick={disconnect}
                  disabled={!isConnected && !isConnecting}
                >
                  Disconnect
                </Button>
                <div className="ml-auto flex items-center gap-1.5">
                  <span
                    className={cn(
                      "h-[7px] w-[7px] rounded-full",
                      isConnected
                        ? "bg-green-500 shadow-[0_0_8px] shadow-green-500"
                        : "bg-muted-foreground"
                    )}
                  />
                  <span
                    className={cn(
                      "text-xs",
                      isConnected ? "text-green-500" : "text-muted-foreground"
                    )}
                  >
                    {status === "idle"
                      ? "Idle"
                      : status === "connecting"
                      ? "Connecting..."
                      : status === "connected"
                      ? "Connected"
                      : status === "error"
                      ? "Error"
                      : "Disconnected"}
                  </span>
                </div>
              </div>

              {/* Interruption Indicator */}
              {isInterrupted && isConnected && (
                <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/10 px-4 py-3 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-yellow-500 opacity-75"></span>
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-yellow-500"></span>
                    </span>
                    <span className="font-medium text-yellow-600">Listening to you...</span>
                  </div>
                </div>
              )}

              {/* Transcripts */}
              <div>
                <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Transcripts
                </h3>
                <div className="max-h-40 overflow-y-auto rounded-lg border border-border bg-background p-3.5 font-mono text-xs leading-relaxed text-muted-foreground">
                  {transcripts.length === 0 ? (
                    <span className="text-muted-foreground/50">Waiting for audio...</span>
                  ) : (
                    transcripts.map((line, i) => (
                      <div key={i} className="whitespace-pre-wrap">
                        {line}
                      </div>
                    ))
                  )}
                  <div ref={transcriptsEndRef} />
                </div>
              </div>

              {/* Latency Metrics - Always visible */}
              <div>
                <h3 className="mb-2.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  <Activity className="h-3.5 w-3.5" />
                  Latency Metrics
                </h3>
                <div className="grid grid-cols-5 gap-2 rounded-lg border border-border bg-background p-3">
                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">VAD</div>
                    <div className={cn(
                      "text-sm font-mono font-semibold",
                      latencyMetrics?.vadLatency !== undefined
                        ? latencyMetrics.vadLatency < 5 ? "text-green-500" : "text-yellow-500"
                        : "text-muted-foreground/50"
                    )}>
                      {latencyMetrics?.vadLatency?.toFixed(1) || '-'}ms
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">STT</div>
                    <div className="text-sm font-mono font-semibold">
                      {latencyMetrics?.sttFinalTranscript?.toFixed(0) || '-'}ms
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">LLM</div>
                    <div className="text-sm font-mono font-semibold">
                      {latencyMetrics?.llmComplete?.toFixed(0) || '-'}ms
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">TTS</div>
                    <div className="text-sm font-mono font-semibold">
                      {latencyMetrics?.ttsComplete?.toFixed(0) || '-'}ms
                    </div>
                  </div>
                  <div className="space-y-1">
                    <div className="text-[10px] text-muted-foreground">Total</div>
                    <div className={cn(
                      "text-sm font-mono font-semibold",
                      latencyMetrics?.totalPipeline !== undefined
                        ? (latencyMetrics.totalPipeline || 0) < 2000 ? "text-green-500" :
                          (latencyMetrics.totalPipeline || 0) < 4000 ? "text-yellow-500" :
                          "text-red-500"
                        : "text-muted-foreground/50"
                    )}>
                      {latencyMetrics?.totalPipeline?.toFixed(0) || '-'}ms
                    </div>
                  </div>
                </div>
              </div>

              {/* Logs */}
              <div>
                <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  System Log
                </h3>
                <div className="max-h-40 overflow-y-auto rounded-lg border border-border bg-background p-3.5 font-mono text-xs leading-relaxed text-muted-foreground">
                  {logs.length === 0 ? (
                    <span className="text-muted-foreground/50">No logs yet...</span>
                  ) : (
                    logs.map((line, i) => (
                      <div key={i} className="whitespace-pre-wrap">
                        {line}
                      </div>
                    ))
                  )}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </div>
          )}

          {/* Chat Panel */}
          {activeTab === "chat" && (
            <div className="flex h-full flex-col">
              {/* Messages */}
              <div className="mb-4 min-h-[280px] max-h-[360px] flex-1 overflow-y-auto rounded-xl border border-border bg-background p-4">
                {messages.length === 0 ? (
                  <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                    Send a message to begin
                  </div>
                ) : (
                  <div className="space-y-3">
                    {messages.map((msg) => (
                      <div
                        key={msg.id}
                        className={cn(
                          "rounded-lg px-4 py-3 text-sm leading-relaxed",
                          msg.role === "user"
                            ? "ml-8 rounded-br-sm bg-primary/10 text-primary"
                            : "mr-8 rounded-bl-sm bg-secondary text-foreground"
                        )}
                      >
                        {msg.content}
                      </div>
                    ))}
                    {isLoading && (
                      <div className="flex items-center gap-2.5 py-3 text-sm text-muted-foreground">
                        <div className="flex gap-1">
                          <span className="loading-dot h-1.5 w-1.5 rounded-full bg-primary" />
                          <span className="loading-dot h-1.5 w-1.5 rounded-full bg-primary" />
                          <span className="loading-dot h-1.5 w-1.5 rounded-full bg-primary" />
                        </div>
                        <span>Thinking...</span>
                      </div>
                    )}
                    <div ref={messagesEndRef} />
                  </div>
                )}
              </div>

              {/* Input */}
              <div className="flex gap-2.5">
                <Input
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSendMessage();
                    }
                  }}
                  placeholder="Type your message..."
                  disabled={isLoading}
                />
                <Button onClick={handleSendMessage} disabled={isLoading || !chatInput.trim()}>
                  Send
                </Button>
              </div>

              <button
                onClick={handleClearChat}
                className="mt-2.5 text-left text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Clear conversation
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Canvas Area */}
      {canvasMode && (
        <div className="flex flex-1 flex-col bg-background">
          {/* Canvas Header */}
          <div className="flex items-center justify-between border-b border-border bg-card px-6 py-4">
            <h2 className="text-sm font-medium text-muted-foreground">Canvas</h2>
            <Button variant="outline" size="sm" onClick={handleClearCanvas}>
              <Trash2 className="mr-2 h-3.5 w-3.5" />
              Clear
            </Button>
          </div>

          {/* Canvas Stage */}
          <div className="relative flex flex-1 items-center justify-center p-8">
            <CanvasRenderer
              ref={canvasRef}
              width={800}
              height={600}
              className="rounded-lg bg-white shadow-2xl"
            />

            {/* Placeholder */}
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-muted-foreground">
              <Paintbrush className="mb-3 h-10 w-10 opacity-40" />
              <p className="text-sm">AI will draw here</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

