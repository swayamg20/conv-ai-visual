from murmur.canvas.state import CANVAS_TOOL_SCHEMA, CanvasState, canvas_update


def test_canvas_state_is_owned_by_the_calling_pipeline() -> None:
    first = CanvasState()
    second = CanvasState()

    result = canvas_update(
        [{"action": "rect", "id": "box", "x": 10, "y": 20}],
        session_id="session-a",
        state=first,
    )

    assert result["applied_count"] == 1
    assert "box" in first.elements
    assert second.elements == {}


def test_canvas_controls_resolve_semantic_labels() -> None:
    state = CanvasState()
    canvas_update(
        [{"action": "text", "id": "generated-id", "label": "answer", "text": "42"}],
        state=state,
    )

    result = canvas_update(
        [{"action": "highlight", "target_id": "answer"}],
        state=state,
    )

    assert result["operations"] == [{"action": "highlight", "id": "generated-id"}]


def test_canvas_schema_matches_backend_actions() -> None:
    action_schema = CANVAS_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"][
        "items"
    ]["properties"]["action"]

    assert set(action_schema["enum"]) == {
        "rect",
        "circle",
        "ellipse",
        "line",
        "arrow",
        "text",
        "path",
        "clear",
        "delete",
        "highlight",
    }


def test_canvas_state_rejects_unknown_actions_and_duplicate_ids() -> None:
    state = CanvasState()

    first = canvas_update(
        [{"action": "circle", "id": "shape"}],
        state=state,
    )
    rejected = canvas_update(
        [
            {"action": "circle", "id": "shape"},
            {"action": "unsupported", "id": "other"},
        ],
        state=state,
    )

    assert first["applied_count"] == 1
    assert rejected["applied_count"] == 0
    assert list(state.elements) == ["shape"]
