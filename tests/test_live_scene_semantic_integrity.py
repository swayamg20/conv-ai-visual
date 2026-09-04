from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.contracts import ScenePatchDraft
from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    SEMANTIC_COMPILER_VERSION,
    CompiledTeachingBeat,
    CompiledVisualAtom,
    CompilerCertificateBodyV1,
    CompilerCertificateV1,
    PythagoreanAreaIdentityState,
    PythagoreanStage,
    SemanticSceneState,
    TeachingBeatDraft,
    compiler_certificate_sha256,
    scene_patch_sha256,
    semantic_scene_sha256,
    teaching_beat_sha256,
    verification_receipt_sha256,
)
from murmur.live_scene.semantic_integrity import (
    SEMANTIC_CANONICALIZATION,
    SEMANTIC_HASH_ALGORITHM,
    canonical_json_v1,
    canonical_sha256,
)
from pydantic import ValidationError


def _beat(
    stage: PythagoreanStage = PythagoreanStage.IDENTITY,
    *,
    narration: str = "See a² and 雪 meet at 320.0 units.",
) -> TeachingBeatDraft:
    return TeachingBeatDraft.model_validate(
        {
            "v": 1,
            "beatId": "beat-integrity",
            "narration": narration,
            "act": "derive",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": "areas",
                "revealThrough": stage.value,
            },
        }
    )


def _prefix_scene(compiled: CompiledTeachingBeat, prefix_length: int) -> SemanticSceneState:
    if prefix_length == 0:
        return compiled.base_scene
    certificate = compiled.atoms[prefix_length - 1].certificate
    assert certificate is not None
    return SemanticSceneState(
        revision=compiled.base_scene.revision + prefix_length,
        components=(
            PythagoreanAreaIdentityState(
                id="areas",
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:prefix_length],
            ),
        ),
        certificate_head_sha256=certificate.certificate_sha256,
    )


def _rehash_certificate(certificate: dict[str, object]) -> None:
    body = CompilerCertificateBodyV1.model_validate(certificate["body"])
    certificate["certificateSha256"] = compiler_certificate_sha256(body)


def test_murmur_json_v1_has_a_fixed_unicode_and_float_vector() -> None:
    value = {"z": [320.0, "a²"], "a": {"β": "雪", "x": 1.5}}
    canonical = canonical_json_v1(value)

    assert canonical.decode("utf-8") == '{"a":{"x":1.5,"β":"雪"},"z":[320.0,"a²"]}'
    assert canonical_sha256(value, domain="murmur:test-vector:v1") == (
        "d3bb402628d14176bb5c12de32ccfc2de73ae2f4e6d9d1c38170cbc17a3344d0"
    )
    assert canonical_sha256(
        {"a": {"x": 1.5, "β": "雪"}, "z": [320.0, "a²"]},
        domain="murmur:test-vector:v1",
    ) == canonical_sha256(value, domain="murmur:test-vector:v1")
    assert canonical_sha256(value, domain="murmur:other-artifact:v1") != canonical_sha256(
        value,
        domain="murmur:test-vector:v1",
    )
    assert canonical_sha256(
        {"z": ["a²", 320.0], "a": {"β": "雪", "x": 1.5}},
        domain="murmur:test-vector:v1",
    ) != canonical_sha256(value, domain="murmur:test-vector:v1")


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_murmur_json_v1_rejects_non_finite_numbers(non_finite: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        canonical_json_v1({"coordinate": non_finite})


def test_compiler_certificate_contract_is_explicit_and_self_checking() -> None:
    assert set(CompilerCertificateBodyV1.model_fields) == {
        "v",
        "issuer",
        "compiler_version",
        "canonicalization",
        "hash_algorithm",
        "atom_id",
        "beat_id",
        "beat_sha256",
        "component_id",
        "role",
        "node_id",
        "atom_ordinal",
        "base_semantic_revision",
        "result_semantic_revision",
        "base_scene_sha256",
        "result_scene_sha256",
        "patch_sha256",
        "receipt_sha256",
        "previous_certificate_sha256",
    }
    assert set(CompilerCertificateV1.model_fields) == {"body", "certificate_sha256"}

    atom = compile_teaching_beat(_beat(), SemanticSceneState(revision=0)).atoms[0]
    certificate = atom.certificate
    assert certificate is not None
    body = certificate.body

    assert body.compiler_version == SEMANTIC_COMPILER_VERSION
    assert body.canonicalization == SEMANTIC_CANONICALIZATION == "murmur-json-v1"
    assert body.hash_algorithm == SEMANTIC_HASH_ALGORITHM == "sha256"
    assert body.atom_ordinal == 1
    assert body.atom_id == atom.atom_id
    assert body.beat_id == atom.beat_id
    assert body.beat_sha256 == teaching_beat_sha256(_beat())
    assert body.component_id == atom.component_id
    assert body.node_id == atom.patch.operations[0].target_id
    assert body.patch_sha256 == scene_patch_sha256(atom.patch)
    assert body.receipt_sha256 == verification_receipt_sha256(atom.receipt)
    assert certificate.certificate_sha256 == compiler_certificate_sha256(body)
    assert body.patch_sha256 == "4892da2c64b0bf08030e6aa3e9f6b826ba2004cfaa61452caf8a94d86bee6c39"
    assert body.beat_sha256 == "7529114153c906f55bd632aad6f25e9693dc5587d03b459f46ca42f0b18254ae"
    assert certificate.certificate_sha256 == (
        "9093c6dc150b2611c89974cd64e6c807d142ac4effbcbb1203dff515d0bdcdb7"
    )


def test_compiler_emits_one_contiguous_certificate_chain() -> None:
    base = SemanticSceneState(revision=4)
    compiled = compile_teaching_beat(_beat(), base)
    current_scene = base

    for ordinal, atom in enumerate(compiled.atoms, start=1):
        certificate = atom.certificate
        assert certificate is not None
        body = certificate.body
        assert body.atom_ordinal == ordinal
        assert body.role is PYTHAGOREAN_ROLE_ORDER[ordinal - 1]
        assert body.base_semantic_revision == current_scene.revision
        assert body.result_semantic_revision == current_scene.revision + 1
        assert body.base_scene_sha256 == semantic_scene_sha256(current_scene)
        assert body.previous_certificate_sha256 == current_scene.certificate_head_sha256

        current_scene = SemanticSceneState(
            revision=current_scene.revision + 1,
            components=(
                PythagoreanAreaIdentityState(
                    id="areas",
                    revealed_roles=PYTHAGOREAN_ROLE_ORDER[:ordinal],
                ),
            ),
            certificate_head_sha256=certificate.certificate_sha256,
        )
        assert body.result_scene_sha256 == semantic_scene_sha256(current_scene)

    assert current_scene == compiled.result_scene
    assert compiled.result_scene.certificate_head_sha256 == (
        compiled.atoms[-1].certificate.certificate_sha256  # type: ignore[union-attr]
    )


def test_resume_from_every_prefix_reproduces_the_exact_certificate_suffix() -> None:
    beat = _beat()
    complete = compile_teaching_beat(beat, SemanticSceneState(revision=0))

    for prefix_length in range(len(PYTHAGOREAN_ROLE_ORDER) + 1):
        base = _prefix_scene(complete, prefix_length)
        resumed = compile_teaching_beat(beat, base)

        assert resumed.atoms == complete.atoms[prefix_length:]
        assert resumed.result_scene == complete.result_scene
        if resumed.atoms:
            certificate = resumed.atoms[0].certificate
            assert certificate is not None
            assert certificate.body.atom_ordinal == prefix_length + 1
            assert certificate.body.previous_certificate_sha256 == (base.certificate_head_sha256)
        else:
            assert resumed.result_scene is base


def test_certificate_output_is_deterministic_and_noop_preserves_the_head() -> None:
    beat = _beat()
    base = SemanticSceneState(revision=9)
    first = compile_teaching_beat(beat, base)
    second = compile_teaching_beat(beat, base)

    assert first.model_dump_json(by_alias=True) == second.model_dump_json(by_alias=True)
    noop = compile_teaching_beat(beat, first.result_scene)
    assert noop.atoms == ()
    assert noop.result_scene is first.result_scene
    assert noop.result_scene.certificate_head_sha256 == (
        first.atoms[-1].certificate.certificate_sha256  # type: ignore[union-attr]
    )


def test_scene_digest_excludes_only_the_non_semantic_chain_head() -> None:
    component = PythagoreanAreaIdentityState(
        id="areas",
        revealed_roles=PYTHAGOREAN_ROLE_ORDER[:1],
    )
    first = SemanticSceneState(
        revision=1,
        components=(component,),
        certificate_head_sha256="0" * 64,
    )
    second = SemanticSceneState(
        revision=1,
        components=(component,),
        certificate_head_sha256="f" * 64,
    )

    assert semantic_scene_sha256(first) == semantic_scene_sha256(second)
    assert semantic_scene_sha256(first) != semantic_scene_sha256(
        SemanticSceneState(
            revision=2,
            components=(component,),
            certificate_head_sha256="0" * 64,
        )
    )


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("patch", "narration", "Mutated narration.", "patchSha256"),
        ("receipt", "obligationCodes", ["stable_id"], "receiptSha256"),
        ("atom", "beatId", "beat-other", "certificate beatId"),
    ],
)
def test_atom_certificate_rejects_bound_artifact_mutations(
    section: str,
    field: str,
    value: object,
    message: str,
) -> None:
    atom = compile_teaching_beat(_beat(), SemanticSceneState(revision=0)).atoms[0]
    payload = atom.model_dump(mode="json", by_alias=True)
    target = payload if section == "atom" else payload[section]
    assert isinstance(target, dict)
    target[field] = value

    with pytest.raises(ValidationError, match=message):
        CompiledVisualAtom.model_validate(payload)


def test_patch_geometry_mutation_invalidates_the_certificate() -> None:
    atom = compile_teaching_beat(_beat(), SemanticSceneState(revision=0)).atoms[0]
    payload = atom.model_dump(mode="json", by_alias=True)
    operations = payload["patch"]["operations"]
    operations[0]["node"]["points"][0][0] = 321.0

    with pytest.raises(ValidationError, match="patchSha256"):
        CompiledVisualAtom.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compilerVersion", "murmur.pythagorean_area_identity.v2"),
        ("atomId", "areas__atom_square_a"),
        ("beatId", "beat-other"),
        ("beatSha256", "0" * 64),
        ("componentId", "other"),
        ("role", "square_a"),
        ("nodeId", "areas__square_a"),
        ("atomOrdinal", 2),
        ("baseSceneSha256", "0" * 64),
        ("resultSceneSha256", "0" * 64),
        ("patchSha256", "0" * 64),
        ("receiptSha256", "0" * 64),
        ("previousCertificateSha256", "0" * 64),
    ],
)
def test_certificate_body_mutation_is_detected(field: str, value: object) -> None:
    atom = compile_teaching_beat(_beat(), SemanticSceneState(revision=0)).atoms[0]
    certificate = atom.certificate
    assert certificate is not None
    payload = certificate.model_dump(mode="json", by_alias=True)
    payload["body"][field] = value

    with pytest.raises(ValidationError):
        CompilerCertificateV1.model_validate(payload)


def test_certificate_revision_pair_mutation_is_detected() -> None:
    atom = compile_teaching_beat(_beat(), SemanticSceneState(revision=0)).atoms[0]
    certificate = atom.certificate
    assert certificate is not None
    payload = certificate.model_dump(mode="json", by_alias=True)
    payload["body"]["baseSemanticRevision"] = 1
    payload["body"]["resultSemanticRevision"] = 2

    with pytest.raises(ValidationError, match="certificateSha256"):
        CompilerCertificateV1.model_validate(payload)


def test_changing_the_model_authored_act_without_rehashing_fails_closed() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    payload["beat"]["act"] = "emphasize"

    with pytest.raises(ValidationError, match="beatSha256"):
        CompiledTeachingBeat.model_validate(payload)


def test_rehashed_wrong_beat_digest_is_rejected_on_every_certificate() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    second_certificate = payload["atoms"][1]["certificate"]
    second_certificate["body"]["beatSha256"] = "0" * 64
    _rehash_certificate(second_certificate)

    with pytest.raises(ValidationError, match="beatSha256"):
        CompiledTeachingBeat.model_validate(payload)


def test_rehashing_one_certificate_for_a_changed_act_breaks_the_chain() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    payload["beat"]["act"] = "emphasize"
    changed_beat = TeachingBeatDraft.model_validate(payload["beat"])
    changed_beat_sha256 = teaching_beat_sha256(changed_beat)
    for atom in payload["atoms"]:
        certificate = atom["certificate"]
        certificate["body"]["beatSha256"] = changed_beat_sha256
        _rehash_certificate(certificate)

    with pytest.raises(ValidationError, match="previousCertificateSha256"):
        CompiledTeachingBeat.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("baseSceneSha256", "baseSceneSha256"),
        ("resultSceneSha256", "resultSceneSha256"),
        ("previousCertificateSha256", "previousCertificateSha256"),
    ],
)
def test_beat_rejects_rehashed_but_false_chain_claims(field: str, message: str) -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    atom_index = 1 if field == "previousCertificateSha256" else 0
    certificate = payload["atoms"][atom_index]["certificate"]
    certificate["body"][field] = "0" * 64
    _rehash_certificate(certificate)

    with pytest.raises(ValidationError, match=message):
        CompiledTeachingBeat.model_validate(payload)


def test_changing_an_earlier_certificate_breaks_the_following_link() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    first_atom = payload["atoms"][0]
    first_atom["patch"]["narration"] = "Still valid JSON, but a different patch."
    first_certificate = first_atom["certificate"]
    patch = ScenePatchDraft.model_validate(first_atom["patch"])
    first_certificate["body"]["patchSha256"] = scene_patch_sha256(patch)
    _rehash_certificate(first_certificate)

    with pytest.raises(ValidationError, match="previousCertificateSha256"):
        CompiledTeachingBeat.model_validate(payload)


def test_result_scene_must_publish_the_last_certificate_as_its_head() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    payload = compiled.model_dump(mode="json", by_alias=True)
    payload["resultScene"]["certificateHeadSha256"] = "0" * 64

    with pytest.raises(ValidationError, match="certified semantic chain result"):
        CompiledTeachingBeat.model_validate(payload)


def test_legacy_atoms_remain_all_none_only_and_cannot_downgrade_a_chain() -> None:
    compiled = compile_teaching_beat(_beat(), SemanticSceneState(revision=0))
    wire = compiled.model_dump(mode="json", by_alias=True)

    mixed = deepcopy(wire)
    mixed["atoms"][0].pop("certificate")
    with pytest.raises(ValidationError, match="cannot mix certified and legacy"):
        CompiledTeachingBeat.model_validate(mixed)

    stripped_with_head = deepcopy(wire)
    for atom in stripped_with_head["atoms"]:
        atom.pop("certificate")
    with pytest.raises(ValidationError, match="legacy atoms cannot consume or produce"):
        CompiledTeachingBeat.model_validate(stripped_with_head)

    legacy = deepcopy(stripped_with_head)
    legacy["resultScene"].pop("certificateHeadSha256")
    parsed = CompiledTeachingBeat.model_validate(legacy)
    assert all(atom.certificate is None for atom in parsed.atoms)
    assert "certificate" not in parsed.atoms[0].model_dump(mode="json", by_alias=True)
