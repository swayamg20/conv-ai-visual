"""Authenticated bootstrap and provider-free canary for first-party voice."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from murmur.api.dependencies import CurrentUserDependency, WebSocketVoiceServiceDependency
from murmur.api.errors import ApiError
from murmur.api.schemas import (
    VoiceSessionBootstrapRequest,
    VoiceSessionEndRequest,
    WebSocketVoiceSessionBootstrapResponse,
)
from murmur.voice.bootstrap import (
    VoiceBootstrapConflict,
    VoiceBootstrapForbidden,
    VoiceBootstrapNotFound,
    VoiceBootstrapUnavailable,
)
from murmur.voice.websocket_protocol import (
    INPUT_FRAME_PCM_BYTES,
    INPUT_SAMPLE_RATE_HZ,
    WEBSOCKET_CANARY_MODE,
    WEBSOCKET_TICKET_PROTOCOL_PREFIX,
    WEBSOCKET_VOICE_PROTOCOL,
    VoiceBinaryFrame,
    VoiceBinaryFrameError,
    VoiceFrameKind,
    decode_voice_binary_frame,
    encode_voice_binary_frame,
)
from murmur.voice.websocket_ticket import (
    WebSocketVoiceBootstrapService,
    WebSocketVoiceConnection,
    is_websocket_ticket,
    normalize_websocket_origin,
)

router = APIRouter(prefix="/api/voice/websocket", tags=["websocket-voice"])
logger = logging.getLogger(__name__)

_MAX_CONTROL_FRAME_CHARS = 1_024
_NORMAL_CLOSE = 1_000
_POLICY_CLOSE = 1_008
_INTERNAL_CLOSE = 1_011
_PRE_ACCEPT_SEND_TIMEOUT_SECONDS = 2.0


class _ReleaseRequested(Exception):
    """Stop non-terminal output as soon as the exact call is released."""


@router.post("/session", response_model=WebSocketVoiceSessionBootstrapResponse)
async def bootstrap_websocket_voice_session(
    body: VoiceSessionBootstrapRequest,
    user: CurrentUserDependency,
    service: WebSocketVoiceServiceDependency,
    response: Response,
) -> WebSocketVoiceSessionBootstrapResponse:
    try:
        assignment = await service.bootstrap(
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
    return WebSocketVoiceSessionBootstrapResponse.model_validate(asdict(assignment))


@router.post("/session/end", status_code=204)
async def end_websocket_voice_session(
    body: VoiceSessionEndRequest,
    user: CurrentUserDependency,
    service: WebSocketVoiceServiceDependency,
) -> Response:
    try:
        await service.release(
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


@router.websocket("")
async def websocket_voice_stream(websocket: WebSocket) -> None:
    service = _service(websocket)
    connection: WebSocketVoiceConnection | None = None
    try:
        if not hasattr(service, "settings"):
            await _close_safely(websocket, _POLICY_CLOSE, _PRE_ACCEPT_SEND_TIMEOUT_SECONDS)
            return
        if not _origin_is_allowed(websocket, service):
            await _close_safely(websocket, _POLICY_CLOSE, service.settings.send_timeout_seconds)
            return
        ticket = _offered_ticket(websocket)
        if ticket is None:
            await _close_safely(websocket, _POLICY_CLOSE, service.settings.send_timeout_seconds)
            return
        try:
            connection = await service.consume(ticket)
        except (VoiceBootstrapForbidden, VoiceBootstrapUnavailable):
            await _close_safely(websocket, _POLICY_CLOSE, service.settings.send_timeout_seconds)
            return
        async with asyncio.timeout(service.settings.send_timeout_seconds):
            await websocket.accept(subprotocol=WEBSOCKET_VOICE_PROTOCOL)
        await _run_provider_free_canary(websocket, service, connection)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Voice WebSocket failed")
        timeout_seconds = getattr(
            getattr(service, "settings", None),
            "send_timeout_seconds",
            _PRE_ACCEPT_SEND_TIMEOUT_SECONDS,
        )
        await _close_safely(websocket, _INTERNAL_CLOSE, timeout_seconds)
    finally:
        if connection is not None:
            await service.disconnect(connection)


def _service(websocket: WebSocket) -> WebSocketVoiceBootstrapService:
    return websocket.app.state.websocket_voice_service


def _origin_is_allowed(websocket: WebSocket, service: WebSocketVoiceBootstrapService) -> bool:
    origins = websocket.headers.getlist("origin")
    if len(origins) != 1:
        return False
    try:
        origin = normalize_websocket_origin(origins[0])
    except ValueError:
        return False
    return origin in service.settings.allowed_origins


def _offered_ticket(websocket: WebSocket) -> str | None:
    offered = websocket.scope.get("subprotocols")
    if not isinstance(offered, list) or len(offered) != 2:
        return None
    if offered.count(WEBSOCKET_VOICE_PROTOCOL) != 1:
        return None
    ticket_protocols = [
        item
        for item in offered
        if isinstance(item, str) and item.startswith(WEBSOCKET_TICKET_PROTOCOL_PREFIX)
    ]
    if len(ticket_protocols) != 1:
        return None
    ticket = ticket_protocols[0][len(WEBSOCKET_TICKET_PROTOCOL_PREFIX) :]
    return ticket if is_websocket_ticket(ticket) else None


async def _run_provider_free_canary(
    websocket: WebSocket,
    service: WebSocketVoiceBootstrapService,
    connection: WebSocketVoiceConnection,
) -> None:
    generation = 0
    expected_sequence = 0
    server_sequence = 0
    started = time.monotonic()
    receive: asyncio.Task[dict[str, Any]] | None = None

    release_wait = asyncio.create_task(
        connection.release_requested.wait(),
        name=f"voice-websocket-release-{connection.connection_id}",
    )

    async def send(
        operation: Callable[[], Awaitable[None]],
        *,
        allow_released: bool = False,
    ) -> None:
        if connection.release_requested.is_set() and not allow_released:
            raise _ReleaseRequested
        outgoing = asyncio.create_task(
            operation(),
            name=f"voice-websocket-send-{connection.connection_id}",
        )
        waiters: set[asyncio.Task[Any]] = {outgoing}
        if not allow_released:
            waiters.add(release_wait)
        try:
            done, _pending = await asyncio.wait(
                waiters,
                timeout=service.settings.send_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not allow_released and (
                connection.release_requested.is_set() or release_wait in done
            ):
                raise _ReleaseRequested
            if outgoing not in done:
                raise TimeoutError("Voice WebSocket send timed out")
            outgoing.result()
        finally:
            if not outgoing.done():
                outgoing.cancel()
            await asyncio.gather(outgoing, return_exceptions=True)

    async def send_control(
        kind: str,
        *,
        allow_released: bool = False,
        **payload: Any,
    ) -> None:
        nonlocal server_sequence
        server_sequence += 1
        message = {
            "type": kind,
            "server_sequence": server_sequence,
            "generation": generation,
            "trace_id": connection.trace_id,
            **payload,
        }
        await send(
            lambda: websocket.send_json(message),
            allow_released=allow_released,
        )

    async def policy_failure(code: str) -> None:
        await send(lambda: websocket.send_json({"type": "error", "code": code}))
        await _close_safely(
            websocket,
            _POLICY_CLOSE,
            service.settings.send_timeout_seconds,
        )

    try:
        try:
            await send_control(
                "canary_ready",
                protocol=WEBSOCKET_VOICE_PROTOCOL,
                runtime_mode=WEBSOCKET_CANARY_MODE,
                session_id=connection.scope.session_id,
                voice_call_id=connection.scope.voice_call_id,
                profile_id=connection.profile_id,
                input_sample_rate_hz=INPUT_SAMPLE_RATE_HZ,
                output_sample_rate_hz=INPUT_SAMPLE_RATE_HZ,
                input_frame_pcm_bytes=INPUT_FRAME_PCM_BYTES,
            )

            while True:
                if connection.release_requested.is_set():
                    raise _ReleaseRequested
                elapsed = time.monotonic() - started
                remaining = service.settings.max_session_seconds - elapsed
                if remaining <= 0:
                    await send_control("session_limit")
                    await _close_safely(
                        websocket,
                        _NORMAL_CLOSE,
                        service.settings.send_timeout_seconds,
                    )
                    return
                timeout = min(service.settings.heartbeat_seconds, remaining)
                receive = asyncio.create_task(
                    websocket.receive(),
                    name=f"voice-websocket-receive-{connection.connection_id}",
                )
                done, _pending = await asyncio.wait(
                    {receive, release_wait},
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if release_wait in done:
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                    receive = None
                    raise _ReleaseRequested
                if receive not in done:
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
                    receive = None
                    elapsed = time.monotonic() - started
                    if elapsed >= service.settings.max_session_seconds:
                        await send_control("session_limit")
                        await _close_safely(
                            websocket,
                            _NORMAL_CLOSE,
                            service.settings.send_timeout_seconds,
                        )
                        return
                    await send_control("heartbeat", elapsed_ms=round(elapsed * 1_000))
                    continue
                message = receive.result()
                receive = None
                if connection.release_requested.is_set():
                    raise _ReleaseRequested
                if time.monotonic() - started >= service.settings.max_session_seconds:
                    await send_control("session_limit")
                    await _close_safely(
                        websocket,
                        _NORMAL_CLOSE,
                        service.settings.send_timeout_seconds,
                    )
                    return

                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    return
                raw_text = message.get("text")
                raw_bytes = message.get("bytes")
                if raw_text is not None:
                    if not isinstance(raw_text, str) or len(raw_text) > _MAX_CONTROL_FRAME_CHARS:
                        await policy_failure("control_frame_invalid")
                        return
                    try:
                        control = json.loads(raw_text)
                    except json.JSONDecodeError:
                        await policy_failure("control_frame_invalid")
                        return
                    if not isinstance(control, dict) or not isinstance(control.get("type"), str):
                        await policy_failure("control_frame_invalid")
                        return
                    control_type = control["type"]
                    if control_type == "ping" and set(control) == {"type", "sequence"}:
                        sequence = control["sequence"]
                        if (
                            isinstance(sequence, bool)
                            or not isinstance(sequence, int)
                            or sequence < 0
                        ):
                            await policy_failure("control_frame_invalid")
                            return
                        await send_control("pong", client_sequence=sequence)
                        continue
                    if control_type == "interrupt" and set(control) == {"type"}:
                        generation += 1
                        expected_sequence = 0
                        await send_control("clear_audio")
                        continue
                    if control_type == "close" and set(control) == {"type"}:
                        await send_control("closing")
                        await _close_safely(
                            websocket,
                            _NORMAL_CLOSE,
                            service.settings.send_timeout_seconds,
                        )
                        return
                    await policy_failure("control_frame_invalid")
                    return

                if not isinstance(raw_bytes, bytes):
                    await policy_failure("frame_type_invalid")
                    return
                try:
                    frame = decode_voice_binary_frame(raw_bytes)
                except VoiceBinaryFrameError:
                    await policy_failure("binary_frame_invalid")
                    return
                if (
                    frame.kind is not VoiceFrameKind.INPUT_PCM
                    or frame.generation != generation
                    or frame.sequence != expected_sequence
                    or len(frame.payload) > INPUT_FRAME_PCM_BYTES
                ):
                    await policy_failure("binary_frame_invalid")
                    return
                expected_sequence += 1
                output = encode_voice_binary_frame(
                    VoiceBinaryFrame(
                        kind=VoiceFrameKind.OUTPUT_PCM,
                        generation=generation,
                        sequence=frame.sequence,
                        payload=frame.payload,
                    )
                )
                await send(lambda output=output: websocket.send_bytes(output))
        except _ReleaseRequested:
            await send_control("session_released", allow_released=True)
            await _close_safely(
                websocket,
                _NORMAL_CLOSE,
                service.settings.send_timeout_seconds,
            )
    finally:
        if receive is not None and not receive.done():
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
        release_wait.cancel()
        await asyncio.gather(release_wait, return_exceptions=True)


async def _close_safely(websocket: WebSocket, code: int, timeout_seconds: float) -> None:
    try:
        async with asyncio.timeout(timeout_seconds):
            await websocket.close(code=code)
    except Exception:
        pass


__all__ = ["router"]
