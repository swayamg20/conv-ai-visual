import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from mem0 import MemoryClient
from funcs.config import config
from funcs.models import (
    EpisodicMemoryRepo,
    UserProfileRepo,
    DecisionMemoryRepo,
    ToolRepo,
    SessionRepo,
    ConversationMessageRepo,
    init_db,
)

logger = logging.getLogger("memory")


class ConversationContext:
    """
    Layer 1: Short-Term Conversation Context.
    Sliding window of recent messages, kept in memory.
    """
    
    def __init__(self, max_messages: int = 20, max_tokens: int = 4000):
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.messages: List[Dict[str, str]] = []
        self.system_prompt: str = ""
    
    def set_system_prompt(self, prompt: str):
        self.system_prompt = prompt
    
    def add(self, role: str, content: str):
        self.messages.append({"role": role, "content": content})
        self._trim()
    
    def _trim(self):
        """Keep only last N messages."""
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]
    
    def get_messages(self) -> List[Dict[str, str]]:
        """Get full context with system prompt."""
        return [{"role": "system", "content": self.system_prompt}] + self.messages
    
    def clear(self):
        self.messages = []
    
    def get_recent_text(self, n: int = 5) -> str:
        """Get recent messages as text for summarization."""
        recent = self.messages[-n:] if len(self.messages) > n else self.messages
        return "\n".join([f"{m['role']}: {m['content']}" for m in recent])


class EpisodicMemory:
    """
    Layer 2: Episodic Memory.
    Stores summarized conversation sessions.
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
    
    def save_summary(
        self, 
        summary: str, 
        session_id: Optional[str] = None,
        turn_count: int = 0,
        metadata: Optional[Dict] = None
    ):
        """Save a conversation summary."""
        EpisodicMemoryRepo.save(
            user_id=self.user_id,
            summary=summary,
            session_id=session_id,
            turn_count=turn_count,
            metadata=metadata
        )
        logger.info(f"Saved episodic memory for user {self.user_id}")
    
    def get_recent(self, limit: int = 5) -> List[Dict]:
        """Get recent conversation summaries."""
        records = EpisodicMemoryRepo.get_recent(self.user_id, limit)
        return [
            {
                "summary": r.summary,
                "session_id": r.session_id,
                "turn_count": r.turn_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "metadata": r.get_meta()
            }
            for r in records
        ]
    
    def get_context_string(self, limit: int = 3) -> str:
        """Get formatted episodic context for prompt injection."""
        episodes = self.get_recent(limit)
        if not episodes:
            return ""
        
        lines = ["Previous conversations:"]
        for ep in episodes:
            date = ep.get("created_at", "")[:10] if ep.get("created_at") else ""
            lines.append(f"- [{date}] {ep['summary']}")
        return "\n".join(lines)


class SemanticMemory:
    """
    Layer 3: Semantic Memory via Mem0 Cloud.
    Vector-based retrieval of facts, preferences, entities.
    """
    
    def __init__(self, user_id: str, api_key: Optional[str] = None):
        self.user_id = user_id
        self.api_key = api_key or config.MEM0_API_KEY
        self.client: Optional[MemoryClient] = None
        
        if self.api_key:
            try:
                self.client = MemoryClient(api_key=self.api_key)
                logger.info(f"Semantic memory initialized for user {user_id}")
            except Exception as e:
                logger.warning(f"Failed to init Mem0 client: {e}")
    
    def add(self, messages: List[Dict[str, str]], metadata: Optional[Dict] = None) -> Dict:
        """Add memories from conversation. Mem0 extracts facts automatically."""
        if not self.client:
            return {"error": "Mem0 not configured"}
        
        try:
            result = self.client.add(messages, user_id=self.user_id, metadata=metadata)
            logger.info(f"Added to semantic memory: {result}")
            return result
        except Exception as e:
            logger.error(f"Semantic memory add error: {e}")
            return {"error": str(e)}
    
    def search(self, query: str, limit: int = 5) -> List[Dict]:
        """Search for relevant memories."""
        if not self.client:
            return []
        
        try:
            results = self.client.search(
                query, 
                filters={"user_id": self.user_id},
                limit=limit
            )
            if isinstance(results, list):
                return results
            return results.get("results", [])
        except Exception as e:
            logger.error(f"Semantic memory search error: {e}")
            return []
    
    def get_all(self) -> List[Dict]:
        """Get all memories for user."""
        if not self.client:
            return []
        
        try:
            results = self.client.get_all(user_id=self.user_id)
            if isinstance(results, list):
                return results
            return results.get("results", [])
        except Exception as e:
            logger.error(f"Semantic memory get_all error: {e}")
            return []
    
    def get_context_string(self, query: str, limit: int = 5) -> str:
        """Get formatted semantic context for prompt injection."""
        logger.info(f"Searching semantic memory for user={self.user_id}, query={query[:50]}...")
        memories = self.search(query, limit)
        logger.info(f"Found {len(memories)} memories")
        if not memories:
            return ""
        
        lines = ["Known facts about the user:"]
        for mem in memories:
            text = mem.get("memory", mem.get("text", str(mem)))
            lines.append(f"- {text}")
        return "\n".join(lines)

    async def get_context_string_async(self, query: str, limit: int = 5) -> str:
        """Async semantic context retrieval with sync fallback."""
        if not self.client:
            return ""

        logger.info(f"Searching semantic memory async for user={self.user_id}, query={query[:50]}...")
        try:
            if hasattr(self.client, "search_async"):
                results = await self.client.search_async(
                    query,
                    filters={"user_id": self.user_id},
                    limit=limit,
                )
                memories = results if isinstance(results, list) else results.get("results", [])
            else:
                memories = await asyncio.to_thread(self.search, query, limit)
        except Exception as e:
            logger.error(f"Semantic memory async search error: {e}")
            return ""

        if not memories:
            return ""

        lines = ["Known facts about the user:"]
        for mem in memories:
            text = mem.get("memory", mem.get("text", str(mem)))
            lines.append(f"- {text}")
        return "\n".join(lines)


class UserProfile:
    """
    Layer 4: User Profile - Canonical Identity.
    Ground truth facts, explicitly set, not inferred.
    """
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        self._ensure_exists()
    
    def _ensure_exists(self):
        """Create profile if doesn't exist."""
        UserProfileRepo.get_or_create(self.user_id)
    
    def get(self) -> Dict:
        """Get full user profile."""
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
    
    def update(self, **kwargs):
        """Update profile fields."""
        allowed = ["name", "timezone", "preferences", "facts"]
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if updates:
            UserProfileRepo.update(self.user_id, **updates)
            logger.info(f"Updated profile for {self.user_id}: {list(updates.keys())}")
    
    def add_fact(self, key: str, value: Any):
        """Add a single fact to profile."""
        profile = self.get()
        facts = profile.get("facts", {})
        facts[key] = value
        self.update(facts=facts)
    
    def add_preference(self, key: str, value: Any):
        """Add a single preference."""
        profile = self.get()
        prefs = profile.get("preferences", {})
        prefs[key] = value
        self.update(preferences=prefs)
    
    def get_context_string(self) -> str:
        """Get formatted profile for prompt injection."""
        profile = self.get()
        if not profile:
            return ""
        
        lines = []
        if profile.get("name"):
            lines.append(f"User's name: {profile['name']}")
        if profile.get("timezone"):
            lines.append(f"Timezone: {profile['timezone']}")
        
        prefs = profile.get("preferences", {})
        if prefs:
            lines.append("Preferences: " + ", ".join([f"{k}={v}" for k, v in prefs.items()]))
        
        facts = profile.get("facts", {})
        if facts:
            lines.append("Facts: " + ", ".join([f"{k}={v}" for k, v in facts.items()]))
        
        return "\n".join(lines) if lines else ""


class DecisionMemory:
    """
    Bonus: Decision Memory for Agentic Loops.
    Tracks tool usage, failures, prevents infinite loops.
    """
    
    def __init__(self, user_id: str, session_id: Optional[str] = None):
        self.user_id = user_id
        self.session_id = session_id
    
    def log_decision(
        self, 
        action: str, 
        tool_used: Optional[str] = None,
        success: bool = True,
        context: Optional[str] = None
    ):
        """Log a decision/action taken."""
        DecisionMemoryRepo.log(
            user_id=self.user_id,
            session_id=self.session_id,
            action=action,
            tool_used=tool_used,
            success=success,
            context=context
        )
    
    def get_recent_failures(self, limit: int = 5) -> List[Dict]:
        """Get recent failed actions to avoid repeating."""
        records = DecisionMemoryRepo.get_recent_failures(self.user_id, limit)
        return [
            {
                "action": r.action,
                "tool_used": r.tool_used,
                "context": r.context,
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in records
        ]
    
    def has_recent_failure(self, action: str, within_minutes: int = 5) -> bool:
        """Check if action failed recently."""
        return DecisionMemoryRepo.has_recent_failure(self.user_id, action, within_minutes)


class MemoryManager:
    """
    Unified Memory Manager - orchestrates all 4 layers.
    Now supports cross-session persistence via SessionModel and ConversationMessageModel.
    """

    def __init__(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.agent_id = agent_id

        # Initialize all layers
        self.context = ConversationContext()
        self.episodic = EpisodicMemory(user_id)
        self.semantic = SemanticMemory(user_id)
        self.profile = UserProfile(user_id)
        self.decisions = DecisionMemory(user_id, self.session_id)

        logger.info(f"MemoryManager initialized for user={user_id}, session={self.session_id}, agent={agent_id}")
    
    def load_session_messages(self, session_id: str, limit: int = 20) -> None:
        """Load persisted messages from a previous session into the conversation context."""
        try:
            messages = ConversationMessageRepo.get_recent(session_id, limit=limit)
            for msg in messages:
                self.context.add(msg.role, msg.content)
            if messages:
                logger.info(f"Loaded {len(messages)} messages from session {session_id}")
        except Exception as e:
            logger.warning(f"Failed to load session messages: {e}")

    def persist_message(self, role: str, content: str, tool_calls_json: Optional[str] = None) -> None:
        """Persist a single message to the database."""
        if not self.agent_id:
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
        except Exception as e:
            logger.warning(f"Failed to persist message: {e}")

    def get_cross_session_context(self, agent_id: str, limit: int = 3) -> str:
        """Load summaries from the last N sessions with this agent for cross-session context."""
        try:
            sessions = SessionRepo.list_by_agent(self.user_id, agent_id)
            # Exclude current session and limit
            sessions = [s for s in sessions if s.id != self.session_id and s.summary][:limit]
            if not sessions:
                return ""
            lines = ["Previous session summaries:"]
            for s in sessions:
                date = s.updated_at.strftime("%Y-%m-%d") if s.updated_at else ""
                title = f" ({s.title})" if s.title else ""
                lines.append(f"- [{date}]{title} {s.summary}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"Failed to load cross-session context: {e}")
            return ""

    def build_context_sync(self, current_query: str, base_system_prompt: str) -> List[Dict[str, str]]:
        """Build context sequentially (legacy path)."""
        prompt_parts = [base_system_prompt]

        profile_ctx = self.profile.get_context_string()
        if profile_ctx:
            prompt_parts.append(f"\n{profile_ctx}")

        semantic_ctx = self.semantic.get_context_string(current_query, limit=5)
        if semantic_ctx:
            prompt_parts.append(f"\n{semantic_ctx}")

        episodic_ctx = self.episodic.get_context_string(limit=2)
        if episodic_ctx:
            prompt_parts.append(f"\n{episodic_ctx}")

        # Cross-session context from previous sessions with this agent
        if self.agent_id:
            cross_ctx = self.get_cross_session_context(self.agent_id)
            if cross_ctx:
                prompt_parts.append(f"\n{cross_ctx}")

        enriched_prompt = "\n".join(prompt_parts)
        self.context.set_system_prompt(enriched_prompt)
        return self.context.get_messages()

    async def build_context(self, current_query: str, base_system_prompt: str) -> List[Dict[str, str]]:
        """
        Build full context for LLM call.
        Combines all memory layers into system prompt + conversation history.
        """
        # Start with base prompt
        prompt_parts = [base_system_prompt]

        # Run memory lookups concurrently to cut pre-LLM latency.
        profile_task = asyncio.to_thread(self.profile.get_context_string)
        episodic_task = asyncio.to_thread(self.episodic.get_context_string, 2)
        semantic_task = self.semantic.get_context_string_async(current_query, limit=5)

        try:
            semantic_timeout = config.MEMORY_SEMANTIC_TIMEOUT_SECS
            semantic_task = asyncio.wait_for(semantic_task, timeout=semantic_timeout)
        except Exception:
            # Fallback in case config value is invalid.
            semantic_task = asyncio.wait_for(semantic_task, timeout=1.0)

        profile_ctx, semantic_ctx, episodic_ctx = await asyncio.gather(
            profile_task,
            semantic_task,
            episodic_task,
            return_exceptions=True,
        )

        if isinstance(profile_ctx, Exception):
            logger.warning(f"Profile context lookup failed (non-fatal): {profile_ctx}")
            profile_ctx = ""
        if isinstance(semantic_ctx, asyncio.TimeoutError):
            logger.warning(
                "Semantic memory timed out after %.2fs; continuing without it",
                config.MEMORY_SEMANTIC_TIMEOUT_SECS,
            )
            semantic_ctx = ""
        elif isinstance(semantic_ctx, Exception):
            logger.warning(f"Semantic context lookup failed (non-fatal): {semantic_ctx}")
            semantic_ctx = ""
        if isinstance(episodic_ctx, Exception):
            logger.warning(f"Episodic context lookup failed (non-fatal): {episodic_ctx}")
            episodic_ctx = ""

        if profile_ctx:
            prompt_parts.append(f"\n{profile_ctx}")
        if semantic_ctx:
            prompt_parts.append(f"\n{semantic_ctx}")
        if episodic_ctx:
            prompt_parts.append(f"\n{episodic_ctx}")

        # Cross-session context from previous sessions with this agent
        if self.agent_id:
            cross_ctx = self.get_cross_session_context(self.agent_id)
            if cross_ctx:
                prompt_parts.append(f"\n{cross_ctx}")

        # Set enriched system prompt
        enriched_prompt = "\n".join(prompt_parts)
        self.context.set_system_prompt(enriched_prompt)

        # Return Layer 1: Conversation context
        return self.context.get_messages()
    
    def add_turn(self, role: str, content: str):
        """Add a message to conversation context and persist to DB."""
        self.context.add(role, content)
        self.persist_message(role, content)
    
    def process_for_memory(
        self,
        user_message: str,
        assistant_response: str,
        save_semantic: bool = True
    ):
        """
        Post-turn memory processing.
        Decides what to save to long-term memory.
        """
        # Add to short-term context
        self.context.add("user", user_message)
        self.context.add("assistant", assistant_response)

        # Persist to DB for cross-session memory
        self.persist_message("user", user_message)
        self.persist_message("assistant", assistant_response)

        # Save to semantic memory (Mem0 extracts facts)
        if save_semantic and self.semantic.client:
            self.semantic.add([
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_response}
            ])
    
    def end_session(self, summary: Optional[str] = None):
        """
        End conversation session.
        Saves episodic summary if provided.
        """
        turn_count = len(self.context.messages) // 2
        
        if summary:
            self.episodic.save_summary(
                summary=summary,
                session_id=self.session_id,
                turn_count=turn_count
            )
        
        self.context.clear()
        logger.info(f"Session {self.session_id} ended, {turn_count} turns")
