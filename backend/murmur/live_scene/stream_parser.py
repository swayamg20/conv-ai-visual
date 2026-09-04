"""Incremental strict-NDJSON parser for model-authored scene patch drafts."""

from __future__ import annotations

import codecs
import json
from typing import Literal

from pydantic import ValidationError

from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES, ScenePatchDraft


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

ScenePatchStreamErrorCode = Literal[
    "invalid_utf8",
    "frame_too_large",
    "invalid_json",
    "invalid_patch",
    "parser_closed",
]

_DEFAULT_REPAIR_HINTS: dict[ScenePatchStreamErrorCode, str] = {
    "invalid_utf8": "invalid_utf8: output UTF-8 text only",
    "frame_too_large": "frame_too_large: reduce the operations in one patch",
    "invalid_json": "invalid_json: emit one complete JSON object per NDJSON line",
    "invalid_patch": "invalid_patch: follow the ScenePatch v1 schema exactly",
    "parser_closed": "parser_closed: restart with a fresh NDJSON stream",
}
_SCENE_BOUNDS_REPAIR_HINT = (
    "scene_bounds: resize or reposition nodes using stable IDs; keep every point inside "
    "800x600, x and y at least 0, x plus width at most 800, and y plus height at most 600"
)
_ALLOWED_REPAIR_HINTS = frozenset((*_DEFAULT_REPAIR_HINTS.values(), _SCENE_BOUNDS_REPAIR_HINT))
_RANGE_ERROR_TYPES = frozenset(
    {
        "finite_number",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
    }
)
_BOUNDED_NODE_FIELDS = frozenset({"height", "points", "width", "x", "y"})
_SCENE_NODE_KINDS = frozenset({"latex", "line", "path", "rect", "text"})


def _validation_repair_hint(exc: ValidationError) -> str:
    """Classify schema failures using only fixed hints, never model-authored values."""

    for error in exc.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = error.get("loc", ())
        error_type = error.get("type")
        if (
            not isinstance(location, tuple)
            or len(location) < 5
            or location[0] != "operations"
            or not isinstance(location[1], int)
            or location[1] < 0
            or location[2:4] != ("put", "node")
            or location[4] not in _SCENE_NODE_KINDS
        ):
            continue
        if error_type in _RANGE_ERROR_TYPES and any(
            part in _BOUNDED_NODE_FIELDS for part in location[5:]
        ):
            return _SCENE_BOUNDS_REPAIR_HINT
        if error_type == "value_error" and location[4:] == ("rect",):
            return _SCENE_BOUNDS_REPAIR_HINT
    return _DEFAULT_REPAIR_HINTS["invalid_patch"]


class ScenePatchStreamError(ValueError):
    """Bounded parser failure safe to classify without exposing model output."""

    def __init__(
        self,
        code: ScenePatchStreamErrorCode,
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


class ScenePatchStreamParser:
    """Reconstruct complete ScenePatchDraft records across arbitrary chunks.

    Both text chunks and byte chunks are accepted. Every chunk is routed through
    one strict incremental UTF-8 decoder so split multibyte characters behave
    exactly like provider text chunks. The parser becomes terminal after an
    error, :meth:`finish`, or :meth:`abort`.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_NDJSON_FRAME_BYTES) -> None:
        if isinstance(max_frame_bytes, bool) or not isinstance(max_frame_bytes, int):
            raise TypeError("max_frame_bytes must be an integer")
        if max_frame_bytes <= 0 or max_frame_bytes > MAX_NDJSON_FRAME_BYTES:
            raise ValueError(
                f"max_frame_bytes must be between 1 and {MAX_NDJSON_FRAME_BYTES}"
            )
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

    def feed(self, chunk: str | bytes) -> tuple[ScenePatchDraft, ...]:
        """Consume one provider chunk and return every completed patch frame."""

        self._require_open()
        if not isinstance(chunk, (str, bytes)):
            self._fail("invalid_patch", "scene stream chunks must be text or bytes")
        try:
            raw = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            decoded = self._decoder.decode(raw, final=False)
        except (UnicodeDecodeError, UnicodeEncodeError):
            self._fail("invalid_utf8", "scene stream was not valid UTF-8")
        return self._consume(decoded)

    def finish(self) -> tuple[ScenePatchDraft, ...]:
        """Flush UTF-8 state and accept one final frame without a newline."""

        self._require_open()
        try:
            decoded = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            self._fail("invalid_utf8", "scene stream ended with incomplete UTF-8")

        patches = list(self._consume(decoded))
        if self._buffer.strip():
            patches.append(self._parse_frame(self._buffer.removesuffix("\r")))
        self._buffer = ""
        self._closed = True
        return tuple(patches)

    def abort(self) -> None:
        """Discard an incomplete frame and make later chunks ineligible."""

        self._buffer = ""
        self._closed = True

    def _consume(self, decoded: str) -> tuple[ScenePatchDraft, ...]:
        pending = self._buffer + decoded
        self._buffer = ""
        patches: list[ScenePatchDraft] = []

        while True:
            newline = pending.find("\n")
            if newline < 0:
                self._assert_frame_size(pending)
                self._buffer = pending
                return tuple(patches)

            frame = pending[:newline]
            pending = pending[newline + 1 :]
            if frame.endswith("\r"):
                frame = frame[:-1]
            if not frame.strip():
                continue
            patches.append(self._parse_frame(frame))

    def _parse_frame(self, frame: str) -> ScenePatchDraft:
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
                "scene stream frame was not valid JSON",
                frame_number=next_frame,
            )
        if not isinstance(payload, dict):
            self._fail(
                "invalid_patch",
                "scene stream frame did not match ScenePatch v1",
                frame_number=next_frame,
            )
        if type(payload.get("v")) is not int or payload.get("v") != 1:
            self._fail(
                "invalid_patch",
                "scene stream frame did not match ScenePatch v1",
                frame_number=next_frame,
            )
        try:
            patch = ScenePatchDraft.model_validate(
                payload,
                by_alias=True,
                by_name=False,
            )
        except ValidationError as exc:
            self._fail(
                "invalid_patch",
                "scene stream frame did not match ScenePatch v1",
                frame_number=next_frame,
                repair_hint=_validation_repair_hint(exc),
            )
        self._frame_count = next_frame
        return patch

    def _assert_frame_size(self, frame: str) -> None:
        try:
            size = len(frame.encode("utf-8"))
        except UnicodeEncodeError:
            self._fail("invalid_utf8", "scene stream was not valid UTF-8")
        if size > self._max_frame_bytes:
            self._fail(
                "frame_too_large",
                f"scene stream frame exceeded {self._max_frame_bytes} bytes",
                frame_number=self._frame_count + 1,
            )

    def _require_open(self) -> None:
        if self._closed or self._failed:
            raise ScenePatchStreamError("parser_closed", "scene stream parser is closed")

    def _fail(
        self,
        code: ScenePatchStreamErrorCode,
        message: str,
        *,
        frame_number: int | None = None,
        repair_hint: str | None = None,
    ) -> None:
        self._buffer = ""
        self._failed = True
        self._closed = True
        error = ScenePatchStreamError(
            code,
            message,
            frame_number=frame_number,
            repair_hint=repair_hint,
        )
        # Provider output may appear in JSON/Pydantic exceptions. Suppress the active
        # exception context so a later traceback cannot disclose the rejected frame.
        raise error from None


# Explicit alias for callers that describe the wire format rather than its payload.
NDJSONScenePatchParser = ScenePatchStreamParser
