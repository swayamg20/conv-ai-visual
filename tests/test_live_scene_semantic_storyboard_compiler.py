"""Provider-free tests for the atomic Gate 1.8 visual compiler."""

from __future__ import annotations

import ast
import inspect
import math
from itertools import combinations, pairwise

import pytest
from murmur.live_scene import semantic_storyboard_compiler
from murmur.live_scene.choreography_contracts import (
    EmphasizeCueV1,
    FocusCueV1,
    TracePathCueV2,
)
from murmur.live_scene.contracts import (
    MAX_NDJSON_FRAME_BYTES,
    LatexTokenSceneNode,
    LineSceneNode,
    PathSceneNode,
    PutSceneOperation,
    SceneNode,
)
from murmur.live_scene.semantic_integrity import canonical_json_v1
from murmur.live_scene.semantic_storyboard_compiler import (
    MAX_STORYBOARD_ANCHOR_OPERATIONS,
    MAX_STORYBOARD_BEAT_OPERATIONS,
    STORYBOARD_ANCHOR_CHECKPOINT_ID,
    SemanticStoryboardCompilationError,
    compile_semantic_storyboard_anchor,
    compile_semantic_storyboard_checkpoint,
    materialize_semantic_storyboard_nodes,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    ProjectileStoryboardStateV1,
    RoutedSemanticStoryboardBeatV1,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record

SUPPORTED_COMPARISONS = tuple(
    PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)
    for speed in (20, 25, 30)
    for angles in combinations((30, 45, 60), 2)
)
PREFIX = "projectile-comparison__"
ANCHOR_TARGETS = tuple(
    PREFIX + suffix
    for suffix in (
        "ground_axis",
        "vertical_axis",
        "panel_rule",
        "launch_ray_lower",
        "launch_ray_higher",
        "projectile_marker_lower",
        "projectile_marker_higher",
        "axis_x_label",
        "axis_y_label",
        "launch_angle_lower",
        "launch_angle_higher",
        "givens",
        "range_relation",
    )
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
) -> ProjectileStoryboardSemanticSceneStateV1:
    return ProjectileStoryboardSemanticSceneStateV1(
        revision=1 + len(component.accepted_records),
        components=(component,),
        certificateHeadSha256="a" * 64,
    )


def _compile_record(
    component: ProjectileStoryboardStateV1, record: AcceptedSemanticStoryboardRecordV1
):
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=component.problem_spec,
        semantic_scene=_semantic_scene(component),
    )
    return beat, compile_semantic_storyboard_checkpoint(beat, component)


def _compile_program(
    problem: PairedProjectileComparisonSpecV1,
    records: tuple[AcceptedSemanticStoryboardRecordV1, ...],
):
    checkpoints = [compile_semantic_storyboard_anchor(problem)]
    component = checkpoints[0].result_component
    for record in records:
        _, checkpoint = _compile_record(component, record)
        checkpoints.append(checkpoint)
        component = checkpoint.result_component
    return tuple(checkpoints)


def _node_map(nodes: tuple[SceneNode, ...]) -> dict[str, SceneNode]:
    return {node.id: node for node in nodes}


def _apply(checkpoint) -> tuple[SceneNode, ...]:
    order = [node.id for node in checkpoint.base_nodes]
    nodes = _node_map(checkpoint.base_nodes)
    for operation in checkpoint.patch.operations:
        assert isinstance(operation, PutSceneOperation)
        if operation.target_id not in nodes:
            order.append(operation.target_id)
        nodes[operation.target_id] = operation.node
    return tuple(nodes[node_id] for node_id in order)


def _bbox(node: SceneNode) -> tuple[float, float, float, float]:
    if isinstance(node, LatexTokenSceneNode):
        left = node.x - node.width / 2.0
        return left, node.y, left + node.width, node.y + node.height
    assert isinstance(node, LineSceneNode | PathSceneNode)
    xs = tuple(point[0] for point in node.points)
    ys = tuple(point[1] for point in node.points)
    stroke_padding = node.style.stroke_width / 2.0
    return (
        min(xs) - stroke_padding,
        min(ys) - stroke_padding,
        max(xs) + stroke_padding,
        max(ys) + stroke_padding,
    )


def _assert_inside_viewport(node: SceneNode, viewport) -> None:
    left, top, right, bottom = _bbox(node)
    safety_padding = 12.0
    assert left >= viewport.x + safety_padding - 1e-9
    assert top >= viewport.y + safety_padding - 1e-9
    assert right <= viewport.x + viewport.width - safety_padding + 1e-9
    assert bottom <= viewport.y + viewport.height - safety_padding + 1e-9


def test_anchor_is_one_exact_provider_free_thirteen_put_blueprint() -> None:
    problem = _problem()
    checkpoint = compile_semantic_storyboard_anchor(problem)

    assert checkpoint.checkpoint_id == STORYBOARD_ANCHOR_CHECKPOINT_ID
    assert checkpoint.base_component is None
    assert checkpoint.base_nodes == ()
    assert checkpoint.result_component == ProjectileStoryboardStateV1(problemSpec=problem)
    assert checkpoint.result_component.accepted_records == ()
    assert tuple(operation.target_id for operation in checkpoint.patch.operations) == ANCHOR_TARGETS
    assert len(checkpoint.patch.operations) == MAX_STORYBOARD_ANCHOR_OPERATIONS == 13
    assert checkpoint.patch.patch_id == f"{PREFIX}cp_anchor"
    assert checkpoint.patch.narration == (
        "Same launch speed, two angles. Watch how path, landing range, height, "
        "and flight time compare."
    )
    assert _apply(checkpoint) == checkpoint.result_nodes
    assert checkpoint.result_nodes == materialize_semantic_storyboard_nodes(
        checkpoint.result_component
    )
    assert checkpoint.presentation.base_viewports == checkpoint.presentation.result_viewports
    assert checkpoint.presentation.checkpoint_narration == checkpoint.patch.narration
    assert checkpoint.presentation.transient_free is True
    assert tuple(cue.cue for cue in checkpoint.choreography.phase.cues) == (
        "enter",
        "emphasize",
        "focus",
    )
    assert checkpoint.choreography.phase.duration_ms == 800
    assert checkpoint.choreography.phase.hold_after_ms == 250


def test_all_nine_comparisons_use_the_locked_uniform_si_geometry() -> None:
    lower_record = _record("trace", trajectoryId="lower_angle")
    higher_record = _record("trace", trajectoryId="higher_angle")
    for problem in SUPPORTED_COMPARISONS:
        checkpoints = _compile_program(problem, (lower_record, higher_record))
        nodes = _node_map(checkpoints[-1].result_nodes)
        lower = nodes[f"{PREFIX}trajectory_lower"]
        higher = nodes[f"{PREFIX}trajectory_higher"]
        assert isinstance(lower, PathSceneNode)
        assert isinstance(higher, PathSceneNode)
        assert len(lower.points) == len(higher.points) == 65

        raw = []
        for angle_deg in problem.angles_deg:
            angle = math.radians(angle_deg)
            raw.append(
                (
                    problem.speed_mps**2 * math.sin(2.0 * angle) / 10.0,
                    problem.speed_mps**2 * math.sin(angle) ** 2 / 20.0,
                )
            )
        if problem.has_complementary_angles:
            raw[1] = (raw[0][0], raw[1][1])
        scale = min(
            12.0, 476.0 / max(value[0] for value in raw), 240.0 / max(value[1] for value in raw)
        )

        for path, (range_m, height_m) in zip((lower, higher), raw, strict=True):
            for index, point in enumerate(path.points):
                u = index / 64.0
                assert point == pytest.approx(
                    (72.0 + scale * range_m * u, 468.0 - scale * 4.0 * height_m * u * (1.0 - u)),
                    abs=1e-9,
                )
            assert path.points[0] == (72.0, 468.0)
            assert path.points[-1][0] <= 548.0 + 1e-9
            assert min(point[1] for point in path.points) >= 228.0 - 1e-9
        if problem.has_complementary_angles:
            assert lower.points[-1] == higher.points[-1]


def test_each_atomic_visual_uses_its_exact_paint_manifest_and_budget() -> None:
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
    cases = (
        (_problem(), (formula,), ("range_formula",)),
        (_problem(), (complementary,), ("complementary_angles",)),
        (_problem(), (lower,), ("trajectory_lower", "projectile_marker_lower")),
        (_problem(), (higher,), ("trajectory_higher", "projectile_marker_higher")),
        (
            _problem(),
            (lower, higher, equal_visual),
            (
                "range_bracket_lower",
                "range_bracket_higher",
                "landing_ring_lower",
                "landing_ring_higher",
                "range_value_lower",
                "range_value_higher",
                "range_relation",
            ),
        ),
        (_problem(), (formula, complementary, equal_analytic), ("sine_relation", "range_relation")),
        (
            _problem(),
            (lower, higher, apex),
            (
                "height_bracket_lower",
                "height_bracket_higher",
                "apex_ring_lower",
                "apex_ring_higher",
                "height_value_lower",
                "height_value_higher",
                "height_relation",
            ),
        ),
        (
            _problem(),
            (lower, higher, flight),
            ("flight_time_lower", "flight_time_higher", "flight_relation"),
        ),
        (
            _problem((30, 45)),
            (lower, higher, unequal_visual),
            (
                "range_bracket_lower",
                "range_bracket_higher",
                "landing_ring_lower",
                "landing_ring_higher",
                "range_value_lower",
                "range_value_higher",
                "range_relation",
            ),
        ),
        (
            _problem((30, 45)),
            (formula, unequal_analytic),
            ("sine_relation", "range_relation"),
        ),
    )
    for problem, records, expected_suffixes in cases:
        checkpoint = _compile_program(problem, records)[-1]
        assert tuple(operation.target_id for operation in checkpoint.patch.operations) == tuple(
            PREFIX + suffix for suffix in expected_suffixes
        )
        assert len(checkpoint.patch.operations) <= MAX_STORYBOARD_BEAT_OPERATIONS == 7
        assert len(checkpoint.result_component.accepted_records) == len(records)
        assert checkpoint.result_component.accepted_records[-1] is records[-1]


def test_one_record_never_redraws_prerequisites_or_prior_nodes() -> None:
    formula = _record("reveal", conceptId="range_formula")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    apex = _record(
        "relate",
        claimId="higher_apex",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    checkpoints = _compile_program(_problem(), (formula, lower, higher, apex))

    for previous, current in pairwise(checkpoints):
        targets = {operation.target_id for operation in current.patch.operations}
        previous_nodes = _node_map(previous.result_nodes)
        current_nodes = _node_map(current.result_nodes)
        assert current.base_nodes == previous.result_nodes
        assert all(
            current_nodes[node_id] == node
            for node_id, node in previous_nodes.items()
            if node_id not in targets
        )
        assert _apply(current) == current.result_nodes
        assert current.result_nodes == materialize_semantic_storyboard_nodes(
            current.result_component
        )

    formula_checkpoint = checkpoints[1]
    formula_ids = set(_node_map(formula_checkpoint.result_nodes))
    assert f"{PREFIX}trajectory_lower" not in formula_ids
    assert f"{PREFIX}complementary_angles" not in formula_ids
    assert f"{PREFIX}height_relation" not in formula_ids


def test_complementary_range_rings_are_concentric_and_copy_never_claims_same_time() -> None:
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    equal = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    checkpoint = _compile_program(_problem(), (lower, higher, equal))[-1]
    nodes = _node_map(checkpoint.result_nodes)
    lower_ring = nodes[f"{PREFIX}landing_ring_lower"]
    higher_ring = nodes[f"{PREFIX}landing_ring_higher"]
    assert isinstance(lower_ring, PathSceneNode)
    assert isinstance(higher_ring, PathSceneNode)

    def centre(path: PathSceneNode) -> tuple[float, float]:
        return (
            (min(point[0] for point in path.points) + max(point[0] for point in path.points)) / 2.0,
            (min(point[1] for point in path.points) + max(point[1] for point in path.points)) / 2.0,
        )

    assert centre(lower_ring) == pytest.approx(centre(higher_ring), abs=1e-12)
    assert lower_ring.style.fill == "hsl(var(--amber))"
    assert higher_ring.style.fill == "transparent"
    assert max(point[0] for point in lower_ring.points) - centre(lower_ring)[0] == pytest.approx(
        5.0
    )
    assert max(point[0] for point in higher_ring.points) - centre(higher_ring)[0] == pytest.approx(
        8.0
    )
    caption = checkpoint.patch.narration.lower()
    assert "same range coordinate" in caption
    assert "same time" not in caption
    assert "together" not in caption


def test_server_owned_captions_use_exact_values_and_evidence_branch() -> None:
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    equal_analytic = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["range_formula", "complementary_angles"],
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
    checkpoints = _compile_program(
        _problem(),
        (formula, complementary, equal_analytic, lower, higher, apex, flight),
    )
    captions = tuple(checkpoint.patch.narration for checkpoint in checkpoints)
    assert captions[1] == (
        "For equal launch and landing heights, range is controlled by "
        r"\(R(\theta)=v_0^2\sin(2\theta)/g\)."
    )
    assert captions[2] == (
        "30° and 60° are complementary, so their doubled angles are supplementary."
    )
    assert captions[3] == (r"\(\sin(2·30°)=\sin(2·60°)\), so the range law gives equal ranges.")
    assert captions[4] == "The amber marker traces the 30° trajectory from launch to impact."
    assert captions[5] == "The lavender marker traces the 60° trajectory from launch to impact."
    assert captions[6] == "The 60° arc reaches 15 metres, above the 30° arc at 5 metres."
    assert captions[7] == ("The 60° launch remains airborne for 3.46 seconds, versus 2 seconds.")


def test_dynamic_tokens_use_browser_measured_safe_frames_and_sizes() -> None:
    formula = _record("reveal", conceptId="range_formula")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    for problem in SUPPORTED_COMPARISONS:
        range_record = _record(
            "relate",
            claimId="equal_range" if problem.has_complementary_angles else "unequal_range",
            evidenceIds=(
                ["range_formula", "complementary_angles"]
                if problem.has_complementary_angles
                else ["range_formula"]
            ),
        )
        analytic_prefix = (
            (formula, _record("reveal", conceptId="complementary_angles"), range_record)
            if problem.has_complementary_angles
            else (formula, range_record)
        )
        analytic_nodes = _node_map(_compile_program(problem, analytic_prefix)[-1].result_nodes)
        visual_nodes = _node_map(
            _compile_program(
                problem,
                (
                    lower,
                    higher,
                    _record(
                        "relate",
                        claimId="equal_range"
                        if problem.has_complementary_angles
                        else "unequal_range",
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
                ),
            )[-1].result_nodes
        )
        nodes = {**analytic_nodes, **visual_nodes}
        givens = nodes[f"{PREFIX}givens"]
        range_formula = nodes[f"{PREFIX}range_formula"]
        assert isinstance(givens, LatexTokenSceneNode)
        assert isinstance(range_formula, LatexTokenSceneNode)
        assert givens.x == range_formula.x == 690.0
        assert givens.width == range_formula.width == 188.0
        assert givens.height == 64.0
        assert r"\begin{aligned}" in givens.latex
        assert r"\\" in givens.latex
        assert givens.style.font_size == 14.0
        assert range_formula.style.font_size == 19.0

        for suffix in (
            "range_value_lower",
            "range_value_higher",
            "height_value_lower",
            "height_value_higher",
            "flight_time_lower",
            "flight_time_higher",
        ):
            token = nodes[f"{PREFIX}{suffix}"]
            assert isinstance(token, LatexTokenSceneNode)
            assert token.style.font_size == 16.0
        for suffix in ("sine_relation", "flight_relation"):
            token = nodes[f"{PREFIX}{suffix}"]
            assert isinstance(token, LatexTokenSceneNode)
            assert token.style.font_size == 16.0
        for suffix in ("flight_time_lower", "flight_time_higher", "flight_relation"):
            token = nodes[f"{PREFIX}{suffix}"]
            assert isinstance(token, LatexTokenSceneNode)
            assert token.height == 30.0

        for node in nodes.values():
            if isinstance(node, LatexTokenSceneNode) and node.x == 690.0:
                left, top, right, bottom = _bbox(node)
                assert 582.0 <= left < right <= 800.0
                assert 80.0 <= top < bottom <= 600.0


def test_cues_use_purposeful_timing_and_stay_inside_both_layouts() -> None:
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    equal = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
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
    checkpoints = _compile_program(
        _problem(speed=30),
        (formula, complementary, lower, higher, equal, apex, flight),
    )
    expected_timings = (
        (800, 250, "ease_out_quart"),
        (650, 300, "ease_out_quart"),
        (650, 300, "ease_out_quart"),
        (2250, 200, "ease_in_out"),
        (3200, 200, "ease_in_out"),
        (1000, 450, "ease_out_quint"),
        (1100, 450, "ease_out_quart"),
        (900, 400, "ease_out_quart"),
    )
    for checkpoint, expected in zip(checkpoints, expected_timings, strict=True):
        phase = checkpoint.choreography.phase
        assert (phase.duration_ms, phase.hold_after_ms, phase.easing.value) == expected
        assert phase.hold_after_ms <= 550
        assert all(cue.cue != "exit" for cue in phase.cues)
        nodes = _node_map(checkpoint.result_nodes)
        subject_ids: set[str] = set()
        for cue in phase.cues:
            if isinstance(cue, EmphasizeCueV1 | FocusCueV1):
                subject_ids.update(cue.target_ids)
            elif isinstance(cue, TracePathCueV2):
                subject_ids.update((cue.path_id, cue.marker_id))
        for node_id in subject_ids:
            _assert_inside_viewport(
                nodes[node_id], checkpoint.presentation.result_viewports.cinematic
            )
            _assert_inside_viewport(
                nodes[node_id], checkpoint.presentation.result_viewports.compact
            )


def test_canonical_five_beat_pacing_stays_inside_the_eight_to_twelve_second_gate() -> None:
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    equal = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["range_formula", "complementary_angles"],
    )
    totals = []
    for speed in (20, 25, 30):
        checkpoints = _compile_program(
            _problem(speed=speed),
            (formula, complementary, lower, higher, equal),
        )[1:]
        authored_ms = sum(
            checkpoint.choreography.phase.duration_ms + checkpoint.choreography.phase.hold_after_ms
            for checkpoint in checkpoints
        )
        assert 8_000 <= authored_ms <= 12_000
        totals.append(authored_ms)
    assert totals == [8_209, 8_824, 9_400]


def test_evidence_specific_range_cameras_and_targets_do_not_fake_dependencies() -> None:
    formula = _record("reveal", conceptId="range_formula")
    complementary = _record("reveal", conceptId="complementary_angles")
    lower = _record("trace", trajectoryId="lower_angle")
    higher = _record("trace", trajectoryId="higher_angle")
    analytic = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["range_formula", "complementary_angles"],
    )
    visual = _record(
        "relate",
        claimId="equal_range",
        evidenceIds=["lower_trajectory", "higher_trajectory"],
    )
    analytic_checkpoint = _compile_program(_problem(), (formula, complementary, analytic))[-1]
    visual_checkpoint = _compile_program(_problem(), (lower, higher, visual))[-1]

    assert analytic_checkpoint.presentation.result_viewports.cinematic.model_dump() == {
        "v": 1,
        "x": 492.0,
        "y": 140.0,
        "width": 308.0,
        "height": 326.0,
    }
    assert visual_checkpoint.presentation.result_viewports.cinematic.model_dump() == {
        "v": 1,
        "x": 36.0,
        "y": 230.0,
        "width": 548.0,
        "height": 338.0,
    }
    analytic_targets = {operation.target_id for operation in analytic_checkpoint.patch.operations}
    visual_targets = {operation.target_id for operation in visual_checkpoint.patch.operations}
    assert analytic_targets == {f"{PREFIX}sine_relation", f"{PREFIX}range_relation"}
    assert f"{PREFIX}sine_relation" not in visual_targets
    assert not any("trajectory" in target for target in analytic_targets)


def test_compiler_rejects_tampered_route_frontiers_before_drawing() -> None:
    problem = _problem()
    anchor = compile_semantic_storyboard_anchor(problem)
    component = anchor.result_component
    record = _record("trace", trajectoryId="lower_angle")
    beat, _ = _compile_record(component, record)

    with pytest.raises(SemanticStoryboardCompilationError, match="ordinal"):
        compile_semantic_storyboard_checkpoint(beat.model_copy(update={"ordinal": 2}), component)
    with pytest.raises(SemanticStoryboardCompilationError, match="base program hash"):
        compile_semantic_storyboard_checkpoint(
            beat.model_copy(update={"base_program_sha256": "b" * 64}),
            component,
        )
    with pytest.raises(SemanticStoryboardCompilationError, match="result program hash"):
        compile_semantic_storyboard_checkpoint(
            beat.model_copy(update={"result_program_sha256": "b" * 64}),
            component,
        )
    mismatched = ProjectileStoryboardStateV1(problemSpec=_problem((30, 45)))
    with pytest.raises(SemanticStoryboardCompilationError, match="accepted storyboard"):
        compile_semantic_storyboard_checkpoint(beat, mismatched)


def test_compilation_is_byte_deterministic_and_within_wire_budget() -> None:
    records = (
        _record("trace", trajectoryId="higher_angle"),
        _record("trace", trajectoryId="lower_angle"),
        _record(
            "relate",
            claimId="equal_range",
            evidenceIds=["lower_trajectory", "higher_trajectory"],
        ),
    )
    left = _compile_program(_problem(speed=25), records)
    right = _compile_program(_problem(speed=25), records)
    assert left == right
    for checkpoint in left:
        assert (
            len(canonical_json_v1(checkpoint.patch.model_dump(mode="json", by_alias=True)))
            <= MAX_NDJSON_FRAME_BYTES
        )
        assert all(
            node.id.startswith(PREFIX) and len(node.id) <= 64 for node in checkpoint.result_nodes
        )


def test_complete_catalog_frontier_has_33_nodes_and_no_cue_exceeds_21_references() -> None:
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
    checkpoints = _compile_program(_problem(), records)
    assert len(checkpoints[-1].result_nodes) == 33
    reference_counts = []
    for checkpoint in checkpoints:
        reference_counts.append(
            sum(
                2 if isinstance(cue, TracePathCueV2) else len(cue.target_ids)
                for cue in checkpoint.choreography.phase.cues
            )
        )
    assert max(reference_counts) == 21
    assert reference_counts[0] == 21


def test_compiler_has_no_gate_17_or_prompt_specific_visual_dependency() -> None:
    source = inspect.getsource(semantic_storyboard_compiler)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any("projectile_motion_compiler" in module for module in imported_modules)
    assert not any("projectile_motion_verifier" in module for module in imported_modules)
    for forbidden in ("full_lesson", "compare_everything", "stage_to_suffix", "learner_prompt"):
        assert forbidden not in source


def test_runtime_type_guards_reject_cross_boundary_objects() -> None:
    with pytest.raises(TypeError, match="problem_spec"):
        compile_semantic_storyboard_anchor(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="component"):
        materialize_semantic_storyboard_nodes(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="beat"):
        compile_semantic_storyboard_checkpoint(object(), object())  # type: ignore[arg-type]

    problem = _problem()
    anchor = compile_semantic_storyboard_anchor(problem)
    record = _record("trace", trajectoryId="lower_angle")
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=problem,
        semantic_scene=_semantic_scene(anchor.result_component),
    )
    assert isinstance(beat, RoutedSemanticStoryboardBeatV1)
    with pytest.raises(TypeError, match="base_component"):
        compile_semantic_storyboard_checkpoint(beat, object())  # type: ignore[arg-type]
