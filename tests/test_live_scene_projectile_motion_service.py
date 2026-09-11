"""Transactional service coverage for Gate 1.7 projectile choreography."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from murmur.live_scene import projectile_motion_service as service_module
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_NODES,
    LatexTokenSceneNode,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStage,
    RetargetProjectileMotionRouteV1,
)
from murmur.live_scene.projectile_motion_director import (
    ProjectileMotionDirectorResult,
)
from murmur.live_scene.projectile_motion_requests import (
    PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    ProjectileMotionDirectorRequestV1,
    ProjectileMotionReflexRequestV1,
)
from murmur.live_scene.projectile_motion_routing import (
    ResolvedProjectileMotionAct,
    StartProjectileMotionDecisionV1,
    lower_resolved_projectile_motion_act,
    resolve_projectile_motion_director_decision,
    resolve_projectile_motion_reflex_route,
)
from murmur.live_scene.projectile_motion_service import ProjectileMotionService
from murmur.live_scene.projectile_motion_service_contracts import (
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographyFailureCode,
    ProjectileChoreographySceneCheckpointEventV1,
    ProjectileChoreographySceneStreamDeclinedEventV1,
    ProjectileChoreographySceneStreamFailedEventV1,
)
from murmur.live_scene.projectile_motion_verifier import (
    verify_projectile_motion_frontier,
)
from murmur.live_scene.projectile_motion_wire import (
    MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState
from murmur.live_scene.visual_act_engine import (
    VisualActEngineError,
    VisualActEngineErrorCode,
    VisualActRoutingRepairing,
)
from murmur.live_scene.wire import SceneStreamWireError

SUPPORTED_PROBLEMS = tuple(
    ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)
    for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
    for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
)


def _problem(speed: int = 20, angle: int = 45) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)


def _reflex_request(
    *,
    problem: ProjectileMotionProblemSpecV1 | None = None,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    route: object | None = None,
    generation: int = 17,
) -> ProjectileMotionReflexRequestV1:
    spec = problem or _problem()
    return ProjectileMotionReflexRequestV1.model_validate(
        {
            "protocol": PROJECTILE_CHOREOGRAPHY_PROTOCOL,
            "problemSpec": spec.model_dump(mode="json", by_alias=True),
            "generation": generation,
            "baseScene": (scene or SceneState(revision=0)).model_dump(mode="json", by_alias=True),
            "baseSemanticScene": (semantic_scene or SemanticSceneState(revision=0)).model_dump(
                mode="json", by_alias=True
            ),
            "routingMode": "reflex",
            "requestedRoute": route or {"intent": "advance", "targetStage": "solve"},
        }
    )


def _director_request(
    *,
    prompt: str = "Teach the setup first.",
    problem: ProjectileMotionProblemSpecV1 | None = None,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    generation: int = 17,
) -> ProjectileMotionDirectorRequestV1:
    spec = problem or _problem()
    return ProjectileMotionDirectorRequestV1.model_validate(
        {
            "protocol": PROJECTILE_CHOREOGRAPHY_PROTOCOL,
            "problemSpec": spec.model_dump(mode="json", by_alias=True),
            "generation": generation,
            "baseScene": (scene or SceneState(revision=0)).model_dump(mode="json", by_alias=True),
            "baseSemanticScene": (semantic_scene or SemanticSceneState(revision=0)).model_dump(
                mode="json", by_alias=True
            ),
            "routingMode": "director",
            "prompt": prompt,
        }
    )


def _decision(
    action: str,
    *,
    stage: str = "setup",
    topic: str = "apex_acceleration",
    reason: str = "unsupported_intent",
) -> str:
    payload: dict[str, object] = {"v": 1, "action": action}
    if action in {"start", "continue"}:
        payload["stage"] = stage
    elif action == "clarify":
        payload["topic"] = topic
    else:
        payload["reasonCode"] = reason
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


class _HangingStream:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.closed = False

    def __aiter__(self) -> _HangingStream:
        return self

    async def __anext__(self) -> str:
        self.entered.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, attempts: list[list[object] | _HangingStream]) -> None:
        self._attempts = attempts
        self.calls: list[dict[str, object]] = []
        self.streams: list[_Stream | _HangingStream] = []
        self.close_calls = 0

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> AsyncIterator[str | bytes]:
        attempt = self._attempts[len(self.calls)]
        stream: _Stream | _HangingStream
        stream = attempt if isinstance(attempt, _HangingStream) else _Stream(attempt)
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


class _MalformedClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class _Counters:
    def __init__(self, client: _Client) -> None:
        self.client = client
        self.factory_calls = 0
        self.admission_calls = 0

    def factory(self) -> _Client:
        self.factory_calls += 1
        return self.client

    async def admit(self) -> None:
        self.admission_calls += 1


def _compiled_route(
    problem: ProjectileMotionProblemSpecV1,
    route: AdvanceProjectileMotionRouteV1
    | ClarifyProjectileMotionRouteV1
    | RetargetProjectileMotionRouteV1,
    *,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
):
    base_scene = scene or SceneState(revision=0)
    base_semantic = semantic_scene or SemanticSceneState(revision=0)
    resolved = resolve_projectile_motion_reflex_route(
        route,
        problem_spec=problem,
        semantic_scene=base_semantic,
    )
    beat = lower_resolved_projectile_motion_act(resolved, generation=7)
    return compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic,
    )


def _frontier(
    problem: ProjectileMotionProblemSpecV1,
    stage: ProjectileMotionStage,
) -> tuple[SceneState, SemanticSceneState]:
    compiled = _compiled_route(problem, AdvanceProjectileMotionRouteV1(targetStage=stage))
    return compiled.result_scene, compiled.result_semantic_scene


def _advance_frontier(
    problem: ProjectileMotionProblemSpecV1,
    scene: SceneState,
    semantic_scene: SemanticSceneState,
    route: ClarifyProjectileMotionRouteV1 | RetargetProjectileMotionRouteV1,
) -> tuple[SceneState, SemanticSceneState]:
    compiled = _compiled_route(
        problem,
        route,
        scene=scene,
        semantic_scene=semantic_scene,
    )
    return compiled.result_scene, compiled.result_semantic_scene


async def _collect(
    service: ProjectileMotionService,
    request: ProjectileMotionReflexRequestV1 | ProjectileMotionDirectorRequestV1,
) -> list[object]:
    return [event async for event in service.stream_events(request)]


def _checkpoints(events: list[object]) -> list[ProjectileChoreographySceneCheckpointEventV1]:
    return [
        event for event in events if isinstance(event, ProjectileChoreographySceneCheckpointEventV1)
    ]


def _failure(
    events: list[object],
    code: ProjectileChoreographyFailureCode,
    *,
    revision: int = 0,
) -> ProjectileChoreographySceneStreamFailedEventV1:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "projectile_choreography_scene_stream_failed",
    ]
    assert _checkpoints(events) == []
    failed = events[-1]
    assert isinstance(failed, ProjectileChoreographySceneStreamFailedEventV1)
    assert failed.code is code
    assert failed.last_accepted_revision == revision
    return failed


def _decline(
    events: list[object],
    reason: ProjectileChoreographyDeclineReason,
    *,
    attempt: int = 1,
    revision: int = 0,
) -> ProjectileChoreographySceneStreamDeclinedEventV1:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "projectile_choreography_scene_stream_declined",
    ]
    assert _checkpoints(events) == []
    declined = events[-1]
    assert isinstance(declined, ProjectileChoreographySceneStreamDeclinedEventV1)
    assert declined.reason_code is reason
    assert declined.attempt == attempt
    assert declined.final_revision == revision
    return declined


def _dirty_token(node_id: str = "dirty") -> LatexTokenSceneNode:
    return LatexTokenSceneNode.model_validate(
        {
            "id": node_id,
            "kind": "latex_token",
            "presentation": {"enter": "fade", "exit": "fade"},
            "x": 10.0,
            "y": 10.0,
            "width": 50.0,
            "height": 30.0,
            "anchor": "start",
            "latex": "dirty",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": 20.0,
                "opacity": 1.0,
            },
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", SUPPORTED_PROBLEMS, ids=lambda item: f"{item.speed_mps}@{item.angle_deg}"
)
async def test_every_supported_problem_completes_full_reflex_suffix_without_provider(
    problem: ProjectileMotionProblemSpecV1,
) -> None:
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _reflex_request(problem=problem),
    )

    assert [event.semantic.checkpoint_id.value for event in _checkpoints(events)] == [
        checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    ]
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_full_reflex_suffix_is_one_exact_wire_verified_event_chain() -> None:
    ticks = iter((10.0, 10.125, 10.2))
    request = _reflex_request(generation=31)
    events = await _collect(ProjectileMotionService(clock=lambda: next(ticks)), request)
    checkpoints = _checkpoints(events)

    assert [event.type for event in events] == [
        "scene_stream_started",
        *("projectile_choreography_scene_checkpoint" for _ in range(6)),
        "scene_stream_completed",
    ]
    assert isinstance(events[0], SceneStreamStartedEvent)
    assert [event.sequence for event in checkpoints] == list(range(1, 7))
    assert [(event.base_revision, event.result_revision) for event in checkpoints] == [
        (revision, revision + 1) for revision in range(6)
    ]

    scene = request.base_scene
    semantic_scene = request.base_semantic_scene
    for event in checkpoints:
        assert event.semantic.base_component == (
            semantic_scene.components[0] if semantic_scene.components else None
        )
        assert event.semantic.semantic_base_revision == semantic_scene.revision
        assert (
            event.semantic.semantic_base_certificate_sha256
            == semantic_scene.certificate_head_sha256
        )
        scene = service_module._apply_patch(scene, event.patch)
        semantic_scene = SemanticSceneState(
            revision=event.result_revision,
            components=(event.semantic.result_component,),
            certificateHeadSha256=event.semantic.semantic_result_certificate_sha256,
        )
        assert (
            verify_projectile_motion_frontier(
                event.semantic.result_component,
                scene,
            )
            is None
        )

        wire = encode_projectile_choreography_scene_stream_event(event)
        assert len(wire.encode("utf-8")) <= MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES
        payload = json.loads(wire.removeprefix("data: ").strip())
        assert PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (
        completed.generation,
        completed.final_revision,
        completed.patch_count,
        completed.repaired,
    ) == (31, 6, 6, False)
    assert completed.first_patch_ms == pytest.approx(125.0)
    assert completed.total_ms == pytest.approx(200.0)


_CONTINUATIONS = (
    (ProjectileMotionStage.SETUP, "launch", ("decompose_velocity",)),
    (
        ProjectileMotionStage.LAUNCH,
        "flight",
        ("trace_ascent", "apex_state", "trace_descent"),
    ),
    (ProjectileMotionStage.FLIGHT, "solve", ("summary",)),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("base_stage", "target_stage", "expected"), _CONTINUATIONS)
async def test_reflex_continuation_emits_only_exact_missing_suffix(
    base_stage: ProjectileMotionStage,
    target_stage: str,
    expected: tuple[str, ...],
) -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, base_stage)
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(client_factory=counters.factory),
        _reflex_request(
            problem=problem,
            scene=scene,
            semantic_scene=semantic,
            route={"intent": "advance", "targetStage": target_stage},
        ),
    )

    checkpoints = _checkpoints(events)
    assert tuple(event.semantic.checkpoint_id.value for event in checkpoints) == expected
    assert checkpoints[0].base_revision == scene.revision
    assert (
        checkpoints[0].semantic.semantic_base_certificate_sha256 == semantic.certificate_head_sha256
    )
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert (counters.factory_calls, client.calls) == (0, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "topic", "checkpoint_id"),
    [
        (
            ProjectileMotionStage.LAUNCH,
            "horizontal_velocity",
            ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL,
        ),
        (
            ProjectileMotionStage.FLIGHT,
            "apex_acceleration",
            ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL,
        ),
        (
            ProjectileMotionStage.FLIGHT,
            "flight_symmetry",
            ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL,
        ),
    ],
)
async def test_reflex_clarification_is_one_atomic_topic_bound_checkpoint(
    stage: ProjectileMotionStage,
    topic: str,
    checkpoint_id: ProjectileMotionCheckpointId,
) -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, stage)
    events = await _collect(
        ProjectileMotionService(),
        _reflex_request(
            problem=problem,
            scene=scene,
            semantic_scene=semantic,
            route={"intent": "clarify", "topic": topic},
        ),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    assert checkpoints[0].semantic.checkpoint_id is checkpoint_id
    assert checkpoints[0].semantic.clarification_topic.value == topic
    assert checkpoints[0].semantic.result_component.active_clarification.value == topic
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].patch_count == 1


@pytest.mark.asyncio
async def test_reflex_retarget_changes_only_problem_and_preserves_settled_frontier() -> None:
    base_problem = _problem(20, 45)
    target_problem = _problem(30, 60)
    scene, semantic = _frontier(base_problem, ProjectileMotionStage.FLIGHT)
    base_component = semantic.components[0]
    events = await _collect(
        ProjectileMotionService(),
        _reflex_request(
            problem=base_problem,
            scene=scene,
            semantic_scene=semantic,
            route={
                "intent": "retarget",
                "targetProblemSpec": target_problem.model_dump(mode="json", by_alias=True),
            },
        ),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    event = checkpoints[0]
    assert event.semantic.checkpoint_id is ProjectileMotionCheckpointId.PARAMETERS_RETARGETED
    assert event.semantic.base_problem_spec == base_problem
    assert event.semantic.result_problem_spec == target_problem
    assert event.semantic.result_component.last_main_checkpoint == (
        base_component.last_main_checkpoint
    )
    assert event.semantic.result_component.clarified_topics == base_component.clarified_topics
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].patch_count == 1


@pytest.mark.asyncio
async def test_retarget_preserves_active_clarification_and_patch_history() -> None:
    base_problem = _problem()
    target_problem = _problem(25, 60)
    scene, semantic = _frontier(base_problem, ProjectileMotionStage.FLIGHT)
    scene, semantic = _advance_frontier(
        base_problem,
        scene,
        semantic,
        ClarifyProjectileMotionRouteV1(topic="apex_acceleration"),
    )
    events = await _collect(
        ProjectileMotionService(),
        _reflex_request(
            problem=base_problem,
            scene=scene,
            semantic_scene=semantic,
            route=RetargetProjectileMotionRouteV1(targetProblemSpec=target_problem),
        ),
    )

    event = _checkpoints(events)[0]
    assert event.semantic.result_component.active_clarification is (
        ProjectileMotionClarificationTopic.APEX_ACCELERATION
    )
    result_scene = service_module._apply_patch(scene, event.patch)
    assert result_scene.nodes[-1].id == "projectile-lesson__clarify_apex_acceleration"
    assert (
        verify_projectile_motion_frontier(
            event.semantic.result_component,
            result_scene,
        )
        is None
    )


@pytest.mark.asyncio
async def test_backward_complete_and_repeated_routes_decline_without_provider() -> None:
    problem = _problem()
    flight_scene, flight_semantic = _frontier(problem, ProjectileMotionStage.FLIGHT)
    complete_scene, complete_semantic = _frontier(problem, ProjectileMotionStage.SOLVE)
    clarified_scene, clarified_semantic = _advance_frontier(
        problem,
        flight_scene,
        flight_semantic,
        ClarifyProjectileMotionRouteV1(topic="flight_symmetry"),
    )
    cases = (
        (
            flight_scene,
            flight_semantic,
            {"intent": "advance", "targetStage": "launch"},
        ),
        (
            complete_scene,
            complete_semantic,
            {"intent": "advance", "targetStage": "solve"},
        ),
        (
            clarified_scene,
            clarified_semantic,
            {"intent": "clarify", "topic": "flight_symmetry"},
        ),
        (
            flight_scene,
            flight_semantic,
            {
                "intent": "retarget",
                "targetProblemSpec": problem.model_dump(mode="json", by_alias=True),
            },
        ),
    )
    client = _Client([])
    counters = _Counters(client)
    service = ProjectileMotionService(client_factory=counters.factory)

    for scene, semantic, route in cases:
        events = await _collect(
            service,
            _reflex_request(
                problem=problem,
                scene=scene,
                semantic_scene=semantic,
                route=route,
            ),
        )
        _decline(
            events,
            ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS,
            revision=scene.revision,
        )
    assert (counters.factory_calls, client.calls) == (0, [])


@pytest.mark.asyncio
async def test_problem_conflict_declines_before_director_factory_or_admission() -> None:
    accepted = _problem()
    other = _problem(30, 60)
    scene, semantic = _frontier(accepted, ProjectileMotionStage.LAUNCH)
    request = _director_request(
        problem=accepted,
        scene=scene,
        semantic_scene=semantic,
    ).model_copy(update={"problem_spec": other})
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        request,
    )

    _decline(
        events,
        ProjectileChoreographyDeclineReason.PROBLEM_CONFLICT,
        revision=scene.revision,
    )
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_swapped_reflex_start_problem_fails_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_problem = _problem(20, 45)
    substituted_problem = _problem(30, 60)
    substituted = resolve_projectile_motion_reflex_route(
        AdvanceProjectileMotionRouteV1(targetStage="solve"),
        problem_spec=substituted_problem,
        semantic_scene=SemanticSceneState(revision=0),
    )

    monkeypatch.setattr(
        service_module,
        "resolve_projectile_motion_reflex_route",
        lambda *_args, **_kwargs: substituted,
    )
    events = await _collect(
        ProjectileMotionService(),
        _reflex_request(problem=requested_problem),
    )

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert failed.retryable is False


@pytest.mark.asyncio
async def test_unexpected_frontier_verifier_fault_is_sanitized_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, ProjectileMotionStage.LAUNCH)
    secret = "private verifier implementation detail"

    def fail_verifier(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        service_module,
        "verify_projectile_motion_frontier",
        fail_verifier,
    )
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(
            problem=problem,
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
        revision=scene.revision,
    )
    assert secret not in failed.model_dump_json(by_alias=True)
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack",
    [
        "dirty_empty",
        "positive_empty",
        "missing_head",
        "missing_node",
        "foreign_node",
        "reordered",
        "revision_mismatch",
    ],
)
async def test_invalid_base_attacks_fail_before_provider_resolution(attack: str) -> None:
    problem = _problem()
    scene = SceneState(revision=0)
    semantic = SemanticSceneState(revision=0)
    if attack == "dirty_empty":
        scene = SceneState(revision=0, nodes=(_dirty_token(),))
    elif attack == "positive_empty":
        scene = SceneState(revision=1)
        semantic = SemanticSceneState(revision=1)
    else:
        scene, semantic = _frontier(problem, ProjectileMotionStage.LAUNCH)
        if attack == "missing_head":
            semantic = semantic.model_copy(update={"certificate_head_sha256": None})
        elif attack == "missing_node":
            scene = scene.model_copy(update={"nodes": scene.nodes[1:]})
        elif attack == "foreign_node":
            scene = scene.model_copy(
                update={"nodes": (*scene.nodes, _dirty_token("foreign__node"))}
            )
        elif attack == "reordered":
            scene = scene.model_copy(
                update={"nodes": (scene.nodes[1], scene.nodes[0], *scene.nodes[2:])}
            )
        else:
            semantic = semantic.model_copy(update={"revision": semantic.revision + 1})

    request = _director_request()
    request = request.model_copy(update={"base_scene": scene, "base_semantic_scene": semantic})
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        request,
    )

    _failure(
        events,
        ProjectileChoreographyFailureCode.SEMANTIC_BASE_MISMATCH,
        revision=scene.revision,
    )
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_revision_and_node_capacity_fail_before_provider() -> None:
    client = _Client([])
    counters = _Counters(client)
    service = ProjectileMotionService(
        client_factory=counters.factory,
        before_provider_dispatch=counters.admit,
    )
    limited = _director_request().model_copy(
        update={
            "base_scene": SceneState(revision=MAX_SAFE_SEQUENCE),
            "base_semantic_scene": SemanticSceneState(revision=MAX_SAFE_SEQUENCE),
        }
    )
    events = await _collect(service, limited)
    _failure(
        events,
        ProjectileChoreographyFailureCode.REVISION_LIMIT,
        revision=MAX_SAFE_SEQUENCE,
    )

    full = SceneState(
        revision=0,
        nodes=tuple(_dirty_token(f"dirty_{index}") for index in range(MAX_SCENE_NODES)),
    )
    events = await _collect(
        service,
        _reflex_request(scene=full),
    )
    _failure(events, ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED)
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_partial_revision_budget_is_rejected_before_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, ProjectileMotionStage.SETUP)
    revision = MAX_SAFE_SEQUENCE - 1
    request = _reflex_request(
        problem=problem,
        scene=scene.model_copy(update={"revision": revision}),
        semantic_scene=semantic.model_copy(update={"revision": revision}),
        route={"intent": "advance", "targetStage": "solve"},
    )

    def must_not_compile(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("revision capacity must fail before compilation")

    monkeypatch.setattr(
        service_module,
        "compile_projectile_motion_checkpoint_beat",
        must_not_compile,
    )
    events = await _collect(ProjectileMotionService(), request)

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT,
        revision=revision,
    )
    assert failed.retryable is False


@pytest.mark.asyncio
async def test_corrupt_last_compiled_checkpoint_emits_zero_checkpoint_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = service_module.compile_projectile_motion_checkpoint_beat

    def corrupt_last(*args: object, **kwargs: object) -> object:
        compiled = original(*args, **kwargs)
        last = compiled.checkpoints[-1]
        patch = last.patch.model_copy(update={"narration": "private corrupt late narration"})
        corrupted = last.model_copy(update={"patch": patch})
        return compiled.model_copy(update={"checkpoints": (*compiled.checkpoints[:-1], corrupted)})

    monkeypatch.setattr(
        service_module,
        "compile_projectile_motion_checkpoint_beat",
        corrupt_last,
    )
    events = await _collect(ProjectileMotionService(), _reflex_request())

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert "private" not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_completed", [False, True])
async def test_late_checkpoint_or_completed_wire_failure_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
    fail_completed: bool,
) -> None:
    original = service_module.encode_projectile_choreography_scene_stream_event

    def fail_late(event: object) -> str:
        if fail_completed and isinstance(event, SceneStreamCompletedEvent):
            raise SceneStreamWireError("private completed overflow")
        if (
            not fail_completed
            and isinstance(event, ProjectileChoreographySceneCheckpointEventV1)
            and event.sequence == 6
        ):
            raise SceneStreamWireError("private late checkpoint overflow")
        return original(event)  # type: ignore[arg-type]

    monkeypatch.setattr(
        service_module,
        "encode_projectile_choreography_scene_stream_event",
        fail_late,
    )
    events = await _collect(ProjectileMotionService(), _reflex_request())

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert "private" not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
async def test_director_normal_path_resolves_once_and_closes_only_owned_client() -> None:
    owned = _Client([[_decision("start", stage="solve")]])
    counters = _Counters(owned)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
            max_tokens=4_096,
        ),
        _director_request(prompt="Teach the complete flight."),
    )

    assert len(_checkpoints(events)) == 6
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].repaired is False
    assert (counters.factory_calls, counters.admission_calls, len(owned.calls)) == (1, 1, 1)
    assert owned.calls[0]["temperature"] == 0.0
    assert owned.calls[0]["max_tokens"] == 2_048
    assert all(stream.closed for stream in owned.streams)
    assert owned.close_calls == 1

    injected = _Client([[_decision("start", stage="setup")]])
    injected_events = await _collect(
        ProjectileMotionService(injected),
        _director_request(),
    )
    assert isinstance(injected_events[-1], SceneStreamCompletedEvent)
    assert injected.streams[0].closed is True
    assert injected.close_calls == 0


@pytest.mark.asyncio
async def test_director_repair_is_announced_once_and_attempt_two_owns_suffix() -> None:
    client = _Client([["not-json\n"], [_decision("start", stage="setup")]])
    counters = _Counters(client)
    events = await _collect(
        ProjectileMotionService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "projectile_choreography_scene_checkpoint",
        "scene_stream_completed",
    ]
    assert isinstance(events[1], SceneStreamRepairingEvent)
    assert all(event.attempt == 2 for event in _checkpoints(events))
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].repaired is True
    assert (counters.factory_calls, counters.admission_calls, len(client.calls)) == (1, 2, 2)
    assert all(stream.closed for stream in client.streams)
    assert client.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("unsupported_intent", ProjectileChoreographyDeclineReason.UNSUPPORTED_INTENT),
        ("no_forward_progress", ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS),
    ],
)
async def test_director_abstention_is_closed_successful_noop(
    reason: str,
    expected: ProjectileChoreographyDeclineReason,
) -> None:
    client = _Client([[_decision("abstain", reason=reason)]])
    events = await _collect(ProjectileMotionService(client), _director_request())

    _decline(events, expected)
    assert len(client.calls) == 1
    assert client.streams[0].closed is True
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_director_continuation_and_clarification_reuse_accepted_chain() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, ProjectileMotionStage.LAUNCH)
    continuation = await _collect(
        ProjectileMotionService(_Client([[_decision("continue", stage="flight")]])),
        _director_request(
            problem=problem,
            scene=scene,
            semantic_scene=semantic,
            prompt="Show the flight.",
        ),
    )
    continued = _checkpoints(continuation)
    assert [event.semantic.checkpoint_id.value for event in continued] == [
        "trace_ascent",
        "apex_state",
        "trace_descent",
    ]
    assert (
        continued[0].semantic.semantic_base_certificate_sha256 == semantic.certificate_head_sha256
    )

    clarification = await _collect(
        ProjectileMotionService(_Client([[_decision("clarify", topic="horizontal_velocity")]])),
        _director_request(
            problem=problem,
            scene=scene,
            semantic_scene=semantic,
            prompt="Why is horizontal velocity constant?",
        ),
    )
    clarified = _checkpoints(clarification)
    assert len(clarified) == 1
    assert clarified[0].semantic.checkpoint_id is (
        ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL
    )


@pytest.mark.asyncio
async def test_director_result_is_re_resolved_locally_before_compilation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _problem()
    semantic = SemanticSceneState(revision=0)
    decision = StartProjectileMotionDecisionV1(v=1, action="start", stage="setup")
    expected = resolve_projectile_motion_director_decision(
        decision,
        problem_spec=problem,
        semantic_scene=semantic,
    )
    assert isinstance(expected, ResolvedProjectileMotionAct)
    malicious = replace(expected, component_id="hijacked-projectile")

    class _TamperingEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            yield ProjectileMotionDirectorResult(decision, malicious, 1)

    monkeypatch.setattr(
        service_module,
        "ProjectileMotionDirectorEngine",
        _TamperingEngine,
    )
    client = _Client([])
    events = await _collect(
        ProjectileMotionService(client),
        _director_request(problem=problem),
    )

    _failure(events, ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR)
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_attempt", [0, 3, True])
async def test_director_result_rejects_unjoined_provider_attempts(
    monkeypatch: pytest.MonkeyPatch,
    bad_attempt: object,
) -> None:
    problem = _problem()
    decision = StartProjectileMotionDecisionV1(v=1, action="start", stage="setup")
    resolved = resolve_projectile_motion_director_decision(
        decision,
        problem_spec=problem,
        semantic_scene=SemanticSceneState(revision=0),
    )
    assert isinstance(resolved, ResolvedProjectileMotionAct)

    class _BadAttemptEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            yield ProjectileMotionDirectorResult(
                decision,
                resolved,
                bad_attempt,  # type: ignore[arg-type]
            )

    monkeypatch.setattr(
        service_module,
        "ProjectileMotionDirectorEngine",
        _BadAttemptEngine,
    )
    events = await _collect(
        ProjectileMotionService(_Client([])),
        _director_request(problem=problem),
    )

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert (failed.attempt, failed.retryable) == (1, False)


@pytest.mark.asyncio
async def test_director_rejects_a_repair_boundary_after_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _problem()
    decision = StartProjectileMotionDecisionV1(v=1, action="start", stage="setup")
    resolved = resolve_projectile_motion_director_decision(
        decision,
        problem_spec=problem,
        semantic_scene=SemanticSceneState(revision=0),
    )
    assert isinstance(resolved, ResolvedProjectileMotionAct)

    class _ResultThenRepairEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            yield ProjectileMotionDirectorResult(decision, resolved, 2)
            yield VisualActRoutingRepairing()

    monkeypatch.setattr(
        service_module,
        "ProjectileMotionDirectorEngine",
        _ResultThenRepairEngine,
    )
    events = await _collect(
        ProjectileMotionService(_Client([])),
        _director_request(problem=problem),
    )

    failed = _failure(
        events,
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert (failed.attempt, failed.retryable) == (1, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("announce_repair", "reported_attempts", "expected_attempt"),
    [(False, 2, 1), (True, 0, 2), (True, 1, 2)],
)
async def test_director_error_attempt_is_derived_from_repair_boundary(
    monkeypatch: pytest.MonkeyPatch,
    announce_repair: bool,
    reported_attempts: int,
    expected_attempt: int,
) -> None:
    class _InconsistentErrorEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            if announce_repair:
                yield VisualActRoutingRepairing()
            raise VisualActEngineError(
                VisualActEngineErrorCode.PROVIDER_ERROR,
                provider_attempts=reported_attempts,  # type: ignore[arg-type]
            )

    monkeypatch.setattr(
        service_module,
        "ProjectileMotionDirectorEngine",
        _InconsistentErrorEngine,
    )
    events = await _collect(
        ProjectileMotionService(_Client([])),
        _director_request(),
    )

    expected_types = ["scene_stream_started"]
    if announce_repair:
        expected_types.append("scene_stream_repairing")
    expected_types.append("projectile_choreography_scene_stream_failed")
    assert [event.type for event in events] == expected_types
    assert _checkpoints(events) == []
    failed = events[-1]
    assert isinstance(failed, ProjectileChoreographySceneStreamFailedEventV1)
    assert (
        failed.code,
        failed.attempt,
        failed.retryable,
    ) == (
        ProjectileChoreographyFailureCode.PROVIDER_ERROR,
        expected_attempt,
        True,
    )


@pytest.mark.asyncio
async def test_invalid_second_director_decision_maps_to_retryable_failure() -> None:
    client = _Client([["bad\n"], ["still-bad\n"]])
    events = await _collect(ProjectileMotionService(client), _director_request())

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "projectile_choreography_scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, ProjectileChoreographySceneStreamFailedEventV1)
    assert (failed.code, failed.attempt, failed.retryable) == (
        ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT,
        2,
        True,
    )


@pytest.mark.asyncio
async def test_provider_and_admission_errors_use_closed_codes_without_text_leakage() -> None:
    secret = "secret provider credential detail"
    provider_events = await _collect(
        ProjectileMotionService(_Client([[RuntimeError(secret)]])),
        _director_request(),
    )
    provider = _failure(provider_events, ProjectileChoreographyFailureCode.PROVIDER_ERROR)
    assert provider.retryable is True
    assert secret not in provider.model_dump_json(by_alias=True)

    client = _Client([])

    async def reject() -> None:
        raise SceneAdmissionError("provider_rate_limited", "private admission detail")

    admission_events = await _collect(
        ProjectileMotionService(client, before_provider_dispatch=reject),
        _director_request(),
    )
    admission = _failure(
        admission_events,
        ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED,
    )
    assert admission.retryable is True
    assert "private" not in admission.model_dump_json(by_alias=True)
    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_timeout_closes_stream_and_uses_closed_retryable_code() -> None:
    hanging = _HangingStream()
    client = _Client([hanging])
    events = await _collect(
        ProjectileMotionService(client, timeout_seconds=0.01),
        _director_request(),
    )

    failed = _failure(events, ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT)
    assert failed.retryable is True
    assert hanging.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("engine_code", "service_code", "retryable"),
    [
        (
            VisualActEngineErrorCode.CONTEXT_INVALID,
            ProjectileChoreographyFailureCode.CONTEXT_TOO_LARGE,
            False,
        ),
        (
            VisualActEngineErrorCode.PROVIDER_RATE_LIMIT,
            ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED,
            True,
        ),
        (
            VisualActEngineErrorCode.PROVIDER_TIMEOUT,
            ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT,
            True,
        ),
        (
            VisualActEngineErrorCode.PROVIDER_ERROR,
            ProjectileChoreographyFailureCode.PROVIDER_ERROR,
            True,
        ),
        (
            VisualActEngineErrorCode.INVALID_VISUAL_ACT,
            ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT,
            True,
        ),
        (
            VisualActEngineErrorCode.INTERNAL_ERROR,
            ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
            False,
        ),
    ],
)
async def test_every_director_error_maps_to_its_closed_service_failure(
    monkeypatch: pytest.MonkeyPatch,
    engine_code: VisualActEngineErrorCode,
    service_code: ProjectileChoreographyFailureCode,
    retryable: bool,
) -> None:
    class _FailingEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            if False:
                yield object()
            raise VisualActEngineError(engine_code, provider_attempts=1)

    monkeypatch.setattr(
        service_module,
        "ProjectileMotionDirectorEngine",
        _FailingEngine,
    )
    events = await _collect(
        ProjectileMotionService(_Client([])),
        _director_request(),
    )

    failed = _failure(events, service_code)
    assert failed.retryable is retryable


@pytest.mark.asyncio
async def test_factory_failure_and_malformed_owned_client_map_and_cleanup() -> None:
    calls = 0

    def fail_factory() -> _Client:
        nonlocal calls
        calls += 1
        raise RuntimeError("private credential detail")

    events = await _collect(
        ProjectileMotionService(client_factory=fail_factory),
        _director_request(),
    )
    failed = _failure(events, ProjectileChoreographyFailureCode.PROVIDER_ERROR)
    assert calls == 1
    assert "credential" not in failed.model_dump_json(by_alias=True)

    malformed = _MalformedClient()
    events = await _collect(
        ProjectileMotionService(client_factory=lambda: malformed),  # type: ignore[arg-type]
        _director_request(),
    )
    _failure(events, ProjectileChoreographyFailureCode.PROVIDER_ERROR)
    assert malformed.close_calls == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_stream_and_owned_client() -> None:
    hanging = _HangingStream()
    client = _Client([hanging])
    counters = _Counters(client)
    stream = ProjectileMotionService(client_factory=counters.factory).stream_events(
        _director_request()
    )

    started = await anext(stream)
    assert started.type == "scene_stream_started"
    pending = asyncio.create_task(anext(stream))
    await hanging.entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert hanging.closed is True
    assert client.close_calls == 1
    assert counters.factory_calls == 1


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"client": object(), "client_factory": lambda: object()}, ValueError),
        ({"client_factory": "not callable"}, TypeError),
        ({"clock": None}, TypeError),
        ({"max_tokens": True}, ValueError),
        ({"max_tokens": 0}, ValueError),
        ({"timeout_seconds": float("nan")}, ValueError),
        ({"before_provider_dispatch": "not callable"}, TypeError),
    ],
)
def test_constructor_fails_closed(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        ProjectileMotionService(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wrong_request_type_is_rejected_without_lifecycle_output() -> None:
    stream = ProjectileMotionService().stream_events(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ProjectileMotionRequestV1"):
        await anext(stream)
