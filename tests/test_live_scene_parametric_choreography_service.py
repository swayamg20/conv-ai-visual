"""Provider-free service tests for Gate 1.6 live parametric choreography."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from murmur.live_scene import parametric_choreography_service as service_module
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ClarifyCornerRouteV2,
    CompletingSquareStage,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_NODES,
    LatexTokenSceneNode,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamRepairingEvent,
)
from murmur.live_scene.parametric_checkpoint_compiler import (
    compile_parametric_checkpoint_beat,
)
from murmur.live_scene.parametric_choreography_director import (
    ParametricChoreographyDirectorResult,
)
from murmur.live_scene.parametric_choreography_requests import (
    PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    ParametricChoreographyDirectorRequestV3,
    ParametricChoreographyReflexRequestV3,
)
from murmur.live_scene.parametric_choreography_routing import (
    ResolvedParametricChoreographyAct,
    StartParametricChoreographyDecisionV1,
    lower_resolved_parametric_choreography_act,
    resolve_parametric_reflex_route,
)
from murmur.live_scene.parametric_choreography_service import (
    ParametricChoreographyService,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricChoreographyFailureCode,
    ParametricChoreographySceneCheckpointEventV3,
    ParametricChoreographySceneStreamDeclinedEventV3,
    ParametricChoreographySceneStreamFailedEventV3,
)
from murmur.live_scene.parametric_choreography_wire import (
    MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    materialize_parametric_nodes,
)
from murmur.live_scene.parametric_completing_square_verifier import (
    verify_parametric_completing_square_checkpoint,
)
from murmur.live_scene.semantic_contracts import (
    PythagoreanAreaIdentityState,
    SemanticSceneState,
)
from murmur.live_scene.wire import SceneStreamWireError

_PROBLEMS = tuple((h, m) for m in range(2, 10) for h in range(1, m))


def _problem(h: int = 4, m: int = 6) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=2 * h,
        rightHandSide=m * m - h * h,
    )


def _problem_text(problem: CompletingSquareProblemSpecV1) -> str:
    return f"x² + {problem.linear_coefficient}x = {problem.right_hand_side}"


def _reflex_request(
    *,
    problem: CompletingSquareProblemSpecV1 | None = None,
    problem_text: str | object | None = ...,  # ``...`` means derive the normal text.
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    route: object | None = None,
    generation: int = 17,
) -> ParametricChoreographyReflexRequestV3:
    spec = problem or _problem()
    text = _problem_text(spec) if problem_text is ... else problem_text
    return ParametricChoreographyReflexRequestV3.model_validate(
        {
            "protocol": PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
            "problemText": text,
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
    problem: CompletingSquareProblemSpecV1 | None = None,
    problem_text: str | object | None = ...,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    generation: int = 17,
) -> ParametricChoreographyDirectorRequestV3:
    spec = problem or _problem()
    text = _problem_text(spec) if problem_text is ... else problem_text
    return ParametricChoreographyDirectorRequestV3.model_validate(
        {
            "protocol": PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
            "problemText": text,
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
    reason: str = "unsupported_intent",
) -> str:
    if action in {"start", "continue"}:
        payload = {"v": 1, "action": action, "stage": stage}
    elif action == "clarify":
        payload = {"v": 1, "action": action}
    else:
        payload = {"v": 1, "action": "abstain", "reasonCode": reason}
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


def _frontier(
    problem: CompletingSquareProblemSpecV1,
    checkpoint: CompletingSquareMainCheckpoint,
    *,
    clarified: bool = False,
) -> tuple[SceneState, SemanticSceneState]:
    component = ParametricCompletingSquareStateV1(
        id="square-lesson",
        problemSpec=problem,
        lastMainCheckpoint=checkpoint,
        cornerClarified=clarified,
    )
    ordinal = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(checkpoint) + 1
    revision = ordinal + int(clarified)
    return (
        SceneState(revision=revision, nodes=materialize_parametric_nodes(component)),
        SemanticSceneState(
            revision=revision,
            components=(component,),
            certificateHeadSha256="a" * 64,
        ),
    )


async def _collect(
    service: ParametricChoreographyService,
    request: ParametricChoreographyReflexRequestV3 | ParametricChoreographyDirectorRequestV3,
) -> list[object]:
    return [event async for event in service.stream_events(request)]


def _checkpoints(events: list[object]) -> list[ParametricChoreographySceneCheckpointEventV3]:
    return [
        event for event in events if isinstance(event, ParametricChoreographySceneCheckpointEventV3)
    ]


def _failure(
    events: list[object],
    code: ParametricChoreographyFailureCode,
    *,
    revision: int = 0,
) -> ParametricChoreographySceneStreamFailedEventV3:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "parametric_choreography_scene_stream_failed",
    ]
    assert _checkpoints(events) == []
    failed = events[-1]
    assert isinstance(failed, ParametricChoreographySceneStreamFailedEventV3)
    assert failed.code is code
    assert failed.last_accepted_revision == revision
    return failed


def _decline(
    events: list[object],
    reason: str,
    *,
    attempt: int = 1,
) -> ParametricChoreographySceneStreamDeclinedEventV3:
    assert [event.type for event in events] == [
        "scene_stream_started",
        "parametric_choreography_scene_stream_declined",
    ]
    declined = events[-1]
    assert isinstance(declined, ParametricChoreographySceneStreamDeclinedEventV3)
    assert declined.reason_code.value == reason
    assert declined.attempt == attempt
    return declined


@pytest.mark.asyncio
@pytest.mark.parametrize("h,m", _PROBLEMS)
async def test_every_supported_problem_starts_through_reflex_without_provider(
    h: int,
    m: int,
) -> None:
    problem = _problem(h, m)
    client = _Client([])
    counters = _Counters(client)
    service = ParametricChoreographyService(
        client_factory=counters.factory,
        before_provider_dispatch=counters.admit,
    )

    events = await _collect(
        service,
        _reflex_request(
            problem=problem,
            route={"intent": "advance", "targetStage": "setup"},
        ),
    )

    assert [item.semantic.checkpoint_id.value for item in _checkpoints(events)] == [
        "problem",
        "area_model",
    ]
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_full_reflex_suffix_is_one_exact_verified_event_chain() -> None:
    problem = _problem()
    request = _reflex_request(problem=problem)
    events = await _collect(ParametricChoreographyService(), request)
    checkpoints = _checkpoints(events)

    assert [event.type for event in events] == [
        "scene_stream_started",
        *("parametric_choreography_scene_checkpoint" for _ in range(8)),
        "scene_stream_completed",
    ]
    assert [event.semantic.checkpoint_id.value for event in checkpoints] == [
        checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER
    ]
    assert [event.sequence for event in checkpoints] == list(range(1, 9))
    assert [(event.base_revision, event.result_revision) for event in checkpoints] == [
        (revision, revision + 1) for revision in range(8)
    ]

    scene = request.base_scene
    previous_head = request.base_semantic_scene.certificate_head_sha256
    for event in checkpoints:
        result_scene = service_module._apply_patch(scene, event.patch)
        assert (
            verify_parametric_completing_square_checkpoint(
                event.semantic.beat.component_id,
                problem,
                event.semantic.checkpoint_id,
                scene,
                result_scene,
                event.patch,
                event.semantic.presentation,
                event.semantic.choreography,
            )
            == event.semantic.receipt
        )
        assert event.semantic.semantic_base_certificate_sha256 == previous_head
        assert (
            event.semantic.semantic_result_certificate_sha256
            == event.semantic.certificate.certificate_sha256
        )
        wire = encode_parametric_choreography_scene_stream_event(event)
        assert len(wire.encode("utf-8")) <= MAX_PARAMETRIC_CHOREOGRAPHY_SSE_EVENT_BYTES
        payload = json.loads(wire.removeprefix("data: ").strip())
        assert PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event
        scene = result_scene
        previous_head = event.semantic.semantic_result_certificate_sha256

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert (completed.final_revision, completed.patch_count, completed.repaired) == (
        8,
        8,
        False,
    )


_FRONTIER_ROUTES: tuple[
    tuple[CompletingSquareMainCheckpoint, object, tuple[str, ...] | None], ...
] = (
    (
        CompletingSquareMainCheckpoint.PROBLEM,
        {"intent": "advance", "targetStage": "setup"},
        ("area_model",),
    ),
    (
        CompletingSquareMainCheckpoint.AREA_MODEL,
        {"intent": "advance", "targetStage": "split"},
        ("split_linear_term", "rearrange_halves"),
    ),
    (
        CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM,
        {"intent": "advance", "targetStage": "split"},
        ("rearrange_halves",),
    ),
    (
        CompletingSquareMainCheckpoint.REARRANGE_HALVES,
        {"intent": "advance", "targetStage": "complete"},
        ("missing_corner", "balance_and_complete"),
    ),
    (
        CompletingSquareMainCheckpoint.MISSING_CORNER,
        {"intent": "clarify_corner"},
        ("corner_detail",),
    ),
    (
        CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE,
        {"intent": "advance", "targetStage": "solve"},
        ("factor_square", "solve_roots"),
    ),
    (
        CompletingSquareMainCheckpoint.FACTOR_SQUARE,
        {"intent": "advance", "targetStage": "solve"},
        ("solve_roots",),
    ),
    (
        CompletingSquareMainCheckpoint.SOLVE_ROOTS,
        {"intent": "advance", "targetStage": "solve"},
        None,
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint,route,expected", _FRONTIER_ROUTES)
async def test_every_main_frontier_resumes_or_declines_exactly(
    checkpoint: CompletingSquareMainCheckpoint,
    route: object,
    expected: tuple[str, ...] | None,
) -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, checkpoint)
    events = await _collect(
        ParametricChoreographyService(),
        _reflex_request(
            problem=problem,
            problem_text=None,
            scene=scene,
            semantic_scene=semantic,
            route=route,
        ),
    )

    if expected is None:
        _decline(events, "no_forward_progress")
        return
    checkpoints = _checkpoints(events)
    assert tuple(item.semantic.checkpoint_id.value for item in checkpoints) == expected
    assert checkpoints[0].base_revision == scene.revision
    assert (
        checkpoints[0].semantic.semantic_base_certificate_sha256 == semantic.certificate_head_sha256
    )
    assert isinstance(events[-1], SceneStreamCompletedEvent)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("problem_text", "reason"),
    [
        (None, "problem_required"),
        ("Teach me something", "problem_required"),
        ("x² + ??? = 20", "problem_ambiguous"),
        ("x² + 8x = 20; x² + 6x = 7", "problem_ambiguous"),
        ("x² + 7x = 20", "problem_unsupported"),
        ("2x² + 8x = 20", "problem_unsupported"),
    ],
)
async def test_every_initial_problem_decline_happens_before_provider_resolution(
    problem_text: str | None,
    reason: str,
) -> None:
    client = _Client([])
    counters = _Counters(client)
    request = _reflex_request(problem_text=problem_text)

    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        request,
    )

    _decline(events, reason)
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_conflicting_continuation_problem_declines_before_provider() -> None:
    accepted = _problem(3, 4)
    other = _problem(4, 6)
    scene, semantic = _frontier(accepted, CompletingSquareMainCheckpoint.AREA_MODEL)
    client = _Client([])
    counters = _Counters(client)

    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(
            problem=other,
            scene=scene,
            semantic_scene=semantic,
            prompt="Continue to split.",
        ),
    )

    _decline(events, "problem_conflict")
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


def _dirty_token(node_id: str = "dirty") -> LatexTokenSceneNode:
    return LatexTokenSceneNode.model_validate(
        {
            "id": node_id,
            "kind": "latex_token",
            "presentation": {"enter": "draw", "exit": "fade"},
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
    "attack",
    [
        "dirty",
        "orphan_head",
        "v1",
        "v2",
        "v3_orphan",
        "missing_head",
        "missing_nodes",
        "foreign_node",
        "mixed",
    ],
)
async def test_all_frontier_attacks_fail_before_factory_or_admission(attack: str) -> None:
    problem = _problem()
    scene = SceneState(revision=0)
    semantic = SemanticSceneState(revision=0)
    if attack == "dirty":
        scene = SceneState(revision=0, nodes=(_dirty_token(),))
    elif attack == "orphan_head":
        semantic = SemanticSceneState(revision=0, certificateHeadSha256="a" * 64)
    elif attack == "v1":
        semantic = SemanticSceneState(
            revision=0,
            components=(PythagoreanAreaIdentityState(id="old"),),
        )
    elif attack == "v2":
        semantic = SemanticSceneState(
            revision=1,
            components=(
                CompletingSquareState(
                    id="square-lesson",
                    lastMainCheckpoint=CompletingSquareMainCheckpoint.PROBLEM,
                ),
            ),
            certificateHeadSha256="a" * 64,
        )
        scene = SceneState(revision=1)
    elif attack == "v3_orphan":
        semantic = SemanticSceneState(
            revision=0,
            components=(
                ParametricCompletingSquareStateV1(
                    id="square-lesson",
                    problemSpec=problem,
                ),
            ),
        )
    elif attack in {"missing_head", "missing_nodes", "foreign_node"}:
        scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.PROBLEM)
        if attack == "missing_head":
            semantic = semantic.model_copy(update={"certificate_head_sha256": None})
        elif attack == "missing_nodes":
            scene = SceneState(revision=scene.revision)
        else:
            scene = SceneState(revision=scene.revision, nodes=(*scene.nodes, _dirty_token()))
    else:
        component = ParametricCompletingSquareStateV1(
            id="square-lesson",
            problemSpec=problem,
            lastMainCheckpoint=CompletingSquareMainCheckpoint.PROBLEM,
        )
        semantic = SemanticSceneState(
            revision=1,
            components=(component, PythagoreanAreaIdentityState(id="old")),
            certificateHeadSha256="a" * 64,
        )
        scene = SceneState(revision=1, nodes=materialize_parametric_nodes(component))

    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(problem=problem, scene=scene, semantic_scene=semantic),
    )

    _failure(
        events,
        ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH,
        revision=scene.revision,
    )
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_revision_mismatch_and_absolute_limit_fail_before_provider() -> None:
    client = _Client([])
    counters = _Counters(client)
    service = ParametricChoreographyService(
        client_factory=counters.factory,
        before_provider_dispatch=counters.admit,
    )
    mismatch = _director_request().model_copy(
        update={"base_semantic_scene": SemanticSceneState(revision=1)}
    )
    events = await _collect(service, mismatch)
    _failure(events, ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH)

    limited = _director_request(
        scene=SceneState(revision=MAX_SAFE_SEQUENCE),
        semantic_scene=SemanticSceneState(revision=MAX_SAFE_SEQUENCE),
    )
    events = await _collect(service, limited)
    _failure(
        events,
        ParametricChoreographyFailureCode.REVISION_LIMIT,
        revision=MAX_SAFE_SEQUENCE,
    )
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_exact_node_ceiling_fails_before_provider_resolution() -> None:
    scene = SceneState(
        revision=0,
        nodes=tuple(_dirty_token(f"dirty_{index}") for index in range(MAX_SCENE_NODES)),
    )
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _reflex_request(scene=scene),
    )

    failed = _failure(
        events,
        ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED,
    )
    assert failed.retryable is False
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_positive_revision_empty_frontier_is_not_a_fresh_start() -> None:
    client = _Client([])
    counters = _Counters(client)
    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(
            scene=SceneState(revision=1),
            semantic_scene=SemanticSceneState(revision=1),
        ),
    )

    _failure(
        events,
        ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH,
        revision=1,
    )
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_wrong_explicit_and_contextual_corner_claims_decline_before_provider() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)
    client = _Client([])
    counters = _Counters(client)
    service = ParametricChoreographyService(
        client_factory=counters.factory,
        before_provider_dispatch=counters.admit,
    )

    explicit = await _collect(
        service,
        _director_request(
            problem=problem,
            prompt="Why is the missing corner 15?",
            scene=scene,
            semantic_scene=semantic,
        ),
    )
    contextual = await _collect(
        service,
        _director_request(
            problem=problem,
            problem_text=None,
            prompt="Why is it 15?",
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    _decline(explicit, "problem_conflict")
    _decline(contextual, "problem_conflict")
    assert (counters.factory_calls, counters.admission_calls, client.calls) == (0, 0, [])


@pytest.mark.asyncio
async def test_correct_corner_claim_and_unrelated_number_reach_director() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)
    client = _Client([[_decision("clarify")], [_decision("clarify")]])
    counters = _Counters(client)
    service = ParametricChoreographyService(
        client_factory=counters.factory,
        before_provider_dispatch=counters.admit,
    )

    correct = await _collect(
        service,
        _director_request(
            problem=problem,
            prompt="The 4 by 4 corner is 16; explain why.",
            scene=scene,
            semantic_scene=semantic,
        ),
    )
    unrelated = await _collect(
        service,
        _director_request(
            problem=problem,
            prompt="Use fewer than 15 words to explain the corner.",
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    assert [item.semantic.checkpoint_id.value for item in _checkpoints(correct)] == [
        "corner_detail"
    ]
    assert [item.semantic.checkpoint_id.value for item in _checkpoints(unrelated)] == [
        "corner_detail"
    ]
    assert (counters.factory_calls, counters.admission_calls, len(client.calls)) == (2, 2, 2)
    assert client.close_calls == 2


@pytest.mark.asyncio
async def test_corner_dimensions_are_not_misread_as_an_area_conflict() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)
    client = _Client([[_decision("clarify")]])
    events = await _collect(
        ParametricChoreographyService(client),
        _director_request(
            problem=problem,
            prompt="Why is the corner 4 by 4?",
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    assert [item.semantic.checkpoint_id.value for item in _checkpoints(events)] == ["corner_detail"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_director_normal_path_resolves_once_and_closes_factory_client() -> None:
    client = _Client([[_decision("start", stage="solve")]])
    counters = _Counters(client)
    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
            max_tokens=4_096,
        ),
        _director_request(prompt="Teach all the way through solving."),
    )

    assert len(_checkpoints(events)) == 8
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].repaired is False
    assert (counters.factory_calls, counters.admission_calls, len(client.calls)) == (1, 1, 1)
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["max_tokens"] == 2_048
    assert all(stream.closed for stream in client.streams)
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_swapped_director_problem_is_rejected_before_checkpoint_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = _problem()
    swapped = _problem(3, 4)
    decision = StartParametricChoreographyDecisionV1(v=1, action="start", stage="setup")
    expected = resolve_parametric_reflex_route(
        AdvanceChoreographyRouteV2(targetStage="setup"),
        problem_spec=bound,
        semantic_scene=SemanticSceneState(revision=0),
    )
    malicious = replace(expected, problem_spec=swapped)

    class _SwappingEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def stream_route(self, **_kwargs: object) -> AsyncIterator[object]:
            yield ParametricChoreographyDirectorResult(decision, malicious, 1)

    monkeypatch.setattr(
        service_module,
        "ParametricChoreographyDirectorEngine",
        _SwappingEngine,
    )
    client = _Client([])
    events = await _collect(
        ParametricChoreographyService(client),
        _director_request(problem=bound),
    )

    _failure(events, ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR)
    assert _checkpoints(events) == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_director_repair_is_announced_and_uses_attempt_two() -> None:
    client = _Client([["not-json\n"], [_decision("start")]])
    counters = _Counters(client)
    events = await _collect(
        ParametricChoreographyService(
            client_factory=counters.factory,
            before_provider_dispatch=counters.admit,
        ),
        _director_request(),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "parametric_choreography_scene_checkpoint",
        "parametric_choreography_scene_checkpoint",
        "scene_stream_completed",
    ]
    repairing = events[1]
    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert all(checkpoint.attempt == 2 for checkpoint in _checkpoints(events))
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    assert events[-1].repaired is True
    assert (counters.factory_calls, counters.admission_calls, len(client.calls)) == (1, 2, 2)
    assert all(stream.closed for stream in client.streams)
    assert client.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["unsupported_intent", "no_forward_progress"])
async def test_director_abstention_is_a_closed_successful_noop(reason: str) -> None:
    client = _Client([[_decision("abstain", reason=reason)]])
    events = await _collect(ParametricChoreographyService(client), _director_request())

    _decline(events, reason)
    assert len(client.calls) == 1
    assert client.streams[0].closed is True
    assert client.close_calls == 0


@pytest.mark.asyncio
async def test_director_continuation_reuses_bound_problem_and_chain_head() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.AREA_MODEL)
    client = _Client([[_decision("continue", stage="complete")]])
    events = await _collect(
        ParametricChoreographyService(client),
        _director_request(
            problem=problem,
            problem_text=None,
            prompt="Continue through completion.",
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    checkpoints = _checkpoints(events)
    assert [item.semantic.checkpoint_id.value for item in checkpoints] == [
        "split_linear_term",
        "rearrange_halves",
        "missing_corner",
        "balance_and_complete",
    ]
    assert checkpoints[0].semantic.problem_spec == problem
    assert (
        checkpoints[0].semantic.semantic_base_certificate_sha256 == semantic.certificate_head_sha256
    )


@pytest.mark.asyncio
async def test_director_clarification_emits_one_atomic_detail_checkpoint() -> None:
    problem = _problem()
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.MISSING_CORNER)
    events = await _collect(
        ParametricChoreographyService(_Client([[_decision("clarify")]])),
        _director_request(
            problem=problem,
            problem_text=None,
            prompt="Why does this corner complete the square?",
            scene=scene,
            semantic_scene=semantic,
        ),
    )

    checkpoints = _checkpoints(events)
    assert len(checkpoints) == 1
    assert checkpoints[0].semantic.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
    assert checkpoints[0].semantic.result_component.corner_clarified is True


@pytest.mark.asyncio
async def test_invalid_second_decision_maps_to_retryable_invalid_visual_act() -> None:
    client = _Client([["bad\n"], ["still-bad\n"]])
    events = await _collect(ParametricChoreographyService(client), _director_request())

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "parametric_choreography_scene_stream_failed",
    ]
    failed = events[-1]
    assert isinstance(failed, ParametricChoreographySceneStreamFailedEventV3)
    assert (failed.code, failed.attempt, failed.retryable) == (
        ParametricChoreographyFailureCode.INVALID_VISUAL_ACT,
        2,
        True,
    )


@pytest.mark.asyncio
async def test_provider_error_and_admission_rejection_have_closed_retryable_codes() -> None:
    provider_events = await _collect(
        ParametricChoreographyService(_Client([[RuntimeError("secret provider text")]])),
        _director_request(),
    )
    provider_failed = _failure(
        provider_events,
        ParametricChoreographyFailureCode.PROVIDER_ERROR,
    )
    assert provider_failed.retryable is True
    assert "secret" not in provider_failed.model_dump_json(by_alias=True)

    client = _Client([])

    async def reject() -> None:
        raise SceneAdmissionError("provider_rate_limited", "private admission detail")

    admission_events = await _collect(
        ParametricChoreographyService(client, before_provider_dispatch=reject),
        _director_request(),
    )
    admission_failed = _failure(
        admission_events,
        ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED,
    )
    assert admission_failed.retryable is True
    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_timeout_closes_its_stream_and_uses_closed_code() -> None:
    hanging = _HangingStream()
    client = _Client([hanging])
    events = await _collect(
        ParametricChoreographyService(client, timeout_seconds=0.01),
        _director_request(),
    )

    failed = _failure(events, ParametricChoreographyFailureCode.PROVIDER_TIMEOUT)
    assert failed.retryable is True
    assert hanging.closed is True


@pytest.mark.asyncio
async def test_factory_failure_maps_to_provider_error_after_validation() -> None:
    factory_calls = 0

    def fail_factory() -> _Client:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("private credential detail")

    events = await _collect(
        ParametricChoreographyService(client_factory=fail_factory),
        _director_request(),
    )

    failed = _failure(events, ParametricChoreographyFailureCode.PROVIDER_ERROR)
    assert failed.retryable is True
    assert factory_calls == 1
    assert "credential" not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
async def test_malformed_factory_client_is_closed_after_validation_failure() -> None:
    client = _MalformedClient()

    events = await _collect(
        ParametricChoreographyService(client_factory=lambda: client),  # type: ignore[arg-type]
        _director_request(),
    )

    _failure(events, ParametricChoreographyFailureCode.PROVIDER_ERROR)
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_partial_revision_budget_is_rejected_before_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = _problem()
    base_revision = MAX_SAFE_SEQUENCE - 1
    scene, semantic = _frontier(problem, CompletingSquareMainCheckpoint.PROBLEM)
    request = _reflex_request(
        problem=problem,
        scene=scene.model_copy(update={"revision": base_revision}),
        semantic_scene=semantic.model_copy(update={"revision": base_revision}),
    )

    def must_not_compile(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("suffix capacity must be checked before compilation")

    monkeypatch.setattr(service_module, "compile_parametric_checkpoint_beat", must_not_compile)
    events = await _collect(ParametricChoreographyService(), request)

    failed = _failure(
        events,
        ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT,
        revision=base_revision,
    )
    assert failed.retryable is False


@pytest.mark.asyncio
async def test_corrupt_last_checkpoint_emits_zero_checkpoint_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = service_module.compile_parametric_checkpoint_beat

    def corrupt_last(*args: object, **kwargs: object) -> object:
        compiled = original(*args, **kwargs)
        last = compiled.checkpoints[-1]
        bad_patch = last.patch.model_copy(update={"narration": "A corrupt late narration."})
        bad_last = last.model_copy(update={"patch": bad_patch})
        return compiled.model_copy(update={"checkpoints": (*compiled.checkpoints[:-1], bad_last)})

    monkeypatch.setattr(service_module, "compile_parametric_checkpoint_beat", corrupt_last)
    events = await _collect(ParametricChoreographyService(), _reflex_request())

    _failure(events, ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_terminal", [False, True])
async def test_late_wire_or_completed_failure_emits_zero_checkpoint_events(
    monkeypatch: pytest.MonkeyPatch,
    fail_terminal: bool,
) -> None:
    original = service_module.encode_parametric_choreography_scene_stream_event

    def fail_late(event: object) -> str:
        if fail_terminal and isinstance(event, SceneStreamCompletedEvent):
            raise SceneStreamWireError("private completed overflow")
        if (
            not fail_terminal
            and isinstance(event, ParametricChoreographySceneCheckpointEventV3)
            and event.sequence == 8
        ):
            raise SceneStreamWireError("private checkpoint overflow")
        return original(event)  # type: ignore[arg-type]

    monkeypatch.setattr(
        service_module,
        "encode_parametric_choreography_scene_stream_event",
        fail_late,
    )
    events = await _collect(ParametricChoreographyService(), _reflex_request())

    failed = _failure(
        events,
        ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
    )
    assert "private" not in failed.model_dump_json(by_alias=True)


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_stream_and_owned_client() -> None:
    hanging = _HangingStream()
    client = _Client([hanging])
    counters = _Counters(client)
    service = ParametricChoreographyService(client_factory=counters.factory)
    stream = service.stream_events(_director_request())

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
        ParametricChoreographyService(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wrong_request_type_is_rejected_without_lifecycle_output() -> None:
    stream = ParametricChoreographyService().stream_events(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ParametricChoreographyRequestV3"):
        await anext(stream)


def test_service_source_does_not_import_private_legacy_service_helpers() -> None:
    source = service_module.__loader__.get_source(service_module.__name__)  # type: ignore[union-attr]
    assert source is not None
    assert "from murmur.live_scene.service import" not in source


def test_compile_path_used_by_service_returns_exact_requested_suffix() -> None:
    problem = _problem()
    semantic = SemanticSceneState(revision=0)
    resolved = resolve_parametric_reflex_route(
        AdvanceChoreographyRouteV2(targetStage=CompletingSquareStage.SETUP),
        problem_spec=problem,
        semantic_scene=semantic,
    )
    assert isinstance(resolved, ResolvedParametricChoreographyAct)
    beat = lower_resolved_parametric_choreography_act(resolved, generation=17)
    compiled = compile_parametric_checkpoint_beat(
        beat,
        base_scene=SceneState(revision=0),
        base_semantic_scene=semantic,
    )
    assert [item.checkpoint_id.value for item in compiled.checkpoints] == [
        "problem",
        "area_model",
    ]


def test_route_contract_examples_remain_closed() -> None:
    assert AdvanceChoreographyRouteV2(targetStage="solve").intent == "advance"
    assert ClarifyCornerRouteV2().intent == "clarify_corner"
