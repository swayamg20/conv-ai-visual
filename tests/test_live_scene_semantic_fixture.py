"""Golden browser fixture generated from the semantic compiler contracts."""

from __future__ import annotations

import json
from pathlib import Path

from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    SemanticSceneState,
    TeachingBeatDraft,
)
from murmur.live_scene.semantic_service_contracts import (
    SEMANTIC_SCENE_STREAM_EVENT_ADAPTER,
    SemanticAtomMetadata,
    SemanticScenePatchEvent,
    dump_semantic_scene_stream_event,
)

_FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "web"
    / "src"
    / "features"
    / "live-scene"
    / "fixtures"
    / "pythagorean-area-identity.v1.json"
)


def _build_fixture() -> dict[str, object]:
    identity_beat = TeachingBeatDraft.model_validate(
        {
            "v": 1,
            "beatId": "beat-identity",
            "narration": "Relate the three square areas.",
            "act": "derive",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": "areas",
                "revealThrough": "identity",
            },
        }
    )
    identity = compile_teaching_beat(identity_beat, SemanticSceneState(revision=0))
    proof_beat = TeachingBeatDraft.model_validate(
        {
            "v": 1,
            "beatId": "beat-proof",
            "narration": (
                "Project the altitude through the hypotenuse square to prove its two regions "
                "have areas a² and b²."
            ),
            "act": "derive",
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": "areas",
                "revealThrough": "proof",
            },
        }
    )
    proof = compile_teaching_beat(proof_beat, identity.result_scene)

    events: list[dict[str, object]] = []
    for generation, compiled in ((1, identity), (2, proof)):
        for sequence, atom in enumerate(compiled.atoms, start=1):
            certificate = atom.certificate
            assert certificate is not None
            event = SemanticScenePatchEvent(
                generation=generation,
                attempt=1,
                sequence=sequence,
                base_revision=certificate.body.base_semantic_revision,
                result_revision=certificate.body.result_semantic_revision,
                patch=atom.patch,
                semantic=SemanticAtomMetadata(
                    beat=compiled.beat,
                    atom_id=atom.atom_id,
                    component_id=atom.component_id,
                    role=atom.role,
                    atom_ordinal=certificate.body.atom_ordinal,
                    semantic_base_revision=certificate.body.base_semantic_revision,
                    semantic_result_revision=certificate.body.result_semantic_revision,
                    receipt=atom.receipt,
                    certificate=certificate,
                ),
            )
            events.append(dump_semantic_scene_stream_event(event))

    return {
        "v": 1,
        "fixtureId": "pythagorean-area-identity",
        "componentId": "areas",
        "generation": 1,
        "baseRevision": 0,
        "events": events,
    }


def _canonical_fixture_bytes(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()


def test_fixture_is_exact_backend_generated_semantic_transcript() -> None:
    expected = _build_fixture()
    assert _FIXTURE_PATH.read_bytes() == _canonical_fixture_bytes(expected)

    fixture = json.loads(_FIXTURE_PATH.read_text())
    assert set(fixture) == {
        "v",
        "fixtureId",
        "componentId",
        "generation",
        "baseRevision",
        "events",
    }
    assert fixture["v"] == 1
    assert fixture["fixtureId"] == "pythagorean-area-identity"
    assert fixture["componentId"] == "areas"
    assert fixture["generation"] == 1
    assert fixture["baseRevision"] == 0

    events = fixture["events"]
    assert isinstance(events, list)
    parsed = [SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(event) for event in events]
    assert len(parsed) == 16
    assert [event.type for event in parsed] == ["semantic_scene_patch"] * 16
    assert [event.generation for event in parsed] == [1] * 8 + [2] * 8
    assert [event.sequence for event in parsed] == list(range(1, 9)) * 2
    assert [(event.base_revision, event.result_revision) for event in parsed] == [
        (index, index + 1) for index in range(16)
    ]
    assert [event.semantic.role for event in parsed] == list(PYTHAGOREAN_ROLE_ORDER)
