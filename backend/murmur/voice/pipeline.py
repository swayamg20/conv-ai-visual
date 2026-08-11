"""Construction of user- and agent-bound LLM pipelines for voice peers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from funcs.config import config
from murmur.agents.runtime import build_agent_runtime_config, register_agent_resource_tool
from murmur.llm.pipeline import LLMPipeline
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.runtime import RuntimeRegistry

logger = logging.getLogger(__name__)
PipelineFactory = Callable[..., LLMPipeline]


class VoicePipelineFactory:
    """Create or return the LLM pipeline bound to a trusted voice record."""

    def __init__(
        self,
        runtime: RuntimeRegistry,
        *,
        pipeline_factory: PipelineFactory = LLMPipeline,
    ) -> None:
        self.runtime = runtime
        self.pipeline_factory = pipeline_factory

    def get_or_create(self, peer_id: str) -> LLMPipeline:
        voice_session = self.runtime.get_voice(peer_id)
        if voice_session is None:
            raise RuntimeError(f"Missing voice runtime for peer {peer_id}")
        if voice_session.pipeline is not None:
            self.runtime.touch_voice(peer_id)
            return voice_session.pipeline

        user_id = voice_session.user_id
        agent_id = voice_session.agent_id
        persistent_session_id = voice_session.persistent_session_id
        session_key = persistent_session_id or peer_id
        agent = AgentRepo.get_by_id(agent_id) if agent_id else None
        if agent_id and not agent:
            raise RuntimeError(f"Agent not found for voice session: {agent_id}")
        if agent and agent.user_id != user_id:
            raise RuntimeError(f"Forbidden voice agent access for user={user_id} agent={agent_id}")

        ready_resources = ()
        if agent:
            agent_config = build_agent_runtime_config(user_id, agent)
            pipeline = self.pipeline_factory(
                provider=config.LLM_PROVIDER,
                api_key=None,
                model=None,
                system_prompt=agent_config.prompt,
                max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                user_id=user_id,
                session_id=session_key,
                agent_id=agent.id,
                enable_memory=True,
                canvas_mode=agent_config.canvas_enabled,
                canvas_system_prompt=(agent_config.prompt if agent_config.canvas_enabled else None),
            )
            ready_resources = agent_config.ready_resources
        else:
            pipeline = self.pipeline_factory(
                provider=config.LLM_PROVIDER,
                api_key=None,
                model=None,
                system_prompt=config.LLM_SYSTEM_PROMPT,
                max_context_messages=config.LLM_MAX_CONTEXT_MESSAGES,
                user_id=user_id,
                session_id=session_key,
                agent_id=agent_id,
                enable_memory=True,
                canvas_mode=True,
                canvas_system_prompt=config.LLM_MATH_TUTOR_PROMPT,
            )

        if pipeline.memory and agent_id:
            pipeline.memory.agent_id = agent_id
        if persistent_session_id and pipeline.memory:
            existing_session = SessionRepo.get_by_id(persistent_session_id)
            if existing_session and existing_session.message_count > 0:
                pipeline.memory.load_session_messages(persistent_session_id)
                logger.info(
                    "[%s] Resumed voice session %s for user=%s agent=%s with %d messages",
                    peer_id,
                    persistent_session_id,
                    user_id,
                    agent_id or "none",
                    existing_session.message_count,
                )

        pipeline.load_tools_from_db()
        if agent and ready_resources:
            register_agent_resource_tool(pipeline, agent.id, ready_resources)

        async def broadcast_canvas(operations) -> None:
            channel = voice_session.datachannel
            if channel and channel.readyState == "open":
                channel.send(json.dumps({"type": "canvas_update", "operations": operations}))

        async def capture_animation(data) -> None:
            if data.get("tool", "") == "teach_with_visuals" and data.get("sdl"):
                voice_session.pending_sdl = data["sdl"]

        pipeline.set_canvas_callback(broadcast_canvas)
        pipeline.set_animation_callback(capture_animation)
        voice_session.pipeline = pipeline
        self.runtime.touch_voice(peer_id)
        logger.info(
            "[%s] Voice pipeline created (session=%s, agent=%s, tools=%d)",
            peer_id,
            session_key,
            agent_id or "none",
            len(pipeline.get_tools_schema()),
        )
        return pipeline
