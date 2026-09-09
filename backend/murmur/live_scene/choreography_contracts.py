"""Closed server-owned contracts for Gate 1.5 live choreography.

The routed beat is deliberately smaller than the resulting presentation.  It
can select only a completing-square stage or the one supported clarification;
it cannot carry narration, geometry, timing, viewport coordinates, or motion
properties.  The deterministic compiler owns every richer contract in this
module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator

from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import (
    LIVE_SCENE_BOARD_HEIGHT,
    LIVE_SCENE_BOARD_WIDTH,
    LiveSceneContract,
    NarrationText,
    SceneNodeId,
)
from murmur.live_scene.semantic_integrity import canonical_sha256

ROUTED_CHOREOGRAPHY_BEAT_VERSION = 2
ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION = 3
CHOREOGRAPHY_PLAN_VERSION = 1
PRESENTATION_CHECKPOINT_VERSION = 1
VIEWPORT_POSE_VERSION = 1

MAX_CHOREOGRAPHY_ID_CHARS = 64
MAX_CHOREOGRAPHY_COMPONENT_ID_CHARS = 32
MAX_CHOREOGRAPHY_CUES = 5
MAX_CHOREOGRAPHY_TARGETS_PER_CUE = 16
MAX_CHOREOGRAPHY_TARGET_REFERENCES = 32
MIN_CHOREOGRAPHY_PHASE_MS = 100
MAX_CHOREOGRAPHY_PHASE_MS = 6_000
MAX_CHOREOGRAPHY_HOLD_AFTER_MS = 9_000
MAX_CHOREOGRAPHY_PLAN_MS = 12_000

ROUTED_CHOREOGRAPHY_BEAT_HASH_DOMAIN = "murmur:routed-choreography-beat:v2"
ROUTED_CHOREOGRAPHY_BEAT_V3_HASH_DOMAIN = "murmur:routed-choreography-beat:v3"
CHOREOGRAPHY_PLAN_HASH_DOMAIN = "murmur:choreography-plan:v1"
PRESENTATION_CHECKPOINT_HASH_DOMAIN = "murmur:presentation-checkpoint:v1"

ChoreographyId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_CHOREOGRAPHY_ID_CHARS,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    ),
]
ChoreographyComponentId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=MAX_CHOREOGRAPHY_COMPONENT_ID_CHARS,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$",
    ),
]
ChoreographyTargets = Annotated[
    tuple[SceneNodeId, ...],
    Field(min_length=1, max_length=MAX_CHOREOGRAPHY_TARGETS_PER_CUE),
]
PhaseDurationMs = Annotated[
    int,
    Field(strict=True, ge=MIN_CHOREOGRAPHY_PHASE_MS, le=MAX_CHOREOGRAPHY_PHASE_MS),
]
HoldAfterMs = Annotated[
    int,
    Field(strict=True, ge=0, le=MAX_CHOREOGRAPHY_HOLD_AFTER_MS),
]
ViewportCoordinate = Annotated[float, Field(strict=True, ge=0)]
ViewportExtent = Annotated[float, Field(strict=True, gt=0)]


class CompletingSquareStage(StrEnum):
    """Closed main-lesson targets understood by the Gate 1.5 compiler."""

    SETUP = "setup"
    SPLIT = "split"
    COMPLETE = "complete"
    SOLVE = "solve"


class AdvanceChoreographyRouteV2(LiveSceneContract):
    """Advance the fixed lesson to one closed semantic stage."""

    intent: Literal["advance"] = "advance"
    target_stage: CompletingSquareStage = Field(alias="targetStage")


class ClarifyCornerRouteV2(LiveSceneContract):
    """Request the single supported missing-corner clarification."""

    intent: Literal["clarify_corner"] = "clarify_corner"


RoutedChoreographyRouteV2: TypeAlias = Annotated[
    AdvanceChoreographyRouteV2 | ClarifyCornerRouteV2,
    Field(discriminator="intent"),
]


class RoutedChoreographyBeatV2(LiveSceneContract):
    """Resolved routed-only input to the deterministic choreography compiler."""

    v: Literal[ROUTED_CHOREOGRAPHY_BEAT_VERSION] = ROUTED_CHOREOGRAPHY_BEAT_VERSION
    beat_id: ChoreographyId = Field(alias="beatId")
    component_kind: Literal["completing_square"] = Field(
        default="completing_square",
        alias="componentKind",
    )
    component_id: ChoreographyComponentId = Field(alias="componentId")
    route: RoutedChoreographyRouteV2


class RoutedChoreographyBeatV3(LiveSceneContract):
    """Problem-bound routed input to the parametric choreography compiler."""

    v: Literal[ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION] = ROUTED_CHOREOGRAPHY_BEAT_V3_VERSION
    beat_id: ChoreographyId = Field(alias="beatId")
    component_kind: Literal["completing_square_parametric"] = Field(
        default="completing_square_parametric",
        alias="componentKind",
    )
    component_id: ChoreographyComponentId = Field(alias="componentId")
    problem_spec: CompletingSquareProblemSpecV1 = Field(alias="problemSpec")
    route: RoutedChoreographyRouteV2


class _TargetCueV1(LiveSceneContract):
    """Shared canonical target-list validation for closed cue variants."""

    target_ids: ChoreographyTargets = Field(alias="targetIds")

    @field_validator("target_ids")
    @classmethod
    def validate_canonical_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("choreography cue targetIds must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("choreography cue targetIds must use canonical lexical order")
        return value


class EnterCueV1(_TargetCueV1):
    cue: Literal["enter"] = "enter"


class ExitCueV1(_TargetCueV1):
    cue: Literal["exit"] = "exit"


class TransformCueV1(_TargetCueV1):
    cue: Literal["transform"] = "transform"


class EmphasizeCueV1(_TargetCueV1):
    cue: Literal["emphasize"] = "emphasize"


class FocusCueV1(_TargetCueV1):
    cue: Literal["focus"] = "focus"


ChoreographyCueV1: TypeAlias = Annotated[
    EnterCueV1 | ExitCueV1 | TransformCueV1 | EmphasizeCueV1 | FocusCueV1,
    Field(discriminator="cue"),
]


class ChoreographyEasing(StrEnum):
    """Purposeful, non-overshooting curves mapped internally by the renderer."""

    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT_QUART = "ease_out_quart"
    EASE_OUT_QUINT = "ease_out_quint"
    EASE_IN_OUT = "ease_in_out"


_CUE_ORDER = ("enter", "exit", "transform", "emphasize", "focus")


class ChoreographyPhaseV1(LiveSceneContract):
    """The one parallel visible phase and its optional reading hold."""

    cues: Annotated[
        tuple[ChoreographyCueV1, ...],
        Field(min_length=1, max_length=MAX_CHOREOGRAPHY_CUES),
    ]
    duration_ms: PhaseDurationMs = Field(alias="durationMs")
    easing: ChoreographyEasing
    hold_after_ms: HoldAfterMs = Field(default=0, alias="holdAfterMs")

    @model_validator(mode="after")
    def validate_canonical_cues(self) -> Self:
        cue_kinds = tuple(cue.cue for cue in self.cues)
        if len(cue_kinds) != len(set(cue_kinds)):
            raise ValueError("parallel choreography cue kinds must be unique")
        if cue_kinds != tuple(sorted(cue_kinds, key=_CUE_ORDER.index)):
            raise ValueError("parallel choreography cues must use canonical cue order")

        target_references = sum(len(cue.target_ids) for cue in self.cues)
        if target_references > MAX_CHOREOGRAPHY_TARGET_REFERENCES:
            raise ValueError("parallel choreography phase exceeds the target-reference budget")
        return self

    @property
    def total_ms(self) -> int:
        """Return authored visible motion plus its reading hold."""

        return self.duration_ms + self.hold_after_ms


class ChoreographyPlanV1(LiveSceneContract):
    """A checkpoint's single-phase, compiler-owned movement plan."""

    v: Literal[CHOREOGRAPHY_PLAN_VERSION] = CHOREOGRAPHY_PLAN_VERSION
    phase: ChoreographyPhaseV1

    @model_validator(mode="after")
    def validate_total_duration(self) -> Self:
        if self.phase.total_ms > MAX_CHOREOGRAPHY_PLAN_MS:
            raise ValueError("choreography plan exceeds the total duration budget")
        return self


class ViewportPoseV1(LiveSceneContract):
    """An exact terminal camera viewBox inside the canonical 800 by 600 board."""

    v: Literal[VIEWPORT_POSE_VERSION] = VIEWPORT_POSE_VERSION
    x: ViewportCoordinate
    y: ViewportCoordinate
    width: ViewportExtent
    height: ViewportExtent

    @model_validator(mode="after")
    def validate_inside_board(self) -> Self:
        if self.x + self.width > LIVE_SCENE_BOARD_WIDTH:
            raise ValueError("viewport pose must stay inside the board width")
        if self.y + self.height > LIVE_SCENE_BOARD_HEIGHT:
            raise ValueError("viewport pose must stay inside the board height")
        return self


class LayoutViewportMapV1(LiveSceneContract):
    """Exact certified poses for both supported presentation layouts."""

    cinematic: ViewportPoseV1
    compact: ViewportPoseV1


class PresentationCheckpointV1(LiveSceneContract):
    """Server-authored caption and settled camera state for one checkpoint."""

    v: Literal[PRESENTATION_CHECKPOINT_VERSION] = PRESENTATION_CHECKPOINT_VERSION
    checkpoint_id: ChoreographyId = Field(alias="checkpointId")
    checkpoint_narration: NarrationText = Field(alias="checkpointNarration")
    base_viewports: LayoutViewportMapV1 = Field(alias="baseViewports")
    result_viewports: LayoutViewportMapV1 = Field(alias="resultViewports")
    transient_free: Literal[True] = Field(default=True, alias="transientFree")


ROUTED_CHOREOGRAPHY_BEAT_V2_ADAPTER = TypeAdapter(RoutedChoreographyBeatV2)
ROUTED_CHOREOGRAPHY_BEAT_V3_ADAPTER = TypeAdapter(RoutedChoreographyBeatV3)
CHOREOGRAPHY_CUE_V1_ADAPTER = TypeAdapter(ChoreographyCueV1)


def routed_choreography_beat_sha256(beat: RoutedChoreographyBeatV2) -> str:
    """Hash the complete resolved compiler input with a V2-specific domain."""

    return canonical_sha256(
        beat.model_dump(mode="json", by_alias=True),
        domain=ROUTED_CHOREOGRAPHY_BEAT_HASH_DOMAIN,
    )


def routed_choreography_beat_v3_sha256(beat: RoutedChoreographyBeatV3) -> str:
    """Hash the complete problem-bound compiler input in the V3 domain."""

    return canonical_sha256(
        beat.model_dump(mode="json", by_alias=True),
        domain=ROUTED_CHOREOGRAPHY_BEAT_V3_HASH_DOMAIN,
    )


def choreography_plan_sha256(plan: ChoreographyPlanV1) -> str:
    """Hash the exact closed cue plan and authored phase timing."""

    return canonical_sha256(
        plan.model_dump(mode="json", by_alias=True),
        domain=CHOREOGRAPHY_PLAN_HASH_DOMAIN,
    )


def presentation_checkpoint_sha256(checkpoint: PresentationCheckpointV1) -> str:
    """Hash the exact routed-only caption and both viewport transitions."""

    return canonical_sha256(
        checkpoint.model_dump(mode="json", by_alias=True),
        domain=PRESENTATION_CHECKPOINT_HASH_DOMAIN,
    )
