"""Determinism and independent verification for Gate 1.7 browser fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import choreography_plan_v2_sha256
from murmur.live_scene.contracts import (
    PutSceneOperation,
    RemoveSceneOperation,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    CompiledProjectileMotionCheckpointBeatV1,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
    CompiledProjectileMotionCheckpointV1,
    projectile_motion_checkpoint_certificate_sha256,
    projectile_motion_checkpoint_receipt_sha256,
)
from murmur.live_scene.projectile_motion_compiler import (
    compile_projectile_motion_checkpoint_blueprints,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_GRAVITY_MPS2,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStateV1,
    projectile_motion_problem_sha256,
    routed_projectile_motion_beat_sha256,
)
from murmur.live_scene.projectile_motion_requests import PROJECTILE_CHOREOGRAPHY_PROTOCOL
from murmur.live_scene.projectile_motion_service_contracts import (
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileChoreographySceneCheckpointEventV1,
)
from murmur.live_scene.projectile_motion_verifier import (
    verify_projectile_motion_checkpoint,
    verify_projectile_motion_frontier,
)
from murmur.live_scene.projectile_motion_wire import (
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)

_REPOSITORY_ROOT = Path(__file__).parents[1]
_GENERATOR_PATH = _REPOSITORY_ROOT / "scripts" / "generate_projectile_motion_fixtures.py"
_FIXTURE_DIRECTORY = (
    _REPOSITORY_ROOT
    / "web"
    / "src"
    / "features"
    / "live-scene"
    / "fixtures"
    / "projectile-motion-v1"
)
_SEALED_FIXTURES = {
    "completing-square-parametric-b16-c17.v3.json": (
        "8e582ca138ed0976550208c452451a3cd9cd59399bd6b7ede070c7820aa1f7a4"
    ),
    "completing-square-parametric-b2-c80.v3.json": (
        "963653acddb638ddef66297b7597a59301791eef4e51a9f5a2fb2e6bbc9868db"
    ),
    "completing-square-parametric-b8-c20.v3.json": (
        "8c6271c14ce49909db7a46cd01e8c07563121dacefd9b057ae869bc36f0e9538"
    ),
    "completing-the-square.v1.json": (
        "1df951328558d537347625419f9ccf16b454d06f45bd108fc79e86f89ee98887"
    ),
    "pythagorean-area-identity.v1.json": (
        "8e39a004d0e71a88d7a17e0c8db81a8c44aa07f3bd5e406909243982af132bfd"
    ),
}
_SEALED_FIXTURE_DIRECTORY = (
    _REPOSITORY_ROOT / "web" / "src" / "features" / "live-scene" / "fixtures"
)
_CASES = (
    (20, 30, "projectile-motion-v20-a30.v1.json"),
    (20, 45, "projectile-motion-v20-a45.v1.json"),
    (20, 60, "projectile-motion-v20-a60.v1.json"),
    (30, 45, "projectile-motion-v30-a45.v1.json"),
    (30, 60, "projectile-motion-v30-a60.v1.json"),
)
_MAIN_CHECKPOINT_IDS = [checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER]


@dataclass(frozen=True, slots=True)
class _DecodedLane:
    checkpoints: tuple[ProjectileChoreographySceneCheckpointEventV1, ...]
    base_scene: SceneState
    base_semantic_scene: SemanticSceneState
    result_scene: SceneState
    result_semantic_scene: SemanticSceneState


def _fixture(filename: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_FIXTURE_DIRECTORY.joinpath(filename).read_bytes()))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_camel_case_keys(value: object) -> None:
    if isinstance(value, dict):
        assert all("_" not in key for key in value)
        for child in value.values():
            _assert_camel_case_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_camel_case_keys(child)


def _assert_sealed_fixtures() -> None:
    assert {path.name for path in _SEALED_FIXTURE_DIRECTORY.iterdir() if path.is_file()} == set(
        _SEALED_FIXTURES
    )
    for filename, expected in _SEALED_FIXTURES.items():
        assert _digest(_SEALED_FIXTURE_DIRECTORY / filename) == expected


def _frontier_summary(scene: SemanticSceneState) -> dict[str, object]:
    component = scene.components[0] if scene.components else None
    assert component is None or isinstance(component, ProjectileMotionStateV1)
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


def _apply_patch(
    scene: SceneState,
    checkpoint: ProjectileChoreographySceneCheckpointEventV1,
) -> SceneState:
    order = [node.id for node in scene.nodes]
    nodes = {node.id: node for node in scene.nodes}
    for operation in checkpoint.patch.operations:
        if isinstance(operation, PutSceneOperation):
            if operation.node.id not in nodes:
                order.append(operation.node.id)
            nodes[operation.node.id] = operation.node
        elif isinstance(operation, RemoveSceneOperation):
            assert operation.id in nodes
            del nodes[operation.id]
            order.remove(operation.id)
        else:
            raise AssertionError("unknown fixture patch operation")
    return SceneState(
        revision=scene.revision + 1,
        nodes=tuple(nodes[node_id] for node_id in order),
    )


def _advance_semantic(
    scene: SemanticSceneState,
    checkpoint: ProjectileChoreographySceneCheckpointEventV1,
) -> SemanticSceneState:
    return SemanticSceneState(
        revision=scene.revision + 1,
        components=(checkpoint.semantic.result_component,),
        certificate_head_sha256=checkpoint.semantic.semantic_result_certificate_sha256,
    )


def _compiled_checkpoint(
    checkpoint: ProjectileChoreographySceneCheckpointEventV1,
) -> CompiledProjectileMotionCheckpointV1:
    semantic = checkpoint.semantic
    return CompiledProjectileMotionCheckpointV1(
        beat=semantic.beat,
        action=semantic.action,
        checkpoint_id=semantic.checkpoint_id,
        clarification_topic=semantic.clarification_topic,
        patch=checkpoint.patch,
        receipt=semantic.receipt,
        presentation=semantic.presentation,
        choreography=semantic.choreography,
        certificate=semantic.certificate,
    )


def _assert_checkpoint_integrity(
    checkpoint: ProjectileChoreographySceneCheckpointEventV1,
    *,
    base_scene: SceneState,
    result_scene: SceneState,
    base_semantic_scene: SemanticSceneState,
    result_semantic_scene: SemanticSceneState,
) -> None:
    semantic = checkpoint.semantic
    body = semantic.certificate.body
    assert body.routed_beat_sha256 == routed_projectile_motion_beat_sha256(semantic.beat)
    assert body.base_low_level_scene_sha256 == low_level_scene_sha256(base_scene)
    assert body.result_low_level_scene_sha256 == low_level_scene_sha256(result_scene)
    assert body.base_semantic_scene_sha256 == semantic_scene_sha256(base_semantic_scene)
    assert body.result_semantic_scene_sha256 == semantic_scene_sha256(result_semantic_scene)
    assert body.patch_sha256 == scene_patch_sha256(checkpoint.patch)
    assert body.receipt_sha256 == projectile_motion_checkpoint_receipt_sha256(semantic.receipt)
    assert body.choreography_sha256 == choreography_plan_v2_sha256(semantic.choreography)
    assert body.previous_certificate_sha256 == base_semantic_scene.certificate_head_sha256
    assert semantic.semantic_base_certificate_sha256 == (
        base_semantic_scene.certificate_head_sha256
    )
    assert semantic.semantic_result_certificate_sha256 == (semantic.certificate.certificate_sha256)
    assert semantic.certificate.certificate_sha256 == (
        projectile_motion_checkpoint_certificate_sha256(body)
    )
    expected_base_problem = (
        semantic.base_component.problem_spec if semantic.base_component is not None else None
    )
    assert semantic.base_problem_spec == expected_base_problem
    assert semantic.result_problem_spec == semantic.result_component.problem_spec
    assert semantic.receipt.base_problem_spec_sha256 == (
        projectile_motion_problem_sha256(expected_base_problem)
        if expected_base_problem is not None
        else None
    )
    assert semantic.receipt.result_problem_spec_sha256 == (
        projectile_motion_problem_sha256(semantic.result_problem_spec)
    )


def _decode_lane(lane: dict[str, Any]) -> _DecodedLane:
    assert set(lane) == {
        "scenarioId",
        "generation",
        "problemSpec",
        "frontier",
        "route",
        "baseScene",
        "baseSemanticScene",
        "checkpointIds",
        "checkpointCount",
        "events",
        "expectedTerminal",
    }
    generation = lane["generation"]
    base_scene = SceneState.model_validate(lane["baseScene"])
    base_semantic_scene = SemanticSceneState.model_validate(lane["baseSemanticScene"])
    assert lane["frontier"] == _frontier_summary(base_semantic_scene)
    request_problem = ProjectileMotionProblemSpecV1.model_validate(lane["problemSpec"])
    accepted = base_semantic_scene.components[0] if base_semantic_scene.components else None
    if accepted is not None:
        assert isinstance(accepted, ProjectileMotionStateV1)
        assert request_problem == accepted.problem_spec

    events = tuple(
        PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
        for event in lane["events"]
    )
    assert isinstance(events[0], SceneStreamStartedEvent)
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    checkpoints = tuple(
        event for event in events if isinstance(event, ProjectileChoreographySceneCheckpointEventV1)
    )
    assert [event.type for event in events] == [
        "scene_stream_started",
        *("projectile_choreography_scene_checkpoint" for _ in checkpoints),
        "scene_stream_completed",
    ]
    assert lane["checkpointIds"] == [
        checkpoint.semantic.checkpoint_id.value for checkpoint in checkpoints
    ]
    assert lane["checkpointCount"] == len(checkpoints)
    assert [checkpoint.sequence for checkpoint in checkpoints] == list(
        range(1, len(checkpoints) + 1)
    )
    assert events[0].generation == generation
    assert events[0].attempt == 1
    assert events[0].base_revision == base_scene.revision

    blueprints = compile_projectile_motion_checkpoint_blueprints(
        checkpoints[0].semantic.beat,
        cast(ProjectileMotionStateV1 | None, accepted),
    )
    assert len(blueprints.checkpoints) == len(checkpoints)
    scene = base_scene
    semantic_scene = base_semantic_scene
    compiled: list[CompiledProjectileMotionCheckpointV1] = []
    for offset, (checkpoint, blueprint) in enumerate(
        zip(checkpoints, blueprints.checkpoints, strict=True)
    ):
        assert checkpoint.generation == generation
        assert checkpoint.attempt == 1
        assert checkpoint.base_revision == base_scene.revision + offset
        assert checkpoint.result_revision == base_scene.revision + offset + 1
        assert checkpoint.semantic.semantic_base_revision == semantic_scene.revision
        assert checkpoint.semantic.base_component == (
            semantic_scene.components[0] if semantic_scene.components else None
        )
        assert checkpoint.semantic.checkpoint_id == blueprint.checkpoint_id
        assert checkpoint.patch == blueprint.patch
        assert checkpoint.semantic.result_component == blueprint.result_component
        verify_projectile_motion_checkpoint(blueprint)

        next_scene = _apply_patch(scene, checkpoint)
        next_semantic_scene = _advance_semantic(semantic_scene, checkpoint)
        _assert_checkpoint_integrity(
            checkpoint,
            base_scene=scene,
            result_scene=next_scene,
            base_semantic_scene=semantic_scene,
            result_semantic_scene=next_semantic_scene,
        )
        compiled.append(_compiled_checkpoint(checkpoint))
        scene = next_scene
        semantic_scene = next_semantic_scene

    terminal = lane["expectedTerminal"]
    assert set(terminal) == {
        "scene",
        "semanticScene",
        "frontier",
        "certificateSha256",
    }
    result_scene = SceneState.model_validate(terminal["scene"])
    result_semantic_scene = SemanticSceneState.model_validate(terminal["semanticScene"])
    assert scene == result_scene
    assert semantic_scene == result_semantic_scene
    assert terminal["frontier"] == _frontier_summary(result_semantic_scene)
    assert terminal["certificateSha256"] == result_semantic_scene.certificate_head_sha256

    batch = CompiledProjectileMotionCheckpointBeatV1(
        beat=checkpoints[0].semantic.beat,
        base_scene=base_scene,
        result_scene=result_scene,
        base_semantic_scene=base_semantic_scene,
        result_semantic_scene=result_semantic_scene,
        checkpoints=tuple(compiled),
    )
    assert batch.result_scene == result_scene
    assert batch.result_semantic_scene == result_semantic_scene
    result_component = result_semantic_scene.components[0]
    assert isinstance(result_component, ProjectileMotionStateV1)
    verify_projectile_motion_frontier(result_component, result_scene)

    completed = cast(SceneStreamCompletedEvent, events[-1])
    assert completed.model_dump(mode="json", by_alias=True) == {
        "type": "scene_stream_completed",
        "generation": generation,
        "finalRevision": result_scene.revision,
        "patchCount": len(checkpoints),
        "firstPatchMs": 0.0,
        "totalMs": 0.0,
        "repaired": False,
    }
    for event in events:
        encode_projectile_choreography_scene_stream_event(event)
    return _DecodedLane(
        checkpoints=checkpoints,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
        result_scene=result_scene,
        result_semantic_scene=result_semantic_scene,
    )


def _materialize_prefix(
    lane: _DecodedLane,
    count: int,
) -> tuple[SceneState, SemanticSceneState]:
    scene = lane.base_scene
    semantic_scene = lane.base_semantic_scene
    for checkpoint in lane.checkpoints[:count]:
        scene = _apply_patch(scene, checkpoint)
        semantic_scene = _advance_semantic(semantic_scene, checkpoint)
    return scene, semantic_scene


def test_checked_in_fixtures_are_two_byte_identical_regenerations(
    tmp_path: Path,
) -> None:
    _assert_sealed_fixtures()
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output_directory in (first, second):
        subprocess.run(
            [
                sys.executable,
                str(_GENERATOR_PATH),
                "--output-directory",
                str(output_directory),
            ],
            cwd=_REPOSITORY_ROOT,
            check=True,
        )

    expected_names = {filename for _, _, filename in _CASES}
    assert {path.name for path in first.iterdir()} == expected_names
    assert {path.name for path in second.iterdir()} == expected_names
    assert {path.name for path in _FIXTURE_DIRECTORY.iterdir()} == expected_names
    for filename in expected_names:
        checked_in = _FIXTURE_DIRECTORY.joinpath(filename).read_bytes()
        assert first.joinpath(filename).read_bytes() == checked_in
        assert second.joinpath(filename).read_bytes() == checked_in
    _assert_sealed_fixtures()


def test_generator_refuses_the_sealed_gate_15_and_16_directory() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_GENERATOR_PATH),
            "--output-directory",
            str(_SEALED_FIXTURE_DIRECTORY),
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "may not target sealed Gate 1.5/1.6 fixtures" in result.stderr
    _assert_sealed_fixtures()


@pytest.mark.parametrize("speed_mps,angle_deg,filename", _CASES)
def test_each_fixture_is_a_real_six_checkpoint_compiler_lifecycle(
    speed_mps: int,
    angle_deg: int,
    filename: str,
) -> None:
    fixture = _fixture(filename)
    problem = ProjectileMotionProblemSpecV1(speedMps=speed_mps, angleDeg=angle_deg)
    assert set(fixture) == {
        "v",
        "fixtureId",
        "protocol",
        "compilerVersion",
        "scenario",
        "problemSpec",
        "expectedPhysics",
        "coverage",
        "providerRequestCount",
        "lanes",
    }
    assert fixture["v"] == 1
    assert fixture["fixtureId"] == f"projectile-motion-v{speed_mps}-a{angle_deg}"
    assert fixture["protocol"] == PROJECTILE_CHOREOGRAPHY_PROTOCOL
    assert fixture["compilerVersion"] == PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION
    assert fixture["scenario"] == "qualified_projectile_motion"
    assert fixture["problemSpec"] == problem.model_dump(mode="json", by_alias=True)
    assert fixture["providerRequestCount"] == 0
    _assert_camel_case_keys(fixture)
    assert set(fixture["lanes"]) == (
        {
            "main",
            "clarifyHorizontal",
            "clarifyApex",
            "clarifySymmetry",
            "continueAfterClarification",
            "retargetAtApex",
            "retargetAfterSummary",
        }
        if (speed_mps, angle_deg) == (20, 45)
        else {"main"}
    )

    main = fixture["lanes"]["main"]
    assert main["scenarioId"] == "main_solve"
    assert main["problemSpec"] == fixture["problemSpec"]
    assert main["frontier"] == {
        "revision": 0,
        "problemSpec": None,
        "lastMainCheckpoint": None,
        "clarifiedTopics": [],
        "activeClarification": None,
        "certificateHeadSha256": None,
    }
    assert main["route"] == {"intent": "advance", "targetStage": "solve"}
    assert main["checkpointIds"] == _MAIN_CHECKPOINT_IDS
    decoded = _decode_lane(main)
    result = decoded.result_semantic_scene.components[0]
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.problem_spec == problem
    assert result.last_main_checkpoint.value == "summary"
    assert result.clarified_topics == ()
    assert result.active_clarification is None

    trace_cues = [
        cue
        for checkpoint in decoded.checkpoints
        for cue in checkpoint.semantic.choreography.phase.cues
        if cue.cue == "trace_path"
    ]
    assert [checkpoint.semantic.checkpoint_id.value for checkpoint in decoded.checkpoints] == (
        _MAIN_CHECKPOINT_IDS
    )
    assert len(trace_cues) == 2
    assert trace_cues[0].path_id.endswith("__trajectory_ascent")
    assert trace_cues[1].path_id.endswith("__trajectory_descent")
    assert trace_cues[0].marker_id == trace_cues[1].marker_id


@pytest.mark.parametrize(
    "lane_key,prefix_length,topic,checkpoint_id,scenario_id,last_main_checkpoint",
    (
        (
            "clarifyHorizontal",
            2,
            "horizontal_velocity",
            "horizontal_velocity_detail",
            "horizontal_velocity_clarification",
            "decompose_velocity",
        ),
        (
            "clarifySymmetry",
            5,
            "flight_symmetry",
            "flight_symmetry_detail",
            "flight_symmetry_clarification",
            "trace_descent",
        ),
    ),
)
def test_primary_fixture_exposes_each_non_apex_clarification_at_its_prerequisite(
    lane_key: str,
    prefix_length: int,
    topic: str,
    checkpoint_id: str,
    scenario_id: str,
    last_main_checkpoint: str,
) -> None:
    fixture = _fixture("projectile-motion-v20-a45.v1.json")
    main = _decode_lane(fixture["lanes"]["main"])
    prerequisite_scene, prerequisite_semantic_scene = _materialize_prefix(main, prefix_length)

    payload = fixture["lanes"][lane_key]
    assert payload["scenarioId"] == scenario_id
    assert payload["generation"] == 2
    assert payload["route"] == {"intent": "clarify", "topic": topic}
    assert payload["checkpointIds"] == [checkpoint_id]
    clarification = _decode_lane(payload)
    assert clarification.base_scene == prerequisite_scene
    assert clarification.base_semantic_scene == prerequisite_semantic_scene
    result = clarification.result_semantic_scene.components[0]
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.last_main_checkpoint.value == last_main_checkpoint
    assert [candidate.value for candidate in result.clarified_topics] == [topic]
    assert result.active_clarification.value == topic
    assert clarification.checkpoints[0].semantic.certificate.body.previous_certificate_sha256 == (
        prerequisite_semantic_scene.certificate_head_sha256
    )


def test_physics_metadata_proves_complementary_range_and_qualified_extrema() -> None:
    fixtures = {(speed, angle): _fixture(filename) for speed, angle, filename in _CASES}
    for (speed, angle), fixture in fixtures.items():
        theta = math.radians(angle)
        vx = speed * math.cos(theta)
        vy = speed * math.sin(theta)
        apex_time = vy / PROJECTILE_MOTION_GRAVITY_MPS2
        expected = {
            "gravityMps2": PROJECTILE_MOTION_GRAVITY_MPS2,
            "initialHorizontalVelocityMps": vx,
            "initialVerticalVelocityMps": vy,
            "apexTimeSeconds": apex_time,
            "flightTimeSeconds": 2 * apex_time,
            "maximumHeightM": vy * vy / (2 * PROJECTILE_MOTION_GRAVITY_MPS2),
            "rangeM": vx * 2 * apex_time,
        }
        assert fixture["expectedPhysics"] == pytest.approx(expected, abs=1e-11)

    lower = fixtures[(20, 30)]
    upper = fixtures[(20, 60)]
    assert lower["expectedPhysics"]["rangeM"] == upper["expectedPhysics"]["rangeM"]
    assert lower["coverage"]["complementaryRangePartner"] == upper["problemSpec"]
    assert upper["coverage"]["complementaryRangePartner"] == lower["problemSpec"]
    assert upper["expectedPhysics"]["maximumHeightM"] > (lower["expectedPhysics"]["maximumHeightM"])

    qualified = [
        ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)
        for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
        for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
    ]
    assert fixtures[(30, 45)]["expectedPhysics"]["rangeM"] == pytest.approx(
        max(problem.range_m for problem in qualified)
    )
    assert fixtures[(30, 60)]["expectedPhysics"]["maximumHeightM"] == pytest.approx(
        max(problem.maximum_height_m for problem in qualified)
    )
    assert [
        case for case, fixture in fixtures.items() if fixture["coverage"]["isQualifiedMaximumRange"]
    ] == [(30, 45)]
    assert [
        case
        for case, fixture in fixtures.items()
        if fixture["coverage"]["isQualifiedMaximumHeight"]
    ] == [(30, 60)]


def test_primary_fixture_forks_at_apex_then_clarifies_or_retargets_in_place() -> None:
    fixture = _fixture("projectile-motion-v20-a45.v1.json")
    main = _decode_lane(fixture["lanes"]["main"])
    apex_scene, apex_semantic_scene = _materialize_prefix(main, 4)
    apex = apex_semantic_scene.components[0]
    assert isinstance(apex, ProjectileMotionStateV1)
    assert apex.last_main_checkpoint.value == "apex_state"

    clarify_payload = fixture["lanes"]["clarifyApex"]
    assert clarify_payload["scenarioId"] == "apex_acceleration_clarification"
    assert clarify_payload["generation"] == 2
    assert clarify_payload["route"] == {
        "intent": "clarify",
        "topic": "apex_acceleration",
    }
    clarify = _decode_lane(clarify_payload)
    assert clarify.base_scene == apex_scene
    assert clarify.base_semantic_scene == apex_semantic_scene
    clarified = clarify.result_semantic_scene.components[0]
    assert isinstance(clarified, ProjectileMotionStateV1)
    assert clarified.last_main_checkpoint.value == "apex_state"
    assert [topic.value for topic in clarified.clarified_topics] == ["apex_acceleration"]
    assert clarified.active_clarification.value == "apex_acceleration"
    assert clarify.checkpoints[0].semantic.certificate.body.previous_certificate_sha256 == (
        apex_semantic_scene.certificate_head_sha256
    )

    retarget_payload = fixture["lanes"]["retargetAtApex"]
    assert retarget_payload["scenarioId"] == "retarget_20_45_to_20_60_at_apex"
    assert retarget_payload["generation"] == 3
    assert retarget_payload["problemSpec"] == fixture["problemSpec"]
    assert retarget_payload["route"] == {
        "intent": "retarget",
        "targetProblemSpec": {"v": 1, "speedMps": 20, "angleDeg": 60},
    }
    retarget = _decode_lane(retarget_payload)
    assert retarget.base_scene == clarify.result_scene
    assert retarget.base_semantic_scene == clarify.result_semantic_scene
    result = retarget.result_semantic_scene.components[0]
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.id == clarified.id
    assert result.problem_spec == ProjectileMotionProblemSpecV1(speedMps=20, angleDeg=60)
    assert result.last_main_checkpoint == clarified.last_main_checkpoint
    assert result.clarified_topics == clarified.clarified_topics
    assert result.active_clarification == clarified.active_clarification
    assert [node.id for node in retarget.result_scene.nodes] == [
        node.id for node in retarget.base_scene.nodes
    ]
    assert retarget.result_scene.nodes != retarget.base_scene.nodes
    certificate = retarget.checkpoints[0].semantic.certificate
    assert certificate.body.previous_certificate_sha256 == (
        clarify.result_semantic_scene.certificate_head_sha256
    )


def test_primary_fixture_continues_after_clarification_then_retargets_summary() -> None:
    fixture = _fixture("projectile-motion-v20-a45.v1.json")
    main = _decode_lane(fixture["lanes"]["main"])
    apex_scene, apex_semantic_scene = _materialize_prefix(main, 4)

    clarify = _decode_lane(fixture["lanes"]["clarifyApex"])
    assert clarify.base_scene == apex_scene
    assert clarify.base_semantic_scene == apex_semantic_scene

    continuation_payload = fixture["lanes"]["continueAfterClarification"]
    assert continuation_payload["scenarioId"] == "continue_after_apex_clarification"
    assert continuation_payload["generation"] == 3
    assert continuation_payload["problemSpec"] == fixture["problemSpec"]
    assert continuation_payload["route"] == {"intent": "advance", "targetStage": "solve"}
    assert continuation_payload["checkpointIds"] == ["trace_descent", "summary"]
    continuation = _decode_lane(continuation_payload)
    assert continuation.base_scene == clarify.result_scene
    assert continuation.base_semantic_scene == clarify.result_semantic_scene
    assert continuation.checkpoints[0].semantic.certificate.body.previous_certificate_sha256 == (
        clarify.result_semantic_scene.certificate_head_sha256
    )
    continued = continuation.result_semantic_scene.components[0]
    assert isinstance(continued, ProjectileMotionStateV1)
    assert continued.problem_spec == ProjectileMotionProblemSpecV1(speedMps=20, angleDeg=45)
    assert continued.last_main_checkpoint.value == "summary"
    assert [topic.value for topic in continued.clarified_topics] == ["apex_acceleration"]
    assert continued.active_clarification is None

    retarget_payload = fixture["lanes"]["retargetAfterSummary"]
    assert retarget_payload["scenarioId"] == "retarget_20_45_to_20_60_after_summary"
    assert retarget_payload["generation"] == 4
    assert retarget_payload["problemSpec"] == fixture["problemSpec"]
    assert retarget_payload["route"] == {
        "intent": "retarget",
        "targetProblemSpec": {"v": 1, "speedMps": 20, "angleDeg": 60},
    }
    retarget = _decode_lane(retarget_payload)
    assert retarget.base_scene == continuation.result_scene
    assert retarget.base_semantic_scene == continuation.result_semantic_scene
    result = retarget.result_semantic_scene.components[0]
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.id == continued.id
    assert result.problem_spec == ProjectileMotionProblemSpecV1(speedMps=20, angleDeg=60)
    assert result.last_main_checkpoint == continued.last_main_checkpoint
    assert result.clarified_topics == continued.clarified_topics
    assert result.active_clarification is None
    assert [node.id for node in retarget.result_scene.nodes] == [
        node.id for node in retarget.base_scene.nodes
    ]
    assert retarget.result_scene.nodes != retarget.base_scene.nodes
    assert retarget.checkpoints[0].semantic.certificate.body.previous_certificate_sha256 == (
        continuation.result_semantic_scene.certificate_head_sha256
    )
