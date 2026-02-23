"use client";

import { HTMLAttributes, useMemo } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { WaveformVisualizer } from "./waveform-visualizer";
import { Loader2, MicOff } from "lucide-react";

export type VoiceState =
  | "idle"
  | "connecting"
  | "listening"
  | "speaking"
  | "processing"
  | "interrupted"
  | "error";

interface VoiceOrbProps extends Omit<HTMLAttributes<HTMLDivElement>, "onClick"> {
  state: VoiceState;
  size?: "sm" | "md" | "lg" | "xl";
  audioLevel?: number;
  onClick?: () => void;
  disabled?: boolean;
}

const sizeConfig = {
  sm: {
    container: "w-40 h-40",
    orb: "w-36 h-36",
    icon: "h-8 w-8",
    waveform: { barHeight: 24, barCount: 5 },
  },
  md: {
    container: "w-56 h-56",
    orb: "w-52 h-52",
    icon: "h-10 w-10",
    waveform: { barHeight: 32, barCount: 5 },
  },
  lg: {
    container: "w-72 h-72",
    orb: "w-[17rem] h-[17rem]",
    icon: "h-12 w-12",
    waveform: { barHeight: 40, barCount: 5 },
  },
  xl: {
    container: "w-96 h-96 md:w-[28rem] md:h-[28rem]",
    orb: "w-[22rem] h-[22rem] md:w-[26rem] md:h-[26rem]",
    icon: "h-16 w-16",
    waveform: { barHeight: 48, barCount: 5 },
  },
};

const stateConfig = {
  idle: {
    gradient: "from-graphite to-graphite",
    borderStyle: "border-2 border-dashed border-chalk-faint",
    glow: "shadow-none",
    animation: "animate-pulse-glow",
    showWaveform: false,
  },
  connecting: {
    gradient: "from-lavender to-lavender",
    borderStyle: "border-2 border-chalk-faint/50",
    glow: "shadow-orb",
    animation: "animate-pulse-glow",
    showWaveform: false,
  },
  listening: {
    gradient: "from-sage to-sage",
    borderStyle: "border-2 border-chalk-faint/50",
    glow: "shadow-orb-sage",
    animation: "animate-pulse-glow",
    showWaveform: true,
  },
  speaking: {
    gradient: "from-lavender to-amber",
    borderStyle: "border-2 border-chalk-faint/60",
    glow: "shadow-orb",
    animation: "orb-glow",
    showWaveform: true,
  },
  processing: {
    gradient: "from-amber to-amber",
    borderStyle: "border-2 border-chalk-faint/50",
    glow: "shadow-orb-amber",
    animation: "animate-pulse-glow",
    showWaveform: false,
  },
  interrupted: {
    gradient: "from-amber to-amber",
    borderStyle: "border-2 border-amber/50 animate-ping",
    glow: "shadow-orb-amber",
    animation: "",
    showWaveform: false,
  },
  error: {
    gradient: "from-ember to-ember",
    borderStyle: "border-2 border-chalk-faint/30",
    glow: "shadow-orb-ember",
    animation: "",
    showWaveform: false,
  },
};

export function VoiceOrb({
  state,
  size = "xl",
  audioLevel = 0,
  onClick,
  disabled,
  className,
  ...props
}: VoiceOrbProps) {
  const sizeSettings = sizeConfig[size];
  const stateSettings = stateConfig[state];

  const isClickable = !disabled && onClick;

  const orbVariants = useMemo(
    () => ({
      idle: { scale: 1 },
      hover: isClickable ? { scale: 1.05 } : { scale: 1 },
      tap: isClickable ? { scale: 0.95 } : { scale: 1 },
    }),
    [isClickable]
  );

  return (
    <div
      className={cn(
        "relative flex items-center justify-center",
        sizeSettings.container,
        className
      )}
      {...props}
    >
      {/* Main Orb */}
      <motion.div
        variants={orbVariants}
        initial="idle"
        whileHover="hover"
        whileTap="tap"
        onClick={isClickable ? onClick : undefined}
        className={cn(
          "relative rounded-full bg-gradient-to-br backdrop-blur-lg transition-all duration-300",
          sizeSettings.orb,
          stateSettings.gradient,
          stateSettings.borderStyle,
          stateSettings.glow,
          stateSettings.animation,
          isClickable && "cursor-pointer"
        )}
        style={{
          background: `linear-gradient(135deg, var(--tw-gradient-stops))`,
        }}
      >
        {/* Glass overlay */}
        <div className="absolute inset-0 rounded-full bg-white/3 backdrop-blur-sm" />

        {/* Content - either waveform, loading spinner, or icon */}
        <div className="absolute inset-0 flex items-center justify-center">
          {state === "connecting" && (
            <Loader2 className={cn("animate-spin text-chalk", sizeSettings.icon)} />
          )}

          {state === "error" && (
            <MicOff className={cn("text-chalk", sizeSettings.icon)} />
          )}

          {state === "processing" && (
            <div className="flex gap-2">
              <motion.div
                className="h-3 w-3 rounded-full bg-chalk"
                animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1, repeat: Infinity, delay: 0 }}
              />
              <motion.div
                className="h-3 w-3 rounded-full bg-chalk"
                animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1, repeat: Infinity, delay: 0.2 }}
              />
              <motion.div
                className="h-3 w-3 rounded-full bg-chalk"
                animate={{ scale: [1, 1.5, 1], opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1, repeat: Infinity, delay: 0.4 }}
              />
            </div>
          )}

          {stateSettings.showWaveform && (
            <WaveformVisualizer
              amplitude={audioLevel}
              barCount={sizeSettings.waveform.barCount}
              barHeight={sizeSettings.waveform.barHeight}
              barColor="rgba(232, 228, 220, 0.85)"
              animated={state === "speaking" || state === "listening"}
            />
          )}

          {state === "idle" && (
            <div className="text-center">
              <motion.div
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.3 }}
              >
                <p className="text-chalk-soft text-sm font-medium">
                  Tap to connect
                </p>
              </motion.div>
            </div>
          )}
        </div>

        {/* Rotating border for connecting state */}
        {state === "connecting" && (
          <motion.div
            className="absolute inset-0 rounded-full"
            style={{
              background:
                "conic-gradient(from 0deg, transparent 0%, rgba(255,255,255,0.3) 50%, transparent 100%)",
            }}
            animate={{ rotate: 360 }}
            transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
          />
        )}
      </motion.div>
    </div>
  );
}
