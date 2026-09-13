"""Progressive, single-call orchestration for Gate 1.8 storyboards."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, cast

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import MAX_SAFE_SEQUENCE, MAX_SCENE_MODEL_OUTPUT_TOKENS, SceneState
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    compile_certified_semantic_storyboard_anchor,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    AbstainStoryboardRecordV1,
    AcceptedSemanticStoryboardRecordV1,
    ProjectileStoryboardSemanticSceneStateV1,
    StoryboardAbstainReasonCode,
    storyboard_has_forward_capacity,
)
from murmur.live_scene.semantic_storyboard_director import (
    SemanticStoryboardDirectorStreamError,
    SemanticStoryboardDirectorStreamErrorCode,
    SemanticStoryboardDirectorStreamParser,
    build_semantic_storyboard_director_messages,
)
from murmur.live_scene.semantic_storyboard_requests import (
    SemanticStoryboardDirectorRequestV1,
    SemanticStoryboardReflexRequestV1,
    SemanticStoryboardRequestV1,
)
from murmur.live_scene.semantic_storyboard_routing import (
    SemanticStoryboardRoutingError,
    SemanticStoryboardRoutingErrorCode,
    route_semantic_storyboard_record,
)
from murmur.live_scene.semantic_storyboard_service_contracts import (
    SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER,
    SemanticStoryboardAcceptedPrefixCause,
    SemanticStoryboardCompletionReason,
    SemanticStoryboardFailureCode,
    SemanticStoryboardSceneCheckpointEventV1,
    SemanticStoryboardSceneStreamCompletedEventV1,
    SemanticStoryboardSceneStreamDeclinedEventV1,
    SemanticStoryboardSceneStreamEventV1,
    SemanticStoryboardSceneStreamFailedEventV1,
    SemanticStoryboardSceneStreamStartedEventV1,
)
from murmur.live_scene.semantic_storyboard_verifier import (
    verify_semantic_storyboard_frontier,
)
from murmur.live_scene.semantic_storyboard_wire import (
    encode_semantic_storyboard_scene_stream_event,
)

DEFAULT_SEMANTIC_STORYBOARD_DIRECTOR_MAX_TOKENS = 2_048
DEFAULT_SEMANTIC_STORYBOARD_TIMEOUT_SECONDS = 20.0

SceneClock: TypeAlias = Callable[[], float]


class SemanticStoryboardDirectorClient(Protocol):
    """The one provider capability needed by the storyboard Director."""

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str | bytes]: ...


SemanticStoryboardDirectorClientFactory: TypeAlias = Callable[[], SemanticStoryboardDirectorClient]

_FAILURE_MESSAGES = {
    SemanticStoryboardFailureCode.SEMANTIC_BASE_MISMATCH: (
        "The storyboard no longer matches the visible board. Refresh before continuing."
    ),
    SemanticStoryboardFailureCode.STORYBOARD_CAPACITY_EXHAUSTED: (
        "This storyboard is complete. Start a new board to explore another explanation."
    ),
    SemanticStoryboardFailureCode.REVISION_LIMIT: (
        "This board has reached its revision limit. The accepted storyboard is unchanged."
    ),
    SemanticStoryboardFailureCode.CONTEXT_TOO_LARGE: (
        "This storyboard context is too large for another Director pass."
    ),
    SemanticStoryboardFailureCode.INVALID_MODEL_STREAM: (
        "The visual Director returned no usable storyboard step. Please try again."
    ),
    SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED: (
        "Visual Director capacity is busy. Please try again shortly."
    ),
    SemanticStoryboardFailureCode.PROVIDER_TIMEOUT: (
        "The visual Director took too long. Please try again."
    ),
    SemanticStoryboardFailureCode.PROVIDER_ERROR: (
        "The visual Director is temporarily unavailable. Please try again."
    ),
    SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR: (
        "The storyboard runtime rejected an unsafe result. The board is unchanged."
    ),
}
_DECLINE_MESSAGES = {
    StoryboardAbstainReasonCode.ALREADY_PRESENT: (
        "That idea is already visible in the accepted storyboard."
    ),
    StoryboardAbstainReasonCode.AMBIGUOUS_INTENT: (
        "I could not identify one safe visual step from that request."
    ),
    StoryboardAbstainReasonCode.NO_FORWARD_PROGRESS: (
        "That request does not add a new verified storyboard step."
    ),
    StoryboardAbstainReasonCode.UNSUPPORTED_INITIAL_CONDITION: (
        "That initial condition is not supported by this storyboard."
    ),
    StoryboardAbstainReasonCode.UNSUPPORTED_INTENT: (
        "That request is outside the supported storyboard vocabulary."
    ),
    StoryboardAbstainReasonCode.UNSUPPORTED_PHYSICS: (
        "That physics request is outside this verified storyboard."
    ),
    StoryboardAbstainReasonCode.UNSUPPORTED_PROBLEM: (
        "That problem is not supported by this storyboard."
    ),
}
_RETRYABLE_FAILURES = frozenset(
    {
        SemanticStoryboardFailureCode.INVALID_MODEL_STREAM,
        SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED,
        SemanticStoryboardFailureCode.PROVIDER_TIMEOUT,
        SemanticStoryboardFailureCode.PROVIDER_ERROR,
    }
)


class _StoryboardInvariantError(RuntimeError):
    """A fixed, provider-text-free internal trust-boundary failure."""


@dataclass(frozen=True, slots=True)
class _StopCause:
    failure: SemanticStoryboardFailureCode
    accepted_prefix: SemanticStoryboardAcceptedPrefixCause


@dataclass(frozen=True, slots=True)
class _AcceptedFrontier:
    scene: SceneState
    semantic_scene: ProjectileStoryboardSemanticSceneStateV1
    checkpoint_count: int = 0
    first_checkpoint_ms: float | None = None


_INVALID_STREAM = _StopCause(
    SemanticStoryboardFailureCode.INVALID_MODEL_STREAM,
    SemanticStoryboardAcceptedPrefixCause.INVALID_MODEL_STREAM,
)
_PROVIDER_RATE_LIMITED = _StopCause(
    SemanticStoryboardFailureCode.PROVIDER_RATE_LIMITED,
    SemanticStoryboardAcceptedPrefixCause.PROVIDER_RATE_LIMITED,
)
_PROVIDER_TIMEOUT = _StopCause(
    SemanticStoryboardFailureCode.PROVIDER_TIMEOUT,
    SemanticStoryboardAcceptedPrefixCause.PROVIDER_TIMEOUT,
)
_PROVIDER_ERROR = _StopCause(
    SemanticStoryboardFailureCode.PROVIDER_ERROR,
    SemanticStoryboardAcceptedPrefixCause.PROVIDER_ERROR,
)
_CAPACITY_LIMIT = _StopCause(
    SemanticStoryboardFailureCode.STORYBOARD_CAPACITY_EXHAUSTED,
    SemanticStoryboardAcceptedPrefixCause.CAPACITY_LIMIT,
)
_REVISION_LIMIT = _StopCause(
    SemanticStoryboardFailureCode.REVISION_LIMIT,
    SemanticStoryboardAcceptedPrefixCause.REVISION_LIMIT,
)
_INTERNAL_ERROR = _StopCause(
    SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR,
    SemanticStoryboardAcceptedPrefixCause.INTERNAL_INTEGRITY_ERROR,
)


def _parser_stop_cause(
    error: SemanticStoryboardDirectorStreamError,
) -> _StopCause:
    if error.code is SemanticStoryboardDirectorStreamErrorCode.RECORD_LIMIT_EXCEEDED:
        return _CAPACITY_LIMIT
    return _INVALID_STREAM


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return max(0.0, (finished_at - started_at) * 1_000.0)


def _roundtrip_event(
    event: SemanticStoryboardSceneStreamEventV1,
) -> SemanticStoryboardSceneStreamEventV1:
    """Require the exact browser wire representation before publication."""

    encoded = encode_semantic_storyboard_scene_stream_event(event)
    if not encoded.startswith("data: ") or not encoded.endswith("\n\n"):
        raise _StoryboardInvariantError("storyboard encoder returned an invalid SSE frame")
    decoded = SEMANTIC_STORYBOARD_SCENE_STREAM_EVENT_ADAPTER.validate_json(encoded[6:-2])
    if decoded != event:
        raise _StoryboardInvariantError("storyboard wire roundtrip changed an event")
    return decoded


def _started_event(
    request: SemanticStoryboardRequestV1,
) -> SemanticStoryboardSceneStreamStartedEventV1:
    return cast(
        SemanticStoryboardSceneStreamStartedEventV1,
        _roundtrip_event(
            SemanticStoryboardSceneStreamStartedEventV1(
                generation=request.generation,
                attempt=1,
                base_revision=request.base_scene.revision,
            )
        ),
    )


def _failed_event(
    request: SemanticStoryboardRequestV1,
    code: SemanticStoryboardFailureCode,
) -> SemanticStoryboardSceneStreamFailedEventV1:
    return cast(
        SemanticStoryboardSceneStreamFailedEventV1,
        _roundtrip_event(
            SemanticStoryboardSceneStreamFailedEventV1(
                generation=request.generation,
                attempt=1,
                base_revision=request.base_scene.revision,
                code=code,
                message=_FAILURE_MESSAGES[code],
                last_accepted_revision=request.base_scene.revision,
                retryable=code in _RETRYABLE_FAILURES,
            )
        ),
    )


def _declined_event(
    request: SemanticStoryboardDirectorRequestV1,
    reason: StoryboardAbstainReasonCode,
) -> SemanticStoryboardSceneStreamDeclinedEventV1:
    return cast(
        SemanticStoryboardSceneStreamDeclinedEventV1,
        _roundtrip_event(
            SemanticStoryboardSceneStreamDeclinedEventV1(
                generation=request.generation,
                attempt=1,
                base_revision=request.base_scene.revision,
                final_revision=request.base_scene.revision,
                reason_code=reason,
                message=_DECLINE_MESSAGES[reason],
            )
        ),
    )


def _completed_event(
    request: SemanticStoryboardRequestV1,
    frontier: _AcceptedFrontier,
    *,
    started_at: float,
    finished_at: float,
    reason: SemanticStoryboardCompletionReason,
    accepted_prefix_cause: SemanticStoryboardAcceptedPrefixCause | None = None,
) -> SemanticStoryboardSceneStreamCompletedEventV1:
    first_checkpoint_ms = frontier.first_checkpoint_ms
    if first_checkpoint_ms is None:
        raise _StoryboardInvariantError("completed storyboard omitted its first checkpoint time")
    return cast(
        SemanticStoryboardSceneStreamCompletedEventV1,
        _roundtrip_event(
            SemanticStoryboardSceneStreamCompletedEventV1(
                generation=request.generation,
                attempt=1,
                base_revision=request.base_scene.revision,
                final_revision=frontier.scene.revision,
                checkpoint_count=frontier.checkpoint_count,
                first_checkpoint_ms=first_checkpoint_ms,
                total_ms=max(first_checkpoint_ms, _elapsed_ms(started_at, finished_at)),
                reason_code=reason,
                accepted_prefix_cause=accepted_prefix_cause,
            )
        ),
    )


def _terminal_for_cause(
    request: SemanticStoryboardDirectorRequestV1,
    frontier: _AcceptedFrontier,
    cause: _StopCause,
    *,
    started_at: float,
    finished_at: float,
) -> SemanticStoryboardSceneStreamCompletedEventV1 | SemanticStoryboardSceneStreamFailedEventV1:
    if frontier.checkpoint_count:
        return _completed_event(
            request,
            frontier,
            started_at=started_at,
            finished_at=finished_at,
            reason=SemanticStoryboardCompletionReason.ACCEPTED_PREFIX,
            accepted_prefix_cause=cause.accepted_prefix,
        )
    return _failed_event(request, cause.failure)


def _prepare_checkpoint(
    request: SemanticStoryboardDirectorRequestV1,
    record: AcceptedSemanticStoryboardRecordV1,
    frontier: _AcceptedFrontier,
    *,
    started_at: float,
    clock: SceneClock,
) -> tuple[SemanticStoryboardSceneCheckpointEventV1, _AcceptedFrontier]:
    """Route, certify, and wire-admit exactly one record without partial state."""

    if frontier.scene.revision >= MAX_SAFE_SEQUENCE:
        raise _CandidateRejected(_REVISION_LIMIT)
    try:
        component = frontier.semantic_scene.components[0]
        if not storyboard_has_forward_capacity(request.problem_spec, component.accepted_records):
            raise _CandidateRejected(_CAPACITY_LIMIT)
        beat = route_semantic_storyboard_record(
            record,
            problem_spec=request.problem_spec,
            semantic_scene=frontier.semantic_scene,
        )
    except _CandidateRejected:
        raise
    except SemanticStoryboardRoutingError as exc:
        if exc.code is SemanticStoryboardRoutingErrorCode.LEDGER_CAPACITY_EXCEEDED:
            raise _CandidateRejected(_CAPACITY_LIMIT) from None
        raise _CandidateRejected(_INVALID_STREAM) from None
    except asyncio.CancelledError:
        raise
    except Exception:
        raise _CandidateRejected(_INTERNAL_ERROR) from None

    try:
        transition = compile_certified_semantic_storyboard_checkpoint(
            beat,
            base_scene=frontier.scene,
            base_semantic_scene=frontier.semantic_scene,
        )
        verify_semantic_storyboard_frontier(
            request.problem_spec,
            transition.result_scene,
            transition.result_semantic_scene,
        )
        event = SemanticStoryboardSceneCheckpointEventV1(
            generation=request.generation,
            attempt=1,
            sequence=frontier.checkpoint_count + 1,
            base_revision=frontier.scene.revision,
            result_revision=transition.result_scene.revision,
            patch=transition.checkpoint.patch,
            transition=transition,
        )
        admitted = cast(SemanticStoryboardSceneCheckpointEventV1, _roundtrip_event(event))
        accepted_at = _elapsed_ms(started_at, clock())
    except asyncio.CancelledError:
        raise
    except Exception:
        raise _CandidateRejected(_INTERNAL_ERROR) from None

    return admitted, _AcceptedFrontier(
        scene=admitted.transition.result_scene,
        semantic_scene=admitted.transition.result_semantic_scene,
        checkpoint_count=frontier.checkpoint_count + 1,
        first_checkpoint_ms=(
            accepted_at if frontier.first_checkpoint_ms is None else frontier.first_checkpoint_ms
        ),
    )


class _CandidateRejected(Exception):
    def __init__(self, cause: _StopCause) -> None:
        super().__init__(cause.failure.value)
        self.cause = cause


async def _next_before_deadline(
    stream: AsyncIterator[str | bytes],
    *,
    deadline: float,
) -> str | bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(anext(stream), timeout=remaining)


class SemanticStoryboardService:
    """Serve one provider-free anchor or one progressive Director generation."""

    def __init__(
        self,
        client: SemanticStoryboardDirectorClient | None = None,
        *,
        client_factory: SemanticStoryboardDirectorClientFactory | None = None,
        clock: SceneClock = time.perf_counter,
        max_tokens: int = DEFAULT_SEMANTIC_STORYBOARD_DIRECTOR_MAX_TOKENS,
        timeout_seconds: float = DEFAULT_SEMANTIC_STORYBOARD_TIMEOUT_SECONDS,
        before_provider_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if client is not None and client_factory is not None:
            raise ValueError("provide at most one of client or client_factory")
        if client_factory is not None and not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
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
        if before_provider_dispatch is not None and not callable(before_provider_dispatch):
            raise TypeError("before_provider_dispatch must be callable")
        self._client = client
        self._client_factory = client_factory
        self._clock = clock
        self._max_tokens = min(
            max_tokens,
            DEFAULT_SEMANTIC_STORYBOARD_DIRECTOR_MAX_TOKENS,
        )
        self._timeout_seconds = float(timeout_seconds)
        self._before_provider_dispatch = before_provider_dispatch
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    def _resolve_client(self) -> SemanticStoryboardDirectorClient:
        if self._client is not None:
            return self._client
        if self._client_factory is None:
            raise RuntimeError("no semantic storyboard Director client is configured")
        return self._client_factory()

    async def stream_events(
        self,
        request: SemanticStoryboardRequestV1,
    ) -> AsyncIterator[SemanticStoryboardSceneStreamEventV1]:
        """Yield only wire-admitted events for one isolated request frontier."""

        if not isinstance(
            request,
            (SemanticStoryboardReflexRequestV1, SemanticStoryboardDirectorRequestV1),
        ):
            raise TypeError("request must be a SemanticStoryboardRequestV1")

        started_at = self._clock()
        yield _started_event(request)
        try:
            verify_semantic_storyboard_frontier(
                request.problem_spec,
                request.base_scene,
                request.base_semantic_scene,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.SEMANTIC_BASE_MISMATCH,
            )
            return

        if request.base_scene.revision >= MAX_SAFE_SEQUENCE:
            yield _failed_event(request, SemanticStoryboardFailureCode.REVISION_LIMIT)
            return

        if isinstance(request, SemanticStoryboardReflexRequestV1):
            try:
                transition = compile_certified_semantic_storyboard_anchor(
                    request.problem_spec,
                    base_scene=request.base_scene,
                    base_semantic_scene=request.base_semantic_scene,
                )
                checkpoint = cast(
                    SemanticStoryboardSceneCheckpointEventV1,
                    _roundtrip_event(
                        SemanticStoryboardSceneCheckpointEventV1(
                            generation=request.generation,
                            attempt=1,
                            sequence=1,
                            base_revision=0,
                            result_revision=1,
                            patch=transition.checkpoint.patch,
                            transition=transition,
                        )
                    ),
                )
                accepted_at = _elapsed_ms(started_at, self._clock())
                frontier = _AcceptedFrontier(
                    scene=checkpoint.transition.result_scene,
                    semantic_scene=checkpoint.transition.result_semantic_scene,
                    checkpoint_count=1,
                    first_checkpoint_ms=accepted_at,
                )
                completed = _completed_event(
                    request,
                    frontier,
                    started_at=started_at,
                    finished_at=self._clock(),
                    reason=SemanticStoryboardCompletionReason.ANCHOR,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                yield _failed_event(
                    request,
                    SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR,
                )
                return
            yield checkpoint
            yield completed
            return

        if not request.base_semantic_scene.components:
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.SEMANTIC_BASE_MISMATCH,
            )
            return
        try:
            component = request.base_semantic_scene.components[0]
            has_capacity = storyboard_has_forward_capacity(
                request.problem_spec,
                component.accepted_records,
            )
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError):
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.SEMANTIC_BASE_MISMATCH,
            )
            return
        except Exception:
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR,
            )
            return
        if not has_capacity:
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.STORYBOARD_CAPACITY_EXHAUSTED,
            )
            return
        try:
            messages = build_semantic_storyboard_director_messages(
                request.prompt,
                request.problem_spec,
                request.base_semantic_scene,
            )
        except (TypeError, ValueError):
            yield _failed_event(request, SemanticStoryboardFailureCode.CONTEXT_TOO_LARGE)
            return
        except Exception:
            yield _failed_event(
                request,
                SemanticStoryboardFailureCode.STORYBOARD_INTEGRITY_ERROR,
            )
            return

        frontier = _AcceptedFrontier(
            scene=request.base_scene,
            semantic_scene=request.base_semantic_scene,
        )
        client: SemanticStoryboardDirectorClient | None = None
        upstream: object | None = None
        owns_client = False
        terminal_cause: _StopCause | None = None
        clean_eof = False
        decline_reason: StoryboardAbstainReasonCode | None = None
        parser = SemanticStoryboardDirectorStreamParser()
        try:
            try:
                if self._before_provider_dispatch is not None:
                    try:
                        await self._before_provider_dispatch()
                    except (asyncio.CancelledError, SceneAdmissionError):
                        raise
                    except Exception:
                        raise _CandidateRejected(_INTERNAL_ERROR) from None
                client = self._resolve_client()
                owns_client = self._client is None
                if not callable(getattr(client, "stream", None)):
                    raise TypeError("Director client must provide stream()")
                upstream = client.stream(
                    messages,
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                )
                if not hasattr(upstream, "__anext__"):
                    raise TypeError("provider did not return an async iterator")
                stream = cast(AsyncIterator[str | bytes], upstream)
                deadline = asyncio.get_running_loop().time() + self._timeout_seconds

                while terminal_cause is None and not clean_eof:
                    try:
                        chunk = await _next_before_deadline(stream, deadline=deadline)
                    except StopAsyncIteration:
                        clean_eof = True
                        try:
                            records = parser.finish()
                        except SemanticStoryboardDirectorStreamError as exc:
                            records = exc.complete_prefix
                            terminal_cause = _parser_stop_cause(exc)
                    else:
                        try:
                            records = parser.feed(chunk)
                        except SemanticStoryboardDirectorStreamError as exc:
                            records = exc.complete_prefix
                            terminal_cause = _parser_stop_cause(exc)

                    for record in records:
                        if isinstance(record, AbstainStoryboardRecordV1):
                            if not clean_eof or frontier.checkpoint_count:
                                terminal_cause = _INVALID_STREAM
                            else:
                                decline_reason = record.reason_code
                            break
                        try:
                            checkpoint, frontier = _prepare_checkpoint(
                                request,
                                record,
                                frontier,
                                started_at=started_at,
                                clock=self._clock,
                            )
                        except _CandidateRejected as exc:
                            terminal_cause = exc.cause
                            break
                        yield checkpoint
            except asyncio.CancelledError:
                raise
            except _CandidateRejected as exc:
                terminal_cause = exc.cause
            except SceneAdmissionError:
                terminal_cause = _PROVIDER_RATE_LIMITED
            except TimeoutError:
                terminal_cause = _PROVIDER_TIMEOUT
            except Exception:
                terminal_cause = _PROVIDER_ERROR
        finally:
            if not parser.closed:
                parser.abort()
            await close_async_resource(
                upstream,
                timeout_seconds=self._cleanup_timeout_seconds,
            )
            if owns_client and client is not upstream:
                await close_async_resource(
                    client,
                    timeout_seconds=self._cleanup_timeout_seconds,
                )

        if decline_reason is not None:
            yield _declined_event(request, decline_reason)
            return
        if terminal_cause is not None:
            yield _terminal_for_cause(
                request,
                frontier,
                terminal_cause,
                started_at=started_at,
                finished_at=self._clock(),
            )
            return
        if clean_eof and frontier.checkpoint_count:
            yield _completed_event(
                request,
                frontier,
                started_at=started_at,
                finished_at=self._clock(),
                reason=SemanticStoryboardCompletionReason.MODEL_STOP,
            )
            return
        yield _failed_event(request, SemanticStoryboardFailureCode.INVALID_MODEL_STREAM)


__all__ = [
    "DEFAULT_SEMANTIC_STORYBOARD_DIRECTOR_MAX_TOKENS",
    "DEFAULT_SEMANTIC_STORYBOARD_TIMEOUT_SECONDS",
    "SemanticStoryboardDirectorClient",
    "SemanticStoryboardDirectorClientFactory",
    "SemanticStoryboardService",
]
