"""Provider-free prompt and streaming parser tests for Gate 1.8 Director mode."""

from __future__ import annotations

import json
from itertools import combinations

import pytest
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    TraceStoryboardRecordV1,
)
from murmur.live_scene.semantic_storyboard_director import (
    SemanticStoryboardDirectorStreamError,
    SemanticStoryboardDirectorStreamErrorCode,
    SemanticStoryboardDirectorStreamParser,
    build_semantic_storyboard_director_messages,
)
from murmur.live_scene.semantic_storyboard_routing import (
    SemanticStoryboardRoutingError,
    route_semantic_storyboard_record,
)


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


def _scene(
    problem: PairedProjectileComparisonSpecV1 | None = None,
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...] = (),
) -> ProjectileStoryboardSemanticSceneStateV1:
    bound_problem = _problem() if problem is None else problem
    return ProjectileStoryboardSemanticSceneStateV1(
        revision=1 + len(records),
        components=(
            ProjectileStoryboardStateV1(
                id="projectile-comparison",
                problemSpec=bound_problem,
                acceptedRecords=records,
            ),
        ),
        certificateHeadSha256="a" * 64,
    )


def _line(act: str, **fields: object) -> str:
    return json.dumps({"v": 1, "act": act, **fields}, separators=(",", ":"))


def _section_json(content: str, marker: str) -> object:
    lines = content.splitlines()
    return json.loads(lines[lines.index(marker) + 1])


def _accepted_record(act: str, **fields: object) -> AcceptedSemanticStoryboardRecordV1:
    record = SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python({"v": 1, "act": act, **fields})
    assert record.act != "abstain"
    return record


def _catalog_variants(
    problem: PairedProjectileComparisonSpecV1,
) -> tuple[AcceptedSemanticStoryboardRecordV1, ...]:
    variants = [_accepted_record("reveal", conceptId="range_formula")]
    if problem.has_complementary_angles:
        variants.append(_accepted_record("reveal", conceptId="complementary_angles"))
    variants.extend(
        (
            _accepted_record("trace", trajectoryId="lower_angle"),
            _accepted_record("trace", trajectoryId="higher_angle"),
        )
    )
    if problem.has_complementary_angles:
        variants.extend(
            (
                _accepted_record(
                    "relate",
                    claimId="equal_range",
                    evidenceIds=["lower_trajectory", "higher_trajectory"],
                ),
                _accepted_record(
                    "relate",
                    claimId="equal_range",
                    evidenceIds=["range_formula", "complementary_angles"],
                ),
            )
        )
    else:
        variants.extend(
            (
                _accepted_record(
                    "relate",
                    claimId="unequal_range",
                    evidenceIds=["lower_trajectory", "higher_trajectory"],
                ),
                _accepted_record(
                    "relate",
                    claimId="unequal_range",
                    evidenceIds=["range_formula"],
                ),
            )
        )
    variants.extend(
        (
            _accepted_record(
                "relate",
                claimId="higher_apex",
                evidenceIds=["lower_trajectory", "higher_trajectory"],
            ),
            _accepted_record(
                "relate",
                claimId="longer_flight",
                evidenceIds=["lower_trajectory", "higher_trajectory"],
            ),
        )
    )
    return tuple(variants)


def _record_effect_id(record: AcceptedSemanticStoryboardRecordV1) -> str:
    canonical = record.model_dump(mode="json", by_alias=True)
    target = canonical.get("conceptId") or canonical.get("trajectoryId") or canonical["claimId"]
    return f"{record.act}:{target}"


def _visible_evidence(
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...],
) -> tuple[str, ...]:
    produced: set[str] = set()
    for record in records:
        canonical = record.model_dump(mode="json", by_alias=True)
        if canonical.get("conceptId") in {"range_formula", "complementary_angles"}:
            produced.add(canonical["conceptId"])
        if canonical.get("trajectoryId") == "lower_angle":
            produced.add("lower_trajectory")
        if canonical.get("trajectoryId") == "higher_angle":
            produced.add("higher_trajectory")
    return tuple(
        evidence
        for evidence in (
            "lower_trajectory",
            "higher_trajectory",
            "range_formula",
            "complementary_angles",
        )
        if evidence in produced
    )


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


def test_prompt_requires_request_fidelity_before_record_selection() -> None:
    system = build_semantic_storyboard_director_messages(
        "Make the explanation clearer.",
        _problem(),
        _scene(),
    )[0]["content"]

    assert system.index("REQUEST FIDELITY AND ABSTENTION") < system.index("OUTPUT CONTRACT")
    assert "Select only the new semantic teaching beats" in system
    assert "strictly necessary to answer" in system
    assert "Never substitute a generic lesson" in system
    assert "unrequested catalog effect" in system
    assert "If any essential part of the request conflicts" in system
    assert 'Use "ambiguous_intent" when no unique supported teaching effect' in system
    assert 'Use "already_present" when every clearly requested effect is already accepted' in system
    assert 'Use "no_forward_progress" only when a clear supported request' in system


def test_prompt_defines_the_supported_world_and_each_unsupported_boundary() -> None:
    system = build_semantic_storyboard_director_messages(
        "Compare the bound trajectories.",
        _problem(),
        _scene(),
    )[0]["content"]

    assert "both projectiles launch and land at the same ground height" in system
    assert "fixed gravity" in system
    assert "no wind, drag, or extra forces" in system
    assert 'Use "unsupported_problem" when the request replaces the bound speed' in system
    assert 'Use "unsupported_initial_condition" for a different launch height' in system
    assert 'Use "unsupported_physics" for wind, drag, changed gravity' in system
    assert 'Use "unsupported_intent" for raw drawing, coordinates, SVG' in system


def test_prompt_treats_accepted_records_as_visible_relation_evidence() -> None:
    system = build_semantic_storyboard_director_messages(
        "Use the visible paths to compare their apexes.",
        _problem(),
        _scene(),
    )[0]["content"]

    assert "Accepted records are already visible and may satisfy evidence" in system
    assert "lower_angle -> lower_trajectory" in system
    assert "higher_angle -> higher_trajectory" in system
    assert "Emit each request-serving prerequisite as its own preceding atomic record" in system


def test_affordance_manifest_derives_visible_evidence_from_the_certified_frontier() -> None:
    problem = _problem()
    lower = TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="lower_angle")
    higher = TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="higher_angle")
    scene = _scene(problem, (lower, higher))
    user = build_semantic_storyboard_director_messages(
        "Use both visible paths to relate the higher apex.",
        problem,
        scene,
    )[1]["content"]
    manifest = _section_json(user, "CURRENT_STORYBOARD_AFFORDANCES_JSON:")

    assert manifest["supportedWorld"] == {
        "boundProblemImmutable": True,
        "fixedGravity": True,
        "noDrag": True,
        "noExtraForces": True,
        "noWind": True,
        "sameLaunchAndLandingGroundHeight": True,
    }
    assert manifest["acceptedEffectIds"] == ["trace:lower_angle", "trace:higher_angle"]
    assert manifest["visibleEvidenceIds"] == ["lower_trajectory", "higher_trajectory"]
    variants = manifest["unusedApplicableRecordVariants"]
    apex = next(item for item in variants if item["record"].get("claimId") == "higher_apex")
    assert apex == {
        "missingEvidenceIds": [],
        "readyNow": True,
        "record": {
            "act": "relate",
            "claimId": "higher_apex",
            "evidenceIds": ["lower_trajectory", "higher_trajectory"],
            "v": 1,
        },
    }
    assert all(item["record"].get("trajectoryId") is None for item in variants)


def test_affordance_manifest_excludes_inapplicable_and_accepted_effects() -> None:
    problem = PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 45))
    lower = TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="lower_angle")
    user = build_semantic_storyboard_director_messages(
        "Continue the bound comparison.",
        problem,
        _scene(problem, (lower,)),
    )[1]["content"]
    manifest = _section_json(user, "CURRENT_STORYBOARD_AFFORDANCES_JSON:")
    records = [item["record"] for item in manifest["unusedApplicableRecordVariants"]]

    assert not any(record.get("trajectoryId") == "lower_angle" for record in records)
    assert not any(record.get("conceptId") == "complementary_angles" for record in records)
    assert not any(record.get("claimId") == "equal_range" for record in records)
    assert sum(record.get("claimId") == "unequal_range" for record in records) == 2


def test_affordance_manifest_marks_exact_missing_evidence() -> None:
    user = build_semantic_storyboard_director_messages(
        "Relate the higher apex after showing both paths.",
        _problem(),
        _scene(),
    )[1]["content"]
    manifest = _section_json(user, "CURRENT_STORYBOARD_AFFORDANCES_JSON:")
    variants = manifest["unusedApplicableRecordVariants"]
    apex = next(item for item in variants if item["record"].get("claimId") == "higher_apex")

    assert apex["readyNow"] is False
    assert apex["missingEvidenceIds"] == ["lower_trajectory", "higher_trajectory"]


def test_every_ready_manifest_record_routes_from_the_exact_frontier() -> None:
    problem = _problem()
    lower = TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="lower_angle")
    higher = TraceStoryboardRecordV1(v=1, act="trace", trajectoryId="higher_angle")
    scene = _scene(problem, (lower, higher))
    user = build_semantic_storyboard_director_messages(
        "Continue with a supported effect.",
        problem,
        scene,
    )[1]["content"]
    manifest = _section_json(user, "CURRENT_STORYBOARD_AFFORDANCES_JSON:")
    ready_records = [
        SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python(item["record"])
        for item in manifest["unusedApplicableRecordVariants"]
        if item["readyNow"]
    ]

    assert ready_records
    for record in ready_records:
        route_semantic_storyboard_record(
            record,
            problem_spec=problem,
            semantic_scene=scene,
        )


def test_affordance_manifest_is_independent_of_prompt_wording() -> None:
    first = build_semantic_storyboard_director_messages(
        "Trace the lower path.",
        _problem(),
        _scene(),
    )[1]["content"]
    second = build_semantic_storyboard_director_messages(
        "Show the formula.",
        _problem(),
        _scene(),
    )[1]["content"]

    assert _section_json(first, "CURRENT_STORYBOARD_AFFORDANCES_JSON:") == _section_json(
        second, "CURRENT_STORYBOARD_AFFORDANCES_JSON:"
    )
    system = build_semantic_storyboard_director_messages(
        "Explain the bound comparison.",
        _problem(),
        _scene(),
    )[0]["content"]
    assert "Array order is canonical serialization only" in system
    assert "no priority, recommendation, or default lesson order" in system
    assert "Each producer must directly serve the user's clear supported intent" in system


def test_affordance_manifest_is_exact_across_every_reachable_frontier() -> None:
    frontier_count = 0

    for speed in (20, 25, 30):
        for angles in combinations((30, 45, 60), 2):
            problem = PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)
            catalog = _catalog_variants(problem)

            def walk(
                accepted: tuple[AcceptedSemanticStoryboardRecordV1, ...],
                *,
                problem: PairedProjectileComparisonSpecV1 = problem,
                catalog: tuple[AcceptedSemanticStoryboardRecordV1, ...] = catalog,
            ) -> None:
                nonlocal frontier_count
                scene = _scene(problem, accepted)
                content = build_semantic_storyboard_director_messages(
                    "Continue the supported explanation.",
                    problem,
                    scene,
                )[1]["content"]
                manifest = _section_json(content, "CURRENT_STORYBOARD_AFFORDANCES_JSON:")
                accepted_effects = tuple(_record_effect_id(record) for record in accepted)
                accepted_effect_set = frozenset(accepted_effects)
                visible = _visible_evidence(accepted)
                expected_variants: list[dict[str, object]] = []
                ready_records: list[AcceptedSemanticStoryboardRecordV1] = []

                for candidate in catalog:
                    if _record_effect_id(candidate) in accepted_effect_set:
                        continue
                    canonical = candidate.model_dump(mode="json", by_alias=True)
                    required = tuple(canonical.get("evidenceIds", ()))
                    missing = [evidence for evidence in required if evidence not in visible]
                    expected_variants.append(
                        {
                            "record": canonical,
                            "readyNow": not missing,
                            "missingEvidenceIds": missing,
                        }
                    )
                    try:
                        route_semantic_storyboard_record(
                            candidate,
                            problem_spec=problem,
                            semantic_scene=scene,
                        )
                    except SemanticStoryboardRoutingError:
                        assert missing
                    else:
                        assert not missing
                        ready_records.append(candidate)

                assert manifest["acceptedEffectIds"] == list(accepted_effects)
                assert manifest["visibleEvidenceIds"] == list(visible)
                assert manifest["unusedApplicableRecordVariants"] == expected_variants
                frontier_count += 1
                for candidate in ready_records:
                    walk((*accepted, candidate))

            walk(())

    assert frontier_count == 7_593


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

    assert captured.value.code in {
        SemanticStoryboardDirectorStreamErrorCode.INVALID_JSON,
        SemanticStoryboardDirectorStreamErrorCode.INVALID_RECORD,
    }
    assert parser.closed


def test_json_decoder_recursion_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_recursion(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr(json, "loads", raise_recursion)
    parser = SemanticStoryboardDirectorStreamParser()

    with pytest.raises(SemanticStoryboardDirectorStreamError) as captured:
        parser.feed(b"{}\n")

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
