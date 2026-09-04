"""Strict contracts for routed and compiler-verified semantic visual acts.

Only :class:`VisualActDecision` and the legacy :class:`TeachingBeatDraft` cross
model trust boundaries. Geometry, styles, equations, child node identifiers,
verification receipts, and semantic revision bookkeeping are deliberately
represented only by the server-owned contracts in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from murmur.live_scene.contracts import (
    LIVE_SCENE_SCHEMA_VERSION,
    MAX_SCENE_NODES,
    LiveSceneContract,
    NarrationText,
    NonNegativeRevision,
    PositiveRevision,
    PositiveSequence,
    SceneNodeId,
    ScenePatchDraft,
)
from murmur.live_scene.semantic_integrity import (
    COMPILER_CERTIFICATE_HASH_DOMAIN,
    SCENE_PATCH_HASH_DOMAIN,
    SEMANTIC_CANONICALIZATION,
    SEMANTIC_HASH_ALGORITHM,
    SEMANTIC_SCENE_HASH_DOMAIN,
    TEACHING_BEAT_HASH_DOMAIN,
    VERIFICATION_RECEIPT_HASH_DOMAIN,
    canonical_sha256,
    digest_matches,
)

MAX_SEMANTIC_ID_CHARS = 32
MAX_SEMANTIC_COMPONENTS = MAX_SCENE_NODES
SEMANTIC_COMPILER_VERSION = "murmur.pythagorean_area_identity.v1"

SemanticId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_SEMANTIC_ID_CHARS,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$",
    ),
]
BeatId = SemanticId
SemanticComponentId = SemanticId
AtomId = SceneNodeId
Sha256Digest = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class TeachingAct(StrEnum):
    """Closed model-authored pedagogical intent vocabulary for Gate 1.1."""

    INTRODUCE = "introduce"
    DERIVE = "derive"
    CONNECT = "connect"
    EMPHASIZE = "emphasize"


class PythagoreanStage(StrEnum):
    """Named reveal targets understood by the deterministic identity compiler."""

    TRIANGLE = "triangle"
    AREAS = "areas"
    IDENTITY = "identity"


class VisualActAbstainReason(StrEnum):
    """Closed reasons for deliberately producing no visual mutation."""

    UNSUPPORTED_INTENT = "unsupported_intent"
    NO_FORWARD_PROGRESS = "no_forward_progress"


PythagoreanComponentKind: TypeAlias = Literal["pythagorean_area_identity"]


class StartVisualDecision(LiveSceneContract):
    """Model request to create one supported semantic component."""

    v: Literal[LIVE_SCENE_SCHEMA_VERSION] = LIVE_SCENE_SCHEMA_VERSION
    decision: Literal["start_visual"] = "start_visual"
    component_kind: PythagoreanComponentKind = Field(alias="componentKind")
    target_stage: PythagoreanStage = Field(alias="targetStage")


class ContinueVisualDecision(LiveSceneContract):
    """Model request to extend one accepted semantic component."""

    v: Literal[LIVE_SCENE_SCHEMA_VERSION] = LIVE_SCENE_SCHEMA_VERSION
    decision: Literal["continue_visual"] = "continue_visual"
    component_id: SemanticComponentId = Field(alias="componentId")
    target_stage: PythagoreanStage = Field(alias="targetStage")


class AbstainVisualDecision(LiveSceneContract):
    """Model decision to leave the accepted visual state unchanged."""

    v: Literal[LIVE_SCENE_SCHEMA_VERSION] = LIVE_SCENE_SCHEMA_VERSION
    decision: Literal["abstain"] = "abstain"
    reason_code: VisualActAbstainReason = Field(alias="reasonCode")


VisualActDecision: TypeAlias = Annotated[
    StartVisualDecision | ContinueVisualDecision | AbstainVisualDecision,
    Field(discriminator="decision"),
]


class PythagoreanRole(StrEnum):
    """Server-owned semantic order of independently committable visual atoms."""

    TRIANGLE = "triangle"
    SQUARE_A = "square_a"
    LABEL_A2 = "label_a2"
    SQUARE_B = "square_b"
    LABEL_B2 = "label_b2"
    SQUARE_C = "square_c"
    LABEL_C2 = "label_c2"
    IDENTITY = "identity"


PYTHAGOREAN_ROLE_ORDER: tuple[PythagoreanRole, ...] = (
    PythagoreanRole.TRIANGLE,
    PythagoreanRole.SQUARE_A,
    PythagoreanRole.LABEL_A2,
    PythagoreanRole.SQUARE_B,
    PythagoreanRole.LABEL_B2,
    PythagoreanRole.SQUARE_C,
    PythagoreanRole.LABEL_C2,
    PythagoreanRole.IDENTITY,
)

PYTHAGOREAN_STAGE_ROLES: Mapping[PythagoreanStage, tuple[PythagoreanRole, ...]] = MappingProxyType(
    {
        PythagoreanStage.TRIANGLE: PYTHAGOREAN_ROLE_ORDER[:1],
        PythagoreanStage.AREAS: PYTHAGOREAN_ROLE_ORDER[:7],
        PythagoreanStage.IDENTITY: PYTHAGOREAN_ROLE_ORDER,
    }
)


def roles_through(stage: PythagoreanStage) -> tuple[PythagoreanRole, ...]:
    """Return the immutable semantic prefix represented by ``stage``."""

    return PYTHAGOREAN_STAGE_ROLES[stage]


class PythagoreanAreaIdentityDirective(LiveSceneContract):
    """The complete model-authored surface for the first semantic component."""

    kind: PythagoreanComponentKind = "pythagorean_area_identity"
    id: SemanticComponentId
    reveal_through: PythagoreanStage = Field(alias="revealThrough")


TeachingDirective: TypeAlias = PythagoreanAreaIdentityDirective


class TeachingBeatDraft(LiveSceneContract):
    """One compact, lifecycle-free teaching request authored by a model."""

    v: Literal[LIVE_SCENE_SCHEMA_VERSION] = LIVE_SCENE_SCHEMA_VERSION
    beat_id: BeatId = Field(alias="beatId")
    narration: NarrationText
    act: TeachingAct
    directive: TeachingDirective


def teaching_beat_sha256(beat: TeachingBeatDraft) -> str:
    """Hash the complete model-authored semantic teaching request."""

    return canonical_sha256(
        beat.model_dump(mode="json", by_alias=True),
        domain=TEACHING_BEAT_HASH_DOMAIN,
    )


class PythagoreanAreaIdentityState(LiveSceneContract):
    """Server-owned materialized prefix for one Pythagorean identity component."""

    kind: Literal["pythagorean_area_identity"] = "pythagorean_area_identity"
    id: SemanticComponentId
    revealed_roles: Annotated[
        tuple[PythagoreanRole, ...],
        Field(max_length=len(PYTHAGOREAN_ROLE_ORDER)),
    ] = Field(default=(), alias="revealedRoles")

    @model_validator(mode="after")
    def validate_revealed_prefix(self) -> Self:
        expected = PYTHAGOREAN_ROLE_ORDER[: len(self.revealed_roles)]
        if self.revealed_roles != expected:
            raise ValueError("revealedRoles must be an ordered Pythagorean role prefix")
        return self


SemanticComponentState: TypeAlias = PythagoreanAreaIdentityState


class SemanticSceneState(LiveSceneContract):
    """Authoritative semantic scene snapshot maintained by the server."""

    revision: NonNegativeRevision
    components: Annotated[
        tuple[SemanticComponentState, ...],
        Field(max_length=MAX_SEMANTIC_COMPONENTS),
    ] = ()
    certificate_head_sha256: Sha256Digest | None = Field(
        default=None,
        alias="certificateHeadSha256",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_unique_component_ids(self) -> Self:
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("semantic component ids must be unique")
        return self


class VerificationObligation(StrEnum):
    """Machine-readable realization obligations independently checked by the server."""

    STABLE_ID = "stable_id"
    UNIQUE_IDS = "unique_ids"
    BOARD_BOUNDS = "board_bounds"
    RIGHT_ANGLE = "right_angle"
    ATTACHED_SQUARE = "attached_square"
    SQUARE_EDGE_LENGTH = "square_edge_length"
    HYPOTENUSE_RATIO = "hypotenuse_ratio"
    LABEL_CONTAINMENT = "label_containment"
    LABEL_SEPARATION = "label_separation"
    COMPILER_IDENTITY = "compiler_identity"


class VerificationReceipt(LiveSceneContract):
    """Verifier-issued structural checks for one not-yet-materialized visual node.

    A receipt does not acknowledge browser materialization and does not verify the
    model-authored narration carried by the surrounding scene patch.
    """

    issuer: Literal["semantic_verifier"] = "semantic_verifier"
    component_id: SemanticComponentId = Field(alias="componentId")
    role: PythagoreanRole
    node_id: SceneNodeId = Field(alias="nodeId")
    obligation_codes: Annotated[
        tuple[VerificationObligation, ...],
        Field(min_length=1),
    ] = Field(alias="obligationCodes")
    verified: Literal[True] = True

    @model_validator(mode="after")
    def validate_unique_obligation_codes(self) -> Self:
        if len(self.obligation_codes) != len(set(self.obligation_codes)):
            raise ValueError("verification obligation codes must be unique")
        return self


def scene_patch_sha256(patch: ScenePatchDraft) -> str:
    """Hash the complete low-level patch, including model-authored narration."""

    return canonical_sha256(
        patch.model_dump(mode="json", by_alias=True),
        domain=SCENE_PATCH_HASH_DOMAIN,
    )


def verification_receipt_sha256(receipt: VerificationReceipt) -> str:
    """Hash the verifier's exact structural claims for one visual node."""

    return canonical_sha256(
        receipt.model_dump(mode="json", by_alias=True),
        domain=VERIFICATION_RECEIPT_HASH_DOMAIN,
    )


def semantic_scene_sha256(scene: SemanticSceneState) -> str:
    """Hash semantic contents while deliberately excluding the chain-head metadata."""

    payload = {
        "revision": scene.revision,
        "components": [
            component.model_dump(mode="json", by_alias=True) for component in scene.components
        ],
    }
    return canonical_sha256(payload, domain=SEMANTIC_SCENE_HASH_DOMAIN)


class CompilerCertificateBodyV1(LiveSceneContract):
    """Versioned commitments made by the deterministic semantic compiler."""

    v: Literal[1] = 1
    issuer: Literal["semantic_compiler"] = "semantic_compiler"
    compiler_version: Literal[SEMANTIC_COMPILER_VERSION] = Field(alias="compilerVersion")
    canonicalization: Literal[SEMANTIC_CANONICALIZATION] = SEMANTIC_CANONICALIZATION
    hash_algorithm: Literal[SEMANTIC_HASH_ALGORITHM] = Field(
        default=SEMANTIC_HASH_ALGORITHM,
        alias="hashAlgorithm",
    )
    atom_id: AtomId = Field(alias="atomId")
    beat_id: BeatId = Field(alias="beatId")
    beat_sha256: Sha256Digest = Field(alias="beatSha256")
    component_id: SemanticComponentId = Field(alias="componentId")
    role: PythagoreanRole
    node_id: SceneNodeId = Field(alias="nodeId")
    atom_ordinal: PositiveSequence = Field(alias="atomOrdinal")
    base_semantic_revision: NonNegativeRevision = Field(alias="baseSemanticRevision")
    result_semantic_revision: PositiveRevision = Field(alias="resultSemanticRevision")
    base_scene_sha256: Sha256Digest = Field(alias="baseSceneSha256")
    result_scene_sha256: Sha256Digest = Field(alias="resultSceneSha256")
    patch_sha256: Sha256Digest = Field(alias="patchSha256")
    receipt_sha256: Sha256Digest = Field(alias="receiptSha256")
    previous_certificate_sha256: Sha256Digest | None = Field(
        default=None,
        alias="previousCertificateSha256",
    )

    @model_validator(mode="after")
    def validate_transition_identity(self) -> Self:
        if self.result_semantic_revision != self.base_semantic_revision + 1:
            raise ValueError(
                "certificate resultSemanticRevision must be one greater than baseSemanticRevision"
            )
        expected_ordinal = PYTHAGOREAN_ROLE_ORDER.index(self.role) + 1
        if self.atom_ordinal != expected_ordinal:
            raise ValueError("certificate atomOrdinal must be the absolute role ordinal")
        return self


def compiler_certificate_sha256(body: CompilerCertificateBodyV1) -> str:
    """Hash the certificate body without creating a recursive self-reference."""

    return canonical_sha256(
        body.model_dump(mode="json", by_alias=True),
        domain=COMPILER_CERTIFICATE_HASH_DOMAIN,
    )


class CompilerCertificateV1(LiveSceneContract):
    """Self-checking compiler-integrity certificate, not renderer evidence."""

    body: CompilerCertificateBodyV1
    certificate_sha256: Sha256Digest = Field(alias="certificateSha256")

    @model_validator(mode="after")
    def validate_certificate_digest(self) -> Self:
        expected = compiler_certificate_sha256(self.body)
        if not digest_matches(self.certificate_sha256, expected):
            raise ValueError("certificateSha256 must match the canonical certificate body")
        return self


class CompiledVisualAtom(LiveSceneContract):
    """One compiler-verified candidate for a low-level scene commit."""

    atom_id: AtomId = Field(alias="atomId")
    beat_id: BeatId = Field(alias="beatId")
    component_id: SemanticComponentId = Field(alias="componentId")
    role: PythagoreanRole
    patch: ScenePatchDraft
    receipt: VerificationReceipt
    certificate: CompilerCertificateV1 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_bound_metadata(self) -> Self:
        if self.patch.patch_id != self.atom_id:
            raise ValueError("compiled atom patchId must equal atomId")
        if len(self.patch.operations) != 1 or self.patch.operations[0].op != "put":
            raise ValueError("compiled visual atom must contain exactly one put operation")

        operation = self.patch.operations[0]
        if operation.target_id != self.receipt.node_id:
            raise ValueError("verification receipt nodeId must match the atom target")
        if self.receipt.component_id != self.component_id:
            raise ValueError("verification receipt componentId must match the atom componentId")
        if self.receipt.role != self.role:
            raise ValueError("verification receipt role must match the atom role")

        if self.certificate is not None:
            body = self.certificate.body
            if body.atom_id != self.atom_id:
                raise ValueError("certificate atomId must match the atom")
            if body.beat_id != self.beat_id:
                raise ValueError("certificate beatId must match the atom")
            if body.component_id != self.component_id:
                raise ValueError("certificate componentId must match the atom")
            if body.role != self.role:
                raise ValueError("certificate role must match the atom")
            if body.node_id != operation.target_id:
                raise ValueError("certificate nodeId must match the atom target")
            if not digest_matches(body.patch_sha256, scene_patch_sha256(self.patch)):
                raise ValueError("certificate patchSha256 must match the atom patch")
            if not digest_matches(
                body.receipt_sha256,
                verification_receipt_sha256(self.receipt),
            ):
                raise ValueError("certificate receiptSha256 must match the verifier receipt")
        return self


def _advance_semantic_scene(
    scene: SemanticSceneState,
    *,
    component_id: SemanticComponentId,
    revealed_roles: tuple[PythagoreanRole, ...],
    certificate_head_sha256: Sha256Digest,
) -> SemanticSceneState:
    component = PythagoreanAreaIdentityState(
        id=component_id,
        revealed_roles=revealed_roles,
    )
    if any(existing.id == component_id for existing in scene.components):
        components = tuple(
            component if existing.id == component_id else existing for existing in scene.components
        )
    else:
        components = (*scene.components, component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=components,
        certificate_head_sha256=certificate_head_sha256,
    )


class CompiledTeachingBeat(LiveSceneContract):
    """Atomic compiler result with an exact intended semantic suffix."""

    beat: TeachingBeatDraft
    base_scene: SemanticSceneState = Field(alias="baseScene")
    result_scene: SemanticSceneState = Field(alias="resultScene")
    atoms: Annotated[
        tuple[CompiledVisualAtom, ...],
        Field(max_length=len(PYTHAGOREAN_ROLE_ORDER)),
    ] = ()

    @model_validator(mode="after")
    def validate_semantic_transition(self) -> Self:
        component_id = self.beat.directive.id
        target_roles = roles_through(self.beat.directive.reveal_through)
        base_component = next(
            (component for component in self.base_scene.components if component.id == component_id),
            None,
        )
        result_component = next(
            (
                component
                for component in self.result_scene.components
                if component.id == component_id
            ),
            None,
        )

        base_roles = () if base_component is None else base_component.revealed_roles
        if base_roles != target_roles[: len(base_roles)]:
            raise ValueError("compiled teaching beat cannot move a component backward")
        if result_component is None or result_component.revealed_roles != target_roles:
            raise ValueError("resultScene must contain the directive's exact target role prefix")

        expected_roles = target_roles[len(base_roles) :]
        actual_roles = tuple(atom.role for atom in self.atoms)
        if actual_roles != expected_roles:
            raise ValueError("compiled atoms must be the exact missing Pythagorean role suffix")
        if self.result_scene.revision != self.base_scene.revision + len(self.atoms):
            raise ValueError("resultScene revision must advance once per compiled atom")

        for atom in self.atoms:
            if atom.beat_id != self.beat.beat_id:
                raise ValueError("compiled atom beatId must match the teaching beat")
            if atom.component_id != component_id:
                raise ValueError("compiled atom componentId must match the directive")

        base_others = tuple(
            component for component in self.base_scene.components if component.id != component_id
        )
        result_others = tuple(
            component for component in self.result_scene.components if component.id != component_id
        )
        if result_others != base_others:
            raise ValueError("compiled teaching beat must preserve unrelated semantic components")

        certification = tuple(atom.certificate is not None for atom in self.atoms)
        if any(certification) and not all(certification):
            raise ValueError("compiled teaching beat cannot mix certified and legacy atoms")

        if not self.atoms:
            if self.result_scene != self.base_scene:
                raise ValueError("an empty compiled teaching beat must preserve the entire scene")
            return self

        if not any(certification):
            if (
                self.base_scene.certificate_head_sha256 is not None
                or self.result_scene.certificate_head_sha256 is not None
            ):
                raise ValueError("legacy atoms cannot consume or produce a certificate chain head")
            return self

        current_scene = self.base_scene
        expected_beat_sha256 = teaching_beat_sha256(self.beat)
        for atom in self.atoms:
            certificate = atom.certificate
            if certificate is None:  # Defensive narrowing after the all-or-none check.
                raise ValueError("compiled teaching beat certificate chain is incomplete")
            body = certificate.body
            if not digest_matches(body.beat_sha256, expected_beat_sha256):
                raise ValueError("certificate beatSha256 must match the exact teaching beat")
            if body.base_semantic_revision != current_scene.revision:
                raise ValueError("certificate baseSemanticRevision must match the prior scene")
            if not digest_matches(
                body.base_scene_sha256,
                semantic_scene_sha256(current_scene),
            ):
                raise ValueError("certificate baseSceneSha256 must match the prior scene")
            if body.previous_certificate_sha256 != current_scene.certificate_head_sha256:
                raise ValueError("certificate previousCertificateSha256 must match the chain head")

            next_scene = _advance_semantic_scene(
                current_scene,
                component_id=component_id,
                revealed_roles=target_roles[: body.atom_ordinal],
                certificate_head_sha256=certificate.certificate_sha256,
            )
            if body.result_semantic_revision != next_scene.revision:
                raise ValueError("certificate resultSemanticRevision must match the next scene")
            if not digest_matches(
                body.result_scene_sha256,
                semantic_scene_sha256(next_scene),
            ):
                raise ValueError("certificate resultSceneSha256 must match the next scene")
            current_scene = next_scene

        if current_scene != self.result_scene:
            raise ValueError("resultScene must equal the certified semantic chain result")
        return self


TEACHING_BEAT_DRAFT_ADAPTER = TypeAdapter(TeachingBeatDraft)
VISUAL_ACT_DECISION_ADAPTER = TypeAdapter(VisualActDecision)
