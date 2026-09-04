"""Focused tests for bounded live-scene model orchestration."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    LiveSceneRequest,
    ScenePatchEvent,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.service import SceneAuthoringService
from murmur.live_scene.wire import MAX_SSE_EVENT_BYTES, encode_scene_stream_event

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


def _near_model_frame_limit_patch() -> str:
    operations: list[dict[str, object]] = []
    short_float_points = 24
    for index in range(16):
        id_prefix = f"p{index:02d}"
        node_id = id_prefix + "x" * (64 - len(id_prefix))
        points: list[list[float]] = []
        for _ in range(96):
            x = 800.0 if short_float_points > 0 else 123.12345678901234
            short_float_points -= int(short_float_points > 0)
            points.append([x, 456.12345678901234])
        operations.append(
            {
                "op": "put",
                "node": {
                    "id": node_id,
                    "kind": "path",
                    "presentation": _presentation(),
                    "points": points,
                    "closed": False,
                    "style": {
                        "stroke": "hsl(var(--lavender))",
                        "strokeWidth": 3.123456789012345,
                        "opacity": 0.9876543210123456,
                        "roughness": 0.1234567890123456,
                        "fill": "transparent",
                    },
                },
            }
        )
    # Exponent notation keeps the model frame below 64 KiB. Canonical server
    # serialization expands these values and exercises the separate SSE budget.
    return json.dumps(
        {
            "v": 1,
            "patchId": "p" + "z" * 63,
            "narration": "n" * 512,
            "operations": operations,
        },
        separators=(",", ":"),
    ).replace("800.0", "8e2")


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
        self.close_calls = 0

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

    async def aclose(self) -> None:
        self.close_calls += 1


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
async def test_near_limit_model_frame_fits_the_larger_canonical_sse_budget() -> None:
    frame = _near_model_frame_limit_patch()
    client = _FakeClient([[frame]])
    service = SceneAuthoringService(client)

    events = await _collect(service)

    patch_event = next(event for event in events if isinstance(event, ScenePatchEvent))
    wire = encode_scene_stream_event(patch_event)
    assert len(frame.encode("utf-8")) <= MAX_NDJSON_FRAME_BYTES
    assert len(wire.encode("utf-8")) > MAX_NDJSON_FRAME_BYTES
    assert len(wire.encode("utf-8")) <= MAX_SSE_EVENT_BYTES
    assert isinstance(events[-1], SceneStreamCompletedEvent)


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
async def test_repair_receives_safe_scene_bounds_guidance_without_model_values() -> None:
    invalid_node = _line("private-out-of-bounds-node")
    invalid_node["points"] = [[-12_345, 20], [200, 220]]
    invalid = _patch_line(
        "private-invalid-patch",
        _put(invalid_node),
        narration="private narration",
    )
    repaired = _patch_line("safe-repair", _put(_line("visible-node")))
    accepted = _patch_line("accepted-patch", _put(_line("accepted-node")))
    client = _FakeClient([[accepted + "\n" + invalid + "\n"], [repaired + "\n"]])
    service = SceneAuthoringService(client)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_patch",
        "scene_stream_repairing",
        "scene_patch",
        "scene_stream_completed",
    ]
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    snapshot = _repair_snapshot(client)
    assert snapshot["revision"] == 1
    assert [node["id"] for node in snapshot["nodes"]] == ["accepted-node"]  # type: ignore[index]
    repair_user = client.calls[1]["messages"][1]["content"]  # type: ignore[index]
    repair_error = json.loads(
        next(
            line.removeprefix("SANITIZED_VALIDATION_ERROR_JSON:")
            for line in repair_user.splitlines()
            if line.startswith("SANITIZED_VALIDATION_ERROR_JSON:")
        )
    )
    assert repair_error == (
        "scene_bounds: resize or reposition nodes using stable IDs; keep every point inside "
        "800x600, x and y at least 0, x plus width at most 800, and y plus height at most 600"
    )
    assert len(repair_error) <= 320
    assert "private" not in repair_error
    assert "12345" not in repair_error
    assert "private" not in "".join(event.model_dump_json() for event in events)
    assert "private" not in repair_user


@pytest.mark.asyncio
async def test_repair_completes_after_one_valid_patch_without_parsing_trailing_garbage() -> None:
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
        "scene_stream_completed",
    ]
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.repaired is True
    assert completed.patch_count == 2
    assert completed.final_revision == 2
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
async def test_provider_dispatch_limit_is_classified_before_raw_scene_call() -> None:
    client = _FakeClient([[]])

    async def reject() -> None:
        raise SceneAdmissionError("provider_rate_limited", "private limiter state")

    events = await _collect(
        SceneAuthoringService(client, before_provider_dispatch=reject),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert (failure.code, failure.attempt, failure.retryable) == (
        "provider_rate_limited",
        1,
        True,
    )
    assert client.calls == []


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
async def test_initial_attempt_accepts_three_and_closes_before_invalid_fourth_frame() -> None:
    lines = [_patch_line(f"patch-{index}", _put(_line(f"node-{index}"))) for index in range(1, 4)]
    client = _FakeClient([["\n".join(lines) + "\n"]])
    client._attempts[0][0] += '{"v":1,"patchId":"truncated-fourth"'
    service = SceneAuthoringService(client)

    events = await _collect(service)

    patches = [event for event in events if isinstance(event, ScenePatchEvent)]
    assert [event.sequence for event in patches] == [1, 2, 3]
    assert [event.patch.patch_id for event in patches] == [
        "patch-1",
        "patch-2",
        "patch-3",
    ]
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.patch_count == 3
    assert completed.final_revision == 3
    assert "REMAINING_PATCH_BUDGET:8" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert "TARGET_PATCH_COUNT:3" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
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
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_factory_owned_client_closes_when_consumer_aborts_after_visible_patch() -> None:
    patch = _patch_line("visible", _put(_line("visible-node")))
    client = _FakeClient([[patch + "\n", _BLOCK]])
    service = SceneAuthoringService(client_factory=lambda: client)
    events = service.stream_events(_request())

    assert isinstance(await anext(events), SceneStreamStartedEvent)
    assert isinstance(await anext(events), ScenePatchEvent)
    await events.aclose()

    assert client.streams[0].closed is True
    assert client.close_calls == 1


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
        ({"client": object(), "max_tokens": 4_097}, ValueError),
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
