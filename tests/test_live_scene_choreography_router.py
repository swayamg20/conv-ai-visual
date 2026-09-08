from __future__ import annotations

import json
from copy import deepcopy

import pytest
from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ClarifyCornerRouteV2,
    CompletingSquareStage,
    RoutedChoreographyBeatV2,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.semantic_contracts import (
    TEACHING_BEAT_DRAFT_ADAPTER,
    VISUAL_ACT_DECISION_ADAPTER,
    ClarifyCornerDecision,
    ContinueChoreographyDecision,
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    StartChoreographyDecision,
)
from murmur.live_scene.semantic_stream_parser import VisualActDecisionStreamParser
from murmur.live_scene.visual_act_lowering import lower_resolved_choreography_act
from murmur.live_scene.visual_act_router import (
    ResolvedChoreographyAct,
    VisualActRoutingError,
    VisualActRoutingErrorCode,
    resolve_visual_act,
)
from pydantic import ValidationError


def _start(stage: str = "solve") -> dict[str, object]:
    return {
        "v": 1,
        "decision": "start_choreography",
        "componentKind": "completing_square",
        "targetStage": stage,
    }


def _continue(
    stage: str = "solve",
    *,
    component_id: str = "square-lesson",
) -> dict[str, object]:
    return {
        "v": 1,
        "decision": "continue_choreography",
        "componentId": component_id,
        "targetStage": stage,
    }


def _clarify(*, component_id: str = "square-lesson") -> dict[str, object]:
    return {
        "v": 1,
        "decision": "clarify_corner",
        "componentId": component_id,
    }


def _scene(
    checkpoint: CompletingSquareMainCheckpoint | None,
    *,
    clarified: bool = False,
    component_id: str = "square-lesson",
) -> SemanticSceneState:
    revision = (
        0 if checkpoint is None else COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(checkpoint) + 1
    )
    return SemanticSceneState(
        revision=revision,
        components=(
            CompletingSquareState(
                id=component_id,
                last_main_checkpoint=checkpoint,
                corner_clarified=clarified,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_fields"),
    [
        (
            _start(),
            StartChoreographyDecision,
            {"v", "decision", "component_kind", "target_stage"},
        ),
        (
            _continue(),
            ContinueChoreographyDecision,
            {"v", "decision", "component_id", "target_stage"},
        ),
        (
            _clarify(),
            ClarifyCornerDecision,
            {"v", "decision", "component_id"},
        ),
    ],
)
def test_choreography_decisions_are_closed_v1_router_choices(
    payload: dict[str, object],
    expected_type: type[object],
    expected_fields: set[str],
) -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(
        payload,
        by_alias=True,
        by_name=False,
    )

    assert isinstance(decision, expected_type)
    assert set(type(decision).model_fields) == expected_fields
    assert decision.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "forbidden",
    [
        "beatId",
        "narration",
        "equation",
        "latex",
        "x",
        "points",
        "style",
        "operations",
        "patch",
        "choreography",
        "presentation",
        "durationMs",
        "viewport",
        "certificate",
    ],
)
def test_choreography_decisions_reject_compiler_owned_fields(forbidden: str) -> None:
    payload = _start()
    payload[forbidden] = "model-owned"

    with pytest.raises(ValidationError, match="Extra inputs"):
        VISUAL_ACT_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {**_start(), "v": 2},
        {**_start(), "componentKind": "freeform_svg"},
        {**_start(), "targetStage": "proof"},
        {**_continue(), "componentKind": "completing_square"},
        {**_clarify(), "targetStage": "complete"},
        {**_clarify(), "componentId": "1 invalid"},
    ],
)
def test_choreography_decisions_reject_open_or_cross_variant_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        VISUAL_ACT_DECISION_ADAPTER.validate_python(payload)


def test_start_resolves_the_server_owned_component_and_exact_stage_prefix() -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_start("complete"))
    scene = SemanticSceneState(revision=0)
    before = deepcopy(scene)

    resolved = resolve_visual_act(decision, scene)

    assert isinstance(resolved, ResolvedChoreographyAct)
    assert resolved.component_id == "square-lesson"
    assert resolved.component_kind == "completing_square"
    assert isinstance(resolved.route, AdvanceChoreographyRouteV2)
    assert resolved.route.target_stage is CompletingSquareStage.COMPLETE
    assert resolved.missing_checkpoints == COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:6]
    assert scene == before


def test_continue_resolves_only_the_missing_checkpoint_suffix() -> None:
    scene = _scene(CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM)
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue("solve"))

    resolved = resolve_visual_act(decision, scene)

    assert isinstance(resolved, ResolvedChoreographyAct)
    assert resolved.missing_checkpoints == COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[3:]
    assert isinstance(resolved.route, AdvanceChoreographyRouteV2)
    assert resolved.route.target_stage is CompletingSquareStage.SOLVE


def test_clarification_is_available_only_at_the_exact_unclarified_frontier() -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_clarify())
    scene = _scene(CompletingSquareMainCheckpoint.MISSING_CORNER)

    resolved = resolve_visual_act(decision, scene)

    assert isinstance(resolved, ResolvedChoreographyAct)
    assert isinstance(resolved.route, ClarifyCornerRouteV2)
    assert resolved.missing_checkpoints == ()

    for invalid in (
        _scene(CompletingSquareMainCheckpoint.REARRANGE_HALVES),
        _scene(CompletingSquareMainCheckpoint.MISSING_CORNER, clarified=True),
        _scene(CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE),
    ):
        with pytest.raises(VisualActRoutingError) as captured:
            resolve_visual_act(decision, invalid)
        assert captured.value.code is VisualActRoutingErrorCode.CLARIFICATION_UNAVAILABLE


def test_cross_kind_and_non_forward_routes_fail_closed() -> None:
    pythagorean = SemanticSceneState(
        revision=0,
        components=(PythagoreanAreaIdentityState(id="square-lesson"),),
    )
    with pytest.raises(VisualActRoutingError) as captured:
        resolve_visual_act(
            VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue()),
            pythagorean,
        )
    assert captured.value.code is VisualActRoutingErrorCode.COMPONENT_KIND_MISMATCH

    completed_setup = _scene(CompletingSquareMainCheckpoint.AREA_MODEL)
    with pytest.raises(VisualActRoutingError) as captured:
        resolve_visual_act(
            VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue("setup")),
            completed_setup,
        )
    assert captured.value.code is VisualActRoutingErrorCode.NON_FORWARD_TARGET


def test_lowering_adds_only_a_deterministic_server_beat_identity() -> None:
    resolved = resolve_visual_act(
        VISUAL_ACT_DECISION_ADAPTER.validate_python(_start("split")),
        SemanticSceneState(revision=0),
    )
    assert isinstance(resolved, ResolvedChoreographyAct)
    before = deepcopy(resolved)

    beat = lower_resolved_choreography_act(resolved, generation=42)

    assert beat == RoutedChoreographyBeatV2(
        beat_id="route-2a",
        component_id="square-lesson",
        route=AdvanceChoreographyRouteV2(target_stage=CompletingSquareStage.SPLIT),
    )
    assert set(type(beat).model_fields) == {
        "v",
        "beat_id",
        "component_kind",
        "component_id",
        "route",
    }
    assert not {
        "narration",
        "patch",
        "presentation",
        "choreography",
    }.intersection(type(beat).model_fields)
    assert resolved == before


def test_existing_v1_parser_accepts_new_choices_but_legacy_beat_parser_rejects_v2() -> None:
    parser = VisualActDecisionStreamParser()
    decoded = []
    for payload in (_start("setup"), _continue("complete"), _clarify()):
        decoded.extend(parser.feed(json.dumps(payload, separators=(",", ":")) + "\n"))
    decoded.extend(parser.finish())

    assert [decision.decision for decision in decoded] == [
        "start_choreography",
        "continue_choreography",
        "clarify_corner",
    ]

    resolved = resolve_visual_act(
        VISUAL_ACT_DECISION_ADAPTER.validate_python(_start("setup")),
        SemanticSceneState(revision=0),
    )
    assert isinstance(resolved, ResolvedChoreographyAct)
    beat = lower_resolved_choreography_act(resolved, generation=1)
    with pytest.raises(ValidationError):
        TEACHING_BEAT_DRAFT_ADAPTER.validate_python(
            beat.model_dump(mode="json", by_alias=True),
            by_alias=True,
            by_name=False,
        )
