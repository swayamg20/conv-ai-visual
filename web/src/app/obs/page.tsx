"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import {
  BarChart3,
  Clock,
  AlertTriangle,
  Activity,
  ChevronDown,
  ChevronRight,
  ArrowLeft,
  RefreshCw,
  Wrench,
  Copy,
  Check,
  Mic,
  MessageSquare,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";
import { ThemeToggle } from "@/components/theme-toggle";

interface VoiceLogEntry {
  id: number;
  session_id: string;
  user_id: string | null;
  mode: string;
  user_message: string;
  response_text: string | null;
  llm_provider: string | null;
  llm_model: string | null;
  latency_vad_ms: number | null;
  latency_stt_ms: number | null;
  latency_turn_detection_ms: number | null;
  latency_stt_to_llm_ms: number | null;
  latency_llm_ms: number | null;
  latency_llm_first_token_ms: number | null;
  latency_tool_ms: number | null;
  latency_tts_ms: number | null;
  latency_tts_first_chunk_ms: number | null;
  latency_total_ms: number | null;
  tool_calls: Array<{ name: string; arguments: any }>;
  tokens_in: number | null;
  tokens_out: number | null;
  tts_chunks_sent: number | null;
  tts_interrupted: boolean;
  tts_provider_used: string | null;
  tts_retry_count: number;
  tts_fallback_used: boolean;
  tts_fallback_provider: string | null;
  smart_turn_used: boolean;
  smart_turn_result: string | null;
  error: string | null;
  created_at: string;
}

interface Stats {
  total_turns: number;
  avg_total_ms: number;
  avg_vad_ms: number;
  avg_stt_ms: number;
  avg_turn_detection_ms: number;
  avg_llm_ms: number;
  avg_llm_ttft_ms: number;
  avg_tool_ms: number;
  avg_tts_ms: number;
  avg_tts_ttfb_ms: number;
  error_count: number;
  error_rate: number;
  interrupted_count: number;
  interrupt_rate: number;
  retry_turns?: number;
  avg_tts_retry_count?: number;
  fallback_count?: number;
  fallback_rate?: number;
}

const API_URL = "http://localhost:8000";

function StatCard({
  title,
  value,
  unit,
  icon: Icon,
  color,
  subtitle,
}: {
  title: string;
  value: string | number;
  unit?: string;
  icon: any;
  color: string;
  subtitle?: string;
}) {
  return (
    <GlassmorphicCard variant="elevated" padding="lg">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-muted-foreground uppercase tracking-wider">
            {title}
          </p>
          <p className="text-2xl font-bold mt-1">
            {value}
            {unit && (
              <span className="text-sm font-normal text-muted-foreground ml-1">
                {unit}
              </span>
            )}
          </p>
          {subtitle && (
            <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </GlassmorphicCard>
  );
}

const STAGE_COLORS: Record<string, string> = {
  stt: "bg-sage",
  turn: "bg-sage/70",
  llm: "bg-lavender",
  tool: "bg-amber",
  tts: "bg-lavender/70",
  tts_ttfb: "bg-lavender/50",
  other: "bg-chalk-faint",
};

function PipelineBar({ log }: { log: VoiceLogEntry }) {
  const total = log.latency_total_ms;
  if (!total) return <span className="text-muted-foreground text-xs">-</span>;

  const stages = [
    { key: "stt", value: log.latency_stt_ms, label: "STT" },
    { key: "turn", value: log.latency_turn_detection_ms, label: "Turn" },
    { key: "llm", value: log.latency_llm_ms, label: "LLM" },
    { key: "tool", value: log.latency_tool_ms, label: "Tools" },
    { key: "tts", value: log.latency_tts_ms, label: "TTS" },
  ].filter((s) => s.value && s.value > 0);

  return (
    <div className="flex items-center gap-2 min-w-[200px]">
      <div className="flex-1 h-2.5 rounded-full bg-white/5 overflow-hidden flex">
        {stages.map((s) => (
          <div
            key={s.key}
            className={`h-full ${STAGE_COLORS[s.key]}`}
            style={{ width: `${((s.value! / total) * 100).toFixed(1)}%` }}
            title={`${s.label}: ${s.value!.toFixed(0)}ms`}
          />
        ))}
      </div>
      <span className="text-xs text-muted-foreground w-16 text-right font-mono">
        {total.toFixed(0)}ms
      </span>
    </div>
  );
}

export default function Dashboard() {
  const [logs, setLogs] = useState<VoiceLogEntry[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [modeFilter, setModeFilter] = useState<string | null>(null);

  const copyToolCalls = useCallback((logId: number, toolCalls: any[]) => {
    navigator.clipboard.writeText(JSON.stringify(toolCalls, null, 2));
    setCopiedId(logId);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const modeParam = modeFilter ? `&mode=${modeFilter}` : "";
      const [logsRes, statsRes] = await Promise.all([
        fetch(`${API_URL}/api/voice-logs?limit=50${modeParam}`),
        fetch(`${API_URL}/api/voice-logs/stats${modeFilter ? `?mode=${modeFilter}` : ""}`),
      ]);
      const logsData = await logsRes.json();
      const statsData = await statsRes.json();
      setLogs(logsData.logs);
      setStats(statsData);
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Failed to fetch logs", e);
    } finally {
      setLoading(false);
    }
  }, [modeFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const toggleRow = (id: number) => {
    setExpandedRow(expandedRow === id ? null : id);
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-chalk-faint/30">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="p-2 rounded-lg hover:bg-graphite transition-colors"
            >
              <ArrowLeft className="h-5 w-5 text-muted-foreground" />
            </Link>
            <div className="flex items-center gap-3">
              <BarChart3 className="h-6 w-6 text-amber" />
              <div>
                <h1 className="text-lg font-semibold tracking-tight">
                  Pipeline Observatory
                </h1>
                <p className="text-xs text-muted-foreground">
                  End-to-end voice & chat latency tracking
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {/* Mode filter */}
            <div className="flex items-center gap-1 bg-void rounded-lg p-0.5">
              <button
                onClick={() => setModeFilter(null)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                  modeFilter === null ? "bg-graphite text-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                All
              </button>
              <button
                onClick={() => setModeFilter("voice")}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1 ${
                  modeFilter === "voice" ? "bg-graphite text-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Mic className="h-3 w-3" />
                Voice
              </button>
              <button
                onClick={() => setModeFilter("chat")}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors flex items-center gap-1 ${
                  modeFilter === "chat" ? "bg-graphite text-foreground" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <MessageSquare className="h-3 w-3" />
                Chat
              </button>
            </div>

            <span className="text-xs text-muted-foreground">
              Updated {lastRefresh.toLocaleTimeString()}
            </span>
            <ThemeToggle />
            <button
              onClick={fetchData}
              className="p-2 rounded-lg hover:bg-graphite transition-colors"
            >
              <RefreshCw
                className={`h-4 w-4 text-muted-foreground ${loading ? "animate-spin" : ""}`}
              />
            </button>
          </div>
        </div>
      </nav>

      <main className="pt-24 px-8 pb-8 max-w-7xl mx-auto">
        {/* Stats Cards */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-6"
        >
          <StatCard
            title="Total Turns"
            value={stats?.total_turns ?? 0}
            icon={Activity}
            color="bg-lavender/20 text-lavender"
          />
          <StatCard
            title="Avg Total"
            value={stats?.avg_total_ms?.toFixed(0) ?? 0}
            unit="ms"
            icon={Clock}
            color="bg-sage/20 text-sage"
          />
          <StatCard
            title="Avg STT"
            value={stats?.avg_stt_ms?.toFixed(0) ?? 0}
            unit="ms"
            icon={Mic}
            color="bg-sage/20 text-sage"
          />
          <StatCard
            title="Avg LLM"
            value={stats?.avg_llm_ms?.toFixed(0) ?? 0}
            unit="ms"
            subtitle={stats?.avg_llm_ttft_ms ? `TTFT: ${stats.avg_llm_ttft_ms.toFixed(0)}ms` : undefined}
            icon={Zap}
            color="bg-lavender/20 text-lavender"
          />
          <StatCard
            title="Avg TTS"
            value={stats?.avg_tts_ms?.toFixed(0) ?? 0}
            unit="ms"
            subtitle={stats?.avg_tts_ttfb_ms ? `TTFB: ${stats.avg_tts_ttfb_ms.toFixed(0)}ms` : undefined}
            icon={BarChart3}
            color="bg-lavender/20 text-lavender"
          />
          <StatCard
            title="Error Rate"
            value={stats?.error_rate?.toFixed(1) ?? 0}
            unit="%"
            subtitle={stats?.interrupt_rate ? `Interrupt: ${stats.interrupt_rate.toFixed(1)}%` : undefined}
            icon={AlertTriangle}
            color={
              (stats?.error_rate ?? 0) > 10
                ? "bg-ember/20 text-ember"
                : "bg-sage/20 text-sage"
            }
          />
        </motion.div>

        {/* Legend */}
        <div className="flex items-center gap-4 mb-4 text-xs text-muted-foreground flex-wrap">
          <span className="font-medium">Pipeline stages:</span>
          {[
            { key: "stt", label: "STT" },
            { key: "turn", label: "Turn" },
            { key: "llm", label: "LLM" },
            { key: "tool", label: "Tools" },
            { key: "tts", label: "TTS" },
          ].map(({ key, label }) => (
            <span key={key} className="flex items-center gap-1">
              <span className={`w-3 h-2 rounded ${STAGE_COLORS[key]} inline-block`} />
              {label}
            </span>
          ))}
        </div>

        {/* Logs Table */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <GlassmorphicCard variant="elevated" padding="none">
            <div className="grid grid-cols-[40px_100px_1fr_140px_220px_70px] gap-3 px-4 py-3 border-b border-chalk-faint/30 text-xs font-medium text-muted-foreground uppercase tracking-wider">
              <span></span>
              <span>Time</span>
              <span>Message</span>
              <span>Tools</span>
              <span>Pipeline Latency</span>
              <span>Status</span>
            </div>

            {logs.length === 0 && !loading && (
              <div className="px-4 py-12 text-center text-muted-foreground">
                No logs yet. Start a voice or chat session to begin logging.
              </div>
            )}

            {loading && logs.length === 0 && (
              <div className="px-4 py-12 text-center text-muted-foreground">
                Loading...
              </div>
            )}

            {logs.map((log) => (
              <div key={log.id}>
                <button
                  onClick={() => toggleRow(log.id)}
                  className="w-full grid grid-cols-[40px_100px_1fr_140px_220px_70px] gap-3 px-4 py-3 border-b border-chalk-faint/15 hover:bg-graphite transition-colors text-left items-center"
                >
                  {/* Mode icon */}
                  <span>
                    {log.mode === "voice" ? (
                      <Mic className="h-3.5 w-3.5 text-sage" />
                    ) : (
                      <MessageSquare className="h-3.5 w-3.5 text-lavender" />
                    )}
                  </span>

                  {/* Time */}
                  <span className="text-xs text-muted-foreground font-mono">
                    {new Date(log.created_at).toLocaleTimeString()}
                  </span>

                  {/* Message */}
                  <span className="text-sm truncate flex items-center gap-2">
                    {expandedRow === log.id ? (
                      <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                    )}
                    {log.user_message.length > 55
                      ? log.user_message.slice(0, 55) + "..."
                      : log.user_message}
                  </span>

                  {/* Tools */}
                  <div className="flex flex-wrap gap-1">
                    {log.tool_calls.length > 0 ? (
                      log.tool_calls.slice(0, 3).map((tc, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber/20 text-amber"
                        >
                          <Wrench className="h-2.5 w-2.5" />
                          {tc.name.replace("create_teaching_", "").replace("_element", "").replace("render_", "")}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                    {log.tool_calls.length > 3 && (
                      <span className="text-[10px] text-muted-foreground">+{log.tool_calls.length - 3}</span>
                    )}
                  </div>

                  {/* Pipeline Latency Bar */}
                  <PipelineBar log={log} />

                  {/* Status */}
                  <span>
                    {log.error ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-ember/20 text-ember">
                        Error
                      </span>
                    ) : log.tts_fallback_used ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-amber/20 text-amber">
                        Fallback
                      </span>
                    ) : log.tts_retry_count > 0 ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-lavender/20 text-lavender">
                        Retried
                      </span>
                    ) : log.tts_interrupted ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-amber/20 text-amber">
                        Cut
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-sage/20 text-sage">
                        OK
                      </span>
                    )}
                  </span>
                </button>

                {/* Expanded Details */}
                {expandedRow === log.id && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="px-4 py-4 border-b border-chalk-faint/30 bg-white/[0.02]"
                  >
                    <div className="grid grid-cols-3 gap-6">
                      {/* Left: Full Latency Breakdown */}
                      <div className="space-y-3">
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                          Stage Latencies
                        </p>
                        <div className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-xs">
                          {[
                            { label: "STT (speech→transcript)", value: log.latency_stt_ms, color: "text-sage" },
                            { label: "Turn Detection", value: log.latency_turn_detection_ms, color: "text-sage/80" },
                            { label: "STT → LLM gap", value: log.latency_stt_to_llm_ms, color: "text-chalk-soft" },
                            { label: "LLM (inference+tools)", value: log.latency_llm_ms, color: "text-lavender" },
                            { label: "  └ Tool execution", value: log.latency_tool_ms, color: "text-amber" },
                            { label: "  └ LLM first token", value: log.latency_llm_first_token_ms, color: "text-lavender/80" },
                            { label: "TTS (generation)", value: log.latency_tts_ms, color: "text-lavender" },
                            { label: "  └ TTS first chunk", value: log.latency_tts_first_chunk_ms, color: "text-lavender/70" },
                            { label: "Total end-to-end", value: log.latency_total_ms, color: "text-foreground font-semibold" },
                          ].map(({ label, value, color }) => (
                            <div key={label} className="contents">
                              <span className={color}>{label}</span>
                              <span className="font-mono text-right">
                                {value != null ? `${value.toFixed(0)}ms` : "-"}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Middle: Metadata */}
                      <div className="space-y-3">
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
                          Metadata
                        </p>
                        <div className="space-y-2 text-xs">
                          <div>
                            <span className="text-muted-foreground">Provider:</span>{" "}
                            <span className="font-mono">{log.llm_provider} / {log.llm_model}</span>
                          </div>
                          <div>
                            <span className="text-muted-foreground">Session:</span>{" "}
                            <span className="font-mono text-muted-foreground">{log.session_id.slice(0, 16)}...</span>
                          </div>
                          <div>
                            <span className="text-muted-foreground">Mode:</span>{" "}
                            <span className={`font-mono ${log.mode === "voice" ? "text-sage" : "text-lavender"}`}>
                              {log.mode}
                            </span>
                          </div>
                          {(log.tokens_in || log.tokens_out) && (
                            <div>
                              <span className="text-muted-foreground">Tokens:</span>{" "}
                              <span className="font-mono">
                                {log.tokens_in ?? "-"} in / {log.tokens_out ?? "-"} out
                              </span>
                            </div>
                          )}
                          {log.tts_provider_used && (
                            <div>
                              <span className="text-muted-foreground">TTS provider:</span>{" "}
                              <span className="font-mono">{log.tts_provider_used}</span>
                            </div>
                          )}
                          {log.tts_chunks_sent != null && (
                            <div>
                              <span className="text-muted-foreground">TTS chunks:</span>{" "}
                              <span className="font-mono">{log.tts_chunks_sent}</span>
                              {log.tts_interrupted && (
                                <span className="text-amber ml-2">(interrupted)</span>
                              )}
                            </div>
                          )}
                          {log.tts_retry_count > 0 && (
                            <div>
                              <span className="text-muted-foreground">TTS retries:</span>{" "}
                              <span className="font-mono text-lavender">{log.tts_retry_count}</span>
                            </div>
                          )}
                          {log.tts_fallback_used && (
                            <div>
                              <span className="text-muted-foreground">TTS fallback:</span>{" "}
                              <span className="font-mono text-amber">
                                {log.tts_fallback_provider ?? "used"}
                              </span>
                            </div>
                          )}
                          {log.smart_turn_used && (
                            <div>
                              <span className="text-muted-foreground">Smart Turn:</span>{" "}
                              <span className={`font-mono ${
                                log.smart_turn_result === "complete" ? "text-sage" :
                                log.smart_turn_result === "fallback" ? "text-amber" :
                                "text-muted-foreground"
                              }`}>
                                {log.smart_turn_result}
                              </span>
                            </div>
                          )}
                          {log.error && (
                            <div className="mt-2">
                              <p className="text-[10px] uppercase tracking-wider text-ember mb-1">Error</p>
                              <p className="text-xs text-ember/80 font-mono bg-ember/10 p-2 rounded">
                                {log.error}
                              </p>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Right: Tool Calls + Response */}
                      <div className="space-y-3">
                        {log.tool_calls.length > 0 && (
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                                Tool Calls ({log.tool_calls.length})
                              </p>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  copyToolCalls(log.id, log.tool_calls);
                                }}
                                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] hover:bg-graphite transition-colors text-muted-foreground hover:text-foreground"
                                title="Copy tool calls JSON"
                              >
                                {copiedId === log.id ? (
                                  <>
                                    <Check className="h-3 w-3 text-sage" />
                                    <span className="text-sage">Copied</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="h-3 w-3" />
                                    <span>Copy</span>
                                  </>
                                )}
                              </button>
                            </div>
                            <pre className="text-xs font-mono bg-void/80 p-3 rounded overflow-auto max-h-36 text-muted-foreground">
                              {JSON.stringify(log.tool_calls, null, 2)}
                            </pre>
                          </div>
                        )}
                        {log.response_text && (
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                              Response
                            </p>
                            <p className="text-xs bg-void/60 p-3 rounded max-h-28 overflow-auto text-foreground/80">
                              {log.response_text}
                            </p>
                          </div>
                        )}
                      </div>
                    </div>
                  </motion.div>
                )}
              </div>
            ))}
          </GlassmorphicCard>
        </motion.div>
      </main>
    </div>
  );
}
