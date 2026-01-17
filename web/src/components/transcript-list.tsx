"use client";

import { HTMLAttributes, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils";
import { GlassmorphicCard } from "./ui/glassmorphic-card";

interface TranscriptListProps extends HTMLAttributes<HTMLDivElement> {
  transcripts: string[];
}

export function TranscriptList({ transcripts, className, ...props }: TranscriptListProps) {
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcripts]);

  return (
    <div className={cn("space-y-3", className)} {...props}>
      <AnimatePresence mode="popLayout">
        {transcripts.map((transcript, index) => {
          // Detect if it's an assistant message
          const isAssistant = transcript.startsWith("Assistant:");
          const isEmpty = transcript.trim() === "";

          // Skip empty lines
          if (isEmpty) return null;

          // Parse transcript format: "HH:MM:SS [final] text" or "HH:MM:SS [...] text"
          const timeMatch = transcript.match(/^(\d{2}:\d{2}:\d{2})\s+(\[.*?\])\s+(.*)$/);
          const assistantMatch = transcript.match(/^Assistant:\s+(.*)$/);

          let badge = null;
          let content = transcript;
          let timestamp = null;

          if (timeMatch) {
            timestamp = timeMatch[1];
            const badgeText = timeMatch[2];
            content = timeMatch[3];
            badge = badgeText === "[final]" ? "final" : "interim";
          } else if (assistantMatch) {
            content = assistantMatch[1];
          }

          return (
            <motion.div
              key={`${index}-${transcript.substring(0, 20)}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.2 }}
            >
              <GlassmorphicCard
                padding="md"
                variant={isAssistant ? "elevated" : "default"}
                className={cn(
                  "transition-colors",
                  isAssistant && "border-l-2 border-primary"
                )}
              >
                <div className="flex flex-col gap-1.5">
                  {/* Header with timestamp and badge */}
                  {(timestamp || badge) && (
                    <div className="flex items-center gap-2">
                      {timestamp && (
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {timestamp}
                        </span>
                      )}
                      {badge && (
                        <span
                          className={cn(
                            "text-[9px] px-1.5 py-0.5 rounded font-medium uppercase tracking-wider",
                            badge === "final"
                              ? "bg-state-listening/20 text-state-listening"
                              : "bg-state-processing/20 text-state-processing"
                          )}
                        >
                          {badge}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Content */}
                  <p
                    className={cn(
                      "text-sm leading-relaxed",
                      isAssistant
                        ? "text-foreground font-medium"
                        : "text-foreground/90"
                    )}
                  >
                    {isAssistant && (
                      <span className="text-primary font-semibold mr-2">
                        Assistant:
                      </span>
                    )}
                    {content}
                  </p>
                </div>
              </GlassmorphicCard>
            </motion.div>
          );
        })}
      </AnimatePresence>
      <div ref={endRef} />
    </div>
  );
}
