"""Provider-free prompt and streaming parser tests for Gate 1.8 Director mode."""

from __future__ import annotations

import json

import pytest
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
)
from murmur.live_scene.semantic_storyboard_director import (
    SemanticStoryboardDirectorStreamError,
    SemanticStoryboardDirectorStreamErrorCode,
    SemanticStoryboardDirectorStreamParser,
    build_semantic_storyboard_director_messages,
)


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


def _scene(
    problem: PairedProjectileComparisonSpecV1 | None = None,
) -> ProjectileStoryboardSemanticSceneStateV1:
    bound_problem = _problem() if problem is None else problem
    return ProjectileStoryboardSemanticSceneStateV1(
        revision=1,
        components=(
            ProjectileStoryboardStateV1(
                id="projectile-comparison",
                problemSpec=bound_problem,
            ),
        ),
        certificateHeadSha256="a" * 64,
    )


def _line(act: str, **fields: object) -> str:
    return json.dumps({"v": 1, "act": act, **fields}, separators=(",", ":"))


def test_prompt_is_deterministic_catalog_only_and_excludes_certificate_frontier() -> None:
    messages = build_semantic_storyboard_director_messages(
        "  Trace the low arc, then relate the ranges.  ",
        _problem(),
        _scene(),
    )
    repeated = build_semantic_storyboard_director_messages(
        "Trace the low arc, then relate the ranges.",
        _problem(),
        _scene(),
    )

    assert messages == repeated
    assert [message["role"] for message in messages] == ["system", "user"]
    combined = "\n".join(message["content"] for message in messages)
    assert "MAX_RECORD_COUNT:5" in combined
    assert '"act":"reveal"' in combined
    assert "range_formula" in combined
    assert "lower_angle" in combined
    assert "equal_range" in combined
    assert "lower_trajectory" in combined
    assert "component IDs" in combined
    assert "a" * 64 not in combined
    assert '"id":"projectile-comparison"' not in combined


def test_prompt_rejects_empty_oversized_unbound_and_mismatched_context() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        build_semantic_storyboard_director_messages(" ", _problem(), _scene())
    with pytest.raises(ValueError, match="exceeds"):
        build_semantic_storyboard_director_messages("x" * 2_001, _problem(), _scene())
    with pytest.raises(ValueError, match="requires the certified"):
        build_semantic_storyboard_director_messages(
            "Trace it.",
            _problem(),
            ProjectileStoryboardSemanticSceneStateV1(revision=0),
        )
    mismatch = PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 45))
    with pytest.raises(ValueError, match="must match"):
        build_semantic_storyboard_director_messages("Trace it.", mismatch, _scene())


def test_prompt_states_the_noncomplementary_concept_restriction() -> None:
    problem = PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 45))
    messages = build_semantic_storyboard_director_messages(
        "Compare the ranges.",
        problem,
        _scene(problem),
    )

    assert "complementary_angles is selectable only" in messages[0]["content"]


def test_parser_emits_each_complete_non_abstain_record_across_arbitrary_chunks() -> None:
    payload = "\n".join(
        (
            _line("trace", trajectoryId="lower_angle"),
            _line("reveal", conceptId="range_formula"),
            _line("trace", trajectoryId="higher_angle"),
        )
    )
    raw = payload.encode()
    parser = SemanticStoryboardDirectorStreamParser()
    emitted = []
    chunks = (raw[:1], raw[1:7], raw[7:23], raw[23:61], raw[61:])
    for chunk in chunks:
        emitted.extend(parser.feed(chunk))
    emitted.extend(parser.finish())

    assert [record.act for record in emitted] == ["trace", "reveal", "trace"]
    assert parser.frame_count == 3
    assert parser.closed


def test_valid_final_record_does_not_require_a_trailing_newline() -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    assert parser.feed(_line("trace", trajectoryId="lower_angle")) == ()

    emitted = parser.finish()

    assert len(emitted) == 1
    assert emitted[0].act == "trace"


def test_abstain_is_held_until_clean_eos_and_yields_no_early_record() -> None:
    parser = SemanticStoryboardDirectorStreamParser()

    assert parser.feed(_line("abstain", reasonCode="unsupported_intent") + "\n") == ()
    assert parser.frame_count == 1
    emitted = parser.finish()

    assert len(emitted) == 1
    assert emitted[0].act == "abstain"


@pytest.mark.parametrize(
    "payload",
    [
        _line("trace", trajectoryId="lower_angle")
        + "\n"
        + _line("abstain", reasonCode="no_forward_progress")
        + "\n",
        _line("abstain", reasonCode="unsupported_intent")
        + "\n"
        + _line("trace", trajectoryId="higher_angle")
        + "\n",
    ],
)
def test_abstain_must_be_the_only_record(payload: str) -> None:
    parser = SemanticStoryboardDirectorStreamParser()

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(payload)

    assert captured.value.code is SemanticStoryboardDirectorStreamErrorCode.INVALID_ABSTAIN_POSITION
    assert parser.closed


@pytest.mark.parametrize(
    ("payload", "at_finish", "expected"),
    [
        ("", True, SemanticStoryboardDirectorStreamErrorCode.EMPTY_STREAM),
        ("\n", False, SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON),
        (
            '{"v":1,"act":"trace","trajectoryId":"lower_angle","trajectoryId":"higher_angle"}\n',
            False,
            SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON,
        ),
        (
            '{"v":NaN,"act":"trace","trajectoryId":"lower_angle"}\n',
            False,
            SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON,
        ),
        (
            '{"v":1,"act":"trace","trajectoryId":"lower_angle","x":123}\n',
            False,
            SemanticStoryboardDirectorStreamErrorCode.INVALID_RECORD,
        ),
        (
            '{"v":1,"act":"trace","trajectoryId":',
            True,
            SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON,
        ),
    ],
)
def test_parser_rejects_empty_blank_duplicate_nonstandard_extra_and_incomplete_frames(
    payload: str,
    at_finish: bool,
    expected: SemanticStoryboardDirectorStreamErrorCode,
) -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        if at_finish:
            parser.feed(payload)
            parser.finish()
        else:
            parser.feed(payload)
    assert captured.value.code is expected


def test_parser_rejects_invalid_utf8_oversized_frame_and_sixth_record() -> None:
    invalid_utf8 = SemanticStoryboardDirectorStreamParser()
    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured_utf8:
        invalid_utf8.feed(b"\xff")
    assert captured_utf8.value.code is SemanticStoryboardDirectorStreamErrorCode.INVALID_UTF8

    oversized = SemanticStoryboardDirectorStreamParser(max_frame_bytes=32)
    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured_size:
        oversized.feed(_line("trace", trajectoryId="lower_angle"))
    assert captured_size.value.code is SemanticStoryboardDirectorStreamErrorCode.FRAME_TOO_LARGE

    six = SemanticStoryboardDirectorStreamParser()
    records = "\n".join(
        (
            _line("trace", trajectoryId="lower_angle"),
            _line("trace", trajectoryId="higher_angle"),
            _line("reveal", conceptId="range_formula"),
            _line("reveal", conceptId="complementary_angles"),
            _line(
                "relate",
                claimId="equal_range",
                evidenceIds=["range_formula", "complementary_angles"],
            ),
            _line(
                "relate",
                claimId="higher_apex",
                evidenceIds=["lower_trajectory", "higher_trajectory"],
            ),
        )
    )
    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured_limit:
        six.feed(records + "\n")
    assert (
        captured_limit.value.code is SemanticStoryboardDirectorStreamErrorCode.RECORD_LIMIT_EXCEEDED
    )


def test_invalid_utf8_tail_preserves_a_complete_record_from_the_same_chunk() -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    payload = _line("trace", trajectoryId="lower_angle").encode() + b"\n\xff\n"

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(payload)

    assert captured.value.code is SemanticStoryboardDirectorStreamErrorCode.INVALID_UTF8
    assert [record.act for record in captured.value.complete_prefix] == ["trace"]
    assert parser.closed


def test_deeply_nested_json_fails_closed_with_a_fixed_error() -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    deeply_nested = "[" * 1_000 + "0" + "]" * 1_000 + "\n"

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(deeply_nested)

    assert captured.value.code is SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON
    assert parser.closed


def test_failures_do_not_echo_provider_text_and_closed_parser_stays_closed() -> None:
    sentinel = "SECRET_PROVIDER_SENTINEL"
    parser = SemanticStoryboardDirectorStreamParser()
    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(sentinel + "\n")

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value)
    assert sentinel not in captured.value.repair_hint
    with pytest.raises(SemanticStoryboardDirectorStreamError) as closed:
        parser.finish()
    assert closed.value.code is SemanticStoryboardDirectorStreamErrorCode.PARSER_CLOSED

    aborted = SemanticStoryboardDirectorStreamParser()
    aborted.feed(_line("trace", trajectoryId="lower_angle")[:5])
    aborted.abort()
    with pytest.raises(SemanticStoryboardDirectorStreamError) as after_abort:
        aborted.feed(b"")
    assert after_abort.value.code is SemanticStoryboardDirectorStreamErrorCode.PARSER_CLOSED


def test_error_exposes_only_typed_complete_prefix_when_one_chunk_has_a_bad_tail() -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    payload = _line("trace", trajectoryId="lower_angle") + "\n" + "SECRET_MALFORMED_PROVIDER_TAIL\n"

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(payload)

    assert captured.value.code is SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON
    assert [record.act for record in captured.value.complete_prefix] == ["trace"]
    assert "SECRET_MALFORMED_PROVIDER_TAIL" not in repr(captured.value)


def test_error_does_not_repeat_records_emitted_by_an_earlier_chunk() -> None:
    parser = SemanticStoryboardDirectorStreamParser()
    emitted = parser.feed(_line("trace", trajectoryId="lower_angle") + "\n")

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed("SECRET_MALFORMED_PROVIDER_TAIL\n")

    assert [record.act for record in emitted] == ["trace"]
    assert captured.value.complete_prefix == ()
