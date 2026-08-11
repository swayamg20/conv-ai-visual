"""Authenticated WebRTC signaling routes."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from murmur.api.dependencies import CurrentUserDependency, VoiceServiceDependency
from murmur.api.schemas import Offer
from murmur.voice import VoiceOfferRequest

router = APIRouter(tags=["voice"])


@router.post("/offer")
async def offer(
    body: Offer,
    user: CurrentUserDependency,
    voice_service: VoiceServiceDependency,
) -> JSONResponse:
    answer = await voice_service.negotiate(
        user["id"],
        VoiceOfferRequest(
            sdp=body.sdp,
            type=body.type,
            canvas_mode=body.canvas_mode,
            session_id=body.session_id,
            agent_id=body.agent_id,
        ),
    )
    return JSONResponse(
        {
            "sdp": answer.sdp,
            "type": answer.type,
            "session_id": answer.session_id,
            "agent_id": answer.agent_id,
        }
    )
