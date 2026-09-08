"""Closed semantic state for the Gate 1.5 completing-square lesson.

This module defines checkpoint identity and valid materialized component states.
It intentionally does not decide whether a requested transition is legal; the
router and service own forward-progress and one-shot clarification sequencing.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self

from pydantic import Field, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyComponentId,
    CompletingSquareStage,
)
from murmur.live_scene.contracts import LiveSceneContract


class CompletingSquareMainCheckpoint(StrEnum):
    """The eight settled checkpoints in authoritative lesson order."""

    PROBLEM = "problem"
    AREA_MODEL = "area_model"
    SPLIT_LINEAR_TERM = "split_linear_term"
    REARRANGE_HALVES = "rearrange_halves"
    MISSING_CORNER = "missing_corner"
    BALANCE_AND_COMPLETE = "balance_and_complete"
    FACTOR_SQUARE = "factor_square"
    SOLVE_ROOTS = "solve_roots"


COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER: tuple[CompletingSquareMainCheckpoint, ...] = (
    CompletingSquareMainCheckpoint.PROBLEM,
    CompletingSquareMainCheckpoint.AREA_MODEL,
    CompletingSquareMainCheckpoint.SPLIT_LINEAR_TERM,
    CompletingSquareMainCheckpoint.REARRANGE_HALVES,
    CompletingSquareMainCheckpoint.MISSING_CORNER,
    CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE,
    CompletingSquareMainCheckpoint.FACTOR_SQUARE,
    CompletingSquareMainCheckpoint.SOLVE_ROOTS,
)


class CompletingSquareCheckpointId(StrEnum):
    """Every certificate-level checkpoint identity, including the one detour."""

    PROBLEM = "problem"
    AREA_MODEL = "area_model"
    SPLIT_LINEAR_TERM = "split_linear_term"
    REARRANGE_HALVES = "rearrange_halves"
    MISSING_CORNER = "missing_corner"
    BALANCE_AND_COMPLETE = "balance_and_complete"
    FACTOR_SQUARE = "factor_square"
    SOLVE_ROOTS = "solve_roots"
    CORNER_DETAIL = "corner_detail"


COMPLETING_SQUARE_STAGE_PREFIXES: Mapping[
    CompletingSquareStage,
    tuple[CompletingSquareMainCheckpoint, ...],
] = MappingProxyType(
    {
        CompletingSquareStage.SETUP: COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:2],
        CompletingSquareStage.SPLIT: COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:4],
        CompletingSquareStage.COMPLETE: COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[:6],
        CompletingSquareStage.SOLVE: COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    }
)


_CHECKPOINT_INTRODUCTION_STAGES: Mapping[
    CompletingSquareMainCheckpoint,
    CompletingSquareStage,
] = MappingProxyType(
    {
        checkpoint: next(
            stage
            for stage in CompletingSquareStage
            if checkpoint in COMPLETING_SQUARE_STAGE_PREFIXES[stage]
        )
        for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER
    }
)


def checkpoints_through(
    stage: CompletingSquareStage,
) -> tuple[CompletingSquareMainCheckpoint, ...]:
    """Return the immutable main-checkpoint prefix represented by ``stage``."""

    return COMPLETING_SQUARE_STAGE_PREFIXES[stage]


def checkpoint_prefix(
    last_checkpoint: CompletingSquareMainCheckpoint | None,
) -> tuple[CompletingSquareMainCheckpoint, ...]:
    """Return the exact settled prefix for a nullable component frontier."""

    if last_checkpoint is None:
        return ()
    ordinal = COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER.index(last_checkpoint)
    return COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[: ordinal + 1]


def introduction_stage_for(
    checkpoint: CompletingSquareMainCheckpoint,
) -> CompletingSquareStage:
    """Return the first routed stage whose prefix includes ``checkpoint``."""

    return _CHECKPOINT_INTRODUCTION_STAGES[checkpoint]


def next_main_checkpoint(
    last_checkpoint: CompletingSquareMainCheckpoint | None,
) -> CompletingSquareMainCheckpoint | None:
    """Return the next main checkpoint, or ``None`` after the lesson frontier."""

    prefix = checkpoint_prefix(last_checkpoint)
    if len(prefix) == len(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER):
        return None
    return COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER[len(prefix)]


class CompletingSquareState(LiveSceneContract):
    """Materialized semantic frontier for one completing-square component."""

    kind: Literal["completing_square"] = "completing_square"
    id: ChoreographyComponentId
    last_main_checkpoint: CompletingSquareMainCheckpoint | None = Field(
        default=None,
        alias="lastMainCheckpoint",
    )
    corner_clarified: bool = Field(default=False, strict=True, alias="cornerClarified")

    @model_validator(mode="after")
    def validate_clarification_frontier(self) -> Self:
        if self.corner_clarified:
            prefix = checkpoint_prefix(self.last_main_checkpoint)
            if CompletingSquareMainCheckpoint.MISSING_CORNER not in prefix:
                raise ValueError("cornerClarified requires a frontier at or after missing_corner")
        return self
