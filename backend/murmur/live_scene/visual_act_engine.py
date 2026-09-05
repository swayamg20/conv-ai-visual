"""Bounded model orchestration for narration-free visual-act routing."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeAlias, cast

from murmur.core.async_cleanup import (
    DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
    close_async_resource,
)
from murmur.live_scene.admission import SceneAdmissionError
from murmur.live_scene.contracts import MAX_SCENE_MODEL_OUTPUT_TOKENS
from murmur.live_scene.semantic_contracts import SemanticSceneState, VisualActDecision
from murmur.live_scene.semantic_prompt import build_visual_act_decision_messages
from murmur.live_scene.semantic_stream_parser import (
    VisualActDecisionStreamError,
    VisualActDecisionStreamParser,
)
from murmur.live_scene.visual_act_router import (
    ResolvedVisualAct,
    VisualActRoutingError,
    VisualActRoutingErrorCode,
    resolve_visual_act,
)

_EMPTY_STREAM_REPAIR_HINT = "visual_act_stream: emit one visual-act decision"
DEFAULT_VISUAL_ACT_MAX_TOKENS = 2_048
_ROUTING_REPAIR_HINTS = {
    VisualActRoutingErrorCode.COMPONENT_ALREADY_EXISTS: (
        "visual_act_state: continue the accepted component or abstain"
    ),
    VisualActRoutingErrorCode.COMPONENT_NOT_FOUND: (
        "visual_act_state: copy an accepted componentId exactly"
    ),
    VisualActRoutingErrorCode.MULTIPLE_COMPONENTS_UNSUPPORTED: (
        "visual_act_state: abstain because multiple components are unsupported"
    ),
    VisualActRoutingErrorCode.NON_FORWARD_TARGET: (
        "visual_act_state: choose a strictly later target or abstain"
    ),
    VisualActRoutingErrorCode.PROOF_REQUIRES_IDENTITY: (
        "visual_act_state: reveal the identity before continuing to proof"
    ),
}


class VisualActModelClient(Protocol):
    """Provider-neutral streaming surface required by the router."""

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str | bytes]: ...


@dataclass(frozen=True, slots=True)
class VisualActRoutingResult:
    """One accepted decision and its deterministic server resolution."""

    decision: VisualActDecision
    resolved: ResolvedVisualAct | None
    provider_attempts: Literal[1, 2]

    @property
    def repaired(self) -> bool:
        return self.provider_attempts == 2


@dataclass(frozen=True, slots=True)
class VisualActRoutingRepairing:
    """Lifecycle boundary emitted before the one allowed repair dispatch."""

    from_attempt: Literal[1] = 1
    to_attempt: Literal[2] = 2


VisualActRoutingStep: TypeAlias = VisualActRoutingRepairing | VisualActRoutingResult


class VisualActEngineErrorCode(StrEnum):
    """Stable public failure categories for routing orchestration."""

    CONTEXT_INVALID = "context_invalid"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_VISUAL_ACT = "invalid_visual_act"
    INTERNAL_ERROR = "internal_error"


_ERROR_MESSAGES = {
    VisualActEngineErrorCode.CONTEXT_INVALID: "Visual routing context was invalid.",
    VisualActEngineErrorCode.PROVIDER_RATE_LIMIT: "Visual routing capacity is busy.",
    VisualActEngineErrorCode.PROVIDER_TIMEOUT: "Visual routing timed out.",
    VisualActEngineErrorCode.PROVIDER_ERROR: "Visual routing provider failed.",
    VisualActEngineErrorCode.INVALID_VISUAL_ACT: "Visual routing returned no valid decision.",
    VisualActEngineErrorCode.INTERNAL_ERROR: "Visual routing failed an internal invariant.",
}
_ERROR_RETRYABLE = {
    VisualActEngineErrorCode.CONTEXT_INVALID: False,
    VisualActEngineErrorCode.PROVIDER_RATE_LIMIT: True,
    VisualActEngineErrorCode.PROVIDER_TIMEOUT: True,
    VisualActEngineErrorCode.PROVIDER_ERROR: True,
    VisualActEngineErrorCode.INVALID_VISUAL_ACT: True,
    VisualActEngineErrorCode.INTERNAL_ERROR: False,
}


class VisualActEngineError(RuntimeError):
    """Sanitized routing failure that never retains model or provider text."""

    def __init__(
        self,
        code: VisualActEngineErrorCode,
        *,
        provider_attempts: Literal[0, 1, 2],
    ) -> None:
        if not isinstance(code, VisualActEngineErrorCode):
            raise TypeError("code must be a VisualActEngineErrorCode")
        if (
            isinstance(provider_attempts, bool)
            or not isinstance(provider_attempts, int)
            or provider_attempts not in (0, 1, 2)
        ):
            raise ValueError("provider_attempts must be 0, 1, or 2")
        super().__init__(_ERROR_MESSAGES[code])
        self.code = code
        self.provider_attempts = provider_attempts
        self.retryable = _ERROR_RETRYABLE[code]


class _RejectedVisualAct(ValueError):
    """Fixed repair instruction for one structurally safe rejected attempt."""

    def __init__(self, repair_hint: str) -> None:
        super().__init__(repair_hint)
        self.repair_hint = repair_hint


def _semantic_scene_json(scene: SemanticSceneState) -> str:
    return json.dumps(
        scene.model_dump(
            mode="json",
            by_alias=True,
            exclude={"certificate_head_sha256"},
        ),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _split_after_newlines(chunk: str | bytes) -> tuple[str | bytes, ...]:
    separator = b"\n" if isinstance(chunk, bytes) else "\n"
    parts = chunk.split(separator)
    segments = [part + separator for part in parts[:-1]]
    if parts[-1] or not segments:
        segments.append(parts[-1])
    return tuple(segments)


async def _next_before_deadline(
    stream: AsyncIterator[str | bytes],
    *,
    deadline: float,
) -> str | bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return await asyncio.wait_for(anext(stream), timeout=remaining)


class VisualActRoutingEngine:
    """Resolve one model-authored visual decision with at most one repair."""

    def __init__(
        self,
        client: VisualActModelClient,
        *,
        max_tokens: int = DEFAULT_VISUAL_ACT_MAX_TOKENS,
        timeout_seconds: float = 20.0,
        before_dispatch: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if not callable(getattr(client, "stream", None)):
            raise TypeError("client must provide stream()")
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
        if before_dispatch is not None and not callable(before_dispatch):
            raise TypeError("before_dispatch must be callable")

        self._client = client
        self._max_tokens = max_tokens
        self._timeout_seconds = float(timeout_seconds)
        self._before_dispatch = before_dispatch
        self._cleanup_timeout_seconds = min(
            self._timeout_seconds,
            DEFAULT_ASYNC_RESOURCE_CLOSE_TIMEOUT_SECONDS,
        )

    async def route(
        self,
        *,
        prompt: str,
        semantic_scene: SemanticSceneState,
    ) -> VisualActRoutingResult:
        """Route one prompt while consuming internal lifecycle steps."""

        async for step in self.stream_route(
            prompt=prompt,
            semantic_scene=semantic_scene,
        ):
            if isinstance(step, VisualActRoutingResult):
                return step
        raise AssertionError("visual-act routing ended without a result")

    async def stream_route(
        self,
        *,
        prompt: str,
        semantic_scene: SemanticSceneState,
    ) -> AsyncIterator[VisualActRoutingStep]:
        """Yield a repair boundary before dispatch, then one resolved result."""

        if not isinstance(semantic_scene, SemanticSceneState):
            raise VisualActEngineError(
                VisualActEngineErrorCode.CONTEXT_INVALID,
                provider_attempts=0,
            )
        try:
            scene_json = _semantic_scene_json(semantic_scene)
            messages = build_visual_act_decision_messages(prompt, scene_json)
        except (TypeError, ValueError):
            raise VisualActEngineError(
                VisualActEngineErrorCode.CONTEXT_INVALID,
                provider_attempts=0,
            ) from None

        repair_hint: str | None = None
        for attempt in (1, 2):
            if attempt == 2:
                assert repair_hint is not None
                try:
                    messages = build_visual_act_decision_messages(
                        prompt,
                        scene_json,
                        repair_context={
                            "error": repair_hint,
                            "last_accepted_semantic_scene_json": scene_json,
                        },
                    )
                except (TypeError, ValueError):
                    raise VisualActEngineError(
                        VisualActEngineErrorCode.INTERNAL_ERROR,
                        provider_attempts=1,
                    ) from None
                yield VisualActRoutingRepairing()

            try:
                decision, resolved = await self._attempt(
                    messages=messages,
                    scene=semantic_scene,
                    attempt=attempt,
                )
            except _RejectedVisualAct as exc:
                repair_hint = exc.repair_hint
                if attempt == 1:
                    continue
                raise VisualActEngineError(
                    VisualActEngineErrorCode.INVALID_VISUAL_ACT,
                    provider_attempts=2,
                ) from None
            yield VisualActRoutingResult(
                decision=decision,
                resolved=resolved,
                provider_attempts=attempt,
            )
            return

        raise AssertionError("visual-act routing attempts were exhausted")

    async def _attempt(
        self,
        *,
        messages: list[dict[str, str]],
        scene: SemanticSceneState,
        attempt: Literal[1, 2],
    ) -> tuple[VisualActDecision, ResolvedVisualAct | None]:
        parser = VisualActDecisionStreamParser()
        upstream: object | None = None

        try:
            if self._before_dispatch is not None:
                try:
                    await self._before_dispatch()
                except asyncio.CancelledError:
                    raise
                except SceneAdmissionError:
                    raise VisualActEngineError(
                        VisualActEngineErrorCode.PROVIDER_RATE_LIMIT,
                        provider_attempts=0 if attempt == 1 else 1,
                    ) from None
                except Exception:
                    raise VisualActEngineError(
                        VisualActEngineErrorCode.INTERNAL_ERROR,
                        provider_attempts=0 if attempt == 1 else 1,
                    ) from None
            try:
                upstream = self._client.stream(
                    messages,
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise VisualActEngineError(
                    VisualActEngineErrorCode.PROVIDER_ERROR,
                    provider_attempts=attempt,
                ) from None
            if not hasattr(upstream, "__anext__"):
                raise VisualActEngineError(
                    VisualActEngineErrorCode.PROVIDER_ERROR,
                    provider_attempts=attempt,
                )

            stream = cast(AsyncIterator[str | bytes], upstream)
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            while True:
                try:
                    chunk = await _next_before_deadline(stream, deadline=deadline)
                except StopAsyncIteration:
                    try:
                        decisions = parser.finish()
                    except VisualActDecisionStreamError as exc:
                        raise _RejectedVisualAct(exc.repair_hint) from None
                    if not decisions:
                        raise _RejectedVisualAct(_EMPTY_STREAM_REPAIR_HINT) from None
                    return self._resolve(decisions[0], scene=scene, attempt=attempt)
                except TimeoutError:
                    raise VisualActEngineError(
                        VisualActEngineErrorCode.PROVIDER_TIMEOUT,
                        provider_attempts=attempt,
                    ) from None
                except asyncio.CancelledError:
                    raise
                except Exception:
                    raise VisualActEngineError(
                        VisualActEngineErrorCode.PROVIDER_ERROR,
                        provider_attempts=attempt,
                    ) from None

                for segment in _split_after_newlines(chunk):
                    try:
                        decisions = parser.feed(segment)
                    except VisualActDecisionStreamError as exc:
                        raise _RejectedVisualAct(exc.repair_hint) from None
                    if decisions:
                        return self._resolve(decisions[0], scene=scene, attempt=attempt)
        finally:
            if not parser.closed:
                parser.abort()
            await close_async_resource(
                upstream,
                timeout_seconds=self._cleanup_timeout_seconds,
            )

    @staticmethod
    def _resolve(
        decision: VisualActDecision,
        *,
        scene: SemanticSceneState,
        attempt: Literal[1, 2],
    ) -> tuple[VisualActDecision, ResolvedVisualAct | None]:
        try:
            return decision, resolve_visual_act(decision, scene)
        except VisualActRoutingError as exc:
            raise _RejectedVisualAct(_ROUTING_REPAIR_HINTS[exc.code]) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise VisualActEngineError(
                VisualActEngineErrorCode.INTERNAL_ERROR,
                provider_attempts=attempt,
            ) from None


__all__ = [
    "DEFAULT_VISUAL_ACT_MAX_TOKENS",
    "VisualActEngineError",
    "VisualActEngineErrorCode",
    "VisualActModelClient",
    "VisualActRoutingEngine",
    "VisualActRoutingRepairing",
    "VisualActRoutingResult",
    "VisualActRoutingStep",
]
