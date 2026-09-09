from __future__ import annotations

from copy import deepcopy
from functools import cache

import pytest
from murmur.live_scene.choreography_contracts import (
    CompletingSquareStage,
    RoutedChoreographyBeatV3,
)
from murmur.live_scene.choreography_service_contracts import (
    CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
    CompletingSquareCheckpointId,
    CompletingSquareMainCheckpoint,
    ParametricCompletingSquareStateV1,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.completing_square_problem_parser import (
    CompletingSquareProblemFailureReason,
)
from murmur.live_scene.contracts import (
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.parametric_checkpoint_compiler import (
    CompiledParametricCheckpointBeatV3,
    compile_parametric_checkpoint_beat,
)
from murmur.live_scene.parametric_checkpoint_contracts import (
    CheckpointCompilerCertificateBodyV3,
    CompiledCheckpointV3,
    checkpoint_certificate_v3_sha256,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS,
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricCheckpointSemanticMetadataV3,
    ParametricChoreographyFailureCode,
    ParametricChoreographySceneCheckpointEventV3,
    ParametricChoreographySceneStreamDeclinedEventV3,
    ParametricChoreographySceneStreamFailedEventV3,
    dump_parametric_choreography_scene_stream_event,
)
from murmur.live_scene.parametric_completing_square_compiler import (
    materialize_parametric_nodes,
)
from murmur.live_scene.semantic_contracts import (
    SemanticSceneState,
    VisualActAbstainReason,
)
from pydantic import ValidationError


def _problem(
    linear_coefficient: int = 8,
    right_hand_side: int = 20,
) -> CompletingSquareProblemSpecV1:
    return CompletingSquareProblemSpecV1(
        linearCoefficient=linear_coefficient,
        rightHandSide=right_hand_side,
    )


@cache
def _compiled(
    linear_coefficient: int = 8,
    right_hand_side: int = 20,
) -> CompiledParametricCheckpointBeatV3:
    problem = _problem(linear_coefficient, right_hand_side)
    beat = RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": "beat-parametric-wire",
            "componentKind": "completing_square_parametric",
            "componentId": "square-lesson",
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "advance", "targetStage": CompletingSquareStage.SOLVE},
        }
    )
    return compile_parametric_checkpoint_beat(
        beat,
        base_scene=SceneState(revision=0),
        base_semantic_scene=SemanticSceneState(revision=0),
    )


def _event_from_compiled_checkpoint(
    checkpoint: CompiledCheckpointV3,
    *,
    result_component: ParametricCompletingSquareStateV1,
    sequence: int,
) -> ParametricChoreographySceneCheckpointEventV3:
    body = checkpoint.certificate.body
    return ParametricChoreographySceneCheckpointEventV3(
        generation=9,
        attempt=1,
        sequence=sequence,
        base_revision=body.base_revision,
        result_revision=body.result_revision,
        patch=checkpoint.patch,
        semantic=ParametricCheckpointSemanticMetadataV3(
            problem_spec=checkpoint.beat.problem_spec,
            beat=checkpoint.beat,
            checkpoint_id=checkpoint.checkpoint_id,
            result_component=result_component,
            semantic_base_revision=body.base_revision,
            semantic_result_revision=body.result_revision,
            semantic_base_certificate_sha256=body.previous_certificate_sha256,
            semantic_result_certificate_sha256=(checkpoint.certificate.certificate_sha256),
            receipt=checkpoint.receipt,
            presentation=checkpoint.presentation,
            choreography=checkpoint.choreography,
            certificate=checkpoint.certificate,
        ),
    )


def _checkpoint_event(
    index: int = 0,
    *,
    linear_coefficient: int = 8,
    right_hand_side: int = 20,
) -> ParametricChoreographySceneCheckpointEventV3:
    checkpoint = _compiled(linear_coefficient, right_hand_side).checkpoints[index]
    return _event_from_compiled_checkpoint(
        checkpoint,
        result_component=ParametricCompletingSquareStateV1(
            id=checkpoint.beat.component_id,
            problem_spec=checkpoint.beat.problem_spec,
            last_main_checkpoint=CompletingSquareMainCheckpoint(checkpoint.checkpoint_id.value),
        ),
        sequence=index + 1,
    )


@cache
def _corner_checkpoint_event() -> ParametricChoreographySceneCheckpointEventV3:
    problem = _problem()
    component = ParametricCompletingSquareStateV1(
        id="square-lesson",
        problem_spec=problem,
        last_main_checkpoint=CompletingSquareMainCheckpoint.MISSING_CORNER,
    )
    beat = RoutedChoreographyBeatV3.model_validate(
        {
            "v": 3,
            "beatId": "beat-parametric-corner-wire",
            "componentKind": "completing_square_parametric",
            "componentId": component.id,
            "problemSpec": problem.model_dump(mode="json", by_alias=True),
            "route": {"intent": "clarify_corner"},
        }
    )
    compiled = compile_parametric_checkpoint_beat(
        beat,
        base_scene=SceneState(revision=5, nodes=materialize_parametric_nodes(component)),
        base_semantic_scene=SemanticSceneState(
            revision=5,
            components=(component,),
            certificate_head_sha256="a" * 64,
        ),
    )
    checkpoint = compiled.checkpoints[0]
    return _event_from_compiled_checkpoint(
        checkpoint,
        result_component=component.model_copy(update={"corner_clarified": True}),
        sequence=6,
    )


def _payload(index: int = 0) -> dict[str, object]:
    return _checkpoint_event(index).model_dump(mode="json", by_alias=True)


def _reissue_certificate(payload: dict[str, object]) -> None:
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = CheckpointCompilerCertificateBodyV3.model_validate(body_payload)
    certificate["certificateSha256"] = checkpoint_certificate_v3_sha256(body)


def test_checkpoint_event_round_trips_with_exact_v3_wire_shape() -> None:
    event = _checkpoint_event()
    payload = dump_parametric_choreography_scene_stream_event(event)

    assert PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event
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
    assert payload["type"] == "parametric_choreography_scene_checkpoint"
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    assert set(semantic) == {
        "problemSpec",
        "beat",
        "checkpointId",
        "resultComponent",
        "semanticBaseRevision",
        "semanticResultRevision",
        "semanticBaseCertificateSha256",
        "semanticResultCertificateSha256",
        "receipt",
        "presentation",
        "choreography",
        "certificate",
    }
    assert set(semantic["problemSpec"]) == {"v", "linearCoefficient", "rightHandSide"}
    assert set(semantic["resultComponent"]) == {
        "kind",
        "id",
        "problemSpec",
        "lastMainCheckpoint",
        "cornerClarified",
    }
    assert semantic["semanticBaseCertificateSha256"] is None
    assert (
        semantic["semanticResultCertificateSha256"] == semantic["certificate"]["certificateSha256"]
    )
    with pytest.raises(ValidationError, match="frozen"):
        event.sequence = 2


@pytest.mark.parametrize(
    ("index", "checkpoint"),
    tuple(enumerate(COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER)),
)
def test_every_main_checkpoint_binds_its_problem_frontier_and_chain(
    index: int,
    checkpoint: CompletingSquareMainCheckpoint,
) -> None:
    event = _checkpoint_event(index)
    semantic = event.semantic

    assert event.sequence == index + 1
    assert semantic.checkpoint_id is CompletingSquareCheckpointId(checkpoint.value)
    assert semantic.result_component.last_main_checkpoint is checkpoint
    assert semantic.problem_spec == semantic.beat.problem_spec
    assert semantic.problem_spec == semantic.result_component.problem_spec
    assert (
        semantic.semantic_base_certificate_sha256
        == semantic.certificate.body.previous_certificate_sha256
    )
    assert semantic.semantic_result_certificate_sha256 == semantic.certificate.certificate_sha256


def test_corner_detail_binds_the_existing_problem_and_one_shot_frontier() -> None:
    event = _corner_checkpoint_event()
    semantic = event.semantic

    assert semantic.checkpoint_id is CompletingSquareCheckpointId.CORNER_DETAIL
    assert semantic.result_component.last_main_checkpoint is (
        CompletingSquareMainCheckpoint.MISSING_CORNER
    )
    assert semantic.result_component.corner_clarified is True
    assert semantic.semantic_base_certificate_sha256 == "a" * 64
    assert semantic.semantic_result_certificate_sha256 == semantic.certificate.certificate_sha256

    payload = event.model_dump(mode="json", by_alias=True)
    payload_semantic = payload["semantic"]
    assert isinstance(payload_semantic, dict)
    component = payload_semantic["resultComponent"]
    assert isinstance(component, dict)
    component["cornerClarified"] = False
    with pytest.raises(ValidationError, match="corner_detail resultComponent"):
        ParametricChoreographySceneCheckpointEventV3.model_validate(payload)


@pytest.mark.parametrize("layer", ["receipt", "certificate", "semantic"])
def test_cross_problem_checkpoint_claims_cannot_be_transplanted(layer: str) -> None:
    payload = _payload()
    donor = _checkpoint_event(linear_coefficient=6, right_hand_side=7).model_dump(
        mode="json",
        by_alias=True,
    )
    semantic = payload["semantic"]
    donor_semantic = donor["semantic"]
    assert isinstance(semantic, dict)
    assert isinstance(donor_semantic, dict)

    if layer == "receipt":
        semantic["receipt"] = donor_semantic["receipt"]
    elif layer == "certificate":
        semantic["certificate"] = donor_semantic["certificate"]
        semantic["semanticResultCertificateSha256"] = donor_semantic[
            "semanticResultCertificateSha256"
        ]
    else:
        payload["semantic"] = donor_semantic

    with pytest.raises(ValidationError):
        ParametricChoreographySceneCheckpointEventV3.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("metadata_problem", "problemSpec must match routed beat"),
        ("beat_problem", "problemSpec must match routed beat"),
        ("component_problem", "resultComponent problemSpec"),
        ("receipt_problem", "receipt problemSpecSha256"),
        ("certificate_problem", "certificate problemSpecSha256"),
        ("base_chain", "previousCertificateSha256"),
        ("result_chain", "result chain head"),
        ("component", "resultComponent id"),
        ("frontier", "lastMainCheckpoint"),
        ("semantic_revision", "semanticResultRevision"),
        ("event_revision", "resultRevision"),
        ("sequence", "less than or equal to"),
    ],
)
def test_checkpoint_event_rejects_problem_frontier_revision_and_chain_splices(
    mutation: str,
    message: str,
) -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    other_problem = _problem(6, 7).model_dump(mode="json", by_alias=True)

    if mutation == "metadata_problem":
        semantic["problemSpec"] = other_problem
    elif mutation == "beat_problem":
        beat = semantic["beat"]
        assert isinstance(beat, dict)
        beat["problemSpec"] = other_problem
    elif mutation == "component_problem":
        component = semantic["resultComponent"]
        assert isinstance(component, dict)
        component["problemSpec"] = other_problem
    elif mutation == "receipt_problem":
        receipt = semantic["receipt"]
        assert isinstance(receipt, dict)
        receipt["problemSpecSha256"] = "f" * 64
    elif mutation == "certificate_problem":
        certificate = semantic["certificate"]
        assert isinstance(certificate, dict)
        body = certificate["body"]
        assert isinstance(body, dict)
        body["problemSpecSha256"] = "f" * 64
        _reissue_certificate(payload)
    elif mutation == "base_chain":
        semantic["semanticBaseCertificateSha256"] = "a" * 64
    elif mutation == "result_chain":
        semantic["semanticResultCertificateSha256"] = "f" * 64
    elif mutation == "component":
        component = semantic["resultComponent"]
        assert isinstance(component, dict)
        component["id"] = "other"
    elif mutation == "frontier":
        component = semantic["resultComponent"]
        assert isinstance(component, dict)
        component["lastMainCheckpoint"] = "area_model"
    elif mutation == "semantic_revision":
        semantic["semanticResultRevision"] = 2
    elif mutation == "event_revision":
        payload["resultRevision"] = 2
    else:
        payload["sequence"] = MAX_PARAMETRIC_CHOREOGRAPHY_CHECKPOINTS + 1

    with pytest.raises(ValidationError, match=message):
        ParametricChoreographySceneCheckpointEventV3.model_validate(payload)


@pytest.mark.parametrize("level", ["event", "semantic", "problem", "certificate"])
def test_checkpoint_wire_rejects_unknown_fields_at_every_trust_boundary(level: str) -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    if level == "event":
        payload["providerTrace"] = "private"
    elif level == "semantic":
        semantic["providerTrace"] = "private"
    elif level == "problem":
        problem = semantic["problemSpec"]
        assert isinstance(problem, dict)
        problem["cornerValue"] = 16
    else:
        certificate = semantic["certificate"]
        assert isinstance(certificate, dict)
        certificate["signature"] = "not-a-signature"

    with pytest.raises(ValidationError, match="Extra inputs"):
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "reason",
    [*VisualActAbstainReason, *CompletingSquareProblemFailureReason],
)
def test_decline_reason_is_the_exact_six_value_closed_union(
    reason: VisualActAbstainReason | CompletingSquareProblemFailureReason,
) -> None:
    event = ParametricChoreographySceneStreamDeclinedEventV3(
        generation=3,
        attempt=1,
        final_revision=0,
        reason_code=reason,
        message="The accepted board is unchanged.",
    )
    payload = dump_parametric_choreography_scene_stream_event(event)

    assert payload["reasonCode"] == reason.value
    assert payload["type"] == "parametric_choreography_scene_stream_declined"


@pytest.mark.parametrize("reason", ["later", "problem_private", 1, True, None])
def test_decline_rejects_arbitrary_or_coerced_reason_codes(reason: object) -> None:
    with pytest.raises(ValidationError):
        ParametricChoreographySceneStreamDeclinedEventV3.model_validate(
            {
                "generation": 3,
                "attempt": 1,
                "finalRevision": 0,
                "reasonCode": reason,
                "message": "The accepted board is unchanged.",
            }
        )


@pytest.mark.parametrize("code", list(ParametricChoreographyFailureCode))
def test_failure_codes_are_closed_and_bind_retryability(
    code: ParametricChoreographyFailureCode,
) -> None:
    retryable = code in {
        ParametricChoreographyFailureCode.INVALID_VISUAL_ACT,
        ParametricChoreographyFailureCode.PROVIDER_RATE_LIMITED,
        ParametricChoreographyFailureCode.PROVIDER_TIMEOUT,
        ParametricChoreographyFailureCode.PROVIDER_ERROR,
    }
    event = ParametricChoreographySceneStreamFailedEventV3(
        generation=3,
        attempt=2 if code is ParametricChoreographyFailureCode.INVALID_VISUAL_ACT else 1,
        code=code,
        message="The visual lesson could not continue safely.",
        last_accepted_revision=0,
        retryable=retryable,
    )

    assert dump_parametric_choreography_scene_stream_event(event)["code"] == code.value
    payload = event.model_dump(mode="json", by_alias=True)
    payload["retryable"] = not retryable
    with pytest.raises(ValidationError, match="retryable must be"):
        ParametricChoreographySceneStreamFailedEventV3.model_validate(payload)


@pytest.mark.parametrize("code", ["private_error", "compiler_failed", 1, True, None])
def test_failure_rejects_arbitrary_or_coerced_stable_codes(code: object) -> None:
    with pytest.raises(ValidationError):
        ParametricChoreographySceneStreamFailedEventV3.model_validate(
            {
                "generation": 3,
                "attempt": 1,
                "code": code,
                "message": "The visual lesson could not continue safely.",
                "lastAcceptedRevision": 0,
                "retryable": False,
            }
        )


def test_v3_union_reuses_only_exact_safe_lifecycle_records() -> None:
    events = (
        SceneStreamStartedEvent(generation=3, attempt=1, base_revision=0),
        _checkpoint_event(),
        ParametricChoreographySceneStreamDeclinedEventV3(
            generation=3,
            attempt=1,
            final_revision=0,
            reason_code=CompletingSquareProblemFailureReason.REQUIRED,
            message="Enter one supported equation.",
        ),
        SceneStreamRepairingEvent(
            generation=3,
            from_attempt=1,
            to_attempt=2,
            last_accepted_revision=0,
            message="Repairing the routing decision.",
        ),
        SceneStreamCompletedEvent(
            generation=3,
            final_revision=1,
            patch_count=1,
            first_patch_ms=1.0,
            total_ms=2.0,
            repaired=False,
        ),
        ParametricChoreographySceneStreamFailedEventV3(
            generation=3,
            attempt=1,
            code=ParametricChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
            message="The visual lesson could not be verified.",
            last_accepted_revision=0,
            retryable=False,
        ),
    )

    for event in events:
        payload = event.model_dump(mode="json", by_alias=True)
        assert PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event

    generic_failure = SceneStreamFailedEvent(
        generation=3,
        attempt=1,
        code="private_internal_code",
        message="A private subsystem failed.",
        last_accepted_revision=0,
        retryable=False,
    )
    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            generic_failure.model_dump(mode="json", by_alias=True)
        )


def test_v2_and_v3_terminal_and_checkpoint_discriminators_cannot_splice() -> None:
    v2_declined = ChoreographySceneStreamDeclinedEvent(
        generation=2,
        attempt=1,
        final_revision=0,
        reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
        message="The fixed lesson is unchanged.",
    )
    v3_declined = ParametricChoreographySceneStreamDeclinedEventV3(
        generation=3,
        attempt=1,
        final_revision=0,
        reason_code=CompletingSquareProblemFailureReason.UNSUPPORTED,
        message="That equation is outside the supported family.",
    )

    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            v2_declined.model_dump(mode="json", by_alias=True)
        )
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            v3_declined.model_dump(mode="json", by_alias=True)
        )

    v3_checkpoint = _payload()
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(v3_checkpoint)
    v3_checkpoint["type"] = "choreography_scene_checkpoint"
    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(v3_checkpoint)


def test_v3_checkpoint_rejects_a_v2_component_even_when_ids_match() -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    component = semantic["resultComponent"]
    assert isinstance(component, dict)
    component.pop("problemSpec")
    component["kind"] = "completing_square"

    with pytest.raises(ValidationError):
        ParametricChoreographySceneCheckpointEventV3.model_validate(payload)


def test_checkpoint_sequence_rejects_bool_and_float_coercion() -> None:
    for sequence in (True, 1.0):
        payload = _payload()
        payload["sequence"] = sequence
        with pytest.raises(ValidationError):
            ParametricChoreographySceneCheckpointEventV3.model_validate(payload)


def test_mutation_helpers_do_not_change_cached_valid_event() -> None:
    mutated = deepcopy(_payload())
    mutated["generation"] = 99

    assert _checkpoint_event().generation == 9
