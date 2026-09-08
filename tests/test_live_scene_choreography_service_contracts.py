from __future__ import annotations

import pytest
from murmur.live_scene.checkpoint_contracts import (
    CheckpointCompilerCertificateBodyV2,
    CheckpointCompilerCertificateV2,
    CheckpointVerificationObligation,
    CheckpointVerificationReceiptV2,
    checkpoint_certificate_sha256,
    checkpoint_receipt_sha256,
)
from murmur.live_scene.choreography_contracts import (
    ChoreographyPlanV1,
    PresentationCheckpointV1,
    RoutedChoreographyBeatV2,
    choreography_plan_sha256,
    routed_choreography_beat_sha256,
)
from murmur.live_scene.choreography_service_contracts import (
    CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    MAX_CHOREOGRAPHY_CHECKPOINTS,
    CheckpointSemanticMetadataV2,
    ChoreographySceneCheckpointEvent,
    ChoreographySceneStreamDeclinedEvent,
    dump_choreography_scene_stream_event,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    CompletingSquareState,
)
from murmur.live_scene.contracts import (
    ScenePatchDraft,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_contracts import scene_patch_sha256
from pydantic import ValidationError


def _token(node_id: str = "lesson__equation") -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "latex_token",
        "presentation": {"enter": "fade", "exit": "fade"},
        "x": 220.0,
        "y": 100.0,
        "width": 180.0,
        "height": 48.0,
        "anchor": "middle",
        "latex": "x^2+6x=7",
        "style": {"color": "hsl(var(--chalk))", "fontSize": 34.0, "opacity": 1.0},
    }


def _pose(x: float = 0.0, y: float = 75.0) -> dict[str, object]:
    return {"v": 1, "x": x, "y": y, "width": 800.0 - x, "height": 450.0}


def make_checkpoint_event(
    checkpoint_id: CompletingSquareCheckpointId = CompletingSquareCheckpointId.PROBLEM,
    *,
    narration: str = "Read x² + 6x = 7 as an area problem.",
    base_revision: int = 0,
) -> ChoreographySceneCheckpointEvent:
    component_id = "lesson"
    beat = RoutedChoreographyBeatV2.model_validate(
        {
            "v": 2,
            "beatId": "route-1",
            "componentKind": "completing_square",
            "componentId": component_id,
            "route": (
                {"intent": "clarify_corner"}
                if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
                else {"intent": "advance", "targetStage": "solve"}
            ),
        }
    )
    patch = ScenePatchDraft.model_validate(
        {
            "v": 1,
            "patchId": f"{component_id}__cp_{checkpoint_id.value}",
            "narration": narration,
            "operations": [{"op": "put", "node": _token()}],
        }
    )
    receipt = CheckpointVerificationReceiptV2(
        component_id=component_id,
        checkpoint_id=checkpoint_id,
        operation_targets=("lesson__equation",),
        obligation_codes=(
            CheckpointVerificationObligation.STABLE_ID,
            CheckpointVerificationObligation.EQUATION_IDENTITY,
        ),
    )
    presentation = PresentationCheckpointV1.model_validate(
        {
            "v": 1,
            "checkpointId": checkpoint_id.value,
            "checkpointNarration": narration,
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
                    {"cue": "enter", "targetIds": ["lesson__equation"]},
                    {"cue": "focus", "targetIds": ["lesson__equation"]},
                ],
                "durationMs": 700,
                "easing": "ease_out_quart",
                "holdAfterMs": 900,
            },
        }
    )
    if checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL:
        last_main = CompletingSquareMainCheckpoint.MISSING_CORNER
        corner_clarified = True
    else:
        last_main = CompletingSquareMainCheckpoint(checkpoint_id.value)
        corner_clarified = False
    result_component = CompletingSquareState(
        id=component_id,
        last_main_checkpoint=last_main,
        corner_clarified=corner_clarified,
    )
    result_revision = base_revision + 1
    body = CheckpointCompilerCertificateBodyV2(
        beat_id=beat.beat_id,
        routed_beat_sha256=routed_choreography_beat_sha256(beat),
        component_id=component_id,
        checkpoint_id=checkpoint_id,
        base_revision=base_revision,
        result_revision=result_revision,
        base_low_level_scene_sha256="1" * 64,
        result_low_level_scene_sha256="2" * 64,
        base_semantic_scene_sha256="3" * 64,
        result_semantic_scene_sha256="4" * 64,
        patch_sha256=scene_patch_sha256(patch),
        receipt_sha256=checkpoint_receipt_sha256(receipt),
        presentation_checkpoint=presentation,
        choreography_sha256=choreography_plan_sha256(choreography),
    )
    certificate = CheckpointCompilerCertificateV2(
        body=body,
        certificate_sha256=checkpoint_certificate_sha256(body),
    )
    return ChoreographySceneCheckpointEvent(
        generation=7,
        attempt=1,
        sequence=1,
        base_revision=base_revision,
        result_revision=result_revision,
        patch=patch,
        semantic=CheckpointSemanticMetadataV2(
            beat=beat,
            checkpoint_id=checkpoint_id,
            result_component=result_component,
            semantic_base_revision=base_revision,
            semantic_result_revision=result_revision,
            receipt=receipt,
            presentation=presentation,
            choreography=choreography,
            certificate=certificate,
        ),
    )


def _payload(
    checkpoint_id: CompletingSquareCheckpointId = CompletingSquareCheckpointId.PROBLEM,
) -> dict[str, object]:
    return make_checkpoint_event(checkpoint_id).model_dump(mode="json", by_alias=True)


def _reissue(payload: dict[str, object]) -> None:
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = CheckpointCompilerCertificateBodyV2.model_validate(body_payload)
    certificate["certificateSha256"] = checkpoint_certificate_sha256(body)


def test_checkpoint_event_round_trips_with_exact_frozen_wire_shape() -> None:
    event = make_checkpoint_event()
    payload = dump_choreography_scene_stream_event(event)

    assert CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event
    assert set(payload) == {
        "type",
        "generation",
        "attempt",
        "sequence",
        "baseRevision",
        "resultRevision",
        "patch",
        "semantic",
    }
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    assert set(semantic) == {
        "beat",
        "checkpointId",
        "resultComponent",
        "semanticBaseRevision",
        "semanticResultRevision",
        "receipt",
        "presentation",
        "choreography",
        "certificate",
    }
    assert set(semantic["resultComponent"]) == {
        "kind",
        "id",
        "lastMainCheckpoint",
        "cornerClarified",
    }
    with pytest.raises(ValidationError, match="frozen"):
        event.sequence = 2


@pytest.mark.parametrize("checkpoint", COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER)
def test_main_checkpoint_event_binds_exact_result_frontier(
    checkpoint: CompletingSquareMainCheckpoint,
) -> None:
    event = make_checkpoint_event(CompletingSquareCheckpointId(checkpoint.value))

    assert event.semantic.result_component.last_main_checkpoint is checkpoint
    assert event.semantic.checkpoint_id.value == checkpoint.value


def test_corner_detail_binds_missing_corner_and_clarification_flag() -> None:
    event = make_checkpoint_event(CompletingSquareCheckpointId.CORNER_DETAIL)
    component = event.semantic.result_component

    assert component.last_main_checkpoint is CompletingSquareMainCheckpoint.MISSING_CORNER
    assert component.corner_clarified is True

    for last_main, clarified in (
        (CompletingSquareMainCheckpoint.MISSING_CORNER, False),
        (CompletingSquareMainCheckpoint.BALANCE_AND_COMPLETE, True),
    ):
        payload = _payload(CompletingSquareCheckpointId.CORNER_DETAIL)
        semantic = payload["semantic"]
        assert isinstance(semantic, dict)
        result_component = semantic["resultComponent"]
        assert isinstance(result_component, dict)
        result_component["lastMainCheckpoint"] = last_main.value
        result_component["cornerClarified"] = clarified
        with pytest.raises(ValidationError, match="corner_detail resultComponent"):
            ChoreographySceneCheckpointEvent.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("component", "resultComponent id"),
        ("main_frontier", "lastMainCheckpoint"),
        ("semantic_gap", "semanticResultRevision"),
        ("event_gap", "resultRevision"),
        ("sequence", "less than or equal to"),
        ("extra", "Extra inputs"),
    ],
)
def test_checkpoint_event_rejects_open_or_relational_mutations(
    mutation: str,
    message: str,
) -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    if mutation == "component":
        result_component = semantic["resultComponent"]
        assert isinstance(result_component, dict)
        result_component["id"] = "other"
    elif mutation == "main_frontier":
        result_component = semantic["resultComponent"]
        assert isinstance(result_component, dict)
        result_component["lastMainCheckpoint"] = "area_model"
    elif mutation == "semantic_gap":
        semantic["semanticResultRevision"] = 2
    elif mutation == "event_gap":
        payload["resultRevision"] = 2
    elif mutation == "sequence":
        payload["sequence"] = MAX_CHOREOGRAPHY_CHECKPOINTS + 1
    else:
        semantic["providerTrace"] = {"opaque": True}

    with pytest.raises(ValidationError, match=message):
        ChoreographySceneCheckpointEvent.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("patch", "patchSha256"),
        ("receipt", "receiptSha256"),
        ("presentation", "presentationCheckpoint"),
        ("certificate_revision", "semanticBaseRevision"),
    ],
)
def test_event_reconstructs_compiled_checkpoint_and_rejects_integrity_mutations(
    mutation: str,
    message: str,
) -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    if mutation == "patch":
        patch = payload["patch"]
        assert isinstance(patch, dict)
        operations = patch["operations"]
        assert isinstance(operations, list)
        operation = operations[0]
        assert isinstance(operation, dict)
        node = operation["node"]
        assert isinstance(node, dict)
        node["latex"] = "x^2+8x=9"
    elif mutation == "receipt":
        receipt = semantic["receipt"]
        assert isinstance(receipt, dict)
        receipt["obligationCodes"] = ["stable_id"]
    elif mutation == "presentation":
        presentation = semantic["presentation"]
        assert isinstance(presentation, dict)
        result_viewports = presentation["resultViewports"]
        assert isinstance(result_viewports, dict)
        cinematic = result_viewports["cinematic"]
        assert isinstance(cinematic, dict)
        cinematic["x"] = 41.0
        cinematic["width"] = 759.0
    else:
        certificate = semantic["certificate"]
        assert isinstance(certificate, dict)
        body = certificate["body"]
        assert isinstance(body, dict)
        body["baseRevision"] = 1
        body["resultRevision"] = 2
        _reissue(payload)

    with pytest.raises(ValidationError, match=message):
        ChoreographySceneCheckpointEvent.model_validate(payload)


def test_union_accepts_existing_lifecycle_and_separate_declined_terminal() -> None:
    events = (
        SceneStreamStartedEvent(generation=3, attempt=1, base_revision=0),
        make_checkpoint_event(),
        ChoreographySceneStreamDeclinedEvent(
            generation=3,
            attempt=1,
            final_revision=0,
            reason_code="unsupported_intent",
            message="This lesson cannot visualize that request yet.",
        ),
        SceneStreamRepairingEvent(
            generation=3,
            from_attempt=1,
            to_attempt=2,
            last_accepted_revision=0,
            message="Repairing the lesson stream.",
        ),
        SceneStreamCompletedEvent(
            generation=3,
            final_revision=1,
            patch_count=1,
            first_patch_ms=1.0,
            total_ms=2.0,
            repaired=False,
        ),
        SceneStreamFailedEvent(
            generation=3,
            attempt=1,
            code="compiler_failed",
            message="The lesson could not be compiled.",
            last_accepted_revision=0,
            retryable=False,
        ),
    )

    for event in events:
        payload = event.model_dump(mode="json", by_alias=True)
        assert CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event

    raw_patch = _payload()
    raw_patch["type"] = "scene_patch"
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(raw_patch)


def test_rehashed_certificate_component_relabel_is_rejected() -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body = certificate["body"]
    assert isinstance(body, dict)
    body["componentId"] = "other"
    _reissue(payload)

    with pytest.raises(ValidationError, match="certificate componentId"):
        ChoreographySceneCheckpointEvent.model_validate(payload)
