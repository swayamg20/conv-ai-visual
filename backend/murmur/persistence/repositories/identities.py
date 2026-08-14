"""Repositories for users and their agents."""

import hashlib
import logging

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from murmur.persistence.clock import utc_now
from murmur.persistence.database import get_session
from murmur.persistence.models import AgentModel, UserModel

logger = logging.getLogger(__name__)

_MAX_FIREBASE_UID_LENGTH = 128
_UNVERIFIED_FIREBASE_EMAIL_DOMAIN = "murmur.invalid"


class UserRepo:
    """Persist and retrieve Firebase-backed user accounts."""

    @staticmethod
    def get_or_create_exact_uid(uid: str, name: str | None = None) -> UserModel:
        """Return an exact Firebase UID without consulting or mutating email identity."""

        _validate_firebase_uid(uid)
        placeholder_email = _unverified_firebase_email(uid)
        with get_session() as session:
            user = session.get(UserModel, uid)
            if user is not None:
                return user

            user = UserModel(
                id=uid,
                email=placeholder_email,
                name=name,
                password_hash="__firebase_auth__",
            )
            session.add(user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrently_created = session.get(UserModel, uid)
                if concurrently_created is not None:
                    return concurrently_created
                raise
            session.refresh(user)
            return user

    @staticmethod
    def get_or_create(uid: str, email: str, name: str | None = None) -> UserModel:
        with get_session() as session:
            user = session.get(UserModel, uid)
            if user:
                updated = False
                if email and user.email != email:
                    user.email = email
                    updated = True
                if name != user.name:
                    user.name = name
                    updated = True
                if updated:
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                return user

            if email:
                existing_by_email = session.exec(
                    select(UserModel).where(UserModel.email == email)
                ).first()
                if existing_by_email:
                    if name != existing_by_email.name:
                        existing_by_email.name = name
                        session.add(existing_by_email)
                        session.commit()
                        session.refresh(existing_by_email)
                    logger.info(
                        "Reusing legacy user row %s for Firebase login %s based on email match",
                        existing_by_email.id,
                        uid,
                    )
                    return existing_by_email

            user = UserModel(
                id=uid,
                email=email,
                name=name,
                password_hash="__firebase_auth__",
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    @staticmethod
    def get_by_email(email: str) -> UserModel | None:
        with get_session() as session:
            return session.exec(select(UserModel).where(UserModel.email == email)).first()

    @staticmethod
    def get_by_id(user_id: str) -> UserModel | None:
        with get_session() as session:
            return session.get(UserModel, user_id)


def _validate_firebase_uid(uid: str) -> None:
    if (
        not isinstance(uid, str)
        or not uid
        or len(uid) > _MAX_FIREBASE_UID_LENGTH
        or uid != uid.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in uid)
    ):
        raise ValueError("Firebase UID is invalid")


def _unverified_firebase_email(uid: str) -> str:
    digest = hashlib.sha256(uid.encode("utf-8")).hexdigest()
    return f"firebase-unverified-{digest}@{_UNVERIFIED_FIREBASE_EMAIL_DOMAIN}"


class AgentRepo:
    """Persist and retrieve user-created agents."""

    @staticmethod
    def create(**kwargs) -> AgentModel:
        with get_session() as session:
            agent = AgentModel(**kwargs)
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None:
        with get_session() as session:
            return session.get(AgentModel, agent_id)

    @staticmethod
    def list_by_user(user_id: str) -> list[AgentModel]:
        with get_session() as session:
            statement = (
                select(AgentModel)
                .where(AgentModel.user_id == user_id)
                .order_by(AgentModel.created_at.desc())
            )
            return list(session.exec(statement).all())

    @staticmethod
    def update(agent_id: str, **kwargs) -> AgentModel | None:
        with get_session() as session:
            agent = session.get(AgentModel, agent_id)
            if not agent:
                return None
            for key, value in kwargs.items():
                if hasattr(agent, key):
                    setattr(agent, key, value)
            agent.updated_at = utc_now()
            session.add(agent)
            session.commit()
            session.refresh(agent)
            return agent

    @staticmethod
    def delete(agent_id: str) -> bool:
        with get_session() as session:
            agent = session.get(AgentModel, agent_id)
            if not agent:
                return False
            session.delete(agent)
            session.commit()
            return True

    @staticmethod
    def set_default(agent_id: str, user_id: str) -> None:
        with get_session() as session:
            statement = (
                select(AgentModel)
                .where(AgentModel.user_id == user_id)
                .where(AgentModel.is_default.is_(True))
            )
            for agent in session.exec(statement).all():
                agent.is_default = False
                session.add(agent)

            target = session.get(AgentModel, agent_id)
            if target and target.user_id == user_id:
                target.is_default = True
                target.updated_at = utc_now()
                session.add(target)
            session.commit()
