from __future__ import annotations

import pytest
from murmur.live_scene.projectile_motion_requests import (
    PROJECTILE_CHOREOGRAPHY_PROTOCOL,
    PROJECTILE_MOTION_REQUEST_V1_ADAPTER,
    ProjectileMotionDirectorRequestV1,
    ProjectileMotionReflexRequestV1,
)
from pydantic import ValidationError


def _problem(speed: int = 20, angle: int = 45) -> dict[str, int]:
    return {"v": 1, "speedMps": speed, "angleDeg": angle}


def _semantic_component(speed: int = 20, angle: int = 45) -> dict[str, object]:
    return {
        "kind": "projectile_motion",
        "id": "projectile-lesson",
        "problemSpec": _problem(speed, angle),
        "lastMainCheckpoint": "apex_state",
        "clarifiedTopics": ["apex_acceleration"],
        "activeClarification": "apex_acceleration",
    }


def _base(
    *,
    problem: dict[str, int] | None = None,
    revision: int = 0,
    components: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "protocol": PROJECTILE_CHOREOGRAPHY_PROTOCOL,
        "problemSpec": _problem() if problem is None else problem,
        "generation": 3,
        "baseScene": {"revision": revision, "nodes": []},
        "baseSemanticScene": {
            "revision": revision,
            "components": [] if components is None else components,
        },
    }


def _reflex(
    *,
    route: dict[str, object] | None = None,
    problem: dict[str, int] | None = None,
    revision: int = 0,
    components: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        **_base(problem=problem, revision=revision, components=components),
        "routingMode": "reflex",
        "requestedRoute": (
            {"intent": "advance", "targetStage": "solve"} if route is None else route
        ),
    }


def _director(
    *,
    problem: dict[str, int] | None = None,
    revision: int = 0,
    components: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        **_base(problem=problem, revision=revision, components=components),
        "routingMode": "director",
        "prompt": "Focus on the next meaningful projectile idea.",
    }


def test_reflex_request_has_one_exact_prompt_free_wire_shape() -> None:
    payload = _reflex()
    request = PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, ProjectileMotionReflexRequestV1)
    assert request.problem_spec.speed_mps == 20
    assert request.problem_spec.angle_deg == 45
    assert request.requested_route.intent == "advance"
    assert request.model_dump(mode="json", by_alias=True) == payload
    assert set(ProjectileMotionReflexRequestV1.model_fields) == {
        "protocol",
        "problem_spec",
        "generation",
        "base_scene",
        "base_semantic_scene",
        "routing_mode",
        "requested_route",
    }
    assert "prompt" not in ProjectileMotionReflexRequestV1.model_fields


def test_director_request_has_one_exact_route_free_wire_shape() -> None:
    payload = _director()
    request = PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, ProjectileMotionDirectorRequestV1)
    assert request.prompt == "Focus on the next meaningful projectile idea."
    assert request.model_dump(mode="json", by_alias=True) == payload
    assert set(ProjectileMotionDirectorRequestV1.model_fields) == {
        "protocol",
        "problem_spec",
        "generation",
        "base_scene",
        "base_semantic_scene",
        "routing_mode",
        "prompt",
    }
    assert "requested_route" not in ProjectileMotionDirectorRequestV1.model_fields


@pytest.mark.parametrize(
    "route",
    [
        {"intent": "advance", "targetStage": "flight"},
        {"intent": "clarify", "topic": "horizontal_velocity"},
        {
            "intent": "retarget",
            "targetProblemSpec": _problem(30, 60),
        },
    ],
)
def test_settled_reflex_request_accepts_each_closed_route(
    route: dict[str, object],
) -> None:
    component = _semantic_component()
    payload = _reflex(route=route, revision=4, components=[component])

    request = PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, ProjectileMotionReflexRequestV1)
    assert request.model_dump(mode="json", by_alias=True) == payload


@pytest.mark.parametrize(
    "route",
    [
        {"intent": "clarify", "topic": "horizontal_velocity"},
        {"intent": "retarget", "targetProblemSpec": _problem(20, 60)},
    ],
)
def test_fresh_reflex_request_must_advance(route: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="fresh projectile request must use an advance"):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(_reflex(route=route))


def test_same_problem_retarget_remains_a_valid_request_for_routing_to_decline() -> None:
    component = _semantic_component()
    payload = _reflex(
        route={"intent": "retarget", "targetProblemSpec": _problem()},
        revision=4,
        components=[component],
    )

    request = PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, ProjectileMotionReflexRequestV1)
    assert request.requested_route.intent == "retarget"


def test_bound_problem_must_match_the_accepted_semantic_problem() -> None:
    component = _semantic_component(20, 45)
    mismatch = _reflex(problem=_problem(20, 60), revision=4, components=[component])

    with pytest.raises(ValidationError, match="problemSpec must match"):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(mismatch)


def test_cross_protocol_and_multiple_component_bases_fail_at_request_boundary() -> None:
    completing_square = {
        "kind": "completing_square_parametric",
        "id": "square-lesson",
        "problemSpec": {"v": 1, "linearCoefficient": 8, "rightHandSide": 20},
        "lastMainCheckpoint": "problem",
        "cornerClarified": False,
    }
    cross_protocol = _reflex(revision=1, components=[completing_square])
    with pytest.raises(ValidationError, match="projectile_motion semantic base"):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(cross_protocol)

    multiple = _reflex(
        revision=2,
        components=[_semantic_component(), completing_square],
    )
    with pytest.raises(ValidationError, match="at most one semantic component"):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(multiple)


def test_low_level_and_semantic_revisions_must_be_lockstep() -> None:
    payload = _reflex()
    payload["baseSemanticScene"] = {"revision": 1, "components": []}

    with pytest.raises(ValidationError, match="revisions must match"):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        {"routingMode": "reflex", "prompt": "Draw arbitrary SVG"},
        {
            "routingMode": "director",
            "requestedRoute": {"intent": "advance", "targetStage": "solve"},
        },
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
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("field", ["protocol", "problemSpec", "routingMode", "generation"])
def test_required_identity_and_dispatch_fields_are_not_defaulted(field: str) -> None:
    payload = _reflex()
    del payload[field]

    with pytest.raises(ValidationError):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", True),
        ("generation", "3"),
        ("problemSpec", {"v": 1, "speedMps": "20", "angleDeg": 45}),
        ("problemSpec", {"v": 1, "speedMps": 20, "angleDeg": 90}),
        (
            "problemSpec",
            {"v": 1, "speedMps": 20, "angleDeg": 45, "gravity": 9.81},
        ),
    ],
)
def test_request_rejects_coercions_unsupported_physics_and_extra_problem_fields(
    field: str,
    value: object,
) -> None:
    payload = _reflex()
    payload[field] = value

    with pytest.raises(ValidationError):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    "protocol",
    [None, "projectile_choreography_v0", "projectile_choreography_v2", "future"],
)
def test_projectile_protocol_never_defaults_or_accepts_unknown_values(protocol: object) -> None:
    payload = _reflex()
    if protocol is None:
        del payload["protocol"]
    else:
        payload["protocol"] = protocol

    with pytest.raises(ValidationError):
        PROJECTILE_MOTION_REQUEST_V1_ADAPTER.validate_python(payload)
