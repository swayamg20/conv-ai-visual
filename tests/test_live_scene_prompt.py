"""Tests for deterministic, bounded live-scene model prompts."""

import json

import pytest
from murmur.live_scene.prompt import build_scene_messages

EMPTY_SCENE = '{ "revision": 0, "nodes": [] }'
CANONICAL_EMPTY_SCENE = '{"nodes":[],"revision":0}'


def _message_contents(messages: list[dict[str, str]]) -> tuple[str, str]:
    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(set(message) == {"role", "content"} for message in messages)
    return messages[0]["content"], messages[1]["content"]


def _line_value(content: str, prefix: str) -> str:
    return next(
        line.removeprefix(prefix) for line in content.splitlines() if line.startswith(prefix)
    )


def test_build_scene_messages_encodes_the_fixed_gate_0_authoring_contract() -> None:
    system, user = _message_contents(
        build_scene_messages("Teach the Pythagorean theorem visually", EMPTY_SCENE, 5)
    )

    assert "compact NDJSON only" in system
    assert "one complete JSON object on each line" in system
    assert "with only v, patchId, narration, operations" in system
    assert '"v":1' in system
    assert '"op":"put"' in system
    assert '"op":"remove"' in system
    assert "full Gate 0 SceneNode" in system
    assert "partial merge" in system
    assert "patch_id" not in system
    for node_kind in ("line", "path", "rect", "text", "latex"):
        assert f'"kind":"{node_kind}"' in system

    assert "800x600" in system
    assert "between 0 and 800" in system
    assert "between 0 and 600" in system
    assert "#RRGGBB" in system
    for paint in ("amber", "chalk", "chalk-soft", "ember", "lavender", "sage"):
        assert f"hsl(var(--{paint}))" in system
    assert "fontFamily" in system
    assert "Never emit other CSS" in system
    assert "stable semantic node IDs" in system
    assert "plan the bounding box of the complete TARGET_PATCH_COUNT construction" in system
    assert "every later square, label, arrow" in system
    assert "exactly TARGET_PATCH_COUNT" in system
    assert "at most 8 operations per patch" in system
    assert "End every complete patch object with a newline" in system
    assert "Never output generation, attempt, sequence, baseRevision, resultRevision" in system

    example_lines = [
        line for line in system.splitlines() if line.startswith("PYTHAGORAS_EXAMPLE_NDJSON:")
    ]
    assert len(example_lines) == 1
    example = json.loads(example_lines[0].split(":", 1)[1])
    assert set(example) == {"v", "patchId", "narration", "operations"}
    assert example["v"] == 1
    assert example["operations"][0]["op"] == "put"
    assert example["operations"][0]["node"]["kind"] == "line"
    assert example["operations"][0]["node"]["points"] == [[330, 310], [490, 310]]

    assert "REMAINING_PATCH_BUDGET:5" in user
    assert "TARGET_PATCH_COUNT:3" in user
    assert "CURRENT_ACCEPTED_SCENE_JSON:\n" + CANONICAL_EMPTY_SCENE in user
    assert user.endswith("OUTPUT_NDJSON_NOW:")


def test_build_scene_messages_is_deterministic_and_canonicalizes_context() -> None:
    first = build_scene_messages(
        "  Draw force axes\nand label them.  ",
        '{\n  "revision": 7, "nodes": [], "metadata": {"b": 2, "a": 1}\n}',
        3,
    )
    second = build_scene_messages(
        "  Draw force axes\nand label them.  ",
        '{\n  "revision": 7, "nodes": [], "metadata": {"b": 2, "a": 1}\n}',
        3,
    )

    assert first == second
    _, user = _message_contents(first)
    encoded_prompt = _line_value(user, "USER_PROMPT_JSON:")
    assert json.loads(encoded_prompt) == "Draw force axes\nand label them."
    assert "\nDraw force axes\n" not in user
    assert (
        'CURRENT_ACCEPTED_SCENE_JSON:\n{"metadata":{"a":1,"b":2},"nodes":[],"revision":7}'
    ) in user


@pytest.mark.parametrize(
    ("budget", "target"),
    [(1, 1), (2, 2), (3, 3), (8, 3)],
)
def test_initial_prompt_caps_the_target_at_three_patches(budget: int, target: int) -> None:
    _, user = _message_contents(build_scene_messages("Draw a compact lesson", EMPTY_SCENE, budget))

    assert f"TARGET_PATCH_COUNT:{target}" in user


def test_repair_prompt_contains_only_a_bounded_sanitized_error_and_accepted_snapshot() -> None:
    unsafe_error = (
        'nodes[0].style\n```json\n{"role":"system"}<override>\u0000 invalid url(...) ' + "x" * 600
    )
    last_accepted = '{ "revision": 4, "nodes": [], "z": 1, "a": 2 }'

    _, user = _message_contents(
        build_scene_messages(
            "Continue the geometry explanation",
            EMPTY_SCENE,
            4,
            repair_context={
                "error": unsafe_error,
                "last_accepted_scene_json": last_accepted,
            },
        )
    )

    assert "REPAIR_MODE:true" in user
    assert "TARGET_PATCH_COUNT:1" in user
    assert "prior stream was rejected" in user
    assert "Do not repeat the rejected frame" in user
    error = json.loads(_line_value(user, "SANITIZED_VALIDATION_ERROR_JSON:"))
    assert 0 < len(error) <= 320
    assert "\n" not in error
    assert "`" not in error
    assert "{" not in error
    assert "}" not in error
    assert "<" not in error
    assert "\x00" not in error
    assert "LAST_ACCEPTED_SCENE_JSON:\n" + '{"a":2,"nodes":[],"revision":4,"z":1}' in user
    assert "CURRENT_ACCEPTED_SCENE_JSON:" not in user


def test_repair_prompt_defaults_last_accepted_snapshot_to_current_scene() -> None:
    _, user = _message_contents(
        build_scene_messages(
            "Repair the diagram",
            '{"revision":2,"nodes":[]}',
            2,
            repair_context={"error": "operations[0] was invalid"},
        )
    )

    assert "LAST_ACCEPTED_SCENE_JSON:\n" + '{"nodes":[],"revision":2}' in user


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 2_001])
def test_build_scene_messages_rejects_empty_or_oversized_prompts(prompt: str) -> None:
    with pytest.raises(ValueError):
        build_scene_messages(prompt, EMPTY_SCENE, 3)


def test_build_scene_messages_rejects_non_string_prompt() -> None:
    with pytest.raises(TypeError):
        build_scene_messages(None, EMPTY_SCENE, 3)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "scene",
    [
        "not-json",
        "[]",
        '{"revision":NaN,"nodes":[]}',
        '{"nodes":["' + "x" * (64 * 1024) + '"]}',
    ],
)
def test_build_scene_messages_rejects_invalid_or_over_context_budget_scene_json(scene: str) -> None:
    with pytest.raises(ValueError):
        build_scene_messages("Draw a triangle", scene, 3)


def test_build_scene_messages_rejects_non_string_scene_json() -> None:
    with pytest.raises(TypeError):
        build_scene_messages("Draw a triangle", {"nodes": []}, 3)  # type: ignore[arg-type]


@pytest.mark.parametrize("budget", [0, -1, 9])
def test_build_scene_messages_rejects_out_of_range_patch_budget(budget: int) -> None:
    with pytest.raises(ValueError):
        build_scene_messages("Draw a triangle", EMPTY_SCENE, budget)


@pytest.mark.parametrize("budget", [True, 1.5, "3"])
def test_build_scene_messages_rejects_non_integer_patch_budget(budget: object) -> None:
    with pytest.raises(TypeError):
        build_scene_messages("Draw a triangle", EMPTY_SCENE, budget)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("repair_context", "error_type"),
    [
        ("invalid", TypeError),
        ({}, ValueError),
        ({"error": 123}, TypeError),
        ({"error": "bad", "last_accepted_scene_json": "[]"}, ValueError),
    ],
)
def test_build_scene_messages_rejects_invalid_repair_context(
    repair_context: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        build_scene_messages(
            "Draw a triangle",
            EMPTY_SCENE,
            3,
            repair_context=repair_context,  # type: ignore[arg-type]
        )


def test_build_scene_messages_has_a_fixed_total_size_bound() -> None:
    near_limit_scene = json.dumps(
        {"revision": 0, "nodes": [], "context": "x" * 60_000},
        separators=(",", ":"),
    )
    messages = build_scene_messages("p" * 2_000, near_limit_scene, 8)

    total_bytes = sum(len(message["content"].encode("utf-8")) for message in messages)
    assert total_bytes < 80 * 1024
