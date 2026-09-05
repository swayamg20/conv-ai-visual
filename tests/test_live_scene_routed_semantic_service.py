"""Provider-free integration tests for routed semantic scene authoring."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest
from murmur.live_scene import service as service_module
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
)
from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    PythagoreanAreaIdentityDirective,
    PythagoreanStage,
    SemanticSceneState,
    TeachingAct,
    TeachingBeatDraft,
    VisualActAbstainReason,
    compiler_certificate_sha256,
)
from murmur.live_scene.semantic_service_contracts import (
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
    SemanticSceneStreamDeclinedEvent,
)


def _decision_line(
    decision: str,
    *,
    stage: str = "areas",
    reason: str = "unsupported_intent",
) -> str:
    if decision == "start_visual":
        payload = {"v": 1, "decision": decision, "targetStage": stage}
    elif decision == "continue_visual":
        payload = {
            "v": 1,
            "decision": decision,
            "componentId": "areas",
            "targetStage": stage,
        }
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
        prompt="Teach the next useful visual step.",
        generation=generation,
        base_scene=scene or SceneState(revision=0),
        base_semantic_scene=semantic_scene or SemanticSceneState(revision=0),
    )


def _materialized_triangle() -> tuple[SceneState, SemanticSceneState]:
    beat = TeachingBeatDraft(
        beat_id="prefix-triangle",
        narration="Start with the right triangle.",
        act=TeachingAct.INTRODUCE,
        directive=PythagoreanAreaIdentityDirective(
            id="areas",
            reveal_through=PythagoreanStage.TRIANGLE,
        ),
    )
    compiled = compile_teaching_beat(beat, SemanticSceneState(revision=0))
    scene = SceneState(revision=0)
    for atom in compiled.atoms:
        scene = service_module._apply_patch(scene, atom.patch)
    return scene, compiled.result_scene


def _materialized_identity() -> tuple[SceneState, SemanticSceneState]:
    beat = TeachingBeatDraft(
        beat_id="prefix-identity",
        narration="Connect the three square areas into the Pythagorean relationship.",
        act=TeachingAct.CONNECT,
        directive=PythagoreanAreaIdentityDirective(
            id="areas",
            reveal_through=PythagoreanStage.IDENTITY,
        ),
    )
    compiled = compile_teaching_beat(beat, SemanticSceneState(revision=0))
    scene = SceneState(revision=0)
    for atom in compiled.atoms:
        scene = service_module._apply_patch(scene, atom.patch)
    return scene, compiled.result_scene


async def _collect(
    service: service_module.SceneAuthoringService,
    request: SemanticLiveSceneRequest | None = None,
) -> list[object]:
    return [event async for event in service.stream_routed_semantic_events(request or _request())]


def _patches(events: list[object]) -> list[SemanticScenePatchEvent]:
    return [event for event in events if isinstance(event, SemanticScenePatchEvent)]


def _zero_patch_failure(
    events: list[object],
    *,
    code: str,
    retryable: bool,
    revision: int,
) -> SceneStreamFailedEvent:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    assert _patches(events) == []
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert (failed.code, failed.retryable, failed.last_accepted_revision) == (
        code,
        retryable,
        revision,
    )
    return failed


@pytest.mark.asyncio
async def test_start_to_areas_uses_router_surface_and_real_certified_compiler() -> None:
    client = _Client([[_decision_line("start_visual")]])

    events = await _collect(service_module.SceneAuthoringService(client, max_tokens=37))

    patches = _patches(events)
    assert [event.type for event in events] == [
        "scene_stream_started",
        *("semantic_scene_patch" for _ in range(7)),
        "scene_stream_completed",
    ]
    assert [event.semantic.role for event in patches] == list(PYTHAGOREAN_ROLE_ORDER[:7])
    assert [event.semantic.atom_ordinal for event in patches] == list(range(1, 8))
    assert [(event.base_revision, event.result_revision) for event in patches] == [
        (revision, revision + 1) for revision in range(7)
    ]

    previous_certificate = None
    for event in patches:
        certificate = event.semantic.certificate
        assert certificate.certificate_sha256 == compiler_certificate_sha256(certificate.body)
        assert certificate.body.previous_certificate_sha256 == previous_certificate
        assert event.semantic.beat.directive.id == "areas"
        assert event.semantic.beat.directive.reveal_through is PythagoreanStage.AREAS
        previous_certificate = certificate.certificate_sha256

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (completed.final_revision, completed.patch_count, completed.repaired) == (7, 7, False)
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["max_tokens"] == 37
    assert len(client.calls) == 1
    assert client.streams[0].closed is True
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_continuation_emits_only_missing_suffix_and_extends_certificate_head() -> None:
    scene, semantic_scene = _materialized_triangle()
    client = _Client([[_decision_line("continue_visual")]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    patches = _patches(events)
    assert [event.semantic.role for event in patches] == list(PYTHAGOREAN_ROLE_ORDER[1:7])
    assert [event.semantic.atom_ordinal for event in patches] == list(range(2, 8))
    assert [(event.base_revision, event.result_revision) for event in patches] == [
        (revision, revision + 1) for revision in range(1, 7)
    ]
    assert (
        patches[0].semantic.certificate.body.previous_certificate_sha256
        == semantic_scene.certificate_head_sha256
    )
    assert all(event.patch.operations[0].target_id != "areas__triangle" for event in patches)
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (completed.final_revision, completed.patch_count) == (7, 6)


@pytest.mark.asyncio
async def test_identity_followup_streams_only_the_eight_atom_proof_suffix() -> None:
    scene, semantic_scene = _materialized_identity()
    client = _Client([[_decision_line("continue_visual", stage="proof")]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    patches = _patches(events)
    assert [event.semantic.role for event in patches] == list(PYTHAGOREAN_ROLE_ORDER[8:])
    assert [event.semantic.atom_ordinal for event in patches] == list(range(9, 17))
    assert [(event.base_revision, event.result_revision) for event in patches] == [
        (revision, revision + 1) for revision in range(8, 16)
    ]
    assert (
        patches[0].semantic.certificate.body.previous_certificate_sha256
        == semantic_scene.certificate_head_sha256
    )
    assert all(
        event.semantic.beat.directive.reveal_through is PythagoreanStage.PROOF for event in patches
    )
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (completed.final_revision, completed.patch_count) == (16, 8)


@pytest.mark.asyncio
async def test_partial_remaining_capacity_is_retryable_without_a_partial_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_revision = MAX_SAFE_SEQUENCE - 1
    client = _Client([[_decision_line("start_visual")]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("capacity must be classified before compilation")

    monkeypatch.setattr(service_module, "compile_teaching_beat", compiler_must_not_run)
    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(
            scene=SceneState(revision=base_revision),
            semantic_scene=SemanticSceneState(revision=base_revision),
        ),
    )

    failed = _zero_patch_failure(
        events,
        code="semantic_capacity_limit",
        retryable=True,
        revision=base_revision,
    )
    assert failed.attempt == 1


@pytest.mark.asyncio
async def test_raw_node_id_collision_is_a_non_retryable_namespace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized, _semantic = _materialized_triangle()
    raw_scene = SceneState(revision=0, nodes=materialized.nodes)
    client = _Client([[_decision_line("start_visual", stage="triangle")]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("namespace collision must be classified before compilation")

    monkeypatch.setattr(service_module, "compile_teaching_beat", compiler_must_not_run)
    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=raw_scene),
    )

    failed = _zero_patch_failure(
        events,
        code="semantic_namespace_collision",
        retryable=False,
        revision=0,
    )
    assert failed.attempt == 1


@pytest.mark.asyncio
async def test_unexpected_compiler_exception_is_a_safe_integrity_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "compiler leaked private implementation details"
    client = _Client([[_decision_line("start_visual", stage="triangle")]])

    def fail_compiler(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(service_module, "compile_teaching_beat", fail_compiler)
    events = await _collect(service_module.SceneAuthoringService(client))

    failed = _zero_patch_failure(
        events,
        code="semantic_integrity_error",
        retryable=False,
        revision=0,
    )
    assert failed.attempt == 1
    assert secret not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", list(VisualActAbstainReason))
async def test_abstain_is_a_successful_noop_without_compilation(
    monkeypatch: pytest.MonkeyPatch,
    reason: VisualActAbstainReason,
) -> None:
    scene, semantic_scene = _materialized_triangle()
    before_scene, before_semantic_scene = deepcopy(scene), deepcopy(semantic_scene)
    client = _Client([[_decision_line("abstain", reason=reason.value)]])

    def compiler_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("compiler must not run for abstention")

    monkeypatch.setattr(service_module, "compile_teaching_beat", compiler_must_not_run)
    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "semantic_scene_stream_declined",
    ]
    assert _patches(events) == []
    declined = events[-1]
    assert isinstance(declined, SemanticSceneStreamDeclinedEvent)
    assert declined.reason_code is reason
    assert declined.final_revision == scene.revision
    assert declined.attempt == 1
    assert (scene, semantic_scene) == (before_scene, before_semantic_scene)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("second_decision", "expected_types"),
    [
        (
            _decision_line("start_visual", stage="triangle"),
            [
                "scene_stream_started",
                "scene_stream_repairing",
                "semantic_scene_patch",
                "scene_stream_completed",
            ],
        ),
        (
            _decision_line("abstain", reason="no_forward_progress"),
            [
                "scene_stream_started",
                "scene_stream_repairing",
                "semantic_scene_stream_declined",
            ],
        ),
    ],
)
async def test_repaired_result_announces_attempt_two_before_its_output(
    second_decision: str,
    expected_types: list[str],
) -> None:
    client = _Client([["not-json\n"], [second_decision]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == expected_types
    repairing = events[1]
    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert (repairing.from_attempt, repairing.to_attempt) == (1, 2)
    assert getattr(events[2], "attempt", 2) == 2
    if isinstance(events[-1], SceneStreamCompletedEvent):
        assert events[-1].repaired is True
    else:
        assert isinstance(events[-1], SemanticSceneStreamDeclinedEvent)
        assert events[-1].attempt == 2
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_repair_event_reaches_the_consumer_before_second_provider_dispatch() -> None:
    client = _Client([["not-json\n"], [_decision_line("start_visual", stage="triangle")]])
    events = service_module.SceneAuthoringService(client).stream_routed_semantic_events(_request())

    assert (await anext(events)).type == "scene_stream_started"
    repairing = await anext(events)

    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert len(client.calls) == 1

    patch = await anext(events)
    assert isinstance(patch, SemanticScenePatchEvent)
    assert patch.attempt == 2
    assert len(client.calls) == 2
    await events.aclose()


@pytest.mark.asyncio
async def test_closing_at_repair_boundary_prevents_the_second_paid_dispatch() -> None:
    client = _Client([["not-json\n"], [_decision_line("start_visual", stage="triangle")]])
    service = service_module.SceneAuthoringService(client_factory=lambda: client)
    events = service.stream_routed_semantic_events(_request())

    assert (await anext(events)).type == "scene_stream_started"
    assert isinstance(await anext(events), SceneStreamRepairingEvent)
    assert len(client.calls) == 1

    await events.aclose()

    assert len(client.calls) == 1
    assert client.streams[0].closed is True
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_dispatch_admission_counts_and_can_reject_the_repair_call() -> None:
    client = _Client([["not-json\n"], [_decision_line("start_visual", stage="triangle")]])
    reservations = 0

    async def reserve() -> None:
        nonlocal reservations
        reservations += 1
        if reservations == 2:
            raise SceneAdmissionError("provider_rate_limited", "private limiter state")

    service = service_module.SceneAuthoringService(
        client,
        before_provider_dispatch=reserve,
    )
    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert (failed.code, failed.attempt, failed.retryable) == (
        "provider_rate_limited",
        2,
        True,
    )
    assert reservations == 2
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_provider_failure_is_safe_and_does_not_retry() -> None:
    secret = "provider leaked sk-top-secret"
    client = _Client([[RuntimeError(secret)]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert (failed.code, failed.attempt, failed.retryable) == ("provider_error", 1, True)
    assert secret not in failed.model_dump_json(by_alias=True)
    assert len(client.calls) == 1
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_two_invalid_routing_attempts_fail_safely_without_a_third_call() -> None:
    secrets = ("FIRST-RAW-SECRET", "SECOND-RAW-SECRET")
    client = _Client([[f"not-json-{secrets[0]}\n"], [f"[]-{secrets[1]}\n"]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, SceneStreamFailedEvent)
    assert (failed.code, failed.attempt, failed.retryable) == (
        "invalid_visual_act",
        2,
        True,
    )
    wire = "\n".join(event.model_dump_json(by_alias=True) for event in events)
    assert all(secret not in wire for secret in secrets)
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_factory_owned_router_client_closes_once() -> None:
    client = _Client([[_decision_line("abstain")]])
    factory_calls = 0

    def factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        return client

    events = await _collect(service_module.SceneAuthoringService(client_factory=factory))

    assert isinstance(events[-1], SemanticSceneStreamDeclinedEvent)
    assert factory_calls == 1
    assert client.close_calls == 1
    assert client.streams[0].closed is True
