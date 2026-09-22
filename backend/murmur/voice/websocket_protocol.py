"""Strict wire primitives for Murmur's first-party voice WebSocket."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

WEBSOCKET_VOICE_RUNTIME = "websocket_v1"
WEBSOCKET_VOICE_PROTOCOL = "murmur.voice.websocket.v1"
WEBSOCKET_VOICE_PROFILE = "murmur-direct-cascade-v1"
WEBSOCKET_CANARY_MODE = "provider_free_echo"
WEBSOCKET_TICKET_PROTOCOL_PREFIX = "murmur-ticket."

INPUT_SAMPLE_RATE_HZ = 16_000
OUTPUT_SAMPLE_RATE_HZ = 24_000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH_BYTES = 2
INPUT_FRAME_DURATION_MS = 20
INPUT_FRAME_PCM_BYTES = (
    INPUT_SAMPLE_RATE_HZ * PCM_CHANNELS * PCM_SAMPLE_WIDTH_BYTES * INPUT_FRAME_DURATION_MS // 1_000
)
MAX_PCM_PAYLOAD_BYTES = 4_800

_FRAME_MAGIC = b"MV"
_FRAME_VERSION = 1
_HEADER = struct.Struct("!2sBBII")
BINARY_HEADER_BYTES = _HEADER.size
MAX_BINARY_FRAME_BYTES = BINARY_HEADER_BYTES + MAX_PCM_PAYLOAD_BYTES


class VoiceBinaryFrameError(ValueError):
    """The client supplied a binary frame outside the versioned contract."""


class VoiceFrameKind(IntEnum):
    INPUT_PCM = 1
    OUTPUT_PCM = 2


@dataclass(frozen=True, slots=True)
class VoiceBinaryFrame:
    kind: VoiceFrameKind
    generation: int
    sequence: int
    payload: bytes

    def __post_init__(self) -> None:
        if not 0 <= self.generation <= 0xFFFFFFFF:
            raise VoiceBinaryFrameError("voice frame generation is out of range")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise VoiceBinaryFrameError("voice frame sequence is out of range")
        if not self.payload or len(self.payload) > MAX_PCM_PAYLOAD_BYTES:
            raise VoiceBinaryFrameError("voice frame PCM payload size is invalid")
        if len(self.payload) % PCM_SAMPLE_WIDTH_BYTES:
            raise VoiceBinaryFrameError("voice frame PCM payload is not sample-aligned")


def encode_voice_binary_frame(frame: VoiceBinaryFrame) -> bytes:
    """Serialize one PCM frame with an explicit kind, generation, and sequence."""

    return (
        _HEADER.pack(
            _FRAME_MAGIC,
            _FRAME_VERSION,
            int(frame.kind),
            frame.generation,
            frame.sequence,
        )
        + frame.payload
    )


def decode_voice_binary_frame(value: bytes) -> VoiceBinaryFrame:
    """Decode one complete client or server PCM frame without accepting extensions."""

    if len(value) <= BINARY_HEADER_BYTES or len(value) > MAX_BINARY_FRAME_BYTES:
        raise VoiceBinaryFrameError("voice binary frame size is invalid")
    magic, version, raw_kind, generation, sequence = _HEADER.unpack_from(value)
    if magic != _FRAME_MAGIC or version != _FRAME_VERSION:
        raise VoiceBinaryFrameError("voice binary frame version is unsupported")
    try:
        kind = VoiceFrameKind(raw_kind)
    except ValueError as exc:
        raise VoiceBinaryFrameError("voice binary frame kind is unsupported") from exc
    return VoiceBinaryFrame(
        kind=kind,
        generation=generation,
        sequence=sequence,
        payload=value[BINARY_HEADER_BYTES:],
    )


__all__ = [
    "BINARY_HEADER_BYTES",
    "INPUT_FRAME_DURATION_MS",
    "INPUT_FRAME_PCM_BYTES",
    "INPUT_SAMPLE_RATE_HZ",
    "MAX_BINARY_FRAME_BYTES",
    "MAX_PCM_PAYLOAD_BYTES",
    "OUTPUT_SAMPLE_RATE_HZ",
    "PCM_CHANNELS",
    "PCM_SAMPLE_WIDTH_BYTES",
    "WEBSOCKET_CANARY_MODE",
    "WEBSOCKET_TICKET_PROTOCOL_PREFIX",
    "WEBSOCKET_VOICE_PROFILE",
    "WEBSOCKET_VOICE_PROTOCOL",
    "WEBSOCKET_VOICE_RUNTIME",
    "VoiceBinaryFrame",
    "VoiceBinaryFrameError",
    "VoiceFrameKind",
    "decode_voice_binary_frame",
    "encode_voice_binary_frame",
]
