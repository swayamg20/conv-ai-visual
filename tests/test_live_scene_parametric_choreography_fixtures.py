"""Regeneration and lifecycle checks for the real Gate 1.6 browser fixtures."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from murmur.live_scene.completing_square_contracts import (
    COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER,
)
from murmur.live_scene.completing_square_problem_contracts import (
    CompletingSquareProblemSpecV1,
)
from murmur.live_scene.contracts import SceneStreamCompletedEvent, SceneStreamStartedEvent
from murmur.live_scene.parametric_checkpoint_contracts import CHECKPOINT_COMPILER_V3_VERSION
from murmur.live_scene.parametric_choreography_requests import (
    PARAMETRIC_CHOREOGRAPHY_PROTOCOL,
)
from murmur.live_scene.parametric_choreography_service_contracts import (
    PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ParametricChoreographySceneCheckpointEventV3,
)

_REPOSITORY_ROOT = Path(__file__).parents[1]
_GENERATOR_PATH = _REPOSITORY_ROOT / "scripts" / "generate_parametric_choreography_fixtures.py"
_FIXTURE_DIRECTORY = _REPOSITORY_ROOT / "web" / "src" / "features" / "live-scene" / "fixtures"
_LEGACY_FIXTURE_PATH = _FIXTURE_DIRECTORY / "completing-the-square.v1.json"
_LEGACY_FIXTURE_SHA256 = "1df951328558d537347625419f9ccf16b454d06f45bd108fc79e86f89ee98887"

_CASES = (
    (2, 80, "completing-square-parametric-b2-c80.v3.json"),
    (8, 20, "completing-square-parametric-b8-c20.v3.json"),
    (16, 17, "completing-square-parametric-b16-c17.v3.json"),
)
_MAIN_CHECKPOINT_IDS = [checkpoint.value for checkpoint in COMPLETING_SQUARE_MAIN_CHECKPOINT_ORDER]


def _fixture(filename: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((_FIXTURE_DIRECTORY / filename).read_bytes()))


def _assert_lane(
    lane: dict[str, Any],
    *,
    problem: CompletingSquareProblemSpecV1,
    generation: int,
    expected_checkpoint_ids: list[str],
) -> list[ParametricChoreographySceneCheckpointEventV3]:
    assert set(lane) == {
        "generation",
        "base",
        "route",
        "checkpointIds",
        "checkpointCount",
        "events",
    }
    assert lane["generation"] == generation
    assert lane["checkpointIds"] == expected_checkpoint_ids
    assert lane["checkpointCount"] == len(expected_checkpoint_ids)

    events = [
        PARAMETRIC_CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
        for event in lane["events"]
    ]
    assert [event.type for event in events] == [
        "scene_stream_started",
        *("parametric_choreography_scene_checkpoint" for _ in expected_checkpoint_ids),
        "scene_stream_completed",
    ]
    assert isinstance(events[0], SceneStreamStartedEvent)
    assert isinstance(events[-1], SceneStreamCompletedEvent)
    checkpoints = [
        event for event in events if isinstance(event, ParametricChoreographySceneCheckpointEventV3)
    ]
    assert [event.sequence for event in checkpoints] == list(range(1, len(checkpoints) + 1))
    assert [event.semantic.checkpoint_id.value for event in checkpoints] == (
        expected_checkpoint_ids
    )
    assert all(event.generation == generation and event.attempt == 1 for event in checkpoints)
    assert all(event.semantic.problem_spec == problem for event in checkpoints)

    base = lane["base"]
    assert set(base) == {
        "revision",
        "checkpointId",
        "cornerClarified",
        "certificateHeadSha256",
    }
    base_revision = base["revision"]
    assert events[0].generation == generation
    assert events[0].attempt == 1
    assert events[0].base_revision == base_revision
    assert [(event.base_revision, event.result_revision) for event in checkpoints] == [
        (base_revision + offset, base_revision + offset + 1) for offset in range(len(checkpoints))
    ]

    expected_head = base["certificateHeadSha256"]
    for event in checkpoints:
        assert event.semantic.semantic_base_certificate_sha256 == expected_head
        assert event.semantic.certificate.body.previous_certificate_sha256 == expected_head
        expected_head = event.semantic.semantic_result_certificate_sha256
        assert expected_head == event.semantic.certificate.certificate_sha256

    completed = events[-1]
    assert completed.model_dump(mode="json", by_alias=True) == {
        "type": "scene_stream_completed",
        "generation": generation,
        "finalRevision": base_revision + len(checkpoints),
        "patchCount": len(checkpoints),
        "firstPatchMs": 0.0,
        "totalMs": 0.0,
        "repaired": False,
    }
    return checkpoints


def test_checked_in_v3_fixtures_are_two_exact_deterministic_regenerations(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for output_directory in (first, second):
        subprocess.run(
            [
                sys.executable,
                str(_GENERATOR_PATH),
                "--output-directory",
                str(output_directory),
            ],
            cwd=_REPOSITORY_ROOT,
            check=True,
        )

    expected_names = {filename for _, _, filename in _CASES}
    assert {path.name for path in first.iterdir()} == expected_names
    assert {path.name for path in second.iterdir()} == expected_names
    for filename in expected_names:
        checked_in = (_FIXTURE_DIRECTORY / filename).read_bytes()
        assert first.joinpath(filename).read_bytes() == checked_in
        assert second.joinpath(filename).read_bytes() == checked_in

    assert hashlib.sha256(_LEGACY_FIXTURE_PATH.read_bytes()).hexdigest() == (_LEGACY_FIXTURE_SHA256)


@pytest.mark.parametrize("linear_coefficient,right_hand_side,filename", _CASES)
def test_each_v3_fixture_is_one_real_eight_checkpoint_reflex_solve_lane(
    linear_coefficient: int,
    right_hand_side: int,
    filename: str,
) -> None:
    fixture = _fixture(filename)
    problem = CompletingSquareProblemSpecV1(
        linearCoefficient=linear_coefficient,
        rightHandSide=right_hand_side,
    )
    assert set(fixture) == {
        "v",
        "fixtureId",
        "protocol",
        "compilerVersion",
        "problemText",
        "problemSpec",
        "providerRequestCount",
        "lanes",
    }
    assert fixture["v"] == 1
    assert fixture["fixtureId"] == (
        f"completing-square-parametric-b{linear_coefficient}-c{right_hand_side}"
    )
    assert fixture["protocol"] == PARAMETRIC_CHOREOGRAPHY_PROTOCOL
    assert fixture["compilerVersion"] == CHECKPOINT_COMPILER_V3_VERSION
    assert fixture["problemText"] == (f"x² + {linear_coefficient}x = {right_hand_side}")
    assert fixture["problemSpec"] == problem.model_dump(mode="json", by_alias=True)
    assert fixture["providerRequestCount"] == 0

    lanes = fixture["lanes"]
    assert set(lanes) == (
        {"main", "clarifyCorner", "continueAfterClarification"}
        if (linear_coefficient, right_hand_side) == (8, 20)
        else {"main"}
    )
    main = lanes["main"]
    assert main["base"] == {
        "revision": 0,
        "checkpointId": None,
        "cornerClarified": False,
        "certificateHeadSha256": None,
    }
    assert main["route"] == {"intent": "advance", "targetStage": "solve"}
    checkpoints = _assert_lane(
        main,
        problem=problem,
        generation=1,
        expected_checkpoint_ids=_MAIN_CHECKPOINT_IDS,
    )
    assert [
        event.semantic.result_component.last_main_checkpoint.value for event in checkpoints
    ] == (_MAIN_CHECKPOINT_IDS)
    assert not any(event.semantic.result_component.corner_clarified for event in checkpoints)


def test_primary_fixture_has_exact_stop_clarify_and_continue_certificate_lanes() -> None:
    fixture = _fixture("completing-square-parametric-b8-c20.v3.json")
    problem = CompletingSquareProblemSpecV1(linearCoefficient=8, rightHandSide=20)
    lanes = fixture["lanes"]
    main = _assert_lane(
        lanes["main"],
        problem=problem,
        generation=1,
        expected_checkpoint_ids=_MAIN_CHECKPOINT_IDS,
    )
    missing_corner = main[4]

    clarify_lane = lanes["clarifyCorner"]
    assert clarify_lane["base"] == {
        "revision": missing_corner.result_revision,
        "checkpointId": "missing_corner",
        "cornerClarified": False,
        "certificateHeadSha256": (missing_corner.semantic.semantic_result_certificate_sha256),
    }
    assert clarify_lane["route"] == {"intent": "clarify_corner"}
    clarify = _assert_lane(
        clarify_lane,
        problem=problem,
        generation=2,
        expected_checkpoint_ids=["corner_detail"],
    )
    assert clarify[0].semantic.result_component.last_main_checkpoint.value == "missing_corner"
    assert clarify[0].semantic.result_component.corner_clarified

    continuation_lane = lanes["continueAfterClarification"]
    assert continuation_lane["base"] == {
        "revision": clarify[0].result_revision,
        "checkpointId": "missing_corner",
        "cornerClarified": True,
        "certificateHeadSha256": clarify[0].semantic.semantic_result_certificate_sha256,
    }
    assert continuation_lane["route"] == {
        "intent": "advance",
        "targetStage": "solve",
    }
    continuation = _assert_lane(
        continuation_lane,
        problem=problem,
        generation=3,
        expected_checkpoint_ids=[
            "balance_and_complete",
            "factor_square",
            "solve_roots",
        ],
    )
    assert all(event.semantic.result_component.corner_clarified for event in continuation)
    assert continuation[-1].result_revision == 9
