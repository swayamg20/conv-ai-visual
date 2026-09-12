from __future__ import annotations

from itertools import pairwise, permutations

import pytest
from murmur.live_scene import projectile_motion_checkpoint_compiler as checkpoint_compiler
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES, MAX_PATCH_OPERATIONS, SceneState
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    CompiledProjectileMotionCheckpointBeatV1,
    ProjectileMotionCheckpointCompilationError,
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
    CompiledProjectileMotionCheckpointV1,
    ProjectileMotionCheckpointAction,
    ProjectileMotionCheckpointCompilerCertificateV1,
    projectile_motion_checkpoint_certificate_sha256,
)
from murmur.live_scene.projectile_motion_compiler import (
    materialize_projectile_motion_nodes,
    materialize_projectile_motion_scene_nodes,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
    SUPPORTED_PROJECTILE_ANGLES_DEG,
    SUPPORTED_PROJECTILE_SPEEDS_MPS,
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStage,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
    projectile_motion_problem_sha256,
)
from murmur.live_scene.semantic_contracts import (
    PythagoreanAreaIdentityState,
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1
from pydantic import ValidationError

SUPPORTED_PROBLEMS = tuple(
    ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)
    for speed in SUPPORTED_PROJECTILE_SPEEDS_MPS
    for angle in SUPPORTED_PROJECTILE_ANGLES_DEG
)
ALL_FRONTIERS = (None, *PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER)


def _state(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint | None,
    *,
    topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active: ProjectileMotionClarificationTopic | None = None,
    component_id: str = "lesson",
) -> ProjectileMotionStateV1:
    return ProjectileMotionStateV1(
        id=component_id,
        problem_spec=problem,
        last_main_checkpoint=frontier,
        clarified_topics=topics,
        active_clarification=active,
    )


def _base(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint | None,
    *,
    revision: int = 17,
    certificate_head: str | None = "a" * 64,
    topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active: ProjectileMotionClarificationTopic | None = None,
) -> tuple[SceneState, SemanticSceneState]:
    if frontier is None:
        return (
            SceneState(revision=revision),
            SemanticSceneState(
                revision=revision,
                certificate_head_sha256=certificate_head,
            ),
        )
    component = _state(problem, frontier, topics=topics, active=active)
    return (
        SceneState(
            revision=revision,
            nodes=materialize_projectile_motion_scene_nodes(component),
        ),
        SemanticSceneState(
            revision=revision,
            components=(component,),
            certificate_head_sha256=certificate_head,
        ),
    )


def _advance_beat(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint | None,
    *,
    stage: ProjectileMotionStage = ProjectileMotionStage.SOLVE,
    beat_id: str = "projectile-advance",
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id=beat_id,
        component_id="lesson",
        base_problem_spec=None if frontier is None else problem,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=stage),
    )


def _clarify_beat(
    problem: ProjectileMotionProblemSpecV1,
    topic: ProjectileMotionClarificationTopic,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id=f"projectile-clarify-{topic.value}",
        component_id="lesson",
        base_problem_spec=problem,
        result_problem_spec=problem,
        route=ClarifyProjectileMotionRouteV1(topic=topic),
    )


def _retarget_beat(
    base_problem: ProjectileMotionProblemSpecV1,
    result_problem: ProjectileMotionProblemSpecV1,
) -> RoutedProjectileMotionBeatV1:
    return RoutedProjectileMotionBeatV1(
        beat_id="projectile-retarget",
        component_id="lesson",
        base_problem_spec=base_problem,
        result_problem_spec=result_problem,
        route=RetargetProjectileMotionRouteV1(target_problem_spec=result_problem),
    )


def _compile(
    problem: ProjectileMotionProblemSpecV1 = SUPPORTED_PROBLEMS[1],
    frontier: ProjectileMotionMainCheckpoint | None = None,
) -> CompiledProjectileMotionCheckpointBeatV1:
    base_scene, base_semantic_scene = _base(problem, frontier)
    return compile_projectile_motion_checkpoint_beat(
        _advance_beat(problem, frontier),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )


def _batch_with(
    compiled: CompiledProjectileMotionCheckpointBeatV1,
    *,
    checkpoints: tuple[CompiledProjectileMotionCheckpointV1, ...] | None = None,
    result_scene: SceneState | None = None,
    result_semantic_scene: SemanticSceneState | None = None,
) -> CompiledProjectileMotionCheckpointBeatV1:
    return CompiledProjectileMotionCheckpointBeatV1(
        beat=compiled.beat,
        base_scene=compiled.base_scene,
        result_scene=result_scene or compiled.result_scene,
        base_semantic_scene=compiled.base_semantic_scene,
        result_semantic_scene=result_semantic_scene or compiled.result_semantic_scene,
        checkpoints=compiled.checkpoints if checkpoints is None else checkpoints,
    )


def _reissue_certificate(
    checkpoint: CompiledProjectileMotionCheckpointV1,
    **body_updates: object,
) -> CompiledProjectileMotionCheckpointV1:
    body = checkpoint.certificate.body.model_copy(update=body_updates)
    certificate = ProjectileMotionCheckpointCompilerCertificateV1(
        body=body,
        certificate_sha256=projectile_motion_checkpoint_certificate_sha256(body),
    )
    return checkpoint.model_copy(update={"certificate": certificate})


def test_all_nine_problems_certify_all_seven_main_frontiers() -> None:
    certified_count = 0
    for problem in SUPPORTED_PROBLEMS:
        digest = projectile_motion_problem_sha256(problem)
        for prefix_length, frontier in enumerate(ALL_FRONTIERS):
            compiled = _compile(problem, frontier)
            expected_ids = tuple(
                checkpoint.value
                for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[prefix_length:]
            )
            assert (
                tuple(checkpoint.checkpoint_id.value for checkpoint in compiled.checkpoints)
                == expected_ids
            )
            assert compiled.result_scene.revision == 17 + len(expected_ids)
            assert compiled.result_semantic_scene.revision == compiled.result_scene.revision

            expected_low_hash = low_level_scene_sha256(compiled.base_scene)
            expected_semantic_hash = semantic_scene_sha256(compiled.base_semantic_scene)
            expected_head = compiled.base_semantic_scene.certificate_head_sha256
            expected_revision = compiled.base_scene.revision
            for index, checkpoint in enumerate(compiled.checkpoints):
                body = checkpoint.certificate.body
                receipt = checkpoint.receipt
                expected_base_digest = None if frontier is None and index == 0 else digest
                assert checkpoint.action is ProjectileMotionCheckpointAction.ADVANCE
                assert checkpoint.clarification_topic is None
                assert receipt.base_problem_spec_sha256 == expected_base_digest
                assert receipt.result_problem_spec_sha256 == digest
                assert body.base_problem_spec_sha256 == expected_base_digest
                assert body.result_problem_spec_sha256 == digest
                assert receipt.obligation_codes == PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS
                assert receipt.operation_targets == tuple(
                    operation.target_id for operation in checkpoint.patch.operations
                )
                assert body.base_low_level_revision == expected_revision
                assert body.base_semantic_revision == expected_revision
                assert body.result_low_level_revision == expected_revision + 1
                assert body.result_semantic_revision == expected_revision + 1
                assert body.base_low_level_scene_sha256 == expected_low_hash
                assert body.base_semantic_scene_sha256 == expected_semantic_hash
                assert body.previous_certificate_sha256 == expected_head
                assert 1 <= len(checkpoint.patch.operations) <= MAX_PATCH_OPERATIONS
                assert (
                    len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
                    <= MAX_NDJSON_FRAME_BYTES
                )
                expected_revision += 1
                expected_low_hash = body.result_low_level_scene_sha256
                expected_semantic_hash = body.result_semantic_scene_sha256
                expected_head = checkpoint.certificate.certificate_sha256
                certified_count += 1

            assert expected_low_hash == low_level_scene_sha256(compiled.result_scene)
            assert expected_semantic_hash == semantic_scene_sha256(compiled.result_semantic_scene)
            assert compiled.result_semantic_scene.certificate_head_sha256 == expected_head
            for previous, current in pairwise(compiled.checkpoints):
                assert previous.presentation.result_viewports == current.presentation.base_viewports

    assert certified_count == 189


def test_all_eligible_clarifications_certify_and_preserve_problem_identity() -> None:
    certified_count = 0
    for problem in SUPPORTED_PROBLEMS:
        digest = projectile_motion_problem_sha256(problem)
        for topic in PROJECTILE_MOTION_CLARIFICATION_ORDER:
            prerequisite = PROJECTILE_MOTION_CLARIFICATION_PREREQUISITES[topic]
            start = PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER.index(prerequisite)
            for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER[start:]:
                base_scene, base_semantic_scene = _base(problem, frontier)
                compiled = compile_projectile_motion_checkpoint_beat(
                    _clarify_beat(problem, topic),
                    base_scene=base_scene,
                    base_semantic_scene=base_semantic_scene,
                )
                assert len(compiled.checkpoints) == 1
                checkpoint = compiled.checkpoints[0]
                assert checkpoint.action is ProjectileMotionCheckpointAction.CLARIFY
                assert checkpoint.clarification_topic is topic
                assert checkpoint.receipt.base_problem_spec_sha256 == digest
                assert checkpoint.receipt.result_problem_spec_sha256 == digest
                assert checkpoint.certificate.body.base_problem_spec_sha256 == digest
                assert checkpoint.certificate.body.result_problem_spec_sha256 == digest
                component = next(
                    item
                    for item in compiled.result_semantic_scene.components
                    if item.id == "lesson"
                )
                assert isinstance(component, ProjectileMotionStateV1)
                assert component.active_clarification is topic
                assert topic in component.clarified_topics
                certified_count += 1
    assert certified_count == 90


def test_out_of_order_clarifications_chain_and_main_advance_clears_active_sidecar() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    base_scene, base_semantic_scene = _base(
        problem,
        ProjectileMotionMainCheckpoint.TRACE_DESCENT,
    )
    topics = (
        ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
        ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    )
    expected_head = base_semantic_scene.certificate_head_sha256
    for topic in topics:
        compiled = compile_projectile_motion_checkpoint_beat(
            _clarify_beat(problem, topic),
            base_scene=base_scene,
            base_semantic_scene=base_semantic_scene,
        )
        checkpoint = compiled.checkpoints[0]
        assert checkpoint.certificate.body.previous_certificate_sha256 == expected_head
        base_scene = compiled.result_scene
        base_semantic_scene = compiled.result_semantic_scene
        expected_head = checkpoint.certificate.certificate_sha256

    continued = compile_projectile_motion_checkpoint_beat(
        _advance_beat(
            problem,
            ProjectileMotionMainCheckpoint.TRACE_DESCENT,
            beat_id="continue-after-sidecars",
        ),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    assert len(continued.checkpoints) == 1
    result = next(
        item for item in continued.result_semantic_scene.components if item.id == "lesson"
    )
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.active_clarification is None
    assert result.clarified_topics == PROJECTILE_MOTION_CLARIFICATION_ORDER


def test_all_432_directed_retargets_certify_changed_problem_identity() -> None:
    certified_count = 0
    for base_problem, result_problem in permutations(SUPPORTED_PROBLEMS, 2):
        base_digest = projectile_motion_problem_sha256(base_problem)
        result_digest = projectile_motion_problem_sha256(result_problem)
        for frontier in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER:
            base_scene, base_semantic_scene = _base(base_problem, frontier)
            compiled = compile_projectile_motion_checkpoint_beat(
                _retarget_beat(base_problem, result_problem),
                base_scene=base_scene,
                base_semantic_scene=base_semantic_scene,
            )
            assert len(compiled.checkpoints) == 1
            checkpoint = compiled.checkpoints[0]
            assert checkpoint.action is ProjectileMotionCheckpointAction.RETARGET
            assert checkpoint.clarification_topic is None
            assert checkpoint.receipt.base_problem_spec_sha256 == base_digest
            assert checkpoint.receipt.result_problem_spec_sha256 == result_digest
            assert checkpoint.certificate.body.base_problem_spec_sha256 == base_digest
            assert checkpoint.certificate.body.result_problem_spec_sha256 == result_digest
            result = next(
                item for item in compiled.result_semantic_scene.components if item.id == "lesson"
            )
            assert isinstance(result, ProjectileMotionStateV1)
            assert result.problem_spec == result_problem
            assert result.last_main_checkpoint is frontier
            certified_count += 1
    assert certified_count == 432


@pytest.mark.parametrize("active", PROJECTILE_MOTION_CLARIFICATION_ORDER)
def test_retarget_preserves_active_clarification_and_chain(
    active: ProjectileMotionClarificationTopic,
) -> None:
    base_problem = SUPPORTED_PROBLEMS[0]
    result_problem = SUPPORTED_PROBLEMS[-1]
    base_scene, base_semantic_scene = _base(
        base_problem,
        ProjectileMotionMainCheckpoint.SUMMARY,
        topics=PROJECTILE_MOTION_CLARIFICATION_ORDER,
        active=active,
    )

    compiled = compile_projectile_motion_checkpoint_beat(
        _retarget_beat(base_problem, result_problem),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )

    result = next(item for item in compiled.result_semantic_scene.components if item.id == "lesson")
    assert isinstance(result, ProjectileMotionStateV1)
    assert result.active_clarification is active
    assert result.clarified_topics == PROJECTILE_MOTION_CLARIFICATION_ORDER
    assert (
        compiled.checkpoints[0].certificate.body.previous_certificate_sha256
        == base_semantic_scene.certificate_head_sha256
    )


def test_completed_frontier_returns_zero_suffix_without_changing_scenes_or_head() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    base_scene, base_semantic_scene = _base(
        problem,
        ProjectileMotionMainCheckpoint.SUMMARY,
        revision=29,
        certificate_head="b" * 64,
    )

    compiled = compile_projectile_motion_checkpoint_beat(
        _advance_beat(problem, ProjectileMotionMainCheckpoint.SUMMARY),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )

    assert compiled.checkpoints == ()
    assert compiled.result_scene is compiled.base_scene
    assert compiled.result_semantic_scene is compiled.base_semantic_scene
    assert compiled.result_scene == base_scene
    assert compiled.result_semantic_scene == base_semantic_scene


def test_late_verifier_failure_returns_no_partial_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = checkpoint_compiler.verify_projectile_motion_checkpoint
    verified_ids: list[str] = []

    def fail_on_summary(blueprint) -> None:
        verified_ids.append(blueprint.checkpoint_id.value)
        original(blueprint)
        if blueprint.checkpoint_id.value == "summary":
            raise RuntimeError("synthetic late verifier failure")

    monkeypatch.setattr(
        checkpoint_compiler,
        "verify_projectile_motion_checkpoint",
        fail_on_summary,
    )
    problem = SUPPORTED_PROBLEMS[1]
    base_scene, base_semantic_scene = _base(problem, None)
    result: CompiledProjectileMotionCheckpointBeatV1 | None = None

    with pytest.raises(RuntimeError, match="synthetic late verifier failure"):
        result = compile_projectile_motion_checkpoint_beat(
            _advance_beat(problem, None),
            base_scene=base_scene,
            base_semantic_scene=base_semantic_scene,
        )

    assert result is None
    assert verified_ids == [
        checkpoint.value for checkpoint in PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER
    ]
    assert base_scene == SceneState(revision=17)
    assert base_semantic_scene == SemanticSceneState(
        revision=17,
        certificate_head_sha256="a" * 64,
    )


@pytest.mark.parametrize(
    "body_updates",
    [
        {
            "base_low_level_revision": 101,
            "result_low_level_revision": 102,
            "base_semantic_revision": 101,
            "result_semantic_revision": 102,
        },
        {"base_low_level_scene_sha256": "f" * 64},
        {"result_low_level_scene_sha256": "e" * 64},
        {"base_semantic_scene_sha256": "d" * 64},
        {"result_semantic_scene_sha256": "c" * 64},
        {"previous_certificate_sha256": "b" * 64},
    ],
)
def test_batch_rejects_reissued_revision_hash_and_head_claims(
    body_updates: dict[str, object],
) -> None:
    compiled = _compile()
    first = _reissue_certificate(compiled.checkpoints[0], **body_updates)

    with pytest.raises(ValidationError, match="independently verified compiler claim"):
        _batch_with(compiled, checkpoints=(first, *compiled.checkpoints[1:]))


def test_batch_rejects_reordered_suffix_and_inexact_result_boundaries() -> None:
    compiled = _compile()
    reordered = (
        compiled.checkpoints[1],
        compiled.checkpoints[0],
        *compiled.checkpoints[2:],
    )
    with pytest.raises(ValidationError, match="exact missing suffix"):
        _batch_with(compiled, checkpoints=reordered)

    reversed_result = SceneState(
        revision=compiled.result_scene.revision,
        nodes=tuple(reversed(compiled.result_scene.nodes)),
    )
    with pytest.raises(ValidationError, match="exact result scene"):
        _batch_with(compiled, result_scene=reversed_result)

    wrong_head = compiled.result_semantic_scene.model_copy(
        update={"certificate_head_sha256": "0" * 64}
    )
    with pytest.raises(ValidationError, match="exact semantic frontier"):
        _batch_with(compiled, result_semantic_scene=wrong_head)


def test_batch_rejects_a_fully_reissued_corrupt_final_checkpoint() -> None:
    compiled = _compile()
    original = compiled.checkpoints[-1]
    narration = f"{original.patch.narration} Altered after verification."
    patch = original.patch.model_copy(update={"narration": narration})
    presentation = original.presentation.model_copy(update={"checkpoint_narration": narration})
    body = original.certificate.body.model_copy(
        update={
            "patch_sha256": scene_patch_sha256(patch),
            "presentation_checkpoint": presentation,
        }
    )
    certificate = ProjectileMotionCheckpointCompilerCertificateV1(
        body=body,
        certificate_sha256=projectile_motion_checkpoint_certificate_sha256(body),
    )
    corrupt = CompiledProjectileMotionCheckpointV1(
        beat=original.beat,
        action=original.action,
        checkpoint_id=original.checkpoint_id,
        clarification_topic=original.clarification_topic,
        patch=patch,
        receipt=original.receipt,
        presentation=presentation,
        choreography=original.choreography,
        certificate=certificate,
    )

    with pytest.raises(ValidationError, match="independently verified compiler claim"):
        _batch_with(
            compiled,
            checkpoints=(*compiled.checkpoints[:-1], corrupt),
        )


def test_revision_drift_dirty_namespace_foreign_kind_and_cross_problem_fail() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    beat = _advance_beat(problem, None)
    dirty_state = _state(problem, ProjectileMotionMainCheckpoint.SETUP)
    dirty_node = materialize_projectile_motion_nodes(dirty_state)[0]

    with pytest.raises(ProjectileMotionCheckpointCompilationError, match="revisions must match"):
        compile_projectile_motion_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=1),
            base_semantic_scene=SemanticSceneState(revision=2),
        )
    with pytest.raises(ProjectileMotionCheckpointCompilationError, match="does not match"):
        compile_projectile_motion_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=0, nodes=(dirty_node,)),
            base_semantic_scene=SemanticSceneState(revision=0),
        )
    with pytest.raises(ProjectileMotionCheckpointCompilationError, match="different semantic"):
        compile_projectile_motion_checkpoint_beat(
            beat,
            base_scene=SceneState(revision=0),
            base_semantic_scene=SemanticSceneState(
                revision=0,
                components=(PythagoreanAreaIdentityState(id="lesson"),),
            ),
        )

    other_problem = SUPPORTED_PROBLEMS[-1]
    other = _state(other_problem, ProjectileMotionMainCheckpoint.SETUP)
    with pytest.raises(ProjectileMotionCheckpointCompilationError, match="baseProblemSpec"):
        compile_projectile_motion_checkpoint_beat(
            _advance_beat(problem, ProjectileMotionMainCheckpoint.SETUP),
            base_scene=SceneState(
                revision=1,
                nodes=materialize_projectile_motion_nodes(other),
            ),
            base_semantic_scene=SemanticSceneState(revision=1, components=(other,)),
        )


def test_continuation_rejects_reordered_svg_paint_state_under_an_unchanged_head() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    frontier = ProjectileMotionMainCheckpoint.APEX_STATE
    base_scene, base_semantic_scene = _base(problem, frontier)
    assert tuple(node.id for node in base_scene.nodes) != tuple(
        sorted(node.id for node in base_scene.nodes)
    )
    reordered = SceneState(
        revision=base_scene.revision,
        nodes=tuple(reversed(base_scene.nodes)),
    )

    with pytest.raises(ProjectileMotionCheckpointCompilationError, match="paint order"):
        compile_projectile_motion_checkpoint_beat(
            _advance_beat(problem, frontier),
            base_scene=reordered,
            base_semantic_scene=base_semantic_scene,
        )


def test_compiler_preserves_unrelated_low_level_and_semantic_state() -> None:
    problem = SUPPORTED_PROBLEMS[1]
    unrelated = PythagoreanAreaIdentityState(id="other")
    base_scene = SceneState(revision=3)
    base_semantic_scene = SemanticSceneState(revision=3, components=(unrelated,))

    compiled = compile_projectile_motion_checkpoint_beat(
        _advance_beat(problem, None, stage=ProjectileMotionStage.SETUP),
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )

    assert compiled.result_semantic_scene.components[0] == unrelated
    assert isinstance(compiled.result_semantic_scene.components[1], ProjectileMotionStateV1)


def test_compilation_is_byte_deterministic() -> None:
    first = _compile()
    second = _compile()

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
