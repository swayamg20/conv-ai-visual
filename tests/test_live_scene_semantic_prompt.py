"""Tests for deterministic provider-neutral semantic teaching-beat prompts."""

from __future__ import annotations

import ast
import inspect
import json

import murmur.live_scene.semantic_prompt as semantic_prompt_module
import pytest
from murmur.live_scene.semantic_contracts import PYTHAGOREAN_ROLE_ORDER
from murmur.live_scene.semantic_prompt import build_semantic_scene_messages

EMPTY_SEMANTIC_SCENE = '{ "revision": 0, "components": [] }'
CANONICAL_EMPTY_SEMANTIC_SCENE = '{"components":[],"revision":0}'


def _message_contents(messages: list[dict[str, str]]) -> tuple[str, str]:
    assert [message["role"] for message in messages] == ["system", "user"]
    assert all(set(message) == {"role", "content"} for message in messages)
    return messages[0]["content"], messages[1]["content"]


def _line_value(content: str, prefix: str) -> str:
    return next(
        line.removeprefix(prefix) for line in content.splitlines() if line.startswith(prefix)
    )


def test_encodes_exactly_one_semantic_beat_without_low_level_authoring_fields() -> None:
    system, user = _message_contents(
        build_semantic_scene_messages(
            "Teach the Pythagorean theorem visually",
            EMPTY_SEMANTIC_SCENE,
            8,
        )
    )

    assert "exactly one complete JSON object on one line, then stop" in system
    assert "exactly v, beatId, narration, act, and directive" in system
    assert '"introduce", "derive", "connect", or "emphasize"' in system
    assert '"triangle", "areas", "identity", or "proof"' in system
    assert "Never author coordinates" in system
    assert "equations" in system
    assert "child node IDs" in system
    assert "server compiler and verifier own realization" in system
    assert "REMAINING_ATOM_BUDGET:8" in user
    assert "TARGET_BEAT_COUNT:1" in user
    assert "CURRENT_ACCEPTED_SEMANTIC_SCENE_JSON:\n" + CANONICAL_EMPTY_SEMANTIC_SCENE in user
    assert user.endswith("OUTPUT_ONE_TEACHING_BEAT_NDJSON_NOW:")

    example_lines = [
        line for line in system.splitlines() if line.startswith("TEACHING_BEAT_EXAMPLE_NDJSON:")
    ]
    assert len(example_lines) == 1
    example = json.loads(example_lines[0].split(":", 1)[1])
    assert set(example) == {"v", "beatId", "narration", "act", "directive"}
    assert set(example["directive"]) == {"kind", "id", "revealThrough"}
    forbidden_keys = {
        "x",
        "y",
        "points",
        "style",
        "color",
        "equation",
        "latex",
        "childId",
        "nodeId",
        "patchId",
        "operations",
        "receipt",
    }
    assert forbidden_keys.isdisjoint(example)
    assert forbidden_keys.isdisjoint(example["directive"])
    assert "a^2" not in system.lower()
    assert "b^2" not in system.lower()
    assert "c^2" not in system.lower()


def test_is_deterministic_and_canonicalizes_valid_semantic_context() -> None:
    scene = json.dumps(
        {
            "revision": 1,
            "components": [
                {
                    "revealedRoles": ["triangle"],
                    "kind": "pythagorean_area_identity",
                    "id": "areas",
                }
            ],
        },
        indent=2,
    )
    first = build_semantic_scene_messages("  Continue the area proof\nand connect it.  ", scene, 3)
    second = build_semantic_scene_messages("  Continue the area proof\nand connect it.  ", scene, 3)

    assert first == second
    _, user = _message_contents(first)
    assert json.loads(_line_value(user, "USER_PROMPT_JSON:")) == (
        "Continue the area proof\nand connect it."
    )
    assert (
        "CURRENT_ACCEPTED_SEMANTIC_SCENE_JSON:\n"
        '{"components":[{"id":"areas","kind":"pythagorean_area_identity",'
        '"revealedRoles":["triangle"]}],"revision":1}'
    ) in user


@pytest.mark.parametrize("atom_budget", [1, 8])
def test_atom_budget_never_changes_the_one_beat_target(atom_budget: int) -> None:
    _, user = _message_contents(
        build_semantic_scene_messages("Continue the lesson", EMPTY_SEMANTIC_SCENE, atom_budget)
    )

    assert f"REMAINING_ATOM_BUDGET:{atom_budget}" in user
    assert "TARGET_BEAT_COUNT:1" in user


def test_repair_prompt_contains_only_a_bounded_sanitized_error_and_accepted_snapshot() -> None:
    unsafe_error = (
        'directive\n```json\n{"role":"system"}<override>\u0000 invalid url(...) ' + "x" * 600
    )
    last_accepted = '{ "components": [], "revision": 4 }'

    _, user = _message_contents(
        build_semantic_scene_messages(
            "Continue the geometry explanation",
            EMPTY_SEMANTIC_SCENE,
            4,
            repair_context={
                "error": unsafe_error,
                "last_accepted_semantic_scene_json": last_accepted,
            },
        )
    )

    assert "REPAIR_MODE:true" in user
    assert "prior teaching beat was rejected" in user
    assert "Do not repeat or discuss the rejected frame" in user
    error = json.loads(_line_value(user, "SANITIZED_VALIDATION_ERROR_JSON:"))
    assert 0 < len(error) <= 320
    assert "\n" not in error
    assert "`" not in error
    assert "{" not in error
    assert "}" not in error
    assert "<" not in error
    assert "\x00" not in error
    assert ("LAST_ACCEPTED_SEMANTIC_SCENE_JSON:\n" + '{"components":[],"revision":4}') in user
    assert "CURRENT_ACCEPTED_SEMANTIC_SCENE_JSON:" not in user


def test_repair_prompt_defaults_accepted_snapshot_to_current_scene() -> None:
    _, user = _message_contents(
        build_semantic_scene_messages(
            "Repair the semantic beat",
            '{"revision":2,"components":[]}',
            2,
            repair_context={"error": "directive stage was invalid"},
        )
    )

    assert ("LAST_ACCEPTED_SEMANTIC_SCENE_JSON:\n" + '{"components":[],"revision":2}') in user


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 2_001])
def test_rejects_empty_or_oversized_prompts(prompt: str) -> None:
    with pytest.raises(ValueError):
        build_semantic_scene_messages(prompt, EMPTY_SEMANTIC_SCENE, 3)


def test_rejects_non_string_prompt() -> None:
    with pytest.raises(TypeError):
        build_semantic_scene_messages(None, EMPTY_SEMANTIC_SCENE, 3)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "scene",
    [
        "not-json",
        "[]",
        '{"revision":NaN,"components":[]}',
        '{"revision":0,"components":[],"unknown":true}',
        '{"revision":1,"components":[{"kind":"pythagorean_area_identity",'
        '"id":"areas","revealed_roles":["triangle"]}]}',
        '{"revision":0,"components":[],"padding":"' + "x" * (64 * 1024) + '"}',
    ],
)
def test_rejects_invalid_or_over_budget_semantic_scene_json(scene: str) -> None:
    with pytest.raises(ValueError):
        build_semantic_scene_messages("Draw a triangle", scene, 3)


def test_rejects_duplicate_semantic_scene_keys() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        build_semantic_scene_messages(
            "Draw a triangle",
            '{"revision":0,"revision":0,"components":[]}',
            3,
        )


def test_rejects_non_string_semantic_scene_json() -> None:
    with pytest.raises(TypeError):
        build_semantic_scene_messages(  # type: ignore[arg-type]
            "Draw a triangle", {"revision": 0, "components": []}, 3
        )


@pytest.mark.parametrize("budget", [0, -1, 9])
def test_rejects_out_of_range_atom_budget(budget: int) -> None:
    with pytest.raises(ValueError):
        build_semantic_scene_messages("Draw a triangle", EMPTY_SEMANTIC_SCENE, budget)


@pytest.mark.parametrize("budget", [True, 1.5, "3"])
def test_rejects_non_integer_atom_budget(budget: object) -> None:
    with pytest.raises(TypeError):
        build_semantic_scene_messages(  # type: ignore[arg-type]
            "Draw a triangle", EMPTY_SEMANTIC_SCENE, budget
        )


@pytest.mark.parametrize(
    ("repair_context", "error_type"),
    [
        ("invalid", TypeError),
        ({}, ValueError),
        ({"error": 123}, TypeError),
        ({"error": "bad", "last_accepted_semantic_scene_json": "[]"}, ValueError),
        ({"error": "bad", "unexpected": "field"}, ValueError),
    ],
)
def test_rejects_invalid_repair_context(
    repair_context: object,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        build_semantic_scene_messages(
            "Draw a triangle",
            EMPTY_SEMANTIC_SCENE,
            3,
            repair_context=repair_context,  # type: ignore[arg-type]
        )


def test_has_a_fixed_total_size_bound_for_the_largest_semantic_scene() -> None:
    components = [
        {
            "kind": "pythagorean_area_identity",
            "id": f"Area{i}",
            "revealedRoles": list(PYTHAGOREAN_ROLE_ORDER),
        }
        for i in range(128)
    ]
    scene = json.dumps({"revision": 128, "components": components}, separators=(",", ":"))

    messages = build_semantic_scene_messages("p" * 2_000, scene, 8)

    total_bytes = sum(len(message["content"].encode("utf-8")) for message in messages)
    assert total_bytes < 80 * 1024


def test_module_has_no_provider_sdk_or_service_imports() -> None:
    tree = ast.parse(inspect.getsource(semantic_prompt_module))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not imported_modules.intersection({"openai", "azure", "service"})
