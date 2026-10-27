import type { TranscriptDeltaEvent } from "cosmo-ai";

import type { Transcript } from "./contracts";

export function foldTranscript(
  items: readonly Transcript[],
  event: TranscriptDeltaEvent,
): readonly Transcript[] {
  // Match Cosmo 0.6.0's transcript reducer: finality owns each speaker's
  // bubble. turnId resets on the OTHER speaker's completion, and append
  // changes when roles interleave, so neither safely identifies an utterance.
  const previous = items.findLast((item) => item.role === event.role && !item.final);
  if (!previous && !event.text.trim()) return items;
  const id = previous?.id ?? `${event.role}:${event.id}`;
  const text = event.isFinal ? event.text : (previous?.text ?? "") + event.text;
  const next: Transcript = {
    id,
    role: event.role,
    text: text.slice(-8000),
    final: event.isFinal,
  };
  return (
    previous
      ? items.map((item) => (item.id === id ? next : item))
      : [...items, next]
  ).slice(-80);
}
