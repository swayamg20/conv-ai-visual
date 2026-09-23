"""Closed conversational handoff into the Gate 1.8 storyboard runtime.

The conversational model may select a supported projectile problem and copy a
bounded learner instruction. It cannot author scene nodes, physics, copy,
camera motion, timing, or storyboard beats; those remain owned by Gate 1.8.
"""

from __future__ import annotations

from typing import Final, Literal, Self
from uuid import UUID, uuid4

from pydantic import UUID4, Field, TypeAdapter, field_validator, model_validator

from murmur.live_scene.contracts import LiveSceneContract, PromptText
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileAngleDeg,
    ProjectileSpeedMps,
)
from murmur.live_scene.semantic_storyboard_requests import (
    PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
)

CONVERSATION_STORYBOARD_COMMAND_VERSION: Final = 1
CONVERSATION_STORYBOARD_TOOL_NAME: Final = "start_projectile_storyboard"


class ProjectileStoryboardToolInputV1(LiveSceneContract):
    """Exact arguments the conversational model may choose for one handoff."""

    prompt: PromptText
    speed_mps: ProjectileSpeedMps = Field(default=20, alias="speedMps")
    angles_deg: tuple[ProjectileAngleDeg, ProjectileAngleDeg] = Field(
        default=(30, 60),
        alias="anglesDeg",
    )

    @field_validator("speed_mps", mode="before")
    @classmethod
    def validate_strict_speed(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("speedMps must be a strict integer")
        return value

    @field_validator("angles_deg", mode="before")
    @classmethod
    def validate_strict_angles(cls, value: object) -> object:
        if not isinstance(value, list | tuple) or len(value) != 2:
            raise ValueError("anglesDeg must contain exactly two angles")
        if any(type(angle) is not int for angle in value):
            raise ValueError("anglesDeg values must be strict integers")
        return value

    @model_validator(mode="after")
    def validate_problem(self) -> Self:
        PairedProjectileComparisonSpecV1(
            speedMps=self.speed_mps,
            anglesDeg=self.angles_deg,
        )
        return self


class ConversationStoryboardCommandV1(LiveSceneContract):
    """Browser-safe instruction to mount exactly one certified storyboard."""

    v: Literal[CONVERSATION_STORYBOARD_COMMAND_VERSION] = CONVERSATION_STORYBOARD_COMMAND_VERSION
    command_id: UUID4 = Field(default_factory=uuid4, alias="commandId")
    protocol: Literal[PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL] = (
        PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL
    )
    problem_spec: PairedProjectileComparisonSpecV1 = Field(alias="problemSpec")
    prompt: PromptText


_TOOL_INPUT_ADAPTER = TypeAdapter(ProjectileStoryboardToolInputV1)


def create_conversation_storyboard_command(
    value: object,
    *,
    command_id: UUID | None = None,
) -> ConversationStoryboardCommandV1:
    """Validate model arguments and create one server-owned handoff command."""

    tool_input = _TOOL_INPUT_ADAPTER.validate_python(
        value,
        by_alias=True,
        by_name=False,
    )
    return ConversationStoryboardCommandV1(
        commandId=command_id or uuid4(),
        problemSpec=PairedProjectileComparisonSpecV1(
            speedMps=tool_input.speed_mps,
            anglesDeg=tool_input.angles_deg,
        ),
        prompt=tool_input.prompt,
    )


START_PROJECTILE_STORYBOARD_SCHEMA: Final[dict[str, object]] = {
    "type": "function",
    "function": {
        "name": CONVERSATION_STORYBOARD_TOOL_NAME,
        "description": (
            "Start Murmur's verified animated storyboard for a same-speed, same-height "
            "projectile comparison with fixed gravity and no drag. Use this instead of "
            "teach_with_visuals when the learner asks to explain or compare supported "
            "projectile trajectories. Copy the learner's visual instruction into prompt."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "prompt": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2_000,
                    "description": "The learner's requested visual explanation.",
                },
                "speedMps": {
                    "type": "integer",
                    "enum": [20, 25, 30],
                    # GPT-OSS's tool template concatenates enum defaults as text.
                    # Keep the default in the validated input model, not here.
                    "description": "Shared launch speed in metres per second. Defaults to 20.",
                },
                "anglesDeg": {
                    "type": "array",
                    "items": {"type": "integer", "enum": [30, 45, 60]},
                    "minItems": 2,
                    "maxItems": 2,
                    "default": [30, 60],
                    "description": "Two distinct supported angles in ascending order.",
                },
            },
            "required": ["prompt"],
        },
    },
}


__all__ = [
    "CONVERSATION_STORYBOARD_COMMAND_VERSION",
    "CONVERSATION_STORYBOARD_TOOL_NAME",
    "START_PROJECTILE_STORYBOARD_SCHEMA",
    "ConversationStoryboardCommandV1",
    "ProjectileStoryboardToolInputV1",
    "create_conversation_storyboard_command",
]
