"""Authenticated progressive SceneDoc authoring over server-sent events."""

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from murmur.api.dependencies import (
    CurrentUserDependency,
    SceneAuthoringAdmissionDependency,
    SceneAuthoringServiceDependency,
)
from murmur.api.errors import ApiError
from murmur.core.async_cleanup import close_async_resource
from murmur.live_scene import (
    LiveSceneRequest,
    SceneAdmissionError,
    SceneAdmissionLease,
    SceneStreamEvent,
    encode_scene_stream_event,
)

router = APIRouter(prefix="/api/live-scenes", tags=["live-scenes"])


async def _encode_scene_events(
    events: AsyncIterator[SceneStreamEvent],
    lease: SceneAdmissionLease | None = None,
) -> AsyncIterator[str]:
    """Encode canonical camelCase scene events as one SSE data record each."""
    try:
        async for event in events:
            yield encode_scene_stream_event(event)
    finally:
        try:
            await close_async_resource(events)
        finally:
            if lease is not None:
                await lease.aclose()


@router.post("/stream")
async def stream_live_scene(
    body: LiveSceneRequest,
    user: CurrentUserDependency,
    admission: SceneAuthoringAdmissionDependency,
    scene_service: SceneAuthoringServiceDependency,
) -> StreamingResponse:
    """Stream server-authoritative scene revisions for an authenticated caller."""
    try:
        lease = await admission.acquire(user["id"])
    except SceneAdmissionError as exc:
        raise ApiError(429, str(exc)) from None
    events = scene_service.stream_events(body)
    return StreamingResponse(
        _encode_scene_events(events, lease),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "stream_live_scene"]
