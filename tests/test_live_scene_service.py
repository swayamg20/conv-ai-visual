"""Focused tests for bounded live-scene model orchestration."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from murmur.live_scene.contracts import (
    LiveSceneRequest,
    ScenePatchEvent,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.service import SceneAuthoringService

_BLOCK = object()


def _presentation(enter: str = "draw") -> dict[str, str]:
    return {"enter": enter, "exit": "fade"}


def _line(node_id: str, *, end_x: int = 200) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "line",
        "presentation": _presentation(),
        "points": [[10, 20], [end_x, 220]],
        "style": {
            "stroke": "hsl(var(--lavender))",
            "strokeWidth": 3,
            "opacity": 1,
            "roughness": 0.5,
        },
    }


def _put(node: dict[str, object]) -> dict[str, object]:
    return {"op": "put", "node": node}


def _remove(node_id: str) -> dict[str, object]:
    return {"op": "remove", "id": node_id}


def _patch_line(
    patch_id: str,
    *operations: dict[str, object],
    narration: str = "Draw the next idea.",
) -> str:
    return json.dumps(
        {
            "v": 1,
            "patchId": patch_id,
            "narration": narration,
            "operations": list(operations),
        },
        separators=(",", ":"),
    )


def _request(
    *,
    revision: int = 0,
    nodes: list[dict[str, object]] | None = None,
    generation: int = 11,
) -> LiveSceneRequest:
    return LiveSceneRequest.model_validate(
        {
            "prompt": "Teach this progressively with a diagram.",
            "generation": generation,
            "baseScene": {"revision": revision, "nodes": nodes or []},
        }
    )


class _FakeClock:
    def __init__(self, *values: float) -> None:
        self._values = values
        self.calls = 0

    def __call__(self) -> float:
        index = min(self.calls, len(self._values) - 1)
        self.calls += 1
        return self._values[index]


class _TrackedStream:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)
        self.closed = False
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> _TrackedStream:
        return self

    async def __anext__(self) -> str | bytes:
        if self.closed or not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if item is _BLOCK:
            self.waiting.set()
            await self.release.wait()
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        await asyncio.sleep(0)
        assert isinstance(item, str | bytes)
        return item

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


class _FakeClient:
    def __init__(self, attempts: list[list[object]]) -> None:
        self._attempts = attempts
        self.calls: list[dict[str, object]] = []
        self.streams: list[_TrackedStream] = []
        self.stream_created = asyncio.Event()

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> _TrackedStream:
        stream = _TrackedStream(self._attempts[len(self.calls)])
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        self.streams.append(stream)
        self.stream_created.set()
        return stream


async def _collect(
    service: SceneAuthoringService,
    request: LiveSceneRequest | None = None,
) -> list[object]:
    return [event async for event in service.stream_events(request or _request())]


def _repair_snapshot(client: _FakeClient) -> dict[str, object]:
    messages = client.calls[1]["messages"]
    assert isinstance(messages, list)
    user = messages[1]["content"]
    snapshot = user.split("LAST_ACCEPTED_SCENE_JSON:\n", 1)[1].split("\nOUTPUT_NDJSON_NOW:", 1)[0]
    return json.loads(snapshot)


@pytest.mark.asyncio
async def test_streams_fragmented_and_multiple_frames_with_authoritative_metadata() -> None:
    first = _patch_line("patch-a", _put(_line("side-a")))
    second = _patch_line("patch-b", _put(_line("side-b")))
    chunks = [first[:23], first[23:] + "\n" + second[:17], second[17:]]
    client = _FakeClient([chunks])
    clock = _FakeClock(10.0, 10.05, 10.30)
    service = SceneAuthoringService(client, clock=clock)

    events = await _collect(service, _request(revision=4))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_patch",
        "scene_patch",
        "scene_stream_completed",
    ]
    assert isinstance(events[0], SceneStreamStartedEvent)
    patches = [event for event in events if isinstance(event, ScenePatchEvent)]
    assert [(event.attempt, event.sequence) for event in patches] == [(1, 1), (1, 2)]
    assert [(event.base_revision, event.result_revision) for event in patches] == [(4, 5), (5, 6)]
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.final_revision == 6
    assert completed.patch_count == 2
    assert completed.first_patch_ms == pytest.approx(50.0)
    assert completed.total_ms == pytest.approx(300.0)
    assert completed.repaired is False
    assert client.calls[0]["temperature"] == 0.2
    assert client.calls[0]["max_tokens"] == 4_096
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_repairs_from_partial_snapshot_with_stable_node_order_and_remaining_budget() -> None:
    initial_patch = _patch_line(
        "patch-replace-and-add",
        _put(_line("node-b", end_x=320)),
        _put(_line("node-c")),
    )
    repaired_patch = _patch_line("patch-remove", _remove("node-a"))
    client = _FakeClient(
        [
            [initial_patch + "\nthis is invalid\n"],
            [repaired_patch],
        ]
    )
    service = SceneAuthoringService(client, clock=_FakeClock(0.0, 0.1, 0.4))

    events = await _collect(
        service,
        _request(revision=7, nodes=[_line("node-a"), _line("node-b")]),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_patch",
        "scene_stream_repairing",
        "scene_patch",
        "scene_stream_completed",
    ]
    patches = [event for event in events if isinstance(event, ScenePatchEvent)]
    assert [(event.attempt, event.sequence) for event in patches] == [(1, 1), (2, 2)]
    assert [(event.base_revision, event.result_revision) for event in patches] == [(7, 8), (8, 9)]
    repairing = events[2]
    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert repairing.last_accepted_revision == 8

    snapshot = _repair_snapshot(client)
    nodes = snapshot["nodes"]
    assert isinstance(nodes, list)
    assert [node["id"] for node in nodes] == ["node-a", "node-b", "node-c"]
    assert nodes[1]["points"][1][0] == 320.0
    repair_user = client.calls[1]["messages"][1]["content"]  # type: ignore[index]
    assert "REMAINING_PATCH_BUDGET:7" in repair_user
    assert "SANITIZED_VALIDATION_ERROR_JSON:" in repair_user

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.repaired is True
    assert completed.final_revision == 9
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_second_invalid_attempt_preserves_patches_accepted_by_both_attempts() -> None:
    first = _patch_line("patch-one", _put(_line("node-one")))
    second = _patch_line("patch-two", _put(_line("node-two")))
    client = _FakeClient(
        [
            [first + "\nnot-json\n"],
            [second + "\n```json\n"],
        ]
    )
    service = SceneAuthoringService(client)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_patch",
        "scene_stream_repairing",
        "scene_patch",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.attempt == 2
    assert failure.code == "invalid_scene_stream"
    assert failure.last_accepted_revision == 2
    assert "not-json" not in failure.message
    assert all(stream.closed for stream in client.streams)


@pytest.mark.parametrize("invalid_kind", ["absent_remove", "no_op"])
@pytest.mark.asyncio
async def test_semantic_failure_is_atomic_and_repair_sees_only_accepted_scene(
    invalid_kind: str,
) -> None:
    existing = _line("existing")
    if invalid_kind == "absent_remove":
        invalid = _patch_line(
            "bad-atomic-patch",
            _put(_line("must-not-leak")),
            _remove("missing"),
        )
    else:
        invalid = _patch_line("bad-no-op", _put(existing))
    client = _FakeClient([[invalid + "\n"], []])
    service = SceneAuthoringService(client)

    events = await _collect(service, _request(revision=3, nodes=[existing]))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "scene_stream_failed",
    ]
    snapshot = _repair_snapshot(client)
    assert snapshot["revision"] == 3
    assert [node["id"] for node in snapshot["nodes"]] == ["existing"]  # type: ignore[index]
    assert events[-1].last_accepted_revision == 3  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_duplicate_accepted_patch_id_is_rejected_before_scene_mutation() -> None:
    first = _patch_line("same-patch", _put(_line("node-a")))
    duplicate = _patch_line("same-patch", _put(_line("must-not-leak")))
    repaired = _patch_line("fresh-patch", _put(_line("node-b")))
    client = _FakeClient([[first + "\n" + duplicate + "\n"], [repaired + "\n"]])
    service = SceneAuthoringService(client)

    events = await _collect(service)

    patches = [event for event in events if isinstance(event, ScenePatchEvent)]
    assert [event.patch.patch_id for event in patches] == ["same-patch", "fresh-patch"]
    assert [event.sequence for event in patches] == [1, 2]
    snapshot = _repair_snapshot(client)
    assert [node["id"] for node in snapshot["nodes"]] == ["node-a"]  # type: ignore[index]


@pytest.mark.asyncio
async def test_provider_error_is_friendly_closes_stream_and_preserves_accepted_revision() -> None:
    accepted = _patch_line("accepted", _put(_line("safe-node")))
    raw_secret = "provider body included sk-live-secret"
    client = _FakeClient([[accepted + "\n", RuntimeError(raw_secret)]])
    service = SceneAuthoringService(client)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_patch",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "provider_error"
    assert failure.last_accepted_revision == 1
    assert failure.retryable is True
    assert raw_secret not in failure.model_dump_json(by_alias=True)
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_repair_attempt_has_its_own_timeout_and_closes_blocked_stream() -> None:
    client = _FakeClient([[], [_BLOCK]])
    service = SceneAuthoringService(client, timeout_seconds=0.01)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.attempt == 2
    assert failure.code == "provider_timeout"
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_cancellation_while_awaiting_provider_closes_exact_stream() -> None:
    client = _FakeClient([[_BLOCK]])
    service = SceneAuthoringService(client, timeout_seconds=1.0)
    events = service.stream_events(_request())

    started = await anext(events)
    assert isinstance(started, SceneStreamStartedEvent)
    pending = asyncio.create_task(anext(events))
    await client.stream_created.wait()
    await client.streams[0].waiting.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert client.streams[0].closed is True
    await events.aclose()


@pytest.mark.asyncio
async def test_consumer_close_after_patch_closes_upstream_without_reading_more() -> None:
    patch = _patch_line("visible", _put(_line("visible-node")))
    client = _FakeClient([[patch + "\n", _BLOCK]])
    service = SceneAuthoringService(client)
    events = service.stream_events(_request())

    assert isinstance(await anext(events), SceneStreamStartedEvent)
    assert isinstance(await anext(events), ScenePatchEvent)
    await events.aclose()

    assert client.streams[0].closed is True
    assert client.streams[0].waiting.is_set() is False


@pytest.mark.asyncio
async def test_hard_patch_budget_accepts_eight_and_closes_before_ninth() -> None:
    lines = [_patch_line(f"patch-{index}", _put(_line(f"node-{index}"))) for index in range(1, 10)]
    client = _FakeClient([["\n".join(lines) + "\n"]])
    service = SceneAuthoringService(client)

    events = await _collect(service)

    patches = [event for event in events if isinstance(event, ScenePatchEvent)]
    assert [event.sequence for event in patches] == list(range(1, 9))
    assert [event.patch.patch_id for event in patches] == [
        f"patch-{index}" for index in range(1, 9)
    ]
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.patch_count == 8
    assert completed.final_revision == 8
    assert "REMAINING_PATCH_BUDGET:8" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_factory_is_resolved_once_per_request_and_receives_provider_parameters() -> None:
    client = _FakeClient([[_patch_line("factory-patch", _put(_line("factory-node")))]])
    factory_calls = 0

    def factory() -> _FakeClient:
        nonlocal factory_calls
        factory_calls += 1
        return client

    service = SceneAuthoringService(
        client_factory=factory,
        temperature=0.1,
        max_tokens=777,
    )

    events = await _collect(service)

    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert factory_calls == 1
    assert client.calls[0]["temperature"] == 0.1
    assert client.calls[0]["max_tokens"] == 777


@pytest.mark.asyncio
async def test_oversized_model_context_fails_before_provider_invocation() -> None:
    nodes = [
        {
            "id": f"text-{index}",
            "kind": "text",
            "presentation": _presentation("fade"),
            "x": 10,
            "y": 10,
            "text": "x" * 512,
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 16,
                "opacity": 1,
                "anchor": "start",
            },
        }
        for index in range(128)
    ]
    client = _FakeClient([])
    service = SceneAuthoringService(client)

    events = await _collect(service, _request(nodes=nodes))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "context_too_large"
    assert failure.retryable is False
    assert client.calls == []


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({}, ValueError),
        ({"client": object(), "client_factory": lambda: object()}, ValueError),
        ({"client": object(), "temperature": float("nan")}, ValueError),
        ({"client": object(), "max_tokens": 0}, ValueError),
        ({"client": object(), "max_tokens": True}, ValueError),
        ({"client": object(), "timeout_seconds": 0}, ValueError),
        ({"client": object(), "timeout_seconds": float("inf")}, ValueError),
        ({"client_factory": "not-callable"}, TypeError),
        ({"client": object(), "clock": None}, TypeError),
    ],
)
def test_constructor_rejects_ambiguous_or_unbounded_configuration(
    kwargs: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        SceneAuthoringService(**kwargs)  # type: ignore[arg-type]
