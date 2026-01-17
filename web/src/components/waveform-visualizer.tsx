"use client";

import { HTMLAttributes, useMemo } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface WaveformVisualizerProps extends HTMLAttributes<HTMLDivElement> {
  amplitude?: number;
  barCount?: number;
  barColor?: string;
  barHeight?: number;
  animated?: boolean;
}

export function WaveformVisualizer({
  amplitude = 0.3,
  barCount = 5,
  barColor = "rgba(255, 255, 255, 0.9)",
  barHeight = 40,
  animated = true,
  className,
  ...props
}: WaveformVisualizerProps) {
  // Clamp amplitude between 0 and 1
  const clampedAmplitude = Math.max(0, Math.min(1, amplitude));

  // Generate bar heights based on amplitude
  const bars = useMemo(() => {
    return Array.from({ length: barCount }, (_, i) => {
      // Create a wave pattern - center bars are taller
      const centerDistance = Math.abs(i - (barCount - 1) / 2);
      const normalizedDistance = centerDistance / ((barCount - 1) / 2);
      const baseHeight = 0.3 + (1 - normalizedDistance) * 0.4;

      // Apply amplitude
      const height = animated
        ? baseHeight * clampedAmplitude + 0.2
        : baseHeight;

      return {
        height: height * barHeight,
        delay: i * 0.1,
      };
    });
  }, [barCount, clampedAmplitude, barHeight, animated]);

  return (
    <div
      className={cn("flex items-center justify-center gap-1.5", className)}
      {...props}
    >
      {bars.map((bar, index) => (
        <motion.div
          key={index}
          className="rounded-full"
          style={{
            width: "4px",
            backgroundColor: barColor,
          }}
          animate={
            animated
              ? {
                  height: [bar.height * 0.5, bar.height, bar.height * 0.5],
                }
              : {
                  height: bar.height,
                }
          }
          transition={
            animated
              ? {
                  duration: 1.5,
                  repeat: Infinity,
                  ease: "easeInOut",
                  delay: bar.delay,
                }
              : {
                  duration: 0.15,
                  ease: "easeOut",
                }
          }
        />
      ))}
    </div>
  );
}
