"""ASGI streaming responses that own and release admission capacity."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi.responses import StreamingResponse

from murmur.core.async_cleanup import close_async_resource


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


class OwnedStreamingResponse(StreamingResponse):
    """Close the body and release admission across completion, failure, or disconnect."""

    def __init__(
        self,
        content: AsyncIterator[str],
        *,
        admission_lease: AsyncCloseable,
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
                # A client disconnect can cancel the response task. Shield the
                # admission release so capacity cannot remain charged to that user.
                release = asyncio.create_task(self._admission_lease.aclose())
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    release.add_done_callback(_consume_release_result)
                    raise


def _consume_release_result(task: asyncio.Future[None]) -> None:
    """Retrieve a shielded release result after its response task was cancelled."""

    try:
        task.result()
    except BaseException:
        return


__all__ = ["OwnedStreamingResponse"]
