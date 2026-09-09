from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.checkpoint_contracts import (
    CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    CHECKPOINT_COMPILER_VERSION,
    CHECKPOINT_RECEIPT_HASH_DOMAIN,
    CheckpointCompilerCertificateBodyV2,
    CheckpointCompilerCertificateV2,
    CheckpointVerificationObligation,
    CheckpointVerificationReceiptV2,
    CompiledCheckpointV2,
    checkpoint_receipt_sha256,
    low_level_scene_sha256,
)
from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV3,
    choreography_plan_sha256,
    routed_choreography_beat_v3_sha256,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
    completing_square_problem_sha256,
)
from murmur.live_scene.contracts import MAX_PATCH_OPERATIONS, ScenePatchDraft, SceneState
from murmur.live_scene.parametric_checkpoint_contracts import (
    CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN,
    CHECKPOINT_CERTIFICATE_V3_VERSION,
    CHECKPOINT_COMPILER_V3_VERSION,
    CHECKPOINT_RECEIPT_V3_HASH_DOMAIN,
    CheckpointCompilerCertificateBodyV3,
    CheckpointCompilerCertificateV3,
    CheckpointVerificationObligationV3,
    CheckpointVerificationReceiptV3,
    CompiledCheckpointV3,
    checkpoint_certificate_v3_sha256,
    checkpoint_receipt_v3_sha256,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from murmur.live_scene.semantic_integrity import canonical_sha256
from pydantic import ValidationError


def _problem(
    linear_coefficient: int = 8, right_hand_side: int = 20
) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=linear_coefficient,
        rightHandSide=right_hand_side,
    )


def _token(node_id: str = "lesson__eq_linear", latex: str = "8x") -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "latex_token",
        "presentation": {"enter": "fade", "exit": "fade"},
        "x": 200.0,
        "y": 100.0,
        "width": 64.0,
        "height": 48.0,
        "anchor": "middle",
        "latex": latex,
        "style": {
            "color": "hsl(var(--chalk))",
            "fontSize": 34.0,
            "opacity": 1.0,
        },
    }


def _pose(x: float = 0.0, y: float = 75.0) -> dict[str, object]:
    return {"v": 1, "x": x, "y": y, "width": 800.0 - x, "height": 450.0}


def _artifacts(
    problem: CompletingSquareProblemSpecV1 | None = None,
) -> dict[str, object]:
    problem = problem or _problem()
    beat = RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": "route-1",
            "componentKind": "completing_square_parametric",
            "componentId": "lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "advance", "targetStage": "setup"},
        }
    )
    patch = ScenePatchDraft.model_validate(
        {
            "v": 1,
            "patchId": "lesson__cp_problem",
            "narration": (
                f"Read x² + {problem.linear_coefficient}x = "
                f"{problem.right_hand_side} as an area problem."
            ),
            "operations": [
                {
                    "op": "put",
                    "node": _token(latex=f"{problem.linear_coefficient}x"),
                }
            ],
        }
    )
    problem_digest = completing_square_problem_sha256(problem)
    receipt = CheckpointVerificationReceiptV3(
        component_id="lesson",
        problem_spec_sha256=problem_digest,
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        operation_targets=("lesson__eq_linear",),
        obligation_codes=(
            CheckpointVerificationObligationV3.STABLE_ID,
            CheckpointVerificationObligationV3.BOARD_BOUNDS,
            CheckpointVerificationObligationV3.EQUATION_IDENTITY,
            CheckpointVerificationObligationV3.PROBLEM_IDENTITY,
            CheckpointVerificationObligationV3.CAPTION_FACTS,
            CheckpointVerificationObligationV3.AUTHORED_TIMING,
        ),
    )
    presentation = PresentationCheckpointV1.model_validate(
        {
            "v": 1,
            "checkpointId": "problem",
            "checkpointNarration": patch.narration,
            "baseViewports": {"cinematic": _pose(), "compact": _pose(70.0, 35.0)},
            "resultViewports": {
                "cinematic": _pose(40.0, 75.0),
                "compact": _pose(95.0, 40.0),
            },
            "transientFree": True,
        }
    )
    choreography = ChoreographyPlanV1.model_validate(
        {
            "v": 1,
            "phase": {
                "cues": [
                    {"cue": "enter", "targetIds": ["lesson__eq_linear"]},
                    {"cue": "focus", "targetIds": ["lesson__eq_linear"]},
                ],
                "durationMs": 650,
                "easing": "ease_out_quart",
                "holdAfterMs": 4800,
            },
        }
    )
    base_scene = SceneState(revision=0)
    result_scene = SceneState.model_validate(
        {"revision": 1, "nodes": [_token(latex=f"{problem.linear_coefficient}x")]}
    )
    base_semantic_scene = SemanticSceneState(revision=0)
    result_semantic_scene = SemanticSceneState(
        revision=1,
        components=(
            ParametricCompletingSquareStateV1(
                id="lesson",
                problem_spec=problem,
                last_main_checkpoint=CompletingSquareMainCheckpoint.PROBLEM,
            ),
        ),
    )
    body = CheckpointCompilerCertificateBodyV3(
        beat_id=beat.beat_id,
        routed_beat_sha256=routed_choreography_beat_v3_sha256(beat),
        component_id=beat.component_id,
        problem_spec_sha256=problem_digest,
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        base_revision=base_scene.revision,
        result_revision=result_scene.revision,
        base_low_level_scene_sha256=low_level_scene_sha256(base_scene),
        result_low_level_scene_sha256=low_level_scene_sha256(result_scene),
        base_semantic_scene_sha256=semantic_scene_sha256(base_semantic_scene),
        result_semantic_scene_sha256=semantic_scene_sha256(result_semantic_scene),
        patch_sha256=scene_patch_sha256(patch),
        receipt_sha256=checkpoint_receipt_v3_sha256(receipt),
        presentation_checkpoint=presentation,
        choreography_sha256=choreography_plan_sha256(choreography),
    )
    certificate = CheckpointCompilerCertificateV3(
        body=body,
        certificate_sha256=checkpoint_certificate_v3_sha256(body),
    )
    compiled = CompiledCheckpointV3(
        beat=beat,
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        patch=patch,
        receipt=receipt,
        presentation=presentation,
        choreography=choreography,
        certificate=certificate,
    )
    return {
        "problem": problem,
        "beat": beat,
        "patch": patch,
        "receipt": receipt,
        "presentation": presentation,
        "choreography": choreography,
        "base_scene": base_scene,
        "body": body,
        "certificate": certificate,
        "compiled": compiled,
    }


def _compiled_payload(
    problem: CompletingSquareProblemSpecV1 | None = None,
) -> dict[str, object]:
    compiled = _artifacts(problem)["compiled"]
    assert isinstance(compiled, CompiledCheckpointV3)
    return compiled.model_dump(mode="json", by_alias=True)


def _reissue(payload: dict[str, object]) -> None:
    certificate = payload["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = CheckpointCompilerCertificateBodyV3.model_validate(body_payload)
    certificate["certificateSha256"] = checkpoint_certificate_v3_sha256(body)


def test_compiled_checkpoint_v3_round_trips_with_an_exact_problem_bound_shape() -> None:
    artifacts = _artifacts()
    compiled = artifacts["compiled"]
    problem = artifacts["problem"]
    assert isinstance(compiled, CompiledCheckpointV3)
    assert isinstance(problem, CompletingSquareProblemSpecV1)

    wire = compiled.model_dump(mode="json", by_alias=True)

    assert CompiledCheckpointV3.model_validate(wire) == compiled
    assert set(CheckpointVerificationReceiptV3.model_fields) == {
        "issuer",
        "component_kind",
        "component_id",
        "problem_spec_sha256",
        "checkpoint_id",
        "operation_targets",
        "obligation_codes",
        "verified",
    }
    assert set(CheckpointCompilerCertificateBodyV3.model_fields) == {
        "v",
        "issuer",
        "compiler_version",
        "canonicalization",
        "hash_algorithm",
        "beat_id",
        "routed_beat_sha256",
        "component_kind",
        "component_id",
        "problem_spec_sha256",
        "checkpoint_id",
        "base_revision",
        "result_revision",
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
    assert set(CheckpointCompilerCertificateV3.model_fields) == {
        "body",
        "certificate_sha256",
    }
    assert set(CompiledCheckpointV3.model_fields) == {
        "beat",
        "checkpoint_id",
        "patch",
        "receipt",
        "presentation",
        "choreography",
        "certificate",
    }
    assert compiled.receipt.problem_spec_sha256 == completing_square_problem_sha256(problem)
    assert compiled.certificate.body.problem_spec_sha256 == completing_square_problem_sha256(
        problem
    )
    assert compiled.certificate.body.compiler_version == CHECKPOINT_COMPILER_V3_VERSION
    assert compiled.receipt.operation_targets == tuple(
        operation.target_id for operation in compiled.patch.operations
    )

    with pytest.raises(ValidationError, match="frozen"):
        compiled.receipt.problem_spec_sha256 = "0" * 64


def test_v3_hashes_are_canonical_problem_bound_and_domain_separated() -> None:
    primary = _artifacts()
    another = _artifacts(_problem(6, 7))
    receipt = primary["receipt"]
    body = primary["body"]
    other_receipt = another["receipt"]
    other_body = another["body"]
    assert isinstance(receipt, CheckpointVerificationReceiptV3)
    assert isinstance(body, CheckpointCompilerCertificateBodyV3)
    assert isinstance(other_receipt, CheckpointVerificationReceiptV3)
    assert isinstance(other_body, CheckpointCompilerCertificateBodyV3)

    receipt_payload = receipt.model_dump(mode="json", by_alias=True)
    body_payload = body.model_dump(mode="json", by_alias=True)
    receipt_digest = checkpoint_receipt_v3_sha256(receipt)
    certificate_digest = checkpoint_certificate_v3_sha256(body)

    assert receipt_digest == "49fdfcc6b2a599fd48d43d61915368ffd1962035c743fb2912b01c7e4857463d"
    assert certificate_digest == (
        "8b580782b053247f7b36321cdd86f97e8e190f6f52abb76653d9cc3a1e21ea0c"
    )
    assert receipt_digest == canonical_sha256(
        dict(reversed(tuple(receipt_payload.items()))),
        domain=CHECKPOINT_RECEIPT_V3_HASH_DOMAIN,
    )
    assert certificate_digest == canonical_sha256(
        dict(reversed(tuple(body_payload.items()))),
        domain=CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN,
    )
    assert receipt_digest != checkpoint_receipt_v3_sha256(other_receipt)
    assert certificate_digest != checkpoint_certificate_v3_sha256(other_body)
    assert receipt_digest != canonical_sha256(
        receipt_payload,
        domain=CHECKPOINT_RECEIPT_HASH_DOMAIN,
    )
    assert certificate_digest != canonical_sha256(
        body_payload,
        domain=CHECKPOINT_CERTIFICATE_HASH_DOMAIN,
    )


def test_v3_addition_leaves_v2_contracts_versions_and_receipt_hash_sealed() -> None:
    receipt = CheckpointVerificationReceiptV2(
        component_id="lesson",
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        operation_targets=("lesson__eq_x2",),
        obligation_codes=(
            CheckpointVerificationObligation.STABLE_ID,
            CheckpointVerificationObligation.BOARD_BOUNDS,
            CheckpointVerificationObligation.EQUATION_IDENTITY,
        ),
    )

    assert CHECKPOINT_CERTIFICATE_V3_VERSION == 3
    assert CHECKPOINT_COMPILER_VERSION == "murmur.completing_square_choreography.v1"
    assert CHECKPOINT_COMPILER_V3_VERSION == "murmur.completing_square_choreography.v2"
    assert CHECKPOINT_RECEIPT_HASH_DOMAIN == "murmur:checkpoint-receipt:v2"
    assert CHECKPOINT_CERTIFICATE_HASH_DOMAIN == "murmur:checkpoint-certificate:v2"
    assert CHECKPOINT_RECEIPT_V3_HASH_DOMAIN == "murmur:checkpoint-receipt:v3"
    assert CHECKPOINT_CERTIFICATE_V3_HASH_DOMAIN == "murmur:checkpoint-certificate:v3"
    assert checkpoint_receipt_sha256(receipt) == (
        "7cddaaa45d5e15d8cb0c63f2c2924c02ffddd731ce689b75e950177288d48026"
    )
    assert not issubclass(CheckpointCompilerCertificateBodyV3, CheckpointCompilerCertificateBodyV2)
    assert not issubclass(CheckpointCompilerCertificateV3, CheckpointCompilerCertificateV2)
    assert not issubclass(CompiledCheckpointV3, CompiledCheckpointV2)


def test_v3_obligations_extend_a_sealed_copy_without_widening_v2() -> None:
    v2_values = {obligation.value for obligation in CheckpointVerificationObligation}
    v3_values = {obligation.value for obligation in CheckpointVerificationObligationV3}

    assert v3_values == v2_values | {
        "problem_identity",
        "caption_facts",
        "authored_timing",
    }
    assert "problem_identity" not in v2_values
    assert "caption_facts" not in v2_values
    assert "authored_timing" not in v2_values


def test_v3_receipt_preserves_closed_ordered_claim_budgets() -> None:
    base = _artifacts()["receipt"]
    assert isinstance(base, CheckpointVerificationReceiptV3)
    payload = base.model_dump(mode="json", by_alias=True)

    for field, value in (
        ("operationTargets", []),
        ("operationTargets", ["lesson__same", "lesson__same"]),
        ("obligationCodes", []),
        ("obligationCodes", ["stable_id", "stable_id"]),
        ("verified", False),
        ("issuer", "compiler"),
        ("componentKind", "completing_square"),
    ):
        with pytest.raises(ValidationError):
            CheckpointVerificationReceiptV3.model_validate({**payload, field: value})

    too_many = {
        **payload,
        "operationTargets": [f"lesson__node_{index:02d}" for index in range(17)],
    }
    with pytest.raises(ValidationError, match="at most 16"):
        CheckpointVerificationReceiptV3.model_validate(too_many)

    maximum = {
        **payload,
        "operationTargets": [f"lesson__node_{index:02d}" for index in range(MAX_PATCH_OPERATIONS)],
    }
    assert (
        len(CheckpointVerificationReceiptV3.model_validate(maximum).operation_targets)
        == MAX_PATCH_OPERATIONS
    )


def test_v3_models_reject_noncanonical_versions_unknown_fields_and_bad_digest() -> None:
    payload = _compiled_payload()

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
            CompiledCheckpointV3.model_validate(changed)

    for version in (True, 3.0, "3"):
        changed = deepcopy(payload)
        certificate = changed["certificate"]
        assert isinstance(certificate, dict)
        body = certificate["body"]
        assert isinstance(body, dict)
        body["v"] = version
        with pytest.raises(ValidationError, match="strict integer"):
            CompiledCheckpointV3.model_validate(changed)

    wrong_version = deepcopy(payload)
    certificate = wrong_version["certificate"]
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)
    body["v"] = 2
    with pytest.raises(ValidationError, match="Input should be 3"):
        CompiledCheckpointV3.model_validate(wrong_version)

    wrong_digest = deepcopy(payload)
    certificate = wrong_digest["certificate"]
    assert isinstance(certificate, dict)
    certificate["certificateSha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical checkpoint body"):
        CompiledCheckpointV3.model_validate(wrong_digest)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("patch_id", "patchId"),
        ("narration", "narration"),
        ("receipt_component", "receipt componentId"),
        ("receipt_problem", "receipt problemSpecSha256"),
        ("receipt_checkpoint", "receipt checkpointId"),
        ("presentation_checkpoint", "presentation checkpointId"),
        ("beat_hash", "routedBeatSha256"),
        ("certificate_problem", "certificate problemSpecSha256"),
        ("patch_hash", "patchSha256"),
        ("receipt_hash", "receiptSha256"),
        ("choreography_hash", "choreographySha256"),
    ],
)
def test_compiled_checkpoint_v3_rejects_mutated_bindings(
    mutation: str,
    message: str,
) -> None:
    payload = _compiled_payload()
    patch = payload["patch"]
    receipt = payload["receipt"]
    presentation = payload["presentation"]
    certificate = payload["certificate"]
    assert isinstance(patch, dict)
    assert isinstance(receipt, dict)
    assert isinstance(presentation, dict)
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    if mutation == "patch_id":
        patch["patchId"] = "lesson__cp_area_model"
    elif mutation == "narration":
        patch["narration"] = "Changed narration."
        body["patchSha256"] = scene_patch_sha256(ScenePatchDraft.model_validate(patch))
        _reissue(payload)
    elif mutation == "receipt_component":
        receipt["componentId"] = "other"
        body["receiptSha256"] = checkpoint_receipt_v3_sha256(
            CheckpointVerificationReceiptV3.model_validate(receipt)
        )
        _reissue(payload)
    elif mutation == "receipt_problem":
        receipt["problemSpecSha256"] = completing_square_problem_sha256(_problem(6, 7))
        body["receiptSha256"] = checkpoint_receipt_v3_sha256(
            CheckpointVerificationReceiptV3.model_validate(receipt)
        )
        _reissue(payload)
    elif mutation == "receipt_checkpoint":
        receipt["checkpointId"] = "area_model"
        body["receiptSha256"] = checkpoint_receipt_v3_sha256(
            CheckpointVerificationReceiptV3.model_validate(receipt)
        )
        _reissue(payload)
    elif mutation == "presentation_checkpoint":
        presentation["checkpointId"] = "area_model"
    elif mutation == "beat_hash":
        body["routedBeatSha256"] = "0" * 64
        _reissue(payload)
    elif mutation == "certificate_problem":
        body["problemSpecSha256"] = completing_square_problem_sha256(_problem(6, 7))
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
        CompiledCheckpointV3.model_validate(payload)


def test_v3_rejects_a_reissued_receipt_transplanted_from_another_problem() -> None:
    primary = _compiled_payload(_problem(8, 20))
    donor = _compiled_payload(_problem(6, 7))
    donor_receipt = donor["receipt"]
    assert isinstance(donor_receipt, dict)

    primary["receipt"] = deepcopy(donor_receipt)
    certificate = primary["certificate"]
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)
    body["receiptSha256"] = checkpoint_receipt_v3_sha256(
        CheckpointVerificationReceiptV3.model_validate(donor_receipt)
    )
    _reissue(primary)

    with pytest.raises(ValidationError, match="receipt problemSpecSha256"):
        CompiledCheckpointV3.model_validate(primary)


def test_v3_rejects_a_valid_certificate_transplanted_from_another_problem() -> None:
    primary = _compiled_payload(_problem(8, 20))
    donor = _compiled_payload(_problem(6, 7))

    primary["certificate"] = deepcopy(donor["certificate"])

    with pytest.raises(ValidationError, match="routedBeatSha256"):
        CompiledCheckpointV3.model_validate(primary)


def test_v3_rejects_a_reissued_problem_mutation_that_leaves_the_receipt_behind() -> None:
    payload = _compiled_payload(_problem(8, 20))
    beat = payload["beat"]
    certificate = payload["certificate"]
    assert isinstance(beat, dict)
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    other = _problem(6, 7)
    beat["problemSpec"] = other.model_dump(mode="json", by_alias=True)
    changed_beat = RoutedChoreographyBeatV3.model_validate(beat)
    body["routedBeatSha256"] = routed_choreography_beat_v3_sha256(changed_beat)
    body["problemSpecSha256"] = completing_square_problem_sha256(other)
    _reissue(payload)

    with pytest.raises(ValidationError, match="receipt problemSpecSha256"):
        CompiledCheckpointV3.model_validate(payload)
