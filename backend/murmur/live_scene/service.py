"""Bounded model-stream orchestration for ephemeral live scenes."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from pydantic import ValidationError

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_MODEL_OUTPUT_TOKENS,
    MAX_SCENE_NODES,
    LiveSceneRequest,
    PutSceneOperation,
    ScenePatchDraft,
    ScenePatchEvent,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.prompt import build_scene_messages, scene_patch_target
from murmur.live_scene.semantic_compiler import (
    SemanticCompilationError,
    compile_teaching_beat,
)
from murmur.live_scene.semantic_contracts import (
    MAX_SEMANTIC_COMPONENTS,
    PYTHAGOREAN_ROLE_ORDER,
    AbstainVisualDecision,
    CompiledTeachingBeat,
    CompiledVisualAtom,
    CompilerCertificateV1,
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    TeachingBeatDraft,
    VisualActAbstainReason,
    compiler_certificate_sha256,
    roles_through,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import digest_matches
from murmur.live_scene.semantic_prompt import build_semantic_scene_messages
from murmur.live_scene.semantic_service_contracts import (
    MAX_COMPILED_ATOMS,
    SemanticAtomMetadata,
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
    SemanticSceneStreamDeclinedEvent,
    SemanticSceneStreamEvent,
)
from murmur.live_scene.semantic_stream_parser import (
    TeachingBeatStreamError,
    TeachingBeatStreamParser,
)
from murmur.live_scene.semantic_verifier import (
    SemanticVerificationError,
    verify_pythagorean_realization,
)
from murmur.live_scene.semantic_wire import encode_semantic_scene_stream_event
from murmur.live_scene.stream_parser import ScenePatchStreamError, ScenePatchStreamParser
from murmur.live_scene.visual_act_engine import (
    DEFAULT_VISUAL_ACT_MAX_TOKENS,
    VisualActEngineError,
    VisualActEngineErrorCode,
    VisualActRoutingEngine,
    VisualActRoutingRepairing,
    VisualActRoutingResult,
)
from murmur.live_scene.visual_act_lowering import lower_resolved_visual_act
from murmur.live_scene.wire import SceneStreamWireError, encode_scene_stream_event

_REPAIR_MESSAGE = "The first visual draft needed correction. The last board is safe while I retry."
_INVALID_STREAM_MESSAGE = (
    "I couldn't finish a valid visual. The last board is safe; please try again."
)
_PROVIDER_ERROR_MESSAGE = (
    "The visual generator is temporarily unavailable. The last board is safe; please try again."
)
_PROVIDER_TIMEOUT_MESSAGE = (
    "The visual generator took too long. The last board is safe; please try again."
)
_PROVIDER_RATE_LIMIT_MESSAGE = "Visual model capacity is busy. Please try again shortly."
_CONTEXT_LIMIT_MESSAGE = (
    "This board is too large for another model pass. The current board remains safe."
)
_REVISION_LIMIT_MESSAGE = (
    "This board has reached its revision limit. The current board remains safe."
)
_SEMANTIC_BASE_MISMATCH_MESSAGE = (
    "The semantic lesson no longer matches the visible board. Refresh before continuing."
)
_SEMANTIC_CAPACITY_MESSAGE = (
    "This board has no room for another verified visual atom. The current board remains safe."
)
_SEMANTIC_INTEGRITY_MESSAGE = (
    "The verified visual runtime rejected an internal result. The current board remains safe."
)
_SEMANTIC_NAMESPACE_MESSAGE = (
    "The current board conflicts with this verified visual. Reset the board before trying again."
)
_UNSUPPORTED_VISUAL_MESSAGE = (
    "This request does not match a visual I can draw yet. The current board is unchanged."
)
_NO_FORWARD_VISUAL_MESSAGE = (
    "That visual is already complete at this stage. The current board is unchanged."
)
_ROUTING_REPAIR_MESSAGE = (
    "The first visual direction needed correction. The current board is safe while I retry."
)

_VISUAL_ROUTING_FAILURES: dict[
    VisualActEngineErrorCode,
    tuple[str, str],
] = {
    VisualActEngineErrorCode.CONTEXT_INVALID: (
        "context_too_large",
        _CONTEXT_LIMIT_MESSAGE,
    ),
    VisualActEngineErrorCode.PROVIDER_RATE_LIMIT: (
        "provider_rate_limited",
        _PROVIDER_RATE_LIMIT_MESSAGE,
    ),
    VisualActEngineErrorCode.PROVIDER_TIMEOUT: (
        "provider_timeout",
        _PROVIDER_TIMEOUT_MESSAGE,
    ),
    VisualActEngineErrorCode.PROVIDER_ERROR: (
        "provider_error",
        _PROVIDER_ERROR_MESSAGE,
    ),
    VisualActEngineErrorCode.INVALID_VISUAL_ACT: (
        "invalid_visual_act",
        _INVALID_STREAM_MESSAGE,
    ),
    VisualActEngineErrorCode.INTERNAL_ERROR: (
        "semantic_integrity_error",
        _SEMANTIC_INTEGRITY_MESSAGE,
    ),
}


def _provider_failure_message(code: str) -> str:
    if code == "provider_timeout":
        return _PROVIDER_TIMEOUT_MESSAGE
    if code == "provider_rate_limited":
        return _PROVIDER_RATE_LIMIT_MESSAGE
    return _PROVIDER_ERROR_MESSAGE


def _visual_decline_message(reason: VisualActAbstainReason) -> str:
    if reason is VisualActAbstainReason.UNSUPPORTED_INTENT:
        return _UNSUPPORTED_VISUAL_MESSAGE
    return _NO_FORWARD_VISUAL_MESSAGE


def _semantic_failure_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
    code: str,
    message: str,
    retryable: bool,
) -> SceneStreamFailedEvent:
    event = SceneStreamFailedEvent(
        generation=generation,
        attempt=attempt,
        code=code,
        message=message,
        last_accepted_revision=revision,
        retryable=retryable,
    )
    encode_semantic_scene_stream_event(event)
    return event


def _semantic_integrity_failure_event(
    *,
    generation: int,
    attempt: int,
    revision: int,
) -> SceneStreamFailedEvent:
    return _semantic_failure_event(
        generation=generation,
        attempt=attempt,
        revision=revision,
        code="semantic_integrity_error",
        message=_SEMANTIC_INTEGRITY_MESSAGE,
        retryable=False,
    )


def _semantic_repairing_event(
    *,
    generation: int,
    revision: int,
) -> SceneStreamRepairingEvent:
    event = SceneStreamRepairingEvent(
        generation=generation,
        from_attempt=1,
        to_attempt=2,
        last_accepted_revision=revision,
        message=_ROUTING_REPAIR_MESSAGE,
    )
    encode_semantic_scene_stream_event(event)
    return event


class SceneModelClient(Protocol):
    """Small provider surface required by scene authoring."""

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str | bytes]: ...


SceneModelClientFactory = Callable[[], SceneModelClient]
SceneClock = Callable[[], float]


class _ScenePatchApplicationError(ValueError):
    """Safe internal reason for asking the model to repair its output."""


class _SemanticBaseError(ValueError):
    """Raised when the supplied semantic prefix does not match the low-level board."""


class _SemanticRepairableError(ValueError):
    """Fixed safe reason a different model-authored teaching beat can repair."""


class _SemanticCapacityError(_SemanticRepairableError):
    """The requested suffix does not fit, though a smaller future request may."""


class _SemanticNamespaceCollisionError(_SemanticRepairableError):
    """Accepted low-level state occupies a server-owned semantic node ID."""


class _SemanticInvariantError(RuntimeError):
    """Raised when deterministic server-owned compilation or admission fails."""


def _scene_json(scene: SceneState) -> str:
    return json.dumps(
        scene.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _semantic_scene_json(scene: SemanticSceneState) -> str:
    """Serialize only model-relevant semantic state, excluding the certificate head."""

    return json.dumps(
        scene.model_dump(
            mode="json",
            by_alias=True,
            exclude={"certificate_head_sha256"},
        ),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _split_after_newlines(chunk: str | bytes) -> tuple[str | bytes, ...]:
    """Split provider chunks after LF so earlier valid frames survive a later bad frame."""

    if isinstance(chunk, bytes):
        byte_parts = chunk.split(b"\n")
        byte_segments = [part + b"\n" for part in byte_parts[:-1]]
        if byte_parts[-1] or not byte_segments:
            byte_segments.append(byte_parts[-1])
        return tuple(byte_segments)

    text_parts = chunk.split("\n")
    text_segments = [part + "\n" for part in text_parts[:-1]]
    if text_parts[-1] or not text_segments:
        text_segments.append(text_parts[-1])
    return tuple(text_segments)


def _feed_chunk(
    parser: ScenePatchStreamParser,
    chunk: str | bytes,
) -> Iterable[ScenePatchDraft]:
    for segment in _split_after_newlines(chunk):
        yield from parser.feed(segment)


def _feed_semantic_chunk(
    parser: TeachingBeatStreamParser,
    chunk: str | bytes,
) -> Iterable[TeachingBeatDraft]:
    for segment in _split_after_newlines(chunk):
        yield from parser.feed(segment)


def _apply_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    """Apply one already-validated draft atomically while preserving semantic node order."""

    node_order = [node.id for node in scene.nodes]
    nodes_by_id = {node.id: node for node in scene.nodes}

    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            node_id = operation.node.id
            if node_id not in nodes_by_id:
                node_order.append(node_id)
            nodes_by_id[node_id] = operation.node
            continue

        node_id = operation.id
        if node_id not in nodes_by_id:
            raise _ScenePatchApplicationError("remove operation targeted an absent node")
        del nodes_by_id[node_id]
        node_order.remove(node_id)

    try:
        candidate = SceneState(
            revision=scene.revision + 1,
            nodes=tuple(nodes_by_id[node_id] for node_id in node_order),
        )
    except ValidationError as exc:
        raise _ScenePatchApplicationError("patch produced an invalid scene") from exc

    if candidate.nodes == scene.nodes:
        raise _ScenePatchApplicationError("patch made no scene change")
    return candidate


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return max(0.0, (finished_at - started_at) * 1_000.0)


@dataclass
class _GenerationState:
    generation: int
    scene: SceneState
    patch_limit: int
    started_at: float
    clock: SceneClock
    accepted_patch_ids: set[str] = field(default_factory=set)
    patch_count: int = 0
    first_patch_ms: float | None = None

    @property
    def remaining_patch_budget(self) -> int:
        return self.patch_limit - self.patch_count

    @property
    def budget_reached(self) -> bool:
        return self.patch_count >= self.patch_limit

    def accept(self, patch: ScenePatchDraft, *, attempt: int) -> ScenePatchEvent:
        if patch.patch_id in self.accepted_patch_ids:
            raise _ScenePatchApplicationError("patchId duplicated an accepted patch")

        base_revision = self.scene.revision
        next_scene = _apply_patch(self.scene, patch)
        next_sequence = self.patch_count + 1
        event = ScenePatchEvent(
            generation=self.generation,
            attempt=attempt,
            sequence=next_sequence,
            base_revision=base_revision,
            result_revision=next_scene.revision,
            patch=patch,
        )
        try:
            encode_scene_stream_event(event)
        except SceneStreamWireError as exc:
            raise _ScenePatchApplicationError("patch exceeded the browser wire budget") from exc
        self.scene = next_scene
        self.accepted_patch_ids.add(patch.patch_id)
        self.patch_count = next_sequence
        if self.first_patch_ms is None:
            self.first_patch_ms = _elapsed_ms(self.started_at, self.clock())
        return event

    def completed_event(self, *, repaired: bool) -> SceneStreamCompletedEvent:
        assert self.first_patch_ms is not None
        total_ms = max(self.first_patch_ms, _elapsed_ms(self.started_at, self.clock()))
        return SceneStreamCompletedEvent(
            generation=self.generation,
            final_revision=self.scene.revision,
            patch_count=self.patch_count,
            first_patch_ms=self.first_patch_ms,
            total_ms=total_ms,
            repaired=repaired,
        )


@dataclass
class _AttemptOutcome:
    patch_count: int = 0
    invalid_reason: str | None = None
    provider_failure_code: str | None = None


@dataclass(frozen=True)
class _PreparedSemanticAtom:
    event: SemanticScenePatchEvent
    scene: SceneState
    semantic_scene: SemanticSceneState


@dataclass(frozen=True)
class _PreparedSemanticBatch:
    atoms: tuple[_PreparedSemanticAtom, ...]


@dataclass
class _SemanticAttemptOutcome:
    batch: _PreparedSemanticBatch | None = None
    invalid_reason: str | None = None
    provider_failure_code: str | None = None
    integrity_failure: bool = False


@dataclass
class _SemanticGenerationState:
    generation: int
    scene: SceneState
    semantic_scene: SemanticSceneState
    atom_limit: int
    started_at: float
    clock: SceneClock
    atom_count: int = 0
    first_atom_ms: float | None = None

    @property
    def remaining_atom_budget(self) -> int:
        return self.atom_limit - self.atom_count

    def commit_atom(self, atom: _PreparedSemanticAtom) -> None:
        self.scene = atom.scene
        self.semantic_scene = atom.semantic_scene
        self.atom_count += 1
        if self.first_atom_ms is None:
            self.first_atom_ms = _elapsed_ms(self.started_at, self.clock())

    def completed_event(self, *, repaired: bool) -> SceneStreamCompletedEvent:
        assert self.first_atom_ms is not None
        total_ms = max(self.first_atom_ms, _elapsed_ms(self.started_at, self.clock()))
        return SceneStreamCompletedEvent(
            generation=self.generation,
            final_revision=self.scene.revision,
            patch_count=self.atom_count,
            first_patch_ms=self.first_atom_ms,
            total_ms=total_ms,
            repaired=repaired,
        )


def _semantic_role_node_id(component_id: str, role_index: int) -> str:
    return f"{component_id}__{PYTHAGOREAN_ROLE_ORDER[role_index].value}"


def _serialized_semantic_prefix(
    scene: SceneState,
    *,
    component_id: str,
    role_count: int,
) -> tuple[dict[str, object], ...]:
    nodes_by_id = {node.id: node for node in scene.nodes}
    serialized: list[dict[str, object]] = []
    for role_index in range(role_count):
        node = nodes_by_id.get(_semantic_role_node_id(component_id, role_index))
        if node is None:
            raise _SemanticInvariantError(
                "semantic_base: a revealed role is absent from the low-level scene"
            )
        serialized.append(node.model_dump(mode="json", by_alias=True))
    return tuple(serialized)


def _validate_semantic_base(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
) -> None:
    if scene.revision != semantic_scene.revision:
        raise _SemanticBaseError("semantic_base: low-level and semantic revisions differ")
    has_committed_semantic_atoms = any(
        component.revealed_roles for component in semantic_scene.components
    )
    has_certificate_head = semantic_scene.certificate_head_sha256 is not None
    if has_committed_semantic_atoms != has_certificate_head:
        raise _SemanticBaseError(
            "semantic_base: committed roles and certificate chain head must agree"
        )

    node_ids = {node.id for node in scene.nodes}
    for component in semantic_scene.components:
        role_count = len(component.revealed_roles)
        for role_index in range(role_count, len(PYTHAGOREAN_ROLE_ORDER)):
            if _semantic_role_node_id(component.id, role_index) in node_ids:
                raise _SemanticBaseError(
                    "semantic_base: an unrevealed role already exists in the low-level scene"
                )
        if role_count == 0:
            continue
        try:
            serialized = _serialized_semantic_prefix(
                scene,
                component_id=component.id,
                role_count=role_count,
            )
            verify_pythagorean_realization(component.id, serialized)
        except (_SemanticInvariantError, SemanticVerificationError):
            raise _SemanticBaseError(
                "semantic_base: the low-level realization failed verification"
            ) from None


def _preflight_semantic_intent(
    state: _SemanticGenerationState,
    beat: TeachingBeatDraft,
) -> None:
    """Reject model-fixable intent before invoking the deterministic compiler."""

    component_id = beat.directive.id
    target_roles = roles_through(beat.directive.reveal_through)
    current = next(
        (
            component
            for component in state.semantic_scene.components
            if component.id == component_id
        ),
        None,
    )
    current_roles = () if current is None else current.revealed_roles
    if current_roles != target_roles[: len(current_roles)]:
        raise _SemanticRepairableError(
            "semantic_intent: revealThrough cannot move a component backward"
        )

    missing_roles = target_roles[len(current_roles) :]
    if not missing_roles:
        raise _SemanticRepairableError("semantic_intent: teaching beat made no semantic progress")
    if current is None and len(state.semantic_scene.components) >= MAX_SEMANTIC_COMPONENTS:
        raise _SemanticRepairableError(
            "semantic_intent: semantic component capacity was insufficient"
        )

    atom_count = len(missing_roles)
    if atom_count > state.remaining_atom_budget:
        raise _SemanticCapacityError("semantic_intent: atom batch exceeded the remaining budget")
    if atom_count > MAX_SAFE_SEQUENCE - state.scene.revision:
        raise _SemanticCapacityError("semantic_intent: low-level revision budget was insufficient")
    if atom_count > MAX_SAFE_SEQUENCE - state.semantic_scene.revision:
        raise _SemanticCapacityError("semantic_intent: semantic revision budget was insufficient")
    if atom_count > MAX_SCENE_NODES - len(state.scene.nodes):
        raise _SemanticCapacityError("semantic_intent: low-level node budget was insufficient")

    existing_ids = {node.id for node in state.scene.nodes}
    missing_node_ids = {
        _semantic_role_node_id(component_id, role_index)
        for role_index in range(len(current_roles), len(target_roles))
    }
    if existing_ids.intersection(missing_node_ids):
        raise _SemanticNamespaceCollisionError(
            "semantic_intent: a missing-role node ID collided with the accepted scene"
        )


def _advance_semantic_scene_for_atom(
    scene: SemanticSceneState,
    atom: CompiledVisualAtom,
) -> SemanticSceneState:
    certificate = atom.certificate
    if certificate is None:
        raise _SemanticInvariantError("semantic_batch: every atom requires a certificate")
    body = certificate.body
    component = PythagoreanAreaIdentityState(
        id=atom.component_id,
        revealed_roles=PYTHAGOREAN_ROLE_ORDER[: body.atom_ordinal],
    )
    if any(existing.id == atom.component_id for existing in scene.components):
        components = tuple(
            component if existing.id == atom.component_id else existing
            for existing in scene.components
        )
    else:
        components = (*scene.components, component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=components,
        certificate_head_sha256=certificate.certificate_sha256,
    )


def _validate_semantic_certificate_transition(
    certificate: CompilerCertificateV1,
    base_scene: SemanticSceneState,
    result_scene: SemanticSceneState,
) -> None:
    """Independently bind one certificate to the exact candidate semantic transition."""

    body = certificate.body
    if not digest_matches(
        certificate.certificate_sha256,
        compiler_certificate_sha256(body),
    ):
        raise _SemanticInvariantError("semantic_batch: certificate digest did not match its body")
    if body.previous_certificate_sha256 != base_scene.certificate_head_sha256:
        raise _SemanticInvariantError(
            "semantic_batch: certificate did not extend the candidate chain head"
        )
    if body.base_semantic_revision != base_scene.revision:
        raise _SemanticInvariantError(
            "semantic_batch: certificate base revision did not match the candidate scene"
        )
    if body.result_semantic_revision != result_scene.revision:
        raise _SemanticInvariantError(
            "semantic_batch: certificate result revision did not match the candidate scene"
        )
    if not digest_matches(body.base_scene_sha256, semantic_scene_sha256(base_scene)):
        raise _SemanticInvariantError(
            "semantic_batch: certificate base hash did not match the candidate scene"
        )
    if not digest_matches(body.result_scene_sha256, semantic_scene_sha256(result_scene)):
        raise _SemanticInvariantError(
            "semantic_batch: certificate result hash did not match the candidate scene"
        )
    if result_scene.certificate_head_sha256 != certificate.certificate_sha256:
        raise _SemanticInvariantError(
            "semantic_batch: result scene did not advance to the certificate chain head"
        )


def _prepare_semantic_batch(
    state: _SemanticGenerationState,
    compiled: CompiledTeachingBeat,
    *,
    attempt: int,
) -> _PreparedSemanticBatch:
    if compiled.base_scene != state.semantic_scene:
        raise _SemanticInvariantError("semantic_batch: compiler base did not match accepted state")
    if not compiled.atoms:
        raise _SemanticInvariantError("semantic_batch: compiler omitted preflighted atoms")
    try:
        atoms = tuple(
            CompiledVisualAtom.model_validate(
                atom.model_dump(mode="json", by_alias=True),
            )
            for atom in compiled.atoms
        )
    except (AttributeError, TypeError, ValidationError):
        raise _SemanticInvariantError(
            "semantic_batch: compiler emitted an invalid visual atom"
        ) from None
    if any(atom.certificate is None for atom in atoms):
        raise _SemanticInvariantError("semantic_batch: every atom requires a certificate")

    atom_count = len(atoms)
    if atom_count > state.remaining_atom_budget:
        raise _SemanticInvariantError("semantic_batch: compiler bypassed the atom budget")
    if atom_count > MAX_SAFE_SEQUENCE - state.scene.revision:
        raise _SemanticInvariantError("semantic_batch: compiler bypassed the revision budget")
    if atom_count > MAX_SAFE_SEQUENCE - state.semantic_scene.revision:
        raise _SemanticInvariantError("semantic_batch: compiler bypassed the semantic budget")
    if atom_count > MAX_SCENE_NODES - len(state.scene.nodes):
        raise _SemanticInvariantError("semantic_batch: compiler bypassed the node budget")

    patch_ids = tuple(atom.patch.patch_id for atom in atoms)
    if len(patch_ids) != len(set(patch_ids)):
        raise _SemanticInvariantError("semantic_batch: compiled patch IDs were not unique")
    target_ids = tuple(atom.patch.operations[0].target_id for atom in atoms)
    if len(target_ids) != len(set(target_ids)):
        raise _SemanticInvariantError("semantic_batch: compiled node IDs were not unique")
    existing_ids = {node.id for node in state.scene.nodes}
    if existing_ids.intersection(target_ids):
        raise _SemanticInvariantError(
            "semantic_batch: compiler bypassed missing-role collision admission"
        )

    candidate_scene = state.scene
    candidate_semantic_scene = state.semantic_scene
    prepared_atoms: list[_PreparedSemanticAtom] = []
    for offset, atom in enumerate(atoms, start=1):
        try:
            next_scene = _apply_patch(candidate_scene, atom.patch)
        except _ScenePatchApplicationError:
            raise _SemanticInvariantError(
                "semantic_batch: a compiled patch could not be applied"
            ) from None

        next_semantic_scene = _advance_semantic_scene_for_atom(
            candidate_semantic_scene,
            atom,
        )
        certificate = atom.certificate
        assert certificate is not None
        _validate_semantic_certificate_transition(
            certificate,
            candidate_semantic_scene,
            next_semantic_scene,
        )
        try:
            realized_receipts = verify_pythagorean_realization(
                atom.component_id,
                _serialized_semantic_prefix(
                    next_scene,
                    component_id=atom.component_id,
                    role_count=certificate.body.atom_ordinal,
                ),
            )
        except SemanticVerificationError:
            raise _SemanticInvariantError(
                "semantic_batch: a realized semantic prefix failed verification"
            ) from None
        if realized_receipts[-1] != atom.receipt:
            raise _SemanticInvariantError(
                "semantic_batch: verifier receipt changed after patch application"
            )

        event_candidate = SemanticScenePatchEvent(
            generation=state.generation,
            attempt=attempt,
            sequence=state.atom_count + offset,
            base_revision=candidate_scene.revision,
            result_revision=next_scene.revision,
            patch=atom.patch,
            semantic=SemanticAtomMetadata(
                beat=compiled.beat,
                atom_id=atom.atom_id,
                component_id=atom.component_id,
                role=atom.role,
                atom_ordinal=certificate.body.atom_ordinal,
                semantic_base_revision=candidate_semantic_scene.revision,
                semantic_result_revision=next_semantic_scene.revision,
                receipt=atom.receipt,
                certificate=certificate,
            ),
        )
        try:
            event = SemanticScenePatchEvent.model_validate(
                event_candidate.model_dump(mode="json", by_alias=True),
            )
        except ValidationError:
            raise _SemanticInvariantError(
                "semantic_batch: compiled event failed independent contract validation"
            ) from None
        try:
            encode_semantic_scene_stream_event(event)
        except SceneStreamWireError:
            raise _SemanticInvariantError(
                "semantic_batch: a compiled event exceeded the browser wire budget"
            ) from None
        prepared_atoms.append(
            _PreparedSemanticAtom(
                event=event,
                scene=next_scene,
                semantic_scene=next_semantic_scene,
            )
        )
        candidate_scene = next_scene
        candidate_semantic_scene = next_semantic_scene

    if candidate_semantic_scene != compiled.result_scene:
        raise _SemanticInvariantError(
            "semantic_batch: compiled result state did not match preflight"
        )
    if candidate_scene.revision != candidate_semantic_scene.revision:
        raise _SemanticInvariantError("semantic_batch: resulting revisions diverged")
    return _PreparedSemanticBatch(atoms=tuple(prepared_atoms))


def _compile_semantic_batch(
    state: _SemanticGenerationState,
    beat: TeachingBeatDraft,
    *,
    attempt: int,
) -> _PreparedSemanticBatch:
    """Compile and independently validate one admitted semantic beat."""

    _preflight_semantic_intent(state, beat)
    compiled_candidate = compile_teaching_beat(beat, state.semantic_scene)
    if compiled_candidate.beat != beat:
        raise _SemanticInvariantError("semantic_batch: compiler changed the teaching beat")
    try:
        compiled = CompiledTeachingBeat.model_validate(
            compiled_candidate.model_dump(mode="json", by_alias=True),
        )
    except (AttributeError, TypeError, ValidationError):
        raise _SemanticInvariantError(
            "semantic_batch: compiler result failed independent validation"
        ) from None
    return _prepare_semantic_batch(state, compiled, attempt=attempt)


async def _next_before_deadline(
    stream: AsyncIterator[str | bytes],
    *,
    deadline: float,
) -> str | bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(anext(stream), timeout=remaining)


async def _close_upstream(stream: object | None, *, timeout_seconds: float) -> None:
    await close_async_resource(stream, timeout_seconds=timeout_seconds)


class SceneAuthoringService:
    """Stream server-authoritative scene events from one injected text model client."""

    def __init__(
        self,
        client: SceneModelClient | None = None,
        *,
        client_factory: SceneModelClientFactory | None = None,
        clock: SceneClock = time.perf_counter,
        temperature: float = 0.2,
        max_tokens: int = 4_096,
        timeout_seconds: float = 20.0,
        before_provider_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if (client is None) == (client_factory is None):
            raise ValueError("provide exactly one of client or client_factory")
        if client_factory is not None and not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or not math.isfinite(temperature)
            or temperature < 0
            or temperature > 2
        ):
            raise ValueError("temperature must be finite and between 0 and 2")
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
        self._temperature = float(temperature)
        self._max_tokens = max_tokens
        self._timeout_seconds = float(timeout_seconds)
        self._before_provider_dispatch = before_provider_dispatch
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    def _resolve_client(self) -> SceneModelClient:
        if self._client is not None:
            return self._client
        assert self._client_factory is not None
        return self._client_factory()

    async def _admit_provider_dispatch(self) -> None:
        if self._before_provider_dispatch is not None:
            await self._before_provider_dispatch()

    async def _stream_attempt(
        self,
        *,
        client: SceneModelClient,
        state: _GenerationState,
        attempt: int,
        patch_target: int,
        messages: list[dict[str, str]],
        outcome: _AttemptOutcome,
    ) -> AsyncIterator[ScenePatchEvent]:
        parser = ScenePatchStreamParser()
        upstream: object | None = None

        try:
            await self._admit_provider_dispatch()
            upstream = client.stream(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if not hasattr(upstream, "__anext__"):
                raise TypeError("provider did not return an async iterator")
            typed_upstream = cast(AsyncIterator[str | bytes], upstream)
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds

            while not state.budget_reached and outcome.patch_count < patch_target:
                try:
                    chunk = await _next_before_deadline(typed_upstream, deadline=deadline)
                except StopAsyncIteration:
                    break

                for patch in _feed_chunk(parser, chunk):
                    event = state.accept(patch, attempt=attempt)
                    outcome.patch_count += 1
                    yield event
                    if state.budget_reached or outcome.patch_count >= patch_target:
                        break

            if not state.budget_reached and outcome.patch_count < patch_target:
                for patch in parser.finish():
                    event = state.accept(patch, attempt=attempt)
                    outcome.patch_count += 1
                    yield event
                    if state.budget_reached or outcome.patch_count >= patch_target:
                        break
        except ScenePatchStreamError as exc:
            outcome.invalid_reason = exc.repair_hint
        except _ScenePatchApplicationError as exc:
            outcome.invalid_reason = str(exc)
        except TimeoutError:
            outcome.provider_failure_code = "provider_timeout"
        except SceneAdmissionError:
            outcome.provider_failure_code = "provider_rate_limited"
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome.provider_failure_code = "provider_error"
        finally:
            if not parser.closed:
                parser.abort()
            await _close_upstream(
                upstream,
                timeout_seconds=self._cleanup_timeout_seconds,
            )

    async def _prepare_semantic_attempt(
        self,
        *,
        client: SceneModelClient,
        state: _SemanticGenerationState,
        attempt: int,
        messages: list[dict[str, str]],
        outcome: _SemanticAttemptOutcome,
    ) -> None:
        parser = TeachingBeatStreamParser()
        upstream: object | None = None
        beat: TeachingBeatDraft | None = None

        try:
            await self._admit_provider_dispatch()
            upstream = client.stream(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if not hasattr(upstream, "__anext__"):
                raise TypeError("provider did not return an async iterator")
            typed_upstream = cast(AsyncIterator[str | bytes], upstream)
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds

            while beat is None:
                try:
                    chunk = await _next_before_deadline(typed_upstream, deadline=deadline)
                except StopAsyncIteration:
                    finished_beats = parser.finish()
                    if len(finished_beats) > 1:
                        outcome.invalid_reason = (
                            "semantic_stream: an attempt may contain only one teaching beat"
                        )
                        break
                    beat = finished_beats[0] if finished_beats else None
                    break

                for parsed_beat in _feed_semantic_chunk(parser, chunk):
                    beat = parsed_beat
                    break
        except TeachingBeatStreamError as exc:
            outcome.invalid_reason = exc.repair_hint
        except TimeoutError:
            outcome.provider_failure_code = "provider_timeout"
        except SceneAdmissionError:
            outcome.provider_failure_code = "provider_rate_limited"
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome.provider_failure_code = "provider_error"
        finally:
            if not parser.closed:
                parser.abort()
            await _close_upstream(
                upstream,
                timeout_seconds=self._cleanup_timeout_seconds,
            )

        if outcome.invalid_reason is not None or outcome.provider_failure_code is not None:
            return
        if beat is None:
            outcome.invalid_reason = "semantic_stream: model stream ended without a beat"
            return

        try:
            outcome.batch = _compile_semantic_batch(state, beat, attempt=attempt)
        except _SemanticRepairableError as exc:
            outcome.invalid_reason = str(exc)
        except (
            _SemanticInvariantError,
            SemanticCompilationError,
            SemanticVerificationError,
            ValidationError,
        ):
            outcome.integrity_failure = True
        except Exception:
            outcome.integrity_failure = True

    async def stream_events(self, request: LiveSceneRequest) -> AsyncIterator[SceneStreamEvent]:
        """Yield one bounded generation, including at most one model repair attempt."""

        if not isinstance(request, LiveSceneRequest):
            raise TypeError("request must be a LiveSceneRequest")

        started_at = self._clock()
        repair_reason: str | None = None
        patch_limit = min(
            MAX_ACCEPTED_PATCHES,
            MAX_SAFE_SEQUENCE - request.base_scene.revision,
        )
        state = _GenerationState(
            generation=request.generation,
            scene=request.base_scene,
            patch_limit=patch_limit,
            started_at=started_at,
            clock=self._clock,
        )

        yield SceneStreamStartedEvent(
            generation=request.generation,
            attempt=1,
            base_revision=state.scene.revision,
        )

        if patch_limit <= 0:
            yield SceneStreamFailedEvent(
                generation=request.generation,
                attempt=1,
                code="revision_limit",
                message=_REVISION_LIMIT_MESSAGE,
                last_accepted_revision=state.scene.revision,
                retryable=False,
            )
            return

        client: SceneModelClient | None = None
        owns_client = self._client is None
        try:
            try:
                client = self._resolve_client()
            except Exception:
                yield SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=1,
                    code="provider_error",
                    message=_PROVIDER_ERROR_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                return

            for attempt in (1, 2):
                current_scene_json = _scene_json(state.scene)
                patch_target = scene_patch_target(
                    state.remaining_patch_budget,
                    repair=attempt == 2,
                )
                repair_context: dict[str, str] | None = None
                if attempt == 2:
                    assert repair_reason is not None
                    repair_context = {
                        "error": repair_reason,
                        "last_accepted_scene_json": current_scene_json,
                    }

                try:
                    messages = build_scene_messages(
                        request.prompt,
                        current_scene_json,
                        state.remaining_patch_budget,
                        repair_context=repair_context,
                    )
                except (TypeError, ValueError):
                    yield SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code="context_too_large",
                        message=_CONTEXT_LIMIT_MESSAGE,
                        last_accepted_revision=state.scene.revision,
                        retryable=False,
                    )
                    return

                outcome = _AttemptOutcome()
                attempt_stream = self._stream_attempt(
                    client=client,
                    state=state,
                    attempt=attempt,
                    patch_target=patch_target,
                    messages=messages,
                    outcome=outcome,
                )
                try:
                    async for event in attempt_stream:
                        yield event
                finally:
                    await attempt_stream.aclose()

                if outcome.provider_failure_code is not None:
                    yield SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code=outcome.provider_failure_code,
                        message=_provider_failure_message(outcome.provider_failure_code),
                        last_accepted_revision=state.scene.revision,
                        retryable=True,
                    )
                    return

                if state.budget_reached or (
                    outcome.invalid_reason is None and outcome.patch_count > 0
                ):
                    yield state.completed_event(repaired=attempt == 2)
                    return

                repair_reason = outcome.invalid_reason or "model stream ended without a patch"
                if attempt == 1:
                    yield SceneStreamRepairingEvent(
                        generation=request.generation,
                        from_attempt=1,
                        to_attempt=2,
                        last_accepted_revision=state.scene.revision,
                        message=_REPAIR_MESSAGE,
                    )
                    continue

                yield SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=2,
                    code="invalid_scene_stream",
                    message=_INVALID_STREAM_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                return
        finally:
            if owns_client:
                await _close_upstream(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )

    async def stream_routed_semantic_events(
        self,
        request: SemanticLiveSceneRequest,
    ) -> AsyncIterator[SemanticSceneStreamEvent]:
        """Route one visual act, then compile only server-owned semantic atoms."""

        if not isinstance(request, SemanticLiveSceneRequest):
            raise TypeError("request must be a SemanticLiveSceneRequest")

        started_at = self._clock()
        atom_limit = min(
            MAX_COMPILED_ATOMS,
            MAX_SAFE_SEQUENCE - request.base_scene.revision,
            MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision,
            MAX_SCENE_NODES - len(request.base_scene.nodes),
        )
        state = _SemanticGenerationState(
            generation=request.generation,
            scene=request.base_scene,
            semantic_scene=request.base_semantic_scene,
            atom_limit=atom_limit,
            started_at=started_at,
            clock=self._clock,
        )

        started = SceneStreamStartedEvent(
            generation=request.generation,
            attempt=1,
            base_revision=state.scene.revision,
        )
        encode_semantic_scene_stream_event(started)
        yield started

        if atom_limit <= 0:
            revision_limited = (
                request.base_scene.revision >= MAX_SAFE_SEQUENCE
                or request.base_semantic_scene.revision >= MAX_SAFE_SEQUENCE
            )
            yield _semantic_failure_event(
                generation=request.generation,
                attempt=1,
                revision=state.scene.revision,
                code="revision_limit" if revision_limited else "semantic_capacity_limit",
                message=(
                    _REVISION_LIMIT_MESSAGE if revision_limited else _SEMANTIC_CAPACITY_MESSAGE
                ),
                retryable=False,
            )
            return

        try:
            _validate_semantic_base(state.scene, state.semantic_scene)
        except _SemanticBaseError:
            yield _semantic_failure_event(
                generation=request.generation,
                attempt=1,
                revision=state.scene.revision,
                code="semantic_base_mismatch",
                message=_SEMANTIC_BASE_MISMATCH_MESSAGE,
                retryable=False,
            )
            return

        client: SceneModelClient | None = None
        owns_client = self._client is None
        try:
            try:
                client = self._resolve_client()
            except Exception:
                yield _semantic_failure_event(
                    generation=request.generation,
                    attempt=1,
                    revision=state.scene.revision,
                    code="provider_error",
                    message=_PROVIDER_ERROR_MESSAGE,
                    retryable=True,
                )
                return

            routing: VisualActRoutingResult | None = None
            repair_announced = False
            try:
                engine = VisualActRoutingEngine(
                    client,
                    max_tokens=min(self._max_tokens, DEFAULT_VISUAL_ACT_MAX_TOKENS),
                    timeout_seconds=self._timeout_seconds,
                    before_dispatch=self._admit_provider_dispatch,
                )
                async for step in engine.stream_route(
                    prompt=request.prompt,
                    semantic_scene=state.semantic_scene,
                ):
                    if isinstance(step, VisualActRoutingRepairing):
                        if repair_announced:
                            raise RuntimeError("visual routing emitted duplicate repair boundaries")
                        repair_announced = True
                        yield _semantic_repairing_event(
                            generation=request.generation,
                            revision=state.scene.revision,
                        )
                    elif isinstance(step, VisualActRoutingResult) and routing is None:
                        routing = step
                    else:
                        raise RuntimeError("visual routing emitted an invalid lifecycle step")
            except VisualActEngineError as exc:
                attempt = 2 if repair_announced else 1
                code, message = _VISUAL_ROUTING_FAILURES[exc.code]
                yield _semantic_failure_event(
                    generation=request.generation,
                    attempt=attempt,
                    revision=state.scene.revision,
                    code=code,
                    message=message,
                    retryable=exc.retryable,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                yield _semantic_integrity_failure_event(
                    generation=request.generation,
                    attempt=2 if repair_announced else 1,
                    revision=state.scene.revision,
                )
                return

            if routing is None or routing.repaired != repair_announced:
                yield _semantic_integrity_failure_event(
                    generation=request.generation,
                    attempt=2 if repair_announced else 1,
                    revision=state.scene.revision,
                )
                return

            attempt = routing.provider_attempts

            decision = routing.decision
            resolved = routing.resolved
            if isinstance(decision, AbstainVisualDecision) != (resolved is None):
                yield _semantic_integrity_failure_event(
                    generation=request.generation,
                    attempt=attempt,
                    revision=state.scene.revision,
                )
                return

            if isinstance(decision, AbstainVisualDecision):
                declined = SemanticSceneStreamDeclinedEvent(
                    generation=request.generation,
                    attempt=attempt,
                    final_revision=state.scene.revision,
                    reason_code=decision.reason_code,
                    message=_visual_decline_message(decision.reason_code),
                )
                encode_semantic_scene_stream_event(declined)
                yield declined
                return

            assert resolved is not None
            try:
                beat = lower_resolved_visual_act(
                    resolved,
                    generation=request.generation,
                )
                batch = _compile_semantic_batch(state, beat, attempt=attempt)
            except _SemanticCapacityError:
                yield _semantic_failure_event(
                    generation=request.generation,
                    attempt=attempt,
                    revision=state.scene.revision,
                    code="semantic_capacity_limit",
                    message=_SEMANTIC_CAPACITY_MESSAGE,
                    retryable=True,
                )
                return
            except _SemanticNamespaceCollisionError:
                yield _semantic_failure_event(
                    generation=request.generation,
                    attempt=attempt,
                    revision=state.scene.revision,
                    code="semantic_namespace_collision",
                    message=_SEMANTIC_NAMESPACE_MESSAGE,
                    retryable=False,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                yield _semantic_integrity_failure_event(
                    generation=request.generation,
                    attempt=attempt,
                    revision=state.scene.revision,
                )
                return

            for prepared_atom in batch.atoms:
                state.commit_atom(prepared_atom)
                yield prepared_atom.event
            completed = state.completed_event(repaired=attempt == 2)
            encode_semantic_scene_stream_event(completed)
            yield completed
        finally:
            if owns_client:
                await _close_upstream(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )

    async def stream_semantic_events(
        self,
        request: SemanticLiveSceneRequest,
    ) -> AsyncIterator[SemanticSceneStreamEvent]:
        """Yield one all-or-nothing compiler-preflighted semantic teaching beat."""

        if not isinstance(request, SemanticLiveSceneRequest):
            raise TypeError("request must be a SemanticLiveSceneRequest")

        started_at = self._clock()
        repair_reason: str | None = None
        atom_limit = min(
            MAX_COMPILED_ATOMS,
            MAX_SAFE_SEQUENCE - request.base_scene.revision,
            MAX_SAFE_SEQUENCE - request.base_semantic_scene.revision,
            MAX_SCENE_NODES - len(request.base_scene.nodes),
        )
        state = _SemanticGenerationState(
            generation=request.generation,
            scene=request.base_scene,
            semantic_scene=request.base_semantic_scene,
            atom_limit=atom_limit,
            started_at=started_at,
            clock=self._clock,
        )

        started = SceneStreamStartedEvent(
            generation=request.generation,
            attempt=1,
            base_revision=state.scene.revision,
        )
        encode_semantic_scene_stream_event(started)
        yield started

        if atom_limit <= 0:
            revision_limited = (
                request.base_scene.revision >= MAX_SAFE_SEQUENCE
                or request.base_semantic_scene.revision >= MAX_SAFE_SEQUENCE
            )
            failed = SceneStreamFailedEvent(
                generation=request.generation,
                attempt=1,
                code="revision_limit" if revision_limited else "semantic_capacity_limit",
                message=(
                    _REVISION_LIMIT_MESSAGE if revision_limited else _SEMANTIC_CAPACITY_MESSAGE
                ),
                last_accepted_revision=state.scene.revision,
                retryable=False,
            )
            encode_semantic_scene_stream_event(failed)
            yield failed
            return

        try:
            _validate_semantic_base(state.scene, state.semantic_scene)
        except _SemanticBaseError:
            failed = SceneStreamFailedEvent(
                generation=request.generation,
                attempt=1,
                code="semantic_base_mismatch",
                message=_SEMANTIC_BASE_MISMATCH_MESSAGE,
                last_accepted_revision=state.scene.revision,
                retryable=False,
            )
            encode_semantic_scene_stream_event(failed)
            yield failed
            return

        client: SceneModelClient | None = None
        owns_client = self._client is None
        try:
            try:
                client = self._resolve_client()
            except Exception:
                failed = SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=1,
                    code="provider_error",
                    message=_PROVIDER_ERROR_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                encode_semantic_scene_stream_event(failed)
                yield failed
                return

            for attempt in (1, 2):
                current_semantic_scene_json = _semantic_scene_json(state.semantic_scene)
                repair_context: dict[str, str] | None = None
                if attempt == 2:
                    assert repair_reason is not None
                    repair_context = {
                        "error": repair_reason,
                        "last_accepted_semantic_scene_json": current_semantic_scene_json,
                    }

                try:
                    messages = build_semantic_scene_messages(
                        request.prompt,
                        current_semantic_scene_json,
                        state.remaining_atom_budget,
                        repair_context=repair_context,
                    )
                except (TypeError, ValueError):
                    failed = SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code="context_too_large",
                        message=_CONTEXT_LIMIT_MESSAGE,
                        last_accepted_revision=state.scene.revision,
                        retryable=False,
                    )
                    encode_semantic_scene_stream_event(failed)
                    yield failed
                    return

                outcome = _SemanticAttemptOutcome()
                await self._prepare_semantic_attempt(
                    client=client,
                    state=state,
                    attempt=attempt,
                    messages=messages,
                    outcome=outcome,
                )

                if outcome.integrity_failure:
                    failed = SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code="semantic_integrity_error",
                        message=_SEMANTIC_INTEGRITY_MESSAGE,
                        last_accepted_revision=state.scene.revision,
                        retryable=False,
                    )
                    encode_semantic_scene_stream_event(failed)
                    yield failed
                    return

                if outcome.provider_failure_code is not None:
                    failed = SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code=outcome.provider_failure_code,
                        message=_provider_failure_message(outcome.provider_failure_code),
                        last_accepted_revision=state.scene.revision,
                        retryable=True,
                    )
                    encode_semantic_scene_stream_event(failed)
                    yield failed
                    return

                if outcome.batch is not None:
                    for prepared_atom in outcome.batch.atoms:
                        state.commit_atom(prepared_atom)
                        yield prepared_atom.event
                    completed = state.completed_event(repaired=attempt == 2)
                    encode_semantic_scene_stream_event(completed)
                    yield completed
                    return

                repair_reason = outcome.invalid_reason or (
                    "semantic_stream: model stream ended without a beat"
                )
                if attempt == 1:
                    repairing = SceneStreamRepairingEvent(
                        generation=request.generation,
                        from_attempt=1,
                        to_attempt=2,
                        last_accepted_revision=state.scene.revision,
                        message=_REPAIR_MESSAGE,
                    )
                    encode_semantic_scene_stream_event(repairing)
                    yield repairing
                    continue

                failed = SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=2,
                    code="invalid_scene_stream",
                    message=_INVALID_STREAM_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                encode_semantic_scene_stream_event(failed)
                yield failed
                return
        finally:
            if owns_client:
                await _close_upstream(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )


__all__ = [
    "SceneAuthoringService",
    "SceneClock",
    "SceneModelClient",
    "SceneModelClientFactory",
]
