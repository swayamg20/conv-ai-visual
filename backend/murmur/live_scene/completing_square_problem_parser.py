"""Fail-closed extraction and continuation binding for supported equations.

This parser recognizes a deliberately small symbolic grammar.  It returns only
an enum and, on success, a validated problem contract; arbitrary problem text is
never retained in the result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from pydantic import ValidationError

from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)

MAX_COMPLETING_SQUARE_PROBLEM_TEXT_CHARS: Final = 2_000


class CompletingSquareProblemStatus(StrEnum):
    """Closed parser and binder outcomes used by the service boundary."""

    BOUND = "bound"
    ABSENT = "absent"
    MULTIPLE = "multiple"
    MALFORMED = "malformed"
    UNSUPPORTED = "unsupported"
    CONFLICTING = "conflicting"


class CompletingSquareProblemFailureReason(StrEnum):
    """Stable service-facing categories that never include prompt content."""

    REQUIRED = "problem_required"
    AMBIGUOUS = "problem_ambiguous"
    UNSUPPORTED = "problem_unsupported"
    CONFLICT = "problem_conflict"


_FAILURE_REASON_BY_STATUS: Final[
    Mapping[CompletingSquareProblemStatus, CompletingSquareProblemFailureReason]
] = MappingProxyType(
    {
        CompletingSquareProblemStatus.ABSENT: CompletingSquareProblemFailureReason.REQUIRED,
        CompletingSquareProblemStatus.MULTIPLE: CompletingSquareProblemFailureReason.AMBIGUOUS,
        CompletingSquareProblemStatus.MALFORMED: CompletingSquareProblemFailureReason.AMBIGUOUS,
        CompletingSquareProblemStatus.UNSUPPORTED: CompletingSquareProblemFailureReason.UNSUPPORTED,
        CompletingSquareProblemStatus.CONFLICTING: CompletingSquareProblemFailureReason.CONFLICT,
    }
)


@dataclass(frozen=True, slots=True)
class CompletingSquareProblemBinding:
    """Text-free result of parsing or binding one problem identity."""

    status: CompletingSquareProblemStatus
    problem: CompletingSquareProblemSpecV1 | None = None

    def __post_init__(self) -> None:
        if (self.status is CompletingSquareProblemStatus.BOUND) != (self.problem is not None):
            raise ValueError("only a bound result may carry a problem")

    @property
    def is_bound(self) -> bool:
        """Return whether parsing and frontier binding succeeded."""

        return self.status is CompletingSquareProblemStatus.BOUND

    @property
    def failure_reason(self) -> CompletingSquareProblemFailureReason | None:
        """Return a stable non-sensitive decline category, or ``None``."""

        return _FAILURE_REASON_BY_STATUS.get(self.status)


# A square token may have a numeric coefficient immediately before its variable,
# but not an identifier character.  The small lookarounds avoid matching the end
# of prose words such as ``max²`` or an exponent prefix such as ``x^20``.
_SQUARED_VARIABLE_PATTERN = re.compile(
    r"(?<![A-Za-z_])(?P<variable>[A-Za-z])(?:²|\^2)(?![A-Za-z0-9_])"
)

# This structural grammar is intentionally a little wider than the accepted
# grammar.  It lets us distinguish well-formed-but-unsupported equations (for
# example ``2x² + 4x = 5`` or ``y² + 4y = 5``) from malformed expressions.
_STRUCTURAL_EQUATION_PATTERN = re.compile(
    r"""
    (?<![A-Za-z0-9_.])
    (?P<leading>[+-]?(?:0|[1-9][0-9]*)?)[ \t]*
    (?P<squareVariable>[A-Za-z])(?:²|\^2)[ \t]*
    (?P<linearOperator>[+-])[ \t]*
    (?P<linearCoefficient>(?:0|[1-9][0-9]*))[ \t]*
    (?P<linearVariable>[A-Za-z])[ \t]*
    =[ \t]*
    (?P<rightHandSide>[+-]?(?:0|[1-9][0-9]*))
    (?![A-Za-z0-9_/=+*^-])
    (?!\.[0-9])
    """,
    re.VERBOSE,
)


def _equation_candidate_count(problem_text: str, square_tokens: list[re.Match[str]]) -> int:
    """Count squared terms followed by ``=`` before the next squared term.

    A second explanatory mention such as ``because x² is an area`` is not a
    second equation.  A second equation, including a malformed one, remains a
    fail-closed multiple-problem request.
    """

    count = 0
    for index, token in enumerate(square_tokens):
        next_square_start = (
            square_tokens[index + 1].start()
            if index + 1 < len(square_tokens)
            else len(problem_text)
        )
        search_end = min(next_square_start, token.end() + 160)
        clause = problem_text[token.end() : search_end]
        hard_boundary = re.search(r"[;\n!?]", clause)
        if hard_boundary is not None:
            clause = clause[: hard_boundary.start()]
        if "=" in clause:
            count += 1
    return count


def parse_completing_square_problem(problem_text: str) -> CompletingSquareProblemBinding:
    """Extract exactly one supported canonical equation from ``problem_text``.

    Accepted notation is ``x² + bx = c`` or ``x^2 + bx = c`` with optional
    horizontal whitespace.  Surrounding instructional prose is allowed.
    """

    if (
        not isinstance(problem_text, str)
        or not problem_text
        or len(problem_text) > MAX_COMPLETING_SQUARE_PROBLEM_TEXT_CHARS
    ):
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.MALFORMED)

    square_tokens = list(_SQUARED_VARIABLE_PATTERN.finditer(problem_text))
    candidate_count = _equation_candidate_count(problem_text, square_tokens)
    structural_matches = list(_STRUCTURAL_EQUATION_PATTERN.finditer(problem_text))

    if candidate_count > 1 or len(structural_matches) > 1:
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.MULTIPLE)
    if not structural_matches:
        if square_tokens or "=" in problem_text:
            return CompletingSquareProblemBinding(CompletingSquareProblemStatus.MALFORMED)
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.ABSENT)

    match = structural_matches[0]
    if candidate_count != 1:
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.MALFORMED)

    if (
        match.group("leading") != ""
        or match.group("squareVariable") != "x"
        or match.group("linearOperator") != "+"
        or match.group("linearVariable") != "x"
        or match.group("rightHandSide").startswith("+")
    ):
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.UNSUPPORTED)

    try:
        problem = CompletingSquareProblemSpecV1(
            linearCoefficient=int(match.group("linearCoefficient")),
            rightHandSide=int(match.group("rightHandSide")),
        )
    except ValidationError:
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.UNSUPPORTED)
    return CompletingSquareProblemBinding(CompletingSquareProblemStatus.BOUND, problem)


def bind_completing_square_problem(
    problem_text: str,
    *,
    accepted_problem: CompletingSquareProblemSpecV1 | None,
) -> CompletingSquareProblemBinding:
    """Bind a parsed problem to an optional accepted semantic frontier.

    Initial requests require one equation.  A continuation may omit it and
    reuse the committed problem, repeat the same equation, or fail as
    ``conflicting`` if it names a different supported problem.
    """

    parsed = parse_completing_square_problem(problem_text)
    if parsed.status is CompletingSquareProblemStatus.ABSENT and accepted_problem is not None:
        return CompletingSquareProblemBinding(
            CompletingSquareProblemStatus.BOUND,
            accepted_problem,
        )
    if not parsed.is_bound or accepted_problem is None:
        return parsed
    if parsed.problem != accepted_problem:
        return CompletingSquareProblemBinding(CompletingSquareProblemStatus.CONFLICTING)
    return parsed


__all__ = [
    "MAX_COMPLETING_SQUARE_PROBLEM_TEXT_CHARS",
    "CompletingSquareProblemBinding",
    "CompletingSquareProblemFailureReason",
    "CompletingSquareProblemStatus",
    "bind_completing_square_problem",
    "parse_completing_square_problem",
]
