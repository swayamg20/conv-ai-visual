"""Cancellation and timeout contracts for bounded resource cleanup."""

from __future__ import annotations

import asyncio
import time

import pytest
from murmur.core.async_cleanup import close_async_resource


class _CancellationResistantCloser:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()


@pytest.mark.asyncio
async def test_blocking_closer_cannot_outlive_the_callers_deadline() -> None:
    resource = _CancellationResistantCloser()
    started_at = time.perf_counter()

    closed = await close_async_resource(resource, timeout_seconds=0.01)

    assert closed is False
    assert resource.started.is_set()
    assert time.perf_counter() - started_at < 0.1
    resource.release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_replaced_by_cleanup() -> None:
    resource = _CancellationResistantCloser()
    cleanup = asyncio.create_task(close_async_resource(resource, timeout_seconds=1.0))
    await resource.started.wait()

    cleanup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cleanup

    resource.release.set()
    await asyncio.sleep(0)
