"""Integrity contract tests for one Gate 1.8 storyboard checkpoint."""

from __future__ import annotations

import pytest
from murmur.live_scene.checkpoint_contracts import low_level_scene_sha256
from murmur.live_scene.choreography_contracts import choreography_plan_v2_sha256
from murmur.live_scene.contracts import SceneState
from murmur.live_scene.semantic_contracts import scene_patch_sha256
from murmur.live_scene.semantic_integrity import canonical_sha256
from murmur.live_scene.semantic_storyboard_checkpoint_contracts import (
    SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID,
    SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION,
    SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION,
    SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    CompiledSemanticStoryboardCheckpointV1,
    SemanticStoryboardCheckpointCompilerCertificateBodyV1,
    SemanticStoryboardCheckpointCompilerCertificateV1,
    SemanticStoryboardCheckpointOrigin,
    SemanticStoryboardCheckpointVerificationReceiptV1,
    semantic_storyboard_checkpoint_certificate_sha256,
    semantic_storyboard_checkpoint_receipt_sha256,
)
from murmur.live_scene.semantic_storyboard_compiler import (
    compile_semantic_storyboard_anchor,
    compile_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    RevealStoryboardRecordV1,
    paired_projectile_comparison_sha256,
    routed_semantic_storyboard_beat_sha256,
    semantic_storyboard_program_sha256,
    semantic_storyboard_record_sha256,
    semantic_storyboard_scene_sha256,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record
from murmur.live_scene.semantic_storyboard_verifier import (
    SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS,
    SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS,
    verify_semantic_storyboard_anchor,
    verify_semantic_storyboard_checkpoint,
)
from pydantic import ValidationError


def _problem() -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=(30, 60))


def _artifact(
    origin: SemanticStoryboardCheckpointOrigin,
) -> CompiledSemanticStoryboardCheckpointV1:
    problem = _problem()
    anchor = compile_semantic_storyboard_anchor(problem)
    if origin is SemanticStoryboardCheckpointOrigin.ANCHOR:
        beat = None
        blueprint = anchor
        proof = verify_semantic_storyboard_anchor(problem, blueprint)
        base_scene = SceneState(revision=0)
        base_semantic_scene = ProjectileStoryboardSemanticSceneStateV1(revision=0)
        result_semantic_scene = ProjectileStoryboardSemanticSceneStateV1(
            revision=1,
            components=(blueprint.result_component,),
            certificateHeadSha256="0" * 64,
        )
        routed_digest = record_digest = previous_certificate = beat_id = None
    else:
        base_semantic_scene = ProjectileStoryboardSemanticSceneStateV1(
            revision=1,
            components=(anchor.result_component,),
            certificateHeadSha256="a" * 64,
        )
        record = RevealStoryboardRecordV1(v=1, act="reveal", conceptId="range_formula")
        beat = route_semantic_storyboard_record(
            record,
            problem_spec=problem,
            semantic_scene=base_semantic_scene,
        )
        blueprint = compile_semantic_storyboard_checkpoint(beat, anchor.result_component)
        proof = verify_semantic_storyboard_checkpoint(
            beat,
            blueprint,
            base_semantic_scene=base_semantic_scene,
        )
        base_scene = SceneState(revision=1, nodes=blueprint.base_nodes)
        result_semantic_scene = ProjectileStoryboardSemanticSceneStateV1(
            revision=2,
            components=(blueprint.result_component,),
            certificateHeadSha256="0" * 64,
        )
        routed_digest = routed_semantic_storyboard_beat_sha256(beat)
        record_digest = semantic_storyboard_record_sha256(beat.record)
        previous_certificate = beat.previous_certificate_sha256
        beat_id = beat.beat_id
    result_scene = SceneState(revision=base_scene.revision + 1, nodes=blueprint.result_nodes)
    receipt = SemanticStoryboardCheckpointVerificationReceiptV1(
        checkpointOrigin=origin,
        checkpointId=blueprint.checkpoint_id,
        problemSpecSha256=paired_projectile_comparison_sha256(problem),
        routedBeatSha256=routed_digest,
        baseProgramSha256=proof.base_program_sha256,
        resultProgramSha256=proof.result_program_sha256,
        semanticEffect=proof.semantic_effect,
        operationTargets=proof.operation_targets,
        obligationCodes=proof.obligation_codes,
    )
    body = SemanticStoryboardCheckpointCompilerCertificateBodyV1(
        checkpointOrigin=origin,
        beatId=beat_id,
        routedBeatSha256=routed_digest,
        recordSha256=record_digest,
        checkpointId=blueprint.checkpoint_id,
        problemSpecSha256=paired_projectile_comparison_sha256(problem),
        baseProgramSha256=proof.base_program_sha256,
        resultProgramSha256=proof.result_program_sha256,
        baseLowLevelRevision=base_scene.revision,
        resultLowLevelRevision=result_scene.revision,
        baseSemanticRevision=base_semantic_scene.revision,
        resultSemanticRevision=result_semantic_scene.revision,
        baseLowLevelSceneSha256=low_level_scene_sha256(base_scene),
        resultLowLevelSceneSha256=low_level_scene_sha256(result_scene),
        baseSemanticSceneSha256=semantic_storyboard_scene_sha256(base_semantic_scene),
        resultSemanticSceneSha256=semantic_storyboard_scene_sha256(result_semantic_scene),
        patchSha256=scene_patch_sha256(blueprint.patch),
        receiptSha256=semantic_storyboard_checkpoint_receipt_sha256(receipt),
        presentationCheckpoint=blueprint.presentation,
        choreographySha256=choreography_plan_v2_sha256(blueprint.choreography),
        previousCertificateSha256=previous_certificate,
    )
    certificate = SemanticStoryboardCheckpointCompilerCertificateV1(
        body=body,
        certificateSha256=semantic_storyboard_checkpoint_certificate_sha256(body),
    )
    return CompiledSemanticStoryboardCheckpointV1(
        checkpointOrigin=origin,
        problemSpec=problem,
        beat=beat,
        checkpointId=blueprint.checkpoint_id,
        patch=blueprint.patch,
        receipt=receipt,
        presentation=blueprint.presentation,
        choreography=blueprint.choreography,
        certificate=certificate,
    )


def _payload(origin: SemanticStoryboardCheckpointOrigin) -> dict[str, object]:
    return _artifact(origin).model_dump(mode="json", by_alias=True)


def _reissue(payload: dict[str, object]) -> None:
    certificate = payload["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = semantic_storyboard_checkpoint_certificate_sha256(body)


@pytest.mark.parametrize("origin", tuple(SemanticStoryboardCheckpointOrigin))
def test_checkpoint_contract_round_trips_with_a_closed_origin_shape(
    origin: SemanticStoryboardCheckpointOrigin,
) -> None:
    compiled = _artifact(origin)
    wire = compiled.model_dump(mode="json", by_alias=True)

    assert CompiledSemanticStoryboardCheckpointV1.model_validate(wire) == compiled
    assert set(SemanticStoryboardCheckpointVerificationReceiptV1.model_fields) == {
        "issuer",
        "checkpoint_origin",
        "component_kind",
        "component_id",
        "checkpoint_id",
        "problem_spec_sha256",
        "routed_beat_sha256",
        "base_program_sha256",
        "result_program_sha256",
        "semantic_effect",
        "operation_targets",
        "obligation_codes",
        "verified",
    }
    assert set(SemanticStoryboardCheckpointCompilerCertificateV1.model_fields) == {
        "body",
        "certificate_sha256",
    }
    with pytest.raises(ValidationError, match="frozen"):
        compiled.receipt.verified = False


def test_dedicated_v1_hash_domains_are_canonical_and_distinct() -> None:
    compiled = _artifact(SemanticStoryboardCheckpointOrigin.MODEL_RECORD)
    receipt_payload = compiled.receipt.model_dump(mode="json", by_alias=True)
    body_payload = compiled.certificate.body.model_dump(mode="json", by_alias=True)

    assert SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_VERSION == 1
    assert (
        SEMANTIC_STORYBOARD_CHECKPOINT_COMPILER_VERSION
        == "murmur.semantic_storyboard_choreography.v1"
    )
    assert SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN.endswith(":v1")
    assert SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN.endswith(":v1")
    assert semantic_storyboard_checkpoint_receipt_sha256(compiled.receipt) == canonical_sha256(
        dict(reversed(tuple(receipt_payload.items()))),
        domain=SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    )
    assert semantic_storyboard_checkpoint_certificate_sha256(
        compiled.certificate.body
    ) == canonical_sha256(
        dict(reversed(tuple(body_payload.items()))),
        domain=SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )
    assert SEMANTIC_STORYBOARD_CHECKPOINT_RECEIPT_HASH_DOMAIN != (
        SEMANTIC_STORYBOARD_CHECKPOINT_CERTIFICATE_HASH_DOMAIN
    )


def test_receipt_enforces_exact_origin_rules_and_obligation_sets() -> None:
    anchor = _artifact(SemanticStoryboardCheckpointOrigin.ANCHOR).receipt
    model = _artifact(SemanticStoryboardCheckpointOrigin.MODEL_RECORD).receipt
    anchor_payload = anchor.model_dump(mode="json", by_alias=True)
    model_payload = model.model_dump(mode="json", by_alias=True)

    assert anchor.obligation_codes == SEMANTIC_STORYBOARD_ANCHOR_VERIFICATION_OBLIGATIONS
    assert model.obligation_codes == SEMANTIC_STORYBOARD_MODEL_VERIFICATION_OBLIGATIONS
    for update in (
        {"routedBeatSha256": "a" * 64},
        {"semanticEffect": model_payload["semanticEffect"]},
        {"resultProgramSha256": "b" * 64},
        {"obligationCodes": model_payload["obligationCodes"]},
    ):
        with pytest.raises(ValidationError):
            SemanticStoryboardCheckpointVerificationReceiptV1.model_validate(
                {**anchor_payload, **update}
            )
    for update in (
        {"routedBeatSha256": None},
        {"semanticEffect": None},
        {"resultProgramSha256": model_payload["baseProgramSha256"]},
        {"obligationCodes": anchor_payload["obligationCodes"]},
    ):
        with pytest.raises(ValidationError):
            SemanticStoryboardCheckpointVerificationReceiptV1.model_validate(
                {**model_payload, **update}
            )
    for value in (False, 1, 1.0, "true"):
        with pytest.raises(ValidationError):
            SemanticStoryboardCheckpointVerificationReceiptV1.model_validate(
                {**anchor_payload, "verified": value}
            )


def test_certificate_body_enforces_anchor_and_model_nullable_rules() -> None:
    anchor_body = _artifact(SemanticStoryboardCheckpointOrigin.ANCHOR).certificate.body
    model_body = _artifact(SemanticStoryboardCheckpointOrigin.MODEL_RECORD).certificate.body
    anchor_payload = anchor_body.model_dump(mode="json", by_alias=True)
    model_payload = model_body.model_dump(mode="json", by_alias=True)

    for field in ("beatId", "routedBeatSha256", "recordSha256", "previousCertificateSha256"):
        with pytest.raises(ValidationError, match="forbids"):
            SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(
                {**anchor_payload, field: model_payload[field]}
            )
        with pytest.raises(ValidationError, match="requires"):
            SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(
                {**model_payload, field: None}
            )
    with pytest.raises(ValidationError, match="revision 0"):
        SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(
            {
                **anchor_payload,
                "baseLowLevelRevision": 1,
                "resultLowLevelRevision": 2,
                "baseSemanticRevision": 1,
                "resultSemanticRevision": 2,
            }
        )
    with pytest.raises(ValidationError, match="accepted anchor"):
        SemanticStoryboardCheckpointCompilerCertificateBodyV1.model_validate(
            {
                **model_payload,
                "baseLowLevelRevision": 0,
                "resultLowLevelRevision": 1,
                "baseSemanticRevision": 0,
                "resultSemanticRevision": 1,
            }
        )


@pytest.mark.parametrize(
    ("path", "field", "value", "message"),
    [
        ("compiled", "checkpointId", SEMANTIC_STORYBOARD_ANCHOR_CHECKPOINT_ID, "checkpointId"),
        ("problem", "speedMps", 25, "problemSpec"),
        ("receipt", "problemSpecSha256", "f" * 64, "receipt"),
        ("body", "routedBeatSha256", "e" * 64, "certificate body"),
        ("body", "recordSha256", "d" * 64, "certificate body"),
        ("body", "patchSha256", "c" * 64, "certificate body"),
        ("body", "receiptSha256", "b" * 64, "certificate body"),
        ("body", "choreographySha256", "9" * 64, "certificate body"),
        ("body", "previousCertificateSha256", "8" * 64, "certificate body"),
    ],
)
def test_rehashed_tampering_cannot_break_recomputed_field_bindings(
    path: str,
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _payload(SemanticStoryboardCheckpointOrigin.MODEL_RECORD)
    if path == "compiled":
        target = payload
    elif path == "problem":
        target = payload["problemSpec"]
    elif path == "receipt":
        target = payload["receipt"]
    else:
        certificate = payload["certificate"]
        assert isinstance(certificate, dict)
        target = certificate["body"]
    assert isinstance(target, dict)
    target[field] = value
    if path == "receipt":
        receipt = SemanticStoryboardCheckpointVerificationReceiptV1.model_validate(target)
        certificate = payload["certificate"]
        assert isinstance(certificate, dict)
        body = certificate["body"]
        assert isinstance(body, dict)
        body["receiptSha256"] = semantic_storyboard_checkpoint_receipt_sha256(receipt)
    _reissue(payload)

    with pytest.raises(ValidationError, match=message):
        CompiledSemanticStoryboardCheckpointV1.model_validate(payload)


def test_patch_receipt_presentation_and_certificate_digest_are_all_bound() -> None:
    payload = _payload(SemanticStoryboardCheckpointOrigin.ANCHOR)
    receipt = payload["receipt"]
    presentation = payload["presentation"]
    certificate = payload["certificate"]
    assert isinstance(receipt, dict)
    assert isinstance(presentation, dict)
    assert isinstance(certificate, dict)

    receipt["operationTargets"] = list(reversed(receipt["operationTargets"]))
    with pytest.raises(ValidationError, match="receipt"):
        CompiledSemanticStoryboardCheckpointV1.model_validate(payload)

    payload = _payload(SemanticStoryboardCheckpointOrigin.ANCHOR)
    presentation = payload["presentation"]
    assert isinstance(presentation, dict)
    presentation["checkpointNarration"] = "A different caption."
    with pytest.raises(ValidationError, match="narration"):
        CompiledSemanticStoryboardCheckpointV1.model_validate(payload)

    payload = _payload(SemanticStoryboardCheckpointOrigin.ANCHOR)
    certificate = payload["certificate"]
    assert isinstance(certificate, dict)
    certificate["certificateSha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical storyboard body"):
        CompiledSemanticStoryboardCheckpointV1.model_validate(payload)


def test_expected_existing_hashes_are_bound_for_both_origins() -> None:
    anchor = _artifact(SemanticStoryboardCheckpointOrigin.ANCHOR)
    model = _artifact(SemanticStoryboardCheckpointOrigin.MODEL_RECORD)

    anchor_program = semantic_storyboard_program_sha256(anchor.problem_spec, ())
    assert anchor.receipt.base_program_sha256 == anchor_program
    assert anchor.receipt.result_program_sha256 == anchor_program
    assert anchor.beat is None
    assert model.beat is not None
    assert model.receipt.routed_beat_sha256 == routed_semantic_storyboard_beat_sha256(model.beat)
    assert model.certificate.body.record_sha256 == semantic_storyboard_record_sha256(
        model.beat.record
    )
