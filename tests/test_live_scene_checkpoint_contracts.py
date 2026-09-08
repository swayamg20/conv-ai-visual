from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.checkpoint_contracts import (
    CHECKPOINT_COMPILER_VERSION,
    CheckpointCompilerCertificateBodyV2,
    CheckpointCompilerCertificateV2,
    CheckpointVerificationObligation,
    CheckpointVerificationReceiptV2,
    CompiledCheckpointV2,
    checkpoint_certificate_sha256,
    checkpoint_receipt_sha256,
    low_level_scene_sha256,
)
from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
    choreography_plan_sha256,
    routed_choreography_beat_sha256,
)
from murmur.live_scene.completing_square_contracts import (
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.contracts import MAX_PATCH_OPERATIONS, ScenePatchDraft, SceneState
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    scene_patch_sha256,
    semantic_scene_sha256,
)
from pydantic import ValidationError


def _token(node_id: str = "lesson__eq_x2") -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "latex_token",
        "presentation": {"enter": "fade", "exit": "fade"},
        "x": 200.0,
        "y": 100.0,
        "width": 64.0,
        "height": 48.0,
        "anchor": "middle",
        "latex": "x^2",
        "style": {
            "color": "hsl(var(--chalk))",
            "fontSize": 34.0,
            "opacity": 1.0,
        },
    }


def _pose(x: float = 0.0, y: float = 75.0) -> dict[str, object]:
    return {"v": 1, "x": x, "y": y, "width": 800.0 - x, "height": 450.0}


def _artifacts() -> dict[str, object]:
    beat = RoutedChoreographyBeatV2.model_validate(
        {
            "v": 2,
            "beatId": "route-1",
            "componentKind": "completing_square",
            "componentId": "lesson",
            "route": {"intent": "advance", "targetStage": "setup"},
        }
    )
    patch = ScenePatchDraft.model_validate(
        {
            "v": 1,
            "patchId": "lesson__cp_problem",
            "narration": "Read x² + 6x = 7 as an area problem.",
            "operations": [{"op": "put", "node": _token()}],
        }
    )
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
                    {"cue": "enter", "targetIds": ["lesson__eq_x2"]},
                    {"cue": "focus", "targetIds": ["lesson__eq_x2"]},
                ],
                "durationMs": 700,
                "easing": "ease_out_quart",
                "holdAfterMs": 6500,
            },
        }
    )
    base_scene = SceneState(revision=0)
    result_scene = SceneState.model_validate({"revision": 1, "nodes": [_token()]})
    base_semantic_scene = SemanticSceneState(revision=0)
    result_semantic_scene = SemanticSceneState(
        revision=1,
        components=(
            CompletingSquareState(
                id="lesson",
                last_main_checkpoint=CompletingSquareMainCheckpoint.PROBLEM,
            ),
        ),
    )
    body = CheckpointCompilerCertificateBodyV2(
        beat_id=beat.beat_id,
        routed_beat_sha256=routed_choreography_beat_sha256(beat),
        component_id=beat.component_id,
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        base_revision=base_scene.revision,
        result_revision=result_scene.revision,
        base_low_level_scene_sha256=low_level_scene_sha256(base_scene),
        result_low_level_scene_sha256=low_level_scene_sha256(result_scene),
        base_semantic_scene_sha256=semantic_scene_sha256(base_semantic_scene),
        result_semantic_scene_sha256=semantic_scene_sha256(result_semantic_scene),
        patch_sha256=scene_patch_sha256(patch),
        receipt_sha256=checkpoint_receipt_sha256(receipt),
        presentation_checkpoint=presentation,
        choreography_sha256=choreography_plan_sha256(choreography),
    )
    certificate = CheckpointCompilerCertificateV2(
        body=body,
        certificate_sha256=checkpoint_certificate_sha256(body),
    )
    compiled = CompiledCheckpointV2(
        beat=beat,
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        patch=patch,
        receipt=receipt,
        presentation=presentation,
        choreography=choreography,
        certificate=certificate,
    )
    return {
        "beat": beat,
        "patch": patch,
        "receipt": receipt,
        "presentation": presentation,
        "choreography": choreography,
        "base_scene": base_scene,
        "result_scene": result_scene,
        "base_semantic_scene": base_semantic_scene,
        "result_semantic_scene": result_semantic_scene,
        "body": body,
        "certificate": certificate,
        "compiled": compiled,
    }


def _compiled_payload() -> dict[str, object]:
    compiled = _artifacts()["compiled"]
    assert isinstance(compiled, CompiledCheckpointV2)
    return compiled.model_dump(mode="json", by_alias=True)


def _reissue(payload: dict[str, object]) -> None:
    certificate = payload["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = CheckpointCompilerCertificateBodyV2.model_validate(body_payload)
    certificate["certificateSha256"] = checkpoint_certificate_sha256(body)


def test_compiled_checkpoint_round_trips_and_binds_the_complete_claim() -> None:
    artifacts = _artifacts()
    compiled = artifacts["compiled"]
    assert isinstance(compiled, CompiledCheckpointV2)

    wire = compiled.model_dump(mode="json", by_alias=True)
    assert CompiledCheckpointV2.model_validate(wire) == compiled
    assert set(CompiledCheckpointV2.model_fields) == {
        "beat",
        "checkpoint_id",
        "patch",
        "receipt",
        "presentation",
        "choreography",
        "certificate",
    }
    assert compiled.certificate.body.compiler_version == CHECKPOINT_COMPILER_VERSION
    assert compiled.receipt.operation_targets == tuple(
        operation.target_id for operation in compiled.patch.operations
    )
    with pytest.raises(ValidationError, match="frozen"):
        compiled.checkpoint_id = CompletingSquareCheckpointId.AREA_MODEL


def test_v2_hashes_are_stable_domain_separated_and_order_sensitive() -> None:
    artifacts = _artifacts()
    receipt = artifacts["receipt"]
    body = artifacts["body"]
    base_scene = artifacts["base_scene"]
    assert isinstance(receipt, CheckpointVerificationReceiptV2)
    assert isinstance(body, CheckpointCompilerCertificateBodyV2)
    assert isinstance(base_scene, SceneState)

    assert low_level_scene_sha256(base_scene) == (
        "9d3cb28d5d8e997cc85e3124d3d3077189020c3eb32d6d1f6af84bdc579aabc5"
    )
    assert checkpoint_receipt_sha256(receipt) == (
        "7cddaaa45d5e15d8cb0c63f2c2924c02ffddd731ce689b75e950177288d48026"
    )
    assert checkpoint_certificate_sha256(body) == (
        "358276cbfaa6247832f523dfc7a7bbefec0843a3cbe75c2a05bb27c158cae505"
    )
    assert (
        len(
            {
                low_level_scene_sha256(base_scene),
                checkpoint_receipt_sha256(receipt),
                checkpoint_certificate_sha256(body),
            }
        )
        == 3
    )

    reversed_receipt = CheckpointVerificationReceiptV2(
        component_id=receipt.component_id,
        checkpoint_id=receipt.checkpoint_id,
        operation_targets=receipt.operation_targets,
        obligation_codes=tuple(reversed(receipt.obligation_codes)),
    )
    assert checkpoint_receipt_sha256(reversed_receipt) != checkpoint_receipt_sha256(receipt)


def test_low_level_scene_hash_binds_node_order_unicode_floats_and_revision() -> None:
    first = SceneState.model_validate(
        {
            "revision": 4,
            "nodes": [
                _token("lesson__alpha"),
                {**_token("lesson__unicode"), "latex": "3\\times3=9\\;✓", "x": 300.25},
            ],
        }
    )
    reordered = SceneState(revision=4, nodes=tuple(reversed(first.nodes)))
    advanced = SceneState(revision=5, nodes=first.nodes)

    assert low_level_scene_sha256(first) != low_level_scene_sha256(reordered)
    assert low_level_scene_sha256(first) != low_level_scene_sha256(advanced)
    with pytest.raises(TypeError, match="SceneState"):
        low_level_scene_sha256({"revision": 4})  # type: ignore[arg-type]


def test_receipt_accepts_one_and_sixteen_ordered_targets_but_rejects_bad_claims() -> None:
    obligations = (CheckpointVerificationObligation.STABLE_ID,)
    one = CheckpointVerificationReceiptV2(
        component_id="lesson",
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        operation_targets=("lesson__node_00",),
        obligation_codes=obligations,
    )
    sixteen = CheckpointVerificationReceiptV2(
        component_id="lesson",
        checkpoint_id=CompletingSquareCheckpointId.PROBLEM,
        operation_targets=tuple(
            f"lesson__node_{index:02d}" for index in range(MAX_PATCH_OPERATIONS)
        ),
        obligation_codes=obligations,
    )
    assert len(one.operation_targets) == 1
    assert len(sixteen.operation_targets) == MAX_PATCH_OPERATIONS

    base = one.model_dump(mode="json", by_alias=True)
    for field, value in (
        ("operationTargets", []),
        ("operationTargets", ["lesson__same", "lesson__same"]),
        ("obligationCodes", []),
        ("obligationCodes", ["stable_id", "stable_id"]),
        ("verified", False),
        ("issuer", "compiler"),
    ):
        payload = {**base, field: value}
        with pytest.raises(ValidationError):
            CheckpointVerificationReceiptV2.model_validate(payload)

    too_many = {**base, "operationTargets": [f"lesson__n_{i}" for i in range(17)]}
    with pytest.raises(ValidationError, match="at most 16"):
        CheckpointVerificationReceiptV2.model_validate(too_many)


def test_certificate_rejects_bad_digest_revision_and_open_fields() -> None:
    artifacts = _artifacts()
    certificate = artifacts["certificate"]
    assert isinstance(certificate, CheckpointCompilerCertificateV2)
    payload = certificate.model_dump(mode="json", by_alias=True)

    wrong_digest = deepcopy(payload)
    wrong_digest["certificateSha256"] = "0" * 64
    with pytest.raises(ValidationError, match="must match"):
        CheckpointCompilerCertificateV2.model_validate(wrong_digest)

    wrong_revision = deepcopy(payload)
    body = wrong_revision["body"]
    assert isinstance(body, dict)
    body["resultRevision"] = 2
    with pytest.raises(ValidationError, match="one greater"):
        CheckpointCompilerCertificateV2.model_validate(wrong_revision)

    extra = deepcopy(payload)
    body = extra["body"]
    assert isinstance(body, dict)
    body["provider"] = "forbidden"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CheckpointCompilerCertificateV2.model_validate(extra)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("patch_id", "patchId"),
        ("narration", "narration"),
        ("receipt_component", "receipt componentId"),
        ("receipt_checkpoint", "receipt checkpointId"),
        ("presentation_checkpoint", "presentation checkpointId"),
        ("beat_hash", "routedBeatSha256"),
        ("patch_hash", "patchSha256"),
        ("receipt_hash", "receiptSha256"),
        ("choreography_hash", "choreographySha256"),
    ],
)
def test_compiled_checkpoint_rejects_mutated_bindings(mutation: str, message: str) -> None:
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
        body["receiptSha256"] = checkpoint_receipt_sha256(
            CheckpointVerificationReceiptV2.model_validate(receipt)
        )
        _reissue(payload)
    elif mutation == "receipt_checkpoint":
        receipt["checkpointId"] = "area_model"
        body["receiptSha256"] = checkpoint_receipt_sha256(
            CheckpointVerificationReceiptV2.model_validate(receipt)
        )
        _reissue(payload)
    elif mutation == "presentation_checkpoint":
        presentation["checkpointId"] = "area_model"
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
        CompiledCheckpointV2.model_validate(payload)


def test_compiled_checkpoint_binds_ordered_patch_targets() -> None:
    payload = _compiled_payload()
    patch = payload["patch"]
    receipt = payload["receipt"]
    certificate = payload["certificate"]
    assert isinstance(patch, dict)
    assert isinstance(receipt, dict)
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)

    patch["operations"].append(  # type: ignore[union-attr]
        {"op": "put", "node": _token("lesson__eq_plus")}
    )
    body["patchSha256"] = scene_patch_sha256(ScenePatchDraft.model_validate(patch))
    _reissue(payload)

    with pytest.raises(ValidationError, match="ordered patch targets"):
        CompiledCheckpointV2.model_validate(payload)
