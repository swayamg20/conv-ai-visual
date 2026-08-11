"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { X, Plus, MessageSquare, Clock, Loader2, BarChart3 } from "lucide-react";
import { fetchSessions } from "@/lib/api";
import { FloatingButton } from "@/components/ui/floating-button";
import { MasteryHeatmap } from "@/components/mastery-heatmap";
import type { Agent, Session } from "@/lib/types";

type TabId = "sessions" | "mastery";

interface SessionHistoryPanelProps {
  agent: Agent;
  onClose: () => void;
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays === 0) {
    return `Today at ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  if (diffDays === 1) {
    return `Yesterday at ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  }
  if (diffDays < 7) {
    return `${diffDays} days ago`;
  }
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function SessionsSkeleton() {
  return (
    <div className="space-y-3 p-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="p-4 rounded-xl animate-pulse">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="h-4 w-40 rounded bg-chalk-faint/15 mb-2" />
              <div className="h-3 w-64 rounded bg-chalk-faint/10" />
            </div>
            <div className="shrink-0 text-right">
              <div className="h-3 w-20 rounded bg-chalk-faint/10 mb-1.5" />
              <div className="h-3 w-16 rounded bg-chalk-faint/10" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SessionHistoryPanel({ agent, onClose }: SessionHistoryPanelProps) {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<TabId>("sessions");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reloadSessions = async () => {
    setLoading(true);
    setError(null);
    try {
      setSessions(await fetchSessions(agent.id));
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load sessions";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    fetchSessions(agent.id)
      .then((data) => {
        if (!cancelled) setSessions(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load sessions";
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [agent.id]);

  const handleNewSession = () => {
    router.push(`/session/${agent.id}`);
  };

  const handleResumeSession = (sessionId: string) => {
    router.push(`/session/${agent.id}?session=${sessionId}`);
  };

  const tabs: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: "sessions", label: "Sessions", icon: <MessageSquare className="h-3.5 w-3.5" /> },
    { id: "mastery", label: "Mastery Map", icon: <BarChart3 className="h-3.5 w-3.5" /> },
  ];

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-3 sm:items-center sm:p-6"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-void/60 backdrop-blur-sm" />

      {/* Panel */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        transition={{ type: "spring", stiffness: 300, damping: 30 }}
        onClick={(e) => e.stopPropagation()}
        className="relative my-4 flex max-h-[min(88vh,48rem)] w-full max-w-xl flex-col overflow-hidden rounded-[28px] glass-card sm:my-0"
      >
        {/* Header */}
        <div className="shrink-0 border-b border-chalk-faint/20 p-5 sm:p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <span className="text-3xl">{agent.icon || "🤖"}</span>
              <div className="min-w-0">
                <h2 className="truncate text-lg font-semibold">{agent.name}</h2>
                {agent.description && (
                  <p className="mt-0.5 line-clamp-2 text-sm text-chalk-soft">
                    {agent.description}
                  </p>
                )}
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-graphite transition-colors shrink-0"
              aria-label="Close"
            >
              <X className="h-5 w-5 text-chalk-soft" />
            </button>
          </div>

          <div className="mt-5">
            <FloatingButton
              variant="primary"
              size="sm"
              icon={<Plus className="h-4 w-4" />}
              onClick={handleNewSession}
              className="w-full"
            >
              New Session
            </FloatingButton>
          </div>

          {/* Tabs */}
          <div className="mt-5 flex gap-1 rounded-xl bg-chalk-faint/10 p-1">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`inline-flex h-9 min-w-0 flex-1 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-all duration-200 ${
                  activeTab === tab.id
                    ? "bg-amber/15 text-amber border border-amber/20"
                    : "text-chalk-soft hover:text-foreground"
                }`}
              >
                {tab.icon}
                <span className="truncate">{tab.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-y-auto px-4 pb-4 pt-3 sm:px-5 sm:pb-5">
          <AnimatePresence mode="wait">
            {activeTab === "sessions" && (
              <motion.div
                key="sessions"
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 10 }}
                transition={{ duration: 0.2 }}
                className="min-h-full"
              >
                {loading && <SessionsSkeleton />}

                {error && !loading && (
                  <div className="text-center py-8">
                    <p className="text-ember text-sm mb-3">{error}</p>
                    <button
                      onClick={reloadSessions}
                      className="text-amber hover:underline font-medium text-sm"
                    >
                      Try again
                    </button>
                  </div>
                )}

                {!loading && !error && sessions.length === 0 && (
                  <div className="text-center py-12">
                    <MessageSquare className="h-8 w-8 text-chalk-faint mx-auto mb-3" />
                    <p className="text-sm text-chalk-soft font-handwriting text-lg">Your first session awaits...</p>
                    <p className="text-xs text-chalk-faint mt-1">
                      Start a new session to begin learning
                    </p>
                  </div>
                )}

                {!loading && !error && sessions.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs text-chalk-faint font-mono uppercase tracking-wider px-2 mb-3">
                      Previous Sessions
                    </p>
                    <AnimatePresence>
                      {sessions.map((session, i) => (
                        <motion.button
                          key={session.id}
                          initial={{ opacity: 0, y: 8 }}
                          animate={{ opacity: 1, y: 0 }}
                          transition={{ delay: i * 0.03 }}
                          onClick={() => handleResumeSession(session.id)}
                          className="w-full text-left p-4 rounded-xl hover:bg-graphite/50 transition-colors group"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <p className="text-sm font-medium truncate group-hover:text-amber transition-colors">
                                {session.title || `Session #${i + 1}`}
                              </p>
                              {session.summary && (
                                <p className="text-xs text-chalk-soft line-clamp-2 mt-1">
                                  {session.summary}
                                </p>
                              )}
                            </div>
                            <div className="shrink-0 text-right">
                              <div className="flex items-center gap-1 text-xs text-chalk-faint">
                                <Clock className="h-3 w-3" />
                                <span>{formatDate(session.created_at)}</span>
                              </div>
                              <div className="flex items-center gap-1 text-xs text-chalk-faint mt-1">
                                <MessageSquare className="h-3 w-3" />
                                <span>{session.message_count} messages</span>
                              </div>
                            </div>
                          </div>
                        </motion.button>
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </motion.div>
            )}

            {activeTab === "mastery" && (
              <motion.div
                key="mastery"
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -10 }}
                transition={{ duration: 0.2 }}
                className="min-h-full"
              >
                <MasteryHeatmap agentId={agent.id} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.div>
    </motion.div>
  );
}
