"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

/**
 * Hand-drawn SVG decorative elements for the Murmur brand.
 * Sketchy waveforms, math doodles, and geometric shapes
 * that evoke the "voice becomes a drawing" identity.
 */

const draw = {
  hidden: { pathLength: 0, opacity: 0 },
  visible: (i: number) => ({
    pathLength: 1,
    opacity: 1,
    transition: {
      pathLength: { delay: i * 0.15, duration: 1.2, ease: "easeInOut" },
      opacity: { delay: i * 0.15, duration: 0.3 },
    },
  }),
};

/** Small waveform logo mark for the nav bar */
export function MurmurLogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      className={cn("h-7 w-7", className)}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* Hand-drawn waveform — three bars with sketchy imperfection */}
      <motion.path
        d="M6 20 C6.2 19.8 6 12 6.3 12 C6.5 12.2 6.8 20 7 20"
        stroke="hsl(var(--amber))"
        strokeWidth="1.8"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.8, ease: "easeOut" }}
      />
      <motion.path
        d="M11 22 C11.1 21.8 10.9 7 11.2 7 C11.4 7.1 11.6 22 11.8 22"
        stroke="hsl(var(--amber))"
        strokeWidth="1.8"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.8, delay: 0.1, ease: "easeOut" }}
      />
      <motion.path
        d="M16 18 C16.2 17.9 16 10 16.3 10 C16.5 10.1 16.7 18 16.9 18"
        stroke="hsl(var(--amber))"
        strokeWidth="1.8"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.8, delay: 0.2, ease: "easeOut" }}
      />
      {/* Arrow morphing into a sketchy circle (voice → drawing) */}
      <motion.path
        d="M20 16 L24 16 L22.5 14"
        stroke="hsl(var(--chalk-soft))"
        strokeWidth="1.2"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.6, delay: 0.5, ease: "easeOut" }}
      />
      <motion.circle
        cx="27"
        cy="16"
        r="3.5"
        stroke="hsl(var(--lavender))"
        strokeWidth="1.2"
        fill="none"
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 0.8, delay: 0.7, ease: "easeOut" }}
        style={{ strokeDasharray: "1 0.5" }}
      />
    </svg>
  );
}

/** Decorative waveform-to-sketch illustration for empty/idle states */
export function WaveformToSketch({ className }: { className?: string }) {
  return (
    <motion.svg
      viewBox="0 0 280 120"
      fill="none"
      className={cn("w-full max-w-[280px]", className)}
      strokeLinecap="round"
      strokeLinejoin="round"
      initial="hidden"
      animate="visible"
    >
      {/* Sound waveform (left side) */}
      <motion.path
        d="M20 60 Q25 30 30 60 Q35 90 40 60"
        stroke="hsl(var(--amber))"
        strokeWidth="2"
        custom={0}
        variants={draw}
      />
      <motion.path
        d="M45 60 Q50 20 55 60 Q60 100 65 60"
        stroke="hsl(var(--amber))"
        strokeWidth="2"
        custom={1}
        variants={draw}
      />
      <motion.path
        d="M70 60 Q75 35 80 60 Q85 85 90 60"
        stroke="hsl(var(--amber))"
        strokeWidth="2"
        custom={2}
        variants={draw}
      />

      {/* Transition dots / particles */}
      {[105, 115, 125].map((x, i) => (
        <motion.circle
          key={x}
          cx={x}
          cy={60}
          r="2"
          fill="hsl(var(--chalk-soft))"
          custom={3 + i}
          variants={{
            hidden: { opacity: 0, scale: 0 },
            visible: (j: number) => ({
              opacity: [0, 1, 0.5],
              scale: 1,
              transition: { delay: j * 0.15, duration: 0.4 },
            }),
          }}
        />
      ))}

      {/* Sketchy geometric shapes (right side — what the voice draws) */}
      {/* Triangle */}
      <motion.path
        d="M145 85 L160 40 L178 85 Z"
        stroke="hsl(var(--lavender))"
        strokeWidth="1.8"
        fill="none"
        custom={6}
        variants={draw}
        style={{ strokeDasharray: "4 2" }}
      />

      {/* Circle */}
      <motion.circle
        cx="210"
        cy="60"
        r="22"
        stroke="hsl(var(--sage))"
        strokeWidth="1.8"
        fill="none"
        custom={7}
        variants={draw}
      />

      {/* Square with rough edges */}
      <motion.path
        d="M242 42 L262 41.5 L263 82 L241.5 82.5 Z"
        stroke="hsl(var(--chalk))"
        strokeWidth="1.6"
        fill="none"
        custom={8}
        variants={draw}
      />

      {/* Tiny decorative math symbols */}
      <motion.text
        x="155"
        y="30"
        fill="hsl(var(--chalk-faint))"
        fontSize="10"
        fontFamily="var(--font-handwriting)"
        custom={9}
        variants={{
          hidden: { opacity: 0 },
          visible: (i: number) => ({
            opacity: 0.5,
            transition: { delay: i * 0.15, duration: 0.6 },
          }),
        }}
      >
        x²
      </motion.text>
      <motion.text
        x="230"
        y="100"
        fill="hsl(var(--chalk-faint))"
        fontSize="10"
        fontFamily="var(--font-handwriting)"
        custom={10}
        variants={{
          hidden: { opacity: 0 },
          visible: (i: number) => ({
            opacity: 0.5,
            transition: { delay: i * 0.15, duration: 0.6 },
          }),
        }}
      >
        π
      </motion.text>
    </motion.svg>
  );
}

/** Background doodle accents — math-themed sketches that float behind content */
export function BackgroundDoodles({ className }: { className?: string }) {
  return (
    <motion.svg
      viewBox="0 0 400 300"
      fill="none"
      className={cn("absolute inset-0 w-full h-full pointer-events-none", className)}
      strokeLinecap="round"
      strokeLinejoin="round"
      initial="hidden"
      animate="visible"
    >
      {/* Scattered math doodles at low opacity */}
      {/* Sine wave fragment — top right */}
      <motion.path
        d="M280 50 Q300 20 320 50 Q340 80 360 50"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="1"
        opacity="0.3"
        custom={0}
        variants={draw}
      />

      {/* Small circle — bottom left */}
      <motion.circle
        cx="60"
        cy="230"
        r="12"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.2"
        custom={2}
        variants={draw}
      />

      {/* Little angle mark — top left */}
      <motion.path
        d="M40 60 L60 60 L50 42"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.2"
        custom={3}
        variants={draw}
      />

      {/* Plus sign */}
      <motion.path
        d="M340 220 L340 240 M330 230 L350 230"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.2"
        custom={4}
        variants={draw}
      />

      {/* Equals sign */}
      <motion.path
        d="M80 100 L100 100 M80 106 L100 106"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.15"
        custom={5}
        variants={draw}
      />

      {/* Integral curve */}
      <motion.path
        d="M320 120 C315 130 325 150 320 160"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.2"
        custom={6}
        variants={draw}
      />

      {/* Arrow */}
      <motion.path
        d="M150 250 L190 250 L185 246 M190 250 L185 254"
        stroke="hsl(var(--chalk-faint))"
        strokeWidth="0.8"
        opacity="0.15"
        custom={7}
        variants={draw}
      />
    </motion.svg>
  );
}
