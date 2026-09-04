"""Bounded model-stream orchestration for ephemeral live scenes."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from pydantic import ValidationError

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.contracts import (
    MAX_ACCEPTED_PATCHES,
    MAX_SAFE_SEQUENCE,
    MAX_SCENE_MODEL_OUTPUT_TOKENS,
    LiveSceneRequest,
    PutSceneOperation,
    ScenePatchDraft,
    ScenePatchEvent,
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
    SceneStreamStartedEvent,
)
from murmur.live_scene.prompt import build_scene_messages, scene_patch_target
from murmur.live_scene.stream_parser import ScenePatchStreamError, ScenePatchStreamParser
from murmur.live_scene.wire import SceneStreamWireError, encode_scene_stream_event

_REPAIR_MESSAGE = "The first visual draft needed correction. The last board is safe while I retry."
_INVALID_STREAM_MESSAGE = (
    "I couldn't finish a valid visual. The last board is safe; please try again."
)
_PROVIDER_ERROR_MESSAGE = (
    "The visual generator is temporarily unavailable. The last board is safe; please try again."
)
_PROVIDER_TIMEOUT_MESSAGE = (
    "The visual generator took too long. The last board is safe; please try again."
)
_CONTEXT_LIMIT_MESSAGE = (
    "This board is too large for another model pass. The current board remains safe."
)
_REVISION_LIMIT_MESSAGE = (
    "This board has reached its revision limit. The current board remains safe."
)


class SceneModelClient(Protocol):
    """Small provider surface required by scene authoring."""

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str | bytes]: ...


SceneModelClientFactory = Callable[[], SceneModelClient]
SceneClock = Callable[[], float]


class _ScenePatchApplicationError(ValueError):
    """Safe internal reason for asking the model to repair its output."""


def _scene_json(scene: SceneState) -> str:
    return json.dumps(
        scene.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _split_after_newlines(chunk: str | bytes) -> tuple[str | bytes, ...]:
    """Split provider chunks after LF so earlier valid frames survive a later bad frame."""

    if isinstance(chunk, bytes):
        byte_parts = chunk.split(b"\n")
        byte_segments = [part + b"\n" for part in byte_parts[:-1]]
        if byte_parts[-1] or not byte_segments:
            byte_segments.append(byte_parts[-1])
        return tuple(byte_segments)

    text_parts = chunk.split("\n")
    text_segments = [part + "\n" for part in text_parts[:-1]]
    if text_parts[-1] or not text_segments:
        text_segments.append(text_parts[-1])
    return tuple(text_segments)


def _feed_chunk(
    parser: ScenePatchStreamParser,
    chunk: str | bytes,
) -> Iterable[ScenePatchDraft]:
    for segment in _split_after_newlines(chunk):
        yield from parser.feed(segment)


def _apply_patch(scene: SceneState, patch: ScenePatchDraft) -> SceneState:
    """Apply one already-validated draft atomically while preserving semantic node order."""

    node_order = [node.id for node in scene.nodes]
    nodes_by_id = {node.id: node for node in scene.nodes}

    for operation in patch.operations:
        if isinstance(operation, PutSceneOperation):
            node_id = operation.node.id
            if node_id not in nodes_by_id:
                node_order.append(node_id)
            nodes_by_id[node_id] = operation.node
            continue

        node_id = operation.id
        if node_id not in nodes_by_id:
            raise _ScenePatchApplicationError("remove operation targeted an absent node")
        del nodes_by_id[node_id]
        node_order.remove(node_id)

    try:
        candidate = SceneState(
            revision=scene.revision + 1,
            nodes=tuple(nodes_by_id[node_id] for node_id in node_order),
        )
    except ValidationError as exc:
        raise _ScenePatchApplicationError("patch produced an invalid scene") from exc

    if candidate.nodes == scene.nodes:
        raise _ScenePatchApplicationError("patch made no scene change")
    return candidate


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return max(0.0, (finished_at - started_at) * 1_000.0)


@dataclass
class _GenerationState:
    generation: int
    scene: SceneState
    patch_limit: int
    started_at: float
    clock: SceneClock
    accepted_patch_ids: set[str] = field(default_factory=set)
    patch_count: int = 0
    first_patch_ms: float | None = None

    @property
    def remaining_patch_budget(self) -> int:
        return self.patch_limit - self.patch_count

    @property
    def budget_reached(self) -> bool:
        return self.patch_count >= self.patch_limit

    def accept(self, patch: ScenePatchDraft, *, attempt: int) -> ScenePatchEvent:
        if patch.patch_id in self.accepted_patch_ids:
            raise _ScenePatchApplicationError("patchId duplicated an accepted patch")

        base_revision = self.scene.revision
        next_scene = _apply_patch(self.scene, patch)
        next_sequence = self.patch_count + 1
        event = ScenePatchEvent(
            generation=self.generation,
            attempt=attempt,
            sequence=next_sequence,
            base_revision=base_revision,
            result_revision=next_scene.revision,
            patch=patch,
        )
        try:
            encode_scene_stream_event(event)
        except SceneStreamWireError as exc:
            raise _ScenePatchApplicationError("patch exceeded the browser wire budget") from exc
        self.scene = next_scene
        self.accepted_patch_ids.add(patch.patch_id)
        self.patch_count = next_sequence
        if self.first_patch_ms is None:
            self.first_patch_ms = _elapsed_ms(self.started_at, self.clock())
        return event

    def completed_event(self, *, repaired: bool) -> SceneStreamCompletedEvent:
        assert self.first_patch_ms is not None
        total_ms = max(self.first_patch_ms, _elapsed_ms(self.started_at, self.clock()))
        return SceneStreamCompletedEvent(
            generation=self.generation,
            final_revision=self.scene.revision,
            patch_count=self.patch_count,
            first_patch_ms=self.first_patch_ms,
            total_ms=total_ms,
            repaired=repaired,
        )


@dataclass
class _AttemptOutcome:
    patch_count: int = 0
    invalid_reason: str | None = None
    provider_failure_code: str | None = None


async def _next_before_deadline(
    stream: AsyncIterator[str | bytes],
    *,
    deadline: float,
) -> str | bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(anext(stream), timeout=remaining)


async def _close_upstream(stream: object | None, *, timeout_seconds: float) -> None:
    await close_async_resource(stream, timeout_seconds=timeout_seconds)


class SceneAuthoringService:
    """Stream server-authoritative scene events from one injected text model client."""

    def __init__(
        self,
        client: SceneModelClient | None = None,
        *,
        client_factory: SceneModelClientFactory | None = None,
        clock: SceneClock = time.perf_counter,
        temperature: float = 0.2,
        max_tokens: int = 4_096,
        timeout_seconds: float = 20.0,
    ) -> None:
        if (client is None) == (client_factory is None):
            raise ValueError("provide exactly one of client or client_factory")
        if client_factory is not None and not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or not math.isfinite(temperature)
            or temperature < 0
            or temperature > 2
        ):
            raise ValueError("temperature must be finite and between 0 and 2")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= MAX_SCENE_MODEL_OUTPUT_TOKENS
        ):
            raise ValueError(f"max_tokens must be between 1 and {MAX_SCENE_MODEL_OUTPUT_TOKENS}")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")

        self._client = client
        self._client_factory = client_factory
        self._clock = clock
        self._temperature = float(temperature)
        self._max_tokens = max_tokens
        self._timeout_seconds = float(timeout_seconds)
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    def _resolve_client(self) -> SceneModelClient:
        if self._client is not None:
            return self._client
        assert self._client_factory is not None
        return self._client_factory()

    async def _stream_attempt(
        self,
        *,
        client: SceneModelClient,
        state: _GenerationState,
        attempt: int,
        patch_target: int,
        messages: list[dict[str, str]],
        outcome: _AttemptOutcome,
    ) -> AsyncIterator[ScenePatchEvent]:
        parser = ScenePatchStreamParser()
        upstream: object | None = None

        try:
            upstream = client.stream(
                messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            if not hasattr(upstream, "__anext__"):
                raise TypeError("provider did not return an async iterator")
            typed_upstream = cast(AsyncIterator[str | bytes], upstream)
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds

            while not state.budget_reached and outcome.patch_count < patch_target:
                try:
                    chunk = await _next_before_deadline(typed_upstream, deadline=deadline)
                except StopAsyncIteration:
                    break

                for patch in _feed_chunk(parser, chunk):
                    event = state.accept(patch, attempt=attempt)
                    outcome.patch_count += 1
                    yield event
                    if state.budget_reached or outcome.patch_count >= patch_target:
                        break

            if not state.budget_reached and outcome.patch_count < patch_target:
                for patch in parser.finish():
                    event = state.accept(patch, attempt=attempt)
                    outcome.patch_count += 1
                    yield event
                    if state.budget_reached or outcome.patch_count >= patch_target:
                        break
        except ScenePatchStreamError as exc:
            outcome.invalid_reason = exc.repair_hint
        except _ScenePatchApplicationError as exc:
            outcome.invalid_reason = str(exc)
        except TimeoutError:
            outcome.provider_failure_code = "provider_timeout"
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome.provider_failure_code = "provider_error"
        finally:
            if not parser.closed:
                parser.abort()
            await _close_upstream(
                upstream,
                timeout_seconds=self._cleanup_timeout_seconds,
            )

    async def stream_events(self, request: LiveSceneRequest) -> AsyncIterator[SceneStreamEvent]:
        """Yield one bounded generation, including at most one model repair attempt."""

        if not isinstance(request, LiveSceneRequest):
            raise TypeError("request must be a LiveSceneRequest")

        started_at = self._clock()
        repair_reason: str | None = None
        patch_limit = min(
            MAX_ACCEPTED_PATCHES,
            MAX_SAFE_SEQUENCE - request.base_scene.revision,
        )
        state = _GenerationState(
            generation=request.generation,
            scene=request.base_scene,
            patch_limit=patch_limit,
            started_at=started_at,
            clock=self._clock,
        )

        yield SceneStreamStartedEvent(
            generation=request.generation,
            attempt=1,
            base_revision=state.scene.revision,
        )

        if patch_limit <= 0:
            yield SceneStreamFailedEvent(
                generation=request.generation,
                attempt=1,
                code="revision_limit",
                message=_REVISION_LIMIT_MESSAGE,
                last_accepted_revision=state.scene.revision,
                retryable=False,
            )
            return

        client: SceneModelClient | None = None
        owns_client = self._client is None
        try:
            try:
                client = self._resolve_client()
            except Exception:
                yield SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=1,
                    code="provider_error",
                    message=_PROVIDER_ERROR_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                return

            for attempt in (1, 2):
                current_scene_json = _scene_json(state.scene)
                patch_target = scene_patch_target(
                    state.remaining_patch_budget,
                    repair=attempt == 2,
                )
                repair_context: dict[str, str] | None = None
                if attempt == 2:
                    assert repair_reason is not None
                    repair_context = {
                        "error": repair_reason,
                        "last_accepted_scene_json": current_scene_json,
                    }

                try:
                    messages = build_scene_messages(
                        request.prompt,
                        current_scene_json,
                        state.remaining_patch_budget,
                        repair_context=repair_context,
                    )
                except (TypeError, ValueError):
                    yield SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code="context_too_large",
                        message=_CONTEXT_LIMIT_MESSAGE,
                        last_accepted_revision=state.scene.revision,
                        retryable=False,
                    )
                    return

                outcome = _AttemptOutcome()
                attempt_stream = self._stream_attempt(
                    client=client,
                    state=state,
                    attempt=attempt,
                    patch_target=patch_target,
                    messages=messages,
                    outcome=outcome,
                )
                try:
                    async for event in attempt_stream:
                        yield event
                finally:
                    await attempt_stream.aclose()

                if outcome.provider_failure_code is not None:
                    message = (
                        _PROVIDER_TIMEOUT_MESSAGE
                        if outcome.provider_failure_code == "provider_timeout"
                        else _PROVIDER_ERROR_MESSAGE
                    )
                    yield SceneStreamFailedEvent(
                        generation=request.generation,
                        attempt=attempt,
                        code=outcome.provider_failure_code,
                        message=message,
                        last_accepted_revision=state.scene.revision,
                        retryable=True,
                    )
                    return

                if state.budget_reached or (
                    outcome.invalid_reason is None and outcome.patch_count > 0
                ):
                    yield state.completed_event(repaired=attempt == 2)
                    return

                repair_reason = outcome.invalid_reason or "model stream ended without a patch"
                if attempt == 1:
                    yield SceneStreamRepairingEvent(
                        generation=request.generation,
                        from_attempt=1,
                        to_attempt=2,
                        last_accepted_revision=state.scene.revision,
                        message=_REPAIR_MESSAGE,
                    )
                    continue

                yield SceneStreamFailedEvent(
                    generation=request.generation,
                    attempt=2,
                    code="invalid_scene_stream",
                    message=_INVALID_STREAM_MESSAGE,
                    last_accepted_revision=state.scene.revision,
                    retryable=True,
                )
                return
        finally:
            if owns_client:
                await _close_upstream(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )


__all__ = [
    "SceneAuthoringService",
    "SceneClock",
    "SceneModelClient",
    "SceneModelClientFactory",
]
