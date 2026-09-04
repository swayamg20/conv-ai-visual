"""Incremental strict-NDJSON parsing for model-authored teaching beats."""

from __future__ import annotations

import codecs
import json
from typing import Literal, Never

from pydantic import ValidationError

from murmur.live_scene.contracts import LIVE_SCENE_SCHEMA_VERSION, MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.semantic_contracts import TeachingBeatDraft


class _DuplicateJsonKey(ValueError):
    pass


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key}")
        result[key] = value
    return result


TeachingBeatStreamErrorCode = Literal[
    "invalid_utf8",
    "frame_too_large",
    "invalid_json",
    "invalid_beat",
    "parser_closed",
]

_DEFAULT_REPAIR_HINTS: dict[TeachingBeatStreamErrorCode, str] = {
    "invalid_utf8": "invalid_utf8: output UTF-8 text only",
    "frame_too_large": "frame_too_large: shorten the teaching beat narration",
    "invalid_json": "invalid_json: emit one complete JSON object per NDJSON line",
    "invalid_beat": "invalid_beat: follow the TeachingBeat v1 schema exactly",
    "parser_closed": "parser_closed: restart with a fresh NDJSON stream",
}
_ALLOWED_REPAIR_HINTS = frozenset(_DEFAULT_REPAIR_HINTS.values())


class TeachingBeatStreamError(ValueError):
    """Bounded parser failure that never carries rejected provider output."""

    def __init__(
        self,
        code: TeachingBeatStreamErrorCode,
        message: str,
        *,
        frame_number: int | None = None,
        repair_hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.frame_number = frame_number
        selected_repair_hint = repair_hint or _DEFAULT_REPAIR_HINTS[code]
        if selected_repair_hint not in _ALLOWED_REPAIR_HINTS:
            raise ValueError("repair_hint must be a fixed internal value")
        self.repair_hint = selected_repair_hint


class TeachingBeatStreamParser:
    """Reconstruct strict ``TeachingBeatDraft`` records across arbitrary chunks.

    Text and byte chunks share one incremental UTF-8 decoder. The parser becomes
    terminal after an error, :meth:`finish`, or :meth:`abort`, matching the raw
    scene-patch parser's lifecycle without coupling the two authoring contracts.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_NDJSON_FRAME_BYTES) -> None:
        if isinstance(max_frame_bytes, bool) or not isinstance(max_frame_bytes, int):
            raise TypeError("max_frame_bytes must be an integer")
        if max_frame_bytes <= 0 or max_frame_bytes > MAX_NDJSON_FRAME_BYTES:
            raise ValueError(f"max_frame_bytes must be between 1 and {MAX_NDJSON_FRAME_BYTES}")
        self._max_frame_bytes = max_frame_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buffer = ""
        self._frame_count = 0
        self._closed = False
        self._failed = False

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def closed(self) -> bool:
        return self._closed

    def feed(self, chunk: str | bytes) -> tuple[TeachingBeatDraft, ...]:
        """Consume one provider-neutral chunk and return completed beat frames."""

        self._require_open()
        if not isinstance(chunk, (str, bytes)):
            self._fail("invalid_beat", "teaching beat stream chunks must be text or bytes")
        try:
            raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            decoded = self._decoder.decode(raw, final=False)
        except (UnicodeDecodeError, UnicodeEncodeError):
            self._fail("invalid_utf8", "teaching beat stream was not valid UTF-8")
        return self._consume(decoded)

    def finish(self) -> tuple[TeachingBeatDraft, ...]:
        """Flush UTF-8 state and accept one final frame without a newline."""

        self._require_open()
        try:
            decoded = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            self._fail("invalid_utf8", "teaching beat stream ended with incomplete UTF-8")

        beats = list(self._consume(decoded))
        if self._buffer.strip():
            beats.append(self._parse_frame(self._buffer.removesuffix("\r")))
        self._buffer = ""
        self._closed = True
        return tuple(beats)

    def abort(self) -> None:
        """Discard an incomplete frame and reject all later chunks."""

        self._buffer = ""
        self._closed = True

    def _consume(self, decoded: str) -> tuple[TeachingBeatDraft, ...]:
        pending = self._buffer + decoded
        self._buffer = ""
        beats: list[TeachingBeatDraft] = []

        while True:
            newline = pending.find("\n")
            if newline < 0:
                self._assert_frame_size(pending)
                self._buffer = pending
                return tuple(beats)

            frame = pending[:newline]
            pending = pending[newline + 1 :]
            if frame.endswith("\r"):
                frame = frame[:-1]
            if not frame.strip():
                continue
            beats.append(self._parse_frame(frame))

    def _parse_frame(self, frame: str) -> TeachingBeatDraft:
        self._assert_frame_size(frame)
        next_frame = self._frame_count + 1
        try:
            payload = json.loads(
                frame,
                parse_constant=_reject_nonstandard_constant,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._fail(
                "invalid_json",
                "teaching beat stream frame was not valid JSON",
                frame_number=next_frame,
            )
        if not isinstance(payload, dict):
            self._invalid_beat(next_frame)
        if type(payload.get("v")) is not int or payload.get("v") != LIVE_SCENE_SCHEMA_VERSION:
            self._invalid_beat(next_frame)
        try:
            beat = TeachingBeatDraft.model_validate(
                payload,
                by_alias=True,
                by_name=False,
            )
        except ValidationError:
            self._invalid_beat(next_frame)
        self._frame_count = next_frame
        return beat

    def _invalid_beat(self, frame_number: int) -> Never:
        self._fail(
            "invalid_beat",
            "teaching beat stream frame did not match TeachingBeat v1",
            frame_number=frame_number,
        )

    def _assert_frame_size(self, frame: str) -> None:
        try:
            size = len(frame.encode("utf-8"))
        except UnicodeEncodeError:
            self._fail("invalid_utf8", "teaching beat stream was not valid UTF-8")
        if size > self._max_frame_bytes:
            self._fail(
                "frame_too_large",
                f"teaching beat stream frame exceeded {self._max_frame_bytes} bytes",
                frame_number=self._frame_count + 1,
            )

    def _require_open(self) -> None:
        if self._closed or self._failed:
            raise TeachingBeatStreamError(
                "parser_closed",
                "teaching beat stream parser is closed",
            )

    def _fail(
        self,
        code: TeachingBeatStreamErrorCode,
        message: str,
        *,
        frame_number: int | None = None,
        repair_hint: str | None = None,
    ) -> Never:
        self._buffer = ""
        self._failed = True
        self._closed = True
        error = TeachingBeatStreamError(
            code,
            message,
            frame_number=frame_number,
            repair_hint=repair_hint,
        )
        # JSON and Pydantic exceptions may contain provider output. Suppress the
        # active exception context so rejected teaching content cannot leak later.
        raise error from None


NDJSONTeachingBeatParser = TeachingBeatStreamParser

__all__ = [
    "NDJSONTeachingBeatParser",
    "TeachingBeatStreamError",
    "TeachingBeatStreamErrorCode",
    "TeachingBeatStreamParser",
]
