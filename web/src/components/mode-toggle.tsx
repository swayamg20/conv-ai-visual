"use client";

import { motion } from "framer-motion";
import { Mic, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";

export type AppMode = "voice" | "chat";

interface ModeToggleProps {
  mode: AppMode;
  onChange: (mode: AppMode) => void;
  disabled?: boolean;
}

export function ModeToggle({ mode, onChange, disabled }: ModeToggleProps) {
  return (
    <div className="flex items-center gap-1 p-1 rounded-xl bg-void border border-chalk-faint/30">
      <button
        onClick={() => onChange("voice")}
        disabled={disabled}
        className={cn(
          "relative flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
          mode === "voice"
            ? "text-primary"
            : "text-muted-foreground hover:text-foreground",
          disabled && "opacity-50 cursor-not-allowed"
        )}
      >
        {mode === "voice" && (
          <motion.div
            layoutId="mode-bg"
            className="absolute inset-0 bg-amber/15 rounded-lg"
            transition={{ type: "spring", bounce: 0.2, duration: 0.4 }}
          />
        )}
        <Mic className="h-4 w-4 relative z-10" />
        <span className="relative z-10">Voice</span>
      </button>
      <button
        onClick={() => onChange("chat")}
        disabled={disabled}
        className={cn(
          "relative flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
          mode === "chat"
            ? "text-primary"
            : "text-muted-foreground hover:text-foreground",
          disabled && "opacity-50 cursor-not-allowed"
        )}
      >
        {mode === "chat" && (
          <motion.div
            layoutId="mode-bg"
            className="absolute inset-0 bg-amber/15 rounded-lg"
            transition={{ type: "spring", bounce: 0.2, duration: 0.4 }}
          />
        )}
        <MessageSquare className="h-4 w-4 relative z-10" />
        <span className="relative z-10">Chat</span>
      </button>
    </div>
  );
}
