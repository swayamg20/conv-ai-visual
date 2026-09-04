"""Strict contracts for compiler-verified semantic teaching beats.

Only :class:`TeachingBeatDraft` crosses the model trust boundary.  Geometry,
styles, equations, child node identifiers, verification receipts, and semantic
revision bookkeeping are deliberately represented only by the server-owned
contracts in this module.
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
    SceneNodeId,
    ScenePatchDraft,
)

MAX_SEMANTIC_ID_CHARS = 32
MAX_SEMANTIC_COMPONENTS = MAX_SCENE_NODES

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

PYTHAGOREAN_STAGE_ROLES: Mapping[
    PythagoreanStage, tuple[PythagoreanRole, ...]
] = MappingProxyType(
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

    kind: Literal["pythagorean_area_identity"] = "pythagorean_area_identity"
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
    """Compiler-issued structural checks for one not-yet-materialized visual node.

    A receipt does not acknowledge browser materialization and does not verify the
    model-authored narration carried by the surrounding scene patch.
    """

    issuer: Literal["semantic_compiler"] = "semantic_compiler"
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


class CompiledVisualAtom(LiveSceneContract):
    """One compiler-verified candidate for a low-level scene commit."""

    atom_id: AtomId = Field(alias="atomId")
    beat_id: BeatId = Field(alias="beatId")
    component_id: SemanticComponentId = Field(alias="componentId")
    role: PythagoreanRole
    patch: ScenePatchDraft
    receipt: VerificationReceipt

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
        return self


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
            (component for component in self.result_scene.components if component.id == component_id),
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
        return self


TEACHING_BEAT_DRAFT_ADAPTER = TypeAdapter(TeachingBeatDraft)
