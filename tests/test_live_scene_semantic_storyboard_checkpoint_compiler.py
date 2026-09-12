"""Atomic transition and reissued-forgery tests for the Gate 1.8 certifier."""

from __future__ import annotations

from itertools import combinations

import pytest
from murmur.live_scene import semantic_storyboard_checkpoint_compiler as checkpoint_compiler
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.contracts import ScenePatchDraft, SceneState
from murmur.live_scene.semantic_contracts import scene_patch_sha256
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    VALIDATED_SEMANTIC_STORYBOARD_TRANSITION_V1_ADAPTER,
    SemanticStoryboardCheckpointCompilationError,
    ValidatedSemanticStoryboardTransitionV1,
    compile_certified_semantic_storyboard_anchor,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_checkpoint_contracts import (
    CompiledSemanticStoryboardCheckpointV1,
    SemanticStoryboardCheckpointCompilerCertificateBodyV1,
    SemanticStoryboardCheckpointOrigin,
    SemanticStoryboardCheckpointVerificationReceiptV1,
    semantic_storyboard_checkpoint_certificate_sha256,
    semantic_storyboard_checkpoint_receipt_sha256,
)
from murmur.live_scene.semantic_storyboard_compiler import (
    SemanticStoryboardCompilationError,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER,
    AcceptedSemanticStoryboardRecordV1,
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    RoutedSemanticStoryboardBeatV1,
    StoryboardSemanticEffectClosureV1,
    routed_semantic_storyboard_beat_sha256,
    semantic_storyboard_program_sha256,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record
from murmur.live_scene.semantic_storyboard_verifier import (
    SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS,
    SemanticStoryboardVerificationError,
    SemanticStoryboardVerificationObligation,
    VerifiedStoryboardCheckpoint,
)
from pydantic import ValidationError

SUPPORTED_PROBLEMS = tuple(
    PairedProjectileComparisonSpecV1(speedMps=speed, anglesDeg=angles)
    for speed in (20, 25, 30)
    for angles in combinations((30, 45, 60), 2)
)


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


def _record(act: str, **fields: object) -> AcceptedSemanticStoryboardRecordV1:
    record = SEMANTIC_STORYBOARD_RECORD_V1_ADAPTER.validate_python({"v": 1, "act": act, **fields})
    assert record.act != "abstain"
    return record


def _empty_semantic_scene() -> ProjectileStoryboardSemanticSceneStateV1:
    return ProjectileStoryboardSemanticSceneStateV1(revision=0)


def _anchor(
    problem: PairedProjectileComparisonSpecV1 | None = None,
) -> ValidatedSemanticStoryboardTransitionV1:
    return compile_certified_semantic_storyboard_anchor(
        problem or _problem(),
        base_scene=SceneState(revision=0),
        base_semantic_scene=_empty_semantic_scene(),
    )


def _advance(
    transition: ValidatedSemanticStoryboardTransitionV1,
    record: AcceptedSemanticStoryboardRecordV1,
) -> tuple[RoutedSemanticStoryboardBeatV1, ValidatedSemanticStoryboardTransitionV1]:
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=transition.checkpoint.problem_spec,
        semantic_scene=transition.result_semantic_scene,
    )
    result = compile_certified_semantic_storyboard_checkpoint(
        beat,
        base_scene=transition.result_scene,
        base_semantic_scene=transition.result_semantic_scene,
    )
    return beat, result


def _program(
    problem: PairedProjectileComparisonSpecV1,
) -> tuple[AcceptedSemanticStoryboardRecordV1, ...]:
    path_evidence = ["lower_trajectory", "higher_trajectory"]
    if problem.has_complementary_angles:
        return (
            _record("reveal", conceptId="range_formula"),
            _record("reveal", conceptId="complementary_angles"),
            _record(
                "relate",
                claimId="equal_range",
                evidenceIds=["range_formula", "complementary_angles"],
            ),
            _record("trace", trajectoryId="lower_angle"),
            _record("trace", trajectoryId="higher_angle"),
            _record("relate", claimId="higher_apex", evidenceIds=path_evidence),
            _record("relate", claimId="longer_flight", evidenceIds=path_evidence),
        )
    return (
        _record("reveal", conceptId="range_formula"),
        _record("relate", claimId="unequal_range", evidenceIds=["range_formula"]),
        _record("trace", trajectoryId="lower_angle"),
        _record("trace", trajectoryId="higher_angle"),
        _record("relate", claimId="higher_apex", evidenceIds=path_evidence),
        _record("relate", claimId="longer_flight", evidenceIds=path_evidence),
    )


@pytest.mark.parametrize("problem", SUPPORTED_PROBLEMS)
def test_anchor_is_exact_and_every_catalog_branch_certifies_atomically(
    problem: PairedProjectileComparisonSpecV1,
) -> None:
    transition = _anchor(problem)

    assert transition.base_scene == SceneState(revision=0)
    assert transition.base_semantic_scene == _empty_semantic_scene()
    assert transition.result_scene.revision == 1
    assert transition.result_semantic_scene.revision == 1
    assert transition.checkpoint.checkpoint_origin is SemanticStoryboardCheckpointOrigin.ANCHOR
    assert transition.result_semantic_scene.certificate_head_sha256 == (
        transition.checkpoint.certificate.certificate_sha256
    )

    accepted: tuple[AcceptedSemanticStoryboardRecordV1, ...] = ()
    for record in _program(problem):
        previous = transition
        beat, transition = _advance(transition, record)
        accepted = (*accepted, record)

        assert transition.base_scene == previous.result_scene
        assert transition.base_semantic_scene == previous.result_semantic_scene
        assert transition.result_scene.revision == previous.result_scene.revision + 1
        assert transition.result_semantic_scene.revision == (
            previous.result_semantic_scene.revision + 1
        )
        assert transition.result_semantic_scene.components[0].accepted_records == accepted
        assert beat.ordinal == len(accepted)
        assert beat.previous_certificate_sha256 == (
            previous.result_semantic_scene.certificate_head_sha256
        )
        assert transition.checkpoint.receipt.base_program_sha256 == (
            semantic_storyboard_program_sha256(problem, accepted[:-1])
        )
        assert transition.checkpoint.receipt.result_program_sha256 == (
            semantic_storyboard_program_sha256(problem, accepted)
        )
        assert transition.result_semantic_scene.certificate_head_sha256 == (
            transition.checkpoint.certificate.certificate_sha256
        )


def test_two_record_chain_retains_order_paint_and_exact_certificate_heads() -> None:
    anchor = _anchor()
    first_beat, first = _advance(
        anchor,
        _record("trace", trajectoryId="higher_angle"),
    )
    second_beat, second = _advance(
        first,
        _record("reveal", conceptId="range_formula"),
    )

    assert (
        first_beat.previous_certificate_sha256 == anchor.checkpoint.certificate.certificate_sha256
    )
    assert (
        second_beat.previous_certificate_sha256 == first.checkpoint.certificate.certificate_sha256
    )
    assert second.base_scene == first.result_scene
    assert second.base_semantic_scene == first.result_semantic_scene
    assert second.result_scene.nodes[: len(first.result_scene.nodes)] == first.result_scene.nodes
    assert second.result_semantic_scene.components[0].accepted_records == (
        first_beat.record,
        second_beat.record,
    )
    assert (
        VALIDATED_SEMANTIC_STORYBOARD_TRANSITION_V1_ADAPTER.validate_python(
            second.model_dump(mode="json", by_alias=True)
        )
        == second
    )
    with pytest.raises(ValidationError, match="frozen"):
        second.result_scene.revision = 99


def test_anchor_rejects_any_nonempty_or_nonzero_frontier() -> None:
    anchor = _anchor()

    with pytest.raises(
        (SemanticStoryboardCheckpointCompilationError, SemanticStoryboardVerificationError)
    ):
        compile_certified_semantic_storyboard_anchor(
            _problem(),
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
        )
    with pytest.raises(SemanticStoryboardVerificationError):
        compile_certified_semantic_storyboard_anchor(
            _problem(),
            base_scene=SceneState(revision=0, nodes=anchor.result_scene.nodes),
            base_semantic_scene=_empty_semantic_scene(),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("previousCertificateSha256", "f" * 64, "certificate head"),
        ("ordinal", 2, "ordinal"),
        ("baseProgramSha256", "e" * 64, "base program hash"),
        ("resultProgramSha256", "d" * 64, "result program hash"),
    ],
)
def test_model_checkpoint_rejects_forged_frontier_bindings(
    field: str,
    value: object,
    message: str,
) -> None:
    anchor = _anchor()
    beat = route_semantic_storyboard_record(
        _record("reveal", conceptId="range_formula"),
        problem_spec=_problem(),
        semantic_scene=anchor.result_semantic_scene,
    )
    payload = beat.model_dump(mode="json", by_alias=True)
    payload[field] = value
    forged = RoutedSemanticStoryboardBeatV1.model_validate(payload)

    with pytest.raises(
        (
            SemanticStoryboardCheckpointCompilationError,
            SemanticStoryboardCompilationError,
            SemanticStoryboardVerificationError,
        ),
        match=message,
    ):
        compile_certified_semantic_storyboard_checkpoint(
            forged,
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
        )


def test_model_checkpoint_rejects_a_low_level_scene_not_matching_semantic_frontier() -> None:
    anchor = _anchor()
    beat = route_semantic_storyboard_record(
        _record("trace", trajectoryId="lower_angle"),
        problem_spec=_problem(),
        semantic_scene=anchor.result_semantic_scene,
    )
    reordered = SceneState(
        revision=anchor.result_scene.revision,
        nodes=tuple(reversed(anchor.result_scene.nodes)),
    )

    with pytest.raises(SemanticStoryboardVerificationError, match="frontier"):
        compile_certified_semantic_storyboard_checkpoint(
            beat,
            base_scene=reordered,
            base_semantic_scene=anchor.result_semantic_scene,
        )


def test_forged_verifier_return_is_never_used_as_receipt_authority(monkeypatch) -> None:
    anchor = _anchor()
    beat = route_semantic_storyboard_record(
        _record("reveal", conceptId="range_formula"),
        problem_spec=_problem(),
        semantic_scene=anchor.result_semantic_scene,
    )
    forged = VerifiedStoryboardCheckpoint(
        checkpoint_id="forged-checkpoint",
        operation_targets=("forged-node",),
        base_program_sha256="f" * 64,
        result_program_sha256="e" * 64,
        semantic_effect=StoryboardSemanticEffectClosureV1(conceptIds=("range_formula",)),
        obligation_codes=(SemanticStoryboardVerificationObligation.PATCH,),
    )
    monkeypatch.setattr(
        checkpoint_compiler,
        "verify_semantic_storyboard_checkpoint",
        lambda *args, **kwargs: forged,
    )

    transition = compile_certified_semantic_storyboard_checkpoint(
        beat,
        base_scene=anchor.result_scene,
        base_semantic_scene=anchor.result_semantic_scene,
    )

    assert transition.checkpoint.receipt.checkpoint_id == beat.checkpoint_id
    assert transition.checkpoint.receipt.operation_targets == tuple(
        operation.target_id for operation in transition.checkpoint.patch.operations
    )
    assert (
        transition.checkpoint.receipt.obligation_codes
        == SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
    )
    assert transition.checkpoint.receipt.base_program_sha256 != forged.base_program_sha256


def test_verifier_failure_returns_nothing_and_never_hashes_a_certificate(monkeypatch) -> None:
    anchor = _anchor()
    beat = route_semantic_storyboard_record(
        _record("trace", trajectoryId="higher_angle"),
        problem_spec=_problem(),
        semantic_scene=anchor.result_semantic_scene,
    )
    base_scene_wire = anchor.result_scene.model_dump(mode="json", by_alias=True)
    base_semantic_wire = anchor.result_semantic_scene.model_dump(mode="json", by_alias=True)
    certificate_hash_calls: list[object] = []

    def fail_verification(*_args, **_kwargs):
        raise SemanticStoryboardVerificationError(
            SemanticStoryboardVerificationObligation.PHYSICS_GEOMETRY,
            "forced verifier failure",
        )

    def observe_certificate_hash(body):
        certificate_hash_calls.append(body)
        return "0" * 64

    monkeypatch.setattr(
        checkpoint_compiler,
        "verify_semantic_storyboard_checkpoint",
        fail_verification,
    )
    monkeypatch.setattr(
        checkpoint_compiler,
        "semantic_storyboard_checkpoint_certificate_sha256",
        observe_certificate_hash,
    )

    with pytest.raises(SemanticStoryboardVerificationError, match="forced verifier failure"):
        compile_certified_semantic_storyboard_checkpoint(
            beat,
            base_scene=anchor.result_scene,
            base_semantic_scene=anchor.result_semantic_scene,
        )

    assert certificate_hash_calls == []
    assert anchor.result_scene.model_dump(mode="json", by_alias=True) == base_scene_wire
    assert anchor.result_semantic_scene.model_dump(mode="json", by_alias=True) == base_semantic_wire


def _reissue_nested_checkpoint(payload: dict[str, object]) -> None:
    checkpoint = payload["checkpoint"]
    assert isinstance(checkpoint, dict)
    receipt_payload = checkpoint["receipt"]
    certificate = checkpoint["certificate"]
    assert isinstance(receipt_payload, dict)
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    receipt = SemanticStoryboardCheckpointVerificationReceiptV1.model_validate(receipt_payload)
    body_payload["receiptSha256"] = semantic_storyboard_checkpoint_receipt_sha256(receipt)
    body = SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = semantic_storyboard_checkpoint_certificate_sha256(body)
    CompiledSemanticStoryboardCheckpointV1.model_validate(checkpoint)


def test_outer_envelope_rejects_coherently_reissued_caption_forgery() -> None:
    payload = _anchor().model_dump(mode="json", by_alias=True)
    checkpoint = payload["checkpoint"]
    assert isinstance(checkpoint, dict)
    patch = checkpoint["patch"]
    presentation = checkpoint["presentation"]
    certificate = checkpoint["certificate"]
    assert isinstance(patch, dict)
    assert isinstance(presentation, dict)
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    patch["narration"] = "A forged but internally rehashed caption."
    presentation["checkpointNarration"] = patch["narration"]
    body["patchSha256"] = scene_patch_sha256(ScenePatchDraft.model_validate(patch))
    body["presentationCheckpoint"] = presentation
    _reissue_nested_checkpoint(payload)

    with pytest.raises(ValidationError, match="regenerated verified compiler claim"):
        ValidatedSemanticStoryboardTransitionV1.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    ("scene_hashes", "result_scene", "revisions", "prior_head", "program"),
)
def test_outer_envelope_rejects_coherently_reissued_integrity_forgery(mutation: str) -> None:
    anchor = _anchor()
    _, transition = _advance(anchor, _record("reveal", conceptId="range_formula"))
    payload = transition.model_dump(mode="json", by_alias=True)
    checkpoint = payload["checkpoint"]
    assert isinstance(checkpoint, dict)
    receipt = checkpoint["receipt"]
    certificate = checkpoint["certificate"]
    beat_payload = checkpoint["beat"]
    assert isinstance(receipt, dict)
    assert isinstance(certificate, dict)
    assert isinstance(beat_payload, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    if mutation == "scene_hashes":
        body["baseLowLevelSceneSha256"] = "1" * 64
        body["resultSemanticSceneSha256"] = "2" * 64
    elif mutation == "result_scene":
        result_scene = payload["resultScene"]
        assert isinstance(result_scene, dict)
        nodes = result_scene["nodes"]
        assert isinstance(nodes, list)
        result_scene["nodes"] = list(reversed(nodes))
        body["resultLowLevelSceneSha256"] = low_level_scene_sha256(
            SceneState.model_validate(result_scene)
        )
    elif mutation == "revisions":
        for field in (
            "baseLowLevelRevision",
            "resultLowLevelRevision",
            "baseSemanticRevision",
            "resultSemanticRevision",
        ):
            body[field] = int(body[field]) + 4
    elif mutation == "prior_head":
        beat_payload["previousCertificateSha256"] = "3" * 64
        beat = RoutedSemanticStoryboardBeatV1.model_validate(beat_payload)
        routed_digest = routed_semantic_storyboard_beat_sha256(beat)
        receipt["routedBeatSha256"] = routed_digest
        body["routedBeatSha256"] = routed_digest
        body["previousCertificateSha256"] = beat.previous_certificate_sha256
    else:
        beat_payload["baseProgramSha256"] = "4" * 64
        beat_payload["resultProgramSha256"] = "5" * 64
        beat = RoutedSemanticStoryboardBeatV1.model_validate(beat_payload)
        routed_digest = routed_semantic_storyboard_beat_sha256(beat)
        receipt["routedBeatSha256"] = routed_digest
        receipt["baseProgramSha256"] = beat.base_program_sha256
        receipt["resultProgramSha256"] = beat.result_program_sha256
        body["routedBeatSha256"] = routed_digest
        body["baseProgramSha256"] = beat.base_program_sha256
        body["resultProgramSha256"] = beat.result_program_sha256

    _reissue_nested_checkpoint(payload)

    with pytest.raises(
        (ValidationError, SemanticStoryboardCheckpointCompilationError),
    ):
        ValidatedSemanticStoryboardTransitionV1.model_validate(payload)


def test_outer_envelope_rejects_a_result_head_other_than_its_certificate() -> None:
    payload = _anchor().model_dump(mode="json", by_alias=True)
    result_semantic_scene = payload["resultSemanticScene"]
    assert isinstance(result_semantic_scene, dict)
    result_semantic_scene["certificateHeadSha256"] = "6" * 64

    with pytest.raises(ValidationError, match="certified semantic frontier"):
        ValidatedSemanticStoryboardTransitionV1.model_validate(payload)
