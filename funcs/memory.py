"""
4-Layer Memory Architecture for Voice AI Agent.

Layer 1: Conversation Context (Short-Term) - sliding window, in-memory
Layer 2: Episodic Memory - conversation summaries, SQLite
Layer 3: Semantic Memory - vector search via Mem0 Cloud
Layer 4: User Profile - canonical facts, SQLite
"""
import os
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from mem0 import MemoryClient
from funcs.config import config

logger = logging.getLogger("memory")

# Database path
MEMORY_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "memory.db")


def get_db_connection() -> sqlite3.Connection:
    """Get SQLite connection with row factory."""
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Episodic Memory - conversation summaries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodic_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT,
            summary TEXT NOT NULL,
            turn_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodic_user ON episodic_memory(user_id)")
    
    # User Profile - canonical identity facts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            timezone TEXT,
            preferences TEXT,
            facts TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Decision Memory - for agentic loops
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS decision_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT,
            action TEXT NOT NULL,
            tool_used TEXT,
            success BOOLEAN,
            context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_decision_user ON decision_memory(user_id)")
    
    conn.commit()
    conn.close()
    logger.info("Memory database initialized")


# Initialize DB on module load
init_db()


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
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO episodic_memory (user_id, session_id, summary, turn_count, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (
            self.user_id, 
            session_id, 
            summary, 
            turn_count,
            json.dumps(metadata) if metadata else None
        ))
        conn.commit()
        conn.close()
        logger.info(f"Saved episodic memory for user {self.user_id}")
    
    def get_recent(self, limit: int = 5) -> List[Dict]:
        """Get recent conversation summaries."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT summary, session_id, turn_count, created_at, metadata
            FROM episodic_memory
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (self.user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def get_context_string(self, limit: int = 3) -> str:
        """Get formatted episodic context for prompt injection."""
        episodes = self.get_recent(limit)
        if not episodes:
            return ""
        
        lines = ["Previous conversations:"]
        for ep in episodes:
            date = ep.get("created_at", "")[:10]
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
            results = self.client.search(query, user_id=self.user_id, limit=limit)
            # Handle both list and dict response formats
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
        memories = self.search(query, limit)
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
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO user_profile (user_id, preferences, facts)
            VALUES (?, '{}', '{}')
        """, (self.user_id,))
        conn.commit()
        conn.close()
    
    def get(self) -> Dict:
        """Get full user profile."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_profile WHERE user_id = ?", (self.user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            profile = dict(row)
            profile["preferences"] = json.loads(profile.get("preferences") or "{}")
            profile["facts"] = json.loads(profile.get("facts") or "{}")
            return profile
        return {}
    
    def update(self, **kwargs):
        """Update profile fields."""
        allowed = ["name", "timezone", "preferences", "facts"]
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        
        if not updates:
            return
        
        # JSON encode dict fields
        if "preferences" in updates and isinstance(updates["preferences"], dict):
            updates["preferences"] = json.dumps(updates["preferences"])
        if "facts" in updates and isinstance(updates["facts"], dict):
            updates["facts"] = json.dumps(updates["facts"])
        
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [self.user_id]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE user_profile 
            SET {set_clause}, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, values)
        conn.commit()
        conn.close()
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
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO decision_memory (user_id, session_id, action, tool_used, success, context)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (self.user_id, self.session_id, action, tool_used, success, context))
        conn.commit()
        conn.close()
    
    def get_recent_failures(self, limit: int = 5) -> List[Dict]:
        """Get recent failed actions to avoid repeating."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT action, tool_used, context, created_at
            FROM decision_memory
            WHERE user_id = ? AND success = 0
            ORDER BY created_at DESC
            LIMIT ?
        """, (self.user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    def has_recent_failure(self, action: str, within_minutes: int = 5) -> bool:
        """Check if action failed recently."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM decision_memory
            WHERE user_id = ? AND action = ? AND success = 0
            AND created_at > datetime('now', ?)
        """, (self.user_id, action, f"-{within_minutes} minutes"))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0


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
