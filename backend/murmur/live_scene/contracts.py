"""Strict contracts for incremental model-authored live scenes.

The model authors only :class:`ScenePatchDraft` records. Lifecycle fields such
as generation, attempt, sequence, and scene revisions are assigned by the
server and exist only on the SSE event contracts below.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic.types import StringConstraints

LIVE_SCENE_SCHEMA_VERSION = 1
MAX_SAFE_SEQUENCE = 9_007_199_254_740_991

MAX_SCENE_PROMPT_CHARS = 2_000
MAX_SCENE_NODES = 128
MAX_PATCH_OPERATIONS = 16
MAX_ACCEPTED_PATCHES = 8
MAX_PATH_POINTS = 128
MAX_SCENE_TEXT_CHARS = 512
MAX_SCENE_NARRATION_CHARS = 512
MAX_SCENE_ERROR_CHARS = 512
MAX_NDJSON_FRAME_BYTES = 64 * 1024
MAX_SCENE_MODEL_OUTPUT_TOKENS = 4_096

LIVE_SCENE_BOARD_WIDTH = 800.0
LIVE_SCENE_BOARD_HEIGHT = 600.0
MAX_STROKE_WIDTH = 32.0
MIN_FONT_SIZE = 8.0
MAX_FONT_SIZE = 96.0
MAX_ROUGHNESS = 4.0

THEME_PAINTS = frozenset(
    {
        "hsl(var(--amber))",
        "hsl(var(--chalk))",
        "hsl(var(--chalk-soft))",
        "hsl(var(--ember))",
        "hsl(var(--lavender))",
        "hsl(var(--sage))",
    }
)
SHAPE_FILL_PAINTS = THEME_PAINTS | {"none", "transparent"}
_HEX_PAINT_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class LiveSceneContract(BaseModel):
    """Fail-closed, immutable configuration shared by every Gate 1 model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        populate_by_name=True,
        serialize_by_alias=True,
    )


SceneNodeId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    ),
]
PatchId = SceneNodeId
PositiveSequence = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_SEQUENCE)]
AttemptNumber = Annotated[int, Field(strict=True, ge=1, le=2)]
PatchSequence = Annotated[int, Field(strict=True, ge=1, le=MAX_ACCEPTED_PATCHES)]
NonNegativeRevision = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_SEQUENCE)]
PositiveRevision = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_SEQUENCE)]
XCoordinate = Annotated[float, Field(strict=True, ge=0, le=LIVE_SCENE_BOARD_WIDTH)]
YCoordinate = Annotated[float, Field(strict=True, ge=0, le=LIVE_SCENE_BOARD_HEIGHT)]
Width = Annotated[float, Field(strict=True, gt=0, le=LIVE_SCENE_BOARD_WIDTH)]
Height = Annotated[float, Field(strict=True, gt=0, le=LIVE_SCENE_BOARD_HEIGHT)]
StrokeWidth = Annotated[float, Field(strict=True, gt=0, le=MAX_STROKE_WIDTH)]
FontSize = Annotated[float, Field(strict=True, ge=MIN_FONT_SIZE, le=MAX_FONT_SIZE)]
Opacity = Annotated[float, Field(strict=True, ge=0, le=1)]
Roughness = Annotated[float, Field(strict=True, ge=0, le=MAX_ROUGHNESS)]
Milliseconds = Annotated[float, Field(strict=True, ge=0)]
ScenePoint: TypeAlias = tuple[XCoordinate, YCoordinate]

PromptText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SCENE_PROMPT_CHARS,
    ),
]
SceneText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SCENE_TEXT_CHARS,
    ),
]
NarrationText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SCENE_NARRATION_CHARS,
    ),
]
FriendlyMessage = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SCENE_ERROR_CHARS,
    ),
]
FailureCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    ),
]
Paint = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=32)]


def _validate_paint(value: str, *, allow_empty_fill: bool) -> str:
    allowed = SHAPE_FILL_PAINTS if allow_empty_fill else THEME_PAINTS
    if value not in allowed and _HEX_PAINT_PATTERN.fullmatch(value) is None:
        expected = "a theme paint or six-digit hex color"
        if allow_empty_fill:
            expected += ", plus none or transparent"
        raise ValueError(f"paint must be {expected}")
    return value


class ScenePresentation(LiveSceneContract):
    enter: Literal["draw", "fade", "scale", "none"]
    exit: Literal["fade", "none"]


class StrokeStyle(LiveSceneContract):
    stroke: Paint
    stroke_width: StrokeWidth = Field(alias="strokeWidth")
    opacity: Opacity
    roughness: Roughness

    @field_validator("stroke")
    @classmethod
    def validate_stroke(cls, value: str) -> str:
        return _validate_paint(value, allow_empty_fill=False)


class ShapeStyle(StrokeStyle):
    fill: Paint

    @field_validator("fill")
    @classmethod
    def validate_fill(cls, value: str) -> str:
        return _validate_paint(value, allow_empty_fill=True)


class TextStyle(LiveSceneContract):
    color: Paint
    font_size: FontSize = Field(alias="fontSize")
    opacity: Opacity
    anchor: Literal["start", "middle", "end"]

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        return _validate_paint(value, allow_empty_fill=False)


class LatexStyle(LiveSceneContract):
    color: Paint
    font_size: FontSize = Field(alias="fontSize")
    opacity: Opacity

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        return _validate_paint(value, allow_empty_fill=False)


class LineSceneNode(LiveSceneContract):
    id: SceneNodeId
    kind: Literal["line"]
    presentation: ScenePresentation
    points: tuple[ScenePoint, ScenePoint]
    style: StrokeStyle


class PathSceneNode(LiveSceneContract):
    id: SceneNodeId
    kind: Literal["path"]
    presentation: ScenePresentation
    points: Annotated[tuple[ScenePoint, ...], Field(min_length=2, max_length=MAX_PATH_POINTS)]
    closed: bool = Field(strict=True)
    style: ShapeStyle


class RectSceneNode(LiveSceneContract):
    id: SceneNodeId
    kind: Literal["rect"]
    presentation: ScenePresentation
    x: XCoordinate
    y: YCoordinate
    width: Width
    height: Height
    style: ShapeStyle

    @model_validator(mode="after")
    def validate_inside_board(self) -> Self:
        if self.x + self.width > LIVE_SCENE_BOARD_WIDTH:
            raise ValueError("rectangle must stay inside the board width")
        if self.y + self.height > LIVE_SCENE_BOARD_HEIGHT:
            raise ValueError("rectangle must stay inside the board height")
        return self


class TextSceneNode(LiveSceneContract):
    id: SceneNodeId
    kind: Literal["text"]
    presentation: ScenePresentation
    x: XCoordinate
    y: YCoordinate
    text: SceneText
    style: TextStyle


class LatexSceneNode(LiveSceneContract):
    id: SceneNodeId
    kind: Literal["latex"]
    presentation: ScenePresentation
    x: XCoordinate
    y: YCoordinate
    latex: SceneText
    style: LatexStyle


SceneNode: TypeAlias = Annotated[
    LineSceneNode | PathSceneNode | RectSceneNode | TextSceneNode | LatexSceneNode,
    Field(discriminator="kind"),
]


class SceneState(LiveSceneContract):
    revision: NonNegativeRevision
    nodes: Annotated[tuple[SceneNode, ...], Field(max_length=MAX_SCENE_NODES)] = ()

    @model_validator(mode="after")
    def validate_unique_node_ids(self) -> Self:
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("scene node ids must be unique")
        return self


class PutSceneOperation(LiveSceneContract):
    op: Literal["put"]
    node: SceneNode

    @property
    def target_id(self) -> str:
        return self.node.id


class RemoveSceneOperation(LiveSceneContract):
    op: Literal["remove"]
    id: SceneNodeId

    @property
    def target_id(self) -> str:
        return self.id


ScenePatchOperation: TypeAlias = Annotated[
    PutSceneOperation | RemoveSceneOperation,
    Field(discriminator="op"),
]


class ScenePatchDraft(LiveSceneContract):
    """One lifecycle-free NDJSON record authored by the model."""

    v: Literal[LIVE_SCENE_SCHEMA_VERSION] = LIVE_SCENE_SCHEMA_VERSION
    patch_id: PatchId = Field(alias="patchId")
    narration: NarrationText
    operations: Annotated[
        tuple[ScenePatchOperation, ...],
        Field(min_length=1, max_length=MAX_PATCH_OPERATIONS),
    ]

    @model_validator(mode="after")
    def validate_unique_operation_targets(self) -> Self:
        targets = [operation.target_id for operation in self.operations]
        if len(targets) != len(set(targets)):
            raise ValueError("patch operation targets must be unique")
        return self


class LiveSceneRequest(LiveSceneContract):
    """Immutable request snapshot for one user-owned generation."""

    prompt: PromptText
    generation: PositiveSequence
    base_scene: SceneState = Field(alias="baseScene")


class SceneStreamStartedEvent(LiveSceneContract):
    type: Literal["scene_stream_started"] = "scene_stream_started"
    generation: PositiveSequence
    attempt: AttemptNumber
    base_revision: NonNegativeRevision = Field(alias="baseRevision")


class ScenePatchEvent(LiveSceneContract):
    type: Literal["scene_patch"] = "scene_patch"
    generation: PositiveSequence
    attempt: AttemptNumber
    sequence: PatchSequence
    base_revision: NonNegativeRevision = Field(alias="baseRevision")
    result_revision: PositiveRevision = Field(alias="resultRevision")
    patch: ScenePatchDraft

    @model_validator(mode="after")
    def validate_revision_boundary(self) -> Self:
        if self.result_revision != self.base_revision + 1:
            raise ValueError("resultRevision must be exactly one greater than baseRevision")
        return self


class SceneStreamRepairingEvent(LiveSceneContract):
    type: Literal["scene_stream_repairing"] = "scene_stream_repairing"
    generation: PositiveSequence
    from_attempt: AttemptNumber = Field(alias="fromAttempt")
    to_attempt: AttemptNumber = Field(alias="toAttempt")
    last_accepted_revision: NonNegativeRevision = Field(alias="lastAcceptedRevision")
    message: FriendlyMessage

    @model_validator(mode="after")
    def validate_attempt_boundary(self) -> Self:
        if self.to_attempt != self.from_attempt + 1:
            raise ValueError("toAttempt must be exactly one greater than fromAttempt")
        return self


class SceneStreamCompletedEvent(LiveSceneContract):
    type: Literal["scene_stream_completed"] = "scene_stream_completed"
    generation: PositiveSequence
    final_revision: PositiveRevision = Field(alias="finalRevision")
    patch_count: Annotated[
        int,
        Field(strict=True, ge=1, le=MAX_ACCEPTED_PATCHES),
    ] = Field(alias="patchCount")
    first_patch_ms: Milliseconds = Field(alias="firstPatchMs")
    total_ms: Milliseconds = Field(alias="totalMs")
    repaired: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_latency_order(self) -> Self:
        if self.total_ms < self.first_patch_ms:
            raise ValueError("totalMs must not be less than firstPatchMs")
        return self


class SceneStreamFailedEvent(LiveSceneContract):
    type: Literal["scene_stream_failed"] = "scene_stream_failed"
    generation: PositiveSequence
    attempt: AttemptNumber
    code: FailureCode
    message: FriendlyMessage
    last_accepted_revision: NonNegativeRevision = Field(alias="lastAcceptedRevision")
    retryable: bool = Field(strict=True)


SceneStreamEvent: TypeAlias = Annotated[
    SceneStreamStartedEvent
    | ScenePatchEvent
    | SceneStreamRepairingEvent
    | SceneStreamCompletedEvent
    | SceneStreamFailedEvent,
    Field(discriminator="type"),
]

SCENE_NODE_ADAPTER = TypeAdapter(SceneNode)
SCENE_PATCH_OPERATION_ADAPTER = TypeAdapter(ScenePatchOperation)
SCENE_STREAM_EVENT_ADAPTER = TypeAdapter(SceneStreamEvent)

# Descriptive aliases used by service/API code without changing the wire schema.
SceneGenerationRequest = LiveSceneRequest
ScenePatch = ScenePatchDraft


def dump_scene_stream_event(event: SceneStreamEvent) -> dict[str, object]:
    """Return the canonical camelCase JSON-ready representation of one SSE event."""

    return SCENE_STREAM_EVENT_ADAPTER.dump_python(event, mode="json", by_alias=True)
