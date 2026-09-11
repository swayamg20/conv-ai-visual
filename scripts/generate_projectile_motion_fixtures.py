"""Generate provider-free Gate 1.7 projectile-motion fixtures."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from murmur.live_scene.contracts import (
    PutSceneOperation,
    RemoveSceneOperation,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
    CompiledProjectileMotionCheckpointV1,
)
from murmur.live_scene.projectile_motion_compiler import (
    ProjectileMotionCheckpointBlueprint,
    compile_projectile_motion_checkpoint_blueprints,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_GRAVITY_MPS2,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionClarificationTopic,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStage,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
)
from murmur.live_scene.projectile_motion_requests import PROJECTILE_CHOREOGRAPHY_PROTOCOL
from murmur.live_scene.projectile_motion_service_contracts import (
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileCheckpointSemanticMetadataV1,
    ProjectileChoreographySceneCheckpointEventV1,
    ProjectileChoreographySceneStreamEventV1,
    dump_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.projectile_motion_wire import (
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState

FIXTURE_FORMAT_VERSION: Final = 1
FIXTURE_ATTEMPT: Final = 1
COMPONENT_ID: Final = "projectile"
PRIMARY_PROBLEM: Final = (20, 45)
RETARGET_PROBLEM: Final = (20, 60)
APEX_PREFIX_LENGTH: Final = 4
OUTPUT_FILENAMES: Final = {
    (20, 30): "projectile-motion-v20-a30.v1.json",
    PRIMARY_PROBLEM: "projectile-motion-v20-a45.v1.json",
    (20, 60): "projectile-motion-v20-a60.v1.json",
    (30, 45): "projectile-motion-v30-a45.v1.json",
    (30, 60): "projectile-motion-v30-a60.v1.json",
}

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_SEALED_VISUAL_FIXTURE_DIRECTORY: Final = (
    _REPOSITORY_ROOT / "web" / "src" / "features" / "live-scene" / "fixtures"
)
_GATE_17_VISUAL_FIXTURE_DIRECTORY: Final = _SEALED_VISUAL_FIXTURE_DIRECTORY / "projectile-motion-v1"


@dataclass(frozen=True, slots=True)
class _Lane:
    scenario_id: str
    generation: int
    beat: RoutedProjectileMotionBeatV1
    base_scene: SceneState
    base_semantic_scene: SemanticSceneState
    events: tuple[ProjectileChoreographySceneStreamEventV1, ...]
    checkpoints: tuple[ProjectileChoreographySceneCheckpointEventV1, ...]
    result_scene: SceneState
    result_semantic_scene: SemanticSceneState


def _problem(speed_mps: int, angle_deg: int) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1(speedMps=speed_mps, angleDeg=angle_deg)


def _component(scene: SemanticSceneState) -> ProjectileMotionStateV1 | None:
    if not scene.components:
        return None
    if len(scene.components) != 1 or not isinstance(scene.components[0], ProjectileMotionStateV1):
        raise RuntimeError("projectile fixture frontier must contain one projectile component")
    return scene.components[0]


def _apply_patch(scene: SceneState, checkpoint: CompiledProjectileMotionCheckpointV1) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        elif isinstance(operation, RemoveSceneOperation):
            if operation.id not in nodes:
                raise RuntimeError("projectile fixture checkpoint removes an absent node")
            del nodes[operation.id]
            order.remove(operation.id)
        else:
            raise RuntimeError("projectile fixture checkpoint contains an unknown operation")
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _advance_semantic_scene(
    scene: SemanticSceneState,
    result: ProjectileMotionStateV1,
    certificate_sha256: str,
) -> SemanticSceneState:
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=(result,),
        certificate_head_sha256=certificate_sha256,
    )


def _roundtrip_event(
    event: ProjectileChoreographySceneStreamEventV1,
) -> ProjectileChoreographySceneStreamEventV1:
    payload = dump_projectile_choreography_scene_stream_event(event)
    decoded = PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)
    if decoded != event:
        raise RuntimeError("projectile fixture event changed during independent decoding")
    encode_projectile_choreography_scene_stream_event(decoded)
    return decoded


def _checkpoint_event(
    *,
    generation: int,
    sequence: int,
    checkpoint: CompiledProjectileMotionCheckpointV1,
    blueprint: ProjectileMotionCheckpointBlueprint,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> tuple[ProjectileChoreographySceneCheckpointEventV1, SceneState, SemanticSceneState]:
    base_component = _component(base_semantic_scene)
    if base_component is not None and base_component != blueprint.base_component:
        raise RuntimeError("projectile fixture blueprint changed its accepted component")
    result_scene = _apply_patch(base_scene, checkpoint)
    result_semantic_scene = _advance_semantic_scene(
        base_semantic_scene,
        blueprint.result_component,
        checkpoint.certificate.certificate_sha256,
    )
    event = ProjectileChoreographySceneCheckpointEventV1(
        generation=generation,
        attempt=FIXTURE_ATTEMPT,
        sequence=sequence,
        base_revision=base_scene.revision,
        result_revision=result_scene.revision,
        patch=checkpoint.patch,
        semantic=ProjectileCheckpointSemanticMetadataV1(
            base_problem_spec=(base_component.problem_spec if base_component is not None else None),
            result_problem_spec=blueprint.result_component.problem_spec,
            beat=checkpoint.beat,
            action=checkpoint.action,
            checkpoint_id=checkpoint.checkpoint_id,
            clarification_topic=checkpoint.clarification_topic,
            base_component=base_component,
            result_component=blueprint.result_component,
            semantic_base_revision=base_semantic_scene.revision,
            semantic_result_revision=result_semantic_scene.revision,
            semantic_base_certificate_sha256=(base_semantic_scene.certificate_head_sha256),
            semantic_result_certificate_sha256=(checkpoint.certificate.certificate_sha256),
            receipt=checkpoint.receipt,
            presentation=checkpoint.presentation,
            choreography=checkpoint.choreography,
            certificate=checkpoint.certificate,
        ),
    )
    return (
        PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            dump_projectile_choreography_scene_stream_event(event)
        ),
        result_scene,
        result_semantic_scene,
    )


def _compile_lane(
    *,
    scenario_id: str,
    generation: int,
    beat: RoutedProjectileMotionBeatV1,
    base_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
) -> _Lane:
    compiled = compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    blueprints = compile_projectile_motion_checkpoint_blueprints(
        beat,
        _component(base_semantic_scene),
    )
    if len(compiled.checkpoints) != len(blueprints.checkpoints):
        raise RuntimeError("projectile fixture compiler boundaries disagree")

    scene = base_scene
    semantic_scene = base_semantic_scene
    checkpoint_events: list[ProjectileChoreographySceneCheckpointEventV1] = []
    for sequence, (checkpoint, blueprint) in enumerate(
        zip(compiled.checkpoints, blueprints.checkpoints, strict=True),
        start=1,
    ):
        event, scene, semantic_scene = _checkpoint_event(
            generation=generation,
            sequence=sequence,
            checkpoint=checkpoint,
            blueprint=blueprint,
            base_scene=scene,
            base_semantic_scene=semantic_scene,
        )
        if not isinstance(event, ProjectileChoreographySceneCheckpointEventV1):
            raise RuntimeError("projectile fixture checkpoint changed event type")
        checkpoint_events.append(event)

    if scene != compiled.result_scene or semantic_scene != compiled.result_semantic_scene:
        raise RuntimeError("projectile fixture materialization changed compiler terminal state")
    started = SceneStreamStartedEvent(
        generation=generation,
        attempt=FIXTURE_ATTEMPT,
        base_revision=base_scene.revision,
    )
    completed = SceneStreamCompletedEvent(
        generation=generation,
        final_revision=scene.revision,
        patch_count=len(checkpoint_events),
        first_patch_ms=0.0,
        total_ms=0.0,
        repaired=False,
    )
    events = tuple(_roundtrip_event(event) for event in (started, *checkpoint_events, completed))
    return _Lane(
        scenario_id=scenario_id,
        generation=generation,
        beat=beat,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
        events=events,
        checkpoints=tuple(checkpoint_events),
        result_scene=scene,
        result_semantic_scene=semantic_scene,
    )


def _frontier_summary(scene: SemanticSceneState) -> dict[str, object]:
    component = _component(scene)
    return {
        "revision": scene.revision,
        "problemSpec": (
            component.problem_spec.model_dump(mode="json", by_alias=True)
            if component is not None
            else None
        ),
        "lastMainCheckpoint": (
            component.last_main_checkpoint.value
            if component is not None and component.last_main_checkpoint is not None
            else None
        ),
        "clarifiedTopics": (
            [topic.value for topic in component.clarified_topics] if component is not None else []
        ),
        "activeClarification": (
            component.active_clarification.value
            if component is not None and component.active_clarification is not None
            else None
        ),
        "certificateHeadSha256": scene.certificate_head_sha256,
    }


def _dump_lane(lane: _Lane) -> dict[str, object]:
    terminal_head = lane.result_semantic_scene.certificate_head_sha256
    if terminal_head is None:
        raise RuntimeError("projectile fixture lane has no terminal certificate")
    request_problem = lane.beat.base_problem_spec or lane.beat.result_problem_spec
    return {
        "scenarioId": lane.scenario_id,
        "generation": lane.generation,
        "problemSpec": request_problem.model_dump(mode="json", by_alias=True),
        "frontier": _frontier_summary(lane.base_semantic_scene),
        "route": lane.beat.route.model_dump(mode="json", by_alias=True),
        "baseScene": lane.base_scene.model_dump(mode="json", by_alias=True),
        "baseSemanticScene": lane.base_semantic_scene.model_dump(mode="json", by_alias=True),
        "checkpointIds": [event.semantic.checkpoint_id.value for event in lane.checkpoints],
        "checkpointCount": len(lane.checkpoints),
        "events": [dump_projectile_choreography_scene_stream_event(event) for event in lane.events],
        "expectedTerminal": {
            "scene": lane.result_scene.model_dump(mode="json", by_alias=True),
            "semanticScene": lane.result_semantic_scene.model_dump(mode="json", by_alias=True),
            "frontier": _frontier_summary(lane.result_semantic_scene),
            "certificateSha256": terminal_head,
        },
    }


def _prefix_frontier(lane: _Lane, count: int) -> tuple[SceneState, SemanticSceneState]:
    scene = lane.base_scene
    semantic_scene = lane.base_semantic_scene
    for checkpoint in lane.checkpoints[:count]:
        compiled = CompiledProjectileMotionCheckpointV1(
            beat=checkpoint.semantic.beat,
            action=checkpoint.semantic.action,
            checkpoint_id=checkpoint.semantic.checkpoint_id,
            clarification_topic=checkpoint.semantic.clarification_topic,
            patch=checkpoint.patch,
            receipt=checkpoint.semantic.receipt,
            presentation=checkpoint.semantic.presentation,
            choreography=checkpoint.semantic.choreography,
            certificate=checkpoint.semantic.certificate,
        )
        scene = _apply_patch(scene, compiled)
        semantic_scene = _advance_semantic_scene(
            semantic_scene,
            checkpoint.semantic.result_component,
            checkpoint.semantic.semantic_result_certificate_sha256,
        )
    return scene, semantic_scene


def _advance_beat(problem: ProjectileMotionProblemSpecV1) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beatId=f"fixture_{problem.speed_mps}_{problem.angle_deg}_solve",
        componentId=COMPONENT_ID,
        baseProblemSpec=None,
        resultProblemSpec=problem,
        route=AdvanceProjectileMotionRouteV1(targetStage=ProjectileMotionStage.SOLVE),
    )


def _continue_beat(problem: ProjectileMotionProblemSpecV1) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beatId=f"fixture_{problem.speed_mps}_{problem.angle_deg}_continue",
        componentId=COMPONENT_ID,
        baseProblemSpec=problem,
        resultProblemSpec=problem,
        route=AdvanceProjectileMotionRouteV1(targetStage=ProjectileMotionStage.SOLVE),
    )


def _retarget_beat(
    *,
    beat_id: str,
    base: ProjectileMotionProblemSpecV1,
    target: ProjectileMotionProblemSpecV1,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beatId=beat_id,
        componentId=COMPONENT_ID,
        baseProblemSpec=base,
        resultProblemSpec=target,
        route=RetargetProjectileMotionRouteV1(targetProblemSpec=target),
    )


def _physics(problem: ProjectileMotionProblemSpecV1) -> dict[str, float]:
    return {
        "gravityMps2": PROJECTILE_MOTION_GRAVITY_MPS2,
        "initialHorizontalVelocityMps": round(problem.initial_horizontal_velocity_mps, 12),
        "initialVerticalVelocityMps": round(problem.initial_vertical_velocity_mps, 12),
        "apexTimeSeconds": round(problem.ascent_time_seconds, 12),
        "flightTimeSeconds": round(problem.flight_time_seconds, 12),
        "maximumHeightM": round(problem.maximum_height_m, 12),
        "rangeM": round(problem.range_m, 12),
    }


def _coverage(problem: ProjectileMotionProblemSpecV1) -> dict[str, object]:
    partner_angle = {30: 60, 60: 30}.get(problem.angle_deg)
    return {
        "complementaryRangePartner": (
            _problem(problem.speed_mps, partner_angle).model_dump(mode="json", by_alias=True)
            if partner_angle is not None
            else None
        ),
        "isQualifiedMaximumRange": (problem.speed_mps == 30 and problem.angle_deg == 45),
        "isQualifiedMaximumHeight": (problem.speed_mps == 30 and problem.angle_deg == 60),
    }


def _build_fixture(speed_mps: int, angle_deg: int) -> dict[str, object]:
    problem = _problem(speed_mps, angle_deg)
    empty_scene = SceneState(revision=0)
    empty_semantic_scene = SemanticSceneState(revision=0)
    main = _compile_lane(
        scenario_id="main_solve",
        generation=1,
        beat=_advance_beat(problem),
        base_scene=empty_scene,
        base_semantic_scene=empty_semantic_scene,
    )
    lanes: dict[str, object] = {"main": _dump_lane(main)}

    if (speed_mps, angle_deg) == PRIMARY_PROBLEM:
        apex_scene, apex_semantic_scene = _prefix_frontier(main, APEX_PREFIX_LENGTH)
        apex_component = _component(apex_semantic_scene)
        if apex_component is None:
            raise RuntimeError("projectile apex branch has no accepted component")
        clarify_beat = RoutedProjectileMotionBeatV1(
            beatId="fixture_20_45_clarify_apex",
            componentId=COMPONENT_ID,
            baseProblemSpec=problem,
            resultProblemSpec=problem,
            route=ClarifyProjectileMotionRouteV1(
                topic=ProjectileMotionClarificationTopic.APEX_ACCELERATION
            ),
        )
        clarification = _compile_lane(
            scenario_id="apex_acceleration_clarification",
            generation=2,
            beat=clarify_beat,
            base_scene=apex_scene,
            base_semantic_scene=apex_semantic_scene,
        )
        lanes["clarifyApex"] = _dump_lane(clarification)

        continuation = _compile_lane(
            scenario_id="continue_after_apex_clarification",
            generation=3,
            beat=_continue_beat(problem),
            base_scene=clarification.result_scene,
            base_semantic_scene=clarification.result_semantic_scene,
        )
        lanes["continueAfterClarification"] = _dump_lane(continuation)

        target = _problem(*RETARGET_PROBLEM)
        retarget_at_apex = _compile_lane(
            scenario_id="retarget_20_45_to_20_60_at_apex",
            generation=3,
            beat=_retarget_beat(
                beat_id="fixture_20_45_to_20_60_at_apex",
                base=problem,
                target=target,
            ),
            base_scene=clarification.result_scene,
            base_semantic_scene=clarification.result_semantic_scene,
        )
        lanes["retargetAtApex"] = _dump_lane(retarget_at_apex)

        retarget_after_summary = _compile_lane(
            scenario_id="retarget_20_45_to_20_60_after_summary",
            generation=4,
            beat=_retarget_beat(
                beat_id="fixture_20_45_to_20_60_after_summary",
                base=problem,
                target=target,
            ),
            base_scene=continuation.result_scene,
            base_semantic_scene=continuation.result_semantic_scene,
        )
        lanes["retargetAfterSummary"] = _dump_lane(retarget_after_summary)

    fixture_id = f"projectile-motion-v{speed_mps}-a{angle_deg}"
    return {
        "v": FIXTURE_FORMAT_VERSION,
        "fixtureId": fixture_id,
        "protocol": PROJECTILE_CHOREOGRAPHY_PROTOCOL,
        "compilerVersion": PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
        "scenario": "qualified_projectile_motion",
        "problemSpec": problem.model_dump(mode="json", by_alias=True),
        "expectedPhysics": _physics(problem),
        "coverage": _coverage(problem),
        "providerRequestCount": 0,
        "lanes": lanes,
    }


def render_projectile_motion_fixtures() -> dict[str, bytes]:
    """Return stable compact fixture bytes without filesystem or provider I/O."""

    return {
        filename: (
            json.dumps(
                _build_fixture(speed_mps, angle_deg),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        for (speed_mps, angle_deg), filename in OUTPUT_FILENAMES.items()
    }


def write_projectile_motion_fixtures(output_directory: Path) -> None:
    """Write only the exact Gate 1.7 fixture set beneath an explicit directory."""

    resolved_output = output_directory.resolve()
    sealed_directory = _SEALED_VISUAL_FIXTURE_DIRECTORY.resolve()
    if resolved_output == sealed_directory or (
        sealed_directory in resolved_output.parents
        and resolved_output != _GATE_17_VISUAL_FIXTURE_DIRECTORY.resolve()
    ):
        raise ValueError("Gate 1.7 generation may not target sealed Gate 1.5/1.6 fixtures")
    output_directory.mkdir(parents=True, exist_ok=True)
    for filename, payload in render_projectile_motion_fixtures().items():
        output_directory.joinpath(filename).write_bytes(payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory",
        type=Path,
        required=True,
        help="Directory for the five generated compact JSON fixtures.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    write_projectile_motion_fixtures(args.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
