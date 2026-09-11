"""Bounded data-only SSE encoding for Gate 1.7 projectile events."""

from __future__ import annotations

import json

from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.projectile_motion_service_contracts import (
    ProjectileChoreographySceneStreamEventV1,
    dump_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.wire import SceneStreamWireError

MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES = MAX_NDJSON_FRAME_BYTES


def encode_projectile_choreography_scene_stream_event(
    event: ProjectileChoreographySceneStreamEventV1,
    *,
    max_event_bytes: int = MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES,
) -> str:
    """Encode one canonical projectile event within the 64 KiB browser budget."""

    if (
        isinstance(max_event_bytes, bool)
        or not isinstance(max_event_bytes, int)
        or not 1 <= max_event_bytes <= MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES
    ):
        raise ValueError(
            "max_event_bytes must be an integer between 1 and "
            f"{MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES}"
        )
    payload = dump_projectile_choreography_scene_stream_event(event)
    encoded = f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    if len(encoded.encode("utf-8")) > max_event_bytes:
        raise SceneStreamWireError(
            "projectile choreography scene stream event exceeded the browser wire budget"
        )
    return encoded


__all__ = [
    "MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES",
    "encode_projectile_choreography_scene_stream_event",
]
