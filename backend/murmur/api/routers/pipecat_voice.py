"""Authenticated routes for the standalone Pipecat signaling process."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Protocol, TypeVar, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.requests import ClientDisconnect

from murmur.api.pipecat_schemas import (
    PipecatDeleteBody,
    PipecatOfferBody,
    PipecatPatchBody,
    PipecatSessionRequest,
)
from murmur.voice.pipecat_bootstrap import (
    PipecatBootstrapConflict,
    PipecatBootstrapForbidden,
    PipecatBootstrapNotFound,
    PipecatBootstrapUnavailable,
)
from murmur.voice.pipecat_signaling import (
    PipecatIceCandidate,
    PipecatOfferAnswer,
    PipecatOfferRequest,
    PipecatPatchRequest,
    PipecatSignalingConflict,
    PipecatSignalingForbidden,
    PipecatSignalingNotFound,
    PipecatSignalingUnavailable,
)
from murmur.voice.runtime_projection import (
    PipecatBrowserVoiceAssignment,
    PipecatRuntimeProjectionForbidden,
    PipecatRuntimeProjectionUnavailable,
)

DEFAULT_MAX_PIPECAT_REQUEST_BODY_BYTES = 1_100_000
_MAX_OPAQUE_TOKEN_LENGTH = 512
_Model = TypeVar("_Model", bound=BaseModel)


class PipecatHttpComposition(Protocol):
    async def bootstrap_browser_assignment(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatBrowserVoiceAssignment: ...

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> object: ...

    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer: ...

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None: ...

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> object: ...

    async def aclose(self) -> None: ...


class PipecatHttpError(Exception):
    """A fixed, non-reflective response owned by the dedicated HTTP boundary."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


router = APIRouter(tags=["pipecat-voice"])


@router.post("/api/voice/session")
async def bootstrap_pipecat_session(request: Request) -> JSONResponse:
    body = await _read_dto(request, PipecatSessionRequest)
    composition = _composition(request)
    try:
        assignment = await composition.bootstrap_browser_assignment(
            user_id=_authenticated_user_id(request),
            session_id=body.session_id,
            voice_call_id=body.voice_call_id,
        )
    except PipecatBootstrapNotFound as exc:
        raise PipecatHttpError(404, "Voice session was not found") from exc
    except (PipecatBootstrapForbidden, PipecatRuntimeProjectionForbidden) as exc:
        raise PipecatHttpError(403, "Forbidden") from exc
    except PipecatBootstrapConflict as exc:
        raise PipecatHttpError(409, "Voice session conflicts with retained state") from exc
    except (PipecatBootstrapUnavailable, PipecatRuntimeProjectionUnavailable) as exc:
        raise PipecatHttpError(503, "Voice session is unavailable") from exc
    return JSONResponse(assignment.model_dump(mode="json"))


@router.post("/api/voice/session/end", status_code=204)
async def end_pipecat_session(request: Request) -> Response:
    body = await _read_dto(request, PipecatSessionRequest)
    composition = _composition(request)
    try:
        await composition.release(
            user_id=_authenticated_user_id(request),
            session_id=body.session_id,
            voice_call_id=body.voice_call_id,
        )
    except PipecatBootstrapNotFound as exc:
        raise PipecatHttpError(404, "Voice session was not found") from exc
    except PipecatBootstrapForbidden as exc:
        raise PipecatHttpError(403, "Forbidden") from exc
    except PipecatBootstrapConflict as exc:
        raise PipecatHttpError(409, "Voice session conflicts with retained state") from exc
    except PipecatBootstrapUnavailable as exc:
        raise PipecatHttpError(503, "Voice session is unavailable") from exc
    return Response(status_code=204)


@router.post("/api/voice/pipecat/signal/{opaque_token}")
async def offer_pipecat_peer(opaque_token: str, request: Request) -> JSONResponse:
    token = _validated_opaque_token(opaque_token)
    body = await _read_dto(request, PipecatOfferBody)
    try:
        domain_request = PipecatOfferRequest(
            sdp=body.sdp,
            type=body.type,
            pc_id=body.pc_id,
            restart_pc=body.restart_pc,
        )
    except ValueError as exc:
        raise PipecatHttpError(422, "Request body is invalid") from exc
    try:
        answer = await _composition(request).offer(
            token=token,
            user_id=_authenticated_user_id(request),
            request=domain_request,
        )
    except PipecatSignalingNotFound as exc:
        raise PipecatHttpError(404, "Voice reservation was not found") from exc
    except PipecatSignalingForbidden as exc:
        raise PipecatHttpError(403, "Forbidden") from exc
    except PipecatSignalingConflict as exc:
        raise PipecatHttpError(409, "Voice reservation conflicts with retained state") from exc
    except PipecatSignalingUnavailable as exc:
        raise PipecatHttpError(503, "Voice signaling is unavailable") from exc
    return JSONResponse({"sdp": answer.sdp, "type": answer.type, "pc_id": answer.pc_id})


@router.patch("/api/voice/pipecat/signal/{opaque_token}", status_code=204)
async def patch_pipecat_peer(opaque_token: str, request: Request) -> Response:
    token = _validated_opaque_token(opaque_token)
    body = await _read_dto(request, PipecatPatchBody)
    try:
        domain_request = PipecatPatchRequest(
            pc_id=body.pc_id,
            candidates=tuple(
                PipecatIceCandidate(
                    candidate=candidate.candidate,
                    sdp_mid=candidate.sdp_mid,
                    sdp_mline_index=candidate.sdp_mline_index,
                )
                for candidate in body.candidates
            ),
        )
    except ValueError as exc:
        raise PipecatHttpError(422, "Request body is invalid") from exc
    try:
        await _composition(request).patch(
            token=token,
            user_id=_authenticated_user_id(request),
            request=domain_request,
        )
    except PipecatSignalingNotFound as exc:
        raise PipecatHttpError(404, "Voice reservation was not found") from exc
    except PipecatSignalingForbidden as exc:
        raise PipecatHttpError(403, "Forbidden") from exc
    except PipecatSignalingConflict as exc:
        raise PipecatHttpError(409, "Voice reservation conflicts with retained state") from exc
    except PipecatSignalingUnavailable as exc:
        raise PipecatHttpError(503, "Voice signaling is unavailable") from exc
    return Response(status_code=204)


@router.delete("/api/voice/pipecat/signal/{opaque_token}", status_code=204)
async def delete_pipecat_peer(opaque_token: str, request: Request) -> Response:
    token = _validated_opaque_token(opaque_token)
    body = await _read_dto(request, PipecatDeleteBody)
    try:
        await _composition(request).delete(
            token=token,
            user_id=_authenticated_user_id(request),
            pc_id=body.pc_id,
        )
    except PipecatSignalingNotFound as exc:
        raise PipecatHttpError(404, "Voice reservation was not found") from exc
    except PipecatSignalingForbidden as exc:
        raise PipecatHttpError(403, "Forbidden") from exc
    except PipecatSignalingConflict as exc:
        raise PipecatHttpError(409, "Voice reservation conflicts with retained state") from exc
    except PipecatSignalingUnavailable as exc:
        raise PipecatHttpError(503, "Voice signaling is unavailable") from exc
    return Response(status_code=204)


async def _read_dto(request: Request, model_type: type[_Model]) -> _Model:
    _require_json_content_type(request)
    maximum = cast(int, request.app.state.pipecat_max_request_body_bytes)
    declared_length = _declared_content_length(request, maximum=maximum)
    if declared_length is not None and declared_length > maximum:
        raise PipecatHttpError(413, "Request body is too large")

    body = bytearray()
    try:
        timeout_seconds = cast(float, request.app.state.pipecat_body_read_timeout_seconds)
        async with asyncio.timeout(timeout_seconds):
            async for chunk in request.stream():
                if len(body) + len(chunk) > maximum:
                    raise PipecatHttpError(413, "Request body is too large")
                body.extend(chunk)
    except PipecatHttpError:
        raise
    except TimeoutError as exc:
        raise PipecatHttpError(408, "Request body timed out") from exc
    except ClientDisconnect as exc:
        raise PipecatHttpError(400, "Request body is invalid JSON") from exc
    if declared_length is not None and len(body) != declared_length:
        raise PipecatHttpError(400, "Request framing is invalid")
    if not body:
        raise PipecatHttpError(400, "Request body is invalid JSON")

    try:
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise PipecatHttpError(400, "Request body is invalid JSON") from exc
    try:
        return model_type.model_validate(decoded)
    except ValidationError as exc:
        raise PipecatHttpError(422, "Request body is invalid") from exc


def _require_json_content_type(request: Request) -> None:
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1:
        raise PipecatHttpError(415, "Request body must use application/json")
    media_type = content_types[0].split(";", 1)[0].strip().casefold()
    if media_type != "application/json":
        raise PipecatHttpError(415, "Request body must use application/json")
    content_encoding = request.headers.get("content-encoding")
    if content_encoding is not None and content_encoding.strip().casefold() != "identity":
        raise PipecatHttpError(415, "Request body must use application/json")


def _declared_content_length(request: Request, *, maximum: int) -> int | None:
    values = request.headers.getlist("content-length")
    transfer_encodings = request.headers.getlist("transfer-encoding")
    if transfer_encodings:
        if values or len(transfer_encodings) != 1:
            raise PipecatHttpError(400, "Request framing is invalid")
        if transfer_encodings[0].strip().casefold() != "chunked":
            raise PipecatHttpError(400, "Request framing is invalid")
    if not values:
        return None
    raw_value = values[0] if len(values) == 1 else ""
    if not raw_value or len(raw_value) > 20 or not raw_value.isascii() or not raw_value.isdigit():
        raise PipecatHttpError(400, "Request framing is invalid")
    normalized = raw_value.lstrip("0") or "0"
    maximum_text = str(maximum)
    if len(normalized) > len(maximum_text) or (
        len(normalized) == len(maximum_text) and normalized > maximum_text
    ):
        raise PipecatHttpError(413, "Request body is too large")
    return int(normalized)


def _validated_opaque_token(token: str) -> str:
    if (
        not token
        or token != token.strip()
        or len(token) > _MAX_OPAQUE_TOKEN_LENGTH
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        raise PipecatHttpError(422, "Voice reservation identifier is invalid")
    return token


def _authenticated_user_id(request: Request) -> str:
    identity = getattr(request.state, "pipecat_user", None)
    if not isinstance(identity, Mapping):
        raise PipecatHttpError(401, "Not authenticated")
    user_id = identity.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise PipecatHttpError(401, "Not authenticated")
    return user_id


def _composition(request: Request) -> PipecatHttpComposition:
    return cast(PipecatHttpComposition, request.app.state.pipecat_composition)


class _InvalidJson(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJson
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise _InvalidJson


__all__ = [
    "DEFAULT_MAX_PIPECAT_REQUEST_BODY_BYTES",
    "PipecatHttpComposition",
    "PipecatHttpError",
    "router",
]
