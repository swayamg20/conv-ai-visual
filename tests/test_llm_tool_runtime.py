"""Deterministic tool-runtime policy tests without provider or database access."""

from typing import ClassVar

import pytest
from murmur.llm.tool_runtime import ToolConversationMixin

from funcs.tools import ToolCall, ToolResult


class RecordingExecutor:
    def __init__(self, events: list[tuple[str, tuple[str, ...]]]) -> None:
        self.events = events

    async def execute_batch(self, calls: list[ToolCall]) -> list[ToolResult]:
        names = tuple(call.name for call in calls)
        self.events.append(("batch", names))
        return [ToolResult(call.id, call.name) for call in calls]


class RuntimeHarness(ToolConversationMixin):
    MUTATING_TOOL_NAMES: ClassVar[set[str]] = {"mutate"}

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple[str, ...]]] = []
        self.tool_executor = RecordingExecutor(self.events)

    async def _execute_single_tool_call(self, call: ToolCall) -> ToolResult:
        self.events.append(("single", (call.name,)))
        return ToolResult(call.id, call.name)


@pytest.mark.asyncio
async def test_parallel_policy_flushes_around_mutating_tools(monkeypatch) -> None:
    monkeypatch.setattr("murmur.llm.tool_runtime.config.LLM_PARALLEL_TOOLS", True)
    runtime = RuntimeHarness()
    calls = [
        ToolCall("1", "lookup-a", {}),
        ToolCall("2", "lookup-b", {}),
        ToolCall("3", "mutate", {}),
        ToolCall("4", "lookup-c", {}),
    ]

    results, _ = await runtime._execute_tool_calls_with_policy(calls)

    assert runtime.events == [
        ("batch", ("lookup-a", "lookup-b")),
        ("single", ("mutate",)),
        ("batch", ("lookup-c",)),
    ]
    assert [result.tool_call_id for result in results] == ["1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_disabled_parallel_policy_executes_every_call_in_order(monkeypatch) -> None:
    monkeypatch.setattr("murmur.llm.tool_runtime.config.LLM_PARALLEL_TOOLS", False)
    runtime = RuntimeHarness()
    calls = [ToolCall("1", "lookup", {}), ToolCall("2", "mutate", {})]

    results, _ = await runtime._execute_tool_calls_with_policy(calls)

    assert runtime.events == [
        ("single", ("lookup",)),
        ("single", ("mutate",)),
    ]
    assert [result.content for result in results] == ["lookup", "mutate"]


def test_tool_calls_are_normalized_for_follow_up_round() -> None:
    message = ToolConversationMixin._tool_calls_to_assistant_message(
        [ToolCall("call-1", "lookup", {"query": "voice agents"})],
        content="Checking.",
    )

    assert message == {
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"query": "voice agents"}',
                },
            }
        ],
    }


def test_speech_cues_are_extracted_in_step_order() -> None:
    calls = [
        {
            "name": "teach_with_visuals",
            "arguments": {"steps": [{"say": "First."}, {"say": " Then second. "}]},
        },
        {"name": "lookup", "arguments": {"say": "ignored"}},
    ]

    assert ToolConversationMixin._extract_speech_cues(calls) == "First. Then second."
