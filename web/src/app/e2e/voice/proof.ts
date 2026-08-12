export interface TimedAudioSample {
  readonly t_ms: number;
  readonly rms: number;
}

export interface InterruptionAttribution {
  readonly guard_start_ms: number;
  readonly guard_end_ms: number;
  readonly observation_complete: boolean;
  readonly stale_audio_detected: boolean;
}

interface InterruptionAttributionInput {
  readonly samples: readonly TimedAudioSample[];
  readonly silenceStartMs: number;
  readonly nextAssistantSpeechStartMs: number;
  readonly activeRms: number;
  readonly requiredSilenceMs: number;
  readonly samplingToleranceMs: number;
}

/**
 * Attributes PCM after a canonical assistant_speech_started boundary to the
 * next reply. Before that boundary, any post-interruption active PCM is stale
 * output from the interrupted reply. A small tolerance covers analyser/event
 * sampling skew without allowing an unbounded quiet-period shortcut.
 */
export function interruptionAttribution({
  samples,
  silenceStartMs,
  nextAssistantSpeechStartMs,
  activeRms,
  requiredSilenceMs,
  samplingToleranceMs,
}: InterruptionAttributionInput): InterruptionAttribution {
  const guardStartMs = silenceStartMs + requiredSilenceMs;
  const guardEndMs = Math.max(
    guardStartMs,
    nextAssistantSpeechStartMs - samplingToleranceMs
  );
  return {
    guard_start_ms: guardStartMs,
    guard_end_ms: guardEndMs,
    observation_complete: samples.some(
      (sample) => sample.t_ms >= nextAssistantSpeechStartMs + samplingToleranceMs
    ),
    stale_audio_detected: samples.some(
      (sample) =>
        sample.t_ms >= guardStartMs &&
        sample.t_ms < guardEndMs &&
        sample.rms >= activeRms
    ),
  };
}
