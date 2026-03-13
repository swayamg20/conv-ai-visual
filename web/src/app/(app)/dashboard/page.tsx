"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Plus, Play, Pencil, Trash2, Loader2, Bot, Sparkles, History, FolderOpen } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { fetchAgents, deleteAgent } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { MurmurLogoMark } from "@/components/murmur-doodles";
import { ThemeToggle } from "@/components/theme-toggle";
import { FloatingButton } from "@/components/ui/floating-button";
import { SessionHistoryPanel } from "@/components/session-history-panel";

export default function DashboardPage() {
  const router = useRouter();
  const { user, logout } = useAuth();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);

  const loadAgents = useCallback(async () => {
    try {
      setError(null);
      const data = await fetchAgents();
      setAgents(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load agents";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAgents();
  }, [loadAgents]);

  const handleDeleteConfirm = async (agentId: string) => {
    setConfirmDeleteId(null);
    setDeletingId(agentId);
    try {
      await deleteAgent(agentId);
      setAgents((prev) => prev.filter((a) => a.id !== agentId));
    } catch {
      setError("Failed to delete agent");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <nav className="fixed top-0 left-0 right-0 z-30 glass-card border-b border-chalk-faint/30">
        <div className="container mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <MurmurLogoMark />
            <div>
              <h1 className="text-lg font-semibold tracking-tight">Murmur</h1>
              <p className="text-xs text-muted-foreground">
                {user?.name ? `Hey, ${user.name}` : "Your agents"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <ThemeToggle />
            <button
              onClick={logout}
              className="text-sm text-chalk-soft hover:text-foreground transition-colors font-medium"
            >
              Sign out
            </button>
          </div>
        </div>
      </nav>

      {/* Main */}
      <main className="pt-24 pb-16 px-6 max-w-6xl mx-auto">
        {/* Title Row */}
        <div className="flex items-center justify-between mb-10">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <h2 className="text-3xl font-bold tracking-tight">Your Agents</h2>
            <p className="text-chalk-soft text-sm mt-1">
              Create and manage your personalized AI tutors.
            </p>
          </motion.div>
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.1 }}
          >
            <FloatingButton
              variant="primary"
              size="md"
              icon={<Plus className="h-4 w-4" />}
              onClick={() => router.push("/agents/new")}
            >
              Create Agent
            </FloatingButton>
          </motion.div>
        </div>

        {/* Loading State — Skeleton Cards */}
        {loading && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="glass-card rounded-2xl p-6 flex flex-col animate-pulse"
                style={{ animationDelay: `${i * 100}ms` }}
              >
                <div className="w-10 h-10 rounded-xl bg-chalk-faint/15 mb-4" />
                <div className="h-5 w-36 rounded-lg bg-chalk-faint/15 mb-2" />
                <div className="h-4 w-full rounded bg-chalk-faint/10 mb-1" />
                <div className="h-4 w-2/3 rounded bg-chalk-faint/10 mb-4" />
                <div className="h-3 w-24 rounded bg-chalk-faint/8 mb-4" />
                <div className="mt-auto pt-4 flex items-center gap-2">
                  <div className="flex-1 h-10 rounded-xl bg-chalk-faint/12" />
                  <div className="h-10 w-10 rounded-xl bg-chalk-faint/8" />
                  <div className="h-10 w-10 rounded-xl bg-chalk-faint/8" />
                  <div className="h-10 w-10 rounded-xl bg-chalk-faint/8" />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Error State */}
        {error && !loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="glass-card rounded-2xl p-8 text-center"
          >
            <p className="text-ember mb-4">{error}</p>
            <button
              onClick={loadAgents}
              className="text-amber hover:underline font-medium text-sm"
            >
              Try again
            </button>
          </motion.div>
        )}

        {/* Empty State */}
        {!loading && !error && agents.length === 0 && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15 }}
            className="glass-card rounded-2xl p-16 text-center max-w-lg mx-auto"
          >
            {/* Hand-drawn illustration */}
            <svg
              viewBox="0 0 160 120"
              fill="none"
              className="w-40 h-auto mx-auto mb-6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {/* Bot body sketch */}
              <motion.rect
                x="50" y="20" width="60" height="50" rx="12"
                stroke="hsl(var(--lavender))"
                strokeWidth="1.5"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.4 }}
                transition={{ duration: 1.2, ease: "easeInOut" }}
              />
              {/* Eyes */}
              <motion.circle
                cx="70" cy="42" r="4"
                fill="hsl(var(--lavender))"
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.3 }}
                transition={{ delay: 0.8, duration: 0.4 }}
              />
              <motion.circle
                cx="90" cy="42" r="4"
                fill="hsl(var(--lavender))"
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.3 }}
                transition={{ delay: 0.9, duration: 0.4 }}
              />
              {/* Antenna */}
              <motion.path
                d="M80 20 L80 8"
                stroke="hsl(var(--amber))"
                strokeWidth="1.5"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.4 }}
                transition={{ delay: 1, duration: 0.4 }}
              />
              <motion.circle
                cx="80" cy="6" r="3"
                fill="hsl(var(--amber))"
                initial={{ opacity: 0 }}
                animate={{ opacity: 0.3 }}
                transition={{ delay: 1.2, duration: 0.4 }}
              />
              {/* Arms */}
              <motion.path
                d="M50 40 L35 50"
                stroke="hsl(var(--lavender))"
                strokeWidth="1.5"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.3 }}
                transition={{ delay: 0.6, duration: 0.4 }}
              />
              <motion.path
                d="M110 40 L125 50"
                stroke="hsl(var(--lavender))"
                strokeWidth="1.5"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.3 }}
                transition={{ delay: 0.7, duration: 0.4 }}
              />
              {/* Sparkles around */}
              <motion.text
                x="20" y="30" fontSize="14" fill="hsl(var(--amber))"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.4, 0] }}
                transition={{ delay: 1.5, duration: 2, repeat: Infinity }}
              >*</motion.text>
              <motion.text
                x="135" y="25" fontSize="12" fill="hsl(var(--sage))"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.3, 0] }}
                transition={{ delay: 2, duration: 2, repeat: Infinity }}
              >*</motion.text>
              {/* Ground line */}
              <motion.path
                d="M30 80 Q80 85 130 80"
                stroke="hsl(var(--chalk-faint))"
                strokeWidth="1"
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 0.2 }}
                transition={{ delay: 0.4, duration: 0.8 }}
              />
            </svg>

            <h3 className="text-xl font-semibold mb-2 font-handwriting">No agents yet</h3>
            <p className="text-chalk-soft text-sm mb-8 leading-relaxed">
              Create your first AI agent to get started. It only takes a minute
              to set up a personalized tutor.
            </p>
            <FloatingButton
              variant="primary"
              size="lg"
              icon={<Sparkles className="h-5 w-5" />}
              onClick={() => router.push("/agents/new")}
            >
              Create your first agent
            </FloatingButton>
          </motion.div>
        )}

        {/* Agent Grid */}
        {!loading && !error && agents.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            <AnimatePresence mode="popLayout">
              {agents.map((agent, i) => (
                <motion.div
                  key={agent.id}
                  layout
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ delay: i * 0.05 }}
                  whileHover={{ scale: 1.02, transition: { duration: 0.2 } }}
                  className="glass-card glass-card-hover rounded-2xl p-6 flex flex-col hover:shadow-orb-amber/30 transition-shadow duration-300"
                >
                  {/* Icon */}
                  <div className="text-4xl mb-4">{agent.icon || "🤖"}</div>

                  {/* Name + Badge */}
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="text-lg font-semibold truncate">{agent.name}</h3>
                    {agent.is_default && (
                      <span className="shrink-0 text-[10px] font-mono uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber/15 text-amber border border-amber/20">
                        Default
                      </span>
                    )}
                  </div>

                  {/* Description */}
                  <p className="text-sm text-chalk-soft line-clamp-2 mb-1">
                    {agent.description || "No description"}
                  </p>

                  {/* Meta */}
                  {(agent.subject || agent.level) && (
                    <p className="text-xs text-chalk-faint font-mono mt-1 mb-4 truncate">
                      {[agent.subject, agent.level].filter(Boolean).join(" / ")}
                    </p>
                  )}

                  <div className="mt-auto pt-4 flex items-center gap-2">
                    {/* Start Session */}
                    <button
                      onClick={() => router.push(`/session/${agent.id}`)}
                      className="flex-1 inline-flex items-center justify-center gap-2 h-10 bg-amber text-void font-semibold rounded-xl text-sm hover:brightness-110 transition-all active:scale-[0.97]"
                    >
                      <Play className="h-4 w-4" />
                      Start Session
                    </button>

                    {/* Session History */}
                    <button
                      onClick={() => setSelectedAgent(agent)}
                      className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-xl hover:bg-graphite transition-colors"
                      aria-label="Session history"
                    >
                      <History className="h-4 w-4 text-chalk-soft" />
                    </button>

                    {/* Resources */}
                    <button
                      onClick={() => router.push(`/agents/${agent.id}/resources`)}
                      className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-xl hover:bg-graphite transition-colors"
                      aria-label="Resources"
                    >
                      <FolderOpen className="h-4 w-4 text-chalk-soft" />
                    </button>

                    {/* Edit */}
                    <button
                      onClick={() => router.push(`/agents/${agent.id}/edit`)}
                      className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-xl hover:bg-graphite transition-colors"
                      aria-label="Edit agent"
                    >
                      <Pencil className="h-4 w-4 text-chalk-soft" />
                    </button>

                    {/* Delete — inline confirm to avoid browser dialog */}
                    {confirmDeleteId === agent.id ? (
                      <>
                        <button
                          onClick={() => handleDeleteConfirm(agent.id)}
                          className="h-10 px-3 shrink-0 inline-flex items-center justify-center rounded-xl bg-ember/15 text-ember text-xs font-semibold hover:bg-ember/25 transition-colors"
                          aria-label="Confirm delete"
                        >
                          Delete
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(null)}
                          className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-xl hover:bg-graphite transition-colors text-chalk-soft text-xs font-semibold"
                          aria-label="Cancel delete"
                        >
                          ✕
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => setConfirmDeleteId(agent.id)}
                        disabled={deletingId === agent.id}
                        className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-xl hover:bg-ember/10 transition-colors disabled:opacity-50"
                        aria-label="Delete agent"
                      >
                        {deletingId === agent.id ? (
                          <Loader2 className="h-4 w-4 animate-spin text-ember" />
                        ) : (
                          <Trash2 className="h-4 w-4 text-ember/70" />
                        )}
                      </button>
                    )}
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </main>

      {/* Session History Panel */}
      <AnimatePresence>
        {selectedAgent && (
          <SessionHistoryPanel
            agent={selectedAgent}
            onClose={() => setSelectedAgent(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
