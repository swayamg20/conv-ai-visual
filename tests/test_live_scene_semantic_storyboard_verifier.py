"""Provider-free adversarial tests for the Gate 1.8 independent verifier."""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, replace
from itertools import combinations

import pytest
from murmur.live_scene import semantic_storyboard_verifier
from murmur.live_scene.choreography_contracts import ChoreographyPlanV2
from murmur.live_scene.contracts import LatexTokenSceneNode, PathSceneNode, SceneState
from murmur.live_scene.semantic_storyboard_compiler import (
    SemanticStoryboardCheckpointBlueprint,
    compile_semantic_storyboard_anchor,
    compile_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardSemanticEffectClosureV1,
    TraceStoryboardRecordV1,
    semantic_storyboard_program_sha256,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record
from murmur.live_scene.semantic_storyboard_verifier import (
    SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS,
    SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS,
    SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS,
    SemanticStoryboardVerificationError,
    SemanticStoryboardVerificationObligation,
    VerifiedStoryboardCheckpoint,
    verify_semantic_storyboard_anchor,
    verify_semantic_storyboard_checkpoint,
    verify_semantic_storyboard_frontier,
)

PREFIX = "projectile-comparison__"
SUPPORTED_PROBLEMS = tuple(
    PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)
    for speed in (20, 25, 30)
    for angles in combinations((30, 45, 60), 2)
)


def _problem(
    angles: tuple[int, int] = (30, 60),
    *,
    speed: int = 20,
) -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)


def _record(act: str, **fields: object) -> AcceptedSemanticStoryboardRecordV1:
    record = SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python({"v": 1, "act": act, **fields})
    assert record.act != "abstain"
    return record


def _semantic_scene(
    component: ProjectileStoryboardStateV1,
    *,
    certificate: str = "a" * 64,
) -> ProjectileStoryboardSemanticSceneStateV1:
    return ProjectileStoryboardSemanticSceneStateV1(
        revision=1 + len(component.accepted_records),
        components=(component,),
        certificateHeadSha256=certificate,
    )


def _checkpoint(
    problem: PairedProjectileComparisonSpecV1,
    prefix: tuple[AcceptedSemanticStoryboardRecordV1, ...],
    record: AcceptedSemanticStoryboardRecordV1,
) -> tuple[
    RoutedSemanticStoryboardBeatV1,
    SemanticStoryboardCheckpointBlueprint,
    ProjectileStoryboardSemanticSceneStateV1,
]:
    component = ProjectileStoryboardStateV1(problemSpec=problem, acceptedRecords=prefix)
    semantic_scene = _semantic_scene(component)
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=semantic_scene,
    )
    return beat, compile_semantic_storyboard_checkpoint(beat, component), semantic_scene


def _verify_case(
    problem: PairedProjectileComparisonSpecV1,
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...],
) -> tuple[VerifiedStoryboardCheckpoint, ...]:
    anchor = compile_semantic_storyboard_anchor(problem)
    receipts = [verify_semantic_storyboard_anchor(problem, anchor)]
    component = anchor.result_component
    for index, record in enumerate(records, start=1):
        semantic_scene = _semantic_scene(component, certificate=f"{index:x}".zfill(64))
        beat = route_semantic_storyboard_record(
            record,
            problem_spec=problem,
            semantic_scene=semantic_scene,
        )
        checkpoint = compile_semantic_storyboard_checkpoint(beat, component)
        receipts.append(
            verify_semantic_storyboard_checkpoint(
                beat,
                checkpoint,
                base_semantic_scene=semantic_scene,
            )
        )
        component = checkpoint.result_component
    return tuple(receipts)


def _scene(checkpoint: SemanticStoryboardCheckpointBlueprint) -> SceneState:
    return SceneState(
        revision=1 + len(checkpoint.result_component.accepted_records),
        nodes=checkpoint.result_nodes,
    )


def _assert_rejected(
    beat: RoutedSemanticStoryboardBeatV1,
    checkpoint: SemanticStoryboardCheckpointBlueprint,
    semantic_scene: ProjectileStoryboardSemanticSceneStateV1,
    code: SemanticStoryboardVerificationObligation | None = None,
) -> None:
    with pytest.raises(SemanticStoryboardVerificationError) as captured:
        verify_semantic_storyboard_checkpoint(
            beat,
            checkpoint,
            base_semantic_scene=semantic_scene,
        )
    if code is not None:
        assert captured.value.code is code


def _replace_result_node(
    checkpoint: SemanticStoryboardCheckpointBlueprint,
    node_id: str,
    replacement,
) -> SemanticStoryboardCheckpointBlueprint:
    return replace(
        checkpoint,
        result_nodes=tuple(
            replacement if node.id == node_id else node for node in checkpoint.result_nodes
        ),
    )


def test_anchor_receipt_binds_only_obligations_applicable_at_genesis() -> None:
    problem = _problem()
    checkpoint = compile_semantic_storyboard_anchor(problem)
    verified = verify_semantic_storyboard_anchor(problem, checkpoint)
    empty_hash = semantic_storyboard_program_sha256(problem, ())

    assert verified == VerifiedStoryboardCheckpoint(
        checkpoint_id="storyboard-anchor",
        operation_targets=tuple(operation.target_id for operation in checkpoint.patch.operations),
        base_program_sha256=empty_hash,
        result_program_sha256=empty_hash,
        semantic_effect=None,
        obligation_codes=SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS,
    )
    assert SemanticStoryboardVerificationObligation.EFFECT_CLOSURE not in verified.obligation_codes
    assert (
        SemanticStoryboardVerificationObligation.CERTIFICATE_CHAIN not in verified.obligation_codes
    )
    with pytest.raises(FrozenInstanceError):
        verified.checkpoint_id = "changed"  # type: ignore[misc]


def test_model_receipt_binds_the_complete_verifier_suite() -> None:
    receipt = _verify_case(
        _problem(),
        (_record("trace", trajectoryId="lower_angle"),),
    )[-1]

    assert receipt.obligation_codes == SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
    assert receipt.obligation_codes == SEMANTIC_STORYBOARD_VERIFICATION_OBLIGATIONS


def test_all_nine_anchor_frontiers_verify_against_independent_materialization() -> None:
    for problem in SUPPORTED_PROBLEMS:
        checkpoint = compile_semantic_storyboard_anchor(problem)
        verify_semantic_storyboard_anchor(problem, checkpoint)
        verify_semantic_storyboard_frontier(
            problem,
            _scene(checkpoint),
            _semantic_scene(checkpoint.result_component),
        )


def test_every_atomic_catalog_visual_and_evidence_branch_verifies() -> None:
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    equal_visual = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    equal_analytic = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["range_formula", "complementary_angles"],
    )
    unequal_visual = _record(
        "relate",
        claimId="unequal_range",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    unequal_analytic = _record(
        "relate",
        claimId="unequal_range",
        evidenceIds=["range_formula"],
    )
    apex = _record(
        "relate",
        claimId="higher_apex",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    flight = _record(
        "relate",
        claimId="longer_flight",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    cases = (
        (_problem(), (formula,)),
        (_problem(), (complementary,)),
        (_problem(), (lower,)),
        (_problem(), (higher,)),
        (_problem(), (lower, higher, equal_visual)),
        (_problem(), (formula, complementary, equal_analytic)),
        (_problem(), (lower, higher, apex)),
        (_problem(), (lower, higher, flight)),
        (_problem((30, 45)), (lower, higher, unequal_visual)),
        (_problem((30, 45)), (formula, unequal_analytic)),
    )

    for problem, records in cases:
        receipts = _verify_case(problem, records)
        assert len(receipts) == len(records) + 1
        assert receipts[-1].semantic_effect is not None
        assert receipts[-1].result_program_sha256 == semantic_storyboard_program_sha256(
            problem, records
        )


def test_complete_frontier_accepts_exact_33_node_retained_dom() -> None:
    records = (
        _record("reveal", conceptId="range_formula"),
        _record("reveal", conceptId="complementary_angles"),
        _record("trace", trajectoryId="lower_angle"),
        _record("trace", trajectoryId="higher_angle"),
        _record(
            "relate",
            claimId="equal_range",
            evidenceIds=["lower_trajectory", "higher_trajectory"],
        ),
        _record(
            "relate",
            claimId="higher_apex",
            evidenceIds=["lower_trajectory", "higher_trajectory"],
        ),
        _record(
            "relate",
            claimId="longer_flight",
            evidenceIds=["lower_trajectory", "higher_trajectory"],
        ),
    )
    problem = _problem(speed=30)
    component = compile_semantic_storyboard_anchor(problem).result_component
    checkpoint = None
    for index, record in enumerate(records, start=1):
        semantic_scene = _semantic_scene(component, certificate=f"{index:x}".zfill(64))
        beat = route_semantic_storyboard_record(
            record,
            problem_spec=problem,
            semantic_scene=semantic_scene,
        )
        checkpoint = compile_semantic_storyboard_checkpoint(beat, component)
        verify_semantic_storyboard_checkpoint(
            beat,
            checkpoint,
            base_semantic_scene=semantic_scene,
        )
        component = checkpoint.result_component
    assert checkpoint is not None
    assert len(checkpoint.result_nodes) == 33
    verify_semantic_storyboard_frontier(
        problem,
        _scene(checkpoint),
        _semantic_scene(component),
    )


def test_coherent_physics_tamper_is_rejected_even_when_patch_still_materializes() -> None:
    record = _record("trace", trajectoryId="lower_angle")
    beat, checkpoint, semantic_scene = _checkpoint(_problem(), (), record)
    path_id = f"{PREFIX}trajectory_lower"
    path = next(node for node in checkpoint.result_nodes if node.id == path_id)
    assert isinstance(path, PathSceneNode)
    changed_path = path.model_copy(
        update={"points": ((path.points[0][0] + 1.0, path.points[0][1]), *path.points[1:])}
    )
    changed_result = tuple(
        changed_path if node.id == path_id else node for node in checkpoint.result_nodes
    )
    changed_operations = tuple(
        operation.model_copy(update={"node": changed_path})
        if operation.target_id == path_id
        else operation
        for operation in checkpoint.patch.operations
    )
    changed_patch = checkpoint.patch.model_copy(update={"operations": changed_operations})
    tampered = replace(checkpoint, result_nodes=changed_result, patch=changed_patch)

    _assert_rejected(
        beat,
        tampered,
        semantic_scene,
        SemanticStoryboardVerificationObligation.PHYSICS_GEOMETRY,
    )


def test_snapshot_nodes_are_revalidated_before_geometry_is_read() -> None:
    record = _record("trace", trajectoryId="higher_angle")
    beat, checkpoint, semantic_scene = _checkpoint(_problem(), (), record)
    path_id = f"{PREFIX}trajectory_higher"
    path = next(node for node in checkpoint.result_nodes if node.id == path_id)
    token_id = f"{PREFIX}launch_angle_lower"
    token = next(node for node in checkpoint.result_nodes if node.id == token_id)
    assert isinstance(path, PathSceneNode)
    assert isinstance(token, LatexTokenSceneNode)

    malformed_nodes = (
        (path_id, path.model_copy(update={"points": ()})),
        (
            path_id,
            path.model_copy(update={"points": ((float("nan"), path.points[0][1]),)}),
        ),
        (token_id, token.model_copy(update={"width": 0.0})),
    )
    for node_id, malformed in malformed_nodes:
        with pytest.raises(SemanticStoryboardVerificationError) as captured:
            verify_semantic_storyboard_checkpoint(
                beat,
                _replace_result_node(checkpoint, node_id, malformed),
                base_semantic_scene=semantic_scene,
            )
        assert captured.value.code is SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT


def test_camera_containment_includes_strokes_and_twelve_point_safe_inset() -> None:
    problem = _problem(speed=30)
    anchor = compile_semantic_storyboard_anchor(problem)
    anchor_nodes = {node.id: node for node in anchor.result_nodes}
    anchor_targets = tuple(
        f"{PREFIX}{suffix}"
        for suffix in (
            "launch_ray_lower",
            "launch_ray_higher",
            "launch_angle_lower",
            "launch_angle_higher",
        )
    )
    semantic_storyboard_verifier._verify_viewport_targets(
        anchor.presentation.result_viewports,
        anchor_nodes,
        anchor_targets,
    )

    record = _record("trace", trajectoryId="higher_angle")
    _, checkpoint, _ = _checkpoint(problem, (), record)
    path_id = f"{PREFIX}trajectory_higher"
    marker_id = f"{PREFIX}projectile_marker_higher"
    result_nodes = {node.id: node for node in checkpoint.result_nodes}
    semantic_storyboard_verifier._verify_viewport_targets(
        checkpoint.presentation.result_viewports,
        result_nodes,
        (path_id, marker_id),
    )

    left, top, right, bottom = semantic_storyboard_verifier._node_box(result_nodes[path_id])
    unsafe_padding = 11.9
    unsafe_pose = checkpoint.presentation.result_viewports.cinematic.model_copy(
        update={
            "x": left - unsafe_padding,
            "y": top - 12.0,
            "width": right - left + unsafe_padding + 12.0,
            "height": bottom - top + 24.0,
        }
    )
    unsafe_viewports = checkpoint.presentation.result_viewports.model_copy(
        update={"cinematic": unsafe_pose, "compact": unsafe_pose}
    )
    with pytest.raises(SemanticStoryboardVerificationError) as captured:
        semantic_storyboard_verifier._verify_viewport_targets(
            unsafe_viewports,
            result_nodes,
            (path_id,),
        )
    assert captured.value.code is SemanticStoryboardVerificationObligation.VIEWPORT


def test_node_identity_order_label_style_and_snapshot_prefix_mutations_fail_closed() -> None:
    record = _record("reveal", conceptId="range_formula")
    beat, checkpoint, semantic_scene = _checkpoint(_problem(), (), record)
    token_id = f"{PREFIX}range_formula"
    token = next(node for node in checkpoint.result_nodes if node.id == token_id)
    assert isinstance(token, LatexTokenSceneNode)

    foreign = token.model_copy(update={"id": "other-component__range_formula"})
    _assert_rejected(
        beat,
        _replace_result_node(checkpoint, token_id, foreign),
        semantic_scene,
        SemanticStoryboardVerificationObligation.STABLE_IDS,
    )
    reordered = replace(
        checkpoint,
        result_nodes=(
            *checkpoint.result_nodes[:-2],
            checkpoint.result_nodes[-1],
            checkpoint.result_nodes[-2],
        ),
    )
    _assert_rejected(
        beat,
        reordered,
        semantic_scene,
        SemanticStoryboardVerificationObligation.STABLE_IDS,
    )
    changed_label = token.model_copy(update={"latex": r"R(\theta)=0"})
    _assert_rejected(
        beat,
        _replace_result_node(checkpoint, token_id, changed_label),
        semantic_scene,
        SemanticStoryboardVerificationObligation.LABEL_FACT,
    )
    changed_style = token.model_copy(
        update={"style": token.style.model_copy(update={"opacity": 0.5})}
    )
    _assert_rejected(
        beat,
        _replace_result_node(checkpoint, token_id, changed_style),
        semantic_scene,
        SemanticStoryboardVerificationObligation.VISUAL_STYLE,
    )
    wrong_component = checkpoint.result_component.model_copy(update={"accepted_records": ()})
    _assert_rejected(
        beat,
        replace(checkpoint, result_component=wrong_component),
        semantic_scene,
        SemanticStoryboardVerificationObligation.SEMANTIC_TRANSITION,
    )


def test_patch_caption_cue_timing_and_viewport_mutations_fail_closed() -> None:
    record = _record("trace", trajectoryId="higher_angle")
    beat, checkpoint, semantic_scene = _checkpoint(_problem(), (), record)

    _assert_rejected(
        beat,
        replace(
            checkpoint,
            patch=checkpoint.patch.model_copy(update={"patch_id": "tampered-patch"}),
        ),
        semantic_scene,
        SemanticStoryboardVerificationObligation.PATCH,
    )
    _assert_rejected(
        beat,
        replace(
            checkpoint,
            patch=checkpoint.patch.model_copy(update={"narration": "A different claim."}),
        ),
        semantic_scene,
        SemanticStoryboardVerificationObligation.PATCH,
    )
    _assert_rejected(
        beat,
        replace(
            checkpoint,
            patch=checkpoint.patch.model_copy(
                update={"operations": tuple(reversed(checkpoint.patch.operations))}
            ),
        ),
        semantic_scene,
        SemanticStoryboardVerificationObligation.PATCH,
    )
    changed_cues = checkpoint.choreography.phase.cues[:-1]
    changed_phase = checkpoint.choreography.phase.model_copy(update={"cues": changed_cues})
    changed_choreography = checkpoint.choreography.model_copy(update={"phase": changed_phase})
    _assert_rejected(
        beat,
        replace(checkpoint, choreography=changed_choreography),
        semantic_scene,
        SemanticStoryboardVerificationObligation.CHOREOGRAPHY,
    )
    timing_phase = checkpoint.choreography.phase.model_copy(update={"duration_ms": 1_601})
    timing = ChoreographyPlanV2(phase=timing_phase)
    _assert_rejected(
        beat,
        replace(checkpoint, choreography=timing),
        semantic_scene,
        SemanticStoryboardVerificationObligation.TIMING,
    )
    changed_compact = checkpoint.presentation.result_viewports.compact.model_copy(
        update={"x": 37.0}
    )
    changed_viewports = checkpoint.presentation.result_viewports.model_copy(
        update={"compact": changed_compact}
    )
    changed_presentation = checkpoint.presentation.model_copy(
        update={"result_viewports": changed_viewports}
    )
    _assert_rejected(
        beat,
        replace(checkpoint, presentation=changed_presentation),
        semantic_scene,
        SemanticStoryboardVerificationObligation.VIEWPORT,
    )


def test_effect_closure_program_hashes_and_previous_certificate_are_recomputed() -> None:
    record = _record("trace", trajectoryId="lower_angle")
    beat, checkpoint, semantic_scene = _checkpoint(_problem(), (), record)
    wrong_effect = StoryboardSemanticEffectClosureV1(
        conceptIds=("range_formula",),
        producedEvidenceIds=("range_formula",),
    )
    _assert_rejected(
        beat.model_copy(update={"semantic_effect": wrong_effect}),
        checkpoint,
        semantic_scene,
        SemanticStoryboardVerificationObligation.EFFECT_CLOSURE,
    )
    _assert_rejected(
        beat.model_copy(update={"base_program_sha256": "b" * 64}),
        checkpoint,
        semantic_scene,
        SemanticStoryboardVerificationObligation.PROGRAM_HASH,
    )
    _assert_rejected(
        beat.model_copy(update={"result_program_sha256": "b" * 64}),
        checkpoint,
        semantic_scene,
        SemanticStoryboardVerificationObligation.PROGRAM_HASH,
    )
    _assert_rejected(
        beat.model_copy(update={"previous_certificate_sha256": "b" * 64}),
        checkpoint,
        semantic_scene,
        SemanticStoryboardVerificationObligation.CERTIFICATE_CHAIN,
    )


def test_frontier_rejects_wrong_revision_problem_order_and_visual_content() -> None:
    problem = _problem()
    checkpoint = compile_semantic_storyboard_anchor(problem)
    scene = _scene(checkpoint)
    semantic_scene = _semantic_scene(checkpoint.result_component)

    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(
            problem,
            scene.model_copy(update={"revision": 2}),
            semantic_scene,
        )
    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(_problem((30, 45)), scene, semantic_scene)
    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(
            problem,
            scene.model_copy(update={"nodes": tuple(reversed(scene.nodes))}),
            semantic_scene,
        )
    givens_id = f"{PREFIX}givens"
    givens = next(node for node in scene.nodes if node.id == givens_id)
    assert isinstance(givens, LatexTokenSceneNode)
    changed = givens.model_copy(update={"latex": r"v_0=999"})
    changed_nodes = tuple(changed if node.id == givens_id else node for node in scene.nodes)
    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(
            problem,
            scene.model_copy(update={"nodes": changed_nodes}),
            semantic_scene,
        )


def test_empty_frontier_is_exactly_revision_zero_and_node_free() -> None:
    problem = _problem()
    empty_semantic = ProjectileStoryboardSemanticSceneStateV1(revision=0)
    assert (
        verify_semantic_storyboard_frontier(problem, SceneState(revision=0), empty_semantic) is None
    )
    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(
            problem,
            SceneState(revision=0, nodes=compile_semantic_storyboard_anchor(problem).result_nodes),
            empty_semantic,
        )


def test_runtime_boundaries_and_forbidden_imports_are_fail_closed() -> None:
    source = inspect.getsource(semantic_storyboard_verifier)
    imported_modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any("semantic_storyboard_compiler" in module for module in imported_modules)
    assert not any("semantic_storyboard_routing" in module for module in imported_modules)
    assert not any("projectile_motion_compiler" in module for module in imported_modules)
    assert not any("projectile_motion_verifier" in module for module in imported_modules)
    for shared_derivation in (
        "semantic_storyboard_program_sha256",
        "semantic_effect_for_storyboard_record",
        "storyboard_evidence_after",
        "storyboard_record_is_applicable",
        "storyboard_record_slug",
    ):
        assert shared_derivation not in source

    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_anchor(object(), object())  # type: ignore[arg-type]
    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_frontier(object(), object(), object())  # type: ignore[arg-type]

    problem = _problem()
    component = compile_semantic_storyboard_anchor(problem).result_component
    semantic_scene = _semantic_scene(component)
    record = _record("trace", trajectoryId="lower_angle")
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=semantic_scene,
    )
    checkpoint = compile_semantic_storyboard_checkpoint(beat, component)
    malformed_records = (
        TraceStoryboardRecordV1.model_construct(v=1, act="trace"),
        TraceStoryboardRecordV1.model_construct(
            v=1,
            act="trace",
            trajectory_id="invented",
        ),
    )
    for malformed_record in malformed_records:
        malformed_beat = beat.model_copy(update={"record": malformed_record})
        with pytest.raises(SemanticStoryboardVerificationError) as malformed_error:
            verify_semantic_storyboard_checkpoint(
                malformed_beat,
                checkpoint,
                base_semantic_scene=semantic_scene,
            )
        assert (
            malformed_error.value.code
            is SemanticStoryboardVerificationObligation.BLUEPRINT_CONTRACT
        )

    with pytest.raises(SemanticStoryboardVerificationError):
        verify_semantic_storyboard_checkpoint(
            beat,
            object(),  # type: ignore[arg-type]
            base_semantic_scene=semantic_scene,
        )
