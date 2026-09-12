"""Disjoint Reflex and Director requests for Gate 1.8 storyboards."""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, model_validator

from murmur.live_scene.contracts import (
    LiveSceneContract,
    PositiveSequence,
    PromptText,
    SceneState,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
)

PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL: Final = "projectile_comparison_storyboard_v1"
# The shorter name is kept inside the new module only; no old request union is
# widened in this milestone.
SEMANTIC_STORYBOARD_PROTOCOL: Final = PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL


class _SemanticStoryboardRequestBase(LiveSceneContract):
    """Exact problem and frontier fields shared by the two routing modes."""

    protocol: Literal[PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL]
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1 = Field(alias="baseSemanticScene")

    @model_validator(mode="after")
    def validate_bound_frontier(self) -> Self:
        if self.base_scene.revision != self.base_semantic_scene.revision:
            raise ValueError("baseScene and baseSemanticScene revisions must match")
        if self.base_semantic_scene.components:
            component = self.base_semantic_scene.components[0]
            if component.problem_spec != self.problem_spec:
                raise ValueError("problemSpec must match the accepted storyboard problem")
        return self


class SemanticStoryboardReflexRequestV1(_SemanticStoryboardRequestBase):
    """Provider-free request for the one deterministic paired-launch anchor."""

    routing_mode: Literal["reflex"] = Field(alias="routingMode")

    @model_validator(mode="after")
    def validate_fresh_anchor(self) -> Self:
        if self.base_scene.revision != 0 or self.base_scene.nodes:
            raise ValueError("the storyboard anchor requires an empty low-level revision 0 scene")
        if self.base_semantic_scene.components:
            raise ValueError("the storyboard anchor requires an empty semantic scene")
        return self


class SemanticStoryboardDirectorRequestV1(_SemanticStoryboardRequestBase):
    """Prompt-only request continuing the exact certified storyboard frontier."""

    routing_mode: Literal["director"] = Field(alias="routingMode")
    prompt: PromptText

    @model_validator(mode="after")
    def validate_anchor_exists(self) -> Self:
        if not self.base_semantic_scene.components:
            raise ValueError("Director mode requires the certified storyboard anchor")
        return self


SemanticStoryboardRequestV1: TypeAlias = Annotated[
    SemanticStoryboardReflexRequestV1 | SemanticStoryboardDirectorRequestV1,
    Field(discriminator="routing_mode"),
]

SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER = TypeAdapter(SemanticStoryboardRequestV1)


__all__ = [
    "PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL",
    "SEMANTIC_STORYBOARD_PROTOCOL",
    "SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER",
    "SemanticStoryboardDirectorRequestV1",
    "SemanticStoryboardReflexRequestV1",
    "SemanticStoryboardRequestV1",
]
