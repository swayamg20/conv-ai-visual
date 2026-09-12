"""Strict contract tests for the Gate 1.8 semantic storyboard language."""

from __future__ import annotations

from itertools import combinations

import pytest
from murmur.live_scene.projectile_motion_contracts import (
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    AbstainStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    StoryboardClaimId,
    TraceStoryboardRecordV1,
    paired_projectile_comparison_sha256,
    semantic_storyboard_program_sha256,
    semantic_storyboard_record_sha256,
    semantic_storyboard_scene_sha256,
)
from pydantic import ValidationError


def _problem(
    speed: int = 20,
    angles: tuple[int, int] = (30, 60),
) -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)


def _decode(payload: dict[str, object]) -> object:
    return SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python(payload)


def test_all_nine_supported_same_speed_angle_pairs_round_trip_exactly() -> None:
    pairs = tuple(combinations(SUPPORTED_PROJECTILE_ANGLES_DEG, 2))
    assert len(pairs) == 3

    problems = tuple(
        _problem(speed, pair) for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS for pair in pairs
    )

    assert len(problems) == 9
    for problem in problems:
        assert problem.model_dump(mode="json", by_alias=True) == {
            "v": 1,
            "speedMps": problem.speed_mps,
            "anglesDeg": list(problem.angles_deg),
        }


@pytest.mark.parametrize(
    "payload",
    [
        {"v": True, "speedMps": 20, "anglesDeg": [30, 60]},
        {"v": 1, "speedMps": True, "anglesDeg": [30, 60]},
        {"v": 1, "speedMps": "20", "anglesDeg": [30, 60]},
        {"v": 1, "speedMps": 40, "anglesDeg": [30, 60]},
        {"v": 1, "speedMps": 20, "anglesDeg": [30]},
        {"v": 1, "speedMps": 20, "anglesDeg": [30, 30]},
        {"v": 1, "speedMps": 20, "anglesDeg": [60, 30]},
        {"v": 1, "speedMps": 20, "anglesDeg": [30, 90]},
        {"v": 1, "speedMps": 20, "anglesDeg": [30, 60], "gravity": 9.81},
    ],
)
def test_pair_spec_rejects_coercion_unsupported_or_nonascending_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PairedProjectileComparisonSpecV1.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "expected_type", "expected_fields"),
    [
        (
            {"v": 1, "act": "reveal", "conceptId": "range_formula"},
            RevealStoryboardRecordV1,
            {"v", "act", "concept_id"},
        ),
        (
            {"v": 1, "act": "trace", "trajectoryId": "lower_angle"},
            TraceStoryboardRecordV1,
            {"v", "act", "trajectory_id"},
        ),
        (
            {
                "v": 1,
                "act": "relate",
                "claimId": "equal_range",
                "evidenceIds": ["lower_trajectory", "higher_trajectory"],
            },
            RelateStoryboardRecordV1,
            {"v", "act", "claim_id", "evidence_ids"},
        ),
        (
            {"v": 1, "act": "abstain", "reasonCode": "unsupported_intent"},
            AbstainStoryboardRecordV1,
            {"v", "act", "reason_code"},
        ),
    ],
)
def test_model_records_have_exact_minimal_wire_shapes(
    payload: dict[str, object],
    expected_type: type[object],
    expected_fields: set[str],
) -> None:
    record = _decode(payload)

    assert isinstance(record, expected_type)
    assert set(type(record).model_fields) == expected_fields
    assert record.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"act": "reveal", "conceptId": "range_formula"},
        {"v": True, "act": "trace", "trajectoryId": "lower_angle"},
        {"v": 1, "act": "reveal", "conceptId": "full_lesson"},
        {"v": 1, "act": "trace", "trajectoryId": "both_angles"},
        {
            "v": 1,
            "act": "relate",
            "claimId": "compare_everything",
            "evidenceIds": ["range_formula"],
        },
        {"v": 1, "act": "relate", "claimId": "equal_range", "evidenceIds": []},
        {
            "v": 1,
            "act": "relate",
            "claimId": "equal_range",
            "evidenceIds": ["higher_trajectory", "lower_trajectory"],
        },
        {"v": 1, "act": "abstain", "reasonCode": "try_later"},
        {"v": 1, "act": "reveal", "conceptId": "range_formula", "narration": "Trust me"},
    ],
)
def test_model_records_reject_open_compound_noncanonical_and_extra_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _decode(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "speedMps",
        "anglesDeg",
        "gravity",
        "equation",
        "narration",
        "caption",
        "componentId",
        "beatId",
        "checkpointId",
        "nodeId",
        "coordinates",
        "durationMs",
        "camera",
        "revision",
        "generation",
        "certificate",
    ],
)
def test_model_record_cannot_own_server_fields(forbidden: str) -> None:
    payload: dict[str, object] = {
        "v": 1,
        "act": "trace",
        "trajectoryId": "higher_angle",
        forbidden: "model-owned",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        _decode(payload)


def test_ordered_program_enforces_applicability_dependencies_and_no_duplicates() -> None:
    problem = _problem()
    lower = _decode({"v": 1, "act": "trace", "trajectoryId": "lower_angle"})
    higher = _decode({"v": 1, "act": "trace", "trajectoryId": "higher_angle"})
    relation = _decode(
        {
            "v": 1,
            "act": "relate",
            "claimId": "equal_range",
            "evidenceIds": ["lower_trajectory", "higher_trajectory"],
        }
    )

    state = ProjectileStoryboardStateV1(
        id="projectile-comparison",
        problemSpec=problem,
        acceptedRecords=(lower, higher, relation),
    )
    assert state.accepted_records[-1].claim_id is StoryboardClaimId.EQUAL_RANGE

    with pytest.raises(ValidationError, match="already be visible"):
        ProjectileStoryboardStateV1(
            id="projectile-comparison",
            problemSpec=problem,
            acceptedRecords=(relation,),
        )
    with pytest.raises(ValidationError, match="repeat a semantic effect"):
        ProjectileStoryboardStateV1(
            id="projectile-comparison",
            problemSpec=problem,
            acceptedRecords=(lower, lower),
        )
    with pytest.raises(ValidationError, match="inapplicable"):
        ProjectileStoryboardStateV1(
            id="projectile-comparison",
            problemSpec=_problem(angles=(30, 45)),
            acceptedRecords=(lower, higher, relation),
        )


def test_scene_revision_is_exactly_anchor_plus_one_per_accepted_record() -> None:
    problem = _problem()
    lower = _decode({"v": 1, "act": "trace", "trajectoryId": "lower_angle"})
    component = ProjectileStoryboardStateV1(
        id="projectile-comparison",
        problemSpec=problem,
        acceptedRecords=(lower,),
    )
    scene = ProjectileStoryboardSemanticSceneStateV1(
        revision=2,
        components=(component,),
        certificateHeadSha256="a" * 64,
    )
    assert scene.revision == 1 + len(component.accepted_records)

    with pytest.raises(ValidationError, match="one anchor plus accepted records"):
        ProjectileStoryboardSemanticSceneStateV1(
            revision=3,
            components=(component,),
            certificateHeadSha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="certificate head"):
        ProjectileStoryboardSemanticSceneStateV1(revision=2, components=(component,))


def test_storyboard_component_id_is_server_owned_and_closed() -> None:
    with pytest.raises(ValidationError):
        ProjectileStoryboardStateV1(
            id="attacker-chosen",
            problemSpec=_problem(),
        )


def test_new_hash_domains_are_deterministic_ordered_and_distinct() -> None:
    problem = _problem()
    lower = _decode({"v": 1, "act": "trace", "trajectoryId": "lower_angle"})
    higher = _decode({"v": 1, "act": "trace", "trajectoryId": "higher_angle"})
    low_first = semantic_storyboard_program_sha256(problem, (lower, higher))
    high_first = semantic_storyboard_program_sha256(problem, (higher, lower))

    assert low_first == semantic_storyboard_program_sha256(problem, (lower, higher))
    assert low_first != high_first
    assert paired_projectile_comparison_sha256(problem) != low_first
    assert semantic_storyboard_record_sha256(lower) != low_first

    component = ProjectileStoryboardStateV1(
        id="projectile-comparison",
        problemSpec=problem,
        acceptedRecords=(lower, higher),
    )
    scene = ProjectileStoryboardSemanticSceneStateV1(
        revision=3,
        components=(component,),
        certificateHeadSha256="a" * 64,
    )
    assert semantic_storyboard_scene_sha256(scene) not in {low_first, high_first}
    different_chain_head = scene.model_copy(update={"certificate_head_sha256": "b" * 64})
    assert semantic_storyboard_scene_sha256(scene) == semantic_storyboard_scene_sha256(
        different_chain_head
    )
