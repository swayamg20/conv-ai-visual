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
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    StartChoreographyDecision,
)
from murmur.live_scene.visual_act_lowering import lower_resolved_choreography_act
from murmur.live_scene.visual_act_router import ResolvedChoreographyAct, resolve_visual_act

FIXTURE_VERSION = 1
FIXTURE_ID = "completing-the-square"
FIXTURE_GENERATION = 1
FIXTURE_ATTEMPT = 1
FIXTURE_PROMPT = "Solve x² + 6x = 7 visually by completing the square."


def _compile_fixture() -> tuple[StartChoreographyDecision, CompiledCheckpointBeatV2]:
    base_scene = SceneState(revision=0)
    base_semantic_scene = SemanticSceneState(revision=0)
    decision = StartChoreographyDecision(
        component_kind="completing_square",
        target_stage="solve",
    )
    resolved = resolve_visual_act(decision, base_semantic_scene)
    if not isinstance(resolved, ResolvedChoreographyAct):
        raise RuntimeError("fixture route did not resolve to completing-square choreography")

    beat = lower_resolved_choreography_act(resolved, generation=FIXTURE_GENERATION)
    compiled = compile_checkpoint_beat(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    expected_ids = tuple(checkpoint.value for checkpoint in resolved.missing_checkpoints)
    actual_ids = tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints)
    if actual_ids != expected_ids:
        raise RuntimeError("compiler output did not match the routed checkpoint suffix")
    return decision, compiled


def _checkpoint_events(
    compiled: CompiledCheckpointBeatV2,
) -> tuple[ChoreographySceneCheckpointEvent, ...]:
    events: list[ChoreographySceneCheckpointEvent] = []
    expected_revision = compiled.base_semantic_scene.revision
    expected_certificate = compiled.base_semantic_scene.certificate_head_sha256

    for sequence, checkpoint in enumerate(compiled.checkpoints, start=1):
        certificate_body = checkpoint.certificate.body
        if (
            certificate_body.base_revision != expected_revision
            or certificate_body.previous_certificate_sha256 != expected_certificate
        ):
            raise RuntimeError("compiler output did not form a continuous certificate chain")

        result_component = CompletingSquareState(
            id=checkpoint.beat.component_id,
            last_main_checkpoint=CompletingSquareMainCheckpoint(checkpoint.checkpoint_id.value),
            corner_clarified=False,
        )
        event = ChoreographySceneCheckpointEvent(
            generation=FIXTURE_GENERATION,
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
    ):
        raise RuntimeError("compiler output did not reach its declared semantic result")
    return tuple(events)


def _wire_event(event: ChoreographySceneStreamEvent) -> dict[str, object]:
    # Encoding is also the fixture's per-frame wire-budget preflight.
    encode_choreography_scene_stream_event(event)
    return dump_choreography_scene_stream_event(event)


def build_live_choreography_fixture() -> dict[str, object]:
    """Return the deterministic fixture payload without filesystem or provider I/O."""

    decision, compiled = _compile_fixture()
    checkpoint_events = _checkpoint_events(compiled)
    lifecycle_events: tuple[ChoreographySceneStreamEvent, ...] = (
        SceneStreamStartedEvent(
            generation=FIXTURE_GENERATION,
            attempt=FIXTURE_ATTEMPT,
            base_revision=compiled.base_scene.revision,
        ),
        *checkpoint_events,
        SceneStreamCompletedEvent(
            generation=FIXTURE_GENERATION,
            final_revision=compiled.result_scene.revision,
            patch_count=len(checkpoint_events),
            first_patch_ms=0.0,
            total_ms=0.0,
            repaired=False,
        ),
    )
    checkpoint_ids = [event.semantic.checkpoint_id.value for event in checkpoint_events]

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
