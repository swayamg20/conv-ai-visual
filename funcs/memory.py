import json
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
    """
    
    def __init__(self, user_id: str, session_id: Optional[str] = None):
        self.user_id = user_id
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Initialize all layers
        self.context = ConversationContext()
        self.episodic = EpisodicMemory(user_id)
        self.semantic = SemanticMemory(user_id)
        self.profile = UserProfile(user_id)
        self.decisions = DecisionMemory(user_id, self.session_id)
        
        logger.info(f"MemoryManager initialized for user={user_id}, session={self.session_id}")
    
    def build_context(self, current_query: str, base_system_prompt: str) -> List[Dict[str, str]]:
        """
        Build full context for LLM call.
        Combines all memory layers into system prompt + conversation history.
        """
        # Start with base prompt
        prompt_parts = [base_system_prompt]
        
        # Layer 4: User profile (identity)
        profile_ctx = self.profile.get_context_string()
        if profile_ctx:
            prompt_parts.append(f"\n{profile_ctx}")
        
        # Layer 3: Semantic memory (relevant facts)
        semantic_ctx = self.semantic.get_context_string(current_query, limit=5)
        if semantic_ctx:
            prompt_parts.append(f"\n{semantic_ctx}")
        
        # Layer 2: Episodic memory (past conversations)
        episodic_ctx = self.episodic.get_context_string(limit=2)
        if episodic_ctx:
            prompt_parts.append(f"\n{episodic_ctx}")
        
        # Set enriched system prompt
        enriched_prompt = "\n".join(prompt_parts)
        self.context.set_system_prompt(enriched_prompt)
        
        # Return Layer 1: Conversation context
        return self.context.get_messages()
    
    def add_turn(self, role: str, content: str):
        """Add a message to conversation context."""
        self.context.add(role, content)
    
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
