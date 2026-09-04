"""Focused tests for deterministic visual-act compiler lowering."""

from __future__ import annotations

import re
from copy import deepcopy

import pytest
from murmur.live_scene.contracts import MAX_SAFE_SEQUENCE
from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    ContinueVisualDecision,
    PythagoreanAreaIdentityState,
    PythagoreanStage,
    SemanticSceneState,
    StartVisualDecision,
    TeachingAct,
    TeachingBeatDraft,
)
from murmur.live_scene.visual_act_lowering import lower_resolved_visual_act
from murmur.live_scene.visual_act_router import resolve_visual_act

_EXPECTED_CONTENT = {
    PythagoreanStage.TRIANGLE: (
        TeachingAct.INTRODUCE,
        "Start with the right triangle.",
    ),
    PythagoreanStage.AREAS: (
        TeachingAct.DERIVE,
        "Compare the squares built on the triangle's three sides.",
    ),
    PythagoreanStage.IDENTITY: (
        TeachingAct.CONNECT,
        "Connect the three square areas into the Pythagorean relationship.",
    ),
}


@pytest.mark.parametrize("stage", list(PythagoreanStage))
def test_lowers_each_start_target_to_fixed_server_authored_beat(
    stage: PythagoreanStage,
) -> None:
    scene = SemanticSceneState(revision=0)
    resolved = resolve_visual_act(StartVisualDecision(target_stage=stage), scene)
    assert resolved is not None
    before = deepcopy(resolved)

    beat = lower_resolved_visual_act(resolved, generation=7)

    expected_act, expected_narration = _EXPECTED_CONTENT[stage]
    assert isinstance(beat, TeachingBeatDraft)
    assert beat.beat_id == "route-7"
    assert beat.act is expected_act
    assert beat.narration == expected_narration
    assert beat.directive.kind == "pythagorean_area_identity"
    assert beat.directive.id == "areas"
    assert beat.directive.reveal_through is stage
    assert resolved == before


def test_continue_reuses_resolved_component_and_compiles_only_its_missing_suffix() -> None:
    scene = SemanticSceneState(
        revision=7,
        components=(
            PythagoreanAreaIdentityState(
                id="areas-2",
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:7],
            ),
        ),
        certificate_head_sha256="0" * 64,
    )
    resolved = resolve_visual_act(
        ContinueVisualDecision(component_id="areas-2", target_stage=PythagoreanStage.IDENTITY),
        scene,
    )
    assert resolved is not None

    beat = lower_resolved_visual_act(resolved, generation=8)
    compiled = compile_teaching_beat(beat, scene)

    assert beat.directive.id == "areas-2"
    assert beat.directive.reveal_through is PythagoreanStage.IDENTITY
    assert tuple(atom.role for atom in compiled.atoms) == PYTHAGOREAN_ROLE_ORDER[7:]


def test_server_beat_id_is_deterministic_unique_by_generation_and_bounded() -> None:
    resolved = resolve_visual_act(
        StartVisualDecision(target_stage=PythagoreanStage.TRIANGLE),
        SemanticSceneState(revision=0),
    )
    assert resolved is not None

    first = lower_resolved_visual_act(resolved, generation=1)
    repeated = lower_resolved_visual_act(resolved, generation=1)
    final = lower_resolved_visual_act(resolved, generation=MAX_SAFE_SEQUENCE)

    assert first == repeated
    assert first.beat_id == "route-1"
    assert final.beat_id == "route-1fffffffffffff"
    assert first.beat_id != final.beat_id
    assert len(final.beat_id) <= 32
    assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", final.beat_id)


@pytest.mark.parametrize("generation", [0, MAX_SAFE_SEQUENCE + 1, -1])
def test_rejects_out_of_range_generation(generation: int) -> None:
    resolved = resolve_visual_act(
        StartVisualDecision(target_stage=PythagoreanStage.TRIANGLE),
        SemanticSceneState(revision=0),
    )
    assert resolved is not None

    with pytest.raises(ValueError, match="generation must be between"):
        lower_resolved_visual_act(resolved, generation=generation)


@pytest.mark.parametrize("generation", [True, 1.0, "1", None])
def test_rejects_non_integer_generation(generation: object) -> None:
    resolved = resolve_visual_act(
        StartVisualDecision(target_stage=PythagoreanStage.TRIANGLE),
        SemanticSceneState(revision=0),
    )
    assert resolved is not None

    with pytest.raises(TypeError, match="generation must be an integer"):
        lower_resolved_visual_act(resolved, generation=generation)  # type: ignore[arg-type]


def test_abstain_cannot_cross_the_lowering_boundary() -> None:
    with pytest.raises(TypeError, match="ResolvedVisualAct"):
        lower_resolved_visual_act(None, generation=1)  # type: ignore[arg-type]
