"""Golden consistency checks for the provider-free choreography fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from murmur.live_scene.checkpoint_contracts import CHECKPOINT_COMPILER_VERSION
from murmur.live_scene.choreography_service_contracts import (
    CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER,
    ChoreographySceneCheckpointEvent,
)

_REPOSITORY_ROOT = Path(__file__).parents[1]
_GENERATOR_PATH = _REPOSITORY_ROOT / "scripts" / "generate_live_choreography_fixture.py"
_FIXTURE_PATH = (
    _REPOSITORY_ROOT
    / "web"
    / "src"
    / "features"
    / "live-scene"
    / "fixtures"
    / "completing-the-square.v1.json"
)
_FIXTURE_PROMPT = "Solve x² + 6x = 7 visually by completing the square."

_CHECKPOINT_IDS = [
    "problem",
    "area_model",
    "split_linear_term",
    "rearrange_halves",
    "missing_corner",
    "balance_and_complete",
    "factor_square",
    "solve_roots",
]


def test_fixture_is_exact_backend_generated_choreography_transcript(tmp_path: Path) -> None:
    regenerated = tmp_path / "completing-the-square.v1.json"
    subprocess.run(
        [sys.executable, str(_GENERATOR_PATH), "--output", str(regenerated)],
        cwd=_REPOSITORY_ROOT,
        check=True,
    )
    expected_bytes = _FIXTURE_PATH.read_bytes()
    assert regenerated.read_bytes() == expected_bytes

    fixture = json.loads(expected_bytes)
    assert set(fixture) == {
        "v",
        "fixtureId",
        "compilerVersion",
        "generation",
        "attempt",
        "baseRevision",
        "resultRevision",
        "transcript",
        "events",
    }
    assert fixture["v"] == 1
    assert fixture["fixtureId"] == "completing-the-square"
    assert fixture["compilerVersion"] == CHECKPOINT_COMPILER_VERSION
    assert fixture["generation"] == 1
    assert fixture["attempt"] == 1
    assert fixture["baseRevision"] == 0
    assert fixture["resultRevision"] == 8

    transcript = fixture["transcript"]
    assert set(transcript) == {
        "prompt",
        "routeDecision",
        "routedBeat",
        "checkpointIds",
        "checkpointCount",
        "authoredDurationMs",
        "providerRequestCount",
    }
    assert transcript["prompt"] == _FIXTURE_PROMPT
    assert transcript["routeDecision"] == {
        "v": 1,
        "decision": "start_choreography",
        "componentKind": "completing_square",
        "targetStage": "solve",
    }
    assert transcript["routedBeat"] == {
        "v": 2,
        "beatId": "route-1",
        "componentKind": "completing_square",
        "componentId": "square-lesson",
        "route": {"intent": "advance", "targetStage": "solve"},
    }
    assert transcript["checkpointIds"] == _CHECKPOINT_IDS
    assert transcript["checkpointCount"] == 8
    assert transcript["authoredDurationMs"] == 63_800
    assert transcript["providerRequestCount"] == 0

    parsed = [
        CHOREOGRAPHY_SCENE_STREAM_EVENT_ADAPTER.validate_python(event)
        for event in fixture["events"]
    ]
    assert [event.type for event in parsed] == [
        "scene_stream_started",
        *(["choreography_scene_checkpoint"] * 8),
        "scene_stream_completed",
    ]
    checkpoints = [event for event in parsed if isinstance(event, ChoreographySceneCheckpointEvent)]
    assert [event.sequence for event in checkpoints] == list(range(1, 9))
    assert [event.semantic.checkpoint_id.value for event in checkpoints] == _CHECKPOINT_IDS
    assert [(event.base_revision, event.result_revision) for event in checkpoints] == [
        (revision, revision + 1) for revision in range(8)
    ]
    assert [event.semantic.semantic_base_revision for event in checkpoints] == list(range(8))
    assert [event.semantic.semantic_result_revision for event in checkpoints] == list(range(1, 9))
    assert [
        event.semantic.certificate.body.previous_certificate_sha256 for event in checkpoints
    ] == [
        None,
        *[event.semantic.certificate.certificate_sha256 for event in checkpoints[:-1]],
    ]
    assert [
        event.semantic.result_component.last_main_checkpoint.value for event in checkpoints
    ] == (_CHECKPOINT_IDS)

    completed = parsed[-1]
    assert completed.model_dump(mode="json", by_alias=True) == {
        "type": "scene_stream_completed",
        "generation": 1,
        "finalRevision": 8,
        "patchCount": 8,
        "firstPatchMs": 0.0,
        "totalMs": 0.0,
        "repaired": False,
    }
