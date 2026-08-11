"""User-scoped LLM and voice observability routes."""

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from murmur.api.dependencies import CurrentUserDependency
from murmur.persistence.models import LLMCallLogModel, VoicePipelineLogModel
from murmur.persistence.repositories.observability import (
    LLMCallLogRepo,
    TTSResilienceLogRepo,
    VoicePipelineLogRepo,
)

router = APIRouter(tags=["observability"])


def _serialize_llm_log(log: LLMCallLogModel) -> dict[str, Any]:
    return {
        "id": log.id,
        "session_id": log.session_id,
        "user_id": log.user_id,
        "user_message": log.user_message,
        "llm_provider": log.llm_provider,
        "llm_model": log.llm_model,
        "tool_calls": log.get_tool_calls(),
        "response_text": log.response_text,
        "latency_total_ms": log.latency_total_ms,
        "latency_llm_ms": log.latency_llm_ms,
        "latency_tool_ms": log.latency_tool_ms,
        "latency_stream_ms": log.latency_stream_ms,
        "tokens_in": log.tokens_in,
        "tokens_out": log.tokens_out,
        "error": log.error,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _serialize_voice_log(
    log: VoicePipelineLogModel,
    resilience_by_log_id: dict,
) -> dict[str, Any]:
    resilience = resilience_by_log_id.get(log.id)
    return {
        "id": log.id,
        "session_id": log.session_id,
        "user_id": log.user_id,
        "mode": log.mode,
        "user_message": log.user_message,
        "response_text": log.response_text,
        "llm_provider": log.llm_provider,
        "llm_model": log.llm_model,
        "latency_vad_ms": log.latency_vad_ms,
        "latency_stt_ms": log.latency_stt_ms,
        "latency_turn_detection_ms": log.latency_turn_detection_ms,
        "latency_stt_to_llm_ms": log.latency_stt_to_llm_ms,
        "latency_llm_ms": log.latency_llm_ms,
        "latency_llm_first_token_ms": log.latency_llm_first_token_ms,
        "latency_tool_ms": log.latency_tool_ms,
        "latency_tts_ms": log.latency_tts_ms,
        "latency_tts_first_chunk_ms": log.latency_tts_first_chunk_ms,
        "latency_total_ms": log.latency_total_ms,
        "tool_calls": log.get_tool_calls(),
        "tokens_in": log.tokens_in,
        "tokens_out": log.tokens_out,
        "tts_chunks_sent": log.tts_chunks_sent,
        "tts_interrupted": log.tts_interrupted,
        "tts_provider_used": resilience.provider_used if resilience else None,
        "tts_retry_count": resilience.retry_count if resilience else 0,
        "tts_fallback_used": resilience.fallback_used if resilience else False,
        "tts_fallback_provider": resilience.fallback_provider if resilience else None,
        "smart_turn_used": log.smart_turn_used,
        "smart_turn_result": log.smart_turn_result,
        "error": log.error,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get("/api/logs")
async def get_logs(
    user: CurrentUserDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JSONResponse:
    logs = LLMCallLogRepo.get_recent(user["id"], limit=limit, offset=offset)
    return JSONResponse(
        {
            "logs": [_serialize_llm_log(log) for log in logs],
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/api/logs/stats")
async def get_logs_stats(user: CurrentUserDependency) -> JSONResponse:
    return JSONResponse(LLMCallLogRepo.get_stats(user["id"]))


@router.get("/api/voice-logs")
async def get_voice_logs(
    user: CurrentUserDependency,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    mode: str | None = None,
) -> JSONResponse:
    logs = VoicePipelineLogRepo.get_recent(user["id"], limit=limit, offset=offset, mode=mode)
    resilience = TTSResilienceLogRepo.get_by_voice_log_ids(
        [log.id for log in logs if log.id is not None]
    )
    return JSONResponse(
        {
            "logs": [_serialize_voice_log(log, resilience) for log in logs],
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/api/voice-logs/stats")
async def get_voice_logs_stats(
    user: CurrentUserDependency,
    mode: str | None = None,
) -> JSONResponse:
    return JSONResponse(VoicePipelineLogRepo.get_stats(user["id"], mode=mode))
