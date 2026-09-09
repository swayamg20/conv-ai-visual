"""Bounded SSE encoding for Gate 1.6 parametric choreography events."""

from __future__ import annotations

import json

from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.parametric_choreography_service_contracts import (
    ParametricChoreographySceneStreamEventV3,
    dump_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.wire import SceneStreamWireError

MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES = MAX_NDJSON_FRAME_BYTES


def encode_parametric_choreography_scene_stream_event(
    event: ParametricChoreographySceneStreamEventV3,
    *,
    max_event_bytes: int = MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical data-only V3 event within the 64 KiB budget."""

    if (
        isinstance(max_event_bytes, bool)
        or not isinstance(max_event_bytes, int)
        or not 1 <= max_event_bytes <= MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES
    ):
        raise ValueError(
            "max_event_bytes must be an integer between 1 and "
            f"{MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES}"
        )
    payload = dump_parametric_choreography_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError(
            "parametric choreography scene stream event exceeded the browser wire budget"
        )
    return encoded


__all__ = [
    "MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES",
    "encode_parametric_choreography_scene_stream_event",
]
