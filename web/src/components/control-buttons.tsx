"use client";

import { motion } from "framer-motion";
import { Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

interface ControlButtonsProps {
  isMicMuted: boolean;
  isTTSEnabled: boolean;
  onToggleMic: () => void;
  onToggleTTS: () => void;
  disabled?: boolean;
  className?: string;
}

export function ControlButtons({
  isMicMuted,
  isTTSEnabled,
  onToggleMic,
  onToggleTTS,
  disabled = false,
  className,
}: ControlButtonsProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.5 }}
      className={cn("flex items-center gap-3", className)}
    >
      {/* Microphone Mute/Unmute Button */}
      <motion.button
        onClick={onToggleMic}
        disabled={disabled}
        whileHover={{ scale: disabled ? 1 : 1.05 }}
        whileTap={{ scale: disabled ? 1 : 0.95 }}
        className={cn(
          "relative flex items-center justify-center w-12 h-12 rounded-full",
          "glass-card border border-white/10 transition-all duration-300",
          "hover:shadow-glass hover:border-white/20",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          isMicMuted
            ? "bg-red-500/20 border-red-500/30 hover:bg-red-500/30"
            : "hover:bg-white/10"
        )}
        aria-label={isMicMuted ? "Unmute microphone" : "Mute microphone"}
        title={isMicMuted ? "Unmute microphone" : "Mute microphone"}
      >
        {isMicMuted ? (
          <MicOff className="w-5 h-5 text-red-400" />
        ) : (
          <Mic className="w-5 h-5 text-green-400" />
        )}

        {/* Status indicator dot */}
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          className={cn(
            "absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-background",
            isMicMuted ? "bg-red-500" : "bg-green-500"
          )}
        >
          {!isMicMuted && (
            <motion.div
              className="absolute inset-0 rounded-full bg-green-500"
              animate={{
                scale: [1, 1.5, 1],
                opacity: [0.8, 0, 0.8],
              }}
              transition={{
                duration: 2,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />
          )}
        </motion.div>
      </motion.button>

      {/* TTS Enable/Disable Button */}
      <motion.button
        onClick={onToggleTTS}
        disabled={disabled}
        whileHover={{ scale: disabled ? 1 : 1.05 }}
        whileTap={{ scale: disabled ? 1 : 0.95 }}
        className={cn(
          "relative flex items-center justify-center w-12 h-12 rounded-full",
          "glass-card border border-white/10 transition-all duration-300",
          "hover:shadow-glass hover:border-white/20",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          !isTTSEnabled
            ? "bg-orange-500/20 border-orange-500/30 hover:bg-orange-500/30"
            : "hover:bg-white/10"
        )}
        aria-label={isTTSEnabled ? "Disable voice responses" : "Enable voice responses"}
        title={isTTSEnabled ? "Disable voice responses" : "Enable voice responses"}
      >
        {!isTTSEnabled ? (
          <VolumeX className="w-5 h-5 text-orange-400" />
        ) : (
          <Volume2 className="w-5 h-5 text-blue-400" />
        )}

        {/* Status indicator dot */}
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          className={cn(
            "absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-background",
            !isTTSEnabled ? "bg-orange-500" : "bg-blue-500"
          )}
        >
          {isTTSEnabled && (
            <motion.div
              className="absolute inset-0 rounded-full bg-blue-500"
              animate={{
                scale: [1, 1.5, 1],
                opacity: [0.8, 0, 0.8],
              }}
              transition={{
                duration: 2,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />
          )}
        </motion.div>
      </motion.button>

      {/* Tooltip/Labels */}
      <div className="hidden md:flex flex-col gap-0.5 text-xs text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <div className={cn("w-1.5 h-1.5 rounded-full", isMicMuted ? "bg-red-500" : "bg-green-500")} />
          <span>{isMicMuted ? "Mic muted" : "Mic active"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className={cn("w-1.5 h-1.5 rounded-full", !isTTSEnabled ? "bg-orange-500" : "bg-blue-500")} />
          <span>{isTTSEnabled ? "Voice on" : "Voice off"}</span>
        </div>
      </div>
    </motion.div>
  );
}
