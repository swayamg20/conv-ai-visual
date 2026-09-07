"""Pure state validation for model-authored visual-act decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeAlias

from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ClarifyCornerRouteV2,
    RoutedChoreographyRouteV2,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
    checkpoint_prefix,
    checkpoints_through,
)
from murmur.live_scene.semantic_contracts import (
    AbstainVisualDecision,
    ClarifyCornerDecision,
    ContinueChoreographyDecision,
    ContinueVisualDecision,
    PythagoreanAreaIdentityState,
    PythagoreanComponentKind,
    PythagoreanRole,
    PythagoreanStage,
    SemanticComponentId,
    SemanticSceneState,
    StartChoreographyDecision,
    StartVisualDecision,
    VisualActDecision,
    roles_through,
)


class VisualActRoutingErrorCode(StrEnum):
    """Stable reasons a structurally valid decision cannot extend a scene."""

    COMPONENT_ALREADY_EXISTS = "component_already_exists"
    COMPONENT_NOT_FOUND = "component_not_found"
    MULTIPLE_COMPONENTS_UNSUPPORTED = "multiple_components_unsupported"
    NON_FORWARD_TARGET = "non_forward_target"
    PROOF_REQUIRES_IDENTITY = "proof_requires_identity"
    COMPONENT_KIND_MISMATCH = "component_kind_mismatch"
    CLARIFICATION_UNAVAILABLE = "clarification_unavailable"


class VisualActRoutingError(ValueError):
    """Fail-closed state mismatch without user or provider content."""

    def __init__(self, code: VisualActRoutingErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedVisualAct:
    """Server-resolved target and exact semantic suffix for one visual act."""

    component_kind: PythagoreanComponentKind
    component_id: SemanticComponentId
    target_stage: PythagoreanStage
    missing_roles: tuple[PythagoreanRole, ...]


@dataclass(frozen=True, slots=True)
class ResolvedChoreographyAct:
    """Server-resolved completing-square route and its exact missing suffix."""

    component_kind: Literal["completing_square"]
    component_id: SemanticComponentId
    route: RoutedChoreographyRouteV2
    missing_checkpoints: tuple[CompletingSquareMainCheckpoint, ...]


ResolvedVisualRoute: TypeAlias = ResolvedVisualAct | ResolvedChoreographyAct


def resolve_visual_act(
    decision: VisualActDecision,
    scene: SemanticSceneState,
) -> ResolvedVisualRoute | None:
    """Resolve a strict forward-only decision without mutating ``scene``."""

    if not isinstance(scene, SemanticSceneState):
        raise TypeError("scene must be a SemanticSceneState")
    if isinstance(decision, AbstainVisualDecision):
        return None
    if not isinstance(
        decision,
        (
            StartVisualDecision,
            ContinueVisualDecision,
            StartChoreographyDecision,
            ContinueChoreographyDecision,
            ClarifyCornerDecision,
        ),
    ):
        raise TypeError("decision must be a VisualActDecision")
    if len(scene.components) > 1:
        raise VisualActRoutingError(VisualActRoutingErrorCode.MULTIPLE_COMPONENTS_UNSUPPORTED)

    if isinstance(decision, StartChoreographyDecision):
        if scene.components:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_ALREADY_EXISTS)
        target = checkpoints_through(decision.target_stage)
        return ResolvedChoreographyAct(
            component_kind=decision.component_kind,
            component_id="square-lesson",
            route=AdvanceChoreographyRouteV2(target_stage=decision.target_stage),
            missing_checkpoints=target,
        )

    if isinstance(decision, (ContinueChoreographyDecision, ClarifyCornerDecision)):
        component = next(
            (candidate for candidate in scene.components if candidate.id == decision.component_id),
            None,
        )
        if component is None:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_NOT_FOUND)
        if not isinstance(component, CompletingSquareState):
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_KIND_MISMATCH)

        if isinstance(decision, ClarifyCornerDecision):
            if (
                component.last_main_checkpoint is not CompletingSquareMainCheckpoint.MISSING_CORNER
                or component.corner_clarified
            ):
                raise VisualActRoutingError(VisualActRoutingErrorCode.CLARIFICATION_UNAVAILABLE)
            return ResolvedChoreographyAct(
                component_kind="completing_square",
                component_id=component.id,
                route=ClarifyCornerRouteV2(),
                missing_checkpoints=(),
            )

        target = checkpoints_through(decision.target_stage)
        current = checkpoint_prefix(component.last_main_checkpoint)
        if current != target[: len(current)] or len(current) >= len(target):
            raise VisualActRoutingError(VisualActRoutingErrorCode.NON_FORWARD_TARGET)
        return ResolvedChoreographyAct(
            component_kind="completing_square",
            component_id=component.id,
            route=AdvanceChoreographyRouteV2(target_stage=decision.target_stage),
            missing_checkpoints=target[len(current) :],
        )

    if isinstance(decision, StartVisualDecision):
        if scene.components:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_ALREADY_EXISTS)
        current_roles: tuple[PythagoreanRole, ...] = ()
        component_kind: PythagoreanComponentKind = "pythagorean_area_identity"
        component_id: SemanticComponentId = "areas"
    else:
        component = next(
            (candidate for candidate in scene.components if candidate.id == decision.component_id),
            None,
        )
        if component is None:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_NOT_FOUND)
        if not isinstance(component, PythagoreanAreaIdentityState):
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_KIND_MISMATCH)
        current_roles = component.revealed_roles
        component_kind = component.kind
        component_id = decision.component_id

    target_roles = roles_through(decision.target_stage)
    if decision.target_stage is PythagoreanStage.PROOF:
        identity_roles = roles_through(PythagoreanStage.IDENTITY)
        if current_roles[: len(identity_roles)] != identity_roles:
            raise VisualActRoutingError(VisualActRoutingErrorCode.PROOF_REQUIRES_IDENTITY)
    if current_roles != target_roles[: len(current_roles)] or len(current_roles) >= len(
        target_roles
    ):
        raise VisualActRoutingError(VisualActRoutingErrorCode.NON_FORWARD_TARGET)

    return ResolvedVisualAct(
        component_kind=component_kind,
        component_id=component_id,
        target_stage=decision.target_stage,
        missing_roles=target_roles[len(current_roles) :],
    )


__all__ = [
    "ResolvedChoreographyAct",
    "ResolvedVisualAct",
    "ResolvedVisualRoute",
    "VisualActRoutingError",
    "VisualActRoutingErrorCode",
    "resolve_visual_act",
]
