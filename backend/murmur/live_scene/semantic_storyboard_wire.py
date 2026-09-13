"""Bounded data-only SSE encoding for Gate 1.8 storyboard events."""

from __future__ import annotations

import json

from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SemanticStoryboardSceneStreamEventV1,
    dump_semantic_storyboard_scene_stream_event,
)
from murmur.live_scene.wire import SceneStreamWireError

MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES = MAX_NDJSON_FRAME_BYTES


def encode_semantic_storyboard_scene_stream_event(
    event: SemanticStoryboardSceneStreamEventV1,
    *,
    max_event_bytes: int = MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical storyboard event within the 64 KiB browser budget."""

    if (
        isinstance(max_event_bytes, bool)
        or not isinstance(max_event_bytes, int)
        or not 1 <= max_event_bytes <= MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES
    ):
        raise ValueError(
            "max_event_bytes must be an integer between 1 and "
            f"{MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES}"
        )
    payload = dump_semantic_storyboard_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError(
            "semantic storyboard scene stream event exceeded the browser wire budget"
        )
    return encoded


__all__ = [
    "MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES",
    "encode_semantic_storyboard_scene_stream_event",
]
