from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.contracts import LIVE_SCENE_SCHEMA_VERSION, MAX_SCENE_NARRATION_CHARS
from murmur.live_scene.semantic_contracts import (
    MAX_SEMANTIC_ID_CHARS,
    PYTHAGOREAN_ROLE_ORDER,
    PYTHAGOREAN_STAGE_ROLES,
    CompiledTeachingBeat,
    CompiledVisualAtom,
    PythagoreanAreaIdentityDirective,
    PythagoreanAreaIdentityState,
    PythagoreanRole,
    PythagoreanStage,
    SemanticSceneState,
    TeachingAct,
    TeachingBeatDraft,
    VerificationObligation,
    VerificationReceipt,
    roles_through,
)
from pydantic import ValidationError


def _beat(
    stage: PythagoreanStage | str = PythagoreanStage.IDENTITY,
    *,
    beat_id: str = "beat-identity",
    component_id: str = "area-identity",
) -> dict[str, object]:
    return {
        "v": LIVE_SCENE_SCHEMA_VERSION,
        "beatId": beat_id,
        "narration": "Relate the three square areas.",
        "act": "derive",
        "directive": {
            "kind": "pythagorean_area_identity",
            "id": component_id,
            "revealThrough": stage,
        },
    }


def _line(node_id: str) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "line",
        "presentation": {"enter": "draw", "exit": "fade"},
        "points": [[185, 405], [525, 405]],
        "style": {
            "stroke": "hsl(var(--lavender))",
            "strokeWidth": 4,
            "opacity": 1,
            "roughness": 0.75,
        },
    }


def _atom(
    role: PythagoreanRole,
    *,
    beat_id: str = "beat-identity",
    component_id: str = "area-identity",
) -> CompiledVisualAtom:
    atom_id = f"{beat_id}__{role.value}"
    node_id = f"{component_id}__{role.value}"
    return CompiledVisualAtom.model_validate(
        {
            "atomId": atom_id,
            "beatId": beat_id,
            "componentId": component_id,
            "role": role,
            "patch": {
                "v": LIVE_SCENE_SCHEMA_VERSION,
                "patchId": atom_id,
                "narration": "Relate the three square areas.",
                "operations": [{"op": "put", "node": _line(node_id)}],
            },
            "receipt": {
                "componentId": component_id,
                "role": role,
                "nodeId": node_id,
                "obligationCodes": ["stable_id", "board_bounds"],
                "verified": True,
            },
        }
    )


def _component(
    roles: tuple[PythagoreanRole, ...],
    *,
    component_id: str = "area-identity",
) -> PythagoreanAreaIdentityState:
    return PythagoreanAreaIdentityState(
        id=component_id,
        revealed_roles=roles,
    )


def test_model_authored_beat_has_only_the_deliberately_small_surface() -> None:
    assert set(TeachingBeatDraft.model_fields) == {
        "v",
        "beat_id",
        "narration",
        "act",
        "directive",
    }
    assert set(PythagoreanAreaIdentityDirective.model_fields) == {
        "kind",
        "id",
        "reveal_through",
    }

    beat = TeachingBeatDraft.model_validate(_beat())
    assert beat.model_dump(mode="json", by_alias=True) == _beat()
    assert beat.directive.reveal_through is PythagoreanStage.IDENTITY

    with pytest.raises(ValidationError, match="frozen"):
        beat.act = TeachingAct.EMPHASIZE


@pytest.mark.parametrize("act", list(TeachingAct))
def test_teaching_beat_accepts_each_closed_act(act: TeachingAct) -> None:
    payload = _beat()
    payload["act"] = act.value
    assert TeachingBeatDraft.model_validate(payload).act is act


def test_teaching_beat_rejects_unknown_acts_versions_and_excess_narration() -> None:
    invalid = _beat()
    invalid["act"] = "animate"
    with pytest.raises(ValidationError):
        TeachingBeatDraft.model_validate(invalid)

    invalid = _beat()
    invalid["v"] = LIVE_SCENE_SCHEMA_VERSION + 1
    with pytest.raises(ValidationError):
        TeachingBeatDraft.model_validate(invalid)

    invalid = _beat()
    invalid["narration"] = "n" * (MAX_SCENE_NARRATION_CHARS + 1)
    with pytest.raises(ValidationError, match="at most"):
        TeachingBeatDraft.model_validate(invalid)


@pytest.mark.parametrize(
    "forbidden",
    [
        {"x": 100, "y": 200},
        {"style": {"fill": "red"}},
        {"equation": "a^2+b^2=c^2"},
        {"childIds": ["model-child"]},
        {"points": [[0, 0], [1, 1]]},
        {"verificationReceipt": {"verified": True}},
    ],
)
def test_directive_rejects_every_server_owned_concern(forbidden: dict[str, object]) -> None:
    payload = _beat()
    directive = payload["directive"]
    assert isinstance(directive, dict)
    directive.update(forbidden)

    with pytest.raises(ValidationError, match="Extra inputs"):
        TeachingBeatDraft.model_validate(payload)


def test_teaching_beat_rejects_server_lifecycle_and_compilation_fields() -> None:
    for field in (
        "generation",
        "sequence",
        "baseRevision",
        "resultRevision",
        "atoms",
        "receipt",
    ):
        payload = _beat()
        payload[field] = 1
        with pytest.raises(ValidationError, match="Extra inputs"):
            TeachingBeatDraft.model_validate(payload)


@pytest.mark.parametrize("stage", list(PythagoreanStage))
def test_directive_accepts_each_named_stage(stage: PythagoreanStage) -> None:
    beat = TeachingBeatDraft.model_validate(_beat(stage))
    assert beat.directive.reveal_through is stage
    assert roles_through(stage) == PYTHAGOREAN_STAGE_ROLES[stage]


def test_directive_rejects_unknown_stage_and_kind() -> None:
    payload = _beat("proof")
    with pytest.raises(ValidationError):
        TeachingBeatDraft.model_validate(payload)

    payload = _beat()
    directive = payload["directive"]
    assert isinstance(directive, dict)
    directive["kind"] = "freeform_svg"
    with pytest.raises(ValidationError):
        TeachingBeatDraft.model_validate(payload)


def test_semantic_identifiers_are_strict_and_leave_room_for_child_suffixes() -> None:
    longest_valid_id = "P" + "a" * (MAX_SEMANTIC_ID_CHARS - 1)
    beat = TeachingBeatDraft.model_validate(
        _beat(beat_id=longest_valid_id, component_id=longest_valid_id)
    )
    assert len(beat.beat_id) == MAX_SEMANTIC_ID_CHARS
    assert max(
        len(f"{beat.directive.id}__{role.value}") for role in PYTHAGOREAN_ROLE_ORDER
    ) <= 64

    for invalid_id in (
        "P" + "a" * MAX_SEMANTIC_ID_CHARS,
        "1identity",
        "identity with spaces",
        " identity",
        "",
    ):
        with pytest.raises(ValidationError):
            TeachingBeatDraft.model_validate(_beat(beat_id=invalid_id))
        with pytest.raises(ValidationError):
            TeachingBeatDraft.model_validate(_beat(component_id=invalid_id))


def test_pythagorean_role_order_and_stage_boundaries_are_exact() -> None:
    assert tuple(role.value for role in PYTHAGOREAN_ROLE_ORDER) == (
        "triangle",
        "square_a",
        "label_a2",
        "square_b",
        "label_b2",
        "square_c",
        "label_c2",
        "identity",
    )
    assert roles_through(PythagoreanStage.TRIANGLE) == PYTHAGOREAN_ROLE_ORDER[:1]
    assert roles_through(PythagoreanStage.AREAS) == PYTHAGOREAN_ROLE_ORDER[:7]
    assert roles_through(PythagoreanStage.IDENTITY) == PYTHAGOREAN_ROLE_ORDER
    with pytest.raises(TypeError):
        PYTHAGOREAN_STAGE_ROLES[PythagoreanStage.TRIANGLE] = ()  # type: ignore[index]


@pytest.mark.parametrize("prefix_length", range(len(PYTHAGOREAN_ROLE_ORDER) + 1))
def test_component_state_accepts_every_interruption_prefix(prefix_length: int) -> None:
    roles = PYTHAGOREAN_ROLE_ORDER[:prefix_length]
    state = PythagoreanAreaIdentityState.model_validate(
        {
            "kind": "pythagorean_area_identity",
            "id": "area-identity",
            "revealedRoles": [role.value for role in roles],
        }
    )
    assert state.revealed_roles == roles
    assert state.model_dump(mode="json", by_alias=True)["revealedRoles"] == [
        role.value for role in roles
    ]


@pytest.mark.parametrize(
    "roles",
    [
        ["square_a"],
        ["triangle", "label_a2"],
        ["triangle", "square_a", "square_a"],
        ["square_a", "triangle"],
    ],
)
def test_component_state_rejects_gaps_duplicates_and_reordering(roles: list[str]) -> None:
    with pytest.raises(ValidationError, match="ordered Pythagorean role prefix"):
        PythagoreanAreaIdentityState.model_validate(
            {"id": "area-identity", "revealedRoles": roles}
        )


def test_semantic_scene_has_strict_revision_unique_ids_and_immutable_components() -> None:
    component = _component(PYTHAGOREAN_ROLE_ORDER[:1])
    scene = SemanticSceneState(revision=3, components=(component,))
    assert scene.components == (component,)

    with pytest.raises(ValidationError, match="unique"):
        SemanticSceneState(revision=3, components=(component, component))
    with pytest.raises(ValidationError):
        SemanticSceneState.model_validate({"revision": "3", "components": []})
    with pytest.raises(ValidationError, match="frozen"):
        scene.revision = 4


def test_verification_receipt_is_positive_bounded_and_unique() -> None:
    assert set(VerificationReceipt.model_fields) == {
        "issuer",
        "component_id",
        "role",
        "node_id",
        "obligation_codes",
        "verified",
    }
    receipt = VerificationReceipt.model_validate(
        {
            "componentId": "area-identity",
            "role": "triangle",
            "nodeId": "area-identity__triangle",
            "obligationCodes": [
                obligation.value for obligation in VerificationObligation
            ],
        }
    )
    assert receipt.verified is True
    wire = receipt.model_dump(mode="json", by_alias=True)
    assert wire["issuer"] == "semantic_compiler"
    assert wire["nodeId"] == "area-identity__triangle"

    invalid = receipt.model_dump(mode="json", by_alias=True)
    invalid["obligationCodes"] = []
    with pytest.raises(ValidationError, match="at least"):
        VerificationReceipt.model_validate(invalid)

    invalid["obligationCodes"] = ["stable_id", "stable_id"]
    with pytest.raises(ValidationError, match="must be unique"):
        VerificationReceipt.model_validate(invalid)

    invalid["obligationCodes"] = ["stable_id"]
    invalid["verified"] = False
    with pytest.raises(ValidationError):
        VerificationReceipt.model_validate(invalid)

    for unsupported_claim in ("narration", "materialized", "appliedAt", "sceneRevision"):
        invalid = receipt.model_dump(mode="json", by_alias=True)
        invalid[unsupported_claim] = True
        with pytest.raises(ValidationError, match="Extra inputs"):
            VerificationReceipt.model_validate(invalid)

    invalid = receipt.model_dump(mode="json", by_alias=True)
    invalid["issuer"] = "renderer"
    with pytest.raises(ValidationError):
        VerificationReceipt.model_validate(invalid)


def test_compiled_atom_binds_patch_target_and_receipt_metadata() -> None:
    atom = _atom(PythagoreanRole.SQUARE_A)
    wire = atom.model_dump(mode="json", by_alias=True)

    assert wire["atomId"] == "beat-identity__square_a"
    assert wire["patch"]["patchId"] == wire["atomId"]
    assert wire["patch"]["operations"][0]["node"]["id"] == "area-identity__square_a"
    assert wire["receipt"]["nodeId"] == "area-identity__square_a"
    with pytest.raises(ValidationError, match="frozen"):
        atom.role = PythagoreanRole.SQUARE_B


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda atom: atom["patch"].update(patchId="different-atom"),
            "patchId must equal atomId",
        ),
        (
            lambda atom: atom["patch"].update(
                operations=[{"op": "remove", "id": "area-identity__triangle"}]
            ),
            "exactly one put operation",
        ),
        (
            lambda atom: atom["receipt"].update(nodeId="area-identity__other"),
            "nodeId must match",
        ),
        (
            lambda atom: atom["receipt"].update(componentId="other-identity"),
            "componentId must match",
        ),
        (
            lambda atom: atom["receipt"].update(role="square_b"),
            "role must match",
        ),
    ],
)
def test_compiled_atom_rejects_unbound_metadata(mutate, message: str) -> None:
    payload = _atom(PythagoreanRole.SQUARE_A).model_dump(mode="json", by_alias=True)
    mutate(payload)
    with pytest.raises(ValidationError, match=message):
        CompiledVisualAtom.model_validate(payload)


@pytest.mark.parametrize("stage", list(PythagoreanStage))
def test_compiled_beat_accepts_every_exact_missing_suffix(stage: PythagoreanStage) -> None:
    target_roles = roles_through(stage)

    for prefix_length in range(len(target_roles) + 1):
        base_roles = target_roles[:prefix_length]
        base_components = () if prefix_length == 0 else (_component(base_roles),)
        atoms = tuple(_atom(role) for role in target_roles[prefix_length:])
        base_scene = SemanticSceneState(revision=11, components=base_components)
        result_scene = SemanticSceneState(
            revision=11 + len(atoms),
            components=(_component(target_roles),),
        )

        compiled = CompiledTeachingBeat(
            beat=TeachingBeatDraft.model_validate(_beat(stage)),
            base_scene=base_scene,
            result_scene=result_scene,
            atoms=atoms,
        )

        assert tuple(atom.role for atom in compiled.atoms) == target_roles[prefix_length:]
        assert compiled.result_scene.revision == compiled.base_scene.revision + len(atoms)


def test_compiled_beat_allows_an_explicit_empty_component_as_its_base_prefix() -> None:
    target_roles = roles_through(PythagoreanStage.TRIANGLE)
    compiled = CompiledTeachingBeat(
        beat=TeachingBeatDraft.model_validate(_beat(PythagoreanStage.TRIANGLE)),
        base_scene=SemanticSceneState(revision=0, components=(_component(()),)),
        result_scene=SemanticSceneState(
            revision=1,
            components=(_component(target_roles),),
        ),
        atoms=(_atom(PythagoreanRole.TRIANGLE),),
    )
    assert compiled.atoms[0].role is PythagoreanRole.TRIANGLE


def test_compiled_beat_rejects_backward_or_non_exact_role_transitions() -> None:
    areas = roles_through(PythagoreanStage.AREAS)
    triangle = roles_through(PythagoreanStage.TRIANGLE)

    with pytest.raises(ValidationError, match="cannot move a component backward"):
        CompiledTeachingBeat(
            beat=TeachingBeatDraft.model_validate(_beat(PythagoreanStage.TRIANGLE)),
            base_scene=SemanticSceneState(revision=7, components=(_component(areas),)),
            result_scene=SemanticSceneState(revision=7, components=(_component(triangle),)),
            atoms=(),
        )

    with pytest.raises(ValidationError, match="exact missing Pythagorean role suffix"):
        CompiledTeachingBeat(
            beat=TeachingBeatDraft.model_validate(_beat(PythagoreanStage.AREAS)),
            base_scene=SemanticSceneState(revision=1, components=(_component(triangle),)),
            result_scene=SemanticSceneState(revision=2, components=(_component(areas),)),
            atoms=(_atom(PythagoreanRole.LABEL_A2),),
        )


def test_compiled_beat_rejects_wrong_result_state_revision_or_atom_owner() -> None:
    target_roles = roles_through(PythagoreanStage.TRIANGLE)
    beat = TeachingBeatDraft.model_validate(_beat(PythagoreanStage.TRIANGLE))
    atom = _atom(PythagoreanRole.TRIANGLE)

    with pytest.raises(ValidationError, match="exact target role prefix"):
        CompiledTeachingBeat(
            beat=beat,
            base_scene=SemanticSceneState(revision=0),
            result_scene=SemanticSceneState(revision=1),
            atoms=(atom,),
        )

    with pytest.raises(ValidationError, match="advance once per compiled atom"):
        CompiledTeachingBeat(
            beat=beat,
            base_scene=SemanticSceneState(revision=0),
            result_scene=SemanticSceneState(
                revision=2,
                components=(_component(target_roles),),
            ),
            atoms=(atom,),
        )

    wrong_owner = _atom(PythagoreanRole.TRIANGLE, beat_id="other-beat")
    with pytest.raises(ValidationError, match="beatId must match"):
        CompiledTeachingBeat(
            beat=beat,
            base_scene=SemanticSceneState(revision=0),
            result_scene=SemanticSceneState(
                revision=1,
                components=(_component(target_roles),),
            ),
            atoms=(wrong_owner,),
        )


def test_compiled_beat_preserves_unrelated_components_exactly() -> None:
    unrelated = _component(roles_through(PythagoreanStage.TRIANGLE), component_id="other")
    target = _component(roles_through(PythagoreanStage.TRIANGLE))
    beat = TeachingBeatDraft.model_validate(_beat(PythagoreanStage.TRIANGLE))

    compiled = CompiledTeachingBeat(
        beat=beat,
        base_scene=SemanticSceneState(revision=4, components=(unrelated,)),
        result_scene=SemanticSceneState(revision=5, components=(unrelated, target)),
        atoms=(_atom(PythagoreanRole.TRIANGLE),),
    )
    assert compiled.result_scene.components[0] == unrelated

    mutated = deepcopy(unrelated.model_dump(mode="json", by_alias=True))
    mutated["revealedRoles"] = roles_through(PythagoreanStage.AREAS)
    with pytest.raises(ValidationError, match="preserve unrelated"):
        CompiledTeachingBeat.model_validate(
            {
                "beat": beat,
                "baseScene": {"revision": 4, "components": [unrelated]},
                "resultScene": {"revision": 5, "components": [mutated, target]},
                "atoms": [_atom(PythagoreanRole.TRIANGLE)],
            }
        )
