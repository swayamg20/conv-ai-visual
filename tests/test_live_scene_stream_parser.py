from __future__ import annotations

import json
import traceback

import pytest
from murmur.live_scene import ScenePatchStreamError, ScenePatchStreamParser


def _patch_line(patch_id: str, narration: str = "Draw café ∠ABC.") -> str:
    return json.dumps(
        {
            "v": 1,
            "patchId": patch_id,
            "narration": narration,
            "operations": [
                {
                    "op": "put",
                    "node": {
                        "id": f"line-{patch_id}",
                        "kind": "line",
                        "presentation": {"enter": "draw", "exit": "fade"},
                        "points": [[10, 20], [200, 220]],
                        "style": {
                            "stroke": "hsl(var(--lavender))",
                            "strokeWidth": 3,
                            "opacity": 1,
                            "roughness": 0.5,
                        },
                    },
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_reconstructs_one_frame_across_every_character_boundary() -> None:
    line = _patch_line("patch-a") + "\n"
    parser = ScenePatchStreamParser()
    patches = []

    for character in line:
        patches.extend(parser.feed(character))
    patches.extend(parser.finish())

    assert [patch.patch_id for patch in patches] == ["patch-a"]
    assert patches[0].narration == "Draw café ∠ABC."
    assert parser.frame_count == 1
    assert parser.closed is True


def test_reconstructs_utf8_when_every_byte_is_a_separate_chunk() -> None:
    payload = (_patch_line("patch-byte") + "\n").encode()
    parser = ScenePatchStreamParser()
    patches = []

    for value in payload:
        patches.extend(parser.feed(bytes([value])))
    patches.extend(parser.finish())

    assert [patch.patch_id for patch in patches] == ["patch-byte"]
    assert patches[0].narration.endswith("∠ABC.")


def test_accepts_multiple_frames_crlf_blank_lines_and_final_frame_without_newline() -> None:
    parser = ScenePatchStreamParser()
    first = _patch_line("patch-one")
    second = _patch_line("patch-two")
    third = _patch_line("patch-three")

    patches = list(parser.feed(f"\r\n{first}\r\n{second}\n\n{third[:30]}"))
    patches.extend(parser.feed(third[30:]))
    patches.extend(parser.finish())

    assert [patch.patch_id for patch in patches] == [
        "patch-one",
        "patch-two",
        "patch-three",
    ]


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ("This is a scene patch.\n", "invalid_json"),
        ("```json\n", "invalid_json"),
        ("{not-json}\n", "invalid_json"),
        (
            json.dumps(
                {
                    "v": 1,
                    "patchId": "patch-a",
                    "narration": "Draw it.",
                    "operations": [],
                }
            )
            + "\n",
            "invalid_patch",
        ),
        (
            json.dumps(
                {
                    "v": 1,
                    "patchId": "patch-a",
                    "narration": "Draw it.",
                    "operations": [{"op": "clear"}],
                }
            )
            + "\n",
            "invalid_patch",
        ),
        (
            json.dumps(
                {
                    "v": 1,
                    "patchId": "patch-a",
                    "narration": "Draw it.",
                    "operations": [{"op": "remove", "id": "old-node"}],
                    "generation": 1,
                }
            )
            + "\n",
            "invalid_patch",
        ),
    ],
)
def test_prose_fences_malformed_json_and_invalid_drafts_fail_closed(
    frame: str, code: str
) -> None:
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(frame)

    assert captured.value.code == code
    assert captured.value.frame_number == 1
    assert parser.closed is True
    with pytest.raises(ScenePatchStreamError, match="closed"):
        parser.feed(_patch_line("late") + "\n")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(v=True),
        lambda payload: payload.update(v=1.0),
        lambda payload: payload.update(patch_id=payload.pop("patchId")),
        lambda payload: payload["operations"][0]["node"]["style"].update(
            stroke_width=payload["operations"][0]["node"]["style"].pop("strokeWidth")
        ),
    ],
)
def test_wire_parser_requires_strict_version_and_camel_case_aliases(mutation) -> None:
    payload = json.loads(_patch_line("strict-wire"))
    mutation(payload)
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    assert captured.value.code == "invalid_patch"


def test_wire_parser_rejects_duplicate_keys_and_nonstandard_constants() -> None:
    parser = ScenePatchStreamParser()
    duplicate = _patch_line("duplicate").replace('"v":1', '"v":1,"v":1', 1)

    with pytest.raises(ScenePatchStreamError) as duplicate_error:
        parser.feed(duplicate + "\n")
    assert duplicate_error.value.code == "invalid_json"

    parser = ScenePatchStreamParser()
    nonstandard = _patch_line("nan").replace('"opacity":1', '"opacity":NaN', 1)
    with pytest.raises(ScenePatchStreamError) as constant_error:
        parser.feed(nonstandard + "\n")
    assert constant_error.value.code == "invalid_json"


def test_error_identifies_the_frame_after_prior_valid_output() -> None:
    parser = ScenePatchStreamParser()
    first = parser.feed(_patch_line("patch-one") + "\n")
    assert [patch.patch_id for patch in first] == ["patch-one"]

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed("not json\n")

    assert captured.value.frame_number == 2
    assert parser.frame_count == 1


def test_out_of_bounds_patch_exposes_only_a_fixed_safe_repair_hint() -> None:
    payload = json.loads(_patch_line("private-patch", "private narration"))
    payload["operations"][0]["node"] = {
        "id": "private-text",
        "kind": "text",
        "presentation": {"enter": "fade", "exit": "fade"},
        "x": -12_345,
        "y": 120,
        "text": "private model text",
        "style": {
            "color": "hsl(var(--chalk))",
            "fontSize": 18,
            "opacity": 1,
            "anchor": "start",
        },
    }
    payload["operations"].append(
        {
            "op": "put",
            "node": {
                "id": "private-rect",
                "kind": "rect",
                "presentation": {"enter": "draw", "exit": "fade"},
                "x": -50,
                "y": -25,
                "width": 100,
                "height": 100,
                "style": {
                    "stroke": "hsl(var(--lavender))",
                    "strokeWidth": 3,
                    "opacity": 1,
                    "roughness": 0.5,
                    "fill": "transparent",
                },
            },
        }
    )
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    assert captured.value.code == "invalid_patch"
    assert captured.value.repair_hint == (
        "scene_bounds: resize or reposition nodes using stable IDs; keep every point inside "
        "800x600, x and y at least 0, x plus width at most 800, and y plus height at most 600"
    )
    assert len(captured.value.repair_hint) <= 320
    assert "private" not in captured.value.repair_hint
    assert "12345" not in captured.value.repair_hint


def test_rectangle_overflow_uses_the_same_fixed_scene_bounds_hint() -> None:
    payload = json.loads(_patch_line("overflow-patch"))
    payload["operations"][0]["node"] = {
        "id": "overflow-rect",
        "kind": "rect",
        "presentation": {"enter": "draw", "exit": "fade"},
        "x": 750,
        "y": 550,
        "width": 100,
        "height": 100,
        "style": {
            "stroke": "hsl(var(--lavender))",
            "strokeWidth": 3,
            "opacity": 1,
            "roughness": 0.5,
            "fill": "transparent",
        },
    }
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    assert captured.value.repair_hint.startswith("scene_bounds:")


def test_generic_invalid_patch_exposes_a_fixed_schema_repair_hint() -> None:
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed('{"v":1,"patchId":"patch-a","narration":"Draw it","operations":[]}\n')

    assert captured.value.repair_hint == (
        "invalid_patch: follow the ScenePatch v1 schema exactly"
    )


def test_invalid_patch_traceback_suppresses_raw_model_values() -> None:
    raw_sentinel = "private-coordinate-sentinel"
    payload = json.loads(_patch_line("patch-a"))
    payload["operations"][0]["node"]["points"][0][0] = raw_sentinel
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(json.dumps(payload, separators=(",", ":")) + "\n")

    rendered_traceback = "".join(
        traceback.format_exception(
            type(captured.value),
            captured.value,
            captured.value.__traceback__,
        )
    )
    assert raw_sentinel not in rendered_traceback


def test_stream_error_rejects_non_allowlisted_repair_hints() -> None:
    with pytest.raises(ValueError, match="fixed internal value"):
        ScenePatchStreamError(
            "invalid_patch",
            "fixed public message",
            repair_hint="private model-authored value",
        )


def test_frame_size_is_measured_in_utf8_bytes_and_is_bounded() -> None:
    parser = ScenePatchStreamParser(max_frame_bytes=128)

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed("é" * 65)

    assert captured.value.code == "frame_too_large"
    assert captured.value.frame_number == 1


@pytest.mark.parametrize("limit", [0, -1, 65_537, True, 1.5])
def test_invalid_custom_frame_limit_is_rejected(limit: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ScenePatchStreamParser(max_frame_bytes=limit)  # type: ignore[arg-type]


def test_finish_rejects_incomplete_utf8() -> None:
    parser = ScenePatchStreamParser()
    parser.feed(b"\xe2\x88")

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.finish()

    assert captured.value.code == "invalid_utf8"
    assert parser.closed is True


def test_abort_discards_partial_frame_and_rejects_late_chunks() -> None:
    parser = ScenePatchStreamParser()
    parser.feed(_patch_line("partial")[:40])
    parser.abort()

    assert parser.closed is True
    assert parser.frame_count == 0
    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(_patch_line("late") + "\n")
    assert captured.value.code == "parser_closed"


def test_non_text_chunk_fails_closed() -> None:
    parser = ScenePatchStreamParser()

    with pytest.raises(ScenePatchStreamError) as captured:
        parser.feed(123)  # type: ignore[arg-type]

    assert captured.value.code == "invalid_patch"
