"use client";

import { useEffect, useRef, useCallback } from "react";
import { useMicVAD, utils } from "@ricky0123/vad-react";

/**
 * Speed-optimized VAD hook for instant interruption detection
 *
 * Uses Silero VAD (WebAssembly) for accurate, low-latency speech detection
 * Target latency: 10-30ms detection
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
   * Minimum number of speech frames before triggering
   * Default: 3 (for ~30-50ms confirmation)
   * - Lower = faster but more false positives
   * - Higher = more reliable but slower
   */
  minSpeechFrames?: number;

  /**
   * Frames of silence before considering speech ended
   * Default: 8
   */
  redemptionFrames?: number;

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
    minSpeechFrames = 3,
    redemptionFrames = 8,
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
        console.log(`[VAD] Debouncing (${timeSinceLastTrigger}ms since last trigger)`);
      }
      return;
    }

    if (debug) {
      console.log("[VAD] 🎤 Speech detected - triggering interrupt");
    }

    hasTriggeredRef.current = true;
    lastSpeechTimeRef.current = now;
    onSpeechDetected();
  }, [onSpeechDetected, debug]);

  const handleSpeechEnd = useCallback(() => {
    if (debug) {
      console.log("[VAD] 🔇 Speech ended");
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

    // Speed-optimized thresholds
    positiveSpeechThreshold,
    negativeSpeechThreshold,
    minSpeechFrames,
    redemptionFrames,

    // Optimize for low latency
    preSpeechPadFrames: 1,  // Minimal pre-padding for speed

    // Model loading
    modelURL: undefined,  // Use default CDN model
    workletURL: undefined, // Use default CDN worklet

    // Error handling
    onVADMisfire: () => {
      if (debug) {
        console.log("[VAD] ⚠️ Misfire detected");
      }
    },

    onError: (error) => {
      console.error("[VAD] ❌ Error:", error);
    },
  });

  // Control VAD based on enabled state
  useEffect(() => {
    if (!vad) return;

    if (enabled && !vad.listening) {
      if (debug) {
        console.log("[VAD] ▶️ Starting VAD (monitoring for speech)");
      }
      vad.start();
    } else if (!enabled && vad.listening) {
      if (debug) {
        console.log("[VAD] ⏸️ Pausing VAD");
      }
      vad.pause();
      // Reset state when stopping
      hasTriggeredRef.current = false;
    }
  }, [enabled, vad, debug]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (vad?.listening) {
        vad.pause();
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
    minSpeechFrames: 1,
    redemptionFrames: 5,
  },

  // Balanced: Good speed and accuracy
  // Recommended starting point
  balanced: {
    positiveSpeechThreshold: 0.6,
    minSpeechFrames: 3,
    redemptionFrames: 8,
  },

  // Conservative: Prioritize accuracy over speed
  // Best for noisy environments
  conservative: {
    positiveSpeechThreshold: 0.75,
    minSpeechFrames: 5,
    redemptionFrames: 10,
  },
};
