from __future__ import annotations

import pytest
from murmur.live_scene.parametric_choreography_requests import (
    CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER,
    PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
    PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER,
    ParametricChoreographyDirectorRequestV3,
    ParametricChoreographyReflexRequestV3,
)
from murmur.live_scene.projectile_motion_requests import (
    PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    ProjectileMotionDirectorRequestV1,
    ProjectileMotionReflexRequestV1,
)
from murmur.live_scene.semantic_service_contracts import SemanticLiveSceneRequest
from pydantic import ValidationError


def _base() -> dict[str, object]:
    return {
        "protocol": PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
        "problemText": "x² + 8x = 20",
        "generation": 3,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
    }


def _reflex() -> dict[str, object]:
    return {
        **_base(),
        "routingMode": "reflex",
        "requestedRoute": {"intent": "advance", "targetStage": "solve"},
    }


def _director() -> dict[str, object]:
    return {
        **_base(),
        "routingMode": "director",
        "prompt": "Explain the idea one meaningful stage at a time.",
    }


def _projectile_reflex() -> dict[str, object]:
    return {
        "protocol": PROJECTILE_CHOREOGRAPHY_PROTOCOL,
        "problemSpec": {"v": 1, "speedMps": 20, "angleDeg": 45},
        "generation": 4,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
        "routingMode": "reflex",
        "requestedRoute": {"intent": "advance", "targetStage": "setup"},
    }


def _projectile_director() -> dict[str, object]:
    payload = _projectile_reflex()
    payload["routingMode"] = "director"
    payload.pop("requestedRoute")
    payload["prompt"] = "Teach the first projectile checkpoint."
    return payload


def test_reflex_request_has_one_exact_prompt_free_wire_shape() -> None:
    request = PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(_reflex())

    assert isinstance(request, ParametricChoreographyReflexRequestV3)
    assert request.problem_text == "x² + 8x = 20"
    assert request.requested_route.intent == "advance"
    assert request.model_dump(mode="json", by_alias=True) == _reflex()
    assert "prompt" not in type(request).model_fields


def test_director_request_has_one_exact_route_free_wire_shape() -> None:
    request = PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(_director())

    assert isinstance(request, ParametricChoreographyDirectorRequestV3)
    assert request.prompt == "Explain the idea one meaningful stage at a time."
    assert request.model_dump(mode="json", by_alias=True) == _director()
    assert "requested_route" not in type(request).model_fields


def test_problem_text_is_required_but_nullable_for_continuation() -> None:
    continuation = {
        **_reflex(),
        "problemText": None,
        "requestedRoute": {"intent": "advance", "targetStage": "complete"},
    }
    request = PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(continuation)

    assert request.problem_text is None
    missing = _reflex()
    del missing["problemText"]
    with pytest.raises(ValidationError, match="Field required"):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(missing)


@pytest.mark.parametrize(
    "mutation",
    [
        {"routingMode": "reflex", "prompt": "Do what I say"},
        {"routingMode": "director", "requestedRoute": {"intent": "clarify_corner"}},
        {"routingMode": "reflex"},
        {"routingMode": "director"},
        {"routingMode": "automatic"},
        {"unexpected": True},
    ],
)
def test_modes_reject_cross_mode_missing_and_unknown_fields(
    mutation: dict[str, object],
) -> None:
    payload = _reflex() if mutation.get("routingMode") != "director" else _director()
    payload.update(mutation)
    if mutation == {"routingMode": "reflex"}:
        payload.pop("requestedRoute", None)
    if mutation == {"routingMode": "director"}:
        payload.pop("prompt", None)

    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("protocol", [None, "parametric_choreography_v2", "future"])
def test_v3_never_defaults_or_accepts_an_unknown_protocol(protocol: object) -> None:
    payload = _reflex()
    if protocol is None:
        del payload["protocol"]
    else:
        payload["protocol"] = protocol

    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(payload)


def test_top_level_dispatch_preserves_absent_protocol_v2_and_rejects_unknown() -> None:
    legacy = {
        "prompt": "Teach completing the square visually.",
        "generation": 1,
        "baseScene": {"revision": 0, "nodes": []},
        "baseSemanticScene": {"revision": 0, "components": []},
    }
    decoded = CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER.validate_python(legacy)
    assert isinstance(decoded, SemanticLiveSceneRequest)
    assert decoded.model_dump(mode="json", by_alias=True) == legacy

    unknown = {**_reflex(), "protocol": "parametric_choreography_v99"}
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER.validate_python(unknown)


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (_projectile_reflex(), ProjectileMotionReflexRequestV1),
        (_projectile_director(), ProjectileMotionDirectorRequestV1),
    ],
)
def test_top_level_dispatch_accepts_exact_projectile_protocol_modes(
    payload: dict[str, object],
    expected_type: type[ProjectileMotionReflexRequestV1 | ProjectileMotionDirectorRequestV1],
) -> None:
    decoded = CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER.validate_python(payload)

    assert isinstance(decoded, expected_type)
    assert decoded.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {**_projectile_reflex(), "protocol": "projectile_choreography_v99"},
        {key: value for key, value in _projectile_reflex().items() if key != "protocol"},
        {**_projectile_reflex(), "problemText": "x² + 8x = 20"},
        {
            **_reflex(),
            "problemSpec": {"v": 1, "speedMps": 20, "angleDeg": 45},
        },
    ],
)
def test_top_level_dispatch_rejects_unknown_missing_and_cross_mixed_protocols(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CHOREOGRAPHY_LIVE_SCENE_REQUEST_ADAPTER.validate_python(payload)


def test_lockstep_revisions_and_route_vocabulary_fail_closed() -> None:
    mismatch = _reflex()
    mismatch["baseSemanticScene"] = {"revision": 1, "components": []}
    with pytest.raises(ValidationError, match="revisions must match"):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(mismatch)

    open_route = _reflex()
    open_route["requestedRoute"] = {"intent": "jump", "targetStage": "solve"}
    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(open_route)


@pytest.mark.parametrize("field", ["protocol", "routingMode", "generation"])
def test_required_discriminators_and_generation_are_not_defaulted(field: str) -> None:
    payload = _reflex()
    del payload[field]

    with pytest.raises(ValidationError):
        PARAMETRIC_CHOREOGRAPHY_REQUEST_V3_ADAPTER.validate_python(payload)
