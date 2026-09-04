"""Progressive SceneDoc authoring over server-sent events."""

import os
from collections.abc import AsyncIterator
from ipaddress import IPv4Address, ip_address

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from murmur.api.dependencies import (
    CurrentUserDependency,
    SceneAuthoringAdmissionDependency,
    SceneAuthoringServiceDependency,
)
from murmur.api.errors import ApiError
from murmur.core.async_cleanup import close_async_resource
from murmur.core.config import config
from murmur.live_scene import (
    LiveSceneRequest,
    SceneAdmissionError,
    SceneAdmissionLease,
    SceneStreamEvent,
    encode_scene_stream_event,
)
from murmur.live_scene.semantic_service_contracts import (
    SemanticLiveSceneRequest,
    SemanticSceneStreamEvent,
)
from murmur.live_scene.semantic_wire import encode_semantic_scene_stream_event

router = APIRouter(prefix="/api/live-scenes", tags=["live-scenes"])

_DEVELOPMENT_SCENE_LAB_IDENTITY = "live-scene-lab-development"


def _require_development_scene_lab(request: Request) -> None:
    """Hide the auth-free lab route unless both server-side guards are active."""
    environment = getattr(config, "MURMUR_ENVIRONMENT", "")
    try:
        peer_address = ip_address(request.client.host if request.client else "")
        if peer_address.version == 6 and peer_address.ipv4_mapped is not None:
            peer_address = IPv4Address(peer_address.ipv4_mapped)
        is_loopback = peer_address.is_loopback
    except ValueError:
        is_loopback = False
    if os.getenv("MURMUR_SCENE_LAB") != "1" or environment != "development" or not is_loopback:
        raise ApiError(404, "Not found")


class _OwnedStreamingResponse(StreamingResponse):
    """Release admission even when the client disconnects before streaming starts."""

    def __init__(
        self,
        content: AsyncIterator[str],
        *,
        admission_lease: SceneAdmissionLease,
        media_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(content, media_type=media_type, headers=headers)
        self._admission_lease = admission_lease

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await close_async_resource(self.body_iterator)
            finally:
                await self._admission_lease.aclose()


async def _encode_scene_events(
    events: AsyncIterator[SceneStreamEvent],
) -> AsyncIterator[str]:
    """Encode canonical camelCase scene events as one SSE data record each."""
    try:
        async for event in events:
            yield encode_scene_stream_event(event)
    finally:
        await close_async_resource(events)


async def _encode_semantic_scene_events(
    events: AsyncIterator[SemanticSceneStreamEvent],
) -> AsyncIterator[str]:
    """Encode strict semantic events and release every owned upstream resource."""
    try:
        async for event in events:
            yield encode_semantic_scene_stream_event(event)
    finally:
        await close_async_resource(events)


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
    return _OwnedStreamingResponse(
        _encode_scene_events(events),
        admission_lease=lease,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/lab/stream",
    include_in_schema=False,
    dependencies=[Depends(_require_development_scene_lab)],
)
async def stream_development_live_scene(
    body: LiveSceneRequest,
    admission: SceneAuthoringAdmissionDependency,
    scene_service: SceneAuthoringServiceDependency,
) -> StreamingResponse:
    """Stream through the real provider for the explicitly enabled development lab."""
    try:
        lease = await admission.acquire(_DEVELOPMENT_SCENE_LAB_IDENTITY)
    except SceneAdmissionError as exc:
        raise ApiError(429, str(exc)) from None
    events = scene_service.stream_events(body)
    return _OwnedStreamingResponse(
        _encode_scene_events(events),
        admission_lease=lease,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/lab/semantic/stream",
    include_in_schema=False,
    dependencies=[Depends(_require_development_scene_lab)],
)
async def stream_development_semantic_live_scene(
    body: SemanticLiveSceneRequest,
    admission: SceneAuthoringAdmissionDependency,
    scene_service: SceneAuthoringServiceDependency,
) -> StreamingResponse:
    """Stream compiler-verified semantic atoms in the guarded development lab."""
    try:
        lease = await admission.acquire(_DEVELOPMENT_SCENE_LAB_IDENTITY)
    except SceneAdmissionError as exc:
        raise ApiError(429, str(exc)) from None
    events = scene_service.stream_routed_semantic_events(body)
    return _OwnedStreamingResponse(
        _encode_semantic_scene_events(events),
        admission_lease=lease,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "router",
    "stream_development_live_scene",
    "stream_development_semantic_live_scene",
    "stream_live_scene",
]
