"""Strict request DTOs for the dedicated Pipecat HTTP boundary."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.types import StringConstraints

from murmur.api.schemas import UUID4String

_Sdp = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1_000_000)]
_PeerId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
_Candidate = Annotated[str, StringConstraints(strict=True, max_length=8_192)]
_SdpMid = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]


class _PipecatRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )


class PipecatSessionRequest(_PipecatRequest):
    """Exact persistent-session and call scope used by bootstrap and release."""

    session_id: UUID4String
    voice_call_id: UUID4String


class PipecatOfferBody(_PipecatRequest):
    """Pinned SmallWebRTC offer shape without arbitrary request data."""

    sdp: _Sdp
    type: Literal["offer"]
    pc_id: _PeerId | None = None
    restart_pc: Literal[False] = False


class PipecatCandidateBody(_PipecatRequest):
    """One strict trickle-ICE candidate from the pinned browser transport."""

    candidate: _Candidate
    sdp_mid: _SdpMid
    sdp_mline_index: Annotated[int, Field(strict=True, ge=0, le=128)]


class PipecatPatchBody(_PipecatRequest):
    """Candidate batch for one exact active SmallWebRTC peer."""

    pc_id: _PeerId
    candidates: Annotated[tuple[PipecatCandidateBody, ...], Field(min_length=1, max_length=128)]

    @field_validator("candidates", mode="before")
    @classmethod
    def freeze_json_candidates(cls, value: object) -> object:
        """Accept JSON's only array shape, then retain an immutable tuple."""

        return tuple(value) if isinstance(value, list) else value


class PipecatDeleteBody(_PipecatRequest):
    """Optional exact peer ID for an idempotent reservation DELETE."""

    pc_id: _PeerId | None = None


__all__ = [
    "PipecatCandidateBody",
    "PipecatDeleteBody",
    "PipecatOfferBody",
    "PipecatPatchBody",
    "PipecatSessionRequest",
]
