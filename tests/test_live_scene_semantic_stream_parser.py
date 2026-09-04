"""Tests for strict incremental parsing of model-authored teaching beats."""

from __future__ import annotations

import json
import traceback

import pytest
from murmur.live_scene.semantic_stream_parser import (
    TeachingBeatStreamError,
    TeachingBeatStreamParser,
)


def _beat_line(
    beat_id: str = "beat-one",
    narration: str = "Introduce café and angle ∠ABC.",
) -> str:
    return json.dumps(
        {
            "v": 1,
            "beatId": beat_id,
            "narration": narration,
            "act": "introduce",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": "areas",
                "revealThrough": "triangle",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_reconstructs_one_beat_across_every_utf8_byte_boundary() -> None:
    parser = TeachingBeatStreamParser()
    beats = []

    for value in (_beat_line() + "\n").encode():
        beats.extend(parser.feed(bytes([value])))
    beats.extend(parser.finish())

    assert [beat.beat_id for beat in beats] == ["beat-one"]
    assert beats[0].narration == "Introduce café and angle ∠ABC."
    assert parser.frame_count == 1
    assert parser.closed is True


def test_accepts_text_chunks_multiple_frames_crlf_blank_lines_and_unterminated_final_frame() -> (
    None
):
    parser = TeachingBeatStreamParser()
    first = _beat_line("beat-one")
    second = _beat_line("beat-two")
    third = _beat_line("beat-three")

    beats = list(parser.feed(f"\r\n{first}\r\n{second}\n\n{third[:17]}"))
    beats.extend(parser.feed(third[17:]))
    beats.extend(parser.finish())

    assert [beat.beat_id for beat in beats] == ["beat-one", "beat-two", "beat-three"]


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ("This is a teaching beat.\n", "invalid_json"),
        ("```json\n", "invalid_json"),
        ("{not-json}\n", "invalid_json"),
        ("[]\n", "invalid_beat"),
        (
            json.dumps(
                {
                    "v": 1,
                    "beatId": "beat-one",
                    "narration": "Introduce it.",
                    "act": "introduce",
                    "directive": {
                        "kind": "pythagorean_area_identity",
                        "id": "areas",
                        "revealThrough": "triangle",
                    },
                    "generation": 1,
                }
            )
            + "\n",
            "invalid_beat",
        ),
    ],
)
def test_non_ndjson_or_invalid_drafts_fail_closed(frame: str, code: str) -> None:
    parser = TeachingBeatStreamParser()

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(frame)

    assert captured.value.code == code
    assert captured.value.frame_number == 1
    assert parser.closed is True
    with pytest.raises(TeachingBeatStreamError, match="closed") as late:
        parser.feed(_beat_line("late") + "\n")
    assert late.value.code == "parser_closed"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(v=True),
        lambda payload: payload.update(v=1.0),
        lambda payload: payload.update(beat_id=payload.pop("beatId")),
        lambda payload: payload["directive"].update(
            reveal_through=payload["directive"].pop("revealThrough")
        ),
    ],
)
def test_wire_parser_requires_strict_version_and_camel_case_aliases(mutation) -> None:
    payload = json.loads(_beat_line())
    mutation(payload)
    parser = TeachingBeatStreamParser()

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    assert captured.value.code == "invalid_beat"


def test_rejects_duplicate_keys_and_nonstandard_json_constants() -> None:
    parser = TeachingBeatStreamParser()
    duplicate = _beat_line().replace('"v":1', '"v":1,"v":1', 1)

    with pytest.raises(TeachingBeatStreamError) as duplicate_error:
        parser.feed(duplicate + "\n")
    assert duplicate_error.value.code == "invalid_json"

    parser = TeachingBeatStreamParser()
    nonstandard = _beat_line().replace('"v":1', '"v":NaN', 1)
    with pytest.raises(TeachingBeatStreamError) as constant_error:
        parser.feed(nonstandard + "\n")
    assert constant_error.value.code == "invalid_json"


def test_error_after_valid_output_preserves_success_count_and_next_frame_number() -> None:
    parser = TeachingBeatStreamParser()
    assert [beat.beat_id for beat in parser.feed(_beat_line("beat-one") + "\n")] == ["beat-one"]

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed("not json\n")

    assert captured.value.frame_number == 2
    assert parser.frame_count == 1


def test_invalid_beat_exposes_only_a_fixed_bounded_hint_and_no_rejected_content() -> None:
    sentinel = "TOP-SECRET-SENTINEL"
    payload = json.loads(_beat_line(narration=sentinel))
    payload["directive"]["revealThrough"] = sentinel
    parser = TeachingBeatStreamParser()

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    rendered_traceback = "".join(
        traceback.format_exception(
            type(captured.value), captured.value, captured.value.__traceback__
        )
    )
    assert captured.value.repair_hint == ("invalid_beat: follow the TeachingBeat v1 schema exactly")
    assert len(captured.value.repair_hint) <= 320
    assert sentinel not in str(captured.value)
    assert sentinel not in rendered_traceback
    assert captured.value.__suppress_context__ is True


def test_frame_limit_is_measured_in_utf8_bytes() -> None:
    frame = _beat_line(narration="é" * 65)
    parser = TeachingBeatStreamParser(max_frame_bytes=len(frame) - 1)

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(frame + "\n")

    assert captured.value.code == "frame_too_large"
    assert captured.value.frame_number == 1


@pytest.mark.parametrize("limit", [0, -1, 65_537])
def test_rejects_out_of_range_frame_limits(limit: int) -> None:
    with pytest.raises(ValueError):
        TeachingBeatStreamParser(max_frame_bytes=limit)


@pytest.mark.parametrize("limit", [True, 1.5, "128"])
def test_rejects_non_integer_frame_limits(limit: object) -> None:
    with pytest.raises(TypeError):
        TeachingBeatStreamParser(max_frame_bytes=limit)  # type: ignore[arg-type]


def test_finish_rejects_an_incomplete_utf8_codepoint_and_closes_parser() -> None:
    parser = TeachingBeatStreamParser()
    parser.feed(b"\xe2\x88")

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.finish()

    assert captured.value.code == "invalid_utf8"
    assert parser.closed is True


def test_abort_discards_partial_frame_and_is_terminal() -> None:
    parser = TeachingBeatStreamParser()
    parser.feed(_beat_line()[:20])
    parser.abort()

    assert parser.frame_count == 0
    assert parser.closed is True
    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.finish()
    assert captured.value.code == "parser_closed"


def test_finish_without_a_frame_is_terminal() -> None:
    parser = TeachingBeatStreamParser()

    assert parser.finish() == ()
    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(_beat_line() + "\n")
    assert captured.value.code == "parser_closed"


def test_rejects_non_text_chunks() -> None:
    parser = TeachingBeatStreamParser()

    with pytest.raises(TeachingBeatStreamError) as captured:
        parser.feed(123)  # type: ignore[arg-type]

    assert captured.value.code == "invalid_beat"


def test_error_rejects_custom_repair_hints() -> None:
    with pytest.raises(ValueError, match="fixed internal value"):
        TeachingBeatStreamError("invalid_json", "safe", repair_hint="model supplied")
