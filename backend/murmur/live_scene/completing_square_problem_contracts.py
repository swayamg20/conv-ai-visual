"""Bounded problem identity for parametric completing-square choreography.

The serialized contract intentionally stores only the two coefficients supplied
by the learner.  Every other mathematical value is derived locally so a model,
browser, or persisted payload cannot smuggle in inconsistent arithmetic.
"""

from __future__ import annotations

from math import isqrt
from typing import Annotated, Final, Literal, Self

from pydantic import Field, field_validator, model_validator

from murmur.live_scene.contracts import LiveSceneContract
from murmur.live_scene.semantic_integrity import canonical_sha256

COMPLETING_SQUARE_PROBLEM_VERSION: Final = 1
COMPLETING_SQUARE_PROBLEM_HASH_DOMAIN: Final = "murmur:completing-square-problem:v1"

LinearCoefficient = Annotated[int, Field(strict=True, ge=2, le=16)]
RightHandSide = Annotated[int, Field(strict=True, ge=1, le=80)]


class CompletingSquareProblemSpecV1(LiveSceneContract):
    """One supported equation ``x² + bx = c`` in exact wire form.

    The accepted family is equivalent to ``x² + 2hx = m² - h²`` for
    integers ``1 <= h < m <= 9``.  This is a structural 36-problem domain,
    not a catalog of hand-authored examples.
    """

    v: Literal[COMPLETING_SQUARE_PROBLEM_VERSION] = COMPLETING_SQUARE_PROBLEM_VERSION
    linear_coefficient: LinearCoefficient = Field(alias="linearCoefficient")
    right_hand_side: RightHandSide = Field(alias="rightHandSide")

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_supported_family(self) -> Self:
        if self.linear_coefficient % 2 != 0:
            raise ValueError("linearCoefficient must be even")

        completed = self.right_hand_side + self.corner_value
        magnitude = isqrt(completed)
        if magnitude * magnitude != completed:
            raise ValueError("rightHandSide plus the corner must be a perfect square")
        if not self.half_coefficient < magnitude <= 9:
            raise ValueError("completed square root must satisfy h < m <= 9")
        return self

    @property
    def half_coefficient(self) -> int:
        """Return ``h = b / 2`` for the two equal linear strips."""

        return self.linear_coefficient // 2

    @property
    def corner_value(self) -> int:
        """Return the missing corner area ``h²``."""

        return self.half_coefficient**2

    @property
    def completed_right_hand_side(self) -> int:
        """Return the balanced right side ``c + h² = m²``."""

        return self.right_hand_side + self.corner_value

    @property
    def square_root_magnitude(self) -> int:
        """Return the positive integer ``m`` from the completed square."""

        return isqrt(self.completed_right_hand_side)

    @property
    def positive_root(self) -> int:
        """Return the root ``m - h``."""

        return self.square_root_magnitude - self.half_coefficient

    @property
    def negative_root(self) -> int:
        """Return the root ``-(m + h)``."""

        return -(self.square_root_magnitude + self.half_coefficient)


def completing_square_problem_sha256(problem: CompletingSquareProblemSpecV1) -> str:
    """Hash one exact problem identity under its dedicated V1 domain."""

    return canonical_sha256(
        problem.model_dump(mode="json", by_alias=True),
        domain=COMPLETING_SQUARE_PROBLEM_HASH_DOMAIN,
    )


__all__ = [
    "COMPLETING_SQUARE_PROBLEM_HASH_DOMAIN",
    "COMPLETING_SQUARE_PROBLEM_VERSION",
    "CompletingSquareProblemSpecV1",
    "completing_square_problem_sha256",
]
