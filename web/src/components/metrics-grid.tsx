"use client";

import { HTMLAttributes } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { GlassmorphicCard } from "./ui/glassmorphic-card";
import type { LatencyMetrics } from "@/hooks/use-webrtc";

interface MetricsGridProps extends HTMLAttributes<HTMLDivElement> {
  metrics: LatencyMetrics;
  layout?: "grid" | "row";
}

interface MetricCardProps {
  label: string;
  value: number | undefined;
  unit?: string;
  threshold?: { good: number; warning: number };
}

function MetricCard({ label, value, unit = "ms", threshold }: MetricCardProps) {
  // Determine color based on threshold
  let colorClass = "text-muted-foreground";

  if (value !== undefined && threshold) {
    if (value < threshold.good) {
      colorClass = "text-state-listening";
    } else if (value < threshold.warning) {
      colorClass = "text-state-processing";
    } else {
      colorClass = "text-destructive";
    }
  } else if (value !== undefined) {
    colorClass = "text-foreground";
  }

  return (
    <GlassmorphicCard padding="md" variant="elevated" className="text-center">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3 }}
      >
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">
          {label}
        </div>
        <div className={cn("text-2xl font-mono font-semibold transition-colors", colorClass)}>
          {value !== undefined ? `${value.toFixed(value < 10 ? 1 : 0)}${unit}` : "-"}
        </div>
      </motion.div>
    </GlassmorphicCard>
  );
}

export function MetricsGrid({
  metrics,
  layout = "grid",
  className,
  ...props
}: MetricsGridProps) {
  const metricsData = [
    {
      label: "VAD",
      value: metrics.vadLatency,
      threshold: { good: 5, warning: 10 },
    },
    {
      label: "STT",
      value: metrics.sttFinalTranscript,
      threshold: { good: 500, warning: 1000 },
    },
    {
      label: "LLM",
      value: metrics.llmComplete,
      threshold: { good: 1000, warning: 2000 },
    },
    {
      label: "TTS",
      value: metrics.ttsComplete,
      threshold: { good: 500, warning: 1000 },
    },
    {
      label: "Total",
      value: metrics.totalPipeline,
      threshold: { good: 2000, warning: 4000 },
    },
  ];

  return (
    <div
      className={cn(
        layout === "grid"
          ? "grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4"
          : "flex flex-wrap gap-4",
        className
      )}
      {...props}
    >
      {metricsData.map((metric, index) => (
        <motion.div
          key={metric.label}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: index * 0.05 }}
        >
          <MetricCard {...metric} />
        </motion.div>
      ))}
    </div>
  );
}
