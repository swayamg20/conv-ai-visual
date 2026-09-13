"""Progressive lifecycle and cleanup coverage for Gate 1.8 storyboards."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from murmur.live_scene import semantic_storyboard_service as service_module
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import SceneState
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    ValidatedSemanticStoryboardTransitionV1,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    StoryboardAbstainReasonCode,
    StoryboardClaimId,
    StoryboardConceptId,
    StoryboardEvidenceId,
    StoryboardTrajectoryId,
    TraceStoryboardRecordV1,
)
from murmur.live_scene.semantic_storyboard_requests import (
    SEMANTIC_STORYBOARD_PROTOCOL,
    SemanticStoryboardDirectorRequestV1,
    SemanticStoryboardReflexRequestV1,
)
from murmur.live_scene.semantic_storyboard_routing import (
    route_semantic_storyboard_record,
)
from murmur.live_scene.semantic_storyboard_service import SemanticStoryboardService
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SemanticStoryboardAcceptedPrefixCause,
    SemanticStoryboardCompletionReason,
    SemanticStoryboardFailureCode,
    SemanticStoryboardSceneCheckpointEventV1,
    SemanticStoryboardSceneStreamCompletedEventV1,
    SemanticStoryboardSceneStreamDeclinedEventV1,
    SemanticStoryboardSceneStreamFailedEventV1,
)


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


def _reflex_request(
    problem: PairedProjectileComparisonSpecV1 | None = None,
    *,
    generation: int = 41,
) -> SemanticStoryboardReflexRequestV1:
    return SemanticStoryboardReflexRequestV1(
        protocol=SEMANTIC_STORYBOARD_PROTOCOL,
        routing_mode="reflex",
        problem_spec=problem or _problem(),
        generation=generation,
        base_scene=SceneState(revision=0),
        base_semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
    )


def _director_request(
    anchor: ValidatedSemanticStoryboardTransitionV1,
    *,
    prompt: str = "Compare the two launches.",
    generation: int = 42,
) -> SemanticStoryboardDirectorRequestV1:
    return SemanticStoryboardDirectorRequestV1(
        protocol=SEMANTIC_STORYBOARD_PROTOCOL,
        routing_mode="director",
        prompt=prompt,
        problem_spec=anchor.checkpoint.problem_spec,
        generation=generation,
        base_scene=anchor.result_scene,
        base_semantic_scene=anchor.result_semantic_scene,
    )


async def _collect(service: SemanticStoryboardService, request: object) -> list[object]:
    return [event async for event in service.stream_events(request)]  # type: ignore[arg-type]


async def _anchor(
    problem: PairedProjectileComparisonSpecV1 | None = None,
) -> ValidatedSemanticStoryboardTransitionV1:
    events = await _collect(SemanticStoryboardService(), _reflex_request(problem))
    checkpoint = events[1]
    assert isinstance(checkpoint, SemanticStoryboardSceneCheckpointEventV1)
    return checkpoint.transition


def _line(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _trace(trajectory: StoryboardTrajectoryId) -> str:
    return _line({"v": 1, "act": "trace", "trajectoryId": trajectory.value})


class _Stream:
    def __init__(self, items: list[object]) -> None:
        self.items = list(items)
        self.close_calls = 0

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> str | bytes:
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        await asyncio.sleep(0)
        assert isinstance(item, str | bytes)
        return item

    async def aclose(self) -> None:
        self.close_calls += 1


class _HangingStream(_Stream):
    def __init__(self, first: str | None = None) -> None:
        super().__init__([] if first is None else [first])
        self.entered = asyncio.Event()

    async def __anext__(self) -> str | bytes:
        if self.items:
            return await super().__anext__()
        self.entered.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class _Client:
    def __init__(self, streams: list[_Stream], order: list[str] | None = None) -> None:
        self.streams = streams
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0
        self.order = order

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str | bytes]:
        if self.order is not None:
            self.order.append("stream")
        stream = self.streams[len(self.calls)]
        self.calls.append(
            {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        return stream

    async def aclose(self) -> None:
        self.close_calls += 1


def _checkpoints(events: list[object]) -> list[SemanticStoryboardSceneCheckpointEventV1]:
    return [
        event for event in events if isinstance(event, SemanticStoryboardSceneCheckpointEventV1)
    ]


@pytest.mark.asyncio
async def test_reflex_is_provider_free_and_publishes_one_atomic_anchor() -> None:
    factory_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("Reflex must not resolve a client")

    events = await _collect(
        SemanticStoryboardService(client_factory=factory),
        _reflex_request(),
    )

    assert factory_calls == 0
    assert [event.type for event in events] == [
        "semantic_storyboard_scene_stream_started",
        "semantic_storyboard_scene_checkpoint",
        "semantic_storyboard_scene_stream_completed",
    ]
    checkpoint = _checkpoints(events)[0]
    assert (checkpoint.sequence, checkpoint.base_revision, checkpoint.result_revision) == (1, 0, 1)
    completed = events[-1]
    assert isinstance(completed, SemanticStoryboardSceneStreamCompletedEventV1)
    assert (completed.reason_code, completed.checkpoint_count) == (
        SemanticStoryboardCompletionReason.ANCHOR,
        1,
    )


@pytest.mark.asyncio
async def test_director_streams_each_certified_record_from_one_zero_temperature_call() -> None:
    anchor = await _anchor()
    order: list[str] = []
    stream = _Stream(
        [
            _trace(StoryboardTrajectoryId.LOWER_ANGLE),
            _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
        ]
    )
    client = _Client([stream], order)

    def factory() -> _Client:
        order.append("factory")
        return client

    async def admit() -> None:
        order.append("admit")

    events = await _collect(
        SemanticStoryboardService(client_factory=factory, before_provider_dispatch=admit),
        _director_request(anchor),
    )

    checkpoints = _checkpoints(events)
    assert order == ["admit", "factory", "stream"]
    assert len(client.calls) == 1
    assert client.calls[0]["temperature"] == 0.0
    assert [event.sequence for event in checkpoints] == [1, 2]
    assert checkpoints[0].transition.result_scene == checkpoints[1].transition.base_scene
    assert (
        checkpoints[0].transition.result_semantic_scene
        == checkpoints[1].transition.base_semantic_scene
    )
    completed = events[-1]
    assert isinstance(completed, SemanticStoryboardSceneStreamCompletedEventV1)
    assert (completed.reason_code, completed.checkpoint_count, completed.final_revision) == (
        SemanticStoryboardCompletionReason.MODEL_STOP,
        2,
        anchor.result_scene.revision + 2,
    )
    assert stream.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_complete_record_is_yielded_before_provider_eof_and_disconnect_closes_once() -> None:
    anchor = await _anchor()
    stream = _HangingStream(_trace(StoryboardTrajectoryId.LOWER_ANGLE))
    client = _Client([stream])
    events = SemanticStoryboardService(client).stream_events(_director_request(anchor))

    assert (await anext(events)).type == "semantic_storyboard_scene_stream_started"
    checkpoint = await asyncio.wait_for(anext(events), timeout=1)
    assert isinstance(checkpoint, SemanticStoryboardSceneCheckpointEventV1)
    await events.aclose()

    assert stream.close_calls == 1
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_parser_complete_prefix_is_verified_before_sanitized_tail_terminal() -> None:
    anchor = await _anchor()
    secret = "RAW-PROVIDER-SECRET"
    chunk = _trace(StoryboardTrajectoryId.LOWER_ANGLE) + f'{{"secret":"{secret}"}}\n'
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream([chunk])])),
        _director_request(anchor),
    )

    assert len(_checkpoints(events)) == 1
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert (terminal.reason_code, terminal.accepted_prefix_cause) == (
        SemanticStoryboardCompletionReason.ACCEPTED_PREFIX,
        SemanticStoryboardAcceptedPrefixCause.INVALID_MODEL_STREAM,
    )
    assert secret not in repr(events)


@pytest.mark.asyncio
async def test_invalid_routed_tail_cannot_mutate_an_accepted_prefix() -> None:
    anchor = await _anchor()
    invalid_claim = _line(
        {
            "v": 1,
            "act": "relate",
            "claimId": StoryboardClaimId.HIGHER_APEX.value,
            "evidenceIds": [
                StoryboardEvidenceId.LOWER_TRAJECTORY.value,
                StoryboardEvidenceId.HIGHER_TRAJECTORY.value,
            ],
        }
    )
    events = await _collect(
        SemanticStoryboardService(
            _Client([_Stream([_trace(StoryboardTrajectoryId.LOWER_ANGLE), invalid_claim])])
        ),
        _director_request(anchor),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert terminal.final_revision == checkpoints[0].result_revision
    assert terminal.accepted_prefix_cause is (
        SemanticStoryboardAcceptedPrefixCause.INVALID_MODEL_STREAM
    )


@pytest.mark.asyncio
async def test_sixth_record_maps_the_five_record_prefix_to_capacity_limit() -> None:
    anchor = await _anchor()
    rows = [
        {"v": 1, "act": "reveal", "conceptId": StoryboardConceptId.RANGE_FORMULA.value},
        {
            "v": 1,
            "act": "reveal",
            "conceptId": StoryboardConceptId.COMPLEMENTARY_ANGLES.value,
        },
        {"v": 1, "act": "trace", "trajectoryId": StoryboardTrajectoryId.LOWER_ANGLE.value},
        {"v": 1, "act": "trace", "trajectoryId": StoryboardTrajectoryId.HIGHER_ANGLE.value},
        {
            "v": 1,
            "act": "relate",
            "claimId": StoryboardClaimId.EQUAL_RANGE.value,
            "evidenceIds": [
                StoryboardEvidenceId.RANGE_FORMULA.value,
                StoryboardEvidenceId.COMPLEMENTARY_ANGLES.value,
            ],
        },
        {
            "v": 1,
            "act": "relate",
            "claimId": StoryboardClaimId.HIGHER_APEX.value,
            "evidenceIds": [
                StoryboardEvidenceId.LOWER_TRAJECTORY.value,
                StoryboardEvidenceId.HIGHER_TRAJECTORY.value,
            ],
        },
    ]
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream(["".join(_line(row) for row in rows)])])),
        _director_request(anchor),
    )

    assert len(_checkpoints(events)) == 5
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert terminal.accepted_prefix_cause is SemanticStoryboardAcceptedPrefixCause.CAPACITY_LIMIT


@pytest.mark.asyncio
async def test_sole_abstain_is_held_for_clean_eof_and_never_mutates() -> None:
    anchor = await _anchor()
    abstain = _line(
        {
            "v": 1,
            "act": "abstain",
            "reasonCode": StoryboardAbstainReasonCode.AMBIGUOUS_INTENT.value,
        }
    )
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream([abstain])])),
        _director_request(anchor),
    )

    assert _checkpoints(events) == []
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamDeclinedEventV1)
    assert terminal.reason_code is StoryboardAbstainReasonCode.AMBIGUOUS_INTENT
    assert terminal.final_revision == anchor.result_scene.revision


@pytest.mark.asyncio
async def test_abstain_followed_by_provider_failure_is_not_a_decline() -> None:
    anchor = await _anchor()
    secret = RuntimeError("RAW-PROVIDER-SECRET")
    abstain = _line({"v": 1, "act": "abstain", "reasonCode": "no_forward_progress"})
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream([abstain, secret])])),
        _director_request(anchor),
    )

    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.PROVIDER_ERROR
    assert "RAW-PROVIDER-SECRET" not in repr(events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (["not-json\n"], SemanticStoryboardFailureCode.INVALID_MODEL_STREAM),
        ([RuntimeError("private provider body")], SemanticStoryboardFailureCode.PROVIDER_ERROR),
    ],
)
async def test_failure_before_first_beat_is_mutation_free_and_sanitized(
    items: list[object],
    expected: SemanticStoryboardFailureCode,
) -> None:
    anchor = await _anchor()
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream(items)])),
        _director_request(anchor),
    )

    assert _checkpoints(events) == []
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert (terminal.code, terminal.last_accepted_revision) == (
        expected,
        anchor.result_scene.revision,
    )
    assert "private provider body" not in repr(events)


@pytest.mark.asyncio
async def test_timeout_after_one_record_retains_prefix_and_closes_upstream() -> None:
    anchor = await _anchor()
    stream = _HangingStream(_trace(StoryboardTrajectoryId.LOWER_ANGLE))
    events = await _collect(
        SemanticStoryboardService(_Client([stream]), timeout_seconds=0.01),
        _director_request(anchor),
    )

    assert len(_checkpoints(events)) == 1
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert terminal.accepted_prefix_cause is (
        SemanticStoryboardAcceptedPrefixCause.PROVIDER_TIMEOUT
    )
    assert stream.close_calls == 1


@pytest.mark.asyncio
async def test_provider_error_after_one_record_retains_exact_prefix_and_closes_once() -> None:
    anchor = await _anchor()
    stream = _Stream(
        [
            _trace(StoryboardTrajectoryId.LOWER_ANGLE),
            RuntimeError("RAW-PROVIDER-SECRET"),
        ]
    )
    events = await _collect(
        SemanticStoryboardService(_Client([stream])),
        _director_request(anchor),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert (
        terminal.accepted_prefix_cause,
        terminal.final_revision,
        stream.close_calls,
    ) == (
        SemanticStoryboardAcceptedPrefixCause.PROVIDER_ERROR,
        checkpoints[0].result_revision,
        1,
    )
    assert "RAW-PROVIDER-SECRET" not in repr(events)


@pytest.mark.asyncio
async def test_timeout_before_first_record_is_retryable_and_mutation_free() -> None:
    anchor = await _anchor()
    stream = _HangingStream()
    events = await _collect(
        SemanticStoryboardService(_Client([stream]), timeout_seconds=0.01),
        _director_request(anchor),
    )

    assert _checkpoints(events) == []
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert (terminal.code, terminal.retryable, terminal.last_accepted_revision) == (
        SemanticStoryboardFailureCode.PROVIDER_TIMEOUT,
        True,
        anchor.result_scene.revision,
    )
    assert stream.close_calls == 1


@pytest.mark.asyncio
async def test_preflight_rejects_dirty_frontier_before_client_resolution() -> None:
    anchor = await _anchor()
    request = _director_request(anchor).model_copy(
        update={"base_scene": SceneState(revision=1, nodes=())}
    )
    factory_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        return _Client([_Stream([])])

    events = await _collect(SemanticStoryboardService(client_factory=factory), request)

    assert factory_calls == 0
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.SEMANTIC_BASE_MISMATCH


@pytest.mark.asyncio
async def test_admission_rejection_happens_before_factory_resolution() -> None:
    anchor = await _anchor()
    order: list[str] = []
    client = _Client([_Stream([])], order)
    factory_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        order.append("factory")
        return client

    async def reject() -> None:
        order.append("admit")
        raise SceneAdmissionError("provider_rate_limited", "private admission detail")

    events = await _collect(
        SemanticStoryboardService(
            client_factory=factory,
            before_provider_dispatch=reject,
        ),
        _director_request(anchor),
    )

    assert factory_calls == 0
    assert order == ["admit"]
    assert client.calls == []
    assert client.close_calls == 0
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED
    assert "private admission detail" not in repr(events)


@pytest.mark.asyncio
async def test_unexpected_admission_bug_is_sanitized_as_internal_integrity() -> None:
    anchor = await _anchor()
    client = _Client([_Stream([])])

    async def broken_hook() -> None:
        raise RuntimeError("RAW-HOOK-SECRET")

    events = await _collect(
        SemanticStoryboardService(client, before_provider_dispatch=broken_hook),
        _director_request(anchor),
    )

    assert client.calls == []
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR
    assert "RAW-HOOK-SECRET" not in repr(events)


@pytest.mark.asyncio
async def test_unexpected_capacity_preflight_bug_is_internal_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = await _anchor()
    factory_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        return _Client([_Stream([])])

    def broken_capacity(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("RAW-CAPACITY-SECRET")

    monkeypatch.setattr(
        service_module,
        "storyboard_has_forward_capacity",
        broken_capacity,
    )
    events = await _collect(
        SemanticStoryboardService(client_factory=factory),
        _director_request(anchor),
    )

    assert factory_calls == 0
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR
    assert "RAW-CAPACITY-SECRET" not in repr(events)


@pytest.mark.asyncio
async def test_checkpoint_wire_failure_publishes_no_candidate_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = await _anchor()
    real_encode = service_module.encode_semantic_storyboard_scene_stream_event

    def corrupt_checkpoint(event: object, **kwargs: Any) -> str:
        encoded = real_encode(event, **kwargs)  # type: ignore[arg-type]
        if isinstance(event, SemanticStoryboardSceneCheckpointEventV1):
            return encoded.replace('"attempt":1', '"attempt":2', 1)
        return encoded

    monkeypatch.setattr(
        service_module,
        "encode_semantic_storyboard_scene_stream_event",
        corrupt_checkpoint,
    )
    events = await _collect(
        SemanticStoryboardService(_Client([_Stream([_trace(StoryboardTrajectoryId.LOWER_ANGLE)])])),
        _director_request(anchor),
    )

    assert _checkpoints(events) == []
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR


@pytest.mark.asyncio
async def test_cancellation_closes_upstream_and_factory_owned_client_exactly_once() -> None:
    anchor = await _anchor()
    stream = _HangingStream()
    client = _Client([stream])
    events = SemanticStoryboardService(client_factory=lambda: client).stream_events(
        _director_request(anchor)
    )
    await anext(events)
    pending = asyncio.create_task(anext(events))
    await stream.entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert stream.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_reusing_service_does_not_leak_frontier_between_generations() -> None:
    anchor = await _anchor()
    client = _Client(
        [
            _Stream([_trace(StoryboardTrajectoryId.LOWER_ANGLE)]),
            _Stream([_trace(StoryboardTrajectoryId.HIGHER_ANGLE)]),
        ]
    )
    service = SemanticStoryboardService(client)
    first = await _collect(service, _director_request(anchor, generation=70))
    second = await _collect(service, _director_request(anchor, generation=71))

    assert len(client.calls) == 2
    assert [_checkpoints(first)[0].sequence, _checkpoints(second)[0].sequence] == [1, 1]
    assert _checkpoints(first)[0].base_revision == _checkpoints(second)[0].base_revision


def _advance(
    transition: ValidatedSemanticStoryboardTransitionV1,
    record: AcceptedSemanticStoryboardRecordV1,
) -> ValidatedSemanticStoryboardTransitionV1:
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=transition.checkpoint.problem_spec,
        semantic_scene=transition.result_semantic_scene,
    )
    return compile_certified_semantic_storyboard_checkpoint(
        beat,
        base_scene=transition.result_scene,
        base_semantic_scene=transition.result_semantic_scene,
    )


@pytest.mark.asyncio
async def test_exhausted_frontier_rejects_before_client_resolution_or_dispatch() -> None:
    transition = await _anchor()
    trajectories = (
        StoryboardEvidenceId.LOWER_TRAJECTORY,
        StoryboardEvidenceId.HIGHER_TRAJECTORY,
    )
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...] = (
        RevealStoryboardRecordV1(
            v=1,
            act="reveal",
            concept_id=StoryboardConceptId.RANGE_FORMULA,
        ),
        RevealStoryboardRecordV1(
            v=1,
            act="reveal",
            concept_id=StoryboardConceptId.COMPLEMENTARY_ANGLES,
        ),
        TraceStoryboardRecordV1(
            v=1,
            act="trace",
            trajectory_id=StoryboardTrajectoryId.LOWER_ANGLE,
        ),
        TraceStoryboardRecordV1(
            v=1,
            act="trace",
            trajectory_id=StoryboardTrajectoryId.HIGHER_ANGLE,
        ),
        RelateStoryboardRecordV1(
            v=1,
            act="relate",
            claim_id=StoryboardClaimId.EQUAL_RANGE,
            evidence_ids=(
                StoryboardEvidenceId.RANGE_FORMULA,
                StoryboardEvidenceId.COMPLEMENTARY_ANGLES,
            ),
        ),
        RelateStoryboardRecordV1(
            v=1,
            act="relate",
            claim_id=StoryboardClaimId.HIGHER_APEX,
            evidence_ids=trajectories,
        ),
        RelateStoryboardRecordV1(
            v=1,
            act="relate",
            claim_id=StoryboardClaimId.LONGER_FLIGHT,
            evidence_ids=trajectories,
        ),
    )
    for record in records:
        transition = _advance(transition, record)
    factory_calls = 0
    admission_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        return _Client([_Stream([])])

    async def admit() -> None:
        nonlocal admission_calls
        admission_calls += 1

    events = await _collect(
        SemanticStoryboardService(
            client_factory=factory,
            before_provider_dispatch=admit,
        ),
        _director_request(transition),
    )

    assert (factory_calls, admission_calls) == (0, 0)
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamFailedEventV1)
    assert terminal.code is SemanticStoryboardFailureCode.STORYBOARD_CAPACITY_EXHAUSTED


@pytest.mark.asyncio
async def test_revision_boundary_after_prefix_is_a_sanitized_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = await _anchor()
    monkeypatch.setattr(service_module, "MAX_SAFE_SEQUENCE", 2)
    stream = _Stream(
        [
            _trace(StoryboardTrajectoryId.LOWER_ANGLE),
            _trace(StoryboardTrajectoryId.HIGHER_ANGLE),
        ]
    )
    events = await _collect(
        SemanticStoryboardService(_Client([stream])),
        _director_request(anchor),
    )

    assert len(_checkpoints(events)) == 1
    terminal = events[-1]
    assert isinstance(terminal, SemanticStoryboardSceneStreamCompletedEventV1)
    assert terminal.accepted_prefix_cause is (SemanticStoryboardAcceptedPrefixCause.REVISION_LIMIT)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tokens": 0},
        {"max_tokens": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"client": _Client([]), "client_factory": lambda: _Client([])},
    ],
)
def test_constructor_rejects_ambiguous_or_unbounded_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        SemanticStoryboardService(**kwargs)  # type: ignore[arg-type]
