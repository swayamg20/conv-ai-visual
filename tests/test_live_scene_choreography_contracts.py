from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.choreography_contracts import (
    CHOREOGRAPHY_CUE_V1_ADAPTER,
    CHOREOGRAPHY_PLAN_VERSION,
    MAX_CHOREOGRAPHY_HOLD_AFTER_MS,
    MAX_CHOREOGRAPHY_PHASE_MS,
    MAX_CHOREOGRAPHY_PLAN_MS,
    MAX_CHOREOGRAPHY_TARGET_REFERENCES,
    MIN_CHOREOGRAPHY_PHASE_MS,
    PRESENTATION_CHECKPOINT_VERSION,
    ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER,
    ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER,
    ROUTED_CHOREOGRAPHY_BEAT_V3_HASH_DOMAIN,
    ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION,
    ROUTED_CHOREOGRAPHY_BEAT_VERSION,
    AdvanceChoreographyRouteV2,
    ChoreographyEasing,
    ChoreographyPlanV1,
    ClarifyCornerRouteV2,
    CompletingSquareStage,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
    RoutedChoreographyBeatV3,
    ViewportPoseV1,
    choreography_plan_sha256,
    presentation_checkpoint_sha256,
    routed_choreography_beat_sha256,
    routed_choreography_beat_v3_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_sha256
from pydantic import ValidationError


def _advance_beat(
    stage: CompletingSquareStage | str = CompletingSquareStage.SOLVE,
) -> dict[str, object]:
    return {
        "v": ROUTED_CHOREOGRAPHY_BEAT_VERSION,
        "beatId": "beat-solve",
        "componentKind": "completing_square",
        "componentId": "square-lesson",
        "route": {"intent": "advance", "targetStage": stage},
    }


def _clarification_beat() -> dict[str, object]:
    return {
        "v": ROUTED_CHOREOGRAPHY_BEAT_VERSION,
        "beatId": "beat-corner-detail",
        "componentKind": "completing_square",
        "componentId": "square-lesson",
        "route": {"intent": "clarify_corner"},
    }


def _parametric_beat(
    stage: CompletingSquareStage | str = CompletingSquareStage.SOLVE,
) -> dict[str, object]:
    return {
        "v": ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION,
        "beatId": "beat-parametric-solve",
        "componentKind": "completing_square_parametric",
        "componentId": "parametric-lesson",
        "problemSpec": {
            "v": 1,
            "linearCoefficient": 8,
            "rightHandSide": 20,
        },
        "route": {"intent": "advance", "targetStage": stage},
    }


def _plan() -> dict[str, object]:
    return {
        "v": CHOREOGRAPHY_PLAN_VERSION,
        "phase": {
            "cues": [
                {"cue": "enter", "targetIds": ["corner", "dimension_label"]},
                {"cue": "transform", "targetIds": ["strip_left", "strip_top"]},
                {"cue": "emphasize", "targetIds": ["corner"]},
                {"cue": "focus", "targetIds": ["corner", "strip_top"]},
            ],
            "durationMs": 1_800,
            "easing": "ease_in_out",
            "holdAfterMs": 6_000,
        },
    }


def _pose(
    *, x: float = 0.0, y: float = 75.0, width: float = 800.0, height: float = 450.0
) -> dict[str, object]:
    return {"v": 1, "x": x, "y": y, "width": width, "height": height}


def _checkpoint() -> dict[str, object]:
    return {
        "v": PRESENTATION_CHECKPOINT_VERSION,
        "checkpointId": "missing-corner",
        "checkpointNarration": "The two strips leave one three-by-three corner.",
        "baseViewports": {
            "cinematic": _pose(),
            "compact": _pose(x=100.0, y=0.0, width=600.0, height=600.0),
        },
        "resultViewports": {
            "cinematic": _pose(x=180.0, y=105.0, width=440.0, height=247.5),
            "compact": _pose(x=180.0, y=80.0, width=440.0, height=440.0),
        },
        "transientFree": True,
    }


def test_routed_beat_is_a_small_closed_server_owned_surface() -> None:
    assert set(RoutedChoreographyBeatV2.model_fields) == {
        "v",
        "beat_id",
        "component_kind",
        "component_id",
        "route",
    }
    assert set(AdvanceChoreographyRouteV2.model_fields) == {"intent", "target_stage"}
    assert set(ClarifyCornerRouteV2.model_fields) == {"intent"}

    beat = RoutedChoreographyBeatV2.model_validate(_advance_beat())
    assert beat.model_dump(mode="json", by_alias=True) == _advance_beat()
    assert beat.route.target_stage is CompletingSquareStage.SOLVE
    assert "narration" not in RoutedChoreographyBeatV2.model_fields
    assert "presentation" not in RoutedChoreographyBeatV2.model_fields

    with pytest.raises(ValidationError, match="frozen"):
        beat.component_id = "another-component"


def test_parametric_routed_beat_adds_only_the_bound_problem_spec() -> None:
    assert set(RoutedChoreographyBeatV3.model_fields) == {
        "v",
        "beat_id",
        "component_kind",
        "component_id",
        "problem_spec",
        "route",
    }

    payload = _parametric_beat()
    beat = ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER.validate_python(payload)

    assert beat.model_dump(mode="json", by_alias=True) == payload
    assert beat.problem_spec.half_coefficient == 4
    assert beat.problem_spec.corner_value == 16
    assert beat.problem_spec.completed_right_hand_side == 36
    assert beat.route.target_stage is CompletingSquareStage.SOLVE
    assert "narration" not in RoutedChoreographyBeatV3.model_fields
    assert "presentation" not in RoutedChoreographyBeatV3.model_fields


@pytest.mark.parametrize("stage", list(CompletingSquareStage))
def test_parametric_routed_beat_reuses_only_the_closed_v2_routes(
    stage: CompletingSquareStage,
) -> None:
    advance = _parametric_beat(stage)
    decoded = ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER.validate_python(advance)
    assert decoded.route.target_stage is stage

    clarification = _parametric_beat()
    clarification["route"] = {"intent": "clarify_corner"}
    decoded = ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER.validate_python(clarification)
    assert isinstance(decoded.route, ClarifyCornerRouteV2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("v", ROUTED_CHOREOGRAPHY_BEAT_VERSION),
        ("componentKind", "completing_square"),
        ("problemSpec", None),
    ],
)
def test_parametric_routed_beat_rejects_v2_or_unbound_identity(
    field: str,
    value: object,
) -> None:
    payload = _parametric_beat()
    payload[field] = value
    with pytest.raises(ValidationError):
        ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER.validate_python(payload)

    legacy = _advance_beat()
    legacy["problemSpec"] = _parametric_beat()["problemSpec"]
    with pytest.raises(ValidationError, match="Extra inputs"):
        ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER.validate_python(legacy)


@pytest.mark.parametrize("stage", list(CompletingSquareStage))
def test_routed_beat_accepts_every_closed_main_stage(stage: CompletingSquareStage) -> None:
    beat = ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER.validate_python(_advance_beat(stage))

    assert beat.route.intent == "advance"
    assert beat.route.target_stage is stage


def test_routed_beat_accepts_only_the_bounded_corner_clarification() -> None:
    beat = ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER.validate_python(_clarification_beat())

    assert beat.model_dump(mode="json", by_alias=True) == _clarification_beat()
    assert isinstance(beat.route, ClarifyCornerRouteV2)

    invalid = _clarification_beat()
    route = invalid["route"]
    assert isinstance(route, dict)
    route["targetStage"] = "complete"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER.validate_python(invalid)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("v",), 1),
        (("componentKind",), "pythagorean_area_identity"),
        (("route", "targetStage"), "animate_everything"),
        (("route", "intent"), "explain"),
    ],
)
def test_routed_beat_rejects_open_or_wrong_routes(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = _advance_beat()
    target = payload
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        RoutedChoreographyBeatV2.model_validate(payload)


@pytest.mark.parametrize(
    ("location", "forbidden"),
    [
        ("beat", {"narration": "Model-authored caption"}),
        ("beat", {"durationMs": 800}),
        ("beat", {"viewport": {"x": 0, "y": 0}}),
        ("route", {"coordinates": [100, 200]}),
        ("route", {"presentation": {"easing": "bounce"}}),
    ],
)
def test_routed_beat_rejects_every_compiler_owned_concern(
    location: str,
    forbidden: dict[str, object],
) -> None:
    payload = _advance_beat()
    if location == "beat":
        payload.update(forbidden)
    else:
        route = payload["route"]
        assert isinstance(route, dict)
        route.update(forbidden)

    with pytest.raises(ValidationError, match="Extra inputs"):
        RoutedChoreographyBeatV2.model_validate(payload)


@pytest.mark.parametrize("cue", ["enter", "exit", "transform", "emphasize", "focus"])
def test_each_closed_cue_round_trips_with_only_canonical_target_ids(cue: str) -> None:
    payload = {"cue": cue, "targetIds": ["node_a", "node_b"]}

    decoded = CHOREOGRAPHY_CUE_V1_ADAPTER.validate_python(payload)

    assert (
        CHOREOGRAPHY_CUE_V1_ADAPTER.dump_python(
            decoded,
            mode="json",
            by_alias=True,
        )
        == payload
    )
    assert isinstance(decoded.target_ids, tuple)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"cue": "spin", "targetIds": ["node_a"]}, "union_tag_invalid"),
        ({"cue": "enter", "targetIds": []}, "at least 1"),
        ({"cue": "enter", "targetIds": ["node_a", "node_a"]}, "must be unique"),
        (
            {"cue": "enter", "targetIds": ["node_b", "node_a"]},
            "canonical lexical order",
        ),
        ({"cue": "focus", "targetIds": ["#corner"]}, "string_pattern_mismatch"),
        (
            {"cue": "transform", "targetIds": ["node_a"], "x": 100},
            "Extra inputs",
        ),
        (
            {"cue": "emphasize", "targetIds": ["node_a"], "properties": {}},
            "Extra inputs",
        ),
    ],
)
def test_cues_fail_closed_on_open_motion_or_noncanonical_targets(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        CHOREOGRAPHY_CUE_V1_ADAPTER.validate_python(payload)


def test_plan_contains_exactly_one_bounded_parallel_phase() -> None:
    plan = ChoreographyPlanV1.model_validate(_plan())

    assert plan.model_dump(mode="json", by_alias=True) == _plan()
    assert plan.phase.total_ms == 7_800
    assert tuple(cue.cue for cue in plan.phase.cues) == (
        "enter",
        "transform",
        "emphasize",
        "focus",
    )

    with pytest.raises(ValidationError, match="frozen"):
        plan.phase.duration_ms = 2_000

    multiple_phases = {"v": 1, "phases": [_plan()["phase"], _plan()["phase"]]}
    with pytest.raises(ValidationError, match="Extra inputs"):
        ChoreographyPlanV1.model_validate(multiple_phases)


@pytest.mark.parametrize("easing", list(ChoreographyEasing))
def test_plan_accepts_only_purposeful_non_overshooting_easing(
    easing: ChoreographyEasing,
) -> None:
    payload = _plan()
    phase = payload["phase"]
    assert isinstance(phase, dict)
    phase["easing"] = easing.value

    assert ChoreographyPlanV1.model_validate(payload).phase.easing is easing


@pytest.mark.parametrize("easing", ["bounce", "elastic", "back", "power4.out"])
def test_plan_rejects_unknown_or_raw_gsap_easing(easing: str) -> None:
    payload = _plan()
    phase = payload["phase"]
    assert isinstance(phase, dict)
    phase["easing"] = easing

    with pytest.raises(ValidationError):
        ChoreographyPlanV1.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("durationMs", MIN_CHOREOGRAPHY_PHASE_MS - 1),
        ("durationMs", MAX_CHOREOGRAPHY_PHASE_MS + 1),
        ("durationMs", 1_200.5),
        ("holdAfterMs", -1),
        ("holdAfterMs", MAX_CHOREOGRAPHY_HOLD_AFTER_MS + 1),
    ],
)
def test_phase_rejects_out_of_budget_or_fractional_timing(field: str, value: object) -> None:
    payload = _plan()
    phase = payload["phase"]
    assert isinstance(phase, dict)
    phase[field] = value

    with pytest.raises(ValidationError):
        ChoreographyPlanV1.model_validate(payload)


def test_plan_enforces_total_duration_and_target_reference_budgets() -> None:
    over_duration = _plan()
    phase = over_duration["phase"]
    assert isinstance(phase, dict)
    phase["durationMs"] = MAX_CHOREOGRAPHY_PHASE_MS
    phase["holdAfterMs"] = MAX_CHOREOGRAPHY_PLAN_MS - MAX_CHOREOGRAPHY_PHASE_MS + 1
    with pytest.raises(ValidationError, match="total duration budget"):
        ChoreographyPlanV1.model_validate(over_duration)

    over_targets = _plan()
    phase = over_targets["phase"]
    assert isinstance(phase, dict)
    first = [f"enter_{index:02d}" for index in range(16)]
    second = [f"transform_{index:02d}" for index in range(16)]
    third = ["emphasize_00"]
    phase["cues"] = [
        {"cue": "enter", "targetIds": first},
        {"cue": "transform", "targetIds": second},
        {"cue": "emphasize", "targetIds": third},
    ]
    assert sum(len(cue["targetIds"]) for cue in phase["cues"]) == (
        MAX_CHOREOGRAPHY_TARGET_REFERENCES + 1
    )
    with pytest.raises(ValidationError, match="target-reference budget"):
        ChoreographyPlanV1.model_validate(over_targets)


def test_plan_requires_unique_cue_kinds_in_canonical_order() -> None:
    duplicate = _plan()
    phase = duplicate["phase"]
    assert isinstance(phase, dict)
    phase["cues"] = [
        {"cue": "focus", "targetIds": ["corner"]},
        {"cue": "focus", "targetIds": ["strip_top"]},
    ]
    with pytest.raises(ValidationError, match="cue kinds must be unique"):
        ChoreographyPlanV1.model_validate(duplicate)

    unordered = _plan()
    phase = unordered["phase"]
    assert isinstance(phase, dict)
    cues = phase["cues"]
    assert isinstance(cues, list)
    phase["cues"] = list(reversed(cues))
    with pytest.raises(ValidationError, match="canonical cue order"):
        ChoreographyPlanV1.model_validate(unordered)


def test_presentation_checkpoint_binds_exact_cinematic_and_compact_transitions() -> None:
    checkpoint = PresentationCheckpointV1.model_validate(_checkpoint())

    assert checkpoint.model_dump(mode="json", by_alias=True) == _checkpoint()
    assert checkpoint.base_viewports.cinematic.width == 800.0
    assert checkpoint.result_viewports.compact.height == 440.0
    assert checkpoint.transient_free is True
    assert set(PresentationCheckpointV1.model_fields) == {
        "v",
        "checkpoint_id",
        "checkpoint_narration",
        "base_viewports",
        "result_viewports",
        "transient_free",
    }

    with pytest.raises(ValidationError, match="frozen"):
        checkpoint.result_viewports.compact.width = 300.0


def test_component_id_uses_the_existing_32_character_semantic_budget() -> None:
    accepted = _advance_beat()
    accepted["componentId"] = "c" * 32
    assert RoutedChoreographyBeatV2.model_validate(accepted).component_id == "c" * 32

    rejected = _advance_beat()
    rejected["componentId"] = "c" * 33
    with pytest.raises(ValidationError, match="at most 32 characters"):
        RoutedChoreographyBeatV2.model_validate(rejected)


def test_presentation_checkpoint_rejects_missing_layouts_and_open_requirements() -> None:
    missing_compact = _checkpoint()
    viewports = missing_compact["resultViewports"]
    assert isinstance(viewports, dict)
    del viewports["compact"]
    with pytest.raises(ValidationError, match="Field required"):
        PresentationCheckpointV1.model_validate(missing_compact)

    unknown_layout = _checkpoint()
    viewports = unknown_layout["baseViewports"]
    assert isinstance(viewports, dict)
    viewports["tablet"] = _pose()
    with pytest.raises(ValidationError, match="Extra inputs"):
        PresentationCheckpointV1.model_validate(unknown_layout)

    non_terminal = _checkpoint()
    non_terminal["transientFree"] = False
    with pytest.raises(ValidationError):
        PresentationCheckpointV1.model_validate(non_terminal)


@pytest.mark.parametrize(
    "pose",
    [
        _pose(x=1.0, width=800.0),
        _pose(y=151.0, height=450.0),
        _pose(width=0.0),
        _pose(height=float("inf")),
        _pose(x=-1.0),
    ],
)
def test_viewport_pose_must_be_finite_positive_and_inside_the_board(
    pose: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ViewportPoseV1.model_validate(pose)


def test_all_three_contract_hashes_are_canonical_stable_and_domain_separated() -> None:
    beat_payload = _advance_beat()
    reordered_beat_payload = dict(reversed(tuple(beat_payload.items())))
    beat = RoutedChoreographyBeatV2.model_validate(beat_payload)
    reordered_beat = RoutedChoreographyBeatV2.model_validate(reordered_beat_payload)
    plan = ChoreographyPlanV1.model_validate(_plan())
    checkpoint = PresentationCheckpointV1.model_validate(_checkpoint())

    assert routed_choreography_beat_sha256(beat) == routed_choreography_beat_sha256(reordered_beat)
    assert routed_choreography_beat_sha256(beat) == (
        "0a64ea8ab8c19498b59ce6fde11bead1a89c0c7b8df04493d511a961708b1e45"
    )
    assert choreography_plan_sha256(plan) == (
        "59cdded79697d9f9bd04ccf157615a12105dbbb0103b4c622c62b9bfeaa0ddfd"
    )
    assert presentation_checkpoint_sha256(checkpoint) == (
        "d3df146fe16a1bb7f848c52935cdb2ffc94a930bc54a786d59997b0b069399eb"
    )
    assert (
        len(
            {
                routed_choreography_beat_sha256(beat),
                choreography_plan_sha256(plan),
                presentation_checkpoint_sha256(checkpoint),
            }
        )
        == 3
    )

    changed_plan = deepcopy(_plan())
    changed_phase = changed_plan["phase"]
    assert isinstance(changed_phase, dict)
    changed_phase["holdAfterMs"] = 6_001
    assert choreography_plan_sha256(ChoreographyPlanV1.model_validate(changed_plan)) != (
        choreography_plan_sha256(plan)
    )

    changed_checkpoint = deepcopy(_checkpoint())
    changed_checkpoint["checkpointNarration"] = "Unicode remains exact: 3 \u00d7 3 = 9."
    assert presentation_checkpoint_sha256(
        PresentationCheckpointV1.model_validate(changed_checkpoint)
    ) != presentation_checkpoint_sha256(checkpoint)


def test_parametric_beat_hash_is_canonical_problem_bound_and_v3_domain_separated() -> None:
    payload = _parametric_beat()
    reordered = dict(reversed(tuple(payload.items())))
    beat = RoutedChoreographyBeatV3.model_validate(payload)

    digest = routed_choreography_beat_v3_sha256(beat)

    assert digest == routed_choreography_beat_v3_sha256(
        RoutedChoreographyBeatV3.model_validate(reordered)
    )
    assert digest == canonical_sha256(payload, domain=ROUTED_CHOREOGRAPHY_BEAT_V3_HASH_DOMAIN)
    assert digest == "e4acdaef610dedd80d155cd12e4dd5bdb64c74b717c7d634b940effc10cbc6c5"
    assert digest != canonical_sha256(payload, domain="murmur:routed-choreography-beat:v2")

    changed = deepcopy(payload)
    problem_spec = changed["problemSpec"]
    assert isinstance(problem_spec, dict)
    problem_spec.update(linearCoefficient=6, rightHandSide=7)
    assert (
        routed_choreography_beat_v3_sha256(RoutedChoreographyBeatV3.model_validate(changed))
        != digest
    )
