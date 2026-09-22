"""Contracts for the conversational handoff into Gate 1.8."""

from uuid import UUID

import pytest
from murmur.canvas.state import ANIMATION_TOOLS
from murmur.live_scene.conversation_storyboard import (
    CONVERSATION_STORYBOARD_TOOL_NAME,
    START_PROJECTILE_STORYBOARD_SCHEMA,
    create_conversation_storyboard_command,
)
from murmur.live_scene.semantic_storyboard_requests import (
    PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
)
from murmur.llm.pipeline import LLMPipeline
from murmur.llm.tool_runtime import ToolConversationMixin
from murmur.tools.contracts import ToolCall
from pydantic import ValidationError


class _StoryboardRuntime(ToolConversationMixin):
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.storyboard_callback = self.commands.append


def test_command_uses_verified_defaults_and_server_owned_identity() -> None:
    command = create_conversation_storyboard_command(
        {"prompt": "  Trace both arcs, then compare their ranges.  "}
    )

    assert command.protocol == PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL
    assert command.problem_spec.speed_mps == 20
    assert command.problem_spec.angles_deg == (30, 60)
    assert command.prompt == "Trace both arcs, then compare their ranges."
    assert UUID(str(command.command_id)).version == 4
    assert command.model_dump(mode="json", by_alias=True) == {
        "v": 1,
        "commandId": str(command.command_id),
        "protocol": "projectile_comparison_storyboard_v1",
        "problemSpec": {"v": 1, "speedMps": 20, "anglesDeg": [30, 60]},
        "prompt": "Trace both arcs, then compare their ranges.",
    }


@pytest.mark.parametrize("speed", [20, 25, 30])
@pytest.mark.parametrize("angles", [[30, 45], [30, 60], [45, 60]])
def test_command_accepts_every_certified_problem(speed: int, angles: list[int]) -> None:
    command = create_conversation_storyboard_command(
        {
            "prompt": "Compare the paths.",
            "speedMps": speed,
            "anglesDeg": angles,
        }
    )

    assert command.problem_spec.speed_mps == speed
    assert command.problem_spec.angles_deg == tuple(angles)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"prompt": ""},
        {"prompt": "x", "speedMps": 10},
        {"prompt": "x", "speedMps": True},
        {"prompt": "x", "anglesDeg": [60, 30]},
        {"prompt": "x", "anglesDeg": [30, 30]},
        {"prompt": "x", "anglesDeg": [30, 75]},
        {"prompt": "x", "anglesDeg": [30]},
        {"prompt": "x", "unknown": "field"},
        {"prompt": "x" * 2_001},
    ],
)
def test_command_rejects_arguments_outside_gate_1_8(arguments: dict) -> None:
    with pytest.raises(ValidationError):
        create_conversation_storyboard_command(arguments)


def test_tool_schema_is_closed_and_uses_the_expected_name() -> None:
    function = START_PROJECTILE_STORYBOARD_SCHEMA["function"]
    assert isinstance(function, dict)
    assert function["name"] == CONVERSATION_STORYBOARD_TOOL_NAME
    parameters = function["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["additionalProperties"] is False
    assert parameters["required"] == ["prompt"]
    assert CONVERSATION_STORYBOARD_TOOL_NAME in {
        tool["function"]["name"] for tool in ANIMATION_TOOLS
    }


@pytest.mark.asyncio
async def test_tool_runtime_publishes_one_typed_command() -> None:
    runtime = _StoryboardRuntime()

    result = await runtime._execute_single_tool_call(
        ToolCall(
            "call-1",
            CONVERSATION_STORYBOARD_TOOL_NAME,
            {
                "prompt": "Trace the lower path before the higher path.",
                "speedMps": 25,
                "anglesDeg": [30, 45],
            },
        )
    )

    assert result.success is True
    assert result.tool_call_id == "call-1"
    assert "certified projectile storyboard" in result.content
    assert len(runtime.commands) == 1
    assert runtime.commands[0]["problemSpec"] == {
        "v": 1,
        "speedMps": 25,
        "anglesDeg": [30, 45],
    }
    assert runtime.commands[0]["prompt"] == ("Trace the lower path before the higher path.")


def test_storyboard_tool_is_serialized_with_other_visual_mutations() -> None:
    assert CONVERSATION_STORYBOARD_TOOL_NAME in LLMPipeline.MUTATING_TOOL_NAMES
