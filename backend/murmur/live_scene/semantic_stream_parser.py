"""Incremental strict-NDJSON parsing for model-authored teaching beats."""

from __future__ import annotations

import codecs
import json
from typing import Generic, Literal, Never, TypeVar

from pydantic import TypeAdapter, ValidationError

from murmur.live_scene.contracts import LIVE_SCENE_SCHEMA_VERSION, MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.semantic_contracts import (
    TEACHING_BEAT_DRAFT_ADAPTER,
    VISUAL_ACT_DECISION_ADAPTER,
    TeachingBeatDraft,
    VisualActDecision,
)

_RecordT = TypeVar("_RecordT")


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
VisualActDecisionStreamErrorCode = Literal[
    "invalid_utf8",
    "frame_too_large",
    "invalid_json",
    "invalid_decision",
    "parser_closed",
]
SemanticModelStreamErrorCode = TeachingBeatStreamErrorCode | VisualActDecisionStreamErrorCode

_DEFAULT_REPAIR_HINTS: dict[SemanticModelStreamErrorCode, str] = {
    "invalid_utf8": "invalid_utf8: output UTF-8 text only",
    "frame_too_large": "frame_too_large: shorten the teaching beat narration",
    "invalid_json": "invalid_json: emit one complete JSON object per NDJSON line",
    "invalid_beat": "invalid_beat: follow the TeachingBeat v1 schema exactly",
    "invalid_decision": "invalid_decision: follow the VisualActDecision v1 schema exactly",
    "parser_closed": "parser_closed: restart with a fresh NDJSON stream",
}
_VISUAL_DECISION_FRAME_TOO_LARGE_HINT = "frame_too_large: shorten the visual-act decision"
_ALLOWED_REPAIR_HINTS = frozenset(
    (*_DEFAULT_REPAIR_HINTS.values(), _VISUAL_DECISION_FRAME_TOO_LARGE_HINT)
)


class SemanticModelStreamError(ValueError):
    """Bounded parser failure that never carries rejected provider output."""

    def __init__(
        self,
        code: SemanticModelStreamErrorCode,
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


class TeachingBeatStreamError(SemanticModelStreamError):
    """Compatibility error for model-authored teaching-beat streams."""


class VisualActDecisionStreamError(SemanticModelStreamError):
    """Error for model-authored visual-act decision streams."""


class _StrictSemanticStreamParser(Generic[_RecordT]):
    """Reconstruct one strict semantic record type across arbitrary chunks.

    Text and byte chunks share one incremental UTF-8 decoder. The parser becomes
    terminal after an error, :meth:`finish`, or :meth:`abort`, matching the raw
    scene-patch parser's lifecycle without coupling the two authoring contracts.
    """

    def __init__(
        self,
        *,
        adapter: TypeAdapter[_RecordT],
        error_type: type[SemanticModelStreamError],
        invalid_record_code: Literal["invalid_beat", "invalid_decision"],
        stream_label: str,
        schema_label: str,
        frame_too_large_hint: str,
        max_frame_bytes: int = MAX_NDJSON_FRAME_BYTES,
    ) -> None:
        if isinstance(max_frame_bytes, bool) or not isinstance(max_frame_bytes, int):
            raise TypeError("max_frame_bytes must be an integer")
        if max_frame_bytes <= 0 or max_frame_bytes > MAX_NDJSON_FRAME_BYTES:
            raise ValueError(f"max_frame_bytes must be between 1 and {MAX_NDJSON_FRAME_BYTES}")
        self._adapter = adapter
        self._error_type = error_type
        self._invalid_record_code = invalid_record_code
        self._stream_label = stream_label
        self._schema_label = schema_label
        self._frame_too_large_hint = frame_too_large_hint
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

    def feed(self, chunk: str | bytes) -> tuple[_RecordT, ...]:
        """Consume one provider-neutral chunk and return completed records."""

        self._require_open()
        if not isinstance(chunk, (str, bytes)):
            self._fail(
                self._invalid_record_code,
                f"{self._stream_label} stream chunks must be text or bytes",
            )
        try:
            raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            decoded = self._decoder.decode(raw, final=False)
        except (UnicodeDecodeError, UnicodeEncodeError):
            self._fail("invalid_utf8", f"{self._stream_label} stream was not valid UTF-8")
        return self._consume(decoded)

    def finish(self) -> tuple[_RecordT, ...]:
        """Flush UTF-8 state and accept one final frame without a newline."""

        self._require_open()
        try:
            decoded = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            self._fail(
                "invalid_utf8",
                f"{self._stream_label} stream ended with incomplete UTF-8",
            )

        records = list(self._consume(decoded))
        if self._buffer.strip():
            records.append(self._parse_frame(self._buffer.removesuffix("\r")))
        self._buffer = ""
        self._closed = True
        return tuple(records)

    def abort(self) -> None:
        """Discard an incomplete frame and reject all later chunks."""

        self._buffer = ""
        self._closed = True

    def _consume(self, decoded: str) -> tuple[_RecordT, ...]:
        pending = self._buffer + decoded
        self._buffer = ""
        records: list[_RecordT] = []

        while True:
            newline = pending.find("\n")
            if newline < 0:
                self._assert_frame_size(pending)
                self._buffer = pending
                return tuple(records)

            frame = pending[:newline]
            pending = pending[newline + 1 :]
            if frame.endswith("\r"):
                frame = frame[:-1]
            if not frame.strip():
                continue
            records.append(self._parse_frame(frame))

    def _parse_frame(self, frame: str) -> _RecordT:
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
                f"{self._stream_label} stream frame was not valid JSON",
                frame_number=next_frame,
            )
        if not isinstance(payload, dict):
            self._invalid_record(next_frame)
        if type(payload.get("v")) is not int or payload.get("v") != LIVE_SCENE_SCHEMA_VERSION:
            self._invalid_record(next_frame)
        try:
            record = self._adapter.validate_python(
                payload,
                by_alias=True,
                by_name=False,
            )
        except ValidationError:
            self._invalid_record(next_frame)
        self._frame_count = next_frame
        return record

    def _invalid_record(self, frame_number: int) -> Never:
        self._fail(
            self._invalid_record_code,
            f"{self._stream_label} stream frame did not match {self._schema_label}",
            frame_number=frame_number,
        )

    def _assert_frame_size(self, frame: str) -> None:
        try:
            size = len(frame.encode("utf-8"))
        except UnicodeEncodeError:
            self._fail("invalid_utf8", f"{self._stream_label} stream was not valid UTF-8")
        if size > self._max_frame_bytes:
            self._fail(
                "frame_too_large",
                f"{self._stream_label} stream frame exceeded {self._max_frame_bytes} bytes",
                frame_number=self._frame_count + 1,
                repair_hint=self._frame_too_large_hint,
            )

    def _require_open(self) -> None:
        if self._closed or self._failed:
            raise self._error_type(
                "parser_closed",
                f"{self._stream_label} stream parser is closed",
            )

    def _fail(
        self,
        code: SemanticModelStreamErrorCode,
        message: str,
        *,
        frame_number: int | None = None,
        repair_hint: str | None = None,
    ) -> Never:
        self._buffer = ""
        self._failed = True
        self._closed = True
        error = self._error_type(
            code,
            message,
            frame_number=frame_number,
            repair_hint=repair_hint,
        )
        # JSON and Pydantic exceptions may contain provider output. Suppress the
        # active exception context so rejected model content cannot leak later.
        raise error from None


class TeachingBeatStreamParser(_StrictSemanticStreamParser[TeachingBeatDraft]):
    """Reconstruct strict ``TeachingBeatDraft`` records across arbitrary chunks."""

    def __init__(self, *, max_frame_bytes: int = MAX_NDJSON_FRAME_BYTES) -> None:
        super().__init__(
            adapter=TEACHING_BEAT_DRAFT_ADAPTER,
            error_type=TeachingBeatStreamError,
            invalid_record_code="invalid_beat",
            stream_label="teaching beat",
            schema_label="TeachingBeat v1",
            frame_too_large_hint=_DEFAULT_REPAIR_HINTS["frame_too_large"],
            max_frame_bytes=max_frame_bytes,
        )


class VisualActDecisionStreamParser(_StrictSemanticStreamParser[VisualActDecision]):
    """Reconstruct strict ``VisualActDecision`` records across arbitrary chunks."""

    def __init__(self, *, max_frame_bytes: int = MAX_NDJSON_FRAME_BYTES) -> None:
        super().__init__(
            adapter=VISUAL_ACT_DECISION_ADAPTER,
            error_type=VisualActDecisionStreamError,
            invalid_record_code="invalid_decision",
            stream_label="visual-act decision",
            schema_label="VisualActDecision v1",
            frame_too_large_hint=_VISUAL_DECISION_FRAME_TOO_LARGE_HINT,
            max_frame_bytes=max_frame_bytes,
        )


NDJSONTeachingBeatParser = TeachingBeatStreamParser

__all__ = [
    "NDJSONTeachingBeatParser",
    "SemanticModelStreamError",
    "TeachingBeatStreamError",
    "TeachingBeatStreamErrorCode",
    "TeachingBeatStreamParser",
    "VisualActDecisionStreamError",
    "VisualActDecisionStreamErrorCode",
    "VisualActDecisionStreamParser",
]
