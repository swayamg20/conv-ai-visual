"""Durable episodic, semantic, profile, and decision memory adapters."""

import asyncio
import logging
from typing import Any, Optional

from mem0 import MemoryClient

from murmur.core.config import config
from murmur.persistence.repositories.memory import (
    DecisionMemoryRepo,
    EpisodicMemoryRepo,
    UserProfileRepo,
)

logger = logging.getLogger(__name__)


class EpisodicMemory:
    """Store and format summarized conversation sessions."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def save_summary(
        self,
        summary: str,
        session_id: Optional[str] = None,
        turn_count: int = 0,
        metadata: Optional[dict] = None,
    ) -> None:
        EpisodicMemoryRepo.save(
            user_id=self.user_id,
            summary=summary,
            session_id=session_id,
            turn_count=turn_count,
            metadata=metadata,
        )
        logger.info("Saved episodic memory for user %s", self.user_id)

    def get_recent(self, limit: int = 5) -> list[dict]:
        records = EpisodicMemoryRepo.get_recent(self.user_id, limit)
        return [
            {
                "summary": record.summary,
                "session_id": record.session_id,
                "turn_count": record.turn_count,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "metadata": record.get_meta(),
            }
            for record in records
        ]

    def get_context_string(self, limit: int = 3) -> str:
        episodes = self.get_recent(limit)
        if not episodes:
            return ""

        lines = ["Previous conversations:"]
        for episode in episodes:
            created_at = episode.get("created_at")
            date = created_at[:10] if created_at else ""
            lines.append(f"- [{date}] {episode['summary']}")
        return "\n".join(lines)


class SemanticMemory:
    """Store and retrieve user facts through Mem0 Cloud."""

    def __init__(self, user_id: str, api_key: Optional[str] = None):
        self.user_id = user_id
        self.api_key = api_key or config.MEM0_API_KEY
        self.client: Optional[MemoryClient] = None

        if self.api_key:
            try:
                self.client = MemoryClient(api_key=self.api_key)
                logger.info("Semantic memory initialized for user %s", user_id)
            except Exception as exc:
                logger.warning("Failed to initialize Mem0 client: %s", exc)

    def add(
        self,
        messages: list[dict[str, str]],
        metadata: Optional[dict] = None,
    ) -> dict:
        """Let Mem0 extract durable facts from a conversation."""
        if not self.client:
            return {"error": "Mem0 not configured"}

        try:
            result = self.client.add(messages, user_id=self.user_id, metadata=metadata)
            logger.info("Added to semantic memory: %s", result)
            return result
        except Exception as exc:
            logger.error("Semantic memory add error: %s", exc)
            return {"error": str(exc)}

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not self.client:
            return []

        try:
            results = self.client.search(
                query,
                filters={"user_id": self.user_id},
                top_k=limit,
            )
            if isinstance(results, list):
                return results
            return results.get("results", [])
        except Exception as exc:
            logger.error("Semantic memory search error: %s", exc)
            return []

    def get_all(self) -> list[dict]:
        if not self.client:
            return []

        try:
            results = self.client.get_all(filters={"user_id": self.user_id})
            if isinstance(results, list):
                return results
            return results.get("results", [])
        except Exception as exc:
            logger.error("Semantic memory get_all error: %s", exc)
            return []

    @staticmethod
    def _format_context(memories: list[dict]) -> str:
        if not memories:
            return ""
        lines = ["Known facts about the user:"]
        for memory in memories:
            text = memory.get("memory", memory.get("text", str(memory)))
            lines.append(f"- {text}")
        return "\n".join(lines)

    def get_context_string(self, query: str, limit: int = 5) -> str:
        logger.info(
            "Searching semantic memory for user=%s, query=%s...",
            self.user_id,
            query[:50],
        )
        memories = self.search(query, limit)
        logger.info("Found %d memories", len(memories))
        return self._format_context(memories)

    async def get_context_string_async(self, query: str, limit: int = 5) -> str:
        """Retrieve semantic context asynchronously, adapting sync clients."""
        if not self.client:
            return ""

        logger.info(
            "Searching semantic memory async for user=%s, query=%s...",
            self.user_id,
            query[:50],
        )
        try:
            if hasattr(self.client, "search_async"):
                results = await self.client.search_async(
                    query,
                    filters={"user_id": self.user_id},
                    top_k=limit,
                )
                memories = results if isinstance(results, list) else results.get("results", [])
            else:
                memories = await asyncio.to_thread(self.search, query, limit)
        except Exception as exc:
            logger.error("Semantic memory async search error: %s", exc)
            return ""

        return self._format_context(memories)


class UserProfile:
    """Canonical user facts that are explicitly set rather than inferred."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        UserProfileRepo.get_or_create(self.user_id)

    def get(self) -> dict:
        profile = UserProfileRepo.get(self.user_id)
        if not profile:
            return {}
        return {
            "user_id": profile.user_id,
            "name": profile.name,
            "timezone": profile.timezone,
            "preferences": profile.preferences,
            "facts": profile.facts,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }

    def update(self, **kwargs: Any) -> None:
        allowed = {"name", "timezone", "preferences", "facts"}
        updates = {key: value for key, value in kwargs.items() if key in allowed}
        if updates:
            UserProfileRepo.update(self.user_id, **updates)
            logger.info("Updated profile for %s: %s", self.user_id, list(updates))

    def add_fact(self, key: str, value: Any) -> None:
        facts = self.get().get("facts", {})
        facts[key] = value
        self.update(facts=facts)

    def add_preference(self, key: str, value: Any) -> None:
        preferences = self.get().get("preferences", {})
        preferences[key] = value
        self.update(preferences=preferences)

    def get_context_string(self) -> str:
        profile = self.get()
        if not profile:
            return ""

        lines = []
        if profile.get("name"):
            lines.append(f"User's name: {profile['name']}")
        if profile.get("timezone"):
            lines.append(f"Timezone: {profile['timezone']}")

        preferences = profile.get("preferences", {})
        if preferences:
            lines.append(
                "Preferences: " + ", ".join(f"{key}={value}" for key, value in preferences.items())
            )

        facts = profile.get("facts", {})
        if facts:
            lines.append("Facts: " + ", ".join(f"{key}={value}" for key, value in facts.items()))

        return "\n".join(lines)


class DecisionMemory:
    """Track recent tool decisions and failures for agentic loops."""

    def __init__(self, user_id: str, session_id: Optional[str] = None):
        self.user_id = user_id
        self.session_id = session_id

    def log_decision(
        self,
        action: str,
        tool_used: Optional[str] = None,
        success: bool = True,
        context: Optional[str] = None,
    ) -> None:
        DecisionMemoryRepo.log(
            user_id=self.user_id,
            session_id=self.session_id,
            action=action,
            tool_used=tool_used,
            success=success,
            context=context,
        )

    def get_recent_failures(self, limit: int = 5) -> list[dict]:
        records = DecisionMemoryRepo.get_recent_failures(self.user_id, limit)
        return [
            {
                "action": record.action,
                "tool_used": record.tool_used,
                "context": record.context,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
            for record in records
        ]

    def has_recent_failure(self, action: str, within_minutes: int = 5) -> bool:
        return DecisionMemoryRepo.has_recent_failure(
            self.user_id,
            action,
            within_minutes,
        )
