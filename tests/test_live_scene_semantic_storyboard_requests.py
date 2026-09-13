"""Request-boundary tests for the isolated Gate 1.8 protocol."""

from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.contracts import MAX_SCENE_NODES
from murmur.live_scene.semantic_storyboard_requests import (
    PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
    SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER,
    SemanticStoryboardDirectorRequestV1,
    SemanticStoryboardReflexRequestV1,
)
from pydantic import ValidationError


def _problem(
    speed: int = 20,
    angles: list[int] | None = None,
) -> dict[str, object]:
    return {
        "v": 1,
        "speedMps": speed,
        "anglesDeg": [30, 60] if angles is None else angles,
    }


def _anchor_component(
    *,
    problem: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "v": 1,
        "kind": "projectile_comparison_storyboard",
        "id": "projectile-comparison",
        "problemSpec": _problem() if problem is None else problem,
        "acceptedRecords": [],
    }


def _base(
    *,
    revision: int,
    problem: dict[str, object] | None = None,
    component: dict[str, object] | None = None,
) -> dict[str, object]:
    semantic_scene: dict[str, object] = {
        "revision": revision,
        "components": [] if component is None else [component],
    }
    if component is not None:
        semantic_scene["certificateHeadSha256"] = "a" * 64
    return {
        "protocol": PROJECTILE_COMPARISON_STORYBOARD_PROTOCOL,
        "problemSpec": _problem() if problem is None else problem,
        "generation": 2,
        "baseScene": {"revision": revision, "nodes": []},
        "baseSemanticScene": semantic_scene,
    }


def _reflex() -> dict[str, object]:
    return {**_base(revision=0), "routingMode": "reflex"}


def _director(
    *,
    problem: dict[str, object] | None = None,
    component: dict[str, object] | None = None,
) -> dict[str, object]:
    bound_problem = _problem() if problem is None else problem
    return {
        **_base(
            revision=1,
            problem=bound_problem,
            component=_anchor_component(problem=bound_problem) if component is None else component,
        ),
        "routingMode": "director",
        "prompt": "Trace the lower arc before explaining their ranges.",
    }


def test_reflex_request_is_exactly_fresh_prompt_free_anchor_input() -> None:
    payload = _reflex()
    request = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, SemanticStoryboardReflexRequestV1)
    assert request.model_dump(mode="json", by_alias=True) == payload
    assert set(SemanticStoryboardReflexRequestV1.model_fields) == {
        "protocol",
        "problem_spec",
        "generation",
        "base_scene",
        "base_semantic_scene",
        "routing_mode",
    }
    assert "prompt" not in SemanticStoryboardReflexRequestV1.model_fields


def test_director_request_is_prompt_only_and_bound_to_certified_anchor() -> None:
    payload = _director()
    request = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)

    assert isinstance(request, SemanticStoryboardDirectorRequestV1)
    assert request.model_dump(mode="json", by_alias=True) == payload
    assert set(SemanticStoryboardDirectorRequestV1.model_fields) == {
        "protocol",
        "problem_spec",
        "generation",
        "base_scene",
        "base_semantic_scene",
        "routing_mode",
        "prompt",
    }
    assert "requestedRoute" not in SemanticStoryboardDirectorRequestV1.model_fields
    assert "records" not in SemanticStoryboardDirectorRequestV1.model_fields


@pytest.mark.parametrize("prompt", ["\u0085", "\ufeff", "\u0085\ufeff"])
def test_director_rejects_unicode_edge_whitespace_only_prompts(prompt: str) -> None:
    payload = _director()
    payload["prompt"] = prompt

    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


def test_director_uses_the_same_explicit_unicode_edge_normalization() -> None:
    payload = _director()
    payload["prompt"] = "\u0085\ufeff  Compare their ranges.  \ufeff\u0085"

    request = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)

    assert request.prompt == "Compare their ranges."


def test_director_continuation_preserves_the_exact_ordered_frontier() -> None:
    component = _anchor_component()
    component["acceptedRecords"] = [{"v": 1, "act": "trace", "trajectoryId": "lower_angle"}]
    payload = _director(component=component)
    payload["baseScene"] = {"revision": 2, "nodes": []}
    payload["baseSemanticScene"]["revision"] = 2

    request = SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)

    assert request.base_semantic_scene.components[0].accepted_records[0].act == "trace"


@pytest.mark.parametrize(
    "payload",
    [
        {**_reflex(), "protocol": "projectile_choreography_v1"},
        {**_reflex(), "prompt": "A provider prompt is forbidden."},
        {**_reflex(), "routingMode": "director"},
        {**_director(), "routingMode": "reflex"},
        {**_director(), "requestedRoute": {"act": "trace"}},
        {**_director(), "unexpected": True},
    ],
)
def test_cross_protocol_cross_mode_and_unknown_fields_are_rejected(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


def test_reflex_requires_empty_revision_zero_scenes() -> None:
    low_level_not_empty = _reflex()
    low_level_not_empty["baseScene"] = {"revision": 0, "nodes": [{"kind": "model"}]}
    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(low_level_not_empty)

    anchored = {**_base(revision=1, component=_anchor_component()), "routingMode": "reflex"}
    with pytest.raises(ValidationError, match="anchor requires"):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(anchored)


def test_director_requires_anchor_and_matching_problem() -> None:
    no_anchor = {**_base(revision=0), "routingMode": "director", "prompt": "Trace it."}
    with pytest.raises(ValidationError, match="requires the certified storyboard anchor"):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(no_anchor)

    mismatch_problem = _problem(angles=[30, 45])
    mismatch = _director(problem=mismatch_problem, component=_anchor_component())
    with pytest.raises(ValidationError, match="problemSpec must match"):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(mismatch)


def test_director_rejects_a_client_selected_component_id() -> None:
    payload = _director()
    payload["baseSemanticScene"]["components"][0]["id"] = "attacker-chosen"

    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


def test_low_level_and_semantic_revisions_must_match() -> None:
    payload = _director()
    payload["baseScene"] = {"revision": 2, "nodes": []}

    with pytest.raises(ValidationError, match="revisions must match"):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", True),
        ("generation", "2"),
        ("problemSpec", {"v": 1, "speedMps": 20, "anglesDeg": [60, 30]}),
        (
            "problemSpec",
            {"v": 1, "speedMps": 20, "anglesDeg": [30, 60], "gravity": 10},
        ),
    ],
)
def test_identity_frontier_and_problem_values_are_strict(
    field: str,
    value: object,
) -> None:
    payload = _reflex()
    payload[field] = value
    with pytest.raises(ValidationError):
        SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


def test_request_adapter_requires_canonical_wire_keys_at_every_layer() -> None:
    canonical = _director()
    canonical["baseScene"] = {"revision": 2, "nodes": []}
    canonical["baseSemanticScene"]["revision"] = 2
    canonical["baseSemanticScene"]["components"][0]["acceptedRecords"] = [
        {"v": 1, "act": "trace", "trajectoryId": "lower_angle"}
    ]

    payloads: list[dict[str, object]] = []
    for path, canonical_key, alias_key in (
        ((), "routingMode", "routing_mode"),
        (("problemSpec",), "speedMps", "speed_mps"),
        (("baseScene",), "revision", "base_revision"),
        (("baseSemanticScene",), "certificateHeadSha256", "certificate_head_sha256"),
        (("baseSemanticScene", "components", 0), "problemSpec", "problem_spec"),
        (
            ("baseSemanticScene", "components", 0, "acceptedRecords", 0),
            "trajectoryId",
            "trajectory_id",
        ),
    ):
        payload = deepcopy(canonical)
        target: object = payload
        for segment in path:
            target = target[segment]  # type: ignore[index]
        assert isinstance(target, dict)
        target[alias_key] = target.pop(canonical_key)
        payloads.append(payload)

    for payload in payloads:
        with pytest.raises(ValidationError):
            SEMANTIC_STORYBOARD_REQUEST_V1_ADAPTER.validate_python(payload)


def test_request_collections_are_capped_before_child_parsing() -> None:
    oversized_nodes = _reflex()
    oversized_nodes["baseScene"] = {
        "revision": 0,
        "nodes": [{"invalid": True} for _ in range(MAX_SCENE_NODES + 1)],
    }
    with pytest.raises(ValidationError) as node_error:
        SemanticStoryboardReflexRequestV1.model_validate(oversized_nodes)
    assert len(node_error.value.errors()) == 1
    assert f"exceeds {MAX_SCENE_NODES} nodes" in str(node_error.value)


def test_direct_model_construction_remains_ergonomic_with_python_field_names() -> None:
    payload = _reflex()
    payload["routing_mode"] = payload.pop("routingMode")
    payload["problem_spec"] = payload.pop("problemSpec")
    payload["base_scene"] = payload.pop("baseScene")
    payload["base_semantic_scene"] = payload.pop("baseSemanticScene")

    request = SemanticStoryboardReflexRequestV1.model_validate(payload)

    assert request.routing_mode == "reflex"
