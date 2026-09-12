from __future__ import annotations

from copy import deepcopy
from functools import cache

import pytest
from murmur.live_scene.choreography_service_contracts import (
    CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.contracts import (
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricChoreographySceneStreamDeclinedEventV3,
)
from murmur.live_scene.projectile_motion_checkpoint_compiler import (
    CompiledProjectileMotionCheckpointBeatV1,
    compile_projectile_motion_checkpoint_beat,
)
from murmur.live_scene.projectile_motion_checkpoint_contracts import (
    ProjectileMotionCheckpointCompilerCertificateBodyV1,
    projectile_motion_checkpoint_certificate_sha256,
)
from murmur.live_scene.projectile_motion_compiler import (
    ProjectileMotionCheckpointBlueprint,
    compile_projectile_motion_checkpoint_blueprints,
    materialize_projectile_motion_scene_nodes,
)
from murmur.live_scene.projectile_motion_contracts import (
    PROJECTILE_MOTION_CLARIFICATION_ORDER,
    PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER,
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
from murmur.live_scene.projectile_motion_service_contracts import (
    MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS,
    PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ProjectileCheckpointSemanticMetadataV1,
    ProjectileChoreographyDeclineReason,
    ProjectileChoreographyFailureCode,
    ProjectileChoreographySceneCheckpointEventV1,
    ProjectileChoreographySceneStreamDeclinedEventV1,
    ProjectileChoreographySceneStreamFailedEventV1,
    dump_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.projectile_motion_wire import (
    MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_projectile_choreography_scene_stream_event,
)
from murmur.live_scene.semantic_contracts import SemanticSceneState, VisualActAbstainReason
from pydantic import ValidationError


def _problem(speed: int = 20, angle: int = 45) -> ProjectileMotionProblemSpecV1:
    return ProjectileMotionProblemSpecV1(speedMps=speed, angleDeg=angle)


def _state(
    problem: ProjectileMotionProblemSpecV1,
    frontier: ProjectileMotionMainCheckpoint,
    *,
    topics: tuple[ProjectileMotionClarificationTopic, ...] = (),
    active: ProjectileMotionClarificationTopic | None = None,
) -> ProjectileMotionStateV1:
    return ProjectileMotionStateV1(
        id="projectile-lesson",
        problem_spec=problem,
        last_main_checkpoint=frontier,
        clarified_topics=topics,
        active_clarification=active,
    )


def _base(
    component: ProjectileMotionStateV1 | None = None,
    *,
    revision: int = 0,
    head: str | None = None,
) -> tuple[SceneState, SemanticSceneState]:
    return (
        SceneState(
            revision=revision,
            nodes=(
                () if component is None else materialize_projectile_motion_scene_nodes(component)
            ),
        ),
        SemanticSceneState(
            revision=revision,
            components=() if component is None else (component,),
            certificate_head_sha256=head,
        ),
    )


@cache
def _fresh_batch() -> CompiledProjectileMotionCheckpointBeatV1:
    problem = _problem()
    beat = RoutedProjectileMotionBeatV1(
        beat_id="projectile-service-advance",
        component_id="projectile-lesson",
        base_problem_spec=None,
        result_problem_spec=problem,
        route=AdvanceProjectileMotionRouteV1(target_stage=ProjectileMotionStage.SOLVE),
    )
    scene, semantic = _base()
    return compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=scene,
        base_semantic_scene=semantic,
    )


def _blueprints(
    batch: CompiledProjectileMotionCheckpointBeatV1,
) -> tuple[ProjectileMotionCheckpointBlueprint, ...]:
    base_component = (
        None
        if not batch.base_semantic_scene.components
        else batch.base_semantic_scene.components[0]
    )
    assert base_component is None or isinstance(base_component, ProjectileMotionStateV1)
    return compile_projectile_motion_checkpoint_blueprints(
        batch.beat,
        base_component,
    ).checkpoints


def _event_from_batch(
    batch: CompiledProjectileMotionCheckpointBeatV1,
    index: int = 0,
) -> ProjectileChoreographySceneCheckpointEventV1:
    checkpoint = batch.checkpoints[index]
    blueprint = _blueprints(batch)[index]
    body = checkpoint.certificate.body
    base_component = (
        None
        if index == 0 and not batch.base_semantic_scene.components
        else blueprint.base_component
    )
    return ProjectileChoreographySceneCheckpointEventV1(
        generation=7,
        attempt=1,
        sequence=index + 1,
        base_revision=body.base_low_level_revision,
        result_revision=body.result_low_level_revision,
        patch=checkpoint.patch,
        semantic=ProjectileCheckpointSemanticMetadataV1(
            base_problem_spec=(None if base_component is None else base_component.problem_spec),
            result_problem_spec=blueprint.result_component.problem_spec,
            beat=checkpoint.beat,
            action=checkpoint.action,
            checkpoint_id=checkpoint.checkpoint_id,
            clarification_topic=checkpoint.clarification_topic,
            base_component=base_component,
            result_component=blueprint.result_component,
            semantic_base_revision=body.base_semantic_revision,
            semantic_result_revision=body.result_semantic_revision,
            semantic_base_certificate_sha256=body.previous_certificate_sha256,
            semantic_result_certificate_sha256=checkpoint.certificate.certificate_sha256,
            receipt=checkpoint.receipt,
            presentation=checkpoint.presentation,
            choreography=checkpoint.choreography,
            certificate=checkpoint.certificate,
        ),
    )


def _payload(index: int = 0) -> dict[str, object]:
    return _event_from_batch(_fresh_batch(), index).model_dump(mode="json", by_alias=True)


def _reissue_certificate(payload: dict[str, object]) -> None:
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body = ProjectileMotionCheckpointCompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = projectile_motion_checkpoint_certificate_sha256(body)
    semantic["semanticResultCertificateSha256"] = certificate["certificateSha256"]


def _compile_clarification() -> CompiledProjectileMotionCheckpointBeatV1:
    problem = _problem()
    component = _state(
        problem,
        ProjectileMotionMainCheckpoint.TRACE_DESCENT,
        topics=(ProjectileMotionClarificationTopic.APEX_ACCELERATION,),
        active=ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    )
    beat = RoutedProjectileMotionBeatV1(
        beat_id="projectile-service-clarify",
        component_id=component.id,
        base_problem_spec=problem,
        result_problem_spec=problem,
        route=ClarifyProjectileMotionRouteV1(
            topic=ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
        ),
    )
    scene, semantic = _base(component, revision=11, head="a" * 64)
    return compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=scene,
        base_semantic_scene=semantic,
    )


def _compile_retarget() -> CompiledProjectileMotionCheckpointBeatV1:
    base_problem = _problem()
    result_problem = _problem(30, 60)
    component = _state(
        base_problem,
        ProjectileMotionMainCheckpoint.SUMMARY,
        topics=PROJECTILE_MOTION_CLARIFICATION_ORDER,
        active=ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY,
    )
    beat = RoutedProjectileMotionBeatV1(
        beat_id="projectile-service-retarget",
        component_id=component.id,
        base_problem_spec=base_problem,
        result_problem_spec=result_problem,
        route=RetargetProjectileMotionRouteV1(target_problem_spec=result_problem),
    )
    scene, semantic = _base(component, revision=23, head="b" * 64)
    return compile_projectile_motion_checkpoint_beat(
        beat,
        base_scene=scene,
        base_semantic_scene=semantic,
    )


def test_checkpoint_event_round_trips_with_exact_projectile_wire_shape() -> None:
    event = _event_from_batch(_fresh_batch())
    payload = dump_projectile_choreography_scene_stream_event(event)

    assert PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event
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
    assert payload["type"] == "projectile_choreography_scene_checkpoint"
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    assert set(semantic) == {
        "baseProblemSpec",
        "resultProblemSpec",
        "beat",
        "action",
        "checkpointId",
        "clarificationTopic",
        "baseComponent",
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
    assert semantic["baseProblemSpec"] is None
    assert semantic["baseComponent"] is None
    assert semantic["clarificationTopic"] is None
    assert semantic["semanticBaseCertificateSha256"] is None
    assert set(semantic["resultProblemSpec"]) == {"v", "speedMps", "angleDeg"}
    assert set(semantic["resultComponent"]) == {
        "kind",
        "id",
        "problemSpec",
        "lastMainCheckpoint",
        "clarifiedTopics",
        "activeClarification",
    }
    with pytest.raises(ValidationError, match="frozen"):
        event.sequence = 2


@pytest.mark.parametrize(
    ("index", "checkpoint"),
    tuple(enumerate(PROJECTILE_MOTION_MAIN_CHECKPOINT_ORDER)),
)
def test_every_main_checkpoint_binds_problem_frontier_revision_hashes_and_chain(
    index: int,
    checkpoint: ProjectileMotionMainCheckpoint,
) -> None:
    event = _event_from_batch(_fresh_batch(), index)
    semantic = event.semantic
    body = semantic.certificate.body

    assert semantic.checkpoint_id.value == checkpoint.value
    assert semantic.result_component.last_main_checkpoint is checkpoint
    assert semantic.result_problem_spec == semantic.beat.result_problem_spec
    assert semantic.result_problem_spec == semantic.result_component.problem_spec
    assert body.base_low_level_revision == event.base_revision
    assert body.result_low_level_revision == event.result_revision
    assert body.base_semantic_revision == semantic.semantic_base_revision
    assert body.result_semantic_revision == semantic.semantic_result_revision
    assert body.previous_certificate_sha256 == semantic.semantic_base_certificate_sha256
    assert semantic.semantic_result_certificate_sha256 == semantic.certificate.certificate_sha256


def test_every_authored_projectile_checkpoint_fits_the_full_event_budget() -> None:
    batch = _fresh_batch()
    sizes = tuple(
        len(
            encode_projectile_choreography_scene_stream_event(
                _event_from_batch(batch, index)
            ).encode("utf-8")
        )
        for index in range(len(batch.checkpoints))
    )

    assert len(sizes) == 6
    assert max(sizes) <= MAX_PROJECTILE_CHOREOGRAPHY_SSE_EVENT_BYTES


def test_clarification_and_retarget_bind_their_exact_state_transitions() -> None:
    clarified = _event_from_batch(_compile_clarification())
    assert clarified.semantic.action.value == "clarify"
    assert clarified.semantic.result_component.clarified_topics == (
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY,
        ProjectileMotionClarificationTopic.APEX_ACCELERATION,
    )
    assert clarified.semantic.result_component.active_clarification is (
        ProjectileMotionClarificationTopic.HORIZONTAL_VELOCITY
    )

    retargeted = _event_from_batch(_compile_retarget())
    assert retargeted.semantic.action.value == "retarget"
    assert retargeted.semantic.base_problem_spec != retargeted.semantic.result_problem_spec
    assert (
        retargeted.semantic.result_component.last_main_checkpoint
        is retargeted.semantic.base_component.last_main_checkpoint
    )
    assert (
        retargeted.semantic.result_component.active_clarification
        is ProjectileMotionClarificationTopic.FLIGHT_SYMMETRY
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("base_problem", "baseProblemSpec must match baseComponent"),
        ("result_problem", "resultProblemSpec must match resultComponent"),
        ("base_component_id", "baseComponent id"),
        ("result_component_id", "resultComponent id"),
        ("result_frontier", "advance resultComponent"),
        ("semantic_base_hash", "baseSemanticSceneSha256"),
        ("semantic_result_hash", "resultSemanticSceneSha256"),
        ("base_chain", "previousCertificateSha256"),
        ("result_chain", "result chain head"),
        ("semantic_revision", "semanticResultRevision"),
        ("event_revision", "resultRevision"),
        ("sequence", "less than or equal to"),
    ],
)
def test_checkpoint_rejects_problem_component_hash_revision_and_chain_splices(
    mutation: str,
    message: str,
) -> None:
    payload = _payload(1)
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)

    if mutation == "base_problem":
        semantic["baseProblemSpec"] = _problem(25, 30).model_dump(mode="json", by_alias=True)
    elif mutation == "result_problem":
        semantic["resultProblemSpec"] = _problem(25, 30).model_dump(mode="json", by_alias=True)
    elif mutation == "base_component_id":
        base_component = semantic["baseComponent"]
        assert isinstance(base_component, dict)
        base_component["id"] = "other"
    elif mutation == "result_component_id":
        result_component = semantic["resultComponent"]
        assert isinstance(result_component, dict)
        result_component["id"] = "other"
    elif mutation == "result_frontier":
        result_component = semantic["resultComponent"]
        assert isinstance(result_component, dict)
        result_component["lastMainCheckpoint"] = "setup"
    elif mutation in {"semantic_base_hash", "semantic_result_hash"}:
        certificate = semantic["certificate"]
        assert isinstance(certificate, dict)
        body = certificate["body"]
        assert isinstance(body, dict)
        field = (
            "baseSemanticSceneSha256"
            if mutation == "semantic_base_hash"
            else "resultSemanticSceneSha256"
        )
        body[field] = "f" * 64
        _reissue_certificate(payload)
    elif mutation == "base_chain":
        semantic["semanticBaseCertificateSha256"] = "f" * 64
    elif mutation == "result_chain":
        semantic["semanticResultCertificateSha256"] = "f" * 64
    elif mutation == "semantic_revision":
        semantic["semanticResultRevision"] = 99
    elif mutation == "event_revision":
        payload["resultRevision"] = 99
    else:
        payload["sequence"] = MAX_PROJECTILE_CHOREOGRAPHY_CHECKPOINTS + 1

    with pytest.raises(ValidationError, match=message):
        ProjectileChoreographySceneCheckpointEventV1.model_validate(payload)


def test_fresh_null_base_is_allowed_only_for_setup() -> None:
    payload = _payload(1)
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    semantic["baseProblemSpec"] = None
    semantic["baseComponent"] = None

    with pytest.raises(ValidationError, match="routed checkpoint transition"):
        ProjectileChoreographySceneCheckpointEventV1.model_validate(payload)


def test_transition_rejects_modified_clarification_ledger_and_retarget_frontier() -> None:
    clarification = _event_from_batch(_compile_clarification()).model_dump(
        mode="json", by_alias=True
    )
    semantic = clarification["semantic"]
    assert isinstance(semantic, dict)
    result = semantic["resultComponent"]
    assert isinstance(result, dict)
    result["activeClarification"] = "apex_acceleration"
    with pytest.raises(ValidationError, match="activeClarification"):
        ProjectileChoreographySceneCheckpointEventV1.model_validate(clarification)

    retarget = _event_from_batch(_compile_retarget()).model_dump(mode="json", by_alias=True)
    semantic = retarget["semantic"]
    assert isinstance(semantic, dict)
    result = semantic["resultComponent"]
    assert isinstance(result, dict)
    result["lastMainCheckpoint"] = "trace_descent"
    with pytest.raises(ValidationError, match="retarget must preserve the main frontier"):
        ProjectileChoreographySceneCheckpointEventV1.model_validate(retarget)

    retarget = _event_from_batch(_compile_retarget()).model_dump(mode="json", by_alias=True)
    semantic = retarget["semantic"]
    assert isinstance(semantic, dict)
    for field in ("baseComponent", "resultComponent"):
        component = semantic[field]
        assert isinstance(component, dict)
        component["lastMainCheckpoint"] = None
        component["clarifiedTopics"] = []
        component["activeClarification"] = None
    with pytest.raises(ValidationError, match="retarget requires a settled main frontier"):
        ProjectileChoreographySceneCheckpointEventV1.model_validate(retarget)


@pytest.mark.parametrize(
    "level",
    ["event", "semantic", "base_problem", "result_component", "certificate"],
)
def test_checkpoint_rejects_unknown_fields_at_every_trust_boundary(level: str) -> None:
    payload = _payload()
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    if level == "event":
        payload["providerTrace"] = "private"
    elif level == "semantic":
        semantic["providerTrace"] = "private"
    elif level == "base_problem":
        payload = _payload(1)
        semantic = payload["semantic"]
        assert isinstance(semantic, dict)
        problem = semantic["baseProblemSpec"]
        assert isinstance(problem, dict)
        problem["gravity"] = 9.81
    elif level == "result_component":
        component = semantic["resultComponent"]
        assert isinstance(component, dict)
        component["privateReasoning"] = "hidden"
    else:
        certificate = semantic["certificate"]
        assert isinstance(certificate, dict)
        certificate["signature"] = "not-a-signature"

    with pytest.raises(ValidationError, match="Extra inputs"):
        PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("reason", list(ProjectileChoreographyDeclineReason))
def test_decline_reasons_are_an_exact_closed_three_value_vocabulary(
    reason: ProjectileChoreographyDeclineReason,
) -> None:
    event = ProjectileChoreographySceneStreamDeclinedEventV1(
        generation=3,
        attempt=1,
        final_revision=4,
        reason_code=reason,
        message="The accepted projectile board is unchanged.",
    )
    payload = dump_projectile_choreography_scene_stream_event(event)
    assert payload["type"] == "projectile_choreography_scene_stream_declined"
    assert payload["reasonCode"] == reason.value


@pytest.mark.parametrize("reason", ["later", "private_reason", 1, True, None])
def test_decline_rejects_unknown_or_coerced_reason_codes(reason: object) -> None:
    with pytest.raises(ValidationError):
        ProjectileChoreographySceneStreamDeclinedEventV1.model_validate(
            {
                "generation": 3,
                "attempt": 1,
                "finalRevision": 4,
                "reasonCode": reason,
                "message": "The accepted projectile board is unchanged.",
            }
        )


@pytest.mark.parametrize("code", list(ProjectileChoreographyFailureCode))
def test_failure_codes_are_closed_and_bind_retryability(
    code: ProjectileChoreographyFailureCode,
) -> None:
    retryable = code in {
        ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT,
        ProjectileChoreographyFailureCode.PROVIDER_RATE_LIMITED,
        ProjectileChoreographyFailureCode.PROVIDER_TIMEOUT,
        ProjectileChoreographyFailureCode.PROVIDER_ERROR,
    }
    event = ProjectileChoreographySceneStreamFailedEventV1(
        generation=3,
        attempt=2 if code is ProjectileChoreographyFailureCode.INVALID_VISUAL_ACT else 1,
        code=code,
        message="The projectile lesson could not continue safely.",
        last_accepted_revision=4,
        retryable=retryable,
    )
    assert dump_projectile_choreography_scene_stream_event(event)["code"] == code.value

    payload = event.model_dump(mode="json", by_alias=True)
    payload["retryable"] = not retryable
    with pytest.raises(ValidationError, match="retryable must be"):
        ProjectileChoreographySceneStreamFailedEventV1.model_validate(payload)


@pytest.mark.parametrize("code", ["private_error", "compiler_failed", 1, True, None])
def test_failure_rejects_unknown_or_coerced_codes(code: object) -> None:
    with pytest.raises(ValidationError):
        ProjectileChoreographySceneStreamFailedEventV1.model_validate(
            {
                "generation": 3,
                "attempt": 1,
                "code": code,
                "message": "The projectile lesson could not continue safely.",
                "lastAcceptedRevision": 4,
                "retryable": False,
            }
        )


def test_union_reuses_only_the_three_exact_safe_generic_lifecycle_records() -> None:
    events = (
        SceneStreamStartedEvent(generation=3, attempt=1, base_revision=4),
        _event_from_batch(_fresh_batch()),
        ProjectileChoreographySceneStreamDeclinedEventV1(
            generation=3,
            attempt=1,
            final_revision=4,
            reason_code=ProjectileChoreographyDeclineReason.NO_FORWARD_PROGRESS,
            message="The accepted projectile board is unchanged.",
        ),
        SceneStreamRepairingEvent(
            generation=3,
            from_attempt=1,
            to_attempt=2,
            last_accepted_revision=4,
            message="Repairing the projectile route.",
        ),
        SceneStreamCompletedEvent(
            generation=3,
            final_revision=5,
            patch_count=1,
            first_patch_ms=1.0,
            total_ms=2.0,
            repaired=False,
        ),
        ProjectileChoreographySceneStreamFailedEventV1(
            generation=3,
            attempt=1,
            code=ProjectileChoreographyFailureCode.CHOREOGRAPHY_INTEGRITY_ERROR,
            message="The projectile lesson could not be verified.",
            last_accepted_revision=4,
            retryable=False,
        ),
    )
    for event in events:
        payload = event.model_dump(mode="json", by_alias=True)
        assert PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload) == event

    generic_failed = SceneStreamFailedEvent(
        generation=3,
        attempt=1,
        code="private_provider_detail",
        message="Internal detail.",
        last_accepted_revision=4,
        retryable=False,
    )
    with pytest.raises(ValidationError):
        PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
            generic_failed.model_dump(mode="json", by_alias=True)
        )


def test_projectile_custom_records_are_isolated_from_gate15_and_gate16_unions() -> None:
    projectile = ProjectileChoreographySceneStreamDeclinedEventV1(
        generation=1,
        attempt=1,
        final_revision=0,
        reason_code=ProjectileChoreographyDeclineReason.UNSUPPORTED_INTENT,
        message="No projectile mutation was requested.",
    )
    gate15 = ChoreographySceneStreamDeclinedEvent(
        generation=1,
        attempt=1,
        final_revision=0,
        reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
        message="No fixed choreography mutation was requested.",
    )
    gate16 = ParametricChoreographySceneStreamDeclinedEventV3(
        generation=1,
        attempt=1,
        final_revision=0,
        reason_code=VisualActAbstainReason.UNSUPPORTED_INTENT,
        message="No parametric choreography mutation was requested.",
    )

    for foreign in (gate15, gate16):
        with pytest.raises(ValidationError):
            PROJECTILE_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(
                foreign.model_dump(mode="json", by_alias=True)
            )
    projectile_payload = projectile.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(projectile_payload)
    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(projectile_payload)


def test_dump_revalidates_mutated_copies_instead_of_trusting_typed_annotation() -> None:
    payload = deepcopy(_payload())
    payload["type"] = "scene_patch"
    with pytest.raises(ValidationError):
        dump_projectile_choreography_scene_stream_event(payload)  # type: ignore[arg-type]
