"""Versioned contracts shared by the Conductor and background Reasoner."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    field_serializer,
    model_validator,
)
from pydantic.types import StringConstraints

from murmur.core.immutable_json import freeze_json, thaw_json

REASONING_SCHEMA_VERSION = 1
MAX_SAFE_SEQUENCE = 9_007_199_254_740_991

ContractId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=16_000),
]
PositiveSequence = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_SEQUENCE)]
NonNegativeRevision = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_SEQUENCE)]


class ContractModel(BaseModel):
    """Common fail-closed configuration for wire and storage records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )


class TaskStatus(str, Enum):
    QUEUED = "queued"
    WORKING = "working"
    NEEDS_INPUT = "needs_input"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


_TASK_TRANSITIONS: Mapping[TaskStatus | None, frozenset[TaskStatus]] = {
    None: frozenset({TaskStatus.QUEUED}),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.WORKING,
            TaskStatus.NEEDS_INPUT,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.WORKING: frozenset(
        {
            TaskStatus.NEEDS_INPUT,
            TaskStatus.VERIFIED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.NEEDS_INPUT: frozenset(
        {
            TaskStatus.WORKING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
        }
    ),
    TaskStatus.VERIFIED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.SUPERSEDED: frozenset(),
}


class TaskTransition(ContractModel):
    """One validated lifecycle transition for one task generation."""

    schema_version: Literal[REASONING_SCHEMA_VERSION] = REASONING_SCHEMA_VERSION
    task_id: ContractId
    task_generation: PositiveSequence
    from_status: TaskStatus | None
    to_status: TaskStatus
    reason: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        allowed = _TASK_TRANSITIONS[self.from_status]
        if self.to_status not in allowed:
            source = self.from_status.value if self.from_status is not None else "initial"
            raise ValueError(f"invalid task transition: {source} -> {self.to_status.value}")
        return self


class CanvasAction(str, Enum):
    RECT = "rect"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    LINE = "line"
    ARROW = "arrow"
    TEXT = "text"
    PATH = "path"
    CURVE = "curve"
    CLEAR = "clear"
    DELETE = "delete"
    HIGHLIGHT = "highlight"


class AnimateStyle(str, Enum):
    DRAW = "draw"
    FADE = "fade"
    SCALE = "scale"
    NONE = "none"


StrictFinite = Annotated[float, Field(strict=True)]
StrictPositive = Annotated[float, Field(strict=True, gt=0)]
StrictNonNegative = Annotated[float, Field(strict=True, ge=0)]
Point: TypeAlias = tuple[StrictFinite, StrictFinite]


class CanvasOperation(ContractModel):
    """A normalized operation accepted by the existing SVG canvas."""

    action: CanvasAction
    id: ContractId | None = None
    target_id: ContractId | None = None
    label: NonEmptyText | None = None
    x: StrictFinite | None = None
    y: StrictFinite | None = None
    width: StrictPositive | None = None
    height: StrictPositive | None = None
    color: NonEmptyText | None = None
    fill: str | None = None
    stroke_width: StrictPositive | None = None
    points: tuple[Point, ...] = ()
    text: NonEmptyText | None = None
    font_size: StrictPositive | None = None
    font_family: NonEmptyText | None = None
    roughness: StrictNonNegative | None = None
    animate_style: AnimateStyle | None = None
    highlight_color: NonEmptyText | None = None
    centered: StrictBool | None = Field(
        default=None,
        alias="_centered",
        serialization_alias="_centered",
    )

    @model_validator(mode="after")
    def validate_action_shape(self) -> Self:
        creation_actions = {
            CanvasAction.RECT,
            CanvasAction.CIRCLE,
            CanvasAction.ELLIPSE,
            CanvasAction.LINE,
            CanvasAction.ARROW,
            CanvasAction.TEXT,
            CanvasAction.PATH,
            CanvasAction.CURVE,
        }
        if self.action in creation_actions and self.id is None:
            raise ValueError(f"{self.action.value} requires a stable id")
        if self.action in {CanvasAction.DELETE, CanvasAction.HIGHLIGHT}:
            if self.id is None and self.target_id is None:
                raise ValueError(f"{self.action.value} requires id or target_id")
            if self.id is not None and self.target_id is not None and self.id != self.target_id:
                raise ValueError(f"{self.action.value} cannot target two different elements")
        if self.action in {CanvasAction.RECT, CanvasAction.ELLIPSE} and (
            self.width is None or self.height is None
        ):
            raise ValueError(f"{self.action.value} requires positive width and height")
        if self.action is CanvasAction.CIRCLE and self.width is None:
            raise ValueError("circle requires a positive width")
        if (
            self.action
            in {
                CanvasAction.LINE,
                CanvasAction.ARROW,
                CanvasAction.PATH,
                CanvasAction.CURVE,
            }
            and len(self.points) < 2
        ):
            raise ValueError(f"{self.action.value} requires at least two points")
        if self.action is CanvasAction.TEXT and self.text is None:
            raise ValueError("text requires non-empty text")
        if self.action is CanvasAction.CLEAR and (
            self.id is not None or self.target_id is not None
        ):
            raise ValueError("clear cannot target a canvas element")
        return self


class OperationsArtifactV1(ContractModel):
    artifact_type: Literal["operations_v1"] = "operations_v1"
    operations: Annotated[tuple[CanvasOperation, ...], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> Self:
        creation_actions = {
            CanvasAction.RECT,
            CanvasAction.CIRCLE,
            CanvasAction.ELLIPSE,
            CanvasAction.LINE,
            CanvasAction.ARROW,
            CanvasAction.TEXT,
            CanvasAction.PATH,
            CanvasAction.CURVE,
        }
        operation_ids = [
            operation.id
            for operation in self.operations
            if operation.action in creation_actions and operation.id is not None
        ]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation ids must be unique within an artifact")
        return self


class SDLComponent(str, Enum):
    RIGHT_TRIANGLE = "right_triangle"
    EQUATION = "equation"
    COORDINATE_PLANE = "coordinate_plane"
    FUNCTION_PLOT = "function_plot"
    NUMBER_LINE = "number_line"
    BAR_CHART = "bar_chart"
    FLOWCHART = "flowchart"
    TREE = "tree"
    VENN_DIAGRAM = "venn_diagram"
    CIRCLE_DIAGRAM = "circle_diagram"
    LABEL = "label"


class SDLBelowPosition(ContractModel):
    below: ContractId
    gap: StrictNonNegative | None = None


class SDLRightOfPosition(ContractModel):
    right_of: ContractId = Field(alias="rightOf", serialization_alias="rightOf")
    gap: StrictNonNegative | None = None


SDLPosition: TypeAlias = (
    Literal["center", "top", "bottom", "left", "right"] | SDLBelowPosition | SDLRightOfPosition
)


class SDLShowDirective(ContractModel):
    component: SDLComponent
    props: Mapping[str, JsonValue]
    position: SDLPosition | None = None
    id: ContractId | None = None

    @model_validator(mode="after")
    def freeze_props(self) -> Self:
        object.__setattr__(self, "props", freeze_json(dict(self.props)))
        return self

    @field_serializer("props")
    def serialize_props(self, props: Mapping[str, JsonValue]) -> JsonValue:
        return thaw_json(props)


class SDLStep(ContractModel):
    say: NonEmptyText
    show: SDLShowDirective | None = None
    highlight: ContractId | Annotated[tuple[ContractId, ...], Field(min_length=1)] | None = None
    clear: StrictBool = False


class SDLScene(ContractModel):
    steps: Annotated[tuple[SDLStep, ...], Field(min_length=1, max_length=100)]


class SDLSceneArtifactV2(ContractModel):
    artifact_type: Literal["sdl_scene_v2"] = "sdl_scene_v2"
    scene: SDLScene


CanvasArtifact: TypeAlias = Annotated[
    OperationsArtifactV1 | SDLSceneArtifactV2,
    Field(discriminator="artifact_type"),
]


class ArtifactProposal(ContractModel):
    schema_version: Literal[REASONING_SCHEMA_VERSION] = REASONING_SCHEMA_VERSION
    proposal_id: ContractId
    request_id: ContractId
    session_id: ContractId
    task_id: ContractId
    task_generation: PositiveSequence
    base_revision: NonNegativeRevision
    artifact: CanvasArtifact


class ToolPolicy(ContractModel):
    """Tool permissions captured when a reasoning request is enqueued."""

    allowed_tools: tuple[ContractId, ...] = ()
    allow_side_effects: StrictBool = False

    @model_validator(mode="after")
    def validate_unique_tools(self) -> Self:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed_tools must be unique")
        return self


class ReasoningRequest(ContractModel):
    """Immutable snapshot submitted to the background reasoning lane."""

    schema_version: Literal[REASONING_SCHEMA_VERSION] = REASONING_SCHEMA_VERSION
    request_id: ContractId
    user_id: ContractId
    session_id: ContractId
    agent_id: ContractId
    agent_config_revision: ContractId
    turn_id: ContractId
    task_id: ContractId
    task_generation: PositiveSequence
    committed_turn_text: NonEmptyText
    memory_reference_ids: tuple[ContractId, ...] = ()
    resource_reference_ids: tuple[ContractId, ...] = ()
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    canvas_base_revision: NonNegativeRevision = 0

    @model_validator(mode="after")
    def validate_unique_references(self) -> Self:
        for field_name in ("memory_reference_ids", "resource_reference_ids"):
            references = getattr(self, field_name)
            if len(references) != len(set(references)):
                raise ValueError(f"{field_name} must be unique")
        return self


class ProgressKind(str, Enum):
    ACKNOWLEDGEMENT = "acknowledgement"
    PROGRESS = "progress"
    NEEDS_INPUT = "needs_input"


class ReasoningProgress(ContractModel):
    schema_version: Literal[REASONING_SCHEMA_VERSION] = REASONING_SCHEMA_VERSION
    progress_id: ContractId
    request_id: ContractId
    session_id: ContractId
    task_id: ContractId
    task_generation: PositiveSequence
    sequence: PositiveSequence
    kind: ProgressKind
    message: NonEmptyText


class ReasoningResult(ContractModel):
    schema_version: Literal[REASONING_SCHEMA_VERSION] = REASONING_SCHEMA_VERSION
    result_id: ContractId
    request_id: ContractId
    session_id: ContractId
    task_id: ContractId
    task_generation: PositiveSequence
    answer: NonEmptyText
    artifact_proposal_ids: tuple[ContractId, ...] = ()

    @model_validator(mode="after")
    def validate_unique_proposal_ids(self) -> Self:
        if len(self.artifact_proposal_ids) != len(set(self.artifact_proposal_ids)):
            raise ValueError("artifact_proposal_ids must be unique")
        return self
