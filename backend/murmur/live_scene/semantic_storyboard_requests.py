"""Disjoint Reflex and Director requests for Gate 1.8 storyboards."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, field_validator, model_validator

from murmur.live_scene.contracts import (
    MAX_SCENE_NODES,
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

_PROMPT_EDGE_WHITESPACE: Final = (
    "\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020"
    "\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


class _SemanticStoryboardRequestBase(LiveSceneContract):
    """Exact problem and frontier fields shared by the two routing modes."""

    protocol: Literal[PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL]
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")
    base_semantic_scene: ProjectileStoryboardSemanticSceneStateV1 = Field(alias="baseSemanticScene")

    @field_validator("base_scene", mode="before")
    @classmethod
    def validate_node_bound_before_nodes(cls, value: object) -> object:
        if isinstance(value, SceneState):
            return value
        if isinstance(value, Mapping):
            nodes = value.get("nodes", ())
            if isinstance(nodes, list | tuple) and len(nodes) > MAX_SCENE_NODES:
                raise ValueError(f"baseScene exceeds {MAX_SCENE_NODES} nodes")
        return value

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

    @field_validator("prompt", mode="before")
    @classmethod
    def normalize_prompt_edges(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip(_PROMPT_EDGE_WHITESPACE)
        return value

    @model_validator(mode="after")
    def validate_anchor_exists(self) -> Self:
        if not self.base_semantic_scene.components:
            raise ValueError("Director mode requires the certified storyboard anchor")
        return self


SemanticStoryboardRequestV1: TypeAlias = Annotated[
    SemanticStoryboardReflexRequestV1 | SemanticStoryboardDirectorRequestV1,
    Field(discriminator="routing_mode"),
]


class _SemanticStoryboardRequestWireAdapter:
    """Keep model constructors ergonomic but accept aliases only at the wire."""

    _adapter = TypeAdapter(SemanticStoryboardRequestV1)

    def validate_python(self, value: object) -> SemanticStoryboardRequestV1:
        return self._adapter.validate_python(value, by_alias=True, by_name=False)

    def validate_json(self, value: str | bytes | bytearray) -> SemanticStoryboardRequestV1:
        return self._adapter.validate_json(value, by_alias=True, by_name=False)


SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER = _SemanticStoryboardRequestWireAdapter()


__all__ = [
    "PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL",
    "SEMANTIC_STORYBOARD_PROTOCOL",
    "SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER",
    "SemanticStoryboardDirectorRequestV1",
    "SemanticStoryboardReflexRequestV1",
    "SemanticStoryboardRequestV1",
]
