"""Transport-neutral chat request and turn models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from murmur.runtime.registry import ChatRuntimeSession


@dataclass(frozen=True, slots=True)
class ChatTurnRequest:
    message: str
    session_id: str | None = None
    agent_id: str | None = None
    canvas_mode: bool | None = None


@dataclass(frozen=True, slots=True)
class ChatTurn:
    session_id: str
    user_id: str
    message: str
    session: ChatRuntimeSession
    canvas_mode: bool | None = None
