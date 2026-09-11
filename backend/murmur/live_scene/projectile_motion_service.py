"""Transactional orchestration for Gate 1.7 projectile choreography."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

from pydantic import ValidationError

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
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
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    CompiledProjectileMotionCheckpointBeatV1,
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    CompiledProjectileMotionCheckpointV1,
    ProjectileMotionCheckpointAction,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
)
from murmur.live_scene.projectile_motion_director import (
    DEFAULT_PROJECTILE_DIRECTOR_MAX_TOKENS,
    ProjectileMotionDirectorClient,
    ProjectileMotionDirectorEngine,
    ProjectileMotionDirectorResult,
)
from murmur.live_scene.projectile_motion_requests import (
    ProjectileMotionDirectorRequestV1,
    ProjectileMotionReflexRequestV1,
    ProjectileMotionRequestV1,
)
from murmur.live_scene.projectile_motion_routing import (
    PROJECTILE_MOTION_COMPONENT_ID,
    AbstainProjectileMotionDecisionV1,
    ProjectileMotionRoutingError,
    ProjectileMotionRoutingErrorCode,
    ResolvedProjectileMotionAct,
    lower_resolved_projectile_motion_act,
    resolve_projectile_motion_director_decision,
    resolve_projectile_motion_reflex_route,
)
from murmur.live_scene.projectile_motion_service_contracts import (
    MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileCheckpointSemanticMetadataV1,
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographyFailureCode,
    ProjectileChoreographySceneCheckpointEventV1,
    ProjectileChoreographySceneStreamDeclinedEventV1,
    ProjectileChoreographySceneStreamEventV1,
    ProjectileChoreographySceneStreamFailedEventV1,
)
from murmur.live_scene.projectile_motion_verifier import (
    ProjectileMotionVerificationError,
    verify_projectile_motion_frontier,
)
from murmur.live_scene.projectile_motion_wire import (
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import (
    MAX_SEMANTIC_COMPONENTS,
    SemanticSceneState,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import digest_matches
from murmur.live_scene.visual_act_engine import (
    VisualActEngineError,
    VisualActEngineErrorCode,
    VisualActRoutingRepairing,
)
from murmur.live_scene.wire import SceneStreamWireError

ProjectileDirectorClientFactory: TypeAlias = Callable[[], ProjectileMotionDirectorClient]
SceneClock: TypeAlias = Callable[[], float]

_BASE_MISMATCH_MESSAGE = (
    "The projectile lesson no longer matches the visible board. Refresh before continuing."
)
_CAPACITY_MESSAGE = (
    "This board has no room for the requested visual sequence. The current board remains safe."
)
_REVISION_LIMIT_MESSAGE = (
    "This board has reached its revision limit. The current board remains safe."
)
_INTEGRITY_MESSAGE = (
    "The projectile choreography runtime rejected an internal result. "
    "The current board remains safe."
)
_REPAIR_MESSAGE = (
    "The first visual direction needed correction. The current board is safe while I retry."
)
_INVALID_VISUAL_MESSAGE = (
    "I couldn't resolve a valid projectile direction. The current board remains safe."
)
_PROVIDER_ERROR_MESSAGE = (
    "The visual director is temporarily unavailable. The current board remains safe."
)
_PROVIDER_TIMEOUT_MESSAGE = "The visual director took too long. The current board remains safe."
_PROVIDER_RATE_LIMIT_MESSAGE = "Visual director capacity is busy. Please try again shortly."
_CONTEXT_LIMIT_MESSAGE = (
    "This lesson context is too large for another director pass. The current board remains safe."
)

_DECLINE_MESSAGES = {
    ProjectileChoreographyDeclineReason.UNSUPPORTED_INTENT: (
        "That request does not match a supported projectile explanation. The board is unchanged."
    ),
    ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS: (
        "That visual is already complete or unavailable here. The board is unchanged."
    ),
    ProjectileChoreographyDeclineReason.PROBLEM_CONFLICT: (
        "Those parameters conflict with the accepted projectile model. The board is unchanged."
    ),
}

_FAILURE_MESSAGES = {
    ProjectileChoreographyFailureCode.SEMANTIC_BASE_MISMATCH: _BASE_MISMATCH_MESSAGE,
    ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED: _CAPACITY_MESSAGE,
    ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT: _CAPACITY_MESSAGE,
    ProjectileChoreographyFailureCode.REVISION_LIMIT: _REVISION_LIMIT_MESSAGE,
    ProjectileChoreographyFailureCode.CONTEXT_TOO_LARGE: _CONTEXT_LIMIT_MESSAGE,
    ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT: _INVALID_VISUAL_MESSAGE,
    ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED: _PROVIDER_RATE_LIMIT_MESSAGE,
    ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT: _PROVIDER_TIMEOUT_MESSAGE,
    ProjectileChoreographyFailureCode.PROVIDER_ERROR: _PROVIDER_ERROR_MESSAGE,
    ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR: _INTEGRITY_MESSAGE,
}

_RETRYABLE_FAILURES = frozenset(
    {
        ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT,
        ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED,
        ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT,
        ProjectileChoreographyFailureCode.PROVIDER_ERROR,
    }
)

_DIRECTOR_FAILURES = {
    VisualActEngineErrorCode.CONTEXT_INVALID: (ProjectileChoreographyFailureCode.CONTEXT_TOO_LARGE),
    VisualActEngineErrorCode.PROVIDER_RATE_LIMIT: (
        ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED
    ),
    VisualActEngineErrorCode.PROVIDER_TIMEOUT: (ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT),
    VisualActEngineErrorCode.PROVIDER_ERROR: (ProjectileChoreographyFailureCode.PROVIDER_ERROR),
    VisualActEngineErrorCode.INVALID_VISUAL_ACT: (
        ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT
    ),
    VisualActEngineErrorCode.INTERNAL_ERROR: (
        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR
    ),
}


class _ProjectileBaseError(ValueError):
    """Submitted low-level and semantic state is not one accepted frontier."""


class _ProjectileCapacityError(ValueError):
    """A valid routed suffix cannot fit the remaining fixed budgets."""


class _ProjectileInvariantError(RuntimeError):
    """A server-owned compiler, verifier, event, or wire invariant failed."""


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return max(0.0, (finished_at - started_at) * 1_000.0)


def _apply_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
            continue
        if operation.id not in nodes:
            raise _ProjectileInvariantError("checkpoint removed an absent node")
        del nodes[operation.id]
        order.remove(operation.id)
    try:
        result = SceneState(
            revision=scene.revision + 1,
            nodes=tuple(nodes[node_id] for node_id in order),
        )
    except ValidationError:
        raise _ProjectileCapacityError("checkpoint exceeded the low-level scene budget") from None
    if result.nodes == scene.nodes:
        raise _ProjectileInvariantError("checkpoint made no visible scene change")
    return result


def _accepted_frontier(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
) -> ProjectileMotionStateV1 | None:
    if scene.revision != semantic_scene.revision:
        raise _ProjectileBaseError("low-level and semantic revisions differ")
    if len(semantic_scene.components) > 1:
        raise _ProjectileBaseError("multiple semantic components are unsupported")
    if not semantic_scene.components:
        if semantic_scene.certificate_head_sha256 is not None:
            raise _ProjectileBaseError("an empty frontier cannot carry a certificate head")
        if scene.nodes:
            raise _ProjectileBaseError("an empty semantic frontier cannot carry scene nodes")
        if scene.revision != 0:
            raise _ProjectileBaseError("a fresh empty frontier must start at revision zero")
        return None

    component = semantic_scene.components[0]
    if not isinstance(component, ProjectileMotionStateV1):
        raise _ProjectileBaseError("only a projectile component may be continued")
    if component.last_main_checkpoint is None:
        raise _ProjectileBaseError("an unrevealed projectile component is orphaned")
    if semantic_scene.certificate_head_sha256 is None:
        raise _ProjectileBaseError("a committed frontier requires a certificate head")
    if scene.revision == 0:
        raise _ProjectileBaseError("a committed frontier requires a positive revision")
    try:
        verify_projectile_motion_frontier(component, scene)
    except (TypeError, ValueError, ProjectileMotionVerificationError):
        raise _ProjectileBaseError(
            "the submitted projectile frontier failed verification"
        ) from None
    return component


def _derive_result_semantic_scene(
    scene: SemanticSceneState,
    checkpoint: CompiledProjectileMotionCheckpointV1,
) -> tuple[SemanticSceneState, ProjectileMotionStateV1]:
    if len(scene.components) > 1:
        raise _ProjectileInvariantError("checkpoint encountered multiple semantic components")
    existing = scene.components[0] if scene.components else None
    if existing is not None and not isinstance(existing, ProjectileMotionStateV1):
        raise _ProjectileInvariantError("checkpoint encountered a foreign component")
    if existing is not None and existing.id != checkpoint.beat.component_id:
        raise _ProjectileInvariantError("checkpoint changed component identity")
    if existing is None and len(scene.components) >= MAX_SEMANTIC_COMPONENTS:
        raise _ProjectileCapacityError("semantic component capacity is exhausted")
    if scene.revision >= MAX_SAFE_SEQUENCE:
        raise _ProjectileCapacityError("semantic revision capacity is exhausted")

    if checkpoint.action is ProjectileMotionCheckpointAction.ADVANCE:
        result_component = ProjectileMotionStateV1(
            id=checkpoint.beat.component_id,
            problem_spec=checkpoint.beat.result_problem_spec,
            last_main_checkpoint=ProjectileMotionMainCheckpoint(checkpoint.checkpoint_id.value),
            clarified_topics=(existing.clarified_topics if existing is not None else ()),
            active_clarification=None,
        )
    elif checkpoint.action is ProjectileMotionCheckpointAction.CLARIFY:
        if existing is None or checkpoint.clarification_topic is None:
            raise _ProjectileInvariantError("clarification omitted its accepted frontier")
        topic = checkpoint.clarification_topic
        topics = tuple(
            item
            for item in PROJECTILE_MOTION_CLARIFICATION_ORDER
            if item in (*existing.clarified_topics, topic)
        )
        result_component = ProjectileMotionStateV1(
            id=existing.id,
            problem_spec=existing.problem_spec,
            last_main_checkpoint=existing.last_main_checkpoint,
            clarified_topics=topics,
            active_clarification=topic,
        )
    elif checkpoint.action is ProjectileMotionCheckpointAction.RETARGET:
        if existing is None:
            raise _ProjectileInvariantError("retarget omitted its accepted frontier")
        result_component = ProjectileMotionStateV1(
            id=existing.id,
            problem_spec=checkpoint.beat.result_problem_spec,
            last_main_checkpoint=existing.last_main_checkpoint,
            clarified_topics=existing.clarified_topics,
            active_clarification=existing.active_clarification,
        )
    else:
        raise _ProjectileInvariantError("checkpoint used an unsupported action")

    return (
        SemanticSceneState(
            revision=scene.revision + 1,
            components=(result_component,),
            certificate_head_sha256=checkpoint.certificate.certificate_sha256,
        ),
        result_component,
    )


def _roundtrip_event(
    event: ProjectileChoreographySceneStreamEventV1,
) -> ProjectileChoreographySceneStreamEventV1:
    encoded = encode_projectile_choreography_scene_stream_event(event)
    if not encoded.startswith("data: ") or not encoded.endswith("\n\n"):
        raise _ProjectileInvariantError("wire encoder returned an invalid SSE frame")
    try:
        payload = json.loads(encoded.removeprefix("data: ").removesuffix("\n\n"))
        decoded = PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            payload,
            by_alias=True,
            by_name=False,
        )
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
        raise _ProjectileInvariantError("event failed independent wire roundtrip") from None
    if decoded != event:
        raise _ProjectileInvariantError("wire roundtrip changed an event")
    return decoded


def _failure_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
    code: ProjectileChoreographyFailureCode,
) -> ProjectileChoreographySceneStreamFailedEventV1:
    event = ProjectileChoreographySceneStreamFailedEventV1(
        generation=generation,
        attempt=attempt,
        code=code,
        message=_FAILURE_MESSAGES[code],
        last_accepted_revision=revision,
        retryable=code in _RETRYABLE_FAILURES,
    )
    return cast(ProjectileChoreographySceneStreamFailedEventV1, _roundtrip_event(event))


def _decline_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
    reason: ProjectileChoreographyDeclineReason,
) -> ProjectileChoreographySceneStreamDeclinedEventV1:
    event = ProjectileChoreographySceneStreamDeclinedEventV1(
        generation=generation,
        attempt=attempt,
        final_revision=revision,
        reason_code=reason,
        message=_DECLINE_MESSAGES[reason],
    )
    return cast(ProjectileChoreographySceneStreamDeclinedEventV1, _roundtrip_event(event))


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
    request: ProjectileMotionRequestV1

    def started(self) -> SceneStreamStartedEvent:
        event = SceneStreamStartedEvent(
            generation=self.request.generation,
            attempt=1,
            base_revision=self.request.base_scene.revision,
        )
        return cast(SceneStreamStartedEvent, _roundtrip_event(event))

    def failed(
        self,
        code: ProjectileChoreographyFailureCode,
        *,
        attempt: int = 1,
    ) -> ProjectileChoreographySceneStreamFailedEventV1:
        return _failure_event(
            generation=self.request.generation,
            attempt=attempt,
            revision=self.request.base_scene.revision,
            code=code,
        )

    def declined(
        self,
        reason: ProjectileChoreographyDeclineReason,
        *,
        attempt: int = 1,
    ) -> ProjectileChoreographySceneStreamDeclinedEventV1:
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


def _validate_resolved_identity(
    resolved: ResolvedProjectileMotionAct,
    *,
    accepted: ProjectileMotionStateV1 | None,
    expected_result_problem: ProjectileMotionProblemSpecV1,
    expected_route: object,
) -> None:
    expected_component_id = accepted.id if accepted is not None else PROJECTILE_MOTION_COMPONENT_ID
    expected_base_problem = accepted.problem_spec if accepted is not None else None
    if (
        resolved.component_kind != "projectile_motion"
        or resolved.component_id != expected_component_id
        or resolved.base_problem_spec != expected_base_problem
        or resolved.result_problem_spec != expected_result_problem
        or resolved.route != expected_route
        or not resolved.checkpoint_ids
    ):
        raise _ProjectileInvariantError("resolved route changed server-bound identity")


def _validate_certificate_transition(
    checkpoint: CompiledProjectileMotionCheckpointV1,
    *,
    base_scene: SceneState,
    result_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
    result_semantic_scene: SemanticSceneState,
) -> None:
    body = checkpoint.certificate.body
    claims = (
        (body.base_low_level_scene_sha256, low_level_scene_sha256(base_scene)),
        (body.result_low_level_scene_sha256, low_level_scene_sha256(result_scene)),
        (body.base_semantic_scene_sha256, semantic_scene_sha256(base_semantic_scene)),
        (body.result_semantic_scene_sha256, semantic_scene_sha256(result_semantic_scene)),
    )
    if any(not digest_matches(claim, actual) for claim, actual in claims):
        raise _ProjectileInvariantError("certificate did not bind the materialized transition")
    if body.previous_certificate_sha256 != base_semantic_scene.certificate_head_sha256:
        raise _ProjectileInvariantError("certificate did not join the accepted chain head")
    if result_semantic_scene.certificate_head_sha256 != checkpoint.certificate.certificate_sha256:
        raise _ProjectileInvariantError("result semantic scene has the wrong chain head")


def _prepare_batch(
    request: ProjectileMotionRequestV1,
    resolved: ResolvedProjectileMotionAct,
    beat: RoutedProjectileMotionBeatV1,
    compiled_candidate: CompiledProjectileMotionCheckpointBeatV1,
    *,
    attempt: Literal[1, 2],
    started_at: float,
    clock: SceneClock,
) -> tuple[
    tuple[ProjectileChoreographySceneCheckpointEventV1, ...],
    SceneStreamCompletedEvent,
]:
    """Revalidate and wire-preflight the whole suffix before publication."""

    try:
        compiled = CompiledProjectileMotionCheckpointBeatV1.model_validate(
            compiled_candidate.model_dump(mode="json", by_alias=True)
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise _ProjectileInvariantError("compiler result failed independent validation") from None
    if compiled.beat != beat:
        raise _ProjectileInvariantError("compiler changed the routed beat")
    if compiled.base_scene != request.base_scene:
        raise _ProjectileInvariantError("compiler changed the low-level base")
    if compiled.base_semantic_scene != request.base_semantic_scene:
        raise _ProjectileInvariantError("compiler changed the semantic base")

    actual_ids = tuple(checkpoint.checkpoint_id for checkpoint in compiled.checkpoints)
    if actual_ids != resolved.checkpoint_ids:
        raise _ProjectileInvariantError("compiler did not return the exact routed suffix")
    count = len(compiled.checkpoints)
    if not count or count > MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS:
        raise _ProjectileInvariantError("compiler bypassed the checkpoint budget")
    remaining = min(
        MAX_SAFE_SEQUENCE - request.base_scene.revision,
        MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision,
    )
    if count > remaining:
        raise _ProjectileCapacityError("suffix exceeded the remaining revision budget")
    patch_ids = tuple(checkpoint.patch.patch_id for checkpoint in compiled.checkpoints)
    if len(patch_ids) != len(set(patch_ids)):
        raise _ProjectileInvariantError("compiler emitted duplicate patch IDs")

    scene = request.base_scene
    semantic_scene = request.base_semantic_scene
    previous_viewports = None
    prepared: list[ProjectileChoreographySceneCheckpointEventV1] = []
    for sequence, candidate in enumerate(compiled.checkpoints, start=1):
        try:
            checkpoint = CompiledProjectileMotionCheckpointV1.model_validate(
                candidate.model_dump(mode="json", by_alias=True)
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise _ProjectileInvariantError("checkpoint failed independent validation") from None
        if checkpoint.beat != beat:
            raise _ProjectileInvariantError("checkpoint changed the routed beat")
        if (
            previous_viewports is not None
            and checkpoint.presentation.base_viewports != previous_viewports
        ):
            raise _ProjectileInvariantError("checkpoint viewport transitions did not join")

        base_component = semantic_scene.components[0] if semantic_scene.components else None
        if base_component is not None and not isinstance(base_component, ProjectileMotionStateV1):
            raise _ProjectileInvariantError("checkpoint base component changed kind")
        result_scene = _apply_patch(scene, checkpoint.patch)
        if len(result_scene.nodes) > MAX_SCENE_NODES:
            raise _ProjectileCapacityError("checkpoint exceeded the node budget")
        result_semantic_scene, result_component = _derive_result_semantic_scene(
            semantic_scene,
            checkpoint,
        )
        _validate_certificate_transition(
            checkpoint,
            base_scene=scene,
            result_scene=result_scene,
            base_semantic_scene=semantic_scene,
            result_semantic_scene=result_semantic_scene,
        )

        event = ProjectileChoreographySceneCheckpointEventV1(
            generation=request.generation,
            attempt=attempt,
            sequence=sequence,
            base_revision=scene.revision,
            result_revision=result_scene.revision,
            patch=checkpoint.patch,
            semantic=ProjectileCheckpointSemanticMetadataV1(
                base_problem_spec=(
                    base_component.problem_spec if base_component is not None else None
                ),
                result_problem_spec=result_component.problem_spec,
                beat=beat,
                action=checkpoint.action,
                checkpoint_id=checkpoint.checkpoint_id,
                clarification_topic=checkpoint.clarification_topic,
                base_component=base_component,
                result_component=result_component,
                semantic_base_revision=semantic_scene.revision,
                semantic_result_revision=result_semantic_scene.revision,
                semantic_base_certificate_sha256=(semantic_scene.certificate_head_sha256),
                semantic_result_certificate_sha256=(checkpoint.certificate.certificate_sha256),
                receipt=checkpoint.receipt,
                presentation=checkpoint.presentation,
                choreography=checkpoint.choreography,
                certificate=checkpoint.certificate,
            ),
        )
        try:
            event = cast(
                ProjectileChoreographySceneCheckpointEventV1,
                _roundtrip_event(event),
            )
        except (SceneStreamWireError, TypeError, ValueError, ValidationError):
            raise _ProjectileInvariantError("checkpoint failed wire preflight") from None
        prepared.append(event)
        scene = result_scene
        semantic_scene = result_semantic_scene
        previous_viewports = checkpoint.presentation.result_viewports

    if scene != compiled.result_scene or semantic_scene != compiled.result_semantic_scene:
        raise _ProjectileInvariantError("materialized result changed the compiler batch")

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
        raise _ProjectileInvariantError("completed event failed wire preflight") from None
    return tuple(prepared), completed


class ProjectileMotionService:
    """Serve Reflex and optional Director requests through one certified path."""

    def __init__(
        self,
        client: ProjectileMotionDirectorClient | None = None,
        *,
        client_factory: ProjectileDirectorClientFactory | None = None,
        clock: SceneClock = time.perf_counter,
        max_tokens: int = DEFAULT_PROJECTILE_DIRECTOR_MAX_TOKENS,
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
        self._max_tokens = min(max_tokens, DEFAULT_PROJECTILE_DIRECTOR_MAX_TOKENS)
        self._timeout_seconds = float(timeout_seconds)
        self._before_provider_dispatch = before_provider_dispatch
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    def _resolve_client(self) -> ProjectileMotionDirectorClient:
        if self._client is not None:
            return self._client
        if self._client_factory is None:
            raise RuntimeError("no projectile Director client is configured")
        return self._client_factory()

    async def stream_events(
        self,
        request: ProjectileMotionRequestV1,
    ) -> AsyncIterator[ProjectileChoreographySceneStreamEventV1]:
        if not isinstance(
            request,
            (ProjectileMotionReflexRequestV1, ProjectileMotionDirectorRequestV1),
        ):
            raise TypeError("request must be a ProjectileMotionRequestV1")

        started_at = self._clock()
        lifecycle = _Lifecycle(request)
        yield lifecycle.started()
        if request.base_scene.revision != request.base_semantic_scene.revision:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.SEMANTIC_BASE_MISMATCH)
            return
        if len(request.base_scene.nodes) >= MAX_SCENE_NODES:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_EXCEEDED)
            return
        if len(request.base_semantic_scene.components) > MAX_SEMANTIC_COMPONENTS:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT)
            return
        if request.base_scene.revision >= MAX_SAFE_SEQUENCE:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.REVISION_LIMIT)
            return

        try:
            accepted = _accepted_frontier(
                request.base_scene,
                request.base_semantic_scene,
            )
        except _ProjectileBaseError:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.SEMANTIC_BASE_MISMATCH)
            return
        except Exception:
            yield lifecycle.failed(ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR)
            return
        if accepted is not None and accepted.problem_spec != request.problem_spec:
            yield lifecycle.declined(ProjectileChoreographyDeclineReason.PROBLEM_CONFLICT)
            return

        resolved: ResolvedProjectileMotionAct | None = None
        attempt: Literal[1, 2] = 1
        repair_announced = False
        client: ProjectileMotionDirectorClient | None = None
        owns_client = False
        try:
            if isinstance(request, ProjectileMotionReflexRequestV1):
                try:
                    resolved = resolve_projectile_motion_reflex_route(
                        request.requested_route,
                        problem_spec=request.problem_spec,
                        semantic_scene=request.base_semantic_scene,
                    )
                    _validate_resolved_identity(
                        resolved,
                        accepted=accepted,
                        expected_result_problem=(
                            request.requested_route.target_problem_spec
                            if isinstance(
                                request.requested_route,
                                RetargetProjectileMotionRouteV1,
                            )
                            else request.problem_spec
                        ),
                        expected_route=request.requested_route,
                    )
                except ProjectileMotionRoutingError as exc:
                    if exc.code is ProjectileMotionRoutingErrorCode.PROBLEM_MISMATCH:
                        yield lifecycle.declined(
                            ProjectileChoreographyDeclineReason.PROBLEM_CONFLICT
                        )
                    elif exc.code in {
                        ProjectileMotionRoutingErrorCode.NON_FORWARD_TARGET,
                        ProjectileMotionRoutingErrorCode.CLARIFICATION_UNAVAILABLE,
                        ProjectileMotionRoutingErrorCode.RETARGET_UNAVAILABLE,
                        ProjectileMotionRoutingErrorCode.COMPONENT_NOT_FOUND,
                    }:
                        yield lifecycle.declined(
                            ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS
                        )
                    else:
                        yield lifecycle.failed(
                            ProjectileChoreographyFailureCode.SEMANTIC_BASE_MISMATCH
                        )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(
                        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR
                    )
                    return
            else:
                try:
                    client = self._resolve_client()
                    owns_client = self._client is None
                    if not callable(getattr(client, "stream", None)):
                        raise TypeError("Director client must provide stream()")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(ProjectileChoreographyFailureCode.PROVIDER_ERROR)
                    return
                routing: ProjectileMotionDirectorResult | None = None
                try:
                    engine = ProjectileMotionDirectorEngine(
                        client,
                        max_tokens=self._max_tokens,
                        timeout_seconds=self._timeout_seconds,
                        before_dispatch=self._before_provider_dispatch,
                    )
                    async for step in engine.stream_route(
                        prompt=request.prompt,
                        problem_spec=request.problem_spec,
                        semantic_scene=request.base_semantic_scene,
                    ):
                        if isinstance(step, VisualActRoutingRepairing):
                            if repair_announced or routing is not None:
                                raise _ProjectileInvariantError("invalid repair boundary order")
                            repair_announced = True
                            yield lifecycle.repairing()
                        elif isinstance(step, ProjectileMotionDirectorResult) and routing is None:
                            routing = step
                        else:
                            raise _ProjectileInvariantError("invalid Director lifecycle step")
                    expected_attempt: Literal[1, 2] = 2 if repair_announced else 1
                    if (
                        routing is None
                        or type(routing.provider_attempts) is not int
                        or routing.provider_attempts != expected_attempt
                        or routing.repaired != repair_announced
                    ):
                        raise _ProjectileInvariantError("Director lifecycle did not join")
                    attempt = expected_attempt
                    if isinstance(routing.decision, AbstainProjectileMotionDecisionV1):
                        if routing.resolved is not None:
                            raise _ProjectileInvariantError("abstention carried a mutation")
                        yield lifecycle.declined(
                            ProjectileChoreographyDeclineReason(routing.decision.reason_code.value),
                            attempt=attempt,
                        )
                        return
                    if routing.resolved is None:
                        raise _ProjectileInvariantError("Director mutation omitted its route")
                    expected = resolve_projectile_motion_director_decision(
                        routing.decision,
                        problem_spec=request.problem_spec,
                        semantic_scene=request.base_semantic_scene,
                    )
                    if expected is None or routing.resolved != expected:
                        raise _ProjectileInvariantError("Director changed local route resolution")
                    resolved = routing.resolved
                    _validate_resolved_identity(
                        resolved,
                        accepted=accepted,
                        expected_result_problem=request.problem_spec,
                        expected_route=expected.route,
                    )
                except VisualActEngineError as exc:
                    failure_attempt: Literal[1, 2] = 2 if repair_announced else 1
                    yield lifecycle.failed(
                        _DIRECTOR_FAILURES[exc.code],
                        attempt=failure_attempt,
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    yield lifecycle.failed(
                        ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                        attempt=2 if repair_announced else 1,
                    )
                    return

            if not isinstance(resolved, ResolvedProjectileMotionAct):
                yield lifecycle.failed(
                    ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                    attempt=attempt,
                )
                return
            try:
                remaining = min(
                    MAX_SAFE_SEQUENCE - request.base_scene.revision,
                    MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision,
                )
                if len(resolved.checkpoint_ids) > remaining:
                    raise _ProjectileCapacityError("routed suffix exceeds revision capacity")
                beat = lower_resolved_projectile_motion_act(
                    resolved,
                    generation=request.generation,
                )
                compiled = compile_projectile_motion_checkpoint_beat(
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
            except _ProjectileCapacityError:
                yield lifecycle.failed(
                    ProjectileChoreographyFailureCode.CHOREOGRAPHY_CAPACITY_LIMIT,
                    attempt=attempt,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                yield lifecycle.failed(
                    ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
                    attempt=attempt,
                )
                return

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
    "ProjectileDirectorClientFactory",
    "ProjectileMotionService",
]
