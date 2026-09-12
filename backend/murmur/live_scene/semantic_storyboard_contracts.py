"""Closed semantic language for Gate 1.8 projectile-comparison storyboards.

The provider may select only catalog-owned semantic nouns.  Problem values,
physics, copy, equations, geometry, presentation, identifiers, integrity
evidence, and lifecycle fields remain server-owned.  These contracts are
additive and deliberately do not widen the sealed Gate 1.5--1.7 unions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Literal, Never, Self, TypeAlias

from pydantic import Field, TypeAdapter, field_validator, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyId,
)
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    LiveSceneContract,
    NonNegativeRevision,
)
from murmur.live_scene.projectile_motion_contracts import (
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
    ProjectileAngleDeg,
    ProjectileSpeedMps,
)
from murmur.live_scene.semantic_contracts import Sha256Digest
from murmur.live_scene.semantic_integrity import canonical_sha256

SEMANTIC_STORYBOARD_RECORD_VERSION: Final = 1
PAIRED_PROJECTILE_COMPARISON_VERSION: Final = 1
PROJECTILE_STORYBOARD_STATE_VERSION: Final = 1
ROUTED_SEMANTIC_STORYBOARD_BEAT_VERSION: Final = 1
STORYBOARD_EFFECT_CLOSURE_VERSION: Final = 1

SEMANTIC_STORYBOARD_CATALOG_VERSION: Final = "projectile-comparison-catalog-v1"
PROJECTILE_STORYBOARD_COMPONENT_ID: Final = "projectile-comparison"
MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN: Final = 5
# At most seven of the eight catalog effects are applicable to one bound pair:
# equal_range and unequal_range are mutually exclusive.
MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS: Final = 7
MAX_STORYBOARD_EVIDENCE_IDS: Final = 4

StoryboardBeatOrdinal = Annotated[
    int,
    Field(strict=True, ge=1, le=min(MAX_SAFE_SEQUENCE, MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS)),
]

PAIRED_PROJECTILE_COMPARISON_HASH_DOMAIN: Final = "murmur:paired-projectile-comparison:v1"
SEMANTIC_STORYBOARD_RECORD_HASH_DOMAIN: Final = "murmur:semantic-storyboard-record:v1"
SEMANTIC_STORYBOARD_PROGRAM_HASH_DOMAIN: Final = "murmur:semantic-storyboard-program:v1"
SEMANTIC_STORYBOARD_SCENE_HASH_DOMAIN: Final = "murmur:semantic-storyboard-scene:v1"
ROUTED_SEMANTIC_STORYBOARD_BEAT_HASH_DOMAIN: Final = "murmur:routed-semantic-storyboard-beat:v1"


class PairedProjectileComparisonSpecV1(LiveSceneContract):
    """One same-speed comparison over two supported, ascending launch angles."""

    v: Literal[PAIRED_PROJECTILE_COMPARISON_VERSION] = PAIRED_PROJECTILE_COMPARISON_VERSION
    speed_mps: ProjectileSpeedMps = Field(alias="speedMps")
    angles_deg: tuple[ProjectileAngleDeg, ProjectileAngleDeg] = Field(alias="anglesDeg")

    @field_validator("v", "speed_mps", mode="before")
    @classmethod
    def validate_strict_integer(cls, value: object, info) -> object:
        if type(value) is not int:
            raise ValueError(f"{info.field_name} must be a strict integer")
        return value

    @field_validator("angles_deg", mode="before")
    @classmethod
    def validate_strict_angle_pair(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or len(value) != 2:
            raise ValueError("anglesDeg must contain exactly two angles")
        if any(type(angle) is not int for angle in value):
            raise ValueError("anglesDeg values must be strict integers")
        return value

    @model_validator(mode="after")
    def validate_supported_comparison(self) -> Self:
        if self.speed_mps not in SUPPORTED_PROJECTILE_SPEEDS_MPS:
            raise ValueError(
                "speedMps must be one of "
                f"{', '.join(str(speed) for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS)}"
            )
        if any(angle not in SUPPORTED_PROJECTILE_ANGLES_DEG for angle in self.angles_deg):
            raise ValueError(
                "anglesDeg values must be drawn from "
                f"{', '.join(str(angle) for angle in SUPPORTED_PROJECTILE_ANGLES_DEG)}"
            )
        if self.angles_deg[0] >= self.angles_deg[1]:
            raise ValueError("anglesDeg must contain two distinct angles in ascending order")
        return self

    @property
    def lower_angle_deg(self) -> int:
        return self.angles_deg[0]

    @property
    def higher_angle_deg(self) -> int:
        return self.angles_deg[1]

    @property
    def has_complementary_angles(self) -> bool:
        return sum(self.angles_deg) == 90


class StoryboardConceptId(StrEnum):
    """Closed concepts the model may ask the server to reveal."""

    RANGE_FORMULA = "range_formula"
    COMPLEMENTARY_ANGLES = "complementary_angles"


class StoryboardTrajectoryId(StrEnum):
    """Problem-relative trajectories the model may ask the server to trace."""

    LOWER_ANGLE = "lower_angle"
    HIGHER_ANGLE = "higher_angle"


class StoryboardClaimId(StrEnum):
    """Closed, independently verified relationships between the projectiles."""

    EQUAL_RANGE = "equal_range"
    UNEQUAL_RANGE = "unequal_range"
    HIGHER_APEX = "higher_apex"
    LONGER_FLIGHT = "longer_flight"


class StoryboardEvidenceId(StrEnum):
    """Evidence made visible only by a previously accepted atomic beat."""

    LOWER_TRAJECTORY = "lower_trajectory"
    HIGHER_TRAJECTORY = "higher_trajectory"
    RANGE_FORMULA = "range_formula"
    COMPLEMENTARY_ANGLES = "complementary_angles"


class StoryboardAbstainReasonCode(StrEnum):
    """Closed reasons for intentionally producing no storyboard mutation."""

    ALREADY_PRESENT = "already_present"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    NO_FORWARD_PROGRESS = "no_forward_progress"
    UNSUPPORTED_INITIAL_CONDITION = "unsupported_initial_condition"
    UNSUPPORTED_INTENT = "unsupported_intent"
    UNSUPPORTED_PHYSICS = "unsupported_physics"
    UNSUPPORTED_PROBLEM = "unsupported_problem"


STORYBOARD_EVIDENCE_ORDER: Final[tuple[StoryboardEvidenceId, ...]] = (
    StoryboardEvidenceId.LOWER_TRAJECTORY,
    StoryboardEvidenceId.HIGHER_TRAJECTORY,
    StoryboardEvidenceId.RANGE_FORMULA,
    StoryboardEvidenceId.COMPLEMENTARY_ANGLES,
)

STORYBOARD_CONCEPT_EVIDENCE: Final[Mapping[StoryboardConceptId, StoryboardEvidenceId]] = (
    MappingProxyType(
        {
            StoryboardConceptId.RANGE_FORMULA: StoryboardEvidenceId.RANGE_FORMULA,
            StoryboardConceptId.COMPLEMENTARY_ANGLES: (StoryboardEvidenceId.COMPLEMENTARY_ANGLES),
        }
    )
)
STORYBOARD_TRAJECTORY_EVIDENCE: Final[Mapping[StoryboardTrajectoryId, StoryboardEvidenceId]] = (
    MappingProxyType(
        {
            StoryboardTrajectoryId.LOWER_ANGLE: StoryboardEvidenceId.LOWER_TRAJECTORY,
            StoryboardTrajectoryId.HIGHER_ANGLE: StoryboardEvidenceId.HIGHER_TRAJECTORY,
        }
    )
)

_TRAJECTORY_PAIR: Final = (
    StoryboardEvidenceId.LOWER_TRAJECTORY,
    StoryboardEvidenceId.HIGHER_TRAJECTORY,
)
_COMPLEMENTARY_DERIVATION: Final = (
    StoryboardEvidenceId.RANGE_FORMULA,
    StoryboardEvidenceId.COMPLEMENTARY_ANGLES,
)

STORYBOARD_CLAIM_EVIDENCE_OPTIONS: Final[
    Mapping[StoryboardClaimId, tuple[tuple[StoryboardEvidenceId, ...], ...]]
] = MappingProxyType(
    {
        StoryboardClaimId.EQUAL_RANGE: (_TRAJECTORY_PAIR, _COMPLEMENTARY_DERIVATION),
        StoryboardClaimId.UNEQUAL_RANGE: (
            _TRAJECTORY_PAIR,
            (StoryboardEvidenceId.RANGE_FORMULA,),
        ),
        StoryboardClaimId.HIGHER_APEX: (_TRAJECTORY_PAIR,),
        StoryboardClaimId.LONGER_FLIGHT: (_TRAJECTORY_PAIR,),
    }
)


class _SemanticStoryboardRecordBase(LiveSceneContract):
    v: Literal[SEMANTIC_STORYBOARD_RECORD_VERSION]

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value


class RevealStoryboardRecordV1(_SemanticStoryboardRecordBase):
    """Select exactly one server-owned concept reveal."""

    act: Literal["reveal"]
    concept_id: StoryboardConceptId = Field(alias="conceptId")


class TraceStoryboardRecordV1(_SemanticStoryboardRecordBase):
    """Select exactly one problem-relative trajectory trace."""

    act: Literal["trace"]
    trajectory_id: StoryboardTrajectoryId = Field(alias="trajectoryId")


StoryboardEvidenceIds = Annotated[
    tuple[StoryboardEvidenceId, ...],
    Field(min_length=1, max_length=MAX_STORYBOARD_EVIDENCE_IDS),
]


class RelateStoryboardRecordV1(_SemanticStoryboardRecordBase):
    """Select one claim and one exact, already-visible evidence combination."""

    act: Literal["relate"]
    claim_id: StoryboardClaimId = Field(alias="claimId")
    evidence_ids: StoryboardEvidenceIds = Field(alias="evidenceIds")

    @model_validator(mode="after")
    def validate_canonical_evidence(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidenceIds must be unique")
        expected = tuple(
            evidence for evidence in STORYBOARD_EVIDENCE_ORDER if evidence in self.evidence_ids
        )
        if self.evidence_ids != expected:
            raise ValueError("evidenceIds must follow the canonical catalog order")
        return self


class AbstainStoryboardRecordV1(_SemanticStoryboardRecordBase):
    """Explicitly decline the whole provider stream without mutating the scene."""

    act: Literal["abstain"]
    reason_code: StoryboardAbstainReasonCode = Field(alias="reasonCode")


SemanticStoryboardRecordV1: TypeAlias = Annotated[
    RevealStoryboardRecordV1
    | TraceStoryboardRecordV1
    | RelateStoryboardRecordV1
    | AbstainStoryboardRecordV1,
    Field(discriminator="act"),
]
AcceptedSemanticStoryboardRecordV1: TypeAlias = Annotated[
    RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
    Field(discriminator="act"),
]

SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER = TypeAdapter(SemanticStoryboardRecordV1)


def storyboard_record_effect_key(
    record: AcceptedSemanticStoryboardRecordV1,
) -> tuple[str, StrEnum]:
    """Return the catalog identity changed by one accepted record."""

    if isinstance(record, RevealStoryboardRecordV1):
        return (record.act, record.concept_id)
    if isinstance(record, TraceStoryboardRecordV1):
        return (record.act, record.trajectory_id)
    if isinstance(record, RelateStoryboardRecordV1):
        return (record.act, record.claim_id)
    raise TypeError("record must be an accepted semantic storyboard record")


def storyboard_record_slug(record: AcceptedSemanticStoryboardRecordV1) -> str:
    """Return the server-owned stable identifier suffix for one atomic effect."""

    _, target = storyboard_record_effect_key(record)
    return f"{record.act}-{target.value.replace('_', '-')}"


def storyboard_evidence_after(
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> frozenset[StoryboardEvidenceId]:
    """Return only evidence produced by already accepted reveal/trace records."""

    evidence: set[StoryboardEvidenceId] = set()
    for record in records:
        if isinstance(record, RevealStoryboardRecordV1):
            evidence.add(STORYBOARD_CONCEPT_EVIDENCE[record.concept_id])
        elif isinstance(record, TraceStoryboardRecordV1):
            evidence.add(STORYBOARD_TRAJECTORY_EVIDENCE[record.trajectory_id])
        elif not isinstance(record, RelateStoryboardRecordV1):
            raise TypeError("records must contain accepted semantic storyboard records")
    return frozenset(evidence)


def storyboard_record_is_applicable(
    record: AcceptedSemanticStoryboardRecordV1,
    *,
    problem_spec: PairedProjectileComparisonSpecV1,
) -> bool:
    """Return whether a catalog noun can be true for the bound angle pair."""

    if isinstance(record, RevealStoryboardRecordV1):
        return (
            record.concept_id is not StoryboardConceptId.COMPLEMENTARY_ANGLES
            or problem_spec.has_complementary_angles
        )
    if isinstance(record, TraceStoryboardRecordV1):
        return True
    if isinstance(record, RelateStoryboardRecordV1):
        if record.claim_id is StoryboardClaimId.EQUAL_RANGE:
            return problem_spec.has_complementary_angles
        if record.claim_id is StoryboardClaimId.UNEQUAL_RANGE:
            return not problem_spec.has_complementary_angles
        return True
    raise TypeError("record must be an accepted semantic storyboard record")


def validate_storyboard_program(
    problem_spec: PairedProjectileComparisonSpecV1,
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> None:
    """Validate an ordered ledger without inventing prerequisites or effects."""

    if not isinstance(problem_spec, PairedProjectileComparisonSpecV1):
        raise TypeError("problem_spec must be a PairedProjectileComparisonSpecV1")
    if len(records) > MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS:
        raise ValueError("acceptedRecords exceeds the closed storyboard catalog")

    accepted: list[AcceptedSemanticStoryboardRecordV1] = []
    effects: set[tuple[str, StrEnum]] = set()
    for record in records:
        if not isinstance(
            record,
            RevealStoryboardRecordV1 | TraceStoryboardRecordV1 | RelateStoryboardRecordV1,
        ):
            raise TypeError("records must contain accepted semantic storyboard records")
        effect = storyboard_record_effect_key(record)
        if effect in effects:
            raise ValueError("acceptedRecords cannot repeat a semantic effect")
        if not storyboard_record_is_applicable(record, problem_spec=problem_spec):
            raise ValueError("acceptedRecords contains an inapplicable catalog selection")
        if isinstance(record, RelateStoryboardRecordV1):
            if record.evidence_ids not in STORYBOARD_CLAIM_EVIDENCE_OPTIONS[record.claim_id]:
                raise ValueError("evidenceIds is not valid for claimId")
            visible_evidence = storyboard_evidence_after(accepted)
            if not set(record.evidence_ids).issubset(visible_evidence):
                raise ValueError("evidenceIds must already be visible in the accepted prefix")
        effects.add(effect)
        accepted.append(record)


def storyboard_has_forward_capacity(
    problem_spec: PairedProjectileComparisonSpecV1,
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> bool:
    """Return whether the valid ledger has an applicable unused effect."""

    validate_storyboard_program(problem_spec, records)
    applicable_effect_count = 7 if problem_spec.has_complementary_angles else 6
    return len(records) < applicable_effect_count


class ProjectileStoryboardStateV1(LiveSceneContract):
    """Problem-bound ordered ledger after the server-authored anchor checkpoint."""

    v: Literal[PROJECTILE_STORYBOARD_STATE_VERSION] = PROJECTILE_STORYBOARD_STATE_VERSION
    kind: Literal["projectile_comparison_storyboard"] = "projectile_comparison_storyboard"
    id: Literal[PROJECTILE_STORYBOARD_COMPONENT_ID] = PROJECTILE_STORYBOARD_COMPONENT_ID
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    accepted_records: Annotated[
        tuple[AcceptedSemanticStoryboardRecordV1, ...],
        Field(max_length=MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS),
    ] = Field(default=(), alias="acceptedRecords")

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_ordered_program(self) -> Self:
        validate_storyboard_program(self.problem_spec, self.accepted_records)
        return self


class ProjectileStoryboardSemanticSceneStateV1(LiveSceneContract):
    """Dedicated Gate 1.8 semantic frontier; older semantic unions remain sealed."""

    revision: NonNegativeRevision
    components: Annotated[
        tuple[ProjectileStoryboardStateV1, ...],
        Field(max_length=1),
    ] = ()
    certificate_head_sha256: Sha256Digest | None = Field(
        default=None,
        alias="certificateHeadSha256",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_anchor_and_atomic_revisions(self) -> Self:
        if not self.components:
            if self.revision != 0 or self.certificate_head_sha256 is not None:
                raise ValueError(
                    "an empty storyboard semantic scene must be an uncertified revision 0"
                )
            return self
        component = self.components[0]
        expected_revision = 1 + len(component.accepted_records)
        if self.revision != expected_revision:
            raise ValueError(
                "storyboard semantic revision must equal one anchor plus accepted records"
            )
        if self.certificate_head_sha256 is None:
            raise ValueError("a storyboard semantic component requires a certificate head")
        return self


class StoryboardSemanticEffectClosureV1(LiveSceneContract):
    """Exact semantic delta owned by one routed, non-abstain model record."""

    v: Literal[STORYBOARD_EFFECT_CLOSURE_VERSION] = STORYBOARD_EFFECT_CLOSURE_VERSION
    concept_ids: tuple[StoryboardConceptId, ...] = Field(default=(), alias="conceptIds")
    trajectory_ids: tuple[StoryboardTrajectoryId, ...] = Field(
        default=(),
        alias="trajectoryIds",
    )
    claim_ids: tuple[StoryboardClaimId, ...] = Field(default=(), alias="claimIds")
    produced_evidence_ids: tuple[StoryboardEvidenceId, ...] = Field(
        default=(),
        alias="producedEvidenceIds",
    )
    consumed_evidence_ids: tuple[StoryboardEvidenceId, ...] = Field(
        default=(),
        alias="consumedEvidenceIds",
    )

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_one_atomic_effect(self) -> Self:
        owned_effect_count = len(self.concept_ids) + len(self.trajectory_ids) + len(self.claim_ids)
        if owned_effect_count != 1:
            raise ValueError("semantic effect closure must own exactly one catalog effect")
        if any(
            len(values) != len(set(values))
            for values in (
                self.concept_ids,
                self.trajectory_ids,
                self.claim_ids,
                self.produced_evidence_ids,
                self.consumed_evidence_ids,
            )
        ):
            raise ValueError("semantic effect closure values must be unique")
        if self.produced_evidence_ids and self.consumed_evidence_ids:
            raise ValueError("one atomic beat cannot both produce and consume evidence")
        return self


class RoutedSemanticStoryboardBeatV1(LiveSceneContract):
    """Server-bound input for exactly one future compiler checkpoint."""

    v: Literal[ROUTED_SEMANTIC_STORYBOARD_BEAT_VERSION] = ROUTED_SEMANTIC_STORYBOARD_BEAT_VERSION
    beat_id: ChoreographyId = Field(alias="beatId")
    checkpoint_id: ChoreographyId = Field(alias="checkpointId")
    component_kind: Literal["projectile_comparison_storyboard"] = Field(
        default="projectile_comparison_storyboard",
        alias="componentKind",
    )
    component_id: Literal[PROJECTILE_STORYBOARD_COMPONENT_ID] = Field(
        default=PROJECTILE_STORYBOARD_COMPONENT_ID,
        alias="componentId",
    )
    ordinal: StoryboardBeatOrdinal
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    record: AcceptedSemanticStoryboardRecordV1
    semantic_effect: StoryboardSemanticEffectClosureV1 = Field(alias="semanticEffect")
    base_program_sha256: Sha256Digest = Field(alias="baseProgramSha256")
    result_program_sha256: Sha256Digest = Field(alias="resultProgramSha256")
    previous_certificate_sha256: Sha256Digest = Field(alias="previousCertificateSha256")

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_program_transition(self) -> Self:
        if self.base_program_sha256 == self.result_program_sha256:
            raise ValueError("a routed storyboard beat must advance the program hash")
        slug = storyboard_record_slug(self.record)
        if self.beat_id != f"storyboard-beat-{slug}":
            raise ValueError("beatId must be the server-owned ID for record")
        if self.checkpoint_id != f"storyboard-checkpoint-{slug}":
            raise ValueError("checkpointId must be the server-owned ID for record")
        effect = self.semantic_effect
        if isinstance(self.record, RevealStoryboardRecordV1):
            expected = StoryboardSemanticEffectClosureV1(
                conceptIds=(self.record.concept_id,),
                producedEvidenceIds=(STORYBOARD_CONCEPT_EVIDENCE[self.record.concept_id],),
            )
        elif isinstance(self.record, TraceStoryboardRecordV1):
            expected = StoryboardSemanticEffectClosureV1(
                trajectoryIds=(self.record.trajectory_id,),
                producedEvidenceIds=(STORYBOARD_TRAJECTORY_EVIDENCE[self.record.trajectory_id],),
            )
        elif isinstance(self.record, RelateStoryboardRecordV1):
            expected = StoryboardSemanticEffectClosureV1(
                claimIds=(self.record.claim_id,),
                consumedEvidenceIds=self.record.evidence_ids,
            )
        else:
            _unreachable(self.record)
        if effect != expected:
            raise ValueError("semanticEffect must be the exact closure of record")
        return self


def _record_json(record: AcceptedSemanticStoryboardRecordV1) -> dict[str, object]:
    return record.model_dump(mode="json", by_alias=True)


def paired_projectile_comparison_sha256(
    problem_spec: PairedProjectileComparisonSpecV1,
) -> str:
    """Hash only the exact server-qualified paired initial condition."""

    return canonical_sha256(
        problem_spec.model_dump(mode="json", by_alias=True),
        domain=PAIRED_PROJECTILE_COMPARISON_HASH_DOMAIN,
    )


def semantic_storyboard_record_sha256(record: SemanticStoryboardRecordV1) -> str:
    """Hash one exact provider record in its dedicated trust-boundary domain."""

    if not isinstance(
        record,
        RevealStoryboardRecordV1
        | TraceStoryboardRecordV1
        | RelateStoryboardRecordV1
        | AbstainStoryboardRecordV1,
    ):
        raise TypeError("record must be a semantic storyboard record")
    return canonical_sha256(
        record.model_dump(mode="json", by_alias=True),
        domain=SEMANTIC_STORYBOARD_RECORD_HASH_DOMAIN,
    )


def semantic_storyboard_program_sha256(
    problem_spec: PairedProjectileComparisonSpecV1,
    records: Sequence[AcceptedSemanticStoryboardRecordV1],
) -> str:
    """Hash catalog, paired problem, and the canonical ordered accepted ledger."""

    validate_storyboard_program(problem_spec, records)
    return canonical_sha256(
        {
            "catalogVersion": SEMANTIC_STORYBOARD_CATALOG_VERSION,
            "problemSpec": problem_spec.model_dump(mode="json", by_alias=True),
            "acceptedRecords": [_record_json(record) for record in records],
        },
        domain=SEMANTIC_STORYBOARD_PROGRAM_HASH_DOMAIN,
    )


def semantic_storyboard_scene_sha256(
    scene: ProjectileStoryboardSemanticSceneStateV1,
) -> str:
    """Hash the dedicated semantic state without reusing an older scene domain."""

    if not isinstance(scene, ProjectileStoryboardSemanticSceneStateV1):
        raise TypeError("scene must be a ProjectileStoryboardSemanticSceneStateV1")
    return canonical_sha256(
        {
            "revision": scene.revision,
            "components": [
                component.model_dump(mode="json", by_alias=True) for component in scene.components
            ],
        },
        domain=SEMANTIC_STORYBOARD_SCENE_HASH_DOMAIN,
    )


def routed_semantic_storyboard_beat_sha256(beat: RoutedSemanticStoryboardBeatV1) -> str:
    """Hash the exact server-owned lowering of one accepted record."""

    if not isinstance(beat, RoutedSemanticStoryboardBeatV1):
        raise TypeError("beat must be a RoutedSemanticStoryboardBeatV1")
    return canonical_sha256(
        beat.model_dump(mode="json", by_alias=True),
        domain=ROUTED_SEMANTIC_STORYBOARD_BEAT_HASH_DOMAIN,
    )


def _unreachable(_value: object) -> Never:
    raise AssertionError("unreachable semantic storyboard variant")


__all__ = [
    "MAX_SEMANTIC_STORYBOARD_LEDGER_RECORDS",
    "MAX_SEMANTIC_STORYBOARD_RECORDS_PER_TURN",
    "PAIRED_PROJECTILE_COMPARISON_HASH_DOMAIN",
    "PROJECTILE_STORYBOARD_COMPONENT_ID",
    "ROUTED_SEMANTIC_STORYBOARD_BEAT_HASH_DOMAIN",
    "SEMANTIC_STORYBOARD_CATALOG_VERSION",
    "SEMANTIC_STORYBOARD_PROGRAM_HASH_DOMAIN",
    "SEMANTIC_STORYBOARD_RECORD_HASH_DOMAIN",
    "SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER",
    "SEMANTIC_STORYBOARD_SCENE_HASH_DOMAIN",
    "STORYBOARD_CLAIM_EVIDENCE_OPTIONS",
    "STORYBOARD_CONCEPT_EVIDENCE",
    "STORYBOARD_EVIDENCE_ORDER",
    "STORYBOARD_TRAJECTORY_EVIDENCE",
    "AbstainStoryboardRecordV1",
    "AcceptedSemanticStoryboardRecordV1",
    "PairedProjectileComparisonSpecV1",
    "ProjectileStoryboardSemanticSceneStateV1",
    "ProjectileStoryboardStateV1",
    "RelateStoryboardRecordV1",
    "RevealStoryboardRecordV1",
    "RoutedSemanticStoryboardBeatV1",
    "SemanticStoryboardRecordV1",
    "StoryboardAbstainReasonCode",
    "StoryboardBeatOrdinal",
    "StoryboardClaimId",
    "StoryboardConceptId",
    "StoryboardEvidenceId",
    "StoryboardSemanticEffectClosureV1",
    "StoryboardTrajectoryId",
    "TraceStoryboardRecordV1",
    "paired_projectile_comparison_sha256",
    "routed_semantic_storyboard_beat_sha256",
    "semantic_storyboard_program_sha256",
    "semantic_storyboard_record_sha256",
    "semantic_storyboard_scene_sha256",
    "storyboard_evidence_after",
    "storyboard_has_forward_capacity",
    "storyboard_record_effect_key",
    "storyboard_record_is_applicable",
    "storyboard_record_slug",
    "validate_storyboard_program",
]
