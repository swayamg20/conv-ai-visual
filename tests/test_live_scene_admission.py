"""Admission controls for paid live-scene generations."""

from __future__ import annotations

import asyncio

import pytest
from murmur.live_scene import SceneAdmissionError, SceneAuthoringAdmission


@pytest.mark.asyncio
async def test_admission_enforces_per_user_and_global_concurrency() -> None:
    admission = SceneAuthoringAdmission(
        global_limit=2,
        per_user_limit=1,
        requests_per_minute=10,
    )
    first = await admission.acquire("user-a")

    with pytest.raises(SceneAdmissionError, match="already active") as same_user:
        await admission.acquire("user-a")
    assert same_user.value.code == "user_busy"

    second = await admission.acquire("user-b")
    with pytest.raises(SceneAdmissionError, match="busy") as global_capacity:
        await admission.acquire("user-c")
    assert global_capacity.value.code == "capacity_reached"

    await first.aclose()
    replacement = await admission.acquire("user-c")
    await replacement.aclose()
    await second.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_admission_applies_a_per_user_rolling_rate_limit() -> None:
    now = 100.0
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=2,
        clock=lambda: now,
    )

    for _ in range(2):
        lease = await admission.acquire("user-a")
        await lease.aclose()
    with pytest.raises(SceneAdmissionError, match="Too many") as limited:
        await admission.acquire("user-a")
    assert limited.value.code == "rate_limited"

    now += 60.0
    lease = await admission.acquire("user-a")
    await lease.aclose()


@pytest.mark.asyncio
async def test_cancelled_lease_close_can_be_retried_without_leaking_capacity() -> None:
    admission = SceneAuthoringAdmission(
        global_limit=1,
        per_user_limit=1,
        requests_per_minute=10,
    )
    lease = await admission.acquire("user-a")
    await admission._lock.acquire()
    closing = asyncio.create_task(lease.aclose())
    await asyncio.sleep(0)

    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    admission._lock.release()

    await asyncio.gather(lease.aclose(), lease.aclose())
    replacement = await admission.acquire("user-a")
    await replacement.aclose()
