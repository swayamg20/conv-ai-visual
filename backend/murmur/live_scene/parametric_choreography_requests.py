"""Versioned request contracts for parametric live choreography.

The equation and pedagogical prompt intentionally occupy different fields.
Reflex requests carry a closed route and never need a provider prompt;
Director requests carry a prompt and can never smuggle in a client route.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BeforeValidator, Field, TypeAdapter, model_validator

from murmur.live_scene.choreography_contracts import RoutedChoreographyRouteV2
from murmur.live_scene.contracts import (
    LiveSceneContract,
    PositiveSequence,
    PromptText,
    SceneState,
)
from murmur.live_scene.projectile_motion_requests import ProjectileMotionRequestV1
from murmur.live_scene.semantic_contracts import SemanticSceneState
from murmur.live_scene.semantic_service_contracts import SemanticLiveSceneRequest
from murmur.live_scene.semantic_storyboard_requests import (
    SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER,
    SemanticStoryboardRequestV1,
)

PARAMETRIC_CHOREOGRAPHY_PROTOCOL = "parametric_choreography_v3"


class _ParametricChoreographyRequestBase(LiveSceneContract):
    """Fields shared by both explicit V3 routing modes."""

    protocol: Literal[PARAMETRIC_CHOREOGRAPHY_PROTOCOL]
    problem_text: PromptText | None = Field(alias="problemText")
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")
    base_semantic_scene: SemanticSceneState = Field(alias="baseSemanticScene")

    @model_validator(mode="after")
    def validate_lockstep_revisions(self) -> "_ParametricChoreographyRequestBase":
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("baseScene and baseSemanticScene revisions must match")
        return self


class ParametricChoreographyReflexRequestV3(_ParametricChoreographyRequestBase):
    """Zero-provider V3 request carrying one untrusted closed route choice."""

    routing_mode: Literal["reflex"] = Field(alias="routingMode")
    requested_route: RoutedChoreographyRouteV2 = Field(alias="requestedRoute")


class ParametricChoreographyDirectorRequestV3(_ParametricChoreographyRequestBase):
    """Provider-routed V3 request carrying only pedagogical intent text."""

    routing_mode: Literal["director"] = Field(alias="routingMode")
    prompt: PromptText


ParametricChoreographyRequestV3: TypeAlias = Annotated[
    ParametricChoreographyReflexRequestV3 | ParametricChoreographyDirectorRequestV3,
    Field(discriminator="routing_mode"),
]

# FastAPI validates this shared union directly, so keep the Gate 1.8 arm behind
# its aliases-only adapter. Older request arms retain their existing behavior.
_SemanticStoryboardWireRequestV1: TypeAlias = Annotated[
    SemanticStoryboardRequestV1,
    BeforeValidator(SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python),
]

# Absence of ``protocol`` remains the sealed Gate 1.5 request. Exact Gate 1.6
# and Gate 1.7 protocol literals select their own disjoint contracts; unknown
# values match none and therefore fail rather than falling back to V2.
ChoreographyLiveSceneRequest: TypeAlias = (
    SemanticLiveSceneRequest
    | ParametricChoreographyRequestV3
    | ProjectileMotionRequestV1
    | _SemanticStoryboardWireRequestV1
)

PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER = TypeAdapter(ParametricChoreographyRequestV3)
CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER = TypeAdapter(ChoreographyLiveSceneRequest)


__all__ = [
    "CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER",
    "PARAMETRIC_CHOREOGRAPHY_PROTOCOL",
    "PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER",
    "ChoreographyLiveSceneRequest",
    "ParametricChoreographyDirectorRequestV3",
    "ParametricChoreographyReflexRequestV3",
    "ParametricChoreographyRequestV3",
]
