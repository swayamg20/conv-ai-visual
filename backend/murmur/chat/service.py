"""Application service for authenticated, persistent text-chat sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

from murmur.agents.runtime import (
    build_agent_runtime_config,
    register_agent_resource_tool,
)
from murmur.chat.models import ChatTurn, ChatTurnRequest
from murmur.core import (
    InvalidRequestError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceInitializationError,
)
from murmur.core.config import config
from murmur.llm.pipeline import LLMPipeline
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.observability import LLMCallLogRepo, VoicePipelineLogRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.runtime import ChatRuntimeSession, RuntimeRegistry

logger = logging.getLogger(__name__)

DEFAULT_SUMMARY_MIN_MESSAGES = 4
PipelineFactory = Callable[..., LLMPipeline]


class ChatService:
    """Own chat pipeline creation, streaming, persistence, and cleanup."""

    def __init__(
        self,
        runtime: RuntimeRegistry,
        *,
        pipeline_factory: PipelineFactory = LLMPipeline,
    ) -> None:
        self.runtime = runtime
        self._pipeline_factory = pipeline_factory

    def prepare_turn(self, user_id: str, request: ChatTurnRequest) -> ChatTurn:
        """Resolve trusted ownership and prepare one serialized pipeline turn."""
        session_id = request.session_id
        agent_id = request.agent_id
        persistent_session = None
        active_session = self.runtime.get_chat(session_id) if session_id else None

        if session_id:
            persistent_session = SessionRepo.get_by_id(session_id)
            if persistent_session:
                if persistent_session.user_id != user_id:
                    raise PermissionDeniedError("Forbidden")
                if agent_id and agent_id != persistent_session.agent_id:
                    raise InvalidRequestError("Session does not belong to the supplied agent")
                agent_id = persistent_session.agent_id

        if active_session:
            if active_session.user_id != user_id:
                raise PermissionDeniedError("Forbidden")
            if agent_id and active_session.agent_id != agent_id:
                raise InvalidRequestError("Session does not belong to the supplied agent")
            agent_id = active_session.agent_id

        agent = None
        if agent_id:
            agent = AgentRepo.get_by_id(agent_id)
            if not agent:
                raise ResourceNotFoundError("Agent not found")
            if agent.user_id != user_id:
                raise PermissionDeniedError("Forbidden")

        created_persistent_session = False
        if not session_id:
            if agent_id:
                persistent_session = SessionRepo.create(user_id=user_id, agent_id=agent_id)
                session_id = persistent_session.id
                created_persistent_session = True
            else:
                session_id = str(uuid.uuid4())

        active_session = self.runtime.get_chat(session_id)
        if active_session is None:
            active_session = self._create_session(
                session_id=session_id,
                user_id=user_id,
                agent=agent,
                agent_id=agent_id,
                persistent_session=persistent_session,
                load_persisted_messages=not created_persistent_session,
            )

        if created_persistent_session and agent_id:
            title = request.message[:80].strip()
            try:
                SessionRepo.update_title(session_id, title)
            except Exception:
                logger.debug("Failed to title new session %s", session_id, exc_info=True)

        pipeline = active_session.pipeline
        self.runtime.touch_chat(session_id)
        if pipeline.memory:
            pipeline.memory.agent_id = agent_id

        return ChatTurn(
            session_id=session_id,
            user_id=user_id,
            message=request.message,
            session=active_session,
            canvas_mode=request.canvas_mode,
        )

    def _create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent: Any,
        agent_id: str | None,
        persistent_session: Any,
        load_persisted_messages: bool,
    ) -> ChatRuntimeSession:
        try:
            ready_resources: tuple[Any, ...] = ()
            if agent:
                agent_config = build_agent_runtime_config(user_id, agent)
                pipeline = self._pipeline_factory(
                    provider=config.LLM_PROVIDER,
                    api_key=None,
                    model=None,
                    system_prompt=agent_config.prompt,
                    max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    enable_memory=True,
                    canvas_mode=agent_config.canvas_enabled,
                    canvas_system_prompt=(
                        agent_config.prompt if agent_config.canvas_enabled else None
                    ),
                )
                ready_resources = agent_config.ready_resources
            else:
                pipeline = self._pipeline_factory(
                    provider=config.LLM_PROVIDER,
                    api_key=None,
                    model=None,
                    system_prompt=config.LLM_SYSTEM_PROMPT,
                    max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                    user_id=user_id,
                    session_id=session_id,
                    enable_memory=True,
                    canvas_mode=True,
                    canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT,
                )

            if pipeline.memory and agent_id:
                pipeline.memory.agent_id = agent_id
                logger.info(
                    "Chat session %s bound to user=%s agent=%s for memory context",
                    session_id,
                    user_id,
                    agent_id,
                )
            elif agent_id:
                logger.warning(
                    "Chat session %s created for agent=%s without a memory manager",
                    session_id,
                    agent_id,
                )

            if load_persisted_messages and pipeline.memory:
                existing = persistent_session or SessionRepo.get_by_id(session_id)
                if existing and existing.message_count > 0:
                    pipeline.memory.load_session_messages(session_id)
                    logger.info(
                        "Resumed session %s for user=%s agent=%s with %d persisted messages",
                        session_id,
                        user_id,
                        agent_id or "none",
                        existing.message_count,
                    )

            pipeline.load_tools_from_db()
            if agent and ready_resources:
                register_agent_resource_tool(pipeline, agent.id, ready_resources)

            active_session = self.runtime.register_chat(
                session_id,
                pipeline,
                user_id=user_id,
                agent_id=agent_id,
            )
            logger.info(
                "Created chat session %s for user=%s with %d tools "
                "(canvas_mode=%s, agent=%s, persistent=%s)",
                session_id,
                user_id,
                len(pipeline.get_tools_schema()),
                pipeline.canvas_mode,
                agent_id or "none",
                bool(agent_id),
            )
            return active_session
        except Exception as exc:
            logger.exception("Failed to create chat session: %s", exc)
            raise ServiceInitializationError("Failed to initialize chat") from exc

    async def stream_events(self, turn: ChatTurn) -> AsyncIterator[dict[str, Any]]:
        """Yield transport-neutral events for one pipeline turn."""
        canvas_events: deque[Any] = deque()
        animation_events: deque[dict[str, Any]] = deque()
        pipeline = turn.session.pipeline

        async with turn.session.turn_lock:
            if self.runtime.get_chat(turn.session_id) is not turn.session:
                yield {"type": "error", "message": "Session not found"}
                return

            if turn.canvas_mode is not None:
                pipeline.set_canvas_mode(turn.canvas_mode)
            pipeline.set_canvas_callback(canvas_events.append)
            pipeline.set_animation_callback(animation_events.append)
            yield {"type": "session", "session_id": turn.session_id}

            try:
                async for chunk in pipeline.chat_with_tools_stream(
                    turn.message,
                    temperature=config.LLM_TEMPERATURE,
                    max_tokens=config.LLM_MAX_TOKENS,
                ):
                    self.runtime.touch_chat(turn.session_id)
                    for event in self._drain_queued_events(canvas_events, animation_events):
                        yield event
                    yield {"type": "chunk", "text": chunk}

                for event in self._drain_queued_events(canvas_events, animation_events):
                    yield event
                self._save_observability(turn)
                yield {"type": "done"}
            except Exception as exc:
                logger.exception("Chat stream error: %s", exc)
                self._save_stream_error(turn, exc)
                yield {"type": "error", "message": str(exc)}

    @staticmethod
    def _drain_queued_events(
        canvas_events: deque[Any],
        animation_events: deque[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while canvas_events:
            events.append({"type": "canvas_update", "operations": canvas_events.popleft()})
        while animation_events:
            events.append({"type": "animation_event", **animation_events.popleft()})
        return events

    @staticmethod
    def _save_observability(turn: ChatTurn) -> None:
        pipeline = turn.session.pipeline
        try:
            metrics = pipeline.get_last_call_metrics()
            if not metrics:
                return

            model = getattr(pipeline.client, "model", pipeline.provider)
            response_text = (metrics.get("response_text") or "")[:2000]
            tool_calls_json = json.dumps(metrics.get("tool_calls", []))
            LLMCallLogRepo.save(
                session_id=turn.session_id,
                user_id=turn.user_id,
                user_message=turn.message,
                llm_provider=pipeline.provider,
                llm_model=model,
                tool_calls_json=tool_calls_json,
                response_text=response_text,
                latency_total_ms=metrics.get("latency_total_ms"),
                latency_llm_ms=metrics.get("latency_llm_ms"),
                latency_tool_ms=metrics.get("latency_tool_ms"),
                latency_stream_ms=metrics.get("latency_stream_ms"),
                tokens_in=metrics.get("tokens_in"),
                tokens_out=metrics.get("tokens_out"),
                error=metrics.get("error"),
            )
            VoicePipelineLogRepo.save(
                session_id=turn.session_id,
                user_id=turn.user_id,
                mode="chat",
                user_message=turn.message,
                response_text=response_text,
                llm_provider=pipeline.provider,
                llm_model=model,
                latency_llm_ms=metrics.get("latency_llm_ms"),
                latency_llm_first_token_ms=metrics.get("latency_llm_first_token_ms"),
                latency_tool_ms=metrics.get("latency_tool_ms"),
                latency_total_ms=metrics.get("latency_total_ms"),
                tool_calls_json=tool_calls_json,
                tokens_in=metrics.get("tokens_in"),
                tokens_out=metrics.get("tokens_out"),
                error=metrics.get("error"),
            )
        except Exception as exc:
            logger.warning("Failed to save LLM call log: %s", exc)

    @staticmethod
    def _save_stream_error(turn: ChatTurn, exc: Exception) -> None:
        pipeline = turn.session.pipeline
        try:
            LLMCallLogRepo.save(
                session_id=turn.session_id,
                user_id=turn.user_id,
                user_message=turn.message,
                llm_provider=pipeline.provider,
                llm_model=getattr(pipeline.client, "model", pipeline.provider),
                error=str(exc),
            )
        except Exception:
            logger.debug("Failed to persist chat stream error", exc_info=True)

    def require_owner(self, session_id: str, user_id: str) -> ChatRuntimeSession | None:
        """Enforce persistent or active ownership and return the active record, if any."""
        persistent_session = SessionRepo.get_by_id(session_id)
        active_session = self.runtime.get_chat(session_id)
        owner_id = persistent_session.user_id if persistent_session else None
        if owner_id is None and active_session:
            owner_id = active_session.user_id
        if owner_id is None:
            raise ResourceNotFoundError("Session not found")
        if owner_id != user_id:
            raise PermissionDeniedError("Forbidden")
        return active_session

    async def set_canvas_mode(
        self,
        session_id: str,
        user_id: str,
        *,
        enabled: bool,
        custom_prompt: str | None,
    ) -> dict[str, Any]:
        active_session = self.require_owner(session_id, user_id)
        if active_session is None:
            raise ResourceNotFoundError("Session not found")

        async with active_session.turn_lock:
            if self.runtime.get_chat(session_id) is not active_session:
                raise ResourceNotFoundError("Session not found")
            active_session.pipeline.set_canvas_mode(enabled, custom_prompt)
            self.runtime.touch_chat(session_id)
            return {
                "session_id": session_id,
                "canvas_mode": active_session.pipeline.canvas_mode,
                "tools_count": len(active_session.pipeline.get_tools_schema()),
            }

    async def finalize(
        self,
        session_id: str,
        *,
        min_messages: int = 0,
        persist_db_summary: bool = True,
        background: bool = False,
    ) -> str | None:
        """Remove an active chat session and close its memory context once."""
        active_session = self.runtime.get_chat(session_id)
        if active_session is None or active_session.finalizing:
            return None

        active_session.finalizing = True
        try:
            async with active_session.turn_lock:
                if self.runtime.get_chat(session_id) is not active_session:
                    return None
                self.runtime.pop_chat(session_id)

            if background:
                task = asyncio.create_task(
                    self._persist_pipeline_summary(
                        active_session.pipeline,
                        session_id,
                        min_messages=min_messages,
                        persist_db_summary=persist_db_summary,
                    )
                )
                self._track_background_task(task, f"chat session finalizer [{session_id}]")
                return None

            return await self._persist_pipeline_summary(
                active_session.pipeline,
                session_id,
                min_messages=min_messages,
                persist_db_summary=persist_db_summary,
            )
        finally:
            active_session.finalizing = False

    async def close_with_summary(self, session_id: str, summary: str | None) -> None:
        """Close an in-memory pipeline after another service persisted its summary."""
        active_session = self.runtime.get_chat(session_id)
        if active_session is None:
            return
        async with active_session.turn_lock:
            if self.runtime.get_chat(session_id) is not active_session:
                return
            self.runtime.pop_chat(session_id)
            try:
                active_session.pipeline.end_session(summary)
            except Exception:
                logger.debug("In-memory session cleanup failed for %s", session_id, exc_info=True)

    async def evict_idle(
        self,
        *,
        idle_after_seconds: float,
        min_messages: int = DEFAULT_SUMMARY_MIN_MESSAGES,
        now: float | None = None,
    ) -> None:
        """Finalize chat records that have exceeded the inactivity threshold."""
        current_time = time.monotonic() if now is None else now
        for session_id, active_session in list(self.runtime.chat_sessions.items()):
            if current_time - active_session.last_activity < idle_after_seconds:
                continue
            logger.info("[%s] Evicting idle chat session", session_id)
            await self.finalize(
                session_id,
                min_messages=min_messages,
                persist_db_summary=True,
                background=True,
            )

    async def _persist_pipeline_summary(
        self,
        pipeline: LLMPipeline,
        session_id: str,
        *,
        min_messages: int,
        persist_db_summary: bool,
    ) -> str | None:
        persisted_session_id = getattr(pipeline, "session_id", session_id)
        if self._pipeline_message_count(pipeline) < min_messages:
            try:
                pipeline.end_session(None)
            except Exception as exc:
                logger.warning("[%s] Failed to clear pipeline without summary: %s", session_id, exc)
            return None

        summary: str | None = None
        try:
            generated_summary = await pipeline.generate_session_summary()
            summary = generated_summary.strip() if generated_summary else None
        except Exception as exc:
            logger.warning("[%s] Failed to generate session summary: %s", session_id, exc)

        try:
            pipeline.end_session(summary)
        except Exception as exc:
            logger.warning("[%s] Failed to close pipeline after summary: %s", session_id, exc)

        if summary and persist_db_summary:
            try:
                SessionRepo.update_summary(persisted_session_id, summary)
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to persist summary for session=%s: %s",
                    session_id,
                    persisted_session_id,
                    exc,
                )
        return summary

    @staticmethod
    def _pipeline_message_count(pipeline: LLMPipeline) -> int:
        memory = getattr(pipeline, "memory", None)
        context = getattr(memory, "context", None)
        messages = getattr(context, "messages", None)
        return len(messages) if isinstance(messages, list) else 0

    def _track_background_task(self, task: asyncio.Task[Any], label: str) -> None:
        self.runtime.background_tasks.add(task)

        def done(completed_task: asyncio.Task[Any]) -> None:
            self.runtime.background_tasks.discard(completed_task)
            try:
                error = completed_task.exception()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("%s task inspection failed: %s", label, exc)
                return
            if error:
                logger.warning("%s failed: %s", label, error)

        task.add_done_callback(done)
