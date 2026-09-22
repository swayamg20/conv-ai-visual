"""Prompt-routing contracts for conversational storyboard capability."""

from murmur.agents.prompting import (
    append_storyboard_tool_context,
    compile_agent_prompt,
    get_agent_tools,
)
from murmur.live_scene.conversation_storyboard import (
    CONVERSATION_STORYBOARD_TOOL_NAME,
)


def test_physics_canvas_prompt_prefers_verified_projectile_storyboard() -> None:
    prompt = compile_agent_prompt(
        {"subject": "Physics", "learning_style": "visual"},
        ["canvas"],
    )

    assert "same-speed projectile comparison launched and landed at the same height" in prompt
    assert CONVERSATION_STORYBOARD_TOOL_NAME in prompt
    assert "20 m/s and 30°/60° defaults" in prompt


def test_non_physics_prompt_does_not_claim_projectile_specialization() -> None:
    prompt = compile_agent_prompt(
        {"subject": "European history", "learning_style": "visual"},
        ["canvas"],
    )

    assert CONVERSATION_STORYBOARD_TOOL_NAME not in prompt
    assert "teach_with_visuals" in prompt


def test_runtime_storyboard_context_preserves_unsupported_fallback() -> None:
    prompt = append_storyboard_tool_context("Stored agent prompt.")

    assert CONVERSATION_STORYBOARD_TOOL_NAME in prompt
    assert "For every unsupported subject or physics setup" in prompt
    assert "normal conversation and teach_with_visuals path" in prompt


def test_canvas_capability_maps_to_storyboard_tool() -> None:
    tools = get_agent_tools(["canvas"])

    assert tools.count(CONVERSATION_STORYBOARD_TOOL_NAME) == 1
