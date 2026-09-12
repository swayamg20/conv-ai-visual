"""Local routing from one semantic storyboard record to one server-owned beat."""

from __future__ import annotations

from enum import StrEnum

from murmur.live_scene.semantic_storyboard_contracts import (
    PROJECTILE_STORYBOARD_COMPONENT_ID,
    STORYBOARD_CLAIM_EVIDENCE_OPTIONS,
    STORYBOARD_CONCEPT_EVIDENCE,
    STORYBOARD_TRAJECTORY_EVIDENCE,
    AbstainStoryboardRecordV1,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RelateStoryboardRecordV1,
    RevealStoryboardRecordV1,
    RoutedSemanticStoryboardBeatV1,
    SemanticStoryboardRecordV1,
    StoryboardSemanticEffectClosureV1,
    TraceStoryboardRecordV1,
    semantic_storyboard_program_sha256,
    storyboard_evidence_after,
    storyboard_has_forward_capacity,
    storyboard_record_effect_key,
    storyboard_record_is_applicable,
    storyboard_record_slug,
)


class SemanticStoryboardRoutingErrorCode(StrEnum):
    """Stable reasons a model record cannot advance the accepted ledger."""

    COMPONENT_NOT_FOUND = "component_not_found"
    PROBLEM_MISMATCH = "problem_mismatch"
    ABSTAIN_HAS_NO_BEAT = "abstain_has_no_beat"
    DUPLICATE_EFFECT = "duplicate_effect"
    INAPPLICABLE_SELECTION = "inapplicable_selection"
    INVALID_EVIDENCE = "invalid_evidence"
    MISSING_DEPENDENCY = "missing_dependency"
    LEDGER_CAPACITY_EXCEEDED = "ledger_capacity_exceeded"


class SemanticStoryboardRoutingError(ValueError):
    """Fail-closed routing error that never contains prompt or provider text."""

    def __init__(self, code: SemanticStoryboardRoutingErrorCode) -> None:
        if not isinstance(code, SemanticStoryboardRoutingErrorCode):
            raise TypeError("code must be a SemanticStoryboardRoutingErrorCode")
        super().__init__(code.value)
        self.code = code


def validate_semantic_storyboard_frontier(
    problem_spec: PairedProjectileComparisonSpecV1,
    semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> ProjectileStoryboardStateV1:
    """Return the sole certified component after proving its problem binding."""

    if not isinstance(problem_spec, PairedProjectileComparisonSpecV1):
        raise TypeError("problem_spec must be a PairedProjectileComparisonSpecV1")
    if not isinstance(semantic_scene, ProjectileStoryboardSemanticSceneStateV1):
        raise TypeError("semantic_scene must be a ProjectileStoryboardSemanticSceneStateV1")
    if not semantic_scene.components:
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.COMPONENT_NOT_FOUND)
    component = semantic_scene.components[0]
    if component.problem_spec != problem_spec:
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.PROBLEM_MISMATCH)
    return component


def semantic_effect_for_storyboard_record(
    record: AcceptedSemanticStoryboardRecordV1,
) -> StoryboardSemanticEffectClosureV1:
    """Resolve the exact one-record semantic effect without representation data."""

    if isinstance(record, RevealStoryboardRecordV1):
        return StoryboardSemanticEffectClosureV1(
            conceptIds=(record.concept_id,),
            producedEvidenceIds=(STORYBOARD_CONCEPT_EVIDENCE[record.concept_id],),
        )
    if isinstance(record, TraceStoryboardRecordV1):
        return StoryboardSemanticEffectClosureV1(
            trajectoryIds=(record.trajectory_id,),
            producedEvidenceIds=(STORYBOARD_TRAJECTORY_EVIDENCE[record.trajectory_id],),
        )
    if isinstance(record, RelateStoryboardRecordV1):
        return StoryboardSemanticEffectClosureV1(
            claimIds=(record.claim_id,),
            consumedEvidenceIds=record.evidence_ids,
        )
    raise TypeError("record must be an accepted semantic storyboard record")


def _validate_candidate(
    record: AcceptedSemanticStoryboardRecordV1,
    *,
    problem_spec: PairedProjectileComparisonSpecV1,
    component: ProjectileStoryboardStateV1,
) -> None:
    accepted_records = component.accepted_records
    if not storyboard_has_forward_capacity(problem_spec, accepted_records):
        raise SemanticStoryboardRoutingError(
            SemanticStoryboardRoutingErrorCode.LEDGER_CAPACITY_EXCEEDED
        )
    effects = {storyboard_record_effect_key(accepted) for accepted in accepted_records}
    if storyboard_record_effect_key(record) in effects:
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.DUPLICATE_EFFECT)
    if not storyboard_record_is_applicable(record, problem_spec=problem_spec):
        raise SemanticStoryboardRoutingError(
            SemanticStoryboardRoutingErrorCode.INAPPLICABLE_SELECTION
        )
    if not isinstance(record, RelateStoryboardRecordV1):
        return
    if record.evidence_ids not in STORYBOARD_CLAIM_EVIDENCE_OPTIONS[record.claim_id]:
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.INVALID_EVIDENCE)
    if not set(record.evidence_ids).issubset(storyboard_evidence_after(accepted_records)):
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.MISSING_DEPENDENCY)


def route_semantic_storyboard_record(
    record: SemanticStoryboardRecordV1,
    *,
    problem_spec: PairedProjectileComparisonSpecV1,
    semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
) -> RoutedSemanticStoryboardBeatV1:
    """Bind exactly one accepted model record to exactly one future checkpoint."""

    if isinstance(record, AbstainStoryboardRecordV1):
        raise SemanticStoryboardRoutingError(SemanticStoryboardRoutingErrorCode.ABSTAIN_HAS_NO_BEAT)
    if not isinstance(
        record,
        RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
    ):
        raise TypeError("record must be a semantic storyboard record")

    component = validate_semantic_storyboard_frontier(problem_spec, semantic_scene)
    _validate_candidate(record, problem_spec=problem_spec, component=component)

    accepted_records = component.accepted_records
    next_records = (*accepted_records, record)
    ordinal = len(next_records)
    slug = storyboard_record_slug(record)
    return RoutedSemanticStoryboardBeatV1(
        beatId=f"storyboard-beat-{slug}",
        checkpointId=f"storyboard-checkpoint-{slug}",
        componentId=component.id,
        ordinal=ordinal,
        problemSpec=problem_spec,
        record=record,
        semanticEffect=semantic_effect_for_storyboard_record(record),
        baseProgramSha256=semantic_storyboard_program_sha256(
            problem_spec,
            accepted_records,
        ),
        resultProgramSha256=semantic_storyboard_program_sha256(
            problem_spec,
            next_records,
        ),
        previousCertificateSha256=semantic_scene.certificate_head_sha256,
    )


__all__ = [
    "PROJECTILE_STORYBOARD_COMPONENT_ID",
    "SemanticStoryboardRoutingError",
    "SemanticStoryboardRoutingErrorCode",
    "route_semantic_storyboard_record",
    "semantic_effect_for_storyboard_record",
    "validate_semantic_storyboard_frontier",
]
