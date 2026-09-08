"""Generate the provider-free completing-square choreography browser fixture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from murmur.live_scene.choreography_service_contracts import (
    CheckpointSemanticMetadataV2,
    ChoreographySceneCheckpointEvent,
    ChoreographySceneStreamEvent,
    dump_choreography_scene_stream_event,
)
from murmur.live_scene.choreography_wire import encode_choreography_scene_stream_event
from murmur.live_scene.completing_square_compiler import (
    COMPLETING_SQUARE_COMPILER_VERSION,
    CompiledCheckpointBeatV2,
    compile_checkpoint_beat,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.contracts import (
    PutSceneOperation,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_contracts import (
    ClarifyCornerDecision,
    ContinueChoreographyDecision,
    SemanticSceneState,
    StartChoreographyDecision,
    VisualActDecision,
)
from murmur.live_scene.visual_act_lowering import lower_resolved_choreography_act
from murmur.live_scene.visual_act_router import ResolvedChoreographyAct, resolve_visual_act

FIXTURE_VERSION = 1
FIXTURE_ID = "completing-the-square"
FIXTURE_GENERATION = 1
FIXTURE_ATTEMPT = 1
FIXTURE_PROMPT = "Solve x² + 6x = 7 visually by completing the square."


def _compile_decision(
    decision: VisualActDecision,
    *,
    generation: int,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> CompiledCheckpointBeatV2:
    resolved = resolve_visual_act(decision, base_semantic_scene)
    if not isinstance(resolved, ResolvedChoreographyAct):
        raise RuntimeError("fixture route did not resolve to completing-square choreography")

    beat = lower_resolved_choreography_act(resolved, generation=generation)
    compiled = compile_checkpoint_beat(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    expected_ids = (
        ("corner_detail",)
        if isinstance(decision, ClarifyCornerDecision)
        else tuple(checkpoint.value for checkpoint in resolved.missing_checkpoints)
    )
    actual_ids = tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints)
    if actual_ids != expected_ids:
        raise RuntimeError("compiler output did not match the routed checkpoint suffix")
    return compiled


def _replace_component(
    scene: SemanticSceneState,
    component: CompletingSquareState,
    certificate_head_sha256: str,
) -> SemanticSceneState:
    components = tuple(
        component if current.id == component.id else current for current in scene.components
    )
    if not any(current.id == component.id for current in scene.components):
        components = (*components, component)
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=components,
        certificate_head_sha256=certificate_head_sha256,
    )


def _apply_checkpoint(
    scene: SceneState,
    semantic_scene: SemanticSceneState,
    checkpoint,
) -> tuple[SceneState, SemanticSceneState, CompletingSquareState]:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        else:
            if operation.id not in nodes:
                raise RuntimeError("fixture checkpoint removes an absent node")
            del nodes[operation.id]
            order.remove(operation.id)
    next_scene = SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )

    existing = next(
        (
            component
            for component in semantic_scene.components
            if component.id == checkpoint.beat.component_id
        ),
        None,
    )
    if existing is not None and not isinstance(existing, CompletingSquareState):
        raise RuntimeError("fixture component id belongs to a different semantic kind")
    if checkpoint.checkpoint_id.value == "corner_detail":
        if not isinstance(existing, CompletingSquareState):
            raise RuntimeError("fixture clarification has no completing-square base")
        result_component = CompletingSquareState(
            id=existing.id,
            last_main_checkpoint=existing.last_main_checkpoint,
            corner_clarified=True,
        )
    else:
        result_component = CompletingSquareState(
            id=checkpoint.beat.component_id,
            last_main_checkpoint=CompletingSquareMainCheckpoint(checkpoint.checkpoint_id.value),
            corner_clarified=(
                existing.corner_clarified if isinstance(existing, CompletingSquareState) else False
            ),
        )
    next_semantic_scene = _replace_component(
        semantic_scene,
        result_component,
        checkpoint.certificate.certificate_sha256,
    )
    return next_scene, next_semantic_scene, result_component


def _materialize_prefix(
    compiled: CompiledCheckpointBeatV2,
    count: int,
) -> tuple[SceneState, SemanticSceneState]:
    scene = compiled.base_scene
    semantic_scene = compiled.base_semantic_scene
    for checkpoint in compiled.checkpoints[:count]:
        scene, semantic_scene, _ = _apply_checkpoint(
            scene,
            semantic_scene,
            checkpoint,
        )
    return scene, semantic_scene


def _checkpoint_events(
    compiled: CompiledCheckpointBeatV2,
    *,
    generation: int,
) -> tuple[ChoreographySceneCheckpointEvent, ...]:
    events: list[ChoreographySceneCheckpointEvent] = []
    expected_revision = compiled.base_semantic_scene.revision
    expected_certificate = compiled.base_semantic_scene.certificate_head_sha256

    scene = compiled.base_scene
    semantic_scene = compiled.base_semantic_scene
    for sequence, checkpoint in enumerate(compiled.checkpoints, start=1):
        certificate_body = checkpoint.certificate.body
        if (
            certificate_body.base_revision != expected_revision
            or certificate_body.previous_certificate_sha256 != expected_certificate
        ):
            raise RuntimeError("compiler output did not form a continuous certificate chain")

        scene, semantic_scene, result_component = _apply_checkpoint(
            scene,
            semantic_scene,
            checkpoint,
        )
        event = ChoreographySceneCheckpointEvent(
            generation=generation,
            attempt=FIXTURE_ATTEMPT,
            sequence=sequence,
            base_revision=certificate_body.base_revision,
            result_revision=certificate_body.result_revision,
            patch=checkpoint.patch,
            semantic=CheckpointSemanticMetadataV2(
                beat=checkpoint.beat,
                checkpoint_id=checkpoint.checkpoint_id,
                result_component=result_component,
                semantic_base_revision=certificate_body.base_revision,
                semantic_result_revision=certificate_body.result_revision,
                receipt=checkpoint.receipt,
                presentation=checkpoint.presentation,
                choreography=checkpoint.choreography,
                certificate=checkpoint.certificate,
            ),
        )
        events.append(event)
        expected_revision = certificate_body.result_revision
        expected_certificate = checkpoint.certificate.certificate_sha256

    if (
        expected_revision != compiled.result_semantic_scene.revision
        or expected_certificate != compiled.result_semantic_scene.certificate_head_sha256
        or scene != compiled.result_scene
        or semantic_scene != compiled.result_semantic_scene
    ):
        raise RuntimeError("compiler output did not reach its declared semantic result")
    return tuple(events)


def _lifecycle_events(
    compiled: CompiledCheckpointBeatV2,
    *,
    generation: int,
) -> tuple[ChoreographySceneStreamEvent, ...]:
    checkpoints = _checkpoint_events(compiled, generation=generation)
    return (
        SceneStreamStartedEvent(
            generation=generation,
            attempt=FIXTURE_ATTEMPT,
            base_revision=compiled.base_scene.revision,
        ),
        *checkpoints,
        SceneStreamCompletedEvent(
            generation=generation,
            final_revision=compiled.result_scene.revision,
            patch_count=len(checkpoints),
            first_patch_ms=0.0,
            total_ms=0.0,
            repaired=False,
        ),
    )


def _wire_event(event: ChoreographySceneStreamEvent) -> dict[str, object]:
    # Encoding is also the fixture's per-frame wire-budget preflight.
    encode_choreography_scene_stream_event(event)
    return dump_choreography_scene_stream_event(event)


def build_live_choreography_fixture() -> dict[str, object]:
    """Return the deterministic fixture payload without filesystem or provider I/O."""

    base_scene = SceneState(revision=0)
    base_semantic_scene = SemanticSceneState(revision=0)
    decision = StartChoreographyDecision(
        component_kind="completing_square",
        target_stage="solve",
    )
    compiled = _compile_decision(
        decision,
        generation=FIXTURE_GENERATION,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    lifecycle_events = _lifecycle_events(
        compiled,
        generation=FIXTURE_GENERATION,
    )
    checkpoint_events = tuple(
        event for event in lifecycle_events if isinstance(event, ChoreographySceneCheckpointEvent)
    )
    checkpoint_ids = [event.semantic.checkpoint_id.value for event in checkpoint_events]

    interrupted_scene, interrupted_semantic_scene = _materialize_prefix(compiled, 5)
    clarification_decision = ClarifyCornerDecision(component_id="square-lesson")
    clarification = _compile_decision(
        clarification_decision,
        generation=2,
        base_scene=interrupted_scene,
        base_semantic_scene=interrupted_semantic_scene,
    )
    continuation_decision = ContinueChoreographyDecision(
        component_id="square-lesson",
        target_stage="solve",
    )
    continuation = _compile_decision(
        continuation_decision,
        generation=3,
        base_scene=clarification.result_scene,
        base_semantic_scene=clarification.result_semantic_scene,
    )
    adaptive_checkpoints = (*clarification.checkpoints, *continuation.checkpoints)
    adaptive_events = (
        *_lifecycle_events(clarification, generation=2),
        *_lifecycle_events(continuation, generation=3),
    )

    return {
        "v": FIXTURE_VERSION,
        "fixtureId": FIXTURE_ID,
        "compilerVersion": COMPLETING_SQUARE_COMPILER_VERSION,
        "generation": FIXTURE_GENERATION,
        "attempt": FIXTURE_ATTEMPT,
        "baseRevision": compiled.base_scene.revision,
        "resultRevision": compiled.result_scene.revision,
        "transcript": {
            "prompt": FIXTURE_PROMPT,
            "routeDecision": decision.model_dump(mode="json", by_alias=True),
            "routedBeat": compiled.beat.model_dump(mode="json", by_alias=True),
            "checkpointIds": checkpoint_ids,
            "checkpointCount": len(checkpoint_events),
            "authoredDurationMs": sum(
                event.semantic.choreography.phase.total_ms for event in checkpoint_events
            ),
            "providerRequestCount": 0,
        },
        "events": [_wire_event(event) for event in lifecycle_events],
        "adaptiveTranscript": {
            "baseCheckpointId": "missing_corner",
            "clarificationDecision": clarification_decision.model_dump(
                mode="json",
                by_alias=True,
            ),
            "continuationDecision": continuation_decision.model_dump(
                mode="json",
                by_alias=True,
            ),
            "checkpointIds": [
                checkpoint.checkpoint_id.value for checkpoint in adaptive_checkpoints
            ],
            "authoredDurationMs": sum(
                checkpoint.choreography.phase.total_ms for checkpoint in adaptive_checkpoints
            ),
            "events": [_wire_event(event) for event in adaptive_events],
        },
    }


def render_live_choreography_fixture() -> bytes:
    """Render stable UTF-8 pretty JSON with one trailing newline."""

    payload: dict[str, Any] = build_live_choreography_fixture()
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_live_choreography_fixture(output_path: Path) -> None:
    """Write the canonical fixture bytes to an explicit path."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(render_live_choreography_fixture())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path for the generated pretty-JSON fixture.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    write_live_choreography_fixture(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
