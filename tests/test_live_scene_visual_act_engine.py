"""Provider-free tests for bounded visual-act model orchestration."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest
from murmur.live_scene import visual_act_engine as engine_module
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    AbstainVisualDecision,
    ContinueVisualDecision,
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    StartVisualDecision,
)
from murmur.live_scene.visual_act_engine import (
    VisualActEngineError,
    VisualActEngineErrorCode,
    VisualActRoutingEngine,
    VisualActRoutingRepairing,
    VisualActRoutingResult,
)

_BLOCK = object()


def _decision_line(
    decision: str = "start_visual",
    *,
    component_id: str = "areas",
    stage: str = "identity",
) -> str:
    if decision == "start_visual":
        payload = {"v": 1, "decision": decision, "targetStage": stage}
    elif decision == "continue_visual":
        payload = {
            "v": 1,
            "decision": decision,
            "componentId": component_id,
            "targetStage": stage,
        }
    else:
        payload = {"v": 1, "decision": "abstain", "reasonCode": "unsupported_intent"}
    return json.dumps(payload, separators=(",", ":"))


def _line_value(content: str, prefix: str) -> str:
    return next(
        line.removeprefix(prefix) for line in content.splitlines() if line.startswith(prefix)
    )


class _TrackedStream:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)
        self.closed = False
        self.reads = 0
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> _TrackedStream:
        return self

    async def __anext__(self) -> str | bytes:
        if self.closed or not self._items:
            raise StopAsyncIteration
        self.reads += 1
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
    def __init__(
        self,
        attempts: list[list[object]],
        *,
        stream_error: BaseException | None = None,
    ) -> None:
        self._attempts = attempts
        self._stream_error = stream_error
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
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if self._stream_error is not None:
            raise self._stream_error
        stream = _TrackedStream(self._attempts[len(self.streams)])
        self.streams.append(stream)
        self.stream_created.set()
        return stream


@pytest.mark.asyncio
async def test_first_resolved_decision_wins_and_closes_stream_at_temperature_zero() -> None:
    certificate = "a" * 64
    scene = SemanticSceneState(
        revision=1,
        components=(
            PythagoreanAreaIdentityState(
                id="areas",
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:1],
            ),
        ),
        certificate_head_sha256=certificate,
    )
    before = deepcopy(scene)
    accepted = _decision_line("continue_visual", stage="areas")
    client = _FakeClient([[accepted + "\nTOP-SECRET-TRAILING-FRAME\n", _BLOCK]])

    result = await VisualActRoutingEngine(client, max_tokens=321).route(
        prompt="Continue through the side-square areas.",
        semantic_scene=scene,
    )

    assert isinstance(result.decision, ContinueVisualDecision)
    assert result.provider_attempts == 1
    assert result.repaired is False
    assert result.resolved is not None
    assert result.resolved.component_id == "areas"
    assert result.resolved.missing_roles == PYTHAGOREAN_ROLE_ORDER[1:7]
    assert scene == before
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["max_tokens"] == 321
    messages = client.calls[0]["messages"]
    assert isinstance(messages, list)
    assert certificate not in "\n".join(message["content"] for message in messages)
    assert client.streams[0].reads == 1
    assert client.streams[0].waiting.is_set() is False
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_abstain_is_a_successful_one_attempt_noop() -> None:
    client = _FakeClient([[_decision_line("abstain")]])

    result = await VisualActRoutingEngine(client).route(
        prompt="Write a database migration.",
        semantic_scene=SemanticSceneState(revision=0),
    )

    assert isinstance(result.decision, AbstainVisualDecision)
    assert result.resolved is None
    assert result.provider_attempts == 1
    assert result.repaired is False
    assert client.streams[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_attempt", "raw_sentinel", "expected_hint"),
    [
        (
            ["not-json-TOP-SECRET-PARSER\n"],
            "TOP-SECRET-PARSER",
            "invalid_json: emit one complete JSON object per NDJSON line",
        ),
        ([], None, "visual_act_stream: emit one visual-act decision"),
        (
            [_decision_line("continue_visual", component_id="MissingSecret")],
            "MissingSecret",
            "visual_act_state: copy an accepted componentId exactly",
        ),
    ],
)
async def test_parser_empty_and_resolver_rejections_get_one_sanitized_repair(
    first_attempt: list[object],
    raw_sentinel: str | None,
    expected_hint: str,
) -> None:
    client = _FakeClient([first_attempt, [_decision_line(stage="triangle")]])

    result = await VisualActRoutingEngine(client).route(
        prompt="Draw the triangle.",
        semantic_scene=SemanticSceneState(revision=0),
    )

    assert isinstance(result.decision, StartVisualDecision)
    assert result.resolved is not None
    assert result.provider_attempts == 2
    assert result.repaired is True
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)
    repair_messages = client.calls[1]["messages"]
    assert isinstance(repair_messages, list)
    repair_user = repair_messages[1]["content"]
    assert "REPAIR_MODE:true" in repair_user
    assert json.loads(_line_value(repair_user, "SANITIZED_VALIDATION_ERROR_JSON:")) == expected_hint
    snapshot = repair_user.split("LAST_ACCEPTED_SEMANTIC_SCENE_JSON:\n", 1)[1].split(
        "\nOUTPUT_ONE_VISUAL_ACT_DECISION_NDJSON_NOW:",
        1,
    )[0]
    assert json.loads(snapshot) == {"components": [], "revision": 0}
    assert "CURRENT_ACCEPTED_SEMANTIC_SCENE_JSON:" not in repair_user
    if raw_sentinel is not None:
        assert raw_sentinel not in repair_user


@pytest.mark.asyncio
async def test_stream_route_announces_repair_before_the_second_dispatch() -> None:
    client = _FakeClient([["not-json\n"], [_decision_line(stage="triangle")]])
    steps = VisualActRoutingEngine(client).stream_route(
        prompt="Draw the triangle.",
        semantic_scene=SemanticSceneState(revision=0),
    )

    repairing = await anext(steps)

    assert isinstance(repairing, VisualActRoutingRepairing)
    assert (repairing.from_attempt, repairing.to_attempt) == (1, 2)
    assert len(client.calls) == 1

    result = await anext(steps)
    assert isinstance(result, VisualActRoutingResult)
    assert result.provider_attempts == 2
    assert len(client.calls) == 2
    with pytest.raises(StopAsyncIteration):
        await anext(steps)


@pytest.mark.asyncio
async def test_second_rejection_fails_without_a_third_provider_attempt() -> None:
    secret = "TOP-SECRET-SECOND-REJECTION"
    client = _FakeClient([["not-json\n"], [f"[]{secret}\n"]])

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client).route(
            prompt="Draw the visual.",
            semantic_scene=SemanticSceneState(revision=0),
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.INVALID_VISUAL_ACT
    assert error.provider_attempts == 2
    assert error.retryable is True
    assert secret not in str(error)
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_provider_error_is_sanitized_and_never_repaired() -> None:
    secret = "provider leaked sk-top-secret"
    client = _FakeClient([[RuntimeError(secret)]])

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client).route(
            prompt="Draw the triangle.",
            semantic_scene=SemanticSceneState(revision=0),
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.PROVIDER_ERROR
    assert error.provider_attempts == 1
    assert error.retryable is True
    assert secret not in str(error)
    assert len(client.calls) == 1
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_provider_stream_creation_error_is_sanitized_and_never_repaired() -> None:
    secret = "provider constructor leaked sk-top-secret"
    client = _FakeClient([], stream_error=RuntimeError(secret))

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client).route(
            prompt="Draw the triangle.",
            semantic_scene=SemanticSceneState(revision=0),
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.PROVIDER_ERROR
    assert error.provider_attempts == 1
    assert error.retryable is True
    assert secret not in str(error)
    assert len(client.calls) == 1
    assert client.streams == []


@pytest.mark.asyncio
async def test_provider_timeout_is_retryable_but_never_repaired() -> None:
    client = _FakeClient([[_BLOCK]])

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client, timeout_seconds=0.01).route(
            prompt="Draw the triangle.",
            semantic_scene=SemanticSceneState(revision=0),
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.PROVIDER_TIMEOUT
    assert error.provider_attempts == 1
    assert error.retryable is True
    assert len(client.calls) == 1
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_cancellation_passes_through_and_closes_the_exact_stream() -> None:
    client = _FakeClient([[_BLOCK]])
    task = asyncio.create_task(
        VisualActRoutingEngine(client, timeout_seconds=1.0).route(
            prompt="Draw the triangle.",
            semantic_scene=SemanticSceneState(revision=0),
        )
    )
    await client.stream_created.wait()
    await client.streams[0].waiting.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(client.calls) == 1
    assert client.streams[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "scene"),
    [
        ("", SemanticSceneState(revision=0)),
        ("x" * 2_001, SemanticSceneState(revision=0)),
        ("Draw it.", object()),
    ],
)
async def test_invalid_context_fails_before_provider_dispatch(
    prompt: str,
    scene: object,
) -> None:
    client = _FakeClient([])

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client).route(
            prompt=prompt,
            semantic_scene=scene,  # type: ignore[arg-type]
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.CONTEXT_INVALID
    assert error.provider_attempts == 0
    assert error.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_unexpected_resolver_failure_is_a_sanitized_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "TOP-SECRET-INTERNAL"

    def fail_resolution(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(engine_module, "resolve_visual_act", fail_resolution)
    client = _FakeClient([[_decision_line(stage="triangle")]])

    with pytest.raises(VisualActEngineError) as captured:
        await VisualActRoutingEngine(client).route(
            prompt="Draw the triangle.",
            semantic_scene=SemanticSceneState(revision=0),
        )

    error = captured.value
    assert error.code is VisualActEngineErrorCode.INTERNAL_ERROR
    assert error.provider_attempts == 1
    assert error.retryable is False
    assert secret not in str(error)
    assert len(client.calls) == 1
    assert client.streams[0].closed is True


def test_public_error_codes_and_retryability_are_closed_and_stable() -> None:
    expected = {
        VisualActEngineErrorCode.CONTEXT_INVALID: False,
        VisualActEngineErrorCode.PROVIDER_TIMEOUT: True,
        VisualActEngineErrorCode.PROVIDER_ERROR: True,
        VisualActEngineErrorCode.INVALID_VISUAL_ACT: True,
        VisualActEngineErrorCode.INTERNAL_ERROR: False,
    }

    assert {code.value for code in VisualActEngineErrorCode} == {
        "context_invalid",
        "provider_timeout",
        "provider_error",
        "invalid_visual_act",
        "internal_error",
    }
    for code, retryable in expected.items():
        error = VisualActEngineError(code, provider_attempts=0)
        assert error.retryable is retryable


@pytest.mark.parametrize(
    ("args", "kwargs", "error_type"),
    [
        ((object(),), {}, TypeError),
        ((_FakeClient([]),), {"max_tokens": 0}, ValueError),
        ((_FakeClient([]),), {"max_tokens": 4_097}, ValueError),
        ((_FakeClient([]),), {"max_tokens": True}, ValueError),
        ((_FakeClient([]),), {"timeout_seconds": 0}, ValueError),
        ((_FakeClient([]),), {"timeout_seconds": float("nan")}, ValueError),
        ((_FakeClient([]),), {"timeout_seconds": float("inf")}, ValueError),
        ((_FakeClient([]),), {"timeout_seconds": True}, ValueError),
    ],
)
def test_constructor_rejects_invalid_or_unbounded_configuration(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        VisualActRoutingEngine(*args, **kwargs)  # type: ignore[arg-type]
