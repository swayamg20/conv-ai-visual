"""Canonical bounded SSE encoding for live-scene lifecycle events."""

from __future__ import annotations

import json

from murmur.live_scene.contracts import SceneStreamEvent, dump_scene_stream_event

# Model-authored NDJSON is limited separately to 64 KiB. The larger wire budget
# leaves room for authoritative lifecycle metadata and canonical number encoding.
MAX_SSE_EVENT_BYTES = 96 * 1024


class SceneStreamWireError(ValueError):
    """Raised before an event can cross the browser's bounded SSE boundary."""


def encode_scene_stream_event(
    event: SceneStreamEvent,
    *,
    max_event_bytes: int = MAX_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical data-only SSE record and enforce its UTF-8 budget."""

    if max_event_bytes <= 0 or max_event_bytes > MAX_SSE_EVENT_BYTES:
        raise ValueError(f"max_event_bytes must be between 1 and {MAX_SSE_EVENT_BYTES}")
    payload = dump_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError("scene stream event exceeded the browser wire budget")
    return encoded


__all__ = [
    "MAX_SSE_EVENT_BYTES",
    "SceneStreamWireError",
    "encode_scene_stream_event",
]
