"""Authenticated progressive SceneDoc authoring over server-sent events."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from murmur.api.dependencies import CurrentUserDependency, SceneAuthoringServiceDependency
from murmur.live_scene import (
    LiveSceneRequest,
    SceneStreamEvent,
    dump_scene_stream_event,
)

router = APIRouter(prefix="/api/live-scenes", tags=["live-scenes"])


async def _encode_scene_events(events: AsyncIterator[SceneStreamEvent]) -> AsyncIterator[str]:
    """Encode canonical camelCase scene events as one SSE data record each."""
    async for event in events:
        payload = dump_scene_stream_event(event)
        yield f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/stream")
async def stream_live_scene(
    body: LiveSceneRequest,
    _user: CurrentUserDependency,
    scene_service: SceneAuthoringServiceDependency,
) -> StreamingResponse:
    """Stream server-authoritative scene revisions for an authenticated caller."""
    events = scene_service.stream_events(body)
    return StreamingResponse(
        _encode_scene_events(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "stream_live_scene"]
