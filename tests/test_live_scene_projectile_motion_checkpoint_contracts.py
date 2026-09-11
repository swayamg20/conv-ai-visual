from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.checkpoint_contracts import CHECKPOINT_CERTIFICATE_HASH_DOMAIN
from murmur.live_scene.contracts import SceneState
from murmur.live_scene.parametric_checkpoint_contracts import (
    CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN,
)
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION,
    PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION,
    PROJECTILE_MOTION_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS,
    CompiledProjectileMotionCheckpointV1,
    ProjectileMotionCheckpointAction,
    ProjectileMotionCheckpointCompilerCertificateBodyV1,
    ProjectileMotionCheckpointCompilerCertificateV1,
    ProjectileMotionCheckpointVerificationReceiptV1,
    ProjectileMotionVerificationObligation,
    projectile_motion_checkpoint_certificate_sha256,
    projectile_motion_checkpoint_receipt_sha256,
)
from murmur.live_scene.projectile_motion_compiler import materialize_projectile_motion_nodes
from murmur.live_scene.projectile_motion_contracts import (
    AdvanceProjectileMotionRouteV1,
    ClarifyProjectileMotionRouteV1,
    ProjectileMotionClarificationTopic,
    ProjectileMotionMainCheckpoint,
    ProjectileMotionProblemSpecV1,
    ProjectileMotionStage,
    ProjectileMotionStateV1,
    RetargetProjectileMotionRouteV1,
    RoutedProjectileMotionBeatV1,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState
from murmur.live_scene.semantic_integrity import canonical_sha256
from pydantic import ValidationError


def _problem(speed: int = 20, angle: int = 45) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1(speed_mps=speed, angle_deg=angle)


def _fresh_checkpoint(
    *,
    stage: ProjectileMotionStage = ProjectileMotionStage.SETUP,
) -> CompiledProjectileMotionCheckpointV1:
    problem = _problem()
    beat = RoutedProjectileMotionBeatV1(
        beat_id="contract-beat",
        component_id="lesson",
        base_problem_spec=None,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=stage),
    )
    compiled = compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )
    return compiled.checkpoints[0]


def _continued_checkpoint(
    *,
    clarify: ProjectileMotionClarificationTopic | None = None,
    retarget: ProjectileMotionProblemSpecV1 | None = None,
) -> CompiledProjectileMotionCheckpointV1:
    problem = _problem()
    frontier = ProjectileMotionMainCheckpoint.APEX_STATE
    component = ProjectileMotionStateV1(
        id="lesson",
        problem_spec=problem,
        last_main_checkpoint=frontier,
    )
    if clarify is not None:
        route = ClarifyProjectileMotionRouteV1(topic=clarify)
        result_problem = problem
    elif retarget is not None:
        route = RetargetProjectileMotionRouteV1(target_problem_spec=retarget)
        result_problem = retarget
    else:
        raise AssertionError("a continued fixture needs clarify or retarget")
    beat = RoutedProjectileMotionBeatV1(
        beat_id="continued-contract-beat",
        component_id="lesson",
        base_problem_spec=problem,
        result_problem_spec=result_problem,
        route=route,
    )
    compiled = compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=SceneState(
            revision=8,
            nodes=materialize_projectile_motion_nodes(component),
        ),
        base_semantic_scene=SemanticSceneState(
            revision=8,
            components=(component,),
            certificate_head_sha256="a" * 64,
        ),
    )
    return compiled.checkpoints[0]


def _payload() -> dict[str, object]:
    return _fresh_checkpoint().model_dump(mode="json", by_alias=True)


def _reissue(payload: dict[str, object]) -> None:
    certificate = payload["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = ProjectileMotionCheckpointCompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = projectile_motion_checkpoint_certificate_sha256(body)


def test_projectile_integrity_contracts_round_trip_with_an_exact_closed_shape() -> None:
    compiled = _fresh_checkpoint()
    wire = compiled.model_dump(mode="json", by_alias=True)

    assert CompiledProjectileMotionCheckpointV1.model_validate(wire) == compiled
    assert set(ProjectileMotionCheckpointVerificationReceiptV1.model_fields) == {
        "issuer",
        "component_kind",
        "component_id",
        "action",
        "checkpoint_id",
        "clarification_topic",
        "base_problem_spec_sha256",
        "result_problem_spec_sha256",
        "operation_targets",
        "obligation_codes",
        "verified",
    }
    assert set(ProjectileMotionCheckpointCompilerCertificateBodyV1.model_fields) == {
        "v",
        "issuer",
        "compiler_version",
        "canonicalization",
        "hash_algorithm",
        "beat_id",
        "routed_beat_sha256",
        "component_kind",
        "component_id",
        "action",
        "checkpoint_id",
        "clarification_topic",
        "base_problem_spec_sha256",
        "result_problem_spec_sha256",
        "base_low_level_revision",
        "result_low_level_revision",
        "base_semantic_revision",
        "result_semantic_revision",
        "base_low_level_scene_sha256",
        "result_low_level_scene_sha256",
        "base_semantic_scene_sha256",
        "result_semantic_scene_sha256",
        "patch_sha256",
        "receipt_sha256",
        "presentation_checkpoint",
        "choreography_sha256",
        "previous_certificate_sha256",
    }
    assert set(ProjectileMotionCheckpointCompilerCertificateV1.model_fields) == {
        "body",
        "certificate_sha256",
    }
    assert set(CompiledProjectileMotionCheckpointV1.model_fields) == {
        "beat",
        "action",
        "checkpoint_id",
        "clarification_topic",
        "patch",
        "receipt",
        "presentation",
        "choreography",
        "certificate",
    }
    assert wire["clarificationTopic"] is None
    receipt = wire["receipt"]
    body = wire["certificate"]
    assert isinstance(receipt, dict)
    assert isinstance(body, dict)
    assert receipt["baseProblemSpecSha256"] is None
    assert receipt["resultProblemSpecSha256"] is not None

    with pytest.raises(ValidationError, match="frozen"):
        compiled.receipt.action = ProjectileMotionCheckpointAction.RETARGET


def test_hashes_are_canonical_and_separate_from_all_older_checkpoint_domains() -> None:
    compiled = _fresh_checkpoint()
    receipt = compiled.receipt
    body = compiled.certificate.body
    receipt_payload = receipt.model_dump(mode="json", by_alias=True)
    body_payload = body.model_dump(mode="json", by_alias=True)
    receipt_digest = projectile_motion_checkpoint_receipt_sha256(receipt)
    certificate_digest = projectile_motion_checkpoint_certificate_sha256(body)

    assert receipt_digest == ("39a4ee093e20c4e81b2a4d474bb6b81cf2e9b85c83ce4bb0faf931a64cec7d10")
    assert receipt_digest == canonical_sha256(
        dict(reversed(tuple(receipt_payload.items()))),
        domain=PROJECTILE_MOTION_CHECKPOINT_RECEIPT_HASH_DOMAIN,
    )
    assert certificate_digest == canonical_sha256(
        dict(reversed(tuple(body_payload.items()))),
        domain=PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )
    assert certificate_digest != canonical_sha256(
        body_payload,
        domain=CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )
    assert certificate_digest != canonical_sha256(
        body_payload,
        domain=CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN,
    )
    assert PROJECTILE_MOTION_CHECKPOINT_CERTIFICATE_VERSION == 1
    assert (
        PROJECTILE_MOTION_CHECKPOINT_COMPILER_VERSION == "murmur.projectile_motion_choreography.v1"
    )


def test_receipt_requires_the_exact_complete_verifier_obligation_suite() -> None:
    payload = _fresh_checkpoint().receipt.model_dump(mode="json", by_alias=True)
    assert tuple(item.value for item in PROJECTILE_MOTION_VERIFICATION_OBLIGATIONS) == tuple(
        item.value for item in ProjectileMotionVerificationObligation
    )

    for obligations in (
        [],
        payload["obligationCodes"][:-1],
        list(reversed(payload["obligationCodes"])),
        [*payload["obligationCodes"], payload["obligationCodes"][-1]],
    ):
        with pytest.raises(ValidationError):
            ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
                {**payload, "obligationCodes": obligations}
            )

    for value in (False, 1, 1.0, "true"):
        with pytest.raises(ValidationError):
            ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
                {**payload, "verified": value}
            )


def test_problem_hash_transition_rules_are_closed_for_all_three_actions() -> None:
    fresh = _fresh_checkpoint()
    clarify = _continued_checkpoint(clarify=ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY)
    retarget = _continued_checkpoint(retarget=_problem(30, 60))

    assert fresh.receipt.base_problem_spec_sha256 is None
    assert clarify.receipt.base_problem_spec_sha256 == clarify.receipt.result_problem_spec_sha256
    assert retarget.receipt.base_problem_spec_sha256 != retarget.receipt.result_problem_spec_sha256

    second_payload = _fresh_checkpoint(stage=ProjectileMotionStage.SOLVE).model_dump(
        mode="json", by_alias=True
    )["receipt"]
    assert isinstance(second_payload, dict)
    # The fixture helper returns setup; take the actual second checkpoint here.
    problem = _problem()
    beat = RoutedProjectileMotionBeatV1(
        beat_id="full-contract-beat",
        component_id="lesson",
        base_problem_spec=None,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=ProjectileMotionStage.SOLVE),
    )
    full = compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )
    second_payload = full.checkpoints[1].receipt.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="fresh setup"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
            {**second_payload, "baseProblemSpecSha256": None}
        )

    clarify_payload = clarify.receipt.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="requires baseProblemSpecSha256"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
            {**clarify_payload, "baseProblemSpecSha256": None}
        )
    with pytest.raises(ValidationError, match="clarificationTopic must match"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
            {
                **clarify_payload,
                "clarificationTopic": ProjectileMotionClarificationTopic.APEX_ACCELERATION.value,
            }
        )

    retarget_payload = retarget.receipt.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="requires baseProblemSpecSha256"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
            {**retarget_payload, "baseProblemSpecSha256": None}
        )
    with pytest.raises(ValidationError, match="must change"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(
            {
                **retarget_payload,
                "baseProblemSpecSha256": retarget_payload["resultProblemSpecSha256"],
            }
        )
    missing_result = deepcopy(fresh.receipt.model_dump(mode="json", by_alias=True))
    del missing_result["resultProblemSpecSha256"]
    with pytest.raises(ValidationError, match="resultProblemSpecSha256"):
        ProjectileMotionCheckpointVerificationReceiptV1.model_validate(missing_result)


def test_certificate_requires_lockstep_single_revision_and_action_identity() -> None:
    body = _fresh_checkpoint().certificate.body
    payload = body.model_dump(mode="json", by_alias=True)

    mutations = (
        ({"resultLowLevelRevision": payload["resultLowLevelRevision"] + 1}, "one greater"),
        ({"resultSemanticRevision": payload["resultSemanticRevision"] + 1}, "one greater"),
        (
            {
                "baseSemanticRevision": payload["baseSemanticRevision"] + 1,
                "resultSemanticRevision": payload["resultSemanticRevision"] + 1,
            },
            "base low-level",
        ),
        ({"checkpointId": "decompose_velocity"}, "presentation checkpointId"),
    )
    for update, message in mutations:
        with pytest.raises(ValidationError, match=message):
            ProjectileMotionCheckpointCompilerCertificateBodyV1.model_validate(
                {**payload, **update}
            )

    for version in (True, 1.0, "1"):
        with pytest.raises(ValidationError, match="strict integer"):
            ProjectileMotionCheckpointCompilerCertificateBodyV1.model_validate(
                {**payload, "v": version}
            )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("action", "action must match routed beat"),
        ("topic", "clarificationTopic must match routed beat"),
        ("patch_id", "patchId"),
        ("presentation", "presentation checkpointId"),
        ("beat_id", "certificate beatId"),
        ("beat_hash", "routedBeatSha256"),
        ("patch_hash", "patchSha256"),
        ("receipt_hash", "receiptSha256"),
        ("choreography_hash", "choreographySha256"),
    ],
)
def test_compiled_checkpoint_rejects_mutated_bindings(
    mutation: str,
    message: str,
) -> None:
    payload = _payload()
    beat = payload["beat"]
    patch = payload["patch"]
    presentation = payload["presentation"]
    certificate = payload["certificate"]
    assert isinstance(beat, dict)
    assert isinstance(patch, dict)
    assert isinstance(presentation, dict)
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    if mutation == "action":
        payload["action"] = "retarget"
    elif mutation == "topic":
        payload["clarificationTopic"] = "horizontal_velocity"
    elif mutation == "patch_id":
        patch["patchId"] = "lesson__cp_decompose_velocity"
    elif mutation == "presentation":
        presentation["checkpointId"] = "decompose_velocity"
    elif mutation == "beat_id":
        beat["beatId"] = "different-beat"
    elif mutation == "beat_hash":
        body["routedBeatSha256"] = "0" * 64
        _reissue(payload)
    elif mutation == "patch_hash":
        body["patchSha256"] = "0" * 64
        _reissue(payload)
    elif mutation == "receipt_hash":
        body["receiptSha256"] = "0" * 64
        _reissue(payload)
    elif mutation == "choreography_hash":
        body["choreographySha256"] = "0" * 64
        _reissue(payload)

    with pytest.raises(ValidationError, match=message):
        CompiledProjectileMotionCheckpointV1.model_validate(payload)


def test_models_reject_unknown_fields_and_a_bad_certificate_digest() -> None:
    payload = _payload()
    for path in ("compiled", "receipt", "certificate", "body"):
        changed = deepcopy(payload)
        if path == "compiled":
            target = changed
        elif path == "receipt":
            target = changed["receipt"]
        else:
            certificate = changed["certificate"]
            assert isinstance(certificate, dict)
            target = certificate if path == "certificate" else certificate["body"]
        assert isinstance(target, dict)
        target["provider"] = "forbidden"
        with pytest.raises(ValidationError, match="Extra inputs"):
            CompiledProjectileMotionCheckpointV1.model_validate(changed)

    bad_digest = deepcopy(payload)
    certificate = bad_digest["certificate"]
    assert isinstance(certificate, dict)
    certificate["certificateSha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical projectile body"):
        CompiledProjectileMotionCheckpointV1.model_validate(bad_digest)


def test_reissued_cross_problem_and_cross_component_receipts_cannot_be_transplanted() -> None:
    for field, value, message in (
        ("resultProblemSpecSha256", "f" * 64, "resultProblemSpecSha256"),
        ("componentId", "other", "receipt componentId"),
    ):
        payload = _payload()
        receipt_payload = payload["receipt"]
        certificate_payload = payload["certificate"]
        assert isinstance(receipt_payload, dict)
        assert isinstance(certificate_payload, dict)
        body_payload = certificate_payload["body"]
        assert isinstance(body_payload, dict)
        receipt_payload[field] = value
        receipt = ProjectileMotionCheckpointVerificationReceiptV1.model_validate(receipt_payload)
        body_payload["receiptSha256"] = projectile_motion_checkpoint_receipt_sha256(receipt)
        _reissue(payload)

        with pytest.raises(ValidationError, match=message):
            CompiledProjectileMotionCheckpointV1.model_validate(payload)
