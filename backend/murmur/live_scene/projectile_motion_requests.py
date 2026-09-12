"""Exact request contracts for Gate 1.7 projectile choreography.

Structured problem identity is separate from pedagogical intent.  Reflex
requests carry one closed route and never need a provider prompt; Director
requests carry prompt text and can never smuggle in a route or parameters
chosen by the model.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.contracts import (
    LiveSceneContract,
    PositiveSequence,
    PromptText,
    SceneState,
)
from murmur.live_scene.projectile_motion_contracts import (
    AdvanceProjectileMotionRouteV1,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionRouteV1,
    ProjectileMotionStateV1,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState

PROJECTILE_CHOREOGRAPHY_PROTOCOL = "projectile_choreography_v1"


class _ProjectileMotionRequestBase(LiveSceneContract):
    """Problem-bound fields shared by both explicit routing modes."""

    protocol: Literal[PROJECTILE_CHOREOGRAPHY_PROTOCOL]
    problem_spec: ProjectileMotionProblemSpecV1 = Field(alias="problemSpec")
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")

    @model_validator(mode="after")
    def validate_bound_frontier(self) -> "_ProjectileMotionRequestBase":
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("baseScene and baseSemanticScene revisions must match")

        components = self.base_semantic_scene.components
        if len(components) > 1:
            raise ValueError("projectile requests support at most one semantic component")
        if not components:
            return self

        component = components[0]
        if not isinstance(component, ProjectileMotionStateV1):
            raise ValueError("projectile requests require a projectile_motion semantic base")
        if component.problem_spec != self.problem_spec:
            raise ValueError("problemSpec must match the accepted projectile problem")
        return self


class ProjectileMotionReflexRequestV1(_ProjectileMotionRequestBase):
    """Zero-provider request carrying one untrusted closed route choice."""

    routing_mode: Literal["reflex"] = Field(alias="routingMode")
    requested_route: ProjectileMotionRouteV1 = Field(alias="requestedRoute")

    @model_validator(mode="after")
    def validate_fresh_route(self) -> "ProjectileMotionReflexRequestV1":
        if not self.base_semantic_scene.components and not isinstance(
            self.requested_route, AdvanceProjectileMotionRouteV1
        ):
            raise ValueError("a fresh projectile request must use an advance route")
        return self


class ProjectileMotionDirectorRequestV1(_ProjectileMotionRequestBase):
    """Provider-routed request containing only untrusted pedagogical text."""

    routing_mode: Literal["director"] = Field(alias="routingMode")
    prompt: PromptText


ProjectileMotionRequestV1: TypeAlias = Annotated[
    ProjectileMotionReflexRequestV1 | ProjectileMotionDirectorRequestV1,
    Field(discriminator="routing_mode"),
]

PROJECTILE_MOTION_REQUEST_V1_ADAPTER = TypeAdapter(ProjectileMotionRequestV1)


__all__ = [
    "PROJECTILE_CHOREOGRAPHY_PROTOCOL",
    "PROJECTILE_MOTION_REQUEST_V1_ADAPTER",
    "ProjectileMotionDirectorRequestV1",
    "ProjectileMotionReflexRequestV1",
    "ProjectileMotionRequestV1",
]
