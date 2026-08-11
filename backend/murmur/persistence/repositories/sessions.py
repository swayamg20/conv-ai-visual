"""Repositories for conversations and tutoring mastery signals."""

from sqlalchemy import func
from sqlmodel import select

from murmur.persistence.clock import utc_now
from murmur.persistence.database import get_session
from murmur.persistence.models import (
    ConversationMessageModel,
    SessionModel,
    TopicMasteryModel,
)


class SessionRepo:
    """Persist and retrieve chat sessions."""

    @staticmethod
    def create(user_id: str, agent_id: str) -> SessionModel:
        with get_session() as session:
            record = SessionModel(user_id=user_id, agent_id=agent_id)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None:
        with get_session() as session:
            return session.get(SessionModel, session_id)

    @staticmethod
    def list_by_agent(user_id: str, agent_id: str) -> list[SessionModel]:
        with get_session() as session:
            statement = (
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .where(SessionModel.agent_id == agent_id)
                .order_by(SessionModel.updated_at.desc())
            )
            return list(session.exec(statement).all())

    @staticmethod
    def list_by_user(user_id: str, agent_id: str | None = None) -> list[SessionModel]:
        with get_session() as session:
            statement = (
                select(SessionModel)
                .where(SessionModel.user_id == user_id)
                .order_by(SessionModel.updated_at.desc())
            )
            if agent_id:
                statement = statement.where(SessionModel.agent_id == agent_id)
            return list(session.exec(statement).all())

    @staticmethod
    def update_summary(session_id: str, summary: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.summary = summary
                record.updated_at = utc_now()
                session.add(record)
                session.commit()

    @staticmethod
    def update_title(session_id: str, title: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.title = title
                record.updated_at = utc_now()
                session.add(record)
                session.commit()

    @staticmethod
    def increment_message_count(session_id: str) -> None:
        with get_session() as session:
            record = session.get(SessionModel, session_id)
            if record:
                record.message_count += 1
                record.updated_at = utc_now()
                session.add(record)
                session.commit()


class ConversationMessageRepo:
    """Persist and retrieve messages within a session."""

    @staticmethod
    def save(
        session_id: str,
        agent_id: str,
        user_id: str,
        role: str,
        content: str,
        tool_calls_json: str | None = None,
    ) -> ConversationMessageModel:
        with get_session() as session:
            record = ConversationMessageModel(
                session_id=session_id,
                agent_id=agent_id,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls_json=tool_calls_json,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(session_id: str, limit: int = 20) -> list[ConversationMessageModel]:
        with get_session() as session:
            statement = (
                select(ConversationMessageModel)
                .where(ConversationMessageModel.session_id == session_id)
                .order_by(ConversationMessageModel.created_at.desc())
                .limit(limit)
            )
            results = list(session.exec(statement).all())
            results.reverse()
            return results

    @staticmethod
    def count_by_session(session_id: str) -> int:
        with get_session() as session:
            statement = select(func.count(ConversationMessageModel.id)).where(
                ConversationMessageModel.session_id == session_id
            )
            return session.exec(statement).one() or 0


class TopicMasteryRepo:
    """Persist and aggregate tutoring mastery signals."""

    @staticmethod
    def save_batch(entries: list[TopicMasteryModel]) -> None:
        with get_session() as session:
            for entry in entries:
                session.add(entry)
            session.commit()

    @staticmethod
    def get_by_agent(user_id: str, agent_id: str) -> list[TopicMasteryModel]:
        with get_session() as session:
            statement = (
                select(TopicMasteryModel)
                .where(
                    TopicMasteryModel.user_id == user_id,
                    TopicMasteryModel.agent_id == agent_id,
                )
                .order_by(TopicMasteryModel.created_at.desc())
            )
            return list(session.exec(statement).all())

    @classmethod
    def get_summary(cls, user_id: str, agent_id: str) -> dict:
        """Aggregate the latest topic state and chapter counts."""
        entries = cls.get_by_agent(user_id, agent_id)
        if not entries:
            return {"topics": [], "chapters": []}

        topic_latest: dict[str, dict] = {}
        topic_sessions: dict[str, set] = {}
        for entry in entries:
            topic_sessions.setdefault(entry.topic, set()).add(entry.session_id)
            if entry.topic not in topic_latest:
                topic_latest[entry.topic] = {
                    "topic": entry.topic,
                    "chapter": entry.chapter,
                    "signal_type": entry.signal_type,
                }

        topics = [
            {**info, "session_count": len(topic_sessions[topic])}
            for topic, info in topic_latest.items()
        ]

        chapter_stats: dict[str, dict[str, int]] = {}
        for topic in topics:
            chapter = topic.get("chapter") or "Uncategorized"
            stats = chapter_stats.setdefault(
                chapter,
                {"understood": 0, "struggled": 0, "unclear": 0},
            )
            signal = topic["signal_type"]
            if signal in stats:
                stats[signal] += 1

        chapters = [{"name": name, **counts} for name, counts in chapter_stats.items()]
        return {"topics": topics, "chapters": chapters}

    @classmethod
    def get_tutoring_context(
        cls,
        user_id: str,
        agent_id: str,
        max_topics: int = 5,
        max_chapters: int = 5,
    ) -> dict:
        """Return compact, deterministic tutoring context for a prompt."""
        summary = cls.get_summary(user_id, agent_id)
        topics = summary.get("topics", [])
        chapters = summary.get("chapters", [])
        if not topics and not chapters:
            return {"topics": [], "chapters": [], "prompt": ""}

        signal_order = {"struggled": 0, "unclear": 1, "understood": 2}
        ordered_topics = sorted(
            topics,
            key=lambda item: (
                signal_order.get(item.get("signal_type"), 99),
                -int(item.get("session_count") or 0),
                (item.get("topic") or "").lower(),
            ),
        )[:max_topics]
        ordered_chapters = sorted(
            chapters,
            key=lambda item: (
                -(item.get("struggled") or 0),
                -(item.get("unclear") or 0),
                -(item.get("understood") or 0),
                (item.get("name") or "").lower(),
            ),
        )[:max_chapters]

        topic_lines = [
            (
                f"{item.get('topic')} "
                f"[{item.get('signal_type', 'unknown')}, "
                f"sessions={item.get('session_count', 0)}, "
                f"chapter={item.get('chapter') or 'Uncategorized'}]"
            )
            for item in ordered_topics
        ]
        chapter_lines = [
            (
                f"{item.get('name')} "
                f"[U={item.get('understood', 0)}, "
                f"S={item.get('struggled', 0)}, "
                f"C={item.get('unclear', 0)}]"
            )
            for item in ordered_chapters
        ]
        prompt_parts: list[str] = []
        if topic_lines:
            prompt_parts.append("Recent topics: " + "; ".join(topic_lines))
        if chapter_lines:
            prompt_parts.append("Chapter balance: " + "; ".join(chapter_lines))

        return {
            "topics": ordered_topics,
            "chapters": ordered_chapters,
            "prompt": "\n".join(prompt_parts),
        }
