"""Provider-free integration tests for transactional V2 choreography preflight."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from murmur.live_scene import service as service_module
from murmur.live_scene.checkpoint_contracts import (
    CheckpointCompilerCertificateV2,
    CheckpointVerificationReceiptV2,
    checkpoint_certificate_sha256,
)
from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    CompletingSquareStage,
    RoutedChoreographyBeatV2,
)
from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneCheckpointEvent,
    ChoreographySceneStreamDeclinedEvent,
    ChoreographySceneStreamEvent,
)
from murmur.live_scene.choreography_wire import (
    MAX_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_choreography_scene_stream_event,
)
from murmur.live_scene.completing_square_compiler import compile_checkpoint_beat
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.completing_square_verifier import (
    verify_completing_square_checkpoint,
)
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState
from murmur.live_scene.semantic_service_contracts import (
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
)
from murmur.live_scene.wire import SceneStreamWireError


def _decision_line(
    decision: str,
    *,
    stage: str = "solve",
    reason: str = "unsupported_intent",
) -> str:
    if decision == "start_choreography":
        payload = {
            "v": 1,
            "decision": decision,
            "componentKind": "completing_square",
            "targetStage": stage,
        }
    elif decision == "continue_choreography":
        payload = {
            "v": 1,
            "decision": decision,
            "componentId": "square-lesson",
            "targetStage": stage,
        }
    elif decision == "clarify_corner":
        payload = {
            "v": 1,
            "decision": decision,
            "componentId": "square-lesson",
        }
    elif decision == "start_visual":
        payload = {"v": 1, "decision": decision, "targetStage": "triangle"}
    else:
        payload = {"v": 1, "decision": "abstain", "reasonCode": reason}
    return json.dumps(payload, separators=(",", ":"))


class _Stream:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)
        self.closed = False

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> str | bytes:
        if self.closed or not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        await asyncio.sleep(0)
        assert isinstance(item, str | bytes)
        return item

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, attempts: list[list[object]]) -> None:
        self._attempts = attempts
        self.calls: list[dict[str, object]] = []
        self.streams: list[_Stream] = []
        self.close_calls = 0

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> _Stream:
        stream = _Stream(self._attempts[len(self.calls)])
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        self.streams.append(stream)
        return stream

    async def aclose(self) -> None:
        self.close_calls += 1


def _request(
    *,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    generation: int = 17,
) -> SemanticLiveSceneRequest:
    return SemanticLiveSceneRequest(
        prompt="Teach completing the square visually.",
        generation=generation,
        base_scene=scene or SceneState(revision=0),
        base_semantic_scene=semantic_scene or SemanticSceneState(revision=0),
    )


def _beat(stage: CompletingSquareStage = CompletingSquareStage.SOLVE) -> RoutedChoreographyBeatV2:
    return RoutedChoreographyBeatV2(
        beat_id="fixture-beat",
        component_id="square-lesson",
        route=AdvanceChoreographyRouteV2(target_stage=stage),
    )


def _materialized_through(
    target: CompletingSquareMainCheckpoint,
) -> tuple[SceneState, SemanticSceneState]:
    compiled = compile_checkpoint_beat(
        _beat(),
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )
    scene = compiled.base_scene
    semantic_scene = compiled.base_semantic_scene
    for checkpoint in compiled.checkpoints:
        scene = service_module._apply_patch(scene, checkpoint.patch)
        main_checkpoint = CompletingSquareMainCheckpoint(checkpoint.checkpoint_id.value)
        component = CompletingSquareState(
            id=checkpoint.beat.component_id,
            last_main_checkpoint=main_checkpoint,
        )
        semantic_scene = SemanticSceneState(
            revision=semantic_scene.revision + 1,
            components=(component,),
            certificate_head_sha256=checkpoint.certificate.certificate_sha256,
        )
        if main_checkpoint is target:
            return scene, semantic_scene
    raise AssertionError(f"checkpoint fixture did not reach {target.value}")


async def _collect(
    service: service_module.SceneAuthoringService,
    request: SemanticLiveSceneRequest | None = None,
) -> list[object]:
    return [
        event async for event in service.stream_routed_choreography_events(request or _request())
    ]


def _checkpoints(events: list[object]) -> list[ChoreographySceneCheckpointEvent]:
    return [event for event in events if isinstance(event, ChoreographySceneCheckpointEvent)]


def _zero_checkpoint_failure(
    events: list[object],
    *,
    code: str,
    retryable: bool,
    revision: int = 0,
) -> SceneStreamFailedEvent:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    assert _checkpoints(events) == []
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert (failed.code, failed.retryable, failed.last_accepted_revision) == (
        code,
        retryable,
        revision,
    )
    return failed


@pytest.mark.asyncio
async def test_full_solve_suffix_is_verified_and_emitted_in_one_chain() -> None:
    client = _Client([[_decision_line("start_choreography")]])
    request = _request()

    events = await _collect(service_module.SceneAuthoringService(client, max_tokens=71), request)

    checkpoints = _checkpoints(events)
    assert [event.type for event in events] == [
        "scene_stream_started",
        *("choreography_scene_checkpoint" for _ in range(8)),
        "scene_stream_completed",
    ]
    assert [event.semantic.checkpoint_id.value for event in checkpoints] == [
        "problem",
        "area_model",
        "split_linear_term",
        "rearrange_halves",
        "missing_corner",
        "balance_and_complete",
        "factor_square",
        "solve_roots",
    ]
    assert [event.sequence for event in checkpoints] == list(range(1, 9))
    assert [(event.base_revision, event.result_revision) for event in checkpoints] == [
        (revision, revision + 1) for revision in range(8)
    ]

    scene = request.base_scene
    previous_certificate = request.base_semantic_scene.certificate_head_sha256
    for event in checkpoints:
        result_scene = service_module._apply_patch(scene, event.patch)
        assert (
            verify_completing_square_checkpoint(
                event.semantic.beat.component_id,
                event.semantic.checkpoint_id,
                scene,
                result_scene,
                event.patch,
                event.semantic.presentation,
                event.semantic.choreography,
            )
            == event.semantic.receipt
        )
        assert event.semantic.certificate.body.previous_certificate_sha256 == previous_certificate
        assert len(encode_choreography_scene_stream_event(event).encode("utf-8")) <= (
            MAX_CHOREOGRAPHY_SSE_EVENT_BYTES
        )
        scene = result_scene
        previous_certificate = event.semantic.certificate.certificate_sha256

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (completed.final_revision, completed.patch_count, completed.repaired) == (8, 8, False)
    assert checkpoints[-1].semantic.result_component == CompletingSquareState(
        id="square-lesson",
        last_main_checkpoint=CompletingSquareMainCheckpoint.SOLVE_ROOTS,
    )
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["max_tokens"] == 71
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_resume_emits_only_missing_suffix_and_extends_existing_head() -> None:
    scene, semantic_scene = _materialized_through(CompletingSquareMainCheckpoint.AREA_MODEL)
    client = _Client([[_decision_line("continue_choreography", stage="complete")]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    checkpoints = _checkpoints(events)
    assert [event.semantic.checkpoint_id for event in checkpoints] == [
        CompletingSquareCheckpointId.SPLIT_LINEAR_TERM,
        CompletingSquareCheckpointId.REARRANGE_HALVES,
        CompletingSquareCheckpointId.MISSING_CORNER,
        CompletingSquareCheckpointId.BALANCE_AND_COMPLETE,
    ]
    assert checkpoints[0].base_revision == scene.revision
    assert (
        checkpoints[0].semantic.certificate.body.previous_certificate_sha256
        == semantic_scene.certificate_head_sha256
    )
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].final_revision == scene.revision + 4


@pytest.mark.asyncio
async def test_corner_clarification_is_one_atomic_checkpoint() -> None:
    scene, semantic_scene = _materialized_through(CompletingSquareMainCheckpoint.MISSING_CORNER)
    client = _Client([[_decision_line("clarify_corner")]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    checkpoint = checkpoints[0]
    assert checkpoint.semantic.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
    assert checkpoint.semantic.result_component == CompletingSquareState(
        id="square-lesson",
        last_main_checkpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
        corner_clarified=True,
    )
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].patch_count == 1


@pytest.mark.asyncio
async def test_corrupt_later_checkpoint_fails_before_checkpoint_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = service_module.compile_checkpoint_beat

    def corrupt_last(*args: object, **kwargs: object) -> object:
        compiled = original_compile(*args, **kwargs)
        last = compiled.checkpoints[-1]
        body = last.certificate.body.model_copy(update={"base_low_level_scene_sha256": "f" * 64})
        certificate = CheckpointCompilerCertificateV2(
            body=body,
            certificate_sha256=checkpoint_certificate_sha256(body),
        )
        corrupted = last.model_copy(update={"certificate": certificate})
        return compiled.model_copy(update={"checkpoints": (*compiled.checkpoints[:-1], corrupted)})

    monkeypatch.setattr(service_module, "compile_checkpoint_beat", corrupt_last)
    events = await _collect(
        service_module.SceneAuthoringService(_Client([[_decision_line("start_choreography")]]))
    )

    _zero_checkpoint_failure(
        events,
        code="choreography_integrity_error",
        retryable=False,
    )


@pytest.mark.asyncio
async def test_independent_verifier_receipt_mismatch_fails_before_emission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = service_module.verify_completing_square_checkpoint

    def disagree(*args: object, **kwargs: object) -> CheckpointVerificationReceiptV2:
        receipt = original_verify(*args, **kwargs)
        return CheckpointVerificationReceiptV2(
            component_id=receipt.component_id,
            checkpoint_id=receipt.checkpoint_id,
            operation_targets=receipt.operation_targets,
            obligation_codes=tuple(reversed(receipt.obligation_codes)),
        )

    monkeypatch.setattr(service_module, "verify_completing_square_checkpoint", disagree)
    events = await _collect(
        service_module.SceneAuthoringService(_Client([[_decision_line("start_choreography")]]))
    )

    _zero_checkpoint_failure(
        events,
        code="choreography_integrity_error",
        retryable=False,
    )


@pytest.mark.asyncio
async def test_later_wire_overflow_fails_the_whole_suffix_before_checkpoint_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_encode = service_module.encode_choreography_scene_stream_event

    def fail_last(event: ChoreographySceneStreamEvent) -> str:
        if isinstance(event, ChoreographySceneCheckpointEvent) and event.sequence == 8:
            raise SceneStreamWireError("forced private wire overflow")
        return original_encode(event)

    monkeypatch.setattr(service_module, "encode_choreography_scene_stream_event", fail_last)
    events = await _collect(
        service_module.SceneAuthoringService(_Client([[_decision_line("start_choreography")]]))
    )

    failed = _zero_checkpoint_failure(
        events,
        code="choreography_integrity_error",
        retryable=False,
    )
    assert "forced private" not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget_name", ["MAX_SCENE_NODES", "MAX_SEMANTIC_COMPONENTS"])
async def test_independent_node_and_component_caps_fail_without_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    budget_name: str,
) -> None:
    monkeypatch.setattr(service_module, budget_name, 0)
    events = await _collect(
        service_module.SceneAuthoringService(
            _Client([[_decision_line("start_choreography", stage="setup")]])
        )
    )

    _zero_checkpoint_failure(
        events,
        code="choreography_capacity_limit",
        retryable=True,
    )


@pytest.mark.asyncio
async def test_partial_revision_budget_is_rejected_before_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_revision = MAX_SAFE_SEQUENCE - 1
    client = _Client([[_decision_line("start_choreography")]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("routed checkpoint count must be admitted before compilation")

    monkeypatch.setattr(service_module, "compile_checkpoint_beat", compiler_must_not_run)
    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(
            scene=SceneState(revision=base_revision),
            semantic_scene=SemanticSceneState(revision=base_revision),
        ),
    )

    _zero_checkpoint_failure(
        events,
        code="choreography_capacity_limit",
        retryable=True,
        revision=base_revision,
    )


@pytest.mark.asyncio
async def test_absolute_revision_limit_avoids_provider_dispatch() -> None:
    client = _Client([])
    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(
            scene=SceneState(revision=MAX_SAFE_SEQUENCE),
            semantic_scene=SemanticSceneState(revision=MAX_SAFE_SEQUENCE),
        ),
    )

    _zero_checkpoint_failure(
        events,
        code="revision_limit",
        retryable=False,
        revision=MAX_SAFE_SEQUENCE,
    )
    assert client.calls == []


@pytest.mark.asyncio
async def test_revision_mismatch_fails_before_provider_dispatch() -> None:
    client = _Client([])
    request = SemanticLiveSceneRequest.model_construct(
        prompt="Teach completing the square visually.",
        generation=17,
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=1),
    )

    events = await _collect(service_module.SceneAuthoringService(client), request)

    _zero_checkpoint_failure(
        events,
        code="semantic_base_mismatch",
        retryable=False,
    )
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["unsupported_intent", "no_forward_progress"])
async def test_abstain_is_a_successful_v2_noop(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    client = _Client([[_decision_line("abstain", reason=reason)]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("abstention must not compile")

    monkeypatch.setattr(service_module, "compile_checkpoint_beat", compiler_must_not_run)
    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "choreography_scene_stream_declined",
    ]
    declined = events[-1]
    assert isinstance(declined, ChoreographySceneStreamDeclinedEvent)
    assert declined.reason_code.value == reason
    assert declined.final_revision == 0


@pytest.mark.asyncio
async def test_non_choreography_resolution_declines_without_wrong_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([[_decision_line("start_visual")]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the choreography compiler must not consume a V1 resolution")

    monkeypatch.setattr(service_module, "compile_checkpoint_beat", compiler_must_not_run)
    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "choreography_scene_stream_declined",
    ]
    declined = events[-1]
    assert isinstance(declined, ChoreographySceneStreamDeclinedEvent)
    assert declined.reason_code.value == "unsupported_intent"


@pytest.mark.asyncio
async def test_routing_repair_lifecycle_and_factory_cleanup_are_preserved() -> None:
    client = _Client([["not-json\n"], [_decision_line("start_choreography", stage="setup")]])
    service = service_module.SceneAuthoringService(client_factory=lambda: client)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "choreography_scene_checkpoint",
        "choreography_scene_checkpoint",
        "scene_stream_completed",
    ]
    repairing = events[1]
    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert (repairing.from_attempt, repairing.to_attempt) == (1, 2)
    assert all(checkpoint.attempt == 2 for checkpoint in _checkpoints(events))
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].repaired is True
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_existing_routed_v1_path_remains_semantic_and_unchanged() -> None:
    client = _Client([[_decision_line("start_visual")]])
    service = service_module.SceneAuthoringService(client)

    events = [event async for event in service.stream_routed_semantic_events(_request())]

    assert [event.type for event in events] == [
        "scene_stream_started",
        "semantic_scene_patch",
        "scene_stream_completed",
    ]
    assert isinstance(events[1], SemanticScenePatchEvent)


@pytest.mark.asyncio
async def test_v1_path_rejects_a_v2_component_safely_before_provider_dispatch() -> None:
    scene, semantic_scene = _materialized_through(CompletingSquareMainCheckpoint.PROBLEM)
    client = _Client([])
    service = service_module.SceneAuthoringService(client)

    events = [
        event
        async for event in service.stream_routed_semantic_events(
            _request(scene=scene, semantic_scene=semantic_scene)
        )
    ]

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert failed.code == "semantic_base_mismatch"
    assert client.calls == []
