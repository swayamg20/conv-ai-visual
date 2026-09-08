"""Bounded SSE encoding for the separate choreography checkpoint stream."""

from __future__ import annotations

import json

from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneStreamEvent,
    dump_choreography_scene_stream_event,
)
from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.wire import SceneStreamWireError

MAX_CHOREOGRAPHY_SSE_EVENT_BYTES = MAX_NDJSON_FRAME_BYTES


def encode_choreography_scene_stream_event(
    event: ChoreographySceneStreamEvent,
    *,
    max_event_bytes: int = MAX_CHOREOGRAPHY_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical data-only checkpoint event within the 64 KiB budget."""

    if max_event_bytes <= 0 or max_event_bytes > MAX_CHOREOGRAPHY_SSE_EVENT_BYTES:
        raise ValueError(
            f"max_event_bytes must be between 1 and {MAX_CHOREOGRAPHY_SSE_EVENT_BYTES}"
        )
    payload = dump_choreography_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError(
            "choreography scene stream event exceeded the browser wire budget"
        )
    return encoded


__all__ = [
    "MAX_CHOREOGRAPHY_SSE_EVENT_BYTES",
    "encode_choreography_scene_stream_event",
]
