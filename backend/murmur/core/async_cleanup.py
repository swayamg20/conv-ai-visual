"""Bounded cleanup for optional synchronous or asynchronous resources."""

from __future__ import annotations

import asyncio
import inspect

DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS = 0.25


def _consume_task_result(task: asyncio.Future[object]) -> None:
    try:
        task.result()
    except BaseException:
        # Cleanup runs best-effort and must never surface after its owner exits.
        return


async def close_async_resource(
    resource: object | None,
    *,
    timeout_seconds: float = DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
) -> bool:
    """Close a duck-typed resource without letting a stuck closer hang its owner."""

    if resource is None:
        return True
    close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if close is None:
        return True
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    try:
        result = close()
    except Exception:
        return False
    if not inspect.isawaitable(result):
        return True

    task = asyncio.ensure_future(result)
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout_seconds)
    except BaseException:
        task.cancel()
        task.add_done_callback(_consume_task_result)
        raise

    if task in done:
        try:
            task.result()
        except BaseException:
            return False
        return True

    task.cancel()
    task.add_done_callback(_consume_task_result)
    return False
