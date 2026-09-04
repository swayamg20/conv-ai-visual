"""Pure state validation for model-authored visual-act decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from murmur.live_scene.semantic_contracts import (
    MAX_SEMANTIC_COMPONENTS,
    AbstainVisualDecision,
    ContinueVisualDecision,
    PythagoreanComponentKind,
    PythagoreanRole,
    PythagoreanStage,
    SemanticComponentId,
    SemanticSceneState,
    StartVisualDecision,
    VisualActDecision,
    roles_through,
)


class VisualActRoutingErrorCode(StrEnum):
    """Stable reasons a structurally valid decision cannot extend a scene."""

    COMPONENT_CAPACITY = "component_capacity"
    COMPONENT_NOT_FOUND = "component_not_found"
    NON_FORWARD_TARGET = "non_forward_target"


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


def _next_component_id(scene: SemanticSceneState) -> SemanticComponentId:
    used_ids = {component.id for component in scene.components}
    for ordinal in range(1, MAX_SEMANTIC_COMPONENTS + 1):
        candidate = "areas" if ordinal == 1 else f"areas-{ordinal}"
        if candidate not in used_ids:
            return candidate
    raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_CAPACITY)


def resolve_visual_act(
    decision: VisualActDecision,
    scene: SemanticSceneState,
) -> ResolvedVisualAct | None:
    """Resolve a strict forward-only decision without mutating ``scene``."""

    if not isinstance(scene, SemanticSceneState):
        raise TypeError("scene must be a SemanticSceneState")
    if isinstance(decision, AbstainVisualDecision):
        return None
    if not isinstance(decision, (StartVisualDecision, ContinueVisualDecision)):
        raise TypeError("decision must be a VisualActDecision")

    if isinstance(decision, StartVisualDecision):
        if len(scene.components) >= MAX_SEMANTIC_COMPONENTS:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_CAPACITY)
        current_roles: tuple[PythagoreanRole, ...] = ()
        component_kind = decision.component_kind
        component_id = _next_component_id(scene)
    else:
        component = next(
            (candidate for candidate in scene.components if candidate.id == decision.component_id),
            None,
        )
        if component is None:
            raise VisualActRoutingError(VisualActRoutingErrorCode.COMPONENT_NOT_FOUND)
        current_roles = component.revealed_roles
        component_kind = component.kind
        component_id = decision.component_id

    target_roles = roles_through(decision.target_stage)
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
    "ResolvedVisualAct",
    "VisualActRoutingError",
    "VisualActRoutingErrorCode",
    "resolve_visual_act",
]
