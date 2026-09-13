"""Provider-free tests for one-record-to-one-checkpoint storyboard routing."""

from __future__ import annotations

import pytest
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardSemanticEffectClosureV1,
    routed_semantic_storyboard_beat_sha256,
    semantic_storyboard_program_sha256,
    storyboard_has_forward_capacity,
)
from murmur.live_scene.semantic_storyboard_routing import (
    SemanticStoryboardRoutingError,
    SemanticStoryboardRoutingErrorCode,
    route_semantic_storyboard_record,
)
from pydantic import ValidationError


def _problem(
    angles: tuple[int, int] = (30, 60),
) -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=angles)


def _record(act: str, **fields: object) -> object:
    return SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python({"v": 1, "act": act, **fields})


def _scene(
    *,
    problem: PairedProjectileComparisonSpecV1 | None = None,
    records: tuple[object, ...] = (),
    certificate: str = "a" * 64,
) -> ProjectileStoryboardSemanticSceneStateV1:
    bound_problem = _problem() if problem is None else problem
    component = ProjectileStoryboardStateV1(
        id="projectile-comparison",
        problemSpec=bound_problem,
        acceptedRecords=records,
    )
    return ProjectileStoryboardSemanticSceneStateV1(
        revision=1 + len(records),
        components=(component,),
        certificateHeadSha256=certificate,
    )


def _exhausted_records(angles: tuple[int, int]) -> tuple[object, ...]:
    formula = _record("reveal", conceptId="range_formula")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    apex = _record(
        "relate",
        claimId="higher_apex",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    flight = _record(
        "relate",
        claimId="longer_flight",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    if sum(angles) == 90:
        complementary = _record("reveal", conceptId="complementary_angles")
        range_claim = _record(
            "relate",
            claimId="equal_range",
            evidenceIds=["range_formula", "complementary_angles"],
        )
        return (formula, complementary, lower, higher, range_claim, apex, flight)
    range_claim = _record(
        "relate",
        claimId="unequal_range",
        evidenceIds=["range_formula"],
    )
    return (formula, lower, higher, range_claim, apex, flight)


def test_one_trace_record_routes_to_one_server_owned_beat_and_effect_closure() -> None:
    problem = _problem()
    record = _record("trace", trajectoryId="lower_angle")

    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=_scene(problem=problem),
    )

    assert beat.ordinal == 1
    assert beat.beat_id == "storyboard-beat-trace-lower-angle"
    assert beat.checkpoint_id == "storyboard-checkpoint-trace-lower-angle"
    assert beat.component_id == "projectile-comparison"
    assert beat.previous_certificate_sha256 == "a" * 64
    assert beat.record is record
    assert beat.semantic_effect == StoryboardSemanticEffectClosureV1(
        trajectoryIds=("lower_angle",),
        producedEvidenceIds=("lower_trajectory",),
    )
    assert beat.base_program_sha256 == semantic_storyboard_program_sha256(problem, ())
    assert beat.result_program_sha256 == semantic_storyboard_program_sha256(problem, (record,))
    assert beat.base_program_sha256 != beat.result_program_sha256
    assert len(routed_semantic_storyboard_beat_sha256(beat)) == 64


def test_each_accepted_record_advances_exactly_one_ordinal_and_semantic_revision() -> None:
    problem = _problem()
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    base = _scene(problem=problem, records=(lower,), certificate="b" * 64)

    beat = route_semantic_storyboard_record(
        higher,
        problem_spec=problem,
        semantic_scene=base,
    )
    result = _scene(problem=problem, records=(lower, higher), certificate="c" * 64)

    assert beat.ordinal == 2
    assert result.revision == base.revision + 1
    assert (
        len(result.components[0].accepted_records) == len(base.components[0].accepted_records) + 1
    )
    assert beat.result_program_sha256 == semantic_storyboard_program_sha256(
        problem,
        result.components[0].accepted_records,
    )


def test_reveal_and_relate_effects_are_exact_and_never_expand_dependencies() -> None:
    problem = _problem()
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    relation = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["range_formula", "complementary_angles"],
    )

    reveal_beat = route_semantic_storyboard_record(
        formula,
        problem_spec=problem,
        semantic_scene=_scene(problem=problem),
    )
    assert reveal_beat.semantic_effect == StoryboardSemanticEffectClosureV1(
        conceptIds=("range_formula",),
        producedEvidenceIds=("range_formula",),
    )

    relate_beat = route_semantic_storyboard_record(
        relation,
        problem_spec=problem,
        semantic_scene=_scene(problem=problem, records=(formula, complementary)),
    )
    assert relate_beat.semantic_effect == StoryboardSemanticEffectClosureV1(
        claimIds=("equal_range",),
        consumedEvidenceIds=("range_formula", "complementary_angles"),
    )
    assert relate_beat.semantic_effect.concept_ids == ()
    assert relate_beat.semantic_effect.trajectory_ids == ()
    assert relate_beat.semantic_effect.produced_evidence_ids == ()


def test_missing_dependencies_reject_instead_of_implicitly_expanding() -> None:
    problem = _problem()
    relation = _record(
        "relate",
        claimId="higher_apex",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )

    with pytest.raises(SemanticStoryboardRoutingError) as captured:
        route_semantic_storyboard_record(
            relation,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem),
        )

    assert captured.value.code is SemanticStoryboardRoutingErrorCode.MISSING_DEPENDENCY


def test_duplicate_effect_and_invalid_evidence_combination_reject_locally() -> None:
    problem = _problem()
    lower = _record("trace", trajectoryId="lower_angle")
    with pytest.raises(SemanticStoryboardRoutingError) as duplicate:
        route_semantic_storyboard_record(
            lower,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem, records=(lower,)),
        )
    assert duplicate.value.code is SemanticStoryboardRoutingErrorCode.DUPLICATE_EFFECT

    higher = _record("trace", trajectoryId="higher_angle")
    wrong_evidence = _record(
        "relate",
        claimId="higher_apex",
        evidenceIds=["range_formula"],
    )
    with pytest.raises(SemanticStoryboardRoutingError) as invalid:
        route_semantic_storyboard_record(
            wrong_evidence,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem, records=(lower, higher)),
        )
    assert invalid.value.code is SemanticStoryboardRoutingErrorCode.INVALID_EVIDENCE


@pytest.mark.parametrize(
    ("angles", "claim", "should_apply"),
    [
        ((30, 60), "equal_range", True),
        ((30, 60), "unequal_range", False),
        ((30, 45), "equal_range", False),
        ((30, 45), "unequal_range", True),
        ((45, 60), "equal_range", False),
        ((45, 60), "unequal_range", True),
    ],
)
def test_range_claim_applicability_is_bound_to_the_exact_angle_pair(
    angles: tuple[int, int],
    claim: str,
    should_apply: bool,
) -> None:
    problem = _problem(angles)
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    relation = _record(
        "relate",
        claimId=claim,
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )

    if should_apply:
        beat = route_semantic_storyboard_record(
            relation,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem, records=(lower, higher)),
        )
        assert beat.record.claim_id.value == claim
    else:
        with pytest.raises(SemanticStoryboardRoutingError) as captured:
            route_semantic_storyboard_record(
                relation,
                problem_spec=problem,
                semantic_scene=_scene(problem=problem, records=(lower, higher)),
            )
        assert captured.value.code is SemanticStoryboardRoutingErrorCode.INAPPLICABLE_SELECTION


def test_complementary_concept_is_inapplicable_to_inequality_control_pair() -> None:
    problem = _problem((30, 45))
    concept = _record("reveal", conceptId="complementary_angles")

    with pytest.raises(SemanticStoryboardRoutingError) as captured:
        route_semantic_storyboard_record(
            concept,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem),
        )

    assert captured.value.code is SemanticStoryboardRoutingErrorCode.INAPPLICABLE_SELECTION


def test_abstain_empty_frontier_and_problem_mismatch_never_create_a_beat() -> None:
    problem = _problem()
    abstain = _record("abstain", reasonCode="unsupported_intent")
    with pytest.raises(SemanticStoryboardRoutingError) as declined:
        route_semantic_storyboard_record(
            abstain,
            problem_spec=problem,
            semantic_scene=_scene(problem=problem),
        )
    assert declined.value.code is SemanticStoryboardRoutingErrorCode.ABSTAIN_HAS_NO_BEAT

    record = _record("trace", trajectoryId="lower_angle")
    with pytest.raises(SemanticStoryboardRoutingError) as empty:
        route_semantic_storyboard_record(
            record,
            problem_spec=problem,
            semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
        )
    assert empty.value.code is SemanticStoryboardRoutingErrorCode.COMPONENT_NOT_FOUND

    with pytest.raises(SemanticStoryboardRoutingError) as mismatch:
        route_semantic_storyboard_record(
            record,
            problem_spec=_problem((30, 45)),
            semantic_scene=_scene(problem=problem),
        )
    assert mismatch.value.code is SemanticStoryboardRoutingErrorCode.PROBLEM_MISMATCH


@pytest.mark.parametrize(("angles", "expected_count"), [((30, 60), 7), ((30, 45), 6)])
def test_exhausted_problem_rejects_before_another_candidate(
    angles: tuple[int, int],
    expected_count: int,
) -> None:
    problem = _problem(angles)
    records = _exhausted_records(angles)
    assert len(records) == expected_count
    assert not storyboard_has_forward_capacity(problem, records)
    assert storyboard_has_forward_capacity(problem, records[:-1])

    with pytest.raises(SemanticStoryboardRoutingError) as captured:
        route_semantic_storyboard_record(
            _record("trace", trajectoryId="lower_angle"),
            problem_spec=problem,
            semantic_scene=_scene(problem=problem, records=records),
        )

    assert captured.value.code is SemanticStoryboardRoutingErrorCode.LEDGER_CAPACITY_EXCEEDED


def test_routed_contract_rejects_tampered_semantic_effect_closure() -> None:
    problem = _problem()
    record = _record("trace", trajectoryId="lower_angle")
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=_scene(problem=problem),
    )
    payload = beat.model_dump(mode="json", by_alias=True)
    payload["semanticEffect"] = {
        "v": 1,
        "conceptIds": ["range_formula"],
        "trajectoryIds": [],
        "claimIds": [],
        "producedEvidenceIds": ["range_formula"],
        "consumedEvidenceIds": [],
    }

    with pytest.raises(ValidationError, match="exact closure"):
        RoutedSemanticStoryboardBeatV1.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("componentId", "attacker-chosen", "componentId"),
        ("beatId", "storyboard-beat-attacker-chosen", "beatId"),
        (
            "checkpointId",
            "storyboard-checkpoint-attacker-chosen",
            "checkpointId",
        ),
    ],
)
def test_routed_contract_rejects_tampered_server_owned_ids(
    field: str,
    value: str,
    message: str,
) -> None:
    problem = _problem()
    record = _record("trace", trajectoryId="lower_angle")
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=_scene(problem=problem),
    )
    payload = beat.model_dump(mode="json", by_alias=True)
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        RoutedSemanticStoryboardBeatV1.model_validate(payload)
