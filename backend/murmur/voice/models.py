"""Transport-neutral WebRTC signaling models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceOfferRequest:
    sdp: str
    type: str
    canvas_mode: bool = False
    session_id: str | None = None
    agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceOfferAnswer:
    sdp: str
    type: str
    session_id: str | None
    agent_id: str | None
