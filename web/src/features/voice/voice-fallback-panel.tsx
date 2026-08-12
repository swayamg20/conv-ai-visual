"use client";

import type { VoiceUnavailableReason } from "./session-machine";

interface VoiceFallbackPanelProps {
  readonly reason: VoiceUnavailableReason;
  readonly canRetry: boolean;
  readonly onRetry: () => void;
  readonly onContinueInText: () => void;
}

/** Keeps Voice V2 failures visible without trapping the user in voice mode. */
export function VoiceFallbackPanel({
  reason,
  canRetry,
  onRetry,
  onContinueInText,
}: VoiceFallbackPanelProps) {
  return (
    <div
      role="alert"
      className="w-full max-w-md rounded-2xl border border-ember/30 bg-ember/10 p-4 text-left"
    >
      <p className="text-sm font-semibold text-foreground">Voice is unavailable</p>
      <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
        {reason.message}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {canRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-full bg-foreground px-4 py-2 text-sm font-medium text-background transition-opacity hover:opacity-90"
          >
            Retry voice
          </button>
        )}
        <button
          type="button"
          onClick={onContinueInText}
          className="rounded-full border border-chalk-faint/50 px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-graphite"
        >
          Continue in text
        </button>
      </div>
    </div>
  );
}
