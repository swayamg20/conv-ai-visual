from __future__ import annotations

from dataclasses import fields
from math import isqrt

import pytest
from murmur.live_scene.completing_square_problem_contracts import (
    COMPLETING_SQUARE_PROBLEM_HASH_DOMAIN,
    COMPLETING_SQUARE_PROBLEM_VERSION,
    CompletingSquareProblemSpecV1,
    completing_square_problem_sha256,
)
from murmur.live_scene.completing_square_problem_parser import (
    MAX_COMPLETING_SQUARE_PROBLEM_TEXT_CHARS,
    CompletingSquareProblemBinding,
    CompletingSquareProblemFailureReason,
    CompletingSquareProblemStatus,
    bind_completing_square_problem,
    parse_completing_square_problem,
)
from pydantic import ValidationError


def _problem(h: int = 4, m: int = 6) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=2 * h,
        rightHandSide=m * m - h * h,
    )


def test_problem_contract_has_exact_wire_shape_and_derived_values() -> None:
    problem = _problem()

    assert COMPLETING_SQUARE_PROBLEM_VERSION == 1
    assert COMPLETING_SQUARE_PROBLEM_HASH_DOMAIN == "murmur:completing-square-problem:v1"
    assert set(CompletingSquareProblemSpecV1.model_fields) == {
        "v",
        "linear_coefficient",
        "right_hand_side",
    }
    assert problem.model_dump(mode="json", by_alias=True) == {
        "v": 1,
        "linearCoefficient": 8,
        "rightHandSide": 20,
    }
    assert problem.half_coefficient == 4
    assert problem.corner_value == 16
    assert problem.completed_right_hand_side == 36
    assert problem.square_root_magnitude == 6
    assert problem.positive_root == 2
    assert problem.negative_root == -10

    with pytest.raises(ValidationError, match="frozen"):
        problem.right_hand_side = 21


def test_problem_hash_is_canonical_domain_separated_and_problem_bound() -> None:
    problem = _problem()
    reordered = CompletingSquareProblemSpecV1.model_validate(
        {"rightHandSide": 20, "linearCoefficient": 8, "v": 1}
    )
    another = _problem(h=3, m=4)

    assert completing_square_problem_sha256(problem) == completing_square_problem_sha256(reordered)
    assert completing_square_problem_sha256(problem) == (
        "7edad1ec173c85ab9504aa5ec6f694fe78c3930751a47b778be06457ef2a98fb"
    )
    assert completing_square_problem_sha256(problem) != completing_square_problem_sha256(another)


def test_contract_accepts_exactly_the_complete_36_problem_domain() -> None:
    accepted: set[tuple[int, int]] = set()
    expected = {(2 * h, m * m - h * h) for h in range(1, 9) for m in range(h + 1, 10)}

    for linear_coefficient in range(2, 17):
        for right_hand_side in range(1, 81):
            completed = right_hand_side + (linear_coefficient // 2) ** 2
            magnitude = isqrt(completed)
            should_accept = (
                linear_coefficient % 2 == 0
                and magnitude * magnitude == completed
                and linear_coefficient // 2 < magnitude <= 9
            )
            try:
                problem = CompletingSquareProblemSpecV1(
                    linearCoefficient=linear_coefficient,
                    rightHandSide=right_hand_side,
                )
            except ValidationError:
                assert not should_accept
            else:
                assert should_accept
                accepted.add((problem.linear_coefficient, problem.right_hand_side))

    assert accepted == expected
    assert len(accepted) == 36


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"linearCoefficient": 0, "rightHandSide": 3}, "greater than or equal to 2"),
        ({"linearCoefficient": 1, "rightHandSide": 3}, "greater than or equal to 2"),
        ({"linearCoefficient": 17, "rightHandSide": 3}, "less than or equal to 16"),
        ({"linearCoefficient": 18, "rightHandSide": 0}, "less than or equal to 16"),
        ({"linearCoefficient": 3, "rightHandSide": 7}, "must be even"),
        ({"linearCoefficient": 8, "rightHandSide": 19}, "must be a perfect square"),
        ({"linearCoefficient": 10, "rightHandSide": 75}, "h < m <= 9"),
        ({"linearCoefficient": 2, "rightHandSide": 99}, "less than or equal to 80"),
        ({"linearCoefficient": 2, "rightHandSide": 81}, "less than or equal to 80"),
        ({"linearCoefficient": 8, "rightHandSide": 20, "derivedRoot": 6}, "Extra inputs"),
        ({"linearCoefficient": True, "rightHandSide": 20}, "valid integer"),
        ({"linearCoefficient": 8, "rightHandSide": 20.0}, "valid integer"),
        ({"linearCoefficient": "8", "rightHandSide": 20}, "valid integer"),
    ],
)
def test_problem_contract_rejects_outside_values_and_noncanonical_wire_types(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        CompletingSquareProblemSpecV1.model_validate(payload)


def test_problem_contract_rejects_missing_and_wrong_version() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        CompletingSquareProblemSpecV1.model_validate({"linearCoefficient": 8})
    with pytest.raises(ValidationError, match="Input should be 1"):
        CompletingSquareProblemSpecV1.model_validate(
            {"v": 2, "linearCoefficient": 8, "rightHandSide": 20}
        )
    for non_integer_version in (True, 1.0, "1"):
        with pytest.raises(ValidationError, match="strict integer"):
            CompletingSquareProblemSpecV1.model_validate(
                {
                    "v": non_integer_version,
                    "linearCoefficient": 8,
                    "rightHandSide": 20,
                }
            )


@pytest.mark.parametrize("notation", ["x²", "x^2"])
def test_parser_accepts_both_notations_for_every_supported_problem(notation: str) -> None:
    parsed_pairs: set[tuple[int, int]] = set()

    for h in range(1, 9):
        for m in range(h + 1, 10):
            right_hand_side = m * m - h * h
            result = parse_completing_square_problem(
                f"Please show {notation} + {2 * h}x = {right_hand_side}, one step at a time."
            )

            assert result.status is CompletingSquareProblemStatus.BOUND
            assert result.is_bound
            assert result.problem is not None
            assert result.problem.half_coefficient == h
            assert result.problem.corner_value == h * h
            assert result.problem.completed_right_hand_side == m * m
            assert result.problem.square_root_magnitude == m
            assert result.problem.positive_root == m - h
            assert result.problem.negative_root == -(m + h)
            parsed_pairs.add((result.problem.linear_coefficient, result.problem.right_hand_side))

    assert len(parsed_pairs) == 36


def test_parser_allows_an_explanatory_square_mention_after_one_equation() -> None:
    result = parse_completing_square_problem(
        "Teach x² + 4x = 5 because x² is easier to see as an area."
    )

    assert result.status is CompletingSquareProblemStatus.BOUND
    assert result.problem == _problem(h=2, m=3)


@pytest.mark.parametrize(
    "prompt",
    [
        "Why does completing the square work?",
        "Continue the lesson from the current board.",
        "Show the missing corner.",
    ],
)
def test_parser_classifies_prompts_without_symbolic_equations_as_absent(prompt: str) -> None:
    result = parse_completing_square_problem(prompt)

    assert result.status is CompletingSquareProblemStatus.ABSENT
    assert not result.is_bound
    assert result.problem is None


@pytest.mark.parametrize(
    "prompt",
    [
        "Compare x² + 4x = 5 and x^2 + 6x = 7.",
        "First x² + 4x = 5; then y² + 4y = 5.",
        "x² + 4x = 5\nx² + 8x = 20",
    ],
)
def test_parser_classifies_multiple_equations_without_choosing_one(prompt: str) -> None:
    result = parse_completing_square_problem(prompt)

    assert result.status is CompletingSquareProblemStatus.MULTIPLE
    assert result.problem is None


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "x*x + 4*x = 5",
        "x² + 4x 5",
        "x² + x = 5",
        "x² + 04x = 5",
        "x² + 4x = 05",
        "x² + 4x = 5.0",
        "x² + 4x = 5/1",
        "x² + 4x = 5+1",
        "x² + 4x = 5=5",
        "x² + 4x = 5^2",
        "x^ 2 + 4x = 5",
        "x² + 4x == 5",
        "a = b",
        "x" * (MAX_COMPLETING_SQUARE_PROBLEM_TEXT_CHARS + 1),
    ],
)
def test_parser_classifies_malformed_equation_shapes(prompt: str) -> None:
    result = parse_completing_square_problem(prompt)

    assert result.status is CompletingSquareProblemStatus.MALFORMED
    assert result.problem is None


@pytest.mark.parametrize(
    "prompt",
    [
        "2x² + 4x = 5",
        "+x² + 4x = 5",
        "y² + 4y = 5",
        "x² + 4y = 5",
        "x² - 4x = 5",
        "x² + 3x = 7",
        "x² + 0x = 5",
        "x² + 18x = 19",
        "x² + 4x = 4",
        "x² + 4x = 6",
        "x² + 4x = -3",
        "x² + 4x = +5",
        "x² + 16x = 16",
    ],
)
def test_parser_classifies_well_formed_but_unsupported_equations(prompt: str) -> None:
    result = parse_completing_square_problem(prompt)

    assert result.status is CompletingSquareProblemStatus.UNSUPPORTED
    assert result.problem is None


def test_initial_binding_requires_one_supported_problem() -> None:
    bound = bind_completing_square_problem(
        "Begin x² + 8x = 20.",
        accepted_problem=None,
    )
    absent = bind_completing_square_problem("Begin the lesson.", accepted_problem=None)
    null = bind_completing_square_problem(None, accepted_problem=None)

    assert bound.status is CompletingSquareProblemStatus.BOUND
    assert bound.problem == _problem()
    assert absent.status is CompletingSquareProblemStatus.ABSENT
    assert null.status is CompletingSquareProblemStatus.ABSENT
    assert null.failure_reason is CompletingSquareProblemFailureReason.REQUIRED


def test_continuation_binding_reuses_omitted_or_identical_problem() -> None:
    accepted = _problem()

    omitted = bind_completing_square_problem(
        "Continue from the visible board.",
        accepted_problem=accepted,
    )
    repeated = bind_completing_square_problem(
        "Continue x^2 + 8x = 20.",
        accepted_problem=accepted,
    )
    nullable_field = bind_completing_square_problem(None, accepted_problem=accepted)

    assert omitted == CompletingSquareProblemBinding(
        CompletingSquareProblemStatus.BOUND,
        accepted,
    )
    assert repeated == omitted
    assert nullable_field == omitted


def test_continuation_binding_rejects_a_different_supported_problem() -> None:
    result = bind_completing_square_problem(
        "Instead continue x² + 6x = 7.",
        accepted_problem=_problem(),
    )

    assert result.status is CompletingSquareProblemStatus.CONFLICTING
    assert result.problem is None


@pytest.mark.parametrize(
    ("prompt", "status"),
    [
        ("Continue x² + 7x = 8.", CompletingSquareProblemStatus.UNSUPPORTED),
        ("Continue x² + 8x = 20. Also x² + 6x = 7.", CompletingSquareProblemStatus.MULTIPLE),
        ("Continue x² plus 8x equals 20.", CompletingSquareProblemStatus.MALFORMED),
    ],
)
def test_continuation_does_not_hide_invalid_explicit_problem(
    prompt: str,
    status: CompletingSquareProblemStatus,
) -> None:
    result = bind_completing_square_problem(prompt, accepted_problem=_problem())

    assert result.status is status
    assert result.problem is None


def test_parse_result_never_retains_arbitrary_prompt_text() -> None:
    secret_marker = "DO_NOT_RETAIN_THIS_FREE_FORM_TEXT"
    result = parse_completing_square_problem(f"{secret_marker}: x² + 8x = 20")

    assert tuple(field.name for field in fields(result)) == ("status", "problem")
    assert not hasattr(result, "prompt")
    assert secret_marker not in repr(result)


def test_binding_invariant_allows_a_problem_only_for_bound_status() -> None:
    with pytest.raises(ValueError, match="only a bound result"):
        CompletingSquareProblemBinding(CompletingSquareProblemStatus.ABSENT, _problem())
    with pytest.raises(ValueError, match="only a bound result"):
        CompletingSquareProblemBinding(CompletingSquareProblemStatus.BOUND)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (CompletingSquareProblemStatus.BOUND, None),
        (
            CompletingSquareProblemStatus.ABSENT,
            CompletingSquareProblemFailureReason.REQUIRED,
        ),
        (
            CompletingSquareProblemStatus.MULTIPLE,
            CompletingSquareProblemFailureReason.AMBIGUOUS,
        ),
        (
            CompletingSquareProblemStatus.MALFORMED,
            CompletingSquareProblemFailureReason.AMBIGUOUS,
        ),
        (
            CompletingSquareProblemStatus.UNSUPPORTED,
            CompletingSquareProblemFailureReason.UNSUPPORTED,
        ),
        (
            CompletingSquareProblemStatus.CONFLICTING,
            CompletingSquareProblemFailureReason.CONFLICT,
        ),
    ],
)
def test_every_parser_status_has_a_stable_prompt_free_service_reason(
    status: CompletingSquareProblemStatus,
    reason: CompletingSquareProblemFailureReason | None,
) -> None:
    result = CompletingSquareProblemBinding(
        status,
        _problem() if status is CompletingSquareProblemStatus.BOUND else None,
    )

    assert result.failure_reason is reason
