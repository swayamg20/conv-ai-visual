"""Focused tests for atomic semantic live-scene service orchestration."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest
from murmur.live_scene import service as service_module
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import (
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_NODES,
    ScenePatchDraft,
    ScenePatchEvent,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.semantic_compiler import compile_teaching_beat
from murmur.live_scene.semantic_contracts import (
    PYTHAGOREAN_ROLE_ORDER,
    CompilerCertificateBodyV1,
    PythagoreanAreaIdentityState,
    PythagoreanStage,
    SemanticSceneState,
    TeachingBeatDraft,
    compiler_certificate_sha256,
    scene_patch_sha256,
    teaching_beat_sha256,
)
from murmur.live_scene.semantic_service_contracts import (
    SEMANTIC_SCENE_STREAM_EVENT_ADAPTER,
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
)
from murmur.live_scene.semantic_verifier import SemanticVerificationError
from murmur.live_scene.semantic_wire import encode_semantic_scene_stream_event
from murmur.live_scene.wire import MAX_SSE_EVENT_BYTES, SceneStreamWireError
from pydantic import ValidationError

_BLOCK = object()


def _beat_line(
    stage: PythagoreanStage | str = PythagoreanStage.IDENTITY,
    *,
    beat_id: str = "beat-identity",
    component_id: str = "areas",
    act: str = "derive",
) -> str:
    return json.dumps(
        {
            "v": 1,
            "beatId": beat_id,
            "narration": "Relate the three square areas.",
            "act": act,
            "directive": {
                "kind": "pythagorean_area_identity",
                "id": component_id,
                "revealThrough": str(stage),
            },
        },
        separators=(",", ":"),
    )


def _line(node_id: str) -> dict[str, object]:
    return {
        "id": node_id,
        "kind": "line",
        "presentation": {"enter": "draw", "exit": "fade"},
        "points": [[10, 20], [200, 220]],
        "style": {
            "stroke": "hsl(var(--lavender))",
            "strokeWidth": 3,
            "opacity": 1,
            "roughness": 0.5,
        },
    }


def _request(
    *,
    scene: SceneState | None = None,
    semantic_scene: SemanticSceneState | None = None,
    generation: int = 17,
) -> SemanticLiveSceneRequest:
    return SemanticLiveSceneRequest(
        prompt="Teach this progressively with a verified diagram.",
        generation=generation,
        base_scene=scene or SceneState(revision=0),
        base_semantic_scene=semantic_scene or SemanticSceneState(revision=0),
    )


def _materialized_prefix(
    stage: PythagoreanStage,
    *,
    component_id: str = "areas",
) -> tuple[SceneState, SemanticSceneState]:
    beat = TeachingBeatDraft.model_validate_json(_beat_line(stage, component_id=component_id))
    compiled = compile_teaching_beat(beat, SemanticSceneState(revision=0))
    scene = SceneState(revision=0)
    for atom in compiled.atoms:
        scene = service_module._apply_patch(scene, atom.patch)
    return scene, compiled.result_scene


class _FakeClock:
    def __init__(self, *values: float) -> None:
        self._values = values
        self.calls = 0

    def __call__(self) -> float:
        index = min(self.calls, len(self._values) - 1)
        self.calls += 1
        return self._values[index]


class _TrackedStream:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)
        self.closed = False
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> _TrackedStream:
        return self

    async def __anext__(self) -> str | bytes:
        if self.closed or not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if item is _BLOCK:
            self.waiting.set()
            await self.release.wait()
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        await asyncio.sleep(0)
        assert isinstance(item, str | bytes)
        return item

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


class _FakeClient:
    def __init__(self, attempts: list[list[object]]) -> None:
        self._attempts = attempts
        self.calls: list[dict[str, object]] = []
        self.streams: list[_TrackedStream] = []
        self.stream_created = asyncio.Event()
        self.close_calls = 0

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> _TrackedStream:
        stream = _TrackedStream(self._attempts[len(self.calls)])
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        self.streams.append(stream)
        self.stream_created.set()
        return stream

    async def aclose(self) -> None:
        self.close_calls += 1


async def _collect(
    service: service_module.SceneAuthoringService,
    request: SemanticLiveSceneRequest | None = None,
) -> list[object]:
    return [event async for event in service.stream_semantic_events(request or _request())]


def _semantic_patches(events: list[object]) -> list[SemanticScenePatchEvent]:
    return [event for event in events if isinstance(event, SemanticScenePatchEvent)]


def _repair_semantic_snapshot(client: _FakeClient) -> dict[str, object]:
    messages = client.calls[1]["messages"]
    assert isinstance(messages, list)
    user = messages[1]["content"]
    snapshot = user.split("LAST_ACCEPTED_SEMANTIC_SCENE_JSON:\n", 1)[1].split(
        "\nOUTPUT_ONE_TEACHING_BEAT_NDJSON_NOW:",
        1,
    )[0]
    return json.loads(snapshot)


@pytest.mark.asyncio
async def test_streams_all_eight_atoms_with_typed_certificate_metadata() -> None:
    beat = _beat_line()
    client = _FakeClient([[beat + "\nnot-a-second-beat", _BLOCK]])
    clock = _FakeClock(10.0, 10.05, 10.30)
    service = service_module.SceneAuthoringService(client, clock=clock)

    events = await _collect(service)

    assert [event.type for event in events] == [
        "scene_stream_started",
        *("semantic_scene_patch" for _ in range(8)),
        "scene_stream_completed",
    ]
    patches = _semantic_patches(events)
    assert len(patches) == 8
    assert [event.sequence for event in patches] == list(range(1, 9))
    assert [(event.base_revision, event.result_revision) for event in patches] == [
        (index, index + 1) for index in range(8)
    ]
    assert [event.semantic.role for event in patches] == list(PYTHAGOREAN_ROLE_ORDER[:8])
    assert [event.semantic.atom_ordinal for event in patches] == list(range(1, 9))

    previous_certificate = None
    for event in patches:
        metadata = event.semantic
        certificate = metadata.certificate
        assert metadata.receipt.issuer == "semantic_verifier"
        assert certificate.body.previous_certificate_sha256 == previous_certificate
        assert certificate.body.patch_sha256
        assert certificate.body.receipt_sha256
        assert certificate.body.beat_sha256
        previous_certificate = certificate.certificate_sha256

    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.patch_count == 8
    assert completed.final_revision == 8
    assert completed.first_patch_ms == pytest.approx(50.0)
    assert completed.total_ms == pytest.approx(300.0)
    assert completed.repaired is False
    assert "REMAINING_ATOM_BUDGET:8" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert "TARGET_BEAT_COUNT:1" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert client.streams[0].closed is True
    assert client.streams[0].waiting.is_set() is False


@pytest.mark.asyncio
async def test_semantic_wire_round_trip_is_strict_nested_and_bounded() -> None:
    client = _FakeClient([[_beat_line(PythagoreanStage.TRIANGLE)]])
    event = _semantic_patches(await _collect(service_module.SceneAuthoringService(client)))[0]

    wire = encode_semantic_scene_stream_event(event)
    payload = json.loads(wire.removeprefix("data: ").strip())
    reparsed = SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)

    assert isinstance(reparsed, SemanticScenePatchEvent)
    assert reparsed == event
    assert len(wire.encode("utf-8")) <= MAX_SSE_EVENT_BYTES
    assert set(payload["semantic"]) == {
        "beat",
        "atomId",
        "componentId",
        "role",
        "atomOrdinal",
        "semanticBaseRevision",
        "semanticResultRevision",
        "receipt",
        "certificate",
    }

    wrong_role = deepcopy(payload)
    wrong_role["semantic"]["role"] = "identity"
    with pytest.raises(ValidationError):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(wrong_role)

    extra_metadata = deepcopy(payload)
    extra_metadata["semantic"]["providerTrace"] = {"untyped": True}
    with pytest.raises(ValidationError):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(extra_metadata)

    with pytest.raises(SceneStreamWireError):
        encode_semantic_scene_stream_event(event, max_event_bytes=len(wire.encode("utf-8")) - 1)

    raw_event = ScenePatchEvent(
        generation=event.generation,
        attempt=event.attempt,
        sequence=event.sequence,
        base_revision=event.base_revision,
        result_revision=event.result_revision,
        patch=event.patch,
    )
    with pytest.raises(ValidationError):
        encode_semantic_scene_stream_event(raw_event)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("directive_field", "value", "message"),
    [
        ("id", "other-areas", "directive id"),
        ("revealThrough", "triangle", "revealThrough prefix"),
    ],
)
@pytest.mark.asyncio
async def test_rehashed_beat_directive_mutation_cannot_relabel_an_atom(
    directive_field: str,
    value: str,
    message: str,
) -> None:
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY)]])
    event = _semantic_patches(await _collect(service_module.SceneAuthoringService(client)))[-1]
    payload = event.model_dump(mode="json", by_alias=True)
    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    beat_payload = semantic["beat"]
    assert isinstance(beat_payload, dict)
    directive = beat_payload["directive"]
    assert isinstance(directive, dict)
    directive[directive_field] = value

    beat = TeachingBeatDraft.model_validate(beat_payload)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body_payload["beatSha256"] = teaching_beat_sha256(beat)
    body = CompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = compiler_certificate_sha256(body)

    with pytest.raises(ValidationError, match=message):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.asyncio
async def test_rehashed_patch_narration_cannot_diverge_from_the_teaching_beat() -> None:
    client = _FakeClient([[_beat_line(PythagoreanStage.TRIANGLE)]])
    event = _semantic_patches(await _collect(service_module.SceneAuthoringService(client)))[0]
    payload = event.model_dump(mode="json", by_alias=True)
    patch_payload = payload["patch"]
    assert isinstance(patch_payload, dict)
    patch_payload["narration"] = "A different narration that is independently valid."

    semantic = payload["semantic"]
    assert isinstance(semantic, dict)
    certificate = semantic["certificate"]
    assert isinstance(certificate, dict)
    body_payload = certificate["body"]
    assert isinstance(body_payload, dict)
    body_payload["patchSha256"] = scene_patch_sha256(ScenePatchDraft.model_validate(patch_payload))
    body = CompilerCertificateBodyV1.model_validate(body_payload)
    certificate["certificateSha256"] = compiler_certificate_sha256(body)

    with pytest.raises(ValidationError, match="patch narration"):
        SEMANTIC_SCENE_STREAM_EVENT_ADAPTER.validate_python(payload)


@pytest.mark.asyncio
async def test_provider_stream_closes_before_compilation_and_full_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([[_beat_line(PythagoreanStage.TRIANGLE)]])
    original_compile = service_module.compile_teaching_beat

    def assert_closed_before_compile(
        beat: TeachingBeatDraft,
        scene: SemanticSceneState,
    ) -> object:
        assert client.streams[0].closed is True
        return original_compile(beat, scene)

    monkeypatch.setattr(service_module, "compile_teaching_beat", assert_closed_before_compile)

    events = await _collect(service_module.SceneAuthoringService(client))

    assert len(_semantic_patches(events)) == 1
    assert isinstance(events[-1], SceneStreamCompletedEvent)


@pytest.mark.asyncio
async def test_insufficient_atom_capacity_repairs_without_leaking_the_rejected_batch() -> None:
    nodes = [_line(f"existing-{index}") for index in range(MAX_SCENE_NODES - 7)]
    scene = SceneState.model_validate({"revision": 0, "nodes": nodes})
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [
                _beat_line(
                    PythagoreanStage.TRIANGLE,
                    beat_id="beat-repair",
                )
            ],
        ]
    )
    service = service_module.SceneAuthoringService(client)

    events = await _collect(service, _request(scene=scene))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "semantic_scene_patch",
        "scene_stream_completed",
    ]
    patches = _semantic_patches(events)
    assert [(event.attempt, event.sequence) for event in patches] == [(2, 1)]
    assert [(event.base_revision, event.result_revision) for event in patches] == [(0, 1)]
    assert "REMAINING_ATOM_BUDGET:7" in client.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert "REMAINING_ATOM_BUDGET:7" in client.calls[1]["messages"][1]["content"]  # type: ignore[index]
    assert _repair_semantic_snapshot(client) == {"components": [], "revision": 0}


@pytest.mark.asyncio
async def test_insufficient_revision_budget_repairs_before_compilation_overflow() -> None:
    base_revision = MAX_SAFE_SEQUENCE - 7
    scene = SceneState(revision=base_revision)
    semantic_scene = SemanticSceneState(revision=base_revision)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    patches = _semantic_patches(events)
    assert [(event.attempt, event.base_revision, event.result_revision) for event in patches] == [
        (2, base_revision, base_revision + 1)
    ]
    assert isinstance(events[1], SceneStreamRepairingEvent)
    assert events[1].last_accepted_revision == base_revision
    assert _repair_semantic_snapshot(client)["revision"] == base_revision


@pytest.mark.parametrize("failure_index", [1, 4, 8])
@pytest.mark.asyncio
async def test_patch_failure_anywhere_fails_integrity_with_zero_emitted_atoms(
    monkeypatch: pytest.MonkeyPatch,
    failure_index: int,
) -> None:
    original_apply = service_module._apply_patch
    calls = 0

    def fail_once(scene: SceneState, patch: object) -> SceneState:
        nonlocal calls
        calls += 1
        if calls == failure_index:
            raise service_module._ScenePatchApplicationError("injected application failure")
        return original_apply(scene, patch)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "_apply_patch", fail_once)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.last_accepted_revision == 0
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_serialized_verifier_failure_is_nonretryable_with_zero_emitted_atoms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_verify = service_module.verify_pythagorean_realization
    calls = 0

    def fail_middle(component_id: str, nodes: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise SemanticVerificationError("injected verifier failure")
        return original_verify(component_id, nodes)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "verify_pythagorean_realization", fail_middle)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_last_atom_wire_failure_is_nonretryable_with_zero_emitted_atoms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_encode = service_module.encode_semantic_scene_stream_event
    patch_calls = 0

    def fail_last(event: object) -> str:
        nonlocal patch_calls
        if isinstance(event, SemanticScenePatchEvent):
            patch_calls += 1
            if patch_calls == 8:
                raise SceneStreamWireError("injected wire failure")
        return original_encode(event)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "encode_semantic_scene_stream_event", fail_last)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_missing_certificate_fails_integrity_before_first_atom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = service_module.compile_teaching_beat
    calls = 0

    def strip_first_certificate(beat: TeachingBeatDraft, scene: SemanticSceneState) -> object:
        nonlocal calls
        calls += 1
        compiled = original_compile(beat, scene)
        if calls != 1:
            return compiled
        first = compiled.atoms[0].model_copy(update={"certificate": None})
        return compiled.model_copy(update={"atoms": (first, *compiled.atoms[1:])})

    monkeypatch.setattr(service_module, "compile_teaching_beat", strip_first_certificate)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_truncated_compiler_suffix_fails_whole_beat_validation_without_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = service_module.compile_teaching_beat

    def truncate_identity(beat: TeachingBeatDraft, scene: SemanticSceneState) -> object:
        compiled = original_compile(beat, scene)
        first_atom = compiled.atoms[0]
        certificate = first_atom.certificate
        assert certificate is not None
        forged_result = SemanticSceneState(
            revision=scene.revision + 1,
            components=(
                PythagoreanAreaIdentityState(
                    id=beat.directive.id,
                    revealed_roles=PYTHAGOREAN_ROLE_ORDER[:1],
                ),
            ),
            certificate_head_sha256=certificate.certificate_sha256,
        )
        return compiled.model_copy(update={"atoms": (first_atom,), "result_scene": forged_result})

    monkeypatch.setattr(service_module, "compile_teaching_beat", truncate_identity)
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY)]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_compiler_cannot_substitute_a_different_valid_beat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = service_module.compile_teaching_beat

    def substitute_beat(_beat: TeachingBeatDraft, scene: SemanticSceneState) -> object:
        substitute = TeachingBeatDraft.model_validate_json(
            _beat_line(
                PythagoreanStage.TRIANGLE,
                beat_id="substitute-beat",
            )
        )
        return original_compile(substitute, scene)

    monkeypatch.setattr(service_module, "compile_teaching_beat", substitute_beat)
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY)]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("case", "body_updates"),
    [
        ("previous head", {"previous_certificate_sha256": "f" * 64}),
        ("base scene hash", {"base_scene_sha256": "f" * 64}),
        ("result scene hash", {"result_scene_sha256": "f" * 64}),
        (
            "adjacent revisions",
            {"base_semantic_revision": 5, "result_semantic_revision": 6},
        ),
    ],
)
@pytest.mark.asyncio
async def test_service_independently_rejects_rehashed_certificate_transition_faults(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    body_updates: dict[str, object],
) -> None:
    del case
    original_compile = service_module.compile_teaching_beat

    def tamper_transition(beat: TeachingBeatDraft, scene: SemanticSceneState) -> object:
        compiled = original_compile(beat, scene)
        atom = compiled.atoms[0]
        certificate = atom.certificate
        assert certificate is not None
        body = certificate.body.model_copy(update=body_updates)
        replacement_certificate = certificate.model_copy(
            update={
                "body": body,
                "certificate_sha256": compiler_certificate_sha256(body),
            }
        )
        replacement_atom = atom.model_copy(update={"certificate": replacement_certificate})
        return compiled.model_copy(update={"atoms": (replacement_atom, *compiled.atoms[1:])})

    monkeypatch.setattr(service_module, "compile_teaching_beat", tamper_transition)
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY)]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert _semantic_patches(events) == []
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_integrity_error"
    assert failure.last_accepted_revision == 0
    assert failure.retryable is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_invalid_semantic_prefix_fails_before_provider_invocation() -> None:
    scene = SceneState(revision=1)
    semantic_scene = SemanticSceneState(
        revision=1,
        components=(
            PythagoreanAreaIdentityState(
                id="areas",
                revealed_roles=PYTHAGOREAN_ROLE_ORDER[:1],
            ),
        ),
    )
    client = _FakeClient([])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_base_mismatch"
    assert failure.last_accepted_revision == 1
    assert failure.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_progressed_prefix_without_chain_head_fails_before_provider_invocation() -> None:
    scene, semantic_scene = _materialized_prefix(PythagoreanStage.TRIANGLE)
    headless_semantic_scene = semantic_scene.model_copy(update={"certificate_head_sha256": None})
    client = _FakeClient([])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=headless_semantic_scene),
    )

    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_base_mismatch"
    assert failure.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_empty_component_without_chain_head_can_start_its_first_certificate() -> None:
    semantic_scene = SemanticSceneState(
        revision=0,
        components=(PythagoreanAreaIdentityState(id="areas"),),
    )
    client = _FakeClient([[_beat_line(PythagoreanStage.TRIANGLE)]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(semantic_scene=semantic_scene),
    )

    patches = _semantic_patches(events)
    assert len(patches) == 1
    assert patches[0].semantic.certificate.body.previous_certificate_sha256 is None
    assert isinstance(events[-1], SceneStreamCompletedEvent)


@pytest.mark.asyncio
async def test_orphan_chain_head_without_revealed_roles_fails_before_provider_invocation() -> None:
    semantic_scene = SemanticSceneState(
        revision=0,
        components=(PythagoreanAreaIdentityState(id="areas"),),
        certificate_head_sha256="f" * 64,
    )
    client = _FakeClient([])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(semantic_scene=semantic_scene),
    )

    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "semantic_base_mismatch"
    assert failure.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_resume_from_verified_prefix_preserves_absolute_ordinal_and_chain() -> None:
    scene, semantic_scene = _materialized_prefix(PythagoreanStage.AREAS)
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY, beat_id="beat-finish")]])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    patches = _semantic_patches(events)
    assert len(patches) == 1
    event = patches[0]
    assert event.semantic.role.value == "identity"
    assert event.semantic.atom_ordinal == 8
    assert event.sequence == 1
    assert (event.base_revision, event.result_revision) == (7, 8)
    assert (
        event.semantic.certificate.body.previous_certificate_sha256
        == semantic_scene.certificate_head_sha256
    )
    completed = events[-1]
    assert isinstance(completed, SceneStreamCompletedEvent)
    assert completed.final_revision == 8


@pytest.mark.asyncio
async def test_missing_role_node_id_collision_repairs_from_unchanged_prefix() -> None:
    scene = SceneState.model_validate(
        {
            "revision": 0,
            "nodes": [_line("areas__square_b")],
        }
    )
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [_beat_line(PythagoreanStage.TRIANGLE, beat_id="beat-repair")],
        ]
    )

    events = await _collect(service_module.SceneAuthoringService(client), _request(scene=scene))

    patches = _semantic_patches(events)
    assert [(event.attempt, event.base_revision) for event in patches] == [(2, 0)]
    assert isinstance(events[1], SceneStreamRepairingEvent)
    assert _repair_semantic_snapshot(client) == {"components": [], "revision": 0}


@pytest.mark.asyncio
async def test_noop_beat_is_invalid_and_repairs_without_replaying_existing_atoms() -> None:
    scene, semantic_scene = _materialized_prefix(PythagoreanStage.IDENTITY)
    client = _FakeClient(
        [
            [_beat_line(PythagoreanStage.IDENTITY)],
            [
                _beat_line(
                    PythagoreanStage.TRIANGLE,
                    beat_id="beat-new",
                    component_id="next-areas",
                )
            ],
        ]
    )

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    patches = _semantic_patches(events)
    assert [(event.attempt, event.sequence, event.base_revision) for event in patches] == [
        (2, 1, 8)
    ]
    assert patches[0].semantic.component_id == "next-areas"
    repairing = events[1]
    assert isinstance(repairing, SceneStreamRepairingEvent)
    assert repairing.last_accepted_revision == 8
    assert _repair_semantic_snapshot(client)["revision"] == 8


@pytest.mark.asyncio
async def test_revision_limit_fails_before_provider_invocation() -> None:
    scene = SceneState(revision=MAX_SAFE_SEQUENCE)
    semantic_scene = SemanticSceneState(revision=MAX_SAFE_SEQUENCE)
    client = _FakeClient([])

    events = await _collect(
        service_module.SceneAuthoringService(client),
        _request(scene=scene, semantic_scene=semantic_scene),
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "revision_limit"
    assert failure.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_provider_error_is_friendly_and_closes_the_exact_stream() -> None:
    secret = "provider leaked sk-secret"
    client = _FakeClient([[RuntimeError(secret)]])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.code == "provider_error"
    assert secret not in failure.model_dump_json(by_alias=True)
    assert client.streams[0].closed is True


@pytest.mark.asyncio
async def test_provider_dispatch_limit_is_classified_before_legacy_semantic_call() -> None:
    client = _FakeClient([[]])

    async def reject() -> None:
        raise SceneAdmissionError("provider_rate_limited", "private limiter state")

    events = await _collect(
        service_module.SceneAuthoringService(
            client,
            before_provider_dispatch=reject,
        )
    )

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert (failure.code, failure.attempt, failure.retryable) == (
        "provider_rate_limited",
        1,
        True,
    )
    assert client.calls == []


@pytest.mark.asyncio
async def test_invalid_stream_gets_exactly_one_repair_attempt() -> None:
    client = _FakeClient([[], []])

    events = await _collect(service_module.SceneAuthoringService(client))

    assert [event.type for event in events] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "scene_stream_failed",
    ]
    failure = events[-1]
    assert isinstance(failure, SceneStreamFailedEvent)
    assert failure.attempt == 2
    assert failure.code == "invalid_scene_stream"
    assert len(client.calls) == 2
    assert all(stream.closed for stream in client.streams)


@pytest.mark.asyncio
async def test_cancellation_while_awaiting_provider_closes_the_exact_stream() -> None:
    client = _FakeClient([[_BLOCK]])
    service = service_module.SceneAuthoringService(client, timeout_seconds=1.0)
    events = service.stream_semantic_events(_request())

    assert isinstance(await anext(events), SceneStreamStartedEvent)
    pending = asyncio.create_task(anext(events))
    await client.stream_created.wait()
    await client.streams[0].waiting.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert client.streams[0].closed is True
    await events.aclose()


@pytest.mark.asyncio
async def test_factory_owned_client_closes_once_when_consumer_stops_after_first_atom() -> None:
    client = _FakeClient([[_beat_line(PythagoreanStage.IDENTITY)]])
    factory_calls = 0

    def factory() -> _FakeClient:
        nonlocal factory_calls
        factory_calls += 1
        return client

    service = service_module.SceneAuthoringService(client_factory=factory)
    events = service.stream_semantic_events(_request())

    assert isinstance(await anext(events), SceneStreamStartedEvent)
    assert isinstance(await anext(events), SemanticScenePatchEvent)
    await events.aclose()

    assert factory_calls == 1
    assert client.streams[0].closed is True
    assert client.close_calls == 1


def test_semantic_request_is_strict_and_requires_lockstep_revisions() -> None:
    with pytest.raises(ValidationError, match="revisions must match"):
        SemanticLiveSceneRequest.model_validate(
            {
                "prompt": "Teach it.",
                "generation": 1,
                "baseScene": {"revision": 1, "nodes": []},
                "baseSemanticScene": {"revision": 0, "components": []},
            }
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SemanticLiveSceneRequest.model_validate(
            {
                "prompt": "Teach it.",
                "generation": 1,
                "baseScene": {"revision": 0, "nodes": []},
                "baseSemanticScene": {"revision": 0, "components": []},
                "metadata": {},
            }
        )
