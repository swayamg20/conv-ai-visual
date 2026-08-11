"""Orchestration across short-term and durable memory layers."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from murmur.core.config import config
from murmur.memory.context import (
    REPLY_OVERHEAD_TOKENS,
    ConversationContext,
    assemble_budgeted_system_prompt,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from murmur.memory.layers import (
    DecisionMemory,
    EpisodicMemory,
    SemanticMemory,
    UserProfile,
)
from murmur.persistence.repositories.sessions import (
    ConversationMessageRepo,
    SessionRepo,
)

logger = logging.getLogger(__name__)


class MemoryManager:
    """Coordinate conversation, profile, episodic, semantic, and decision memory."""

    def __init__(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.agent_id = agent_id

        self.context = ConversationContext()
        self.episodic = EpisodicMemory(user_id)
        self.semantic = SemanticMemory(user_id)
        self.profile = UserProfile(user_id)
        self.decisions = DecisionMemory(user_id, self.session_id)

        logger.info(
            "MemoryManager initialized for user=%s, session=%s, agent=%s",
            user_id,
            self.session_id,
            agent_id,
        )

    def load_session_messages(self, session_id: str, limit: int = 20) -> None:
        """Load persisted messages into the short-term conversation window."""
        try:
            messages = ConversationMessageRepo.get_recent(session_id, limit=limit)
            for message in messages:
                self.context.add(message.role, message.content)
            if messages:
                logger.info("Loaded %d messages from session %s", len(messages), session_id)
        except Exception as exc:
            logger.warning("Failed to load session messages: %s", exc)

    def persist_message(
        self,
        role: str,
        content: str,
        tool_calls_json: Optional[str] = None,
    ) -> None:
        """Persist one message when this manager is bound to an agent."""
        if not self.agent_id:
            logger.warning(
                "Skipping message persistence for session=%s user=%s role=%s because agent_id is missing",
                self.session_id,
                self.user_id,
                role,
            )
            return
        try:
            ConversationMessageRepo.save(
                session_id=self.session_id,
                agent_id=self.agent_id,
                user_id=self.user_id,
                role=role,
                content=content,
                tool_calls_json=tool_calls_json,
            )
            SessionRepo.increment_message_count(self.session_id)
        except Exception as exc:
            logger.warning("Failed to persist message: %s", exc)

    def get_cross_session_context(self, agent_id: str, limit: int = 3) -> str:
        """Format summaries from the user's prior sessions with this agent."""
        try:
            sessions = SessionRepo.list_by_agent(self.user_id, agent_id)
            sessions = [
                session for session in sessions if session.id != self.session_id and session.summary
            ][:limit]
            if not sessions:
                logger.info(
                    "Cross-session context unavailable for user=%s agent=%s: no prior summaries found",
                    self.user_id,
                    agent_id,
                )
                return ""

            lines = ["Previous session summaries:"]
            for session in sessions:
                date = session.updated_at.strftime("%Y-%m-%d") if session.updated_at else ""
                title = f" ({session.title})" if session.title else ""
                lines.append(f"- [{date}]{title} {session.summary}")

            context = "\n".join(lines)
            logger.info(
                "Cross-session context loaded for user=%s agent=%s: %d summaries, %d chars",
                self.user_id,
                agent_id,
                len(sessions),
                len(context),
            )
            return context
        except Exception as exc:
            logger.warning("Failed to load cross-session context: %s", exc)
            return ""

    def _build_enriched_system_prompt(
        self,
        current_query: str,
        base_system_prompt: str,
        profile_ctx: str = "",
        semantic_ctx: str = "",
        episodic_ctx: str = "",
        cross_ctx: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Build a budgeted system prompt with memory layers added by priority."""
        prompt_sections = [
            ("profile", profile_ctx),
            ("semantic", semantic_ctx),
            ("episodic", episodic_ctx),
            ("cross_session", cross_ctx),
        ]
        return assemble_budgeted_system_prompt(
            base_system_prompt=base_system_prompt,
            prompt_sections=prompt_sections,
            current_messages=self.context.messages,
            current_query=current_query,
            max_tokens=self.context.max_tokens,
        )

    def _log_context_assembly(
        self,
        *,
        mode: str,
        metadata: dict[str, Any],
        messages_before_trim: int,
        messages_after_trim: int,
    ) -> None:
        """Log injected memory layers and final budget usage."""
        available_sections = metadata.get("available_sections", [])
        selected_sections = metadata.get("selected_sections", [])
        skipped_sections = metadata.get("skipped_sections", [])
        cross_session_available = "cross_session" in available_sections
        cross_session_injected = "cross_session" in selected_sections
        final_total_tokens = (
            estimate_text_tokens(self.context.system_prompt)
            + REPLY_OVERHEAD_TOKENS
            + estimate_messages_tokens(self.context.messages)
        )
        logger.info(
            "Memory context assembled (%s) for session=%s: selected=%s skipped=%s estimated_tokens=%d final_tokens=%d/%d messages=%d->%d cross_session_available=%s cross_session_injected=%s",
            mode,
            self.session_id,
            selected_sections,
            skipped_sections,
            metadata.get("estimated_total", 0),
            final_total_tokens,
            metadata.get("budget_tokens", self.context.max_tokens),
            messages_before_trim,
            messages_after_trim,
            cross_session_available,
            cross_session_injected,
        )
        if cross_session_available and not cross_session_injected:
            logger.info(
                "Cross-session summaries were loaded but skipped by budget for session=%s (section_tokens=%d, budget=%d)",
                self.session_id,
                metadata.get("section_token_map", {}).get("cross_session", 0),
                metadata.get("budget_tokens", self.context.max_tokens),
            )
        if final_total_tokens > metadata.get("budget_tokens", self.context.max_tokens):
            logger.warning(
                "Memory context budget exceeded (%s) for session=%s: base=%d messages=%d query=%d sections=%d budget=%d final_tokens=%d",
                mode,
                self.session_id,
                metadata.get("base_tokens", 0),
                metadata.get("messages_tokens", 0),
                metadata.get("query_tokens", 0),
                metadata.get("section_tokens", 0),
                metadata.get("budget_tokens", self.context.max_tokens),
                final_total_tokens,
            )

    def build_context_sync(
        self,
        current_query: str,
        base_system_prompt: str,
    ) -> list[dict[str, str]]:
        """Build an LLM context with sequential durable-memory lookups."""
        profile_ctx = self.profile.get_context_string()
        semantic_ctx = self.semantic.get_context_string(current_query, limit=5)
        episodic_ctx = self.episodic.get_context_string(limit=2)
        cross_ctx = self.get_cross_session_context(self.agent_id) if self.agent_id else ""

        enriched_prompt, metadata = self._build_enriched_system_prompt(
            current_query=current_query,
            base_system_prompt=base_system_prompt,
            profile_ctx=profile_ctx,
            semantic_ctx=semantic_ctx,
            episodic_ctx=episodic_ctx,
            cross_ctx=cross_ctx,
        )
        self.context.set_system_prompt(enriched_prompt)
        messages_before_trim = len(self.context.messages)
        self.context.trim()
        self._log_context_assembly(
            mode="sync",
            metadata=metadata,
            messages_before_trim=messages_before_trim,
            messages_after_trim=len(self.context.messages),
        )
        return self.context.get_messages()

    async def build_context(
        self,
        current_query: str,
        base_system_prompt: str,
    ) -> list[dict[str, str]]:
        """Build an LLM context with concurrent durable-memory lookups."""
        profile_task = asyncio.to_thread(self.profile.get_context_string)
        episodic_task = asyncio.to_thread(self.episodic.get_context_string, 2)
        semantic_task = self.semantic.get_context_string_async(current_query, limit=5)

        try:
            semantic_timeout = config.MEMORY_SEMANTIC_TIMEOUT_SECS
            semantic_task = asyncio.wait_for(semantic_task, timeout=semantic_timeout)
        except Exception:
            semantic_task = asyncio.wait_for(semantic_task, timeout=1.0)

        profile_ctx, semantic_ctx, episodic_ctx = await asyncio.gather(
            profile_task,
            semantic_task,
            episodic_task,
            return_exceptions=True,
        )

        if isinstance(profile_ctx, Exception):
            logger.warning("Profile context lookup failed (non-fatal): %s", profile_ctx)
            profile_ctx = ""
        if isinstance(semantic_ctx, asyncio.TimeoutError):
            logger.warning(
                "Semantic memory timed out after %.2fs; continuing without it",
                config.MEMORY_SEMANTIC_TIMEOUT_SECS,
            )
            semantic_ctx = ""
        elif isinstance(semantic_ctx, Exception):
            logger.warning("Semantic context lookup failed (non-fatal): %s", semantic_ctx)
            semantic_ctx = ""
        if isinstance(episodic_ctx, Exception):
            logger.warning("Episodic context lookup failed (non-fatal): %s", episodic_ctx)
            episodic_ctx = ""

        cross_ctx = self.get_cross_session_context(self.agent_id) if self.agent_id else ""
        enriched_prompt, metadata = self._build_enriched_system_prompt(
            current_query=current_query,
            base_system_prompt=base_system_prompt,
            profile_ctx=profile_ctx,
            semantic_ctx=semantic_ctx,
            episodic_ctx=episodic_ctx,
            cross_ctx=cross_ctx,
        )
        self.context.set_system_prompt(enriched_prompt)
        messages_before_trim = len(self.context.messages)
        self.context.trim()
        self._log_context_assembly(
            mode="async",
            metadata=metadata,
            messages_before_trim=messages_before_trim,
            messages_after_trim=len(self.context.messages),
        )
        return self.context.get_messages()

    def add_turn(self, role: str, content: str) -> None:
        """Add a message to conversation context and persistent storage."""
        self.context.add(role, content)
        self.persist_message(role, content)

    def process_for_memory(
        self,
        user_message: str,
        assistant_response: str,
        save_semantic: bool = True,
    ) -> None:
        """Process one completed turn for short- and long-term memory."""
        self.context.add("user", user_message)
        self.context.add("assistant", assistant_response)
        self.persist_message("user", user_message)
        self.persist_message("assistant", assistant_response)

        if save_semantic and self.semantic.client:
            self.semantic.add(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_response},
                ]
            )

    def end_session(self, summary: Optional[str] = None) -> None:
        """End the conversation and optionally persist an episodic summary."""
        turn_count = len(self.context.messages) // 2
        if summary:
            self.episodic.save_summary(
                summary=summary,
                session_id=self.session_id,
                turn_count=turn_count,
            )

        self.context.clear()
        logger.info("Session %s ended, %d turns", self.session_id, turn_count)
