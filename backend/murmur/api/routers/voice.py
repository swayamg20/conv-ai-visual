"""Authenticated legacy signaling and Voice V2 bootstrap routes."""

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from murmur.api.dependencies import (
    CurrentUserDependency,
    VoiceBootstrapServiceDependency,
    VoiceServiceDependency,
)
from murmur.api.errors import ApiError
from murmur.api.schemas import (
    Offer,
    VoiceSessionBootstrapRequest,
    VoiceSessionBootstrapResponse,
    VoiceSessionEndRequest,
)
from murmur.voice import VoiceOfferRequest
from murmur.voice.bootstrap import (
    VoiceBootstrapConflict,
    VoiceBootstrapForbidden,
    VoiceBootstrapNotFound,
    VoiceBootstrapUnavailable,
)

router = APIRouter(tags=["voice"])


@router.post("/api/voice/session", response_model=VoiceSessionBootstrapResponse)
async def bootstrap_voice_session(
    body: VoiceSessionBootstrapRequest,
    user: CurrentUserDependency,
    bootstrap_service: VoiceBootstrapServiceDependency,
    response: Response,
) -> VoiceSessionBootstrapResponse:
    """Authorize and return one short-lived, server-assigned Voice V2 session."""
    try:
        result = await bootstrap_service.bootstrap(
            user_id=user["id"],
            session_id=body.session_id,
            voice_call_id=body.voice_call_id,
        )
    except VoiceBootstrapNotFound as exc:
        raise ApiError(404, str(exc)) from exc
    except VoiceBootstrapForbidden as exc:
        raise ApiError(403, str(exc)) from exc
    except VoiceBootstrapConflict as exc:
        raise ApiError(409, str(exc)) from exc
    except VoiceBootstrapUnavailable as exc:
        raise ApiError(503, str(exc)) from exc

    response.headers["Cache-Control"] = "no-store"
    return VoiceSessionBootstrapResponse.model_validate(result.__dict__)


@router.post("/api/voice/session/end", status_code=204)
async def end_voice_session(
    body: VoiceSessionEndRequest,
    user: CurrentUserDependency,
    bootstrap_service: VoiceBootstrapServiceDependency,
) -> Response:
    """End one owned Voice V2 call after exact remote cleanup is confirmed."""
    try:
        await bootstrap_service.release(
            user_id=user["id"],
            session_id=body.session_id,
            voice_call_id=body.voice_call_id,
        )
    except VoiceBootstrapNotFound as exc:
        raise ApiError(404, str(exc)) from exc
    except VoiceBootstrapForbidden as exc:
        raise ApiError(403, str(exc)) from exc
    except VoiceBootstrapConflict as exc:
        raise ApiError(409, str(exc)) from exc
    except VoiceBootstrapUnavailable as exc:
        raise ApiError(503, str(exc)) from exc
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


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
