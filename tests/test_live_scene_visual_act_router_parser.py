"""Focused tests for strict incremental visual-act decision parsing."""

from __future__ import annotations

import json
import traceback

import pytest
from murmur.live_scene.semantic_contracts import (
    AbstainVisualDecision,
    ContinueVisualDecision,
    StartVisualDecision,
)
from murmur.live_scene.semantic_stream_parser import (
    VisualActDecisionStreamError,
    VisualActDecisionStreamParser,
)


def _line(decision: str) -> str:
    payloads = {
        "start_visual": {
            "v": 1,
            "decision": "start_visual",
            "targetStage": "triangle",
        },
        "continue_visual": {
            "v": 1,
            "decision": "continue_visual",
            "componentId": "areas",
            "targetStage": "areas",
        },
        "abstain": {
            "v": 1,
            "decision": "abstain",
            "reasonCode": "unsupported_intent",
        },
    }
    return json.dumps(payloads[decision], separators=(",", ":"))


def test_reconstructs_every_decision_variant_across_utf8_byte_boundaries() -> None:
    parser = VisualActDecisionStreamParser()
    stream = "\n".join((_line("start_visual"), _line("continue_visual"), _line("abstain")))
    decisions = []

    for value in (stream + "\n").encode():
        decisions.extend(parser.feed(bytes([value])))
    decisions.extend(parser.finish())

    assert [type(decision) for decision in decisions] == [
        StartVisualDecision,
        ContinueVisualDecision,
        AbstainVisualDecision,
    ]
    assert [decision.decision for decision in decisions] == [
        "start_visual",
        "continue_visual",
        "abstain",
    ]
    assert parser.frame_count == 3
    assert parser.closed is True


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ("```json\n", "invalid_json"),
        ("<svg><script>alert(1)</script></svg>\n", "invalid_json"),
        ("[]\n", "invalid_decision"),
        (
            json.dumps({**json.loads(_line("start_visual")), "style": "red"}) + "\n",
            "invalid_decision",
        ),
        (json.dumps({**json.loads(_line("abstain")), "v": True}) + "\n", "invalid_decision"),
    ],
)
def test_non_ndjson_and_invalid_decisions_fail_closed(frame: str, code: str) -> None:
    parser = VisualActDecisionStreamParser()

    with pytest.raises(VisualActDecisionStreamError) as captured:
        parser.feed(frame)

    assert captured.value.code == code
    assert captured.value.frame_number == 1
    assert parser.closed is True


def test_invalid_decision_exposes_only_a_fixed_hint_without_provider_content() -> None:
    sentinel = "TOP-SECRET-ROUTER-SENTINEL"
    payload = json.loads(_line("start_visual"))
    payload["targetStage"] = sentinel
    parser = VisualActDecisionStreamParser()

    with pytest.raises(VisualActDecisionStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    rendered_traceback = "".join(
        traceback.format_exception(
            type(captured.value), captured.value, captured.value.__traceback__
        )
    )
    assert captured.value.repair_hint == (
        "invalid_decision: follow the VisualActDecision v1 schema exactly"
    )
    assert sentinel not in str(captured.value)
    assert sentinel not in rendered_traceback
    assert captured.value.__suppress_context__ is True


def test_frame_limit_uses_a_decision_specific_fixed_hint() -> None:
    frame = _line("start_visual")
    parser = VisualActDecisionStreamParser(max_frame_bytes=len(frame) - 1)

    with pytest.raises(VisualActDecisionStreamError) as captured:
        parser.feed(frame + "\n")

    assert captured.value.code == "frame_too_large"
    assert captured.value.repair_hint == "frame_too_large: shorten the visual-act decision"
    assert "narration" not in captured.value.repair_hint


def test_direct_decision_error_uses_decision_specific_fixed_hint() -> None:
    error = VisualActDecisionStreamError("frame_too_large", "safe")

    assert error.repair_hint == "frame_too_large: shorten the visual-act decision"


def test_decision_parser_rejects_custom_repair_hints() -> None:
    with pytest.raises(ValueError, match="fixed internal value"):
        VisualActDecisionStreamError(
            "invalid_decision",
            "safe",
            repair_hint="model supplied",
        )
