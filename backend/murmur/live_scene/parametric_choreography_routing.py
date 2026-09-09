"""Closed routing boundary for Gate 1.6 parametric choreography.

The Visual Reflex and Director deliberately meet only after the equation has
been parsed and bound by the service.  Reflex accepts one already-validated
closed route and has no provider dependency.  Director accepts a tiny model
decision that contains pedagogical direction only; the server supplies the
problem identity, component identity, checkpoint suffix, beat identity, and
every compiler-owned presentation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator

from murmur.live_scene.choreography_contracts import (
    AdvanceChoreographyRouteV2,
    ClarifyCornerRouteV2,
    CompletingSquareStage,
    RoutedChoreographyBeatV3,
    RoutedChoreographyRouteV2,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
    checkpoint_prefix,
    checkpoints_through,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import (
    LIVE_SCENE_SCHEMA_VERSION,
    MAX_SAFE_SEQUENCE,
    LiveSceneContract,
)
from murmur.live_scene.semantic_contracts import (
    SemanticComponentId,
    SemanticSceneState,
    VisualActAbstainReason,
)

PARAMETRIC_DIRECTOR_DECISION_VERSION = LIVE_SCENE_SCHEMA_VERSION
PARAMETRIC_CHOREOGRAPHY_COMPONENT_ID: SemanticComponentId = "square-lesson"


class _ParametricDirectorDecisionBase(LiveSceneContract):
    """Shared exact version field for the untrusted Director output."""

    v: Literal[PARAMETRIC_DIRECTOR_DECISION_VERSION]

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value


class StartParametricChoreographyDecisionV1(_ParametricDirectorDecisionBase):
    """Ask the server to start the bound problem through one closed stage."""

    action: Literal["start"]
    stage: CompletingSquareStage


class ContinueParametricChoreographyDecisionV1(_ParametricDirectorDecisionBase):
    """Ask the server to extend the sole accepted parametric component."""

    action: Literal["continue"]
    stage: CompletingSquareStage


class ClarifyParametricChoreographyDecisionV1(_ParametricDirectorDecisionBase):
    """Ask for the one server-authored corner-detail detour."""

    action: Literal["clarify"]


class AbstainParametricChoreographyDecisionV1(_ParametricDirectorDecisionBase):
    """Deliberately leave the accepted board unchanged."""

    action: Literal["abstain"]
    reason_code: VisualActAbstainReason = Field(alias="reasonCode")


ParametricChoreographyDirectorDecisionV1: TypeAlias = Annotated[
    StartParametricChoreographyDecisionV1
    | ContinueParametricChoreographyDecisionV1
    | ClarifyParametricChoreographyDecisionV1
    | AbstainParametricChoreographyDecisionV1,
    Field(discriminator="action"),
]

PARAMETRIC_CHOREOGRAPHY_DIRECTOR_DECISION_ADAPTER = TypeAdapter(
    ParametricChoreographyDirectorDecisionV1
)


class ParametricChoreographyRoutingErrorCode(StrEnum):
    """Stable reasons a closed route cannot extend the accepted V3 frontier."""

    COMPONENT_ALREADY_EXISTS = "component_already_exists"
    COMPONENT_NOT_FOUND = "component_not_found"
    MULTIPLE_COMPONENTS_UNSUPPORTED = "multiple_components_unsupported"
    COMPONENT_KIND_MISMATCH = "component_kind_mismatch"
    PROBLEM_MISMATCH = "problem_mismatch"
    NON_FORWARD_TARGET = "non_forward_target"
    CLARIFICATION_UNAVAILABLE = "clarification_unavailable"


class ParametricChoreographyRoutingError(ValueError):
    """Fail-closed semantic mismatch that never retains user or model text."""

    def __init__(self, code: ParametricChoreographyRoutingErrorCode) -> None:
        if not isinstance(code, ParametricChoreographyRoutingErrorCode):
            raise TypeError("code must be a ParametricChoreographyRoutingErrorCode")
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedParametricChoreographyAct:
    """Server-bound route and exact main-checkpoint suffix for one action."""

    component_kind: Literal["completing_square_parametric"]
    component_id: SemanticComponentId
    problem_spec: CompletingSquareProblemSpecV1
    route: RoutedChoreographyRouteV2
    missing_checkpoints: tuple[CompletingSquareMainCheckpoint, ...]


def validate_parametric_choreography_frontier(
    problem_spec: CompletingSquareProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ParametricCompletingSquareStateV1 | None:
    """Validate the sole-component V3 frontier without changing it."""

    if not isinstance(problem_spec, CompletingSquareProblemSpecV1):
        raise TypeError("problem_spec must be a CompletingSquareProblemSpecV1")
    if not isinstance(semantic_scene, SemanticSceneState):
        raise TypeError("semantic_scene must be a SemanticSceneState")
    if len(semantic_scene.components) > 1:
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.MULTIPLE_COMPONENTS_UNSUPPORTED
        )
    if not semantic_scene.components:
        return None

    component = semantic_scene.components[0]
    if not isinstance(component, ParametricCompletingSquareStateV1):
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.COMPONENT_KIND_MISMATCH
        )
    if component.problem_spec != problem_spec:
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.PROBLEM_MISMATCH
        )
    return component


def _resolve_closed_route(
    route: RoutedChoreographyRouteV2,
    *,
    problem_spec: CompletingSquareProblemSpecV1,
    semantic_scene: SemanticSceneState,
    require_component: bool | None,
) -> ResolvedParametricChoreographyAct:
    component = validate_parametric_choreography_frontier(problem_spec, semantic_scene)

    if require_component is False and component is not None:
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.COMPONENT_ALREADY_EXISTS
        )
    if require_component is True and component is None:
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.COMPONENT_NOT_FOUND
        )

    component_id = component.id if component is not None else PARAMETRIC_CHOREOGRAPHY_COMPONENT_ID
    if isinstance(route, ClarifyCornerRouteV2):
        if (
            component is None
            or component.last_main_checkpoint is not CompletingSquareMainCheckpoint.MISSING_CORNER
            or component.corner_clarified
        ):
            raise ParametricChoreographyRoutingError(
                ParametricChoreographyRoutingErrorCode.CLARIFICATION_UNAVAILABLE
            )
        return ResolvedParametricChoreographyAct(
            component_kind="completing_square_parametric",
            component_id=component_id,
            problem_spec=problem_spec,
            route=route,
            missing_checkpoints=(),
        )
    if not isinstance(route, AdvanceChoreographyRouteV2):
        raise TypeError("route must be a RoutedChoreographyRouteV2")

    target = checkpoints_through(route.target_stage)
    current = checkpoint_prefix(component.last_main_checkpoint if component is not None else None)
    if current != target[: len(current)] or len(current) >= len(target):
        raise ParametricChoreographyRoutingError(
            ParametricChoreographyRoutingErrorCode.NON_FORWARD_TARGET
        )
    return ResolvedParametricChoreographyAct(
        component_kind="completing_square_parametric",
        component_id=component_id,
        problem_spec=problem_spec,
        route=route,
        missing_checkpoints=target[len(current) :],
    )


def resolve_parametric_reflex_route(
    requested_route: RoutedChoreographyRouteV2,
    *,
    problem_spec: CompletingSquareProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ResolvedParametricChoreographyAct:
    """Resolve explicit Reflex input through a dependency-free zero-provider path."""

    return _resolve_closed_route(
        requested_route,
        problem_spec=problem_spec,
        semantic_scene=semantic_scene,
        require_component=None,
    )


def resolve_parametric_director_decision(
    decision: ParametricChoreographyDirectorDecisionV1,
    *,
    problem_spec: CompletingSquareProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ResolvedParametricChoreographyAct | None:
    """Validate one minimal Director choice against the bound server frontier."""

    if isinstance(decision, AbstainParametricChoreographyDecisionV1):
        validate_parametric_choreography_frontier(problem_spec, semantic_scene)
        return None
    if isinstance(decision, StartParametricChoreographyDecisionV1):
        return _resolve_closed_route(
            AdvanceChoreographyRouteV2(targetStage=decision.stage),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=False,
        )
    if isinstance(decision, ContinueParametricChoreographyDecisionV1):
        return _resolve_closed_route(
            AdvanceChoreographyRouteV2(targetStage=decision.stage),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=True,
        )
    if isinstance(decision, ClarifyParametricChoreographyDecisionV1):
        return _resolve_closed_route(
            ClarifyCornerRouteV2(),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=True,
        )
    raise TypeError("decision must be a ParametricChoreographyDirectorDecisionV1")


def _server_beat_id(generation: int) -> str:
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise TypeError("generation must be an integer")
    if not 1 <= generation <= MAX_SAFE_SEQUENCE:
        raise ValueError(f"generation must be between 1 and {MAX_SAFE_SEQUENCE}")
    return f"route-{generation:x}"


def lower_resolved_parametric_choreography_act(
    resolved: ResolvedParametricChoreographyAct,
    *,
    generation: int,
) -> RoutedChoreographyBeatV3:
    """Lower one resolved route to the presentation-free V3 compiler input."""

    if not isinstance(resolved, ResolvedParametricChoreographyAct):
        raise TypeError("resolved must be a ResolvedParametricChoreographyAct")
    return RoutedChoreographyBeatV3(
        beatId=_server_beat_id(generation),
        componentKind=resolved.component_kind,
        componentId=resolved.component_id,
        problemSpec=resolved.problem_spec,
        route=resolved.route,
    )


__all__ = [
    "PARAMETRIC_CHOREOGRAPHY_COMPONENT_ID",
    "PARAMETRIC_CHOREOGRAPHY_DIRECTOR_DECISION_ADAPTER",
    "PARAMETRIC_DIRECTOR_DECISION_VERSION",
    "AbstainParametricChoreographyDecisionV1",
    "ClarifyParametricChoreographyDecisionV1",
    "ContinueParametricChoreographyDecisionV1",
    "ParametricChoreographyDirectorDecisionV1",
    "ParametricChoreographyRoutingError",
    "ParametricChoreographyRoutingErrorCode",
    "ResolvedParametricChoreographyAct",
    "StartParametricChoreographyDecisionV1",
    "lower_resolved_parametric_choreography_act",
    "resolve_parametric_director_decision",
    "resolve_parametric_reflex_route",
    "validate_parametric_choreography_frontier",
]
