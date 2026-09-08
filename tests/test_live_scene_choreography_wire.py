from __future__ import annotations

import json

import pytest
from murmur.live_scene.choreography_service_contracts import (
    ChoreographySceneStreamDeclinedEvent,
)
from murmur.live_scene.choreography_wire import (
    MAX_CHOREOGRAPHY_SSE_EVENT_BYTES,
    encode_choreography_scene_stream_event,
)
from murmur.live_scene.contracts import MAX_NDJSON_FRAME_BYTES
from murmur.live_scene.wire import MAX_SSE_EVENT_BYTES, SceneStreamWireError
from pydantic import ValidationError


def _declined(
    message: str = "The corner detail is already visible — nothing changed ✓.",
) -> ChoreographySceneStreamDeclinedEvent:
    return ChoreographySceneStreamDeclinedEvent(
        generation=4,
        attempt=1,
        final_revision=5,
        reason_code="no_forward_progress",
        message=message,
    )


def test_choreography_encoder_is_compact_unicode_and_uses_its_own_64k_limit() -> None:
    event = _declined()
    wire = encode_choreography_scene_stream_event(event)
    payload = json.loads(wire.removeprefix("data: ").strip())

    assert payload == event.model_dump(mode="json", by_alias=True)
    assert wire.startswith('data: {"type":"choreography_scene_stream_declined"')
    assert wire.endswith("\n\n")
    assert "✓" in wire
    assert "\\u2713" not in wire
    assert MAX_CHOREOGRAPHY_SSE_EVENT_BYTES == MAX_NDJSON_FRAME_BYTES == 64 * 1024
    assert MAX_SSE_EVENT_BYTES == 96 * 1024


def test_choreography_encoder_enforces_exact_utf8_byte_budget() -> None:
    event = _declined("Explain x² using equal tiles ✓.")
    wire = encode_choreography_scene_stream_event(event)
    byte_length = len(wire.encode("utf-8"))

    assert byte_length > len(wire)
    assert (
        encode_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length,
        )
        == wire
    )
    with pytest.raises(SceneStreamWireError, match="browser wire budget"):
        encode_choreography_scene_stream_event(
            event,
            max_event_bytes=byte_length - 1,
        )


@pytest.mark.parametrize("budget", [0, -1, MAX_CHOREOGRAPHY_SSE_EVENT_BYTES + 1])
def test_choreography_encoder_rejects_invalid_budget_overrides(budget: int) -> None:
    with pytest.raises(ValueError, match="between 1"):
        encode_choreography_scene_stream_event(_declined(), max_event_bytes=budget)


def test_choreography_encoder_revalidates_the_discriminated_union() -> None:
    unknown = {
        "type": "scene_patch",
        "generation": 4,
        "attempt": 1,
        "sequence": 1,
        "baseRevision": 0,
        "resultRevision": 1,
        "patch": {},
    }

    with pytest.raises(ValidationError):
        encode_choreography_scene_stream_event(unknown)  # type: ignore[arg-type]
