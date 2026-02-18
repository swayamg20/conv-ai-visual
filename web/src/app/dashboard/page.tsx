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
} from "lucide-react";
import Link from "next/link";
import { GlassmorphicCard } from "@/components/ui/glassmorphic-card";

interface LogEntry {
  id: number;
  session_id: string;
  user_id: string | null;
  user_message: string;
  llm_provider: string;
  llm_model: string;
  tool_calls: Array<{ name: string; arguments: any }>;
  response_text: string | null;
  latency_total_ms: number | null;
  latency_llm_ms: number | null;
  latency_tool_ms: number | null;
  latency_stream_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  error: string | null;
  created_at: string;
}

interface Stats {
  total_calls: number;
  avg_latency_ms: number;
  avg_llm_latency_ms: number;
  avg_tool_latency_ms: number;
  error_count: number;
  error_rate: number;
}

const API_URL = "http://localhost:8000";

function StatCard({
  title,
  value,
  unit,
  icon: Icon,
  color,
}: {
  title: string;
  value: string | number;
  unit?: string;
  icon: any;
  color: string;
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
        </div>
        <div className={`p-2 rounded-lg ${color}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </GlassmorphicCard>
  );
}

function LatencyBar({
  llm,
  tool,
  stream,
  total,
}: {
  llm: number;
  tool: number;
  stream: number;
  total: number;
}) {
  if (!total) return <span className="text-muted-foreground">-</span>;
  const llmPct = (llm / total) * 100;
  const toolPct = (tool / total) * 100;
  const streamPct = (stream / total) * 100;

  return (
    <div className="flex items-center gap-2 min-w-[180px]">
      <div className="flex-1 h-2 rounded-full bg-white/5 overflow-hidden flex">
        <div
          className="h-full bg-blue-500"
          style={{ width: `${llmPct}%` }}
          title={`LLM: ${llm.toFixed(0)}ms`}
        />
        <div
          className="h-full bg-amber-500"
          style={{ width: `${toolPct}%` }}
          title={`Tools: ${tool.toFixed(0)}ms`}
        />
        <div
          className="h-full bg-emerald-500"
          style={{ width: `${streamPct}%` }}
          title={`Stream: ${stream.toFixed(0)}ms`}
        />
      </div>
      <span className="text-xs text-muted-foreground w-16 text-right">
        {total.toFixed(0)}ms
      </span>
    </div>
  );
}

export default function Dashboard() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const copyToolCalls = useCallback((logId: number, toolCalls: any[]) => {
    navigator.clipboard.writeText(JSON.stringify(toolCalls, null, 2));
    setCopiedId(logId);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const [logsRes, statsRes] = await Promise.all([
        fetch(`${API_URL}/api/logs?limit=50`),
        fetch(`${API_URL}/api/logs/stats`),
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
  }, []);

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
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-white/10">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            >
              <ArrowLeft className="h-5 w-5 text-muted-foreground" />
            </Link>
            <div className="flex items-center gap-3">
              <BarChart3 className="h-6 w-6 text-primary" />
              <div>
                <h1 className="text-lg font-semibold tracking-tight">
                  LLM Call Logs
                </h1>
                <p className="text-xs text-muted-foreground">
                  Observability Dashboard
                </p>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground">
              Updated {lastRefresh.toLocaleTimeString()}
            </span>
            <button
              onClick={fetchData}
              className="p-2 rounded-lg hover:bg-white/10 transition-colors"
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
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8"
        >
          <StatCard
            title="Total Calls"
            value={stats?.total_calls ?? 0}
            icon={Activity}
            color="bg-blue-500/20 text-blue-400"
          />
          <StatCard
            title="Avg Latency"
            value={stats?.avg_latency_ms?.toFixed(0) ?? 0}
            unit="ms"
            icon={Clock}
            color="bg-emerald-500/20 text-emerald-400"
          />
          <StatCard
            title="Avg LLM Time"
            value={stats?.avg_llm_latency_ms?.toFixed(0) ?? 0}
            unit="ms"
            icon={BarChart3}
            color="bg-amber-500/20 text-amber-400"
          />
          <StatCard
            title="Error Rate"
            value={stats?.error_rate?.toFixed(1) ?? 0}
            unit="%"
            icon={AlertTriangle}
            color={
              (stats?.error_rate ?? 0) > 10
                ? "bg-red-500/20 text-red-400"
                : "bg-emerald-500/20 text-emerald-400"
            }
          />
        </motion.div>

        {/* Legend */}
        <div className="flex items-center gap-6 mb-4 text-xs text-muted-foreground">
          <span className="font-medium">Latency breakdown:</span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-2 rounded bg-blue-500 inline-block" />
            LLM
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-2 rounded bg-amber-500 inline-block" />
            Tools
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-2 rounded bg-emerald-500 inline-block" />
            Stream
          </span>
        </div>

        {/* Logs Table */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <GlassmorphicCard variant="elevated" padding="none">
            {/* Table Header */}
            <div className="grid grid-cols-[140px_1fr_160px_200px_80px] gap-4 px-4 py-3 border-b border-white/10 text-xs font-medium text-muted-foreground uppercase tracking-wider">
              <span>Time</span>
              <span>Message</span>
              <span>Tools</span>
              <span>Latency</span>
              <span>Status</span>
            </div>

            {/* Table Rows */}
            {logs.length === 0 && !loading && (
              <div className="px-4 py-12 text-center text-muted-foreground">
                No logs yet. Send a chat message to start logging.
              </div>
            )}

            {loading && logs.length === 0 && (
              <div className="px-4 py-12 text-center text-muted-foreground">
                Loading...
              </div>
            )}

            {logs.map((log) => (
              <div key={log.id}>
                {/* Row */}
                <button
                  onClick={() => toggleRow(log.id)}
                  className="w-full grid grid-cols-[140px_1fr_160px_200px_80px] gap-4 px-4 py-3 border-b border-white/5 hover:bg-white/5 transition-colors text-left items-center"
                >
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
                    {log.user_message.length > 60
                      ? log.user_message.slice(0, 60) + "..."
                      : log.user_message}
                  </span>

                  {/* Tools */}
                  <div className="flex flex-wrap gap-1">
                    {log.tool_calls.length > 0 ? (
                      log.tool_calls.map((tc, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300"
                        >
                          <Wrench className="h-2.5 w-2.5" />
                          {tc.name.replace("create_teaching_", "").replace("_element", "").replace("render_", "")}
                        </span>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </div>

                  {/* Latency */}
                  <LatencyBar
                    llm={log.latency_llm_ms ?? 0}
                    tool={log.latency_tool_ms ?? 0}
                    stream={log.latency_stream_ms ?? 0}
                    total={log.latency_total_ms ?? 0}
                  />

                  {/* Status */}
                  <span>
                    {log.error ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-red-500/20 text-red-400">
                        Error
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] bg-emerald-500/20 text-emerald-400">
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
                    className="px-4 py-4 border-b border-white/10 bg-white/[0.02]"
                  >
                    <div className="grid grid-cols-2 gap-6">
                      {/* Left: Metadata */}
                      <div className="space-y-3">
                        <div>
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                            Provider / Model
                          </p>
                          <p className="text-sm font-mono">
                            {log.llm_provider} / {log.llm_model}
                          </p>
                        </div>
                        <div>
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                            Session
                          </p>
                          <p className="text-xs font-mono text-muted-foreground">
                            {log.session_id}
                          </p>
                        </div>
                        <div>
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                            Latency Breakdown
                          </p>
                          <div className="grid grid-cols-2 gap-2 text-xs">
                            <span className="text-muted-foreground">Total:</span>
                            <span className="font-mono">
                              {log.latency_total_ms?.toFixed(0) ?? "-"}ms
                            </span>
                            <span className="text-blue-400">LLM:</span>
                            <span className="font-mono">
                              {log.latency_llm_ms?.toFixed(0) ?? "-"}ms
                            </span>
                            <span className="text-amber-400">Tools:</span>
                            <span className="font-mono">
                              {log.latency_tool_ms?.toFixed(0) ?? "-"}ms
                            </span>
                            <span className="text-emerald-400">Stream:</span>
                            <span className="font-mono">
                              {log.latency_stream_ms?.toFixed(0) ?? "-"}ms
                            </span>
                          </div>
                        </div>
                        {(log.tokens_in || log.tokens_out) && (
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                              Tokens
                            </p>
                            <p className="text-xs font-mono">
                              In: {log.tokens_in ?? "-"} / Out:{" "}
                              {log.tokens_out ?? "-"}
                            </p>
                          </div>
                        )}
                        {log.error && (
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-red-400 mb-1">
                              Error
                            </p>
                            <p className="text-xs text-red-300 font-mono bg-red-500/10 p-2 rounded">
                              {log.error}
                            </p>
                          </div>
                        )}
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
                                className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] hover:bg-white/10 transition-colors text-muted-foreground hover:text-foreground"
                                title="Copy tool calls JSON"
                              >
                                {copiedId === log.id ? (
                                  <>
                                    <Check className="h-3 w-3 text-emerald-400" />
                                    <span className="text-emerald-400">Copied</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="h-3 w-3" />
                                    <span>Copy</span>
                                  </>
                                )}
                              </button>
                            </div>
                            <pre className="text-xs font-mono bg-black/30 p-3 rounded overflow-auto max-h-48 text-muted-foreground">
                              {JSON.stringify(log.tool_calls, null, 2)}
                            </pre>
                          </div>
                        )}
                        {log.response_text && (
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                              Response
                            </p>
                            <p className="text-xs bg-black/20 p-3 rounded max-h-32 overflow-auto text-foreground/80">
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
