from murmur.memory.context import (
    REPLY_OVERHEAD_TOKENS,
    ConversationContext,
    assemble_budgeted_system_prompt,
    estimate_message_tokens,
    estimate_text_tokens,
)
from murmur.memory.manager import MemoryManager


def test_conversation_context_keeps_only_newest_messages() -> None:
    context = ConversationContext(max_messages=2, max_tokens=10_000)

    context.add("user", "first")
    context.add("assistant", "second")
    context.add("user", "third")

    assert context.messages == [
        {"role": "assistant", "content": "second"},
        {"role": "user", "content": "third"},
    ]


def test_prompt_sections_follow_priority_without_exceeding_budget() -> None:
    base_prompt = "base"
    query = "question"
    profile = "profile context"
    semantic = "semantic context"
    fixed_tokens = (
        estimate_text_tokens(base_prompt)
        + estimate_message_tokens({"role": "user", "content": query})
        + REPLY_OVERHEAD_TOKENS
    )
    profile_tokens = estimate_text_tokens(f"\n{profile}")

    prompt, metadata = assemble_budgeted_system_prompt(
        base_system_prompt=base_prompt,
        prompt_sections=[("profile", profile), ("semantic", semantic)],
        current_messages=[],
        current_query=query,
        max_tokens=fixed_tokens + profile_tokens,
    )

    assert prompt == f"{base_prompt}\n\n{profile}"
    assert metadata["selected_sections"] == ["profile"]
    assert metadata["skipped_sections"] == ["semantic"]
    assert metadata["estimated_total"] <= metadata["budget_tokens"]
    assert metadata["budget_exceeded"] is False


def test_memory_manager_declares_each_layer_once_in_priority_order() -> None:
    manager = MemoryManager.__new__(MemoryManager)
    manager.context = ConversationContext(max_tokens=10_000)

    _prompt, metadata = manager._build_enriched_system_prompt(
        current_query="question",
        base_system_prompt="base",
        profile_ctx="profile",
        semantic_ctx="semantic",
        episodic_ctx="episodic",
        cross_ctx="cross-session",
    )

    assert metadata["available_sections"] == [
        "profile",
        "semantic",
        "episodic",
        "cross_session",
    ]
    assert metadata["selected_sections"] == metadata["available_sections"]
