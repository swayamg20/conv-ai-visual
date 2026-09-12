"""Closed Visual Reflex and Director routing for projectile choreography.

The untrusted client or model chooses only a pedagogical action.  This module
binds that choice to the sole accepted projectile component, derives the exact
checkpoint transition, and owns every problem/component identifier passed to
the deterministic compiler.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, field_validator

from murmur.live_scene.contracts import (
    LIVE_SCENE_SCHEMA_VERSION,
    MAX_SAFE_SEQUENCE,
    LiveSceneContract,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionCheckpointId,
    ProjectileMotionClarificationTopic,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionRouteV1,
    ProjectileMotionStage,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    projectile_motion_checkpoint_prefix,
    projectile_motion_checkpoints_through,
)
from murmur.live_scene.semantic_contracts import (
    SemanticComponentId,
    SemanticSceneState,
    VisualActAbstainReason,
)

PROJECTILE_DIRECTOR_DECISION_VERSION = LIVE_SCENE_SCHEMA_VERSION
PROJECTILE_MOTION_COMPONENT_ID: SemanticComponentId = "projectile-lesson"


class _ProjectileDirectorDecisionBase(LiveSceneContract):
    """Shared exact version for the untrusted Director result."""

    v: Literal[PROJECTILE_DIRECTOR_DECISION_VERSION]

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value


class StartProjectileMotionDecisionV1(_ProjectileDirectorDecisionBase):
    """Start a fresh projectile lesson through one closed stage."""

    action: Literal["start"]
    stage: ProjectileMotionStage


class ContinueProjectileMotionDecisionV1(_ProjectileDirectorDecisionBase):
    """Advance the accepted projectile lesson through one closed stage."""

    action: Literal["continue"]
    stage: ProjectileMotionStage


class ClarifyProjectileMotionDecisionV1(_ProjectileDirectorDecisionBase):
    """Request one server-authored visual clarification."""

    action: Literal["clarify"]
    topic: ProjectileMotionClarificationTopic


class AbstainProjectileMotionDecisionV1(_ProjectileDirectorDecisionBase):
    """Deliberately preserve the accepted board."""

    action: Literal["abstain"]
    reason_code: VisualActAbstainReason = Field(alias="reasonCode")


ProjectileMotionDirectorDecisionV1: TypeAlias = Annotated[
    StartProjectileMotionDecisionV1
    | ContinueProjectileMotionDecisionV1
    | ClarifyProjectileMotionDecisionV1
    | AbstainProjectileMotionDecisionV1,
    Field(discriminator="action"),
]

PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER = TypeAdapter(ProjectileMotionDirectorDecisionV1)


class ProjectileMotionRoutingErrorCode(StrEnum):
    """Stable reasons a closed route cannot extend the accepted frontier."""

    COMPONENT_ALREADY_EXISTS = "component_already_exists"
    COMPONENT_NOT_FOUND = "component_not_found"
    MULTIPLE_COMPONENTS_UNSUPPORTED = "multiple_components_unsupported"
    COMPONENT_KIND_MISMATCH = "component_kind_mismatch"
    PROBLEM_MISMATCH = "problem_mismatch"
    NON_FORWARD_TARGET = "non_forward_target"
    CLARIFICATION_UNAVAILABLE = "clarification_unavailable"
    RETARGET_UNAVAILABLE = "retarget_unavailable"


class ProjectileMotionRoutingError(ValueError):
    """Fail-closed mismatch that never retains user or provider text."""

    def __init__(self, code: ProjectileMotionRoutingErrorCode) -> None:
        if not isinstance(code, ProjectileMotionRoutingErrorCode):
            raise TypeError("code must be a ProjectileMotionRoutingErrorCode")
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedProjectileMotionAct:
    """Server-bound route and exact checkpoint sequence for one action."""

    component_kind: Literal["projectile_motion"]
    component_id: SemanticComponentId
    base_problem_spec: ProjectileMotionProblemSpecV1 | None
    result_problem_spec: ProjectileMotionProblemSpecV1
    route: ProjectileMotionRouteV1
    checkpoint_ids: tuple[ProjectileMotionCheckpointId, ...]


def validate_projectile_motion_frontier(
    problem_spec: ProjectileMotionProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ProjectileMotionStateV1 | None:
    """Validate the sole projectile component without changing it."""

    if not isinstance(problem_spec, ProjectileMotionProblemSpecV1):
        raise TypeError("problem_spec must be a ProjectileMotionProblemSpecV1")
    if not isinstance(semantic_scene, SemanticSceneState):
        raise TypeError("semantic_scene must be a SemanticSceneState")
    if len(semantic_scene.components) > 1:
        raise ProjectileMotionRoutingError(
            ProjectileMotionRoutingErrorCode.MULTIPLE_COMPONENTS_UNSUPPORTED
        )
    if not semantic_scene.components:
        return None

    component = semantic_scene.components[0]
    if not isinstance(component, ProjectileMotionStateV1):
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.COMPONENT_KIND_MISMATCH)
    if component.problem_spec != problem_spec:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.PROBLEM_MISMATCH)
    return component


def _resolved_advance(
    route: AdvanceProjectileMotionRouteV1,
    *,
    problem_spec: ProjectileMotionProblemSpecV1,
    component: ProjectileMotionStateV1 | None,
) -> ResolvedProjectileMotionAct:
    target = projectile_motion_checkpoints_through(route.target_stage)
    current = projectile_motion_checkpoint_prefix(
        component.last_main_checkpoint if component is not None else None
    )
    if current != target[: len(current)] or len(current) >= len(target):
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.NON_FORWARD_TARGET)
    return ResolvedProjectileMotionAct(
        component_kind="projectile_motion",
        component_id=(component.id if component is not None else PROJECTILE_MOTION_COMPONENT_ID),
        base_problem_spec=(component.problem_spec if component is not None else None),
        result_problem_spec=problem_spec,
        route=route,
        checkpoint_ids=tuple(
            ProjectileMotionCheckpointId(item.value) for item in target[len(current) :]
        ),
    )


def _resolved_clarification(
    route: ClarifyProjectileMotionRouteV1,
    *,
    component: ProjectileMotionStateV1 | None,
) -> ResolvedProjectileMotionAct:
    if component is None:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND)
    prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[route.topic]
    settled = projectile_motion_checkpoint_prefix(component.last_main_checkpoint)
    if prerequisite not in settled or route.topic in component.clarified_topics:
        raise ProjectileMotionRoutingError(
            ProjectileMotionRoutingErrorCode.CLARIFICATION_UNAVAILABLE
        )
    return ResolvedProjectileMotionAct(
        component_kind="projectile_motion",
        component_id=component.id,
        base_problem_spec=component.problem_spec,
        result_problem_spec=component.problem_spec,
        route=route,
        checkpoint_ids=(PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS[route.topic],),
    )


def _resolved_retarget(
    route: RetargetProjectileMotionRouteV1,
    *,
    component: ProjectileMotionStateV1 | None,
) -> ResolvedProjectileMotionAct:
    if component is None:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND)
    if component.last_main_checkpoint is None:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.RETARGET_UNAVAILABLE)
    if component.problem_spec == route.target_problem_spec:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.NON_FORWARD_TARGET)
    return ResolvedProjectileMotionAct(
        component_kind="projectile_motion",
        component_id=component.id,
        base_problem_spec=component.problem_spec,
        result_problem_spec=route.target_problem_spec,
        route=route,
        checkpoint_ids=(ProjectileMotionCheckpointId.PARAMETERS_RETARGETED,),
    )


def _resolve_closed_route(
    route: ProjectileMotionRouteV1,
    *,
    problem_spec: ProjectileMotionProblemSpecV1,
    semantic_scene: SemanticSceneState,
    require_component: bool | None,
) -> ResolvedProjectileMotionAct:
    component = validate_projectile_motion_frontier(problem_spec, semantic_scene)
    if require_component is False and component is not None:
        raise ProjectileMotionRoutingError(
            ProjectileMotionRoutingErrorCode.COMPONENT_ALREADY_EXISTS
        )
    if require_component is True and component is None:
        raise ProjectileMotionRoutingError(ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND)

    if isinstance(route, AdvanceProjectileMotionRouteV1):
        return _resolved_advance(route, problem_spec=problem_spec, component=component)
    if isinstance(route, ClarifyProjectileMotionRouteV1):
        return _resolved_clarification(route, component=component)
    if isinstance(route, RetargetProjectileMotionRouteV1):
        return _resolved_retarget(route, component=component)
    raise TypeError("route must be a ProjectileMotionRouteV1")


def resolve_projectile_motion_reflex_route(
    requested_route: ProjectileMotionRouteV1,
    *,
    problem_spec: ProjectileMotionProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ResolvedProjectileMotionAct:
    """Resolve structured controls through a zero-provider dependency path."""

    return _resolve_closed_route(
        requested_route,
        problem_spec=problem_spec,
        semantic_scene=semantic_scene,
        require_component=None,
    )


def resolve_projectile_motion_director_decision(
    decision: ProjectileMotionDirectorDecisionV1,
    *,
    problem_spec: ProjectileMotionProblemSpecV1,
    semantic_scene: SemanticSceneState,
) -> ResolvedProjectileMotionAct | None:
    """Bind one minimal model decision to the accepted server frontier."""

    if isinstance(decision, AbstainProjectileMotionDecisionV1):
        validate_projectile_motion_frontier(problem_spec, semantic_scene)
        return None
    if isinstance(decision, StartProjectileMotionDecisionV1):
        return _resolve_closed_route(
            AdvanceProjectileMotionRouteV1(targetStage=decision.stage),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=False,
        )
    if isinstance(decision, ContinueProjectileMotionDecisionV1):
        return _resolve_closed_route(
            AdvanceProjectileMotionRouteV1(targetStage=decision.stage),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=True,
        )
    if isinstance(decision, ClarifyProjectileMotionDecisionV1):
        return _resolve_closed_route(
            ClarifyProjectileMotionRouteV1(topic=decision.topic),
            problem_spec=problem_spec,
            semantic_scene=semantic_scene,
            require_component=True,
        )
    raise TypeError("decision must be a ProjectileMotionDirectorDecisionV1")


def _server_beat_id(generation: int) -> str:
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise TypeError("generation must be an integer")
    if not 1 <= generation <= MAX_SAFE_SEQUENCE:
        raise ValueError(f"generation must be between 1 and {MAX_SAFE_SEQUENCE}")
    return f"projectile-route-{generation:x}"


def lower_resolved_projectile_motion_act(
    resolved: ResolvedProjectileMotionAct,
    *,
    generation: int,
) -> RoutedProjectileMotionBeatV1:
    """Lower one resolved action to the presentation-free compiler input."""

    if not isinstance(resolved, ResolvedProjectileMotionAct):
        raise TypeError("resolved must be a ResolvedProjectileMotionAct")
    return RoutedProjectileMotionBeatV1(
        beatId=_server_beat_id(generation),
        componentKind=resolved.component_kind,
        componentId=resolved.component_id,
        baseProblemSpec=resolved.base_problem_spec,
        resultProblemSpec=resolved.result_problem_spec,
        route=resolved.route,
    )


__all__ = [
    "PROJECTILE_DIRECTOR_DECISION_VERSION",
    "PROJECTILE_MOTION_COMPONENT_ID",
    "PROJECTILE_MOTION_DIRECTOR_DECISION_ADAPTER",
    "AbstainProjectileMotionDecisionV1",
    "ClarifyProjectileMotionDecisionV1",
    "ContinueProjectileMotionDecisionV1",
    "ProjectileMotionDirectorDecisionV1",
    "ProjectileMotionRoutingError",
    "ProjectileMotionRoutingErrorCode",
    "ResolvedProjectileMotionAct",
    "StartProjectileMotionDecisionV1",
    "lower_resolved_projectile_motion_act",
    "resolve_projectile_motion_director_decision",
    "resolve_projectile_motion_reflex_route",
    "validate_projectile_motion_frontier",
]
