"""Provider-free contract and state tests for visual-act routing."""

from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.contracts import LIVE_SCENE_SCHEMA_VERSION
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    VISUAL_ACT_DECISION_ADAPTER,
    AbstainVisualDecision,
    ContinueVisualDecision,
    PythagoreanAreaIdentityState,
    PythagoreanStage,
    SemanticSceneState,
    StartVisualDecision,
    VisualActAbstainReason,
)
from murmur.live_scene.visual_act_router import (
    VisualActRoutingError,
    VisualActRoutingErrorCode,
    resolve_visual_act,
)
from pydantic import ValidationError


def _start(stage: str = "identity") -> dict[str, object]:
    return {
        "v": LIVE_SCENE_SCHEMA_VERSION,
        "decision": "start_visual",
        "targetStage": stage,
    }


def _continue(stage: str = "identity", component_id: str = "areas") -> dict[str, object]:
    return {
        "v": LIVE_SCENE_SCHEMA_VERSION,
        "decision": "continue_visual",
        "componentId": component_id,
        "targetStage": stage,
    }


def _abstain(reason: str = "unsupported_intent") -> dict[str, object]:
    return {
        "v": LIVE_SCENE_SCHEMA_VERSION,
        "decision": "abstain",
        "reasonCode": reason,
    }


def _scene(prefix_length: int, *, component_id: str = "areas") -> SemanticSceneState:
    return SemanticSceneState(
        revision=prefix_length,
        components=(
            PythagoreanAreaIdentityState(
                id=component_id,
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:prefix_length],
            ),
        ),
        certificate_head_sha256="0" * 64 if prefix_length else None,
    )


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_fields"),
    [
        (
            _start(),
            StartVisualDecision,
            {"v", "decision", "target_stage"},
        ),
        (
            _continue(),
            ContinueVisualDecision,
            {"v", "decision", "component_id", "target_stage"},
        ),
        (
            _abstain(),
            AbstainVisualDecision,
            {"v", "decision", "reason_code"},
        ),
    ],
)
def test_decision_is_a_minimal_strict_discriminated_union(
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
    with pytest.raises(ValidationError, match="frozen"):
        decision.decision = "abstain"  # type: ignore[misc]


@pytest.mark.parametrize(
    "forbidden",
    [
        "narration",
        "act",
        "beatId",
        "x",
        "points",
        "style",
        "color",
        "equation",
        "latex",
        "nodeId",
        "operations",
        "patch",
        "receipt",
        "certificate",
        "generation",
        "attempt",
        "revision",
        "provider",
        "model",
        "temperature",
        "reasoning",
        "metadata",
    ],
)
def test_decision_rejects_non_routing_fields(forbidden: str) -> None:
    payload = _start()
    payload[forbidden] = "not-router-owned"

    with pytest.raises(ValidationError, match="Extra inputs"):
        VISUAL_ACT_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {**_start(), "decision": "continue_visual"},
        {**_continue(), "decision": "start_visual"},
        {**_start(), "componentId": "model-owned-id"},
        {**_abstain(), "targetStage": "identity"},
        {**_abstain(), "componentId": "areas"},
        {**_start(), "componentKind": "pythagorean_area_identity"},
        {**_start(), "reasonCode": "unsupported_intent"},
        {**_continue(), "componentKind": "pythagorean_area_identity"},
    ],
)
def test_decision_rejects_fields_from_another_variant(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VISUAL_ACT_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {**_start(), "v": 2},
        {**_start(), "decision": "draw_visual"},
        {**_start(), "componentKind": "freeform_svg"},
        {**_start(), "targetStage": "proof"},
        {**_continue(), "componentId": "1 invalid"},
        {**_abstain(), "reasonCode": "model_unsure"},
    ],
)
def test_decision_rejects_values_outside_the_closed_vocabulary(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        VISUAL_ACT_DECISION_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("stage", list(PythagoreanStage))
def test_start_resolves_each_supported_stage_to_its_exact_prefix(stage: PythagoreanStage) -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_start(stage.value))
    scene = SemanticSceneState(revision=0)
    before = scene.model_dump(mode="json", by_alias=True)

    resolved = resolve_visual_act(decision, scene)

    assert resolved is not None
    assert resolved.component_kind == "pythagorean_area_identity"
    assert resolved.component_id == "areas"
    assert resolved.target_stage is stage
    target_length = {"triangle": 1, "areas": 7, "identity": 8}[stage.value]
    assert resolved.missing_roles == PYTHAGOREAN_ROLE_ORDER[:target_length]
    assert scene.model_dump(mode="json", by_alias=True) == before


@pytest.mark.parametrize(
    ("prefix_length", "stage", "expected_roles"),
    [
        (1, "areas", PYTHAGOREAN_ROLE_ORDER[1:7]),
        (3, "areas", PYTHAGOREAN_ROLE_ORDER[3:7]),
        (7, "identity", PYTHAGOREAN_ROLE_ORDER[7:8]),
    ],
)
def test_continue_reuses_component_and_resolves_only_the_missing_suffix(
    prefix_length: int,
    stage: str,
    expected_roles: tuple[object, ...],
) -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue(stage))
    scene = _scene(prefix_length)
    before = deepcopy(scene)

    resolved = resolve_visual_act(decision, scene)

    assert resolved is not None
    assert resolved.component_id == "areas"
    assert resolved.target_stage.value == stage
    assert resolved.missing_roles == expected_roles
    assert scene == before


@pytest.mark.parametrize(
    ("prefix_length", "stage"),
    [(1, "triangle"), (3, "triangle"), (7, "areas"), (8, "identity")],
)
def test_continue_rejects_every_non_forward_target(prefix_length: int, stage: str) -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue(stage))

    with pytest.raises(VisualActRoutingError) as captured:
        resolve_visual_act(decision, _scene(prefix_length))

    assert captured.value.code is VisualActRoutingErrorCode.NON_FORWARD_TARGET


@pytest.mark.parametrize("reason", list(VisualActAbstainReason))
def test_abstain_is_a_valid_no_mutation_resolution(reason: VisualActAbstainReason) -> None:
    decision = VISUAL_ACT_DECISION_ADAPTER.validate_python(_abstain(reason.value))
    scene = _scene(8)
    before = deepcopy(scene)

    assert resolve_visual_act(decision, scene) is None
    assert scene == before


def test_continue_with_an_unknown_component_fails_closed() -> None:
    with pytest.raises(VisualActRoutingError) as captured:
        resolve_visual_act(
            VISUAL_ACT_DECISION_ADAPTER.validate_python(_continue(component_id="missing")),
            SemanticSceneState(revision=0),
        )

    assert captured.value.code is VisualActRoutingErrorCode.COMPONENT_NOT_FOUND


def test_start_rejects_a_second_component_that_would_overlap_existing_geometry() -> None:
    scene = SemanticSceneState(
        revision=0,
        components=(PythagoreanAreaIdentityState(id="areas"),),
    )

    with pytest.raises(VisualActRoutingError) as captured:
        resolve_visual_act(
            VISUAL_ACT_DECISION_ADAPTER.validate_python(_start()),
            scene,
        )

    assert captured.value.code is VisualActRoutingErrorCode.COMPONENT_ALREADY_EXISTS


def test_resolver_rejects_untyped_inputs() -> None:
    with pytest.raises(TypeError, match="decision"):
        resolve_visual_act("start_visual", SemanticSceneState(revision=0))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="scene"):
        resolve_visual_act(  # type: ignore[arg-type]
            VISUAL_ACT_DECISION_ADAPTER.validate_python(_start()),
            {"revision": 0, "components": []},
        )
