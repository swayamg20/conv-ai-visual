from __future__ import annotations

import ast
import inspect
from collections.abc import Callable

import pytest
from murmur.live_scene import semantic_compiler, semantic_verifier
from murmur.live_scene.contracts import LatexSceneNode, PathSceneNode, SceneNode, TextSceneNode
from murmur.live_scene.semantic_compiler import (
    SemanticCompilationError,
    compile_teaching_beat,
)
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    CompiledTeachingBeat,
    PythagoreanAreaIdentityState,
    PythagoreanStage,
    SemanticSceneState,
    TeachingBeatDraft,
)
from murmur.live_scene.semantic_verifier import (
    SemanticVerificationError,
    verify_pythagorean_realization,
)


def _beat(
    stage: PythagoreanStage = PythagoreanStage.IDENTITY,
    *,
    beat_id: str = "beat-areas",
    component_id: str = "areas",
) -> TeachingBeatDraft:
    return TeachingBeatDraft.model_validate(
        {
            "v": 1,
            "beatId": beat_id,
            "narration": "Relate the three square areas.",
            "act": "derive",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": component_id,
                "revealThrough": stage.value,
            },
        }
    )


def _base_with_prefix(prefix_length: int, *, component_id: str = "areas") -> SemanticSceneState:
    if prefix_length == 0:
        return SemanticSceneState(revision=11)
    return SemanticSceneState(
        revision=11,
        components=(
            PythagoreanAreaIdentityState(
                id=component_id,
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:prefix_length],
            ),
        ),
    )


def _nodes(compiled: CompiledTeachingBeat) -> tuple[SceneNode, ...]:
    return tuple(atom.patch.operations[0].node for atom in compiled.atoms)


def _serialized(nodes: tuple[SceneNode, ...]) -> tuple[dict[str, object], ...]:
    return tuple(node.model_dump(mode="json", by_alias=True) for node in nodes)


def _replace_node(
    nodes: tuple[SceneNode, ...],
    index: int,
    replacement: SceneNode,
) -> tuple[SceneNode, ...]:
    return (*nodes[:index], replacement, *nodes[index + 1 :])


def test_compilation_is_byte_for_byte_deterministic_and_uses_no_entropy_sources() -> None:
    beat = _beat()
    base = SemanticSceneState(revision=0)

    first = compile_teaching_beat(beat, base)
    second = compile_teaching_beat(beat, base)

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)

    tree = ast.parse(inspect.getsource(semantic_compiler))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots.isdisjoint({"datetime", "random", "secrets", "time", "uuid"})


def test_verifier_is_a_separate_serialized_contract_boundary() -> None:
    tree = ast.parse(inspect.getsource(semantic_verifier))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "murmur.live_scene.semantic_compiler" not in imported_modules

    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    with pytest.raises(SemanticVerificationError, match="only serialized"):
        verify_pythagorean_realization("areas", _nodes(compiled))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("stage", "expected_count"),
    [
        (PythagoreanStage.TRIANGLE, 1),
        (PythagoreanStage.AREAS, 7),
        (PythagoreanStage.IDENTITY, 8),
    ],
)
def test_each_stage_lowers_to_the_exact_ordered_one_put_prefix(
    stage: PythagoreanStage,
    expected_count: int,
) -> None:
    compiled = compile_teaching_beat(_beat(stage), SemanticSceneState(revision=3))

    assert tuple(atom.role for atom in compiled.atoms) == PYTHAGOREAN_ROLE_ORDER[:expected_count]
    assert compiled.result_scene.revision == 3 + expected_count
    for atom in compiled.atoms:
        assert atom.atom_id == f"areas__atom_{atom.role.value}"
        assert atom.patch.patch_id == atom.atom_id
        assert len(atom.patch.operations) == 1
        assert atom.patch.operations[0].op == "put"
        assert atom.patch.operations[0].target_id == f"areas__{atom.role.value}"
        assert atom.receipt.node_id == atom.patch.operations[0].target_id
        assert atom.receipt.verified is True
        assert atom.receipt.issuer == "semantic_verifier"


def test_child_and_atom_ids_remain_stable_across_target_stages() -> None:
    compiled_by_stage = {
        stage: compile_teaching_beat(_beat(stage), SemanticSceneState(revision=0))
        for stage in PythagoreanStage
    }
    final = compiled_by_stage[PythagoreanStage.IDENTITY]

    for stage, expected_count in (
        (PythagoreanStage.TRIANGLE, 1),
        (PythagoreanStage.AREAS, 7),
        (PythagoreanStage.IDENTITY, 8),
    ):
        compiled = compiled_by_stage[stage]
        assert tuple(atom.atom_id for atom in compiled.atoms) == tuple(
            atom.atom_id for atom in final.atoms[:expected_count]
        )
        assert tuple(node.id for node in _nodes(compiled)) == tuple(
            node.id for node in _nodes(final)[:expected_count]
        )


def test_resume_from_every_committed_role_emits_only_the_exact_missing_suffix() -> None:
    complete = compile_teaching_beat(_beat(), _base_with_prefix(0))

    for prefix_length in range(len(PYTHAGOREAN_ROLE_ORDER) + 1):
        resumed = compile_teaching_beat(_beat(), _base_with_prefix(prefix_length))

        assert tuple(atom.role for atom in resumed.atoms) == PYTHAGOREAN_ROLE_ORDER[prefix_length:]
        assert tuple(atom.atom_id for atom in resumed.atoms) == tuple(
            atom.atom_id for atom in complete.atoms[prefix_length:]
        )
        assert resumed.result_scene.components[0].revealed_roles == PYTHAGOREAN_ROLE_ORDER
        assert resumed.result_scene.revision == 11 + len(resumed.atoms)


def test_noop_is_empty_and_a_backward_target_is_rejected() -> None:
    complete = _base_with_prefix(len(PYTHAGOREAN_ROLE_ORDER))
    noop = compile_teaching_beat(_beat(), complete)
    assert noop.atoms == ()
    assert noop.result_scene == complete

    with pytest.raises(SemanticCompilationError, match="cannot move backward"):
        compile_teaching_beat(_beat(PythagoreanStage.TRIANGLE), complete)


def test_compiler_owns_the_geometry_labels_and_exact_identity() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    nodes = _nodes(compiled)

    triangle = nodes[0]
    assert isinstance(triangle, PathSceneNode)
    assert triangle.points == ((320.0, 260.0), (440.0, 260.0), (320.0, 420.0))

    for index, text in ((2, "a²"), (4, "b²"), (6, "c²")):
        label = nodes[index]
        assert isinstance(label, TextSceneNode)
        assert label.text == text

    identity = nodes[7]
    assert isinstance(identity, LatexSceneNode)
    assert identity.latex == "a^2+b^2=c^2"
    assert identity.x + 500.0 <= 800.0
    assert identity.y + 120.0 <= 600.0

    receipts = verify_pythagorean_realization("areas", _serialized(nodes))
    assert tuple(receipt.role for receipt in receipts) == PYTHAGOREAN_ROLE_ORDER


def test_semantic_beat_is_at_least_eighty_percent_smaller_than_raw_patches() -> None:
    beat = _beat()
    compiled = compile_teaching_beat(beat, SemanticSceneState(revision=0))

    semantic_bytes = len(beat.model_dump_json(by_alias=True).encode("utf-8"))
    raw_patch_bytes = sum(
        len(atom.patch.model_dump_json(by_alias=True).encode("utf-8")) for atom in compiled.atoms
    )

    assert semantic_bytes <= raw_patch_bytes * 0.2


def test_independent_verifier_fails_closed_for_every_obligation_class() -> None:
    valid = _nodes(compile_teaching_beat(_beat(), SemanticSceneState(revision=0)))
    triangle = valid[0]
    square_a = valid[1]
    square_b = valid[3]
    label_a = valid[2]
    label_b = valid[4]
    identity = valid[7]
    assert isinstance(triangle, PathSceneNode)
    assert isinstance(square_a, PathSceneNode)
    assert isinstance(square_b, PathSceneNode)
    assert isinstance(label_a, TextSceneNode)
    assert isinstance(label_b, TextSceneNode)
    assert isinstance(identity, LatexSceneNode)

    corruptions: tuple[
        tuple[str, Callable[[tuple[SceneNode, ...]], tuple[SceneNode, ...]], str], ...
    ] = (
        (
            "right angle",
            lambda nodes: _replace_node(
                nodes,
                0,
                triangle.model_copy(
                    update={"points": ((320.0, 260.0), (440.0, 260.0), (330.0, 420.0))}
                ),
            ),
            "right angle",
        ),
        (
            "square edge",
            lambda nodes: _replace_node(
                nodes,
                1,
                square_a.model_copy(
                    update={
                        "points": (
                            (320.0, 260.0),
                            (440.0, 260.0),
                            (450.0, 140.0),
                            (320.0, 140.0),
                        )
                    }
                ),
            ),
            "edge length",
        ),
        (
            "inward square",
            lambda nodes: _replace_node(
                nodes,
                1,
                square_a.model_copy(
                    update={
                        "points": (
                            (320.0, 260.0),
                            (440.0, 260.0),
                            (440.0, 380.0),
                            (320.0, 380.0),
                        )
                    }
                ),
            ),
            "outside the triangle",
        ),
        (
            "hypotenuse ratio",
            lambda nodes: _replace_node(
                nodes,
                0,
                triangle.model_copy(
                    update={"points": ((320.0, 260.0), (440.0, 260.0), (320.0, 460.0))}
                ),
            ),
            "3:4:5 ratio",
        ),
        (
            "duplicate id",
            lambda nodes: _replace_node(
                nodes,
                3,
                square_b.model_copy(update={"id": "areas__triangle"}),
            ),
            "node ids must be unique",
        ),
        (
            "stable id",
            lambda nodes: _replace_node(
                nodes,
                2,
                label_a.model_copy(update={"id": "areas__model_chosen_id"}),
            ),
            "stable role prefix",
        ),
        (
            "label containment",
            lambda nodes: _replace_node(
                nodes,
                2,
                label_a.model_copy(update={"x": 200.0}),
            ),
            "contained by its square",
        ),
        (
            "equation",
            lambda nodes: _replace_node(
                nodes,
                7,
                identity.model_copy(update={"latex": "a^2+b^2=c"}),
            ),
            "exact required equation",
        ),
        (
            "latex viewport",
            lambda nodes: _replace_node(
                nodes,
                7,
                identity.model_copy(update={"x": 301.0}),
            ),
            "board width",
        ),
        (
            "label separation",
            lambda nodes: _replace_node(
                _replace_node(
                    nodes,
                    2,
                    label_a.model_copy(update={"x": 344.0, "y": 241.25}),
                ),
                4,
                label_b.model_copy(update={"x": 296.0, "y": 278.75}),
            ),
            "label boxes intersect",
        ),
    )

    for _name, corrupt, message in corruptions:
        with pytest.raises(SemanticVerificationError, match=message):
            verify_pythagorean_realization("areas", _serialized(corrupt(valid)))


def test_compilation_preflights_the_whole_realization_before_creating_any_atom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_builder = semantic_compiler._build_pythagorean_nodes
    original_atom_type = semantic_compiler.CompiledVisualAtom
    attempted_atoms: list[str] = []

    def corrupted_builder(component_id: str) -> tuple[SceneNode, ...]:
        nodes = original_builder(component_id)
        identity = nodes[-1]
        assert isinstance(identity, LatexSceneNode)
        return _replace_node(
            nodes,
            len(nodes) - 1,
            identity.model_copy(update={"latex": "unverified"}),
        )

    def atom_spy(**values: object) -> object:
        attempted_atoms.append(str(values["atom_id"]))
        return original_atom_type(**values)

    monkeypatch.setattr(semantic_compiler, "_build_pythagorean_nodes", corrupted_builder)
    monkeypatch.setattr(semantic_compiler, "CompiledVisualAtom", atom_spy)

    with pytest.raises(SemanticVerificationError, match="exact required equation"):
        compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    assert attempted_atoms == []
