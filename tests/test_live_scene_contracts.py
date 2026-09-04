from __future__ import annotations

from copy import deepcopy

import pytest
from murmur.live_scene.contracts import (
    LIVE_SCENE_SCHEMA_VERSION,
    MAX_PATCH_OPERATIONS,
    MAX_PATH_POINTS,
    MAX_SCENE_NARRATION_CHARS,
    MAX_SCENE_NODES,
    MAX_SCENE_PROMPT_CHARS,
    MAX_SCENE_TEXT_CHARS,
    LiveSceneRequest,
    ScenePatchDraft,
    ScenePatchEvent,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
    dump_scene_stream_event,
)
from pydantic import TypeAdapter, ValidationError


def _presentation(enter: str = "draw") -> dict[str, object]:
    return {"enter": enter, "exit": "fade"}


def _stroke_style(color: str = "hsl(var(--lavender))") -> dict[str, object]:
    return {
        "stroke": color,
        "strokeWidth": 4,
        "opacity": 1,
        "roughness": 0.75,
    }


def _line(node_id: str = "triangle-side-a") -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "line",
        "presentation": _presentation(),
        "points": [[185, 405], [525, 405]],
        "style": _stroke_style(),
    }


def _patch(*operations: dict[str, object]) -> dict[str, object]:
    return {
        "v": LIVE_SCENE_SCHEMA_VERSION,
        "patchId": "patch-foundation",
        "narration": "Start with the first side.",
        "operations": list(operations or ({"op": "put", "node": _line()},)),
    }


def test_scene_state_accepts_every_bounded_node_variant_and_freezes_it() -> None:
    state = SceneState.model_validate(
        {
            "revision": 4,
            "nodes": [
                _line(),
                {
                    "id": "angle-marker",
                    "kind": "path",
                    "presentation": _presentation(),
                    "points": [[485, 405], [485, 365], [525, 365]],
                    "closed": False,
                    "style": {
                        **_stroke_style("hsl(var(--chalk-soft))"),
                        "fill": "none",
                    },
                },
                {
                    "id": "area-square",
                    "kind": "rect",
                    "presentation": _presentation("scale"),
                    "x": 20,
                    "y": 30,
                    "width": 120,
                    "height": 120,
                    "style": {**_stroke_style("#A78BFA"), "fill": "transparent"},
                },
                {
                    "id": "lesson-title",
                    "kind": "text",
                    "presentation": _presentation("fade"),
                    "x": 400,
                    "y": 64,
                    "text": "A right triangle",
                    "style": {
                        "color": "hsl(var(--chalk))",
                        "fontSize": 28,
                        "opacity": 1,
                        "anchor": "middle",
                    },
                },
                {
                    "id": "theorem-equation",
                    "kind": "latex",
                    "presentation": _presentation("fade"),
                    "x": 130,
                    "y": 470,
                    "latex": "a^2+b^2=c^2",
                    "style": {
                        "color": "hsl(var(--amber))",
                        "fontSize": 30,
                        "opacity": 1,
                    },
                },
            ],
        }
    )

    assert [node.kind for node in state.nodes] == ["line", "path", "rect", "text", "latex"]
    assert state.nodes[0].points[0] == (185.0, 405.0)  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="frozen"):
        state.revision = 5


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda node: node.update(points=[[0, 0], [5000, 1]]), "less than or equal"),
        (lambda node: node["style"].update(strokeWidth=33), "less than or equal"),
        (lambda node: node["style"].update(roughness=11), "less than or equal"),
        (lambda node: node["style"].update(stroke="url(https://unsafe.test)"), "paint"),
        (lambda node: node.update(extra=True), "Extra inputs"),
    ],
)
def test_scene_nodes_reject_out_of_bounds_or_unknown_values(mutation, message: str) -> None:
    node = _line()
    mutation(node)

    with pytest.raises(ValidationError, match=message):
        SceneState.model_validate({"revision": 0, "nodes": [node]})


def test_node_bounds_match_the_800_by_600_frontend_contract() -> None:
    edge = _line()
    edge["points"] = [[0, 0], [800, 600]]
    assert SceneState.model_validate({"revision": 0, "nodes": [edge]}).nodes[0].points == (
        (0.0, 0.0),
        (800.0, 600.0),
    )

    for points in ([[0, 0], [800.01, 600]], [[0, 0], [800, 600.01]], [[-0.01, 0], [1, 1]]):
        invalid = _line()
        invalid["points"] = points
        with pytest.raises(ValidationError):
            SceneState.model_validate({"revision": 0, "nodes": [invalid]})


def test_rectangle_must_fit_fully_inside_the_board() -> None:
    base = {
        "id": "bounded-rect",
        "kind": "rect",
        "presentation": _presentation("scale"),
        "x": 700,
        "y": 500,
        "width": 100,
        "height": 100,
        "style": {**_stroke_style(), "fill": "transparent"},
    }
    SceneState.model_validate({"revision": 0, "nodes": [base]})

    for change in ({"width": 100.01}, {"height": 100.01}):
        invalid = {**base, **change}
        with pytest.raises(ValidationError, match="inside the board"):
            SceneState.model_validate({"revision": 0, "nodes": [invalid]})


def test_style_bounds_and_exact_theme_paints_match_the_frontend_decoder() -> None:
    assert SceneState.model_validate({"revision": 0, "nodes": [_line()]}).nodes

    bare_token = _line()
    bare_token["style"] = {**_stroke_style(), "stroke": "lavender"}
    with pytest.raises(ValidationError, match="paint"):
        SceneState.model_validate({"revision": 0, "nodes": [bare_token]})

    rough = _line()
    rough["style"] = {**_stroke_style(), "roughness": 4.01}
    with pytest.raises(ValidationError):
        SceneState.model_validate({"revision": 0, "nodes": [rough]})

    for font_size in (7.99, 96.01):
        text = {
            "id": "bounded-text",
            "kind": "text",
            "presentation": _presentation("fade"),
            "x": 10,
            "y": 10,
            "text": "Safe text",
            "style": {
                "color": "hsl(var(--chalk))",
                "fontSize": font_size,
                "opacity": 1,
                "anchor": "start",
            },
        }
        with pytest.raises(ValidationError):
            SceneState.model_validate({"revision": 0, "nodes": [text]})


def test_text_latex_path_and_scene_limits_are_enforced() -> None:
    text = {
        "id": "note",
        "kind": "text",
        "presentation": _presentation("fade"),
        "x": 10,
        "y": 10,
        "text": "x" * (MAX_SCENE_TEXT_CHARS + 1),
        "style": {
            "color": "hsl(var(--chalk))",
            "fontSize": 16,
            "opacity": 1,
            "anchor": "start",
        },
    }
    with pytest.raises(ValidationError, match="at most"):
        SceneState.model_validate({"revision": 0, "nodes": [text]})

    path = {
        "id": "long-path",
        "kind": "path",
        "presentation": _presentation(),
        "points": [[index, index] for index in range(MAX_PATH_POINTS + 1)],
        "closed": False,
        "style": {**_stroke_style(), "fill": "none"},
    }
    with pytest.raises(ValidationError, match="at most"):
        SceneState.model_validate({"revision": 0, "nodes": [path]})

    with pytest.raises(ValidationError, match="at most"):
        SceneState.model_validate(
            {
                "revision": 0,
                "nodes": [_line(f"node-{index}") for index in range(MAX_SCENE_NODES + 1)],
            }
        )


def test_text_style_rejects_model_authored_font_family() -> None:
    node = {
        "id": "note",
        "kind": "text",
        "presentation": _presentation("fade"),
        "x": 10,
        "y": 10,
        "text": "Safe text",
        "style": {
            "color": "hsl(var(--chalk))",
            "fontSize": 16,
            "fontFamily": "url(unsafe)",
            "opacity": 1,
            "anchor": "start",
        },
    }

    with pytest.raises(ValidationError, match="fontFamily"):
        SceneState.model_validate({"revision": 0, "nodes": [node]})


def test_scene_state_rejects_duplicate_ids_and_scalar_coercion() -> None:
    with pytest.raises(ValidationError, match="unique"):
        SceneState.model_validate({"revision": 0, "nodes": [_line(), _line()]})
    with pytest.raises(ValidationError):
        SceneState.model_validate({"revision": "0", "nodes": []})
    with pytest.raises(ValidationError):
        SceneState.model_validate({"revision": True, "nodes": []})


def test_patch_draft_is_lifecycle_free_exact_and_uses_camel_case_wire_aliases() -> None:
    draft = ScenePatchDraft.model_validate(_patch())
    wire = draft.model_dump(mode="json", by_alias=True)

    assert wire == _patch()
    assert "generation" not in wire
    assert "attempt" not in wire
    assert "sequence" not in wire
    assert "baseRevision" not in wire
    assert "resultRevision" not in wire

    for forbidden in ["generation", "attempt", "sequence", "baseRevision", "resultRevision"]:
        invalid = _patch()
        invalid[forbidden] = 1
        with pytest.raises(ValidationError, match="Extra inputs"):
            ScenePatchDraft.model_validate(invalid)


def test_patch_rejects_duplicate_targets_empty_operations_and_excess_narration() -> None:
    with pytest.raises(ValidationError, match="targets must be unique"):
        ScenePatchDraft.model_validate(
            _patch(
                {"op": "put", "node": _line()},
                {"op": "remove", "id": "triangle-side-a"},
            )
        )

    invalid = _patch()
    invalid["operations"] = []
    with pytest.raises(ValidationError, match="at least"):
        ScenePatchDraft.model_validate(invalid)

    invalid = _patch()
    invalid["narration"] = "n" * (MAX_SCENE_NARRATION_CHARS + 1)
    with pytest.raises(ValidationError, match="at most"):
        ScenePatchDraft.model_validate(invalid)

    invalid = _patch()
    invalid["operations"] = [
        {"op": "put", "node": _line(f"node-{index}")} for index in range(MAX_PATCH_OPERATIONS + 1)
    ]
    with pytest.raises(ValidationError, match="at most"):
        ScenePatchDraft.model_validate(invalid)


def test_request_is_an_exact_immutable_snapshot() -> None:
    request = LiveSceneRequest.model_validate(
        {
            "prompt": "  Explain the theorem progressively.  ",
            "generation": 3,
            "baseScene": {"revision": 7, "nodes": [_line()]},
        }
    )

    assert request.prompt == "Explain the theorem progressively."
    assert request.generation == 3
    assert request.base_scene.revision == 7
    assert request.model_dump(mode="json", by_alias=True)["baseScene"]["revision"] == 7

    with pytest.raises(ValidationError, match="Extra inputs"):
        LiveSceneRequest.model_validate(
            {
                "prompt": "Explain it.",
                "generation": 1,
                "baseScene": {"revision": 0, "nodes": []},
                "attempt": 1,
            }
        )


def test_request_prompt_has_the_shared_2000_character_bound() -> None:
    valid = LiveSceneRequest.model_validate(
        {
            "prompt": "p" * MAX_SCENE_PROMPT_CHARS,
            "generation": 1,
            "baseScene": {"revision": 0, "nodes": []},
        }
    )
    assert len(valid.prompt) == MAX_SCENE_PROMPT_CHARS

    with pytest.raises(ValidationError, match="at most"):
        LiveSceneRequest.model_validate(
            {
                "prompt": "p" * (MAX_SCENE_PROMPT_CHARS + 1),
                "generation": 1,
                "baseScene": {"revision": 0, "nodes": []},
            }
        )


def test_server_events_stamp_lifecycle_and_dump_canonical_wire_shapes() -> None:
    patch = ScenePatchDraft.model_validate(_patch())
    events: list[SceneStreamEvent] = [
        SceneStreamStartedEvent(generation=2, attempt=1, base_revision=4),
        ScenePatchEvent(
            generation=2,
            attempt=1,
            sequence=1,
            base_revision=4,
            result_revision=5,
            patch=patch,
        ),
        SceneStreamRepairingEvent(
            generation=2,
            from_attempt=1,
            to_attempt=2,
            last_accepted_revision=5,
            message="I am repairing the visual plan.",
        ),
        SceneStreamCompletedEvent(
            generation=2,
            final_revision=6,
            patch_count=2,
            first_patch_ms=120,
            total_ms=450,
            repaired=True,
        ),
        SceneStreamFailedEvent(
            generation=2,
            attempt=2,
            code="invalid_model_output",
            message="The last board is safe. Please retry.",
            last_accepted_revision=5,
            retryable=False,
        ),
    ]

    wire = [dump_scene_stream_event(event) for event in events]
    assert [event["type"] for event in wire] == [
        "scene_stream_started",
        "scene_patch",
        "scene_stream_repairing",
        "scene_stream_completed",
        "scene_stream_failed",
    ]
    assert wire[0]["baseRevision"] == 4
    assert wire[1]["resultRevision"] == 5
    assert wire[1]["patch"]["patchId"] == "patch-foundation"
    assert wire[2]["lastAcceptedRevision"] == 5
    assert wire[3]["firstPatchMs"] == 120.0
    assert wire[4]["lastAcceptedRevision"] == 5

    adapter = TypeAdapter(SceneStreamEvent)
    assert adapter.validate_python(wire[1]).type == "scene_patch"


def test_event_revision_attempt_and_latency_boundaries_fail_closed() -> None:
    patch = ScenePatchDraft.model_validate(_patch())
    with pytest.raises(ValidationError, match="exactly one greater"):
        ScenePatchEvent(
            generation=1,
            attempt=1,
            sequence=1,
            base_revision=4,
            result_revision=6,
            patch=patch,
        )
    with pytest.raises(ValidationError, match="exactly one greater"):
        SceneStreamRepairingEvent(
            generation=1,
            from_attempt=1,
            to_attempt=1,
            last_accepted_revision=0,
            message="Repairing.",
        )
    with pytest.raises(ValidationError, match="must not be less"):
        SceneStreamCompletedEvent(
            generation=1,
            final_revision=1,
            patch_count=1,
            first_patch_ms=200,
            total_ms=100,
            repaired=False,
        )


def test_caller_mutation_cannot_change_validated_patch() -> None:
    source = _patch()
    draft = ScenePatchDraft.model_validate(deepcopy(source))
    source["operations"][0]["node"]["points"][0][0] = 999  # type: ignore[index]

    operation = draft.operations[0]
    assert operation.op == "put"
    assert operation.node.points[0][0] == 185.0  # type: ignore[union-attr]
