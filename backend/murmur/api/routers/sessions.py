"""Lifecycle and read routes for persistent tutoring sessions."""

import json
import logging
import re
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from funcs.llm_clients import create_llm_client
from murmur.api.dependencies import (
    ChatServiceDependency,
    CurrentUserDependency,
    require_owned_agent,
)
from murmur.api.errors import ApiError
from murmur.api.schemas import CreateSessionRequest
from murmur.persistence.models import ConversationMessageModel, SessionModel, TopicMasteryModel
from murmur.persistence.repositories.sessions import (
    ConversationMessageRepo,
    SessionRepo,
    TopicMasteryRepo,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


def _serialize_session(session: SessionModel) -> dict[str, Any]:
    return {
        "id": session.id,
        "agent_id": session.agent_id,
        "title": session.title,
        "summary": session.summary,
        "message_count": session.message_count,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


def _serialize_message(message: ConversationMessageModel) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }


@router.post("")
async def create_session(
    body: CreateSessionRequest,
    user: CurrentUserDependency,
) -> JSONResponse:
    agent = require_owned_agent(body.agent_id, user)
    session = SessionRepo.create(user_id=user["id"], agent_id=agent.id)
    payload = _serialize_session(session)
    payload["user_id"] = session.user_id
    payload.pop("updated_at")
    return JSONResponse(payload)


@router.get("")
async def list_sessions(
    user: CurrentUserDependency,
    agent_id: str | None = None,
) -> JSONResponse:
    sessions = SessionRepo.list_by_user(user["id"], agent_id=agent_id)
    return JSONResponse([_serialize_session(session) for session in sessions])


@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    user: CurrentUserDependency,
) -> JSONResponse:
    session = SessionRepo.get_by_id(session_id)
    if not session:
        raise ApiError(404, "Session not found")
    if session.user_id != user["id"]:
        raise ApiError(403, "Forbidden")

    payload = _serialize_session(session)
    payload["messages"] = [
        _serialize_message(message)
        for message in ConversationMessageRepo.get_recent(session_id, limit=50)
    ]
    return JSONResponse(payload)


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    user: CurrentUserDependency,
    chat_service: ChatServiceDependency,
) -> JSONResponse:
    """Summarize a completed session and persist tutoring mastery signals."""
    session = SessionRepo.get_by_id(session_id)
    if not session:
        raise ApiError(404, "Session not found")
    if session.user_id != user["id"]:
        raise ApiError(403, "Forbidden")

    messages = ConversationMessageRepo.get_recent(session_id, limit=30)
    if not messages:
        return JSONResponse({"id": session_id, "summary": None, "status": "ended"})

    conversation = "\n".join(f"{message.role}: {message.content}" for message in messages)
    try:
        summary = await create_llm_client(provider="groq").complete(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize this tutoring session in 3-4 concise sentences for a future "
                        "tutor. Include: the main topics covered, what the student understood "
                        "well, where they struggled or showed misconceptions, and the best next "
                        "thing to revisit in a follow-up session. Avoid generic praise."
                    ),
                },
                {"role": "user", "content": conversation},
            ],
            temperature=0.3,
            max_tokens=150,
        )
    except Exception as exc:
        logger.warning("Failed to generate session summary via LLM: %s", exc)
        summary = None

    if summary:
        SessionRepo.update_summary(session_id, summary)

    mastery_entries: list[TopicMasteryModel] = []
    try:
        mastery_response = await create_llm_client(provider="groq").complete(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "From this tutoring conversation, extract the durable tutoring signals. "
                        "Identify the important concepts/topics actually discussed and classify "
                        "the student's understanding. Return a JSON array: "
                        '[{"topic": "...", "chapter": "...", '
                        '"signal_type": "understood|struggled|unclear", "details": "..."}]. '
                        "Only include topics that were actually discussed. Be specific about topic "
                        "names, prefer chapter names when obvious, and use `details` for a short "
                        "evidence-based note about what the student got right, got wrong, or still "
                        "needs help with. If uncertain, use `unclear`. Return ONLY valid JSON, no "
                        "markdown."
                    ),
                },
                {"role": "user", "content": conversation},
            ],
            temperature=0.2,
            max_tokens=500,
        )
        cleaned = re.sub(r"^```(?:json)?\s*", "", mastery_response.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and "topic" in item and "signal_type" in item:
                    mastery_entries.append(
                        TopicMasteryModel(
                            user_id=user["id"],
                            agent_id=session.agent_id,
                            session_id=session_id,
                            topic=item["topic"],
                            chapter=item.get("chapter"),
                            signal_type=item["signal_type"],
                            details=item.get("details"),
                        )
                    )
        if mastery_entries:
            TopicMasteryRepo.save_batch(mastery_entries)
    except Exception as exc:
        logger.warning("Failed to extract topic mastery: %s", exc)

    await chat_service.close_with_summary(session_id, summary)

    return JSONResponse(
        {
            "id": session_id,
            "summary": summary,
            "mastery_count": len(mastery_entries),
            "status": "ended",
        }
    )
