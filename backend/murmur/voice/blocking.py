"""Bounded offloading for synchronous Voice V2 repository reads."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

_Result = TypeVar("_Result")


class BoundedSyncRunnerUnavailable(RuntimeError):
    """No bounded sync-runner slot is available or the runner is closed."""


class BoundedSyncRunner:
    """Run at most ``max_workers`` sync calls, including calls past async timeout.

    A timed-out await cannot cancel a Python function already running in a thread.
    Admission therefore remains occupied until the underlying concurrent future
    finishes, preventing repeated timeouts from creating an unbounded work queue.
    """

    def __init__(self, max_workers: int = 8, *, thread_name_prefix: str = "voice-v2-repo") -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("bounded sync runner max_workers must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._capacity = threading.BoundedSemaphore(max_workers)
        self._guard = threading.Lock()
        self._closed = False
        self._inflight = 0

    async def run(
        self,
        function: Callable[..., _Result],
        *args: object,
        timeout_seconds: float,
    ) -> _Result:
        if not self._capacity.acquire(blocking=False):
            raise BoundedSyncRunnerUnavailable("bounded sync runner capacity is exhausted")

        with self._guard:
            if self._closed:
                self._capacity.release()
                raise BoundedSyncRunnerUnavailable("bounded sync runner is closed")
            self._inflight += 1

        try:
            future = self._executor.submit(function, *args)
        except BaseException:
            self._release_slot()
            raise
        future.add_done_callback(self._on_done)

        # Shielding is essential: timeout/caller cancellation must not cancel a
        # not-yet-started concurrent future and release admission prematurely.
        wrapped = asyncio.wrap_future(future)
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout_seconds)

    def _on_done(self, _future: Future[object]) -> None:
        self._release_slot()

    def _release_slot(self) -> None:
        with self._guard:
            self._inflight -= 1
        self._capacity.release()

    @property
    def inflight_count(self) -> int:
        with self._guard:
            return self._inflight

    async def aclose(self) -> None:
        """Stop accepting work and join all admitted calls without blocking the loop."""
        with self._guard:
            if self._closed:
                return
            self._closed = True
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)


default_repository_runner = BoundedSyncRunner()
