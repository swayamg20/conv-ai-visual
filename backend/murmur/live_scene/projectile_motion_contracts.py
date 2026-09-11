"""Closed problem, frontier, and route contracts for projectile choreography.

The wire surface stores only the learner-selected speed and angle.  Gravity,
derived physics, geometry, narration, timing, and integrity evidence remain
server-owned.  A compiler may use the convenience properties on the problem
specification; the independent verifier must recompute the physics from the two
primitive inputs instead of trusting those helpers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Final, Literal, Self, TypeAlias

from pydantic import Field, TypeAdapter, field_validator, model_validator

from murmur.live_scene.choreography_contracts import (
    ChoreographyComponentId,
    ChoreographyId,
)
from murmur.live_scene.contracts import LiveSceneContract
from murmur.live_scene.semantic_integrity import canonical_sha256

PROJECTILE_MOTION_PROBLEM_VERSION: Final = 1
PROJECTILE_MOTION_ROUTED_BEAT_VERSION: Final = 1
PROJECTILE_MOTION_PROBLEM_HASH_DOMAIN: Final = "murmur:projectile-motion-problem:v1"
PROJECTILE_MOTION_ROUTED_BEAT_HASH_DOMAIN: Final = "murmur:routed-projectile-motion-beat:v1"
PROJECTILE_MOTION_GRAVITY_MPS2: Final = 10.0
SUPPORTED_PROJECTILE_SPEEDS_MPS: Final = (20, 25, 30)
SUPPORTED_PROJECTILE_ANGLES_DEG: Final = (30, 45, 60)

ProjectileSpeedMps = Annotated[int, Field(strict=True)]
ProjectileAngleDeg = Annotated[int, Field(strict=True)]


class ProjectileMotionProblemSpecV1(LiveSceneContract):
    """One qualified ground-to-ground, no-drag projectile problem."""

    v: Literal[PROJECTILE_MOTION_PROBLEM_VERSION] = PROJECTILE_MOTION_PROBLEM_VERSION
    speed_mps: ProjectileSpeedMps = Field(alias="speedMps")
    angle_deg: ProjectileAngleDeg = Field(alias="angleDeg")

    @field_validator("v", "speed_mps", "angle_deg", mode="before")
    @classmethod
    def validate_strict_integers(cls, value: object, info) -> object:
        if type(value) is not int:
            raise ValueError(f"{info.field_name} must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_supported_problem(self) -> Self:
        if self.speed_mps not in SUPPORTED_PROJECTILE_SPEEDS_MPS:
            raise ValueError(
                "speedMps must be one of "
                f"{', '.join(str(speed) for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS)}"
            )
        if self.angle_deg not in SUPPORTED_PROJECTILE_ANGLES_DEG:
            raise ValueError(
                "angleDeg must be one of "
                f"{', '.join(str(angle) for angle in SUPPORTED_PROJECTILE_ANGLES_DEG)}"
            )
        return self

    @property
    def initial_horizontal_velocity_mps(self) -> float:
        """Return ``v_x = v_0 cos(theta)`` for compiler use."""

        return self.speed_mps * math.cos(math.radians(self.angle_deg))

    @property
    def initial_vertical_velocity_mps(self) -> float:
        """Return ``v_y0 = v_0 sin(theta)`` for compiler use."""

        return self.speed_mps * math.sin(math.radians(self.angle_deg))

    @property
    def ascent_time_seconds(self) -> float:
        """Return the time from launch to the apex."""

        return self.initial_vertical_velocity_mps / PROJECTILE_MOTION_GRAVITY_MPS2

    @property
    def flight_time_seconds(self) -> float:
        """Return the symmetric ground-to-ground flight duration."""

        return 2 * self.ascent_time_seconds

    @property
    def maximum_height_m(self) -> float:
        """Return ``v_y0² / (2g)`` above the zero launch height."""

        vertical_velocity = self.initial_vertical_velocity_mps
        return vertical_velocity**2 / (2 * PROJECTILE_MOTION_GRAVITY_MPS2)

    @property
    def range_m(self) -> float:
        """Return the horizontal distance at ground impact."""

        return self.initial_horizontal_velocity_mps * self.flight_time_seconds


def projectile_motion_problem_sha256(problem: ProjectileMotionProblemSpecV1) -> str:
    """Hash the two exact learner inputs under the projectile problem domain."""

    return canonical_sha256(
        problem.model_dump(mode="json", by_alias=True),
        domain=PROJECTILE_MOTION_PROBLEM_HASH_DOMAIN,
    )


class ProjectileMotionMainCheckpoint(StrEnum):
    """The six settled main-lesson checkpoints in authoritative order."""

    SETUP = "setup"
    DECOMPOSE_VELOCITY = "decompose_velocity"
    TRACE_ASCENT = "trace_ascent"
    APEX_STATE = "apex_state"
    TRACE_DESCENT = "trace_descent"
    SUMMARY = "summary"


PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER: Final[tuple[ProjectileMotionMainCheckpoint, ...]] = (
    ProjectileMotionMainCheckpoint.SETUP,
    ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY,
    ProjectileMotionMainCheckpoint.TRACE_ASCENT,
    ProjectileMotionMainCheckpoint.APEX_STATE,
    ProjectileMotionMainCheckpoint.TRACE_DESCENT,
    ProjectileMotionMainCheckpoint.SUMMARY,
)


class ProjectileMotionStage(StrEnum):
    """Closed pedagogical targets understood by projectile routing."""

    SETUP = "setup"
    LAUNCH = "launch"
    FLIGHT = "flight"
    SOLVE = "solve"


PROJECTILE_MOTION_STAGE_PREFIXES: Final[
    Mapping[ProjectileMotionStage, tuple[ProjectileMotionMainCheckpoint, ...]]
] = MappingProxyType(
    {
        ProjectileMotionStage.SETUP: PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[:1],
        ProjectileMotionStage.LAUNCH: PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[:2],
        ProjectileMotionStage.FLIGHT: PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[:5],
        ProjectileMotionStage.SOLVE: PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    }
)

_PROJECTILE_MOTION_CHECKPOINT_INTRODUCTION_STAGES: Final[
    Mapping[ProjectileMotionMainCheckpoint, ProjectileMotionStage]
] = MappingProxyType(
    {
        checkpoint: next(
            stage
            for stage in ProjectileMotionStage
            if checkpoint in PROJECTILE_MOTION_STAGE_PREFIXES[stage]
        )
        for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    }
)


class ProjectileMotionClarificationTopic(StrEnum):
    """The three one-shot visual detours supported by Gate 1.7."""

    HORIZONTAL_VELOCITY = "horizontal_velocity"
    APEX_ACCELERATION = "apex_acceleration"
    FLIGHT_SYMMETRY = "flight_symmetry"


PROJECTILE_MOTION_CLARIFICATION_ORDER: Final[tuple[ProjectileMotionClarificationTopic, ...]] = (
    ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
    ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
)

PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES: Final[
    Mapping[ProjectileMotionClarificationTopic, ProjectileMotionMainCheckpoint]
] = MappingProxyType(
    {
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY: (
            ProjectileMotionMainCheckpoint.DECOMPOSE_VELOCITY
        ),
        ProjectileMotionClarificationTopic.APEX_ACCELERATION: (
            ProjectileMotionMainCheckpoint.APEX_STATE
        ),
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY: (
            ProjectileMotionMainCheckpoint.TRACE_DESCENT
        ),
    }
)


class ProjectileMotionCheckpointId(StrEnum):
    """Main and sidecar checkpoint identities used by later certificates."""

    SETUP = "setup"
    DECOMPOSE_VELOCITY = "decompose_velocity"
    TRACE_ASCENT = "trace_ascent"
    APEX_STATE = "apex_state"
    TRACE_DESCENT = "trace_descent"
    SUMMARY = "summary"
    HORIZONTAL_VELOCITY_DETAIL = "horizontal_velocity_detail"
    APEX_ACCELERATION_DETAIL = "apex_acceleration_detail"
    FLIGHT_SYMMETRY_DETAIL = "flight_symmetry_detail"
    PARAMETERS_RETARGETED = "parameters_retargeted"


PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS: Final[
    Mapping[ProjectileMotionClarificationTopic, ProjectileMotionCheckpointId]
] = MappingProxyType(
    {
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY: (
            ProjectileMotionCheckpointId.HORIZONTAL_VELOCITY_DETAIL
        ),
        ProjectileMotionClarificationTopic.APEX_ACCELERATION: (
            ProjectileMotionCheckpointId.APEX_ACCELERATION_DETAIL
        ),
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY: (
            ProjectileMotionCheckpointId.FLIGHT_SYMMETRY_DETAIL
        ),
    }
)


def projectile_motion_checkpoints_through(
    stage: ProjectileMotionStage,
) -> tuple[ProjectileMotionMainCheckpoint, ...]:
    """Return the immutable main-checkpoint prefix represented by ``stage``."""

    return PROJECTILE_MOTION_STAGE_PREFIXES[stage]


def projectile_motion_checkpoint_prefix(
    last_checkpoint: ProjectileMotionMainCheckpoint | None,
) -> tuple[ProjectileMotionMainCheckpoint, ...]:
    """Return the exact settled prefix for a nullable component frontier."""

    if last_checkpoint is None:
        return ()
    ordinal = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(last_checkpoint)
    return PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[: ordinal + 1]


def projectile_motion_introduction_stage_for(
    checkpoint: ProjectileMotionMainCheckpoint,
) -> ProjectileMotionStage:
    """Return the first routed stage whose prefix includes ``checkpoint``."""

    return _PROJECTILE_MOTION_CHECKPOINT_INTRODUCTION_STAGES[checkpoint]


def next_projectile_motion_main_checkpoint(
    last_checkpoint: ProjectileMotionMainCheckpoint | None,
) -> ProjectileMotionMainCheckpoint | None:
    """Return the next main checkpoint, or ``None`` at the final frontier."""

    prefix = projectile_motion_checkpoint_prefix(last_checkpoint)
    if len(prefix) == len(PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER):
        return None
    return PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[len(prefix)]


ClarifiedProjectileMotionTopics = Annotated[
    tuple[ProjectileMotionClarificationTopic, ...],
    Field(max_length=len(PROJECTILE_MOTION_CLARIFICATION_ORDER)),
]


class ProjectileMotionStateV1(LiveSceneContract):
    """Problem-bound settled frontier for one living projectile model."""

    kind: Literal["projectile_motion"] = "projectile_motion"
    id: ChoreographyComponentId
    problem_spec: ProjectileMotionProblemSpecV1 = Field(alias="problemSpec")
    last_main_checkpoint: ProjectileMotionMainCheckpoint | None = Field(
        default=None,
        alias="lastMainCheckpoint",
    )
    clarified_topics: ClarifiedProjectileMotionTopics = Field(
        default=(),
        alias="clarifiedTopics",
    )
    active_clarification: ProjectileMotionClarificationTopic | None = Field(
        default=None,
        alias="activeClarification",
    )

    @model_validator(mode="after")
    def validate_clarification_frontier(self) -> Self:
        if len(self.clarified_topics) != len(set(self.clarified_topics)):
            raise ValueError("clarifiedTopics must be unique")
        expected_order = tuple(
            topic
            for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER
            if topic in self.clarified_topics
        )
        if self.clarified_topics != expected_order:
            raise ValueError("clarifiedTopics must use canonical pedagogical order")

        prefix = projectile_motion_checkpoint_prefix(self.last_main_checkpoint)
        for topic in self.clarified_topics:
            prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
            if prerequisite not in prefix:
                raise ValueError(
                    f"{topic.value} clarification requires a frontier at or after "
                    f"{prerequisite.value}"
                )
        if (
            self.active_clarification is not None
            and self.active_clarification not in self.clarified_topics
        ):
            raise ValueError("activeClarification must be present in clarifiedTopics")
        return self


class AdvanceProjectileMotionRouteV1(LiveSceneContract):
    """Advance the main lesson to one closed stage."""

    intent: Literal["advance"] = "advance"
    target_stage: ProjectileMotionStage = Field(alias="targetStage")


class ClarifyProjectileMotionRouteV1(LiveSceneContract):
    """Show one server-authored visual detour at the current frontier."""

    intent: Literal["clarify"] = "clarify"
    topic: ProjectileMotionClarificationTopic


class RetargetProjectileMotionRouteV1(LiveSceneContract):
    """Morph the settled model to one complete supported target problem."""

    intent: Literal["retarget"] = "retarget"
    target_problem_spec: ProjectileMotionProblemSpecV1 = Field(alias="targetProblemSpec")


ProjectileMotionRouteV1: TypeAlias = Annotated[
    AdvanceProjectileMotionRouteV1
    | ClarifyProjectileMotionRouteV1
    | RetargetProjectileMotionRouteV1,
    Field(discriminator="intent"),
]


class RoutedProjectileMotionBeatV1(LiveSceneContract):
    """Server-lowered, problem-bound input to the projectile compiler.

    ``baseProblemSpec`` is null only for a fresh advance from an empty semantic
    scene.  Service validation later proves that the submitted frontier really
    is empty; every continuation, clarification, and retarget names its base.
    """

    v: Literal[PROJECTILE_MOTION_ROUTED_BEAT_VERSION] = PROJECTILE_MOTION_ROUTED_BEAT_VERSION
    beat_id: ChoreographyId = Field(alias="beatId")
    component_kind: Literal["projectile_motion"] = Field(
        default="projectile_motion",
        alias="componentKind",
    )
    component_id: ChoreographyComponentId = Field(alias="componentId")
    base_problem_spec: ProjectileMotionProblemSpecV1 | None = Field(alias="baseProblemSpec")
    result_problem_spec: ProjectileMotionProblemSpecV1 = Field(alias="resultProblemSpec")
    route: ProjectileMotionRouteV1

    @field_validator("v", mode="before")
    @classmethod
    def validate_strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("v must be a strict integer")
        return value

    @model_validator(mode="after")
    def validate_problem_transition(self) -> Self:
        if self.base_problem_spec is None:
            if not isinstance(self.route, AdvanceProjectileMotionRouteV1):
                raise ValueError("only a fresh advance may omit baseProblemSpec")
            return self

        if isinstance(self.route, RetargetProjectileMotionRouteV1):
            if self.route.target_problem_spec != self.result_problem_spec:
                raise ValueError("retarget targetProblemSpec must match resultProblemSpec")
            if self.base_problem_spec == self.result_problem_spec:
                raise ValueError("retarget must change the problem specification")
            return self

        if self.base_problem_spec != self.result_problem_spec:
            raise ValueError("advance and clarification must preserve the problem specification")
        return self


def routed_projectile_motion_beat_sha256(beat: RoutedProjectileMotionBeatV1) -> str:
    """Hash the exact server-lowered projectile transition."""

    return canonical_sha256(
        beat.model_dump(mode="json", by_alias=True),
        domain=PROJECTILE_MOTION_ROUTED_BEAT_HASH_DOMAIN,
    )


PROJECTILE_MOTION_ROUTE_V1_ADAPTER = TypeAdapter(ProjectileMotionRouteV1)
ROUTED_PROJECTILE_MOTION_BEAT_V1_ADAPTER = TypeAdapter(RoutedProjectileMotionBeatV1)


__all__ = [
    "PROJECTILE_MOTION_CLARIFICATION_CHECKPOINTS",
    "PROJECTILE_MOTION_CLARIFICATION_ORDER",
    "PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES",
    "PROJECTILE_MOTION_GRAVITY_MPS2",
    "PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER",
    "PROJECTILE_MOTION_PROBLEM_HASH_DOMAIN",
    "PROJECTILE_MOTION_PROBLEM_VERSION",
    "PROJECTILE_MOTION_ROUTED_BEAT_HASH_DOMAIN",
    "PROJECTILE_MOTION_ROUTED_BEAT_VERSION",
    "PROJECTILE_MOTION_ROUTE_V1_ADAPTER",
    "PROJECTILE_MOTION_STAGE_PREFIXES",
    "ROUTED_PROJECTILE_MOTION_BEAT_V1_ADAPTER",
    "SUPPORTED_PROJECTILE_ANGLES_DEG",
    "SUPPORTED_PROJECTILE_SPEEDS_MPS",
    "AdvanceProjectileMotionRouteV1",
    "ClarifyProjectileMotionRouteV1",
    "ProjectileMotionCheckpointId",
    "ProjectileMotionClarificationTopic",
    "ProjectileMotionMainCheckpoint",
    "ProjectileMotionProblemSpecV1",
    "ProjectileMotionRouteV1",
    "ProjectileMotionStage",
    "ProjectileMotionStateV1",
    "RetargetProjectileMotionRouteV1",
    "RoutedProjectileMotionBeatV1",
    "next_projectile_motion_main_checkpoint",
    "projectile_motion_checkpoint_prefix",
    "projectile_motion_checkpoints_through",
    "projectile_motion_introduction_stage_for",
    "projectile_motion_problem_sha256",
    "routed_projectile_motion_beat_sha256",
]
