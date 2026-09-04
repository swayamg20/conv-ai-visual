"""Bounded SSE encoding for the separate semantic live-scene stream."""

from __future__ import annotations

import json

from murmur.live_scene.semantic_service_contracts import (
    SemanticSceneStreamEvent,
    dump_semantic_scene_stream_event,
)
from murmur.live_scene.wire import MAX_SSE_EVENT_BYTES, SceneStreamWireError


def encode_semantic_scene_stream_event(
    event: SemanticSceneStreamEvent,
    *,
    max_event_bytes: int = MAX_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical data-only semantic SSE record within the shared budget."""

    if max_event_bytes <= 0 or max_event_bytes > MAX_SSE_EVENT_BYTES:
        raise ValueError(f"max_event_bytes must be between 1 and {MAX_SSE_EVENT_BYTES}")
    payload = dump_semantic_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError("semantic scene stream event exceeded the browser wire budget")
    return encoded


__all__ = ["encode_semantic_scene_stream_event"]
