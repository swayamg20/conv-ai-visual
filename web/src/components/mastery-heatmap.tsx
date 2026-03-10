"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { fetchMastery } from "@/lib/api";
import type { MasteryData, TopicMastery, ChapterMastery } from "@/lib/types";

interface MasteryHeatmapProps {
  agentId: string;
}

const SIGNAL_COLORS = {
  understood: {
    bg: "hsl(var(--sage))",
    bgFaint: "hsl(var(--sage) / 0.15)",
    border: "hsl(var(--sage) / 0.4)",
    label: "Understood",
    glow: "0 0 12px hsl(var(--sage) / 0.3)",
  },
  struggled: {
    bg: "hsl(var(--amber))",
    bgFaint: "hsl(var(--amber) / 0.15)",
    border: "hsl(var(--amber) / 0.4)",
    label: "Struggled",
    glow: "0 0 12px hsl(var(--amber) / 0.3)",
  },
  unclear: {
    bg: "hsl(var(--ember))",
    bgFaint: "hsl(var(--ember) / 0.18)",
    border: "hsl(var(--ember) / 0.5)",
    label: "Unclear",
    glow: "0 0 16px hsl(var(--ember) / 0.35)",
  },
} as const;

function TopicNode({
  topic,
  index,
}: {
  topic: TopicMastery;
  index: number;
}) {
  const [showTooltip, setShowTooltip] = useState(false);
  const colors = SIGNAL_COLORS[topic.signal_type];

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.04, duration: 0.3 }}
      className="relative"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <motion.div
        className="px-3 py-2 rounded-xl text-sm font-medium cursor-default transition-all duration-200 border"
        style={{
          backgroundColor: colors.bgFaint,
          borderColor: colors.border,
          boxShadow: topic.signal_type === "struggled" ? colors.glow : "none",
        }}
        animate={
          topic.signal_type === "struggled"
            ? { boxShadow: [colors.glow, "0 0 4px hsl(var(--amber) / 0.1)", colors.glow] }
            : {}
        }
        transition={
          topic.signal_type === "struggled"
            ? { duration: 2, repeat: Infinity, ease: "easeInOut" }
            : {}
        }
        whileHover={{ scale: 1.05 }}
      >
        <span className="font-handwriting text-base" style={{ color: colors.bg }}>
          {topic.topic}
        </span>
      </motion.div>

      {/* Tooltip */}
      <AnimatePresence>
        {showTooltip && (
          <motion.div
            initial={{ opacity: 0, y: 4, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 glass-card rounded-xl p-3 pointer-events-none"
          >
            <p className="text-sm font-semibold text-foreground mb-1">{topic.topic}</p>
            <p className="text-xs text-chalk-soft">
              Discussed in {topic.session_count} session{topic.session_count !== 1 ? "s" : ""}.
            </p>
            <div className="flex items-center gap-1.5 mt-1.5">
              <div
                className="w-2 h-2 rounded-full"
                style={{ backgroundColor: colors.bg }}
              />
              <span className="text-xs font-mono" style={{ color: colors.bg }}>
                {colors.label}
              </span>
            </div>
            {/* Triangle pointer */}
            <div className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[6px] border-t-chalk-faint/20" />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function ChapterGroup({
  chapter,
  topics,
  chapterIndex,
}: {
  chapter: ChapterMastery;
  topics: TopicMastery[];
  chapterIndex: number;
}) {
  const total = chapter.understood + chapter.struggled + chapter.unclear;
  const pctUnderstood = total > 0 ? (chapter.understood / total) * 100 : 0;
  const pctStruggled = total > 0 ? (chapter.struggled / total) * 100 : 0;
  const pctUnclear = total > 0 ? (chapter.unclear / total) * 100 : 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: chapterIndex * 0.1, duration: 0.4 }}
      className="glass-card rounded-2xl p-5"
    >
      {/* Chapter header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-base font-semibold font-handwriting tracking-wide text-foreground">
          {chapter.name}
        </h3>
        <span className="text-xs text-chalk-soft font-mono">
          {total} topic{total !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Mini progress bar */}
      <div className="flex h-1.5 rounded-full overflow-hidden mb-4 bg-chalk-faint/20">
        {pctUnderstood > 0 && (
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${pctUnderstood}%` }}
            transition={{ delay: chapterIndex * 0.1 + 0.3, duration: 0.6 }}
            className="h-full"
            style={{ backgroundColor: "hsl(var(--sage))" }}
          />
        )}
        {pctStruggled > 0 && (
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${pctStruggled}%` }}
            transition={{ delay: chapterIndex * 0.1 + 0.4, duration: 0.6 }}
            className="h-full"
            style={{ backgroundColor: "hsl(var(--amber))" }}
          />
        )}
        {pctUnclear > 0 && (
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: `${pctUnclear}%` }}
            transition={{ delay: chapterIndex * 0.1 + 0.5, duration: 0.6 }}
            className="h-full"
            style={{ backgroundColor: "hsl(var(--ember))" }}
          />
        )}
      </div>

      {/* Topic nodes */}
      <div className="flex flex-wrap gap-2">
        {topics.map((topic, i) => (
          <TopicNode key={topic.topic} topic={topic} index={i} />
        ))}
      </div>
    </motion.div>
  );
}

function SkeletonHeatmap() {
  return (
    <div className="space-y-4">
      {[0, 1, 2].map((i) => (
        <div key={i} className="glass-card rounded-2xl p-5 animate-pulse">
          <div className="flex items-center justify-between mb-4">
            <div className="h-5 w-40 rounded-lg bg-chalk-faint/20" />
            <div className="h-4 w-16 rounded-lg bg-chalk-faint/10" />
          </div>
          <div className="h-1.5 rounded-full bg-chalk-faint/10 mb-4" />
          <div className="flex flex-wrap gap-2">
            {[0, 1, 2, 3].map((j) => (
              <div
                key={j}
                className="h-9 rounded-xl bg-chalk-faint/10"
                style={{ width: `${70 + j * 20}px` }}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyMastery() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="text-center py-12 px-6"
    >
      {/* Hand-drawn map outline */}
      <svg
        viewBox="0 0 200 140"
        fill="none"
        className="w-48 h-auto mx-auto mb-6 opacity-20"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Ghost nodes */}
        <rect x="10" y="10" width="70" height="30" rx="8" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="120" y="10" width="70" height="30" rx="8" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="40" y="55" width="70" height="30" rx="8" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="90" y="100" width="70" height="30" rx="8" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="10" y="100" width="60" height="30" rx="8" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        {/* Connecting lines */}
        <line x1="45" y1="40" x2="65" y2="55" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        <line x1="155" y1="40" x2="85" y2="55" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        <line x1="75" y1="85" x2="40" y2="100" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        <line x1="75" y1="85" x2="125" y2="100" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
      </svg>

      <h3 className="text-lg font-semibold text-foreground mb-2 font-handwriting">
        Your mastery map awaits
      </h3>
      <p className="text-sm text-chalk-soft max-w-xs mx-auto leading-relaxed">
        Complete a few sessions and your concept mastery will appear here — like notes filling a blackboard.
      </p>
    </motion.div>
  );
}

export function MasteryHeatmap({ agentId }: MasteryHeatmapProps) {
  const [data, setData] = useState<MasteryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setError(null);
      setLoading(true);
      const mastery = await fetchMastery(agentId);
      setData(mastery);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load mastery data";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Group topics by chapter
  const topicsByChapter = useMemo(() => {
    if (!data) return new Map<string, TopicMastery[]>();
    const map = new Map<string, TopicMastery[]>();
    for (const topic of data.topics) {
      const ch = topic.chapter || "Uncategorized";
      if (!map.has(ch)) map.set(ch, []);
      map.get(ch)!.push(topic);
    }
    return map;
  }, [data]);

  if (loading) {
    return <SkeletonHeatmap />;
  }

  if (error) {
    return (
      <div className="text-center py-8">
        <p className="text-ember text-sm mb-3">{error}</p>
        <button
          onClick={loadData}
          className="text-amber hover:underline font-medium text-sm"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!data || data.topics.length === 0) {
    return <EmptyMastery />;
  }

  return (
    <div className="space-y-4">
      {/* Legend */}
      <div className="flex items-center gap-4 mb-2 px-1">
        {(["understood", "struggled", "unclear"] as const).map((type) => (
          <div key={type} className="flex items-center gap-1.5">
            <div
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: SIGNAL_COLORS[type].bg }}
            />
            <span className="text-xs text-chalk-soft font-mono">
              {SIGNAL_COLORS[type].label}
            </span>
          </div>
        ))}
      </div>

      {/* Chapter groups */}
      {data.chapters.map((chapter, i) => {
        const topics = topicsByChapter.get(chapter.name) || [];
        return (
          <ChapterGroup
            key={chapter.name}
            chapter={chapter}
            topics={topics}
            chapterIndex={i}
          />
        );
      })}
    </div>
  );
}
