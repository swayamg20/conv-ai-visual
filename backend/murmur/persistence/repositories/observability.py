"""Repositories for LLM, voice, and TTS observability data."""

from sqlalchemy import func
from sqlmodel import Session, select

from murmur.persistence.database import get_session
from murmur.persistence.models import (
    LLMCallLogModel,
    TTSResilienceLogModel,
    VoicePipelineLogModel,
)


def _percentile(values: list[float], percentile: float) -> float | None:
    """Return a nearest-rank percentile for a small in-process dataset."""
    if not values:
        return None
    values.sort()
    rank = round((percentile / 100.0) * (len(values) - 1))
    rank = min(max(rank, 0), len(values) - 1)
    return round(values[rank], 2)


class LLMCallLogRepo:
    """Persist and aggregate LLM call logs."""

    @staticmethod
    def save(
        session_id: str,
        user_message: str,
        llm_provider: str,
        llm_model: str,
        user_id: str | None = None,
        tool_calls_json: str | None = None,
        response_text: str | None = None,
        latency_total_ms: float | None = None,
        latency_llm_ms: float | None = None,
        latency_tool_ms: float | None = None,
        latency_stream_ms: float | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        error: str | None = None,
    ) -> LLMCallLogModel:
        with get_session() as session:
            record = LLMCallLogModel(
                session_id=session_id,
                user_id=user_id,
                user_message=user_message,
                llm_provider=llm_provider,
                llm_model=llm_model,
                tool_calls_json=tool_calls_json,
                response_text=response_text,
                latency_total_ms=latency_total_ms,
                latency_llm_ms=latency_llm_ms,
                latency_tool_ms=latency_tool_ms,
                latency_stream_ms=latency_stream_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=error,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(user_id: str, limit: int = 50, offset: int = 0) -> list[LLMCallLogModel]:
        with get_session() as session:
            statement = (
                select(LLMCallLogModel)
                .where(LLMCallLogModel.user_id == user_id)
                .order_by(LLMCallLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(statement).all())

    @staticmethod
    def get_stats(user_id: str) -> dict:
        """Return aggregate LLM metrics scoped to one user."""
        with get_session() as session:
            user_scope = LLMCallLogModel.user_id == user_id
            total = session.exec(select(func.count(LLMCallLogModel.id)).where(user_scope)).one()
            avg_total = session.exec(
                select(func.avg(LLMCallLogModel.latency_total_ms)).where(user_scope)
            ).one()
            avg_llm = session.exec(
                select(func.avg(LLMCallLogModel.latency_llm_ms)).where(user_scope)
            ).one()
            avg_tool = session.exec(
                select(func.avg(LLMCallLogModel.latency_tool_ms)).where(user_scope)
            ).one()
            error_count = session.exec(
                select(func.count(LLMCallLogModel.id))
                .where(user_scope)
                .where(LLMCallLogModel.error.isnot(None))
            ).one()

            def values_for(column) -> list[float]:
                return [
                    value
                    for value in session.exec(
                        select(column).where(user_scope).where(column.isnot(None))
                    ).all()
                    if value is not None
                ]

            return {
                "total_calls": total or 0,
                "avg_latency_ms": round(avg_total or 0, 2),
                "avg_llm_latency_ms": round(avg_llm or 0, 2),
                "avg_tool_latency_ms": round(avg_tool or 0, 2),
                "p50_llm_latency_ms": _percentile(values_for(LLMCallLogModel.latency_llm_ms), 50),
                "p95_llm_latency_ms": _percentile(values_for(LLMCallLogModel.latency_llm_ms), 95),
                "p50_stream_latency_ms": _percentile(
                    values_for(LLMCallLogModel.latency_stream_ms), 50
                ),
                "p95_stream_latency_ms": _percentile(
                    values_for(LLMCallLogModel.latency_stream_ms), 95
                ),
                "error_count": error_count or 0,
                "error_rate": round((error_count or 0) / total * 100, 2) if total else 0,
            }


class VoicePipelineLogRepo:
    """Persist and aggregate voice-pipeline turn logs."""

    @staticmethod
    def save(**kwargs) -> VoicePipelineLogModel:
        with get_session() as session:
            record = VoicePipelineLogModel(**kwargs)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_recent(
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        mode: str | None = None,
    ) -> list[VoicePipelineLogModel]:
        with get_session() as session:
            statement = (
                select(VoicePipelineLogModel)
                .where(VoicePipelineLogModel.user_id == user_id)
                .order_by(VoicePipelineLogModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            if mode:
                statement = statement.where(VoicePipelineLogModel.mode == mode)
            return list(session.exec(statement).all())

    @staticmethod
    def get_stats(user_id: str, mode: str | None = None) -> dict:
        """Return aggregate voice metrics scoped to one user."""
        with get_session() as session:

            def scoped(statement):
                statement = statement.where(VoicePipelineLogModel.user_id == user_id)
                if mode:
                    statement = statement.where(VoicePipelineLogModel.mode == mode)
                return statement

            total = session.exec(scoped(select(func.count(VoicePipelineLogModel.id)))).one() or 0

            def average(column) -> float:
                return round(session.exec(scoped(select(func.avg(column)))).one() or 0, 2)

            def percentile(column, percentile_value: float) -> float | None:
                values = [
                    value
                    for value in session.exec(
                        scoped(select(column).where(column.isnot(None)))
                    ).all()
                    if value is not None
                ]
                return _percentile(values, percentile_value)

            error_count = (
                session.exec(
                    scoped(
                        select(func.count(VoicePipelineLogModel.id)).where(
                            VoicePipelineLogModel.error.isnot(None)
                        )
                    )
                ).one()
                or 0
            )
            interrupted_count = (
                session.exec(
                    scoped(
                        select(func.count(VoicePipelineLogModel.id)).where(
                            VoicePipelineLogModel.tts_interrupted.is_(True)
                        )
                    )
                ).one()
                or 0
            )
            resilience = TTSResilienceLogRepo.get_stats_for_voice_logs(
                session,
                user_id=user_id,
                mode=mode,
            )

            return {
                "total_turns": total,
                "avg_total_ms": average(VoicePipelineLogModel.latency_total_ms),
                "avg_vad_ms": average(VoicePipelineLogModel.latency_vad_ms),
                "avg_stt_ms": average(VoicePipelineLogModel.latency_stt_ms),
                "avg_turn_detection_ms": average(VoicePipelineLogModel.latency_turn_detection_ms),
                "avg_llm_ms": average(VoicePipelineLogModel.latency_llm_ms),
                "avg_llm_ttft_ms": average(VoicePipelineLogModel.latency_llm_first_token_ms),
                "p50_llm_ttft_ms": percentile(VoicePipelineLogModel.latency_llm_first_token_ms, 50),
                "p95_llm_ttft_ms": percentile(VoicePipelineLogModel.latency_llm_first_token_ms, 95),
                "avg_tool_ms": average(VoicePipelineLogModel.latency_tool_ms),
                "avg_tts_ms": average(VoicePipelineLogModel.latency_tts_ms),
                "avg_tts_ttfb_ms": average(VoicePipelineLogModel.latency_tts_first_chunk_ms),
                "error_count": error_count,
                "error_rate": round(error_count / total * 100, 2) if total else 0,
                "interrupted_count": interrupted_count,
                "interrupt_rate": round(interrupted_count / total * 100, 2) if total else 0,
                **resilience,
            }


class TTSResilienceLogRepo:
    """Persist and aggregate TTS retry and fallback metadata."""

    @staticmethod
    def save(**kwargs) -> TTSResilienceLogModel:
        with get_session() as session:
            record = TTSResilienceLogModel(**kwargs)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    @staticmethod
    def get_by_voice_log_ids(voice_log_ids: list[int]) -> dict[int, TTSResilienceLogModel]:
        if not voice_log_ids:
            return {}
        with get_session() as session:
            statement = select(TTSResilienceLogModel).where(
                TTSResilienceLogModel.voice_log_id.in_(voice_log_ids)
            )
            return {row.voice_log_id: row for row in session.exec(statement).all()}

    @staticmethod
    def get_stats_for_voice_logs(
        db: Session,
        user_id: str,
        mode: str | None = None,
    ) -> dict[str, float]:
        log_ids_statement = select(VoicePipelineLogModel.id).where(
            VoicePipelineLogModel.user_id == user_id
        )
        if mode:
            log_ids_statement = log_ids_statement.where(VoicePipelineLogModel.mode == mode)
        voice_log_ids = list(db.exec(log_ids_statement).all())
        empty_stats = {
            "retry_turns": 0,
            "avg_tts_retry_count": 0.0,
            "fallback_count": 0,
            "fallback_rate": 0.0,
        }
        if not voice_log_ids:
            return empty_stats

        rows = list(
            db.exec(
                select(TTSResilienceLogModel).where(
                    TTSResilienceLogModel.voice_log_id.in_(voice_log_ids)
                )
            ).all()
        )
        if not rows:
            return empty_stats

        retry_turns = sum(1 for row in rows if row.retry_count > 0)
        fallback_count = sum(1 for row in rows if row.fallback_used)
        return {
            "retry_turns": retry_turns,
            "avg_tts_retry_count": round(sum(row.retry_count for row in rows) / len(rows), 2),
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_count / len(rows) * 100, 2),
        }
