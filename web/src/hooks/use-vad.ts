"use client";

import { useEffect, useRef, useCallback } from "react";
import { useMicVAD } from "@ricky0123/vad-react";

/**
 * Speed-optimized VAD hook for instant interruption detection
 *
 * Uses Silero VAD (WebAssembly) for accurate, low-latency speech detection
 * Runs only during assistant playback so speech can interrupt the active turn.
 */

export interface UseVADOptions {
  /**
   * Enable/disable VAD processing
   * Tip: Only enable when state === "speaking" to save CPU
   */
  enabled: boolean;

  /**
   * Callback when speech is detected
   * This fires immediately when VAD detects speech above threshold
   */
  onSpeechDetected: () => void;

  /**
   * Speech probability threshold (0-1)
   * Default: 0.6 (60% confidence)
   * - Lower = more sensitive (may have false positives)
   * - Higher = more conservative (may miss soft speech)
   */
  positiveSpeechThreshold?: number;

  /**
   * Negative speech threshold for end detection (0-1)
   * Default: 0.5
   */
  negativeSpeechThreshold?: number;

  /**
   * Silence after speech before the detector resets, in milliseconds.
   */
  redemptionMs?: number;

  /**
   * Show debug logs
   */
  debug?: boolean;
}

export interface VADState {
  isLoading: boolean;
  isListening: boolean;
  userSpeaking: boolean;
}

export function useVAD(options: UseVADOptions) {
  const {
    enabled,
    onSpeechDetected,
    positiveSpeechThreshold = 0.6,
    negativeSpeechThreshold = 0.5,
    redemptionMs = 500,
    debug = false,
  } = options;

  const hasTriggeredRef = useRef(false);
  const lastSpeechTimeRef = useRef(0);

  // Debounce: Don't re-trigger within 300ms
  const DEBOUNCE_MS = 300;

  const handleSpeechStart = useCallback(() => {
    const now = Date.now();
    const timeSinceLastTrigger = now - lastSpeechTimeRef.current;

    // Debounce check
    if (hasTriggeredRef.current && timeSinceLastTrigger < DEBOUNCE_MS) {
      if (debug) {
        console.debug(`[VAD] Debouncing (${timeSinceLastTrigger}ms since last trigger)`);
      }
      return;
    }

    if (debug) {
      console.debug("[VAD] Speech detected - triggering interrupt");
    }

    hasTriggeredRef.current = true;
    lastSpeechTimeRef.current = now;
    onSpeechDetected();
  }, [onSpeechDetected, debug]);

  const handleSpeechEnd = useCallback(() => {
    if (debug) {
      console.debug("[VAD] Speech ended");
    }
    // Reset trigger flag when speech ends
    hasTriggeredRef.current = false;
  }, [debug]);

  // Initialize VAD with optimal settings for speed
  const vad = useMicVAD({
    // Only start when enabled (e.g., during TTS playback)
    startOnLoad: false,

    // Callbacks
    onSpeechStart: handleSpeechStart,
    onSpeechEnd: handleSpeechEnd,

    positiveSpeechThreshold,
    negativeSpeechThreshold,
    redemptionMs,

    // Error handling
    onVADMisfire: () => {
      if (debug) {
        console.debug("[VAD] Misfire detected");
      }
    },
  });

  // Control VAD based on enabled state
  useEffect(() => {
    if (!vad) return;

    if (enabled && !vad.listening) {
      if (debug) {
        console.debug("[VAD] Starting interruption monitoring");
      }
      void vad.start();
    } else if (!enabled && vad.listening) {
      if (debug) {
        console.debug("[VAD] Pausing interruption monitoring");
      }
      void vad.pause();
      // Reset state when stopping
      hasTriggeredRef.current = false;
    }
  }, [enabled, vad, debug]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (vad?.listening) {
        void vad.pause();
      }
    };
  }, [vad]);

  return {
    isLoading: vad.loading,
    isListening: vad.listening,
    userSpeaking: vad.userSpeaking,
    pause: vad.pause,
    start: vad.start,
  };
}

/**
 * Default configuration presets
 */
export const VAD_PRESETS = {
  // Ultra-fast: Prioritize speed over accuracy
  // Best for quiet environments
  ultraFast: {
    positiveSpeechThreshold: 0.5,
    redemptionMs: 300,
  },

  // Balanced: Good speed and accuracy
  // Recommended starting point
  balanced: {
    positiveSpeechThreshold: 0.6,
    redemptionMs: 500,
  },

  // Conservative: Prioritize accuracy over speed
  // Best for noisy environments
  conservative: {
    positiveSpeechThreshold: 0.75,
    redemptionMs: 800,
  },
};
