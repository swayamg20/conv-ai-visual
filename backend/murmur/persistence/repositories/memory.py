"""Repositories for long-lived user and decision memory."""

import json

from sqlmodel import select

from murmur.persistence.clock import utc_now
from murmur.persistence.database import get_session
from murmur.persistence.models import (
    DecisionMemoryModel,
    EpisodicMemoryModel,
    UserProfileModel,
)


class EpisodicMemoryRepo:
    """Persist and retrieve conversation summaries."""

    @staticmethod
    def save(
        user_id: str,
        summary: str,
        session_id: str | None = None,
        turn_count: int = 0,
        metadata: dict | None = None,
    ) -> EpisodicMemoryModel:
        with get_session() as session:
            record = EpisodicMemoryModel(
                user_id=user_id,
                session_id=session_id,
                summary=summary,
                turn_count=turn_count,
                meta_json=json.dumps(metadata) if metadata else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(user_id: str, limit: int = 5) -> list[EpisodicMemoryModel]:
        with get_session() as session:
            statement = (
                select(EpisodicMemoryModel)
                .where(EpisodicMemoryModel.user_id == user_id)
                .order_by(EpisodicMemoryModel.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(statement).all())


class UserProfileRepo:
    """Persist and retrieve canonical user profiles."""

    @staticmethod
    def get_or_create(user_id: str) -> UserProfileModel:
        with get_session() as session:
            profile = session.get(UserProfileModel, user_id)
            if not profile:
                profile = UserProfileModel(user_id=user_id)
                session.add(profile)
                session.commit()
                session.refresh(profile)
            return profile

    @staticmethod
    def get(user_id: str) -> UserProfileModel | None:
        with get_session() as session:
            return session.get(UserProfileModel, user_id)

    @staticmethod
    def update(user_id: str, **kwargs) -> UserProfileModel | None:
        with get_session() as session:
            profile = session.get(UserProfileModel, user_id)
            if not profile:
                return None

            for key, value in kwargs.items():
                if key == "preferences":
                    profile.preferences_json = json.dumps(value)
                elif key == "facts":
                    profile.facts_json = json.dumps(value)
                elif hasattr(profile, key):
                    setattr(profile, key, value)

            profile.updated_at = utc_now()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile


class DecisionMemoryRepo:
    """Persist and query agentic decisions."""

    @staticmethod
    def log(
        user_id: str,
        action: str,
        session_id: str | None = None,
        tool_used: str | None = None,
        success: bool = True,
        context: str | None = None,
    ) -> DecisionMemoryModel:
        with get_session() as session:
            record = DecisionMemoryModel(
                user_id=user_id,
                session_id=session_id,
                action=action,
                tool_used=tool_used,
                success=success,
                context=context,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent_failures(user_id: str, limit: int = 5) -> list[DecisionMemoryModel]:
        with get_session() as session:
            statement = (
                select(DecisionMemoryModel)
                .where(DecisionMemoryModel.user_id == user_id)
                .where(DecisionMemoryModel.success.is_(False))
                .order_by(DecisionMemoryModel.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(statement).all())

    @staticmethod
    def has_recent_failure(user_id: str, action: str, within_minutes: int = 5) -> bool:
        with get_session() as session:
            statement = (
                select(DecisionMemoryModel)
                .where(DecisionMemoryModel.user_id == user_id)
                .where(DecisionMemoryModel.action == action)
                .where(DecisionMemoryModel.success.is_(False))
                .order_by(DecisionMemoryModel.created_at.desc())
                .limit(1)
            )
            result = session.exec(statement).first()
            if not result:
                return False
            age_minutes = (utc_now() - result.created_at).total_seconds() / 60
            return age_minutes <= within_minutes
