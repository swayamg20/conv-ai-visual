"""Build the LLM-facing runtime context for a user-owned agent."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from murmur.agents.prompting import append_mastery_context, append_resource_context
from murmur.llm.pipeline import LLMPipeline
from murmur.persistence.repositories.resources import ResourceRepo
from murmur.persistence.repositories.sessions import TopicMasteryRepo
from murmur.resources.service import search_chunks

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """Prompt, capabilities, and ready resources for one agent session."""

    prompt: str
    canvas_enabled: bool
    ready_resources: tuple[Any, ...]


def build_agent_runtime_config(user_id: str, agent: Any) -> AgentRuntimeConfig:
    """Build trusted prompt context from agent, resource, and mastery state."""
    capabilities = agent.get_capabilities()
    canvas_enabled = "canvas" in capabilities

    prompt = agent.system_prompt
    resources = ResourceRepo.list_by_agent(agent.id)
    ready_resources = tuple(resource for resource in resources if resource.status == "ready")
    if ready_resources:
        prompt = append_resource_context(prompt, [resource.name for resource in ready_resources])

    mastery_context = TopicMasteryRepo.get_tutoring_context(user_id, agent.id)
    mastery_prompt = mastery_context.get("prompt", "")
    prompt = append_mastery_context(prompt, mastery_prompt)
    if mastery_prompt:
        logger.info(
            "Injected mastery context for user=%s agent=%s topics=%d chapters=%d",
            user_id,
            agent.id,
            len(mastery_context.get("topics", [])),
            len(mastery_context.get("chapters", [])),
        )

    return AgentRuntimeConfig(
        prompt=prompt,
        canvas_enabled=canvas_enabled,
        ready_resources=ready_resources,
    )


def register_agent_resource_tool(
    pipeline: LLMPipeline,
    agent_id: str,
    ready_resources: Sequence[Any],
) -> None:
    """Expose uploaded-resource search when an agent has indexed resources."""
    if not ready_resources:
        return

    def search_resources_handler(query: str, limit: int = 5) -> str:
        chunks = search_chunks(agent_id, query, limit=limit)
        if not chunks:
            return "No relevant content found in the resources."

        results = []
        for chunk in chunks:
            page_info = f" (page {chunk.page_number})" if chunk.page_number else ""
            results.append(f"[Chunk {chunk.chunk_index}{page_info}]\n{chunk.content}")
        return "\n\n---\n\n".join(results)

    pipeline.register_tool(
        name="search_resources",
        description=(
            "Search the agent's uploaded resources (PDFs, URLs) for relevant content. "
            "Use this when the student asks about topics covered in their materials."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to find relevant content",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        func=search_resources_handler,
    )
