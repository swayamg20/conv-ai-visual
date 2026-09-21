"""Safe, queryable lifecycle logging for the Voice V2 control and media planes."""

from __future__ import annotations

import logging

from murmur.voice.bootstrap_contracts import is_contract_id

_UNAVAILABLE = "unavailable"


def log_voice_v2_lifecycle(
    logger: logging.Logger,
    level: int,
    *,
    component: str,
    event: str,
    outcome: str,
    stage: str,
    voice_call_id: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    error: BaseException | None = None,
) -> None:
    """Emit only allowlisted identifiers and an exception type, never payload content."""
    logger.log(
        level,
        (
            "voice_v2 component=%s event=%s outcome=%s stage=%s "
            "voice_call_id=%s trace_id=%s session_id=%s error_type=%s"
        ),
        _safe_identifier(component),
        _safe_identifier(event),
        _safe_identifier(outcome),
        _safe_identifier(stage),
        _safe_identifier(voice_call_id),
        _safe_identifier(trace_id),
        _safe_identifier(session_id),
        _safe_identifier(type(error).__name__ if error is not None else None),
    )


def _safe_identifier(value: object) -> str:
    return value if is_contract_id(value) else _UNAVAILABLE
