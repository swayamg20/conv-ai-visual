"""Transactional Gate 1.6 parametric choreography orchestration.

The service keeps problem identity, accepted-state validation, route lowering,
deterministic compilation, and event admission on the server.  Reflex requests
never touch a provider dependency.  Director requests resolve a client only
after the complete submitted frontier and every deterministic problem premise
have passed validation.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from pydantic import ValidationError

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.choreography_contracts import (
    ClarifyCornerRouteV2,
    RoutedChoreographyBeatV3,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.completing_square_problem_parser import (
    CompletingSquareProblemFailureReason,
    bind_completing_square_problem,
)
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_MODEL_OUTPUT_TOKENS,
    MAX_SCENE_NODES,
    PutSceneOperation,
    ScenePatchDraft,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.parametric_checkpoint_compiler import (
    CompiledParametricCheckpointBeatV3,
    compile_parametric_checkpoint_beat,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CompiledCheckpointV3,
)
from murmur.live_scene.parametric_choreography_director import (
    DEFAULT_PARAMETRIC_DIRECTOR_MAX_TOKENS,
    ParametricChoreographyDirectorClient,
    ParametricChoreographyDirectorEngine,
    ParametricChoreographyDirectorResult,
)
from murmur.live_scene.parametric_choreography_requests import (
    ParametricChoreographyDirectorRequestV3,
    ParametricChoreographyReflexRequestV3,
    ParametricChoreographyRequestV3,
)
from murmur.live_scene.parametric_choreography_routing import (
    PARAMETRIC_CHOREOGRAPHY_COMPONENT_ID,
    AbstainParametricChoreographyDecisionV1,
    ParametricChoreographyRoutingError,
    ParametricChoreographyRoutingErrorCode,
    ResolvedParametricChoreographyAct,
    lower_resolved_parametric_choreography_act,
    resolve_parametric_director_decision,
    resolve_parametric_reflex_route,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricCheckpointSemanticMetadataV3,
    ParametricChoreographyDeclineReason,
    ParametricChoreographyFailureCode,
    ParametricChoreographySceneCheckpointEventV3,
    ParametricChoreographySceneStreamDeclinedEventV3,
    ParametricChoreographySceneStreamEventV3,
    ParametricChoreographySceneStreamFailedEventV3,
)
from murmur.live_scene.parametric_choreography_wire import (
    encode_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    ParametricCompletingSquareCompilationError,
    materialize_parametric_nodes,
)
from murmur.live_scene.parametric_completing_square_verifier import (
    ParametricCompletingSquareVerificationError,
    verify_parametric_completing_square_checkpoint,
    verify_parametric_completing_square_frontier,
)
from murmur.live_scene.semantic_contracts import (
    MAX_SEMANTIC_COMPONENTS,
    SemanticSceneState,
    VisualActAbstainReason,
)
from murmur.live_scene.visual_act_engine import (
    VisualActEngineError,
    VisualActEngineErrorCode,
    VisualActRoutingRepairing,
)
from murmur.live_scene.wire import SceneStreamWireError

ParametricDirectorClientFactory: TypeAlias = Callable[[], ParametricChoreographyDirectorClient]
SceneClock: TypeAlias = Callable[[], float]

_BASE_MISMATCH_MESSAGE = (
    "The parametric lesson no longer matches the visible board. Refresh before continuing."
)
_CAPACITY_MESSAGE = (
    "This board has no room for the requested visual sequence. The current board remains safe."
)
_REVISION_LIMIT_MESSAGE = (
    "This board has reached its revision limit. The current board remains safe."
)
_INTEGRITY_MESSAGE = (
    "The parametric choreography runtime rejected an internal result. "
    "The current board remains safe."
)
_REPAIR_MESSAGE = (
    "The first visual direction needed correction. The current board is safe while I retry."
)
_INVALID_VISUAL_MESSAGE = (
    "I couldn't resolve a valid visual direction. The current board remains safe; please try again."
)
_PROVIDER_ERROR_MESSAGE = (
    "The visual director is temporarily unavailable. The current board remains safe; "
    "please try again."
)
_PROVIDER_TIMEOUT_MESSAGE = (
    "The visual director took too long. The current board remains safe; please try again."
)
_PROVIDER_RATE_LIMIT_MESSAGE = "Visual director capacity is busy. Please try again shortly."
_CONTEXT_LIMIT_MESSAGE = (
    "This lesson context is too large for another director pass. The current board remains safe."
)
_UNSUPPORTED_MESSAGE = (
    "This request does not match a visual direction I can use yet. The current board is unchanged."
)
_NO_FORWARD_MESSAGE = (
    "That visual is already complete or unavailable at this stage. The current board is unchanged."
)
_DECLINE_MESSAGES: dict[ParametricChoreographyDeclineReason, str] = {
    CompletingSquareProblemFailureReason.REQUIRED: (
        "Enter one supported equation before starting this visual lesson."
    ),
    CompletingSquareProblemFailureReason.AMBIGUOUS: (
        "I could not identify exactly one completing-square equation. The board is unchanged."
    ),
    CompletingSquareProblemFailureReason.UNSUPPORTED: (
        "That equation is outside the completing-square family supported by this lesson."
    ),
    CompletingSquareProblemFailureReason.CONFLICT: (
        "That equation or corner value conflicts with the accepted lesson. The board is unchanged."
    ),
    VisualActAbstainReason.UNSUPPORTED_INTENT: _UNSUPPORTED_MESSAGE,
    VisualActAbstainReason.NO_FORWARD_PROGRESS: _NO_FORWARD_MESSAGE,
}

_FAILURE_MESSAGES: dict[ParametricChoreographyFailureCode, str] = {
    ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH: _BASE_MISMATCH_MESSAGE,
    ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED: _CAPACITY_MESSAGE,
    ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT: _CAPACITY_MESSAGE,
    ParametricChoreographyFailureCode.REVISION_LIMIT: _REVISION_LIMIT_MESSAGE,
    ParametricChoreographyFailureCode.CONTEXT_TOO_LARGE: _CONTEXT_LIMIT_MESSAGE,
    ParametricChoreographyFailureCode.INVALID_VISUAL_ACT: _INVALID_VISUAL_MESSAGE,
    ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED: _PROVIDER_RATE_LIMIT_MESSAGE,
    ParametricChoreographyFailureCode.PROVIDER_TIMEOUT: _PROVIDER_TIMEOUT_MESSAGE,
    ParametricChoreographyFailureCode.PROVIDER_ERROR: _PROVIDER_ERROR_MESSAGE,
    ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR: _INTEGRITY_MESSAGE,
}
_DIRECTOR_FAILURES: dict[VisualActEngineErrorCode, ParametricChoreographyFailureCode] = {
    VisualActEngineErrorCode.CONTEXT_INVALID: ParametricChoreographyFailureCode.CONTEXT_TOO_LARGE,
    VisualActEngineErrorCode.PROVIDER_RATE_LIMIT: (
        ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED
    ),
    VisualActEngineErrorCode.PROVIDER_TIMEOUT: ParametricChoreographyFailureCode.PROVIDER_TIMEOUT,
    VisualActEngineErrorCode.PROVIDER_ERROR: ParametricChoreographyFailureCode.PROVIDER_ERROR,
    VisualActEngineErrorCode.INVALID_VISUAL_ACT: (
        ParametricChoreographyFailureCode.INVALID_VISUAL_ACT
    ),
    VisualActEngineErrorCode.INTERNAL_ERROR: (
        ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR
    ),
}

# These patterns intentionally recognize numeric equality claims, not merely a
# number occurring near the word "corner".  A learner may mention the strip
# width and corner area together ("the 4 by 4 corner is 16") without the two
# dimension values being misread as competing area claims.
_EXPLICIT_CORNER_VALUE = re.compile(
    r"\b(?:the\s+)?(?:missing\s+)?corner(?:'s)?(?:\s+area)?\s*"
    r"(?:is|equals?|equal\s+to|=|should\s+be|has\s+(?:an?\s+)?area(?:\s+of)?)\s*"
    r"(?P<value>[+-]?(?:0|[1-9][0-9]*))\b"
    r"(?!\s*(?:by|x|\N{MULTIPLICATION SIGN})\s*[0-9])",
    re.IGNORECASE,
)
_QUESTION_CORNER_VALUE = re.compile(
    r"\b(?:why|how)\s+(?:exactly\s+)?(?:is|was|would\s+be)\s+"
    r"(?:the\s+)?(?:missing\s+)?corner(?:\s+area)?\s+"
    r"(?P<value>[+-]?(?:0|[1-9][0-9]*))\b"
    r"(?!\s*(?:by|x|\N{MULTIPLICATION SIGN})\s*[0-9])",
    re.IGNORECASE,
)
_CONTEXTUAL_CORNER_VALUE = re.compile(
    r"\b(?:why|how)\s+(?:exactly\s+)?(?:is|was|would\s+be)\s+"
    r"(?:it|that)\s+(?P<value>[+-]?(?:0|[1-9][0-9]*))\b"
    r"(?!\s*(?:by|x|\N{MULTIPLICATION SIGN})\s*[0-9])",
    re.IGNORECASE,
)


class _ParametricBaseError(ValueError):
    """Safe local marker for an invalid submitted scene frontier."""


class _ParametricCapacityError(ValueError):
    """Safe local marker for a suffix that cannot fit the fixed budgets."""


class _ParametricInvariantError(RuntimeError):
    """Safe local marker for a failed compiler or event trust boundary."""


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return max(0.0, (finished_at - started_at) * 1_000.0)


def _apply_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    """Independently materialize a checkpoint patch in stable node order."""

    order = [node.id for node in scene.nodes]
    by_id = {node.id: node for node in scene.nodes}
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            node_id = operation.node.id
            if node_id not in by_id:
                order.append(node_id)
            by_id[node_id] = operation.node
            continue
        if operation.id not in by_id:
            raise _ParametricInvariantError("checkpoint removed an absent node")
        del by_id[operation.id]
        order.remove(operation.id)
    try:
        result = SceneState(
            revision=scene.revision + 1,
            nodes=tuple(by_id[node_id] for node_id in order),
        )
    except ValidationError:
        raise _ParametricCapacityError("checkpoint exceeded the low-level scene budget") from None
    if result.nodes == scene.nodes:
        raise _ParametricInvariantError("checkpoint made no visible scene change")
    return result


def _accepted_parametric_frontier(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
) -> ParametricCompletingSquareStateV1 | None:
    """Reject every non-V3, dirty, orphaned, or unverified frontier."""

    if scene.revision != semantic_scene.revision:
        raise _ParametricBaseError("low-level and semantic revisions differ")
    if len(semantic_scene.components) > 1:
        raise _ParametricBaseError("multiple or mixed semantic components are unsupported")
    if not semantic_scene.components:
        if semantic_scene.certificate_head_sha256 is not None:
            raise _ParametricBaseError("an empty frontier cannot carry a certificate head")
        if scene.nodes:
            raise _ParametricBaseError("an empty semantic frontier cannot carry scene nodes")
        if scene.revision != 0:
            raise _ParametricBaseError("a fresh empty frontier must start at revision zero")
        return None

    component = semantic_scene.components[0]
    if not isinstance(component, ParametricCompletingSquareStateV1):
        raise _ParametricBaseError("only a V3 parametric component may be continued")
    if component.last_main_checkpoint is None:
        raise _ParametricBaseError("an unrevealed semantic component is orphaned")
    if semantic_scene.certificate_head_sha256 is None:
        raise _ParametricBaseError("a committed frontier requires a certificate head")
    if scene.revision == 0:
        raise _ParametricBaseError("a committed frontier requires a positive revision")
    try:
        expected_nodes = materialize_parametric_nodes(component)
        verify_parametric_completing_square_frontier(component, scene)
    except (
        ParametricCompletingSquareCompilationError,
        ParametricCompletingSquareVerificationError,
    ):
        raise _ParametricBaseError("the submitted V3 frontier failed realization") from None
    if scene.nodes != expected_nodes:
        raise _ParametricBaseError("the submitted board contains dirty or foreign nodes")
    return component


def _has_wrong_corner_claim(
    prompt: str,
    *,
    problem: CompletingSquareProblemSpecV1,
    component: ParametricCompletingSquareStateV1 | None,
) -> bool:
    claims = [int(match.group("value")) for match in _EXPLICIT_CORNER_VALUE.finditer(prompt)]
    claims.extend(int(match.group("value")) for match in _QUESTION_CORNER_VALUE.finditer(prompt))
    if component is not None and component.last_main_checkpoint is not None:
        frontier_reached_corner = list(CompletingSquareMainCheckpoint).index(
            component.last_main_checkpoint
        ) >= list(CompletingSquareMainCheckpoint).index(
            CompletingSquareMainCheckpoint.MISSING_CORNER
        )
        if frontier_reached_corner:
            claims.extend(
                int(match.group("value")) for match in _CONTEXTUAL_CORNER_VALUE.finditer(prompt)
            )
    return any(claim != problem.corner_value for claim in claims)


def _derive_result_semantic_scene(
    scene: SemanticSceneState,
    checkpoint: CompiledCheckpointV3,
) -> tuple[SemanticSceneState, ParametricCompletingSquareStateV1]:
    if len(scene.components) > 1:
        raise _ParametricInvariantError("checkpoint encountered multiple semantic components")
    existing = scene.components[0] if scene.components else None
    if existing is not None and not isinstance(existing, ParametricCompletingSquareStateV1):
        raise _ParametricInvariantError("checkpoint encountered a non-V3 component")
    if existing is not None and existing.id != checkpoint.beat.component_id:
        raise _ParametricInvariantError("checkpoint changed the accepted component identity")
    if existing is not None and existing.problem_spec != checkpoint.beat.problem_spec:
        raise _ParametricInvariantError("checkpoint changed the accepted problem identity")
    if existing is None and len(scene.components) >= MAX_SEMANTIC_COMPONENTS:
        raise _ParametricCapacityError("semantic component capacity is exhausted")
    if scene.revision >= MAX_SAFE_SEQUENCE:
        raise _ParametricCapacityError("semantic revision capacity is exhausted")

    if checkpoint.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        if (
            existing is None
            or existing.last_main_checkpoint is not CompletingSquareMainCheckpoint.MISSING_CORNER
            or existing.corner_clarified
        ):
            raise _ParametricInvariantError("corner detail did not extend its exact frontier")
        result_component = ParametricCompletingSquareStateV1(
            id=existing.id,
            problem_spec=existing.problem_spec,
            last_main_checkpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
            corner_clarified=True,
        )
    else:
        checkpoint_frontier = CompletingSquareMainCheckpoint(checkpoint.checkpoint_id.value)
        result_component = ParametricCompletingSquareStateV1(
            id=checkpoint.beat.component_id,
            problem_spec=checkpoint.beat.problem_spec,
            last_main_checkpoint=checkpoint_frontier,
            corner_clarified=existing.corner_clarified if existing is not None else False,
        )

    return (
        SemanticSceneState(
            revision=scene.revision + 1,
            components=(result_component,),
            certificate_head_sha256=checkpoint.certificate.certificate_sha256,
        ),
        result_component,
    )


def _roundtrip_event(
    event: ParametricChoreographySceneStreamEventV3,
) -> ParametricChoreographySceneStreamEventV3:
    """Encode and independently decode one exact browser-bound record."""

    encoded = encode_parametric_choreography_scene_stream_event(event)
    if not encoded.startswith("data: ") or not encoded.endswith("\n\n"):
        raise _ParametricInvariantError("wire encoder returned an invalid SSE frame")
    try:
        payload = json.loads(encoded.removeprefix("data: ").removesuffix("\n\n"))
        decoded = PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            payload,
            by_alias=True,
            by_name=False,
        )
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise _ParametricInvariantError("event failed independent wire roundtrip") from None
    if decoded != event:
        raise _ParametricInvariantError("wire roundtrip changed an event")
    return decoded


def _failure_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
    code: ParametricChoreographyFailureCode,
) -> ParametricChoreographySceneStreamFailedEventV3:
    event = ParametricChoreographySceneStreamFailedEventV3(
        generation=generation,
        attempt=attempt,
        code=code,
        message=_FAILURE_MESSAGES[code],
        last_accepted_revision=revision,
        retryable=code
        in {
            ParametricChoreographyFailureCode.INVALID_VISUAL_ACT,
            ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED,
            ParametricChoreographyFailureCode.PROVIDER_TIMEOUT,
            ParametricChoreographyFailureCode.PROVIDER_ERROR,
        },
    )
    return cast(ParametricChoreographySceneStreamFailedEventV3, _roundtrip_event(event))


def _decline_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
    reason: ParametricChoreographyDeclineReason,
) -> ParametricChoreographySceneStreamDeclinedEventV3:
    event = ParametricChoreographySceneStreamDeclinedEventV3(
        generation=generation,
        attempt=attempt,
        final_revision=revision,
        reason_code=reason,
        message=_DECLINE_MESSAGES[reason],
    )
    return cast(ParametricChoreographySceneStreamDeclinedEventV3, _roundtrip_event(event))


def _repairing_event(*, generation: int, revision: int) -> SceneStreamRepairingEvent:
    event = SceneStreamRepairingEvent(
        generation=generation,
        from_attempt=1,
        to_attempt=2,
        last_accepted_revision=revision,
        message=_REPAIR_MESSAGE,
    )
    return cast(SceneStreamRepairingEvent, _roundtrip_event(event))


@dataclass(frozen=True, slots=True)
class _Lifecycle:
    """Bind repetitive terminal construction to one immutable request."""

    request: ParametricChoreographyRequestV3

    def started(self) -> SceneStreamStartedEvent:
        event = SceneStreamStartedEvent(
            generation=self.request.generation,
            attempt=1,
            base_revision=self.request.base_scene.revision,
        )
        return cast(SceneStreamStartedEvent, _roundtrip_event(event))

    def failed(
        self,
        code: ParametricChoreographyFailureCode,
        *,
        attempt: int = 1,
    ) -> ParametricChoreographySceneStreamFailedEventV3:
        return _failure_event(
            generation=self.request.generation,
            attempt=attempt,
            revision=self.request.base_scene.revision,
            code=code,
        )

    def declined(
        self,
        reason: ParametricChoreographyDeclineReason,
        *,
        attempt: int = 1,
    ) -> ParametricChoreographySceneStreamDeclinedEventV3:
        return _decline_event(
            generation=self.request.generation,
            attempt=attempt,
            revision=self.request.base_scene.revision,
            reason=reason,
        )

    def repairing(self) -> SceneStreamRepairingEvent:
        return _repairing_event(
            generation=self.request.generation,
            revision=self.request.base_scene.revision,
        )


def _expected_checkpoint_ids(
    resolved: ResolvedParametricChoreographyAct,
) -> tuple[CompletingSquareCheckpointId, ...]:
    if isinstance(resolved.route, ClarifyCornerRouteV2):
        return (CompletingSquareCheckpointId.CORNER_DETAIL,)
    return tuple(
        CompletingSquareCheckpointId(checkpoint.value)
        for checkpoint in resolved.missing_checkpoints
    )


def _validate_resolved_identity(
    resolved: ResolvedParametricChoreographyAct,
    *,
    problem: CompletingSquareProblemSpecV1,
    accepted_component: ParametricCompletingSquareStateV1 | None,
    expected_route: object,
) -> None:
    expected_component_id = (
        accepted_component.id
        if accepted_component is not None
        else PARAMETRIC_CHOREOGRAPHY_COMPONENT_ID
    )
    if (
        resolved.component_kind != "completing_square_parametric"
        or resolved.component_id != expected_component_id
        or resolved.problem_spec != problem
        or resolved.route != expected_route
    ):
        raise _ParametricInvariantError("resolved route changed server-bound identity")


def _prepare_batch(
    request: ParametricChoreographyRequestV3,
    resolved: ResolvedParametricChoreographyAct,
    beat: RoutedChoreographyBeatV3,
    compiled_candidate: CompiledParametricCheckpointBeatV3,
    *,
    attempt: Literal[1, 2],
    started_at: float,
    clock: SceneClock,
) -> tuple[
    tuple[ParametricChoreographySceneCheckpointEventV3, ...],
    SceneStreamCompletedEvent,
]:
    """Revalidate, materialize, and wire-preflight the complete suffix atomically."""

    try:
        compiled = CompiledParametricCheckpointBeatV3.model_validate(
            compiled_candidate.model_dump(mode="json", by_alias=True)
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise _ParametricInvariantError("compiler result failed independent validation") from None
    if compiled.beat != beat:
        raise _ParametricInvariantError("compiler changed the routed beat")
    if compiled.base_scene != request.base_scene:
        raise _ParametricInvariantError("compiler changed the low-level base")
    if compiled.base_semantic_scene != request.base_semantic_scene:
        raise _ParametricInvariantError("compiler changed the semantic base")

    expected_ids = _expected_checkpoint_ids(resolved)
    actual_ids = tuple(checkpoint.checkpoint_id for checkpoint in compiled.checkpoints)
    if not expected_ids or actual_ids != expected_ids:
        raise _ParametricInvariantError("compiler did not return the exact routed suffix")
    count = len(compiled.checkpoints)
    if count > MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS:
        raise _ParametricInvariantError("compiler bypassed the checkpoint budget")
    remaining_low = MAX_SAFE_SEQUENCE - request.base_scene.revision
    remaining_semantic = MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision
    if count > min(remaining_low, remaining_semantic):
        raise _ParametricCapacityError("routed suffix exceeded the remaining revision budget")
    patch_ids = tuple(checkpoint.patch.patch_id for checkpoint in compiled.checkpoints)
    if len(patch_ids) != len(set(patch_ids)):
        raise _ParametricInvariantError("compiler emitted duplicate checkpoint patch IDs")

    scene = request.base_scene
    semantic_scene = request.base_semantic_scene
    previous_viewports = None
    prepared: list[ParametricChoreographySceneCheckpointEventV3] = []
    for sequence, candidate in enumerate(compiled.checkpoints, start=1):
        try:
            checkpoint = CompiledCheckpointV3.model_validate(
                candidate.model_dump(mode="json", by_alias=True)
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise _ParametricInvariantError("checkpoint failed independent validation") from None
        if checkpoint.beat != beat:
            raise _ParametricInvariantError("checkpoint changed the routed beat")
        if (
            previous_viewports is not None
            and checkpoint.presentation.base_viewports != previous_viewports
        ):
            raise _ParametricInvariantError("checkpoint viewport transitions did not join")
        result_scene = _apply_patch(scene, checkpoint.patch)
        if len(result_scene.nodes) > MAX_SCENE_NODES:
            raise _ParametricCapacityError("checkpoint exceeded the low-level node budget")
        result_semantic_scene, result_component = _derive_result_semantic_scene(
            semantic_scene,
            checkpoint,
        )
        try:
            verified = verify_parametric_completing_square_checkpoint(
                beat.component_id,
                beat.problem_spec,
                checkpoint.checkpoint_id,
                scene,
                result_scene,
                checkpoint.patch,
                checkpoint.presentation,
                checkpoint.choreography,
            )
        except ParametricCompletingSquareVerificationError:
            raise _ParametricInvariantError("checkpoint failed independent verification") from None
        if verified != checkpoint.receipt:
            raise _ParametricInvariantError("verifier receipt changed after materialization")
        try:
            event = ParametricChoreographySceneCheckpointEventV3(
                generation=request.generation,
                attempt=attempt,
                sequence=sequence,
                base_revision=scene.revision,
                result_revision=result_scene.revision,
                patch=checkpoint.patch,
                semantic=ParametricCheckpointSemanticMetadataV3(
                    problem_spec=beat.problem_spec,
                    beat=beat,
                    checkpoint_id=checkpoint.checkpoint_id,
                    result_component=result_component,
                    semantic_base_revision=semantic_scene.revision,
                    semantic_result_revision=result_semantic_scene.revision,
                    semantic_base_certificate_sha256=(semantic_scene.certificate_head_sha256),
                    semantic_result_certificate_sha256=(
                        result_semantic_scene.certificate_head_sha256
                    ),
                    receipt=checkpoint.receipt,
                    presentation=checkpoint.presentation,
                    choreography=checkpoint.choreography,
                    certificate=checkpoint.certificate,
                ),
            )
            event = cast(
                ParametricChoreographySceneCheckpointEventV3,
                _roundtrip_event(event),
            )
        except (SceneStreamWireError, TypeError, ValueError, ValidationError):
            raise _ParametricInvariantError("checkpoint event failed wire preflight") from None
        prepared.append(event)
        scene = result_scene
        semantic_scene = result_semantic_scene
        previous_viewports = checkpoint.presentation.result_viewports

    if scene != compiled.result_scene or semantic_scene != compiled.result_semantic_scene:
        raise _ParametricInvariantError("materialized result did not match the compiler batch")

    first_checkpoint_ms = _elapsed_ms(started_at, clock())
    completed = SceneStreamCompletedEvent(
        generation=request.generation,
        final_revision=scene.revision,
        patch_count=count,
        first_patch_ms=first_checkpoint_ms,
        total_ms=max(first_checkpoint_ms, _elapsed_ms(started_at, clock())),
        repaired=attempt == 2,
    )
    try:
        completed = cast(SceneStreamCompletedEvent, _roundtrip_event(completed))
    except (SceneStreamWireError, TypeError, ValueError, ValidationError):
        raise _ParametricInvariantError("completed event failed wire preflight") from None
    return tuple(prepared), completed


class ParametricChoreographyService:
    """Serve Reflex and Director V3 requests through one verified compiler path."""

    def __init__(
        self,
        client: ParametricChoreographyDirectorClient | None = None,
        *,
        client_factory: ParametricDirectorClientFactory | None = None,
        clock: SceneClock = time.perf_counter,
        max_tokens: int = DEFAULT_PARAMETRIC_DIRECTOR_MAX_TOKENS,
        timeout_seconds: float = 20.0,
        before_provider_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if client is not None and client_factory is not None:
            raise ValueError("provide at most one of client or client_factory")
        if client_factory is not None and not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= MAX_SCENE_MODEL_OUTPUT_TOKENS
        ):
            raise ValueError(f"max_tokens must be between 1 and {MAX_SCENE_MODEL_OUTPUT_TOKENS}")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if before_provider_dispatch is not None and not callable(before_provider_dispatch):
            raise TypeError("before_provider_dispatch must be callable")
        self._client = client
        self._client_factory = client_factory
        self._clock = clock
        self._max_tokens = min(max_tokens, DEFAULT_PARAMETRIC_DIRECTOR_MAX_TOKENS)
        self._timeout_seconds = float(timeout_seconds)
        self._before_provider_dispatch = before_provider_dispatch
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    def _resolve_client(self) -> ParametricChoreographyDirectorClient:
        client = self._client
        if client is None:
            if self._client_factory is None:
                raise RuntimeError("no Director client is configured")
            client = self._client_factory()
        if not callable(getattr(client, "stream", None)):
            raise TypeError("Director client must provide stream()")
        return client

    async def stream_events(
        self,
        request: ParametricChoreographyRequestV3,
    ) -> AsyncIterator[ParametricChoreographySceneStreamEventV3]:
        """Yield one V3 lifecycle with an all-or-nothing checkpoint suffix."""

        if not isinstance(
            request,
            (ParametricChoreographyReflexRequestV3, ParametricChoreographyDirectorRequestV3),
        ):
            raise TypeError("request must be a ParametricChoreographyRequestV3")

        started_at = self._clock()
        lifecycle = _Lifecycle(request)
        yield lifecycle.started()

        if request.base_scene.revision != request.base_semantic_scene.revision:
            yield lifecycle.failed(ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH)
            return
        if len(request.base_scene.nodes) >= MAX_SCENE_NODES:
            yield lifecycle.failed(ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED)
            return
        if len(request.base_semantic_scene.components) > MAX_SEMANTIC_COMPONENTS:
            yield lifecycle.failed(ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT)
            return
        if request.base_scene.revision >= MAX_SAFE_SEQUENCE:
            yield lifecycle.failed(ParametricChoreographyFailureCode.REVISION_LIMIT)
            return

        try:
            accepted_component = _accepted_parametric_frontier(
                request.base_scene,
                request.base_semantic_scene,
            )
        except (TypeError, ValueError):
            yield lifecycle.failed(ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH)
            return

        binding = bind_completing_square_problem(
            request.problem_text,
            accepted_problem=(
                accepted_component.problem_spec if accepted_component is not None else None
            ),
        )
        if not binding.is_bound:
            reason = binding.failure_reason
            if reason is None:
                yield lifecycle.failed(
                    ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR
                )
            else:
                yield lifecycle.declined(reason)
            return
        problem = binding.problem
        assert problem is not None

        if isinstance(request, ParametricChoreographyDirectorRequestV3) and _has_wrong_corner_claim(
            request.prompt,
            problem=problem,
            component=accepted_component,
        ):
            yield lifecycle.declined(CompletingSquareProblemFailureReason.CONFLICT)
            return

        resolved: ResolvedParametricChoreographyAct | None = None
        attempt: Literal[1, 2] = 1
        repair_announced = False
        client: ParametricChoreographyDirectorClient | None = None
        owns_client = False
        try:
            if isinstance(request, ParametricChoreographyReflexRequestV3):
                try:
                    resolved = resolve_parametric_reflex_route(
                        request.requested_route,
                        problem_spec=problem,
                        semantic_scene=request.base_semantic_scene,
                    )
                    _validate_resolved_identity(
                        resolved,
                        problem=problem,
                        accepted_component=accepted_component,
                        expected_route=request.requested_route,
                    )
                except ParametricChoreographyRoutingError as exc:
                    if exc.code in {
                        ParametricChoreographyRoutingErrorCode.NON_FORWARD_TARGET,
                        ParametricChoreographyRoutingErrorCode.CLARIFICATION_UNAVAILABLE,
                    }:
                        yield lifecycle.declined(VisualActAbstainReason.NO_FORWARD_PROGRESS)
                    else:
                        yield lifecycle.failed(
                            ParametricChoreographyFailureCode.SEMANTIC_BASE_MISMATCH
                        )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(
                        ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR
                    )
                    return
            else:
                try:
                    client = self._resolve_client()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(ParametricChoreographyFailureCode.PROVIDER_ERROR)
                    return
                owns_client = self._client is None
                routing: ParametricChoreographyDirectorResult | None = None
                try:
                    engine = ParametricChoreographyDirectorEngine(
                        client,
                        max_tokens=self._max_tokens,
                        timeout_seconds=self._timeout_seconds,
                        before_dispatch=self._before_provider_dispatch,
                    )
                    async for step in engine.stream_route(
                        prompt=request.prompt,
                        problem_spec=problem,
                        semantic_scene=request.base_semantic_scene,
                    ):
                        if isinstance(step, VisualActRoutingRepairing):
                            if repair_announced:
                                raise _ParametricInvariantError(
                                    "Director emitted duplicate repair boundaries"
                                )
                            repair_announced = True
                            yield lifecycle.repairing()
                        elif (
                            isinstance(step, ParametricChoreographyDirectorResult)
                            and routing is None
                        ):
                            routing = step
                        else:
                            raise _ParametricInvariantError(
                                "Director emitted an invalid lifecycle step"
                            )
                    if routing is None or routing.repaired != repair_announced:
                        raise _ParametricInvariantError(
                            "Director lifecycle did not match its accepted result"
                        )
                    attempt = routing.provider_attempts
                    if isinstance(routing.decision, AbstainParametricChoreographyDecisionV1):
                        if routing.resolved is not None:
                            raise _ParametricInvariantError(
                                "Director abstention carried a resolved mutation"
                            )
                        yield lifecycle.declined(
                            routing.decision.reason_code,
                            attempt=attempt,
                        )
                        return
                    if routing.resolved is None:
                        raise _ParametricInvariantError(
                            "Director mutation omitted its resolved route"
                        )
                    expected_resolved = resolve_parametric_director_decision(
                        routing.decision,
                        problem_spec=problem,
                        semantic_scene=request.base_semantic_scene,
                    )
                    if expected_resolved is None or routing.resolved != expected_resolved:
                        raise _ParametricInvariantError(
                            "Director result changed local route resolution"
                        )
                    resolved = routing.resolved
                    _validate_resolved_identity(
                        resolved,
                        problem=problem,
                        accepted_component=accepted_component,
                        expected_route=expected_resolved.route,
                    )
                except VisualActEngineError as exc:
                    code = _DIRECTOR_FAILURES[exc.code]
                    failure_attempt = cast(
                        Literal[1, 2],
                        2 if repair_announced else max(1, exc.provider_attempts),
                    )
                    yield lifecycle.failed(code, attempt=failure_attempt)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(
                        ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                        attempt=2 if repair_announced else 1,
                    )
                    return

            if not isinstance(resolved, ResolvedParametricChoreographyAct):
                yield lifecycle.failed(
                    ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                    attempt=attempt,
                )
                return
            try:
                expected_count = len(_expected_checkpoint_ids(resolved))
                if not expected_count:
                    raise _ParametricInvariantError("resolved route made no checkpoint progress")
                remaining = min(
                    MAX_SAFE_SEQUENCE - request.base_scene.revision,
                    MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision,
                )
                if expected_count > remaining:
                    raise _ParametricCapacityError(
                        "routed suffix exceeded the remaining revision budget"
                    )
                beat = lower_resolved_parametric_choreography_act(
                    resolved,
                    generation=request.generation,
                )
                compiled = compile_parametric_checkpoint_beat(
                    beat,
                    base_scene=request.base_scene,
                    base_semantic_scene=request.base_semantic_scene,
                )
                prepared, completed = _prepare_batch(
                    request,
                    resolved,
                    beat,
                    compiled,
                    attempt=attempt,
                    started_at=started_at,
                    clock=self._clock,
                )
            except _ParametricCapacityError:
                yield lifecycle.failed(
                    ParametricChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT,
                    attempt=attempt,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                yield lifecycle.failed(
                    ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                    attempt=attempt,
                )
                return

            # `_prepare_batch` has already encoded and decoded every checkpoint
            # and the completed terminal.  Only now may checkpoint one escape.
            for checkpoint in prepared:
                yield checkpoint
            yield completed
        finally:
            if owns_client:
                await close_async_resource(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )


__all__ = [
    "ParametricChoreographyService",
    "ParametricDirectorClientFactory",
]
