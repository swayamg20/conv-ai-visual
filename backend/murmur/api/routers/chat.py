"""Authenticated text-chat streaming and control routes."""

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from murmur.api.dependencies import ChatServiceDependency, CurrentUserDependency
from murmur.api.schemas import CanvasModeRequest, ChatMessage
from murmur.chat import ChatTurnRequest

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


async def _encode_sse(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatMessage,
    user: CurrentUserDependency,
    chat_service: ChatServiceDependency,
) -> StreamingResponse:
    if body.user_id and body.user_id != user["id"]:
        logger.warning(
            "Ignoring client-supplied chat user_id %s in favor of authenticated user %s",
            body.user_id,
            user["id"],
        )

    turn = chat_service.prepare_turn(
        user["id"],
        ChatTurnRequest(
            message=body.message,
            session_id=body.session_id,
            agent_id=body.agent_id,
            canvas_mode=body.canvas_mode,
        ),
    )
    return StreamingResponse(
        _encode_sse(chat_service.stream_events(turn)),
        media_type="text/event-stream",
    )


@router.delete("/chat/{session_id}")
async def clear_chat(
    session_id: str,
    user: CurrentUserDependency,
    chat_service: ChatServiceDependency,
) -> JSONResponse:
    chat_service.require_owner(session_id, user["id"])
    try:
        await chat_service.finalize(
            session_id,
            min_messages=0,
            persist_db_summary=True,
            background=False,
        )
    except Exception as exc:
        logger.warning("Failed to clear chat session %s: %s", session_id, exc)
    return JSONResponse({"status": "cleared"})


@router.post("/chat/{session_id}/canvas-mode")
async def set_canvas_mode(
    session_id: str,
    body: CanvasModeRequest,
    user: CurrentUserDependency,
    chat_service: ChatServiceDependency,
) -> JSONResponse:
    return JSONResponse(
        await chat_service.set_canvas_mode(
            session_id,
            user["id"],
            enabled=body.enabled,
            custom_prompt=body.custom_prompt,
        )
    )
