"""Contract tests for the split persistence domains."""

import os
import subprocess
import sys

from murmur.persistence.models import ResourceChunkModel
from murmur.persistence.repositories.identities import AgentRepo, UserRepo
from murmur.persistence.repositories.memory import (
    DecisionMemoryRepo,
    EpisodicMemoryRepo,
    UserProfileRepo,
)
from murmur.persistence.repositories.observability import (
    LLMCallLogRepo,
    TTSResilienceLogRepo,
    VoicePipelineLogRepo,
)
from murmur.persistence.repositories.resources import ResourceChunkRepo, ResourceRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.persistence.repositories.tools import ToolRepo


def _create_user_agent_session(suffix: str):
    user = UserRepo.get_or_create(
        uid=f"user-{suffix}",
        email=f"user-{suffix}@example.com",
        name=f"User {suffix}",
    )
    agent = AgentRepo.create(
        user_id=user.id,
        name=f"Agent {suffix}",
        system_prompt="Teach carefully.",
    )
    session = SessionRepo.create(user.id, agent.id)
    return user, agent, session


def test_memory_and_tool_repositories_preserve_domain_contracts() -> None:
    user, _, session = _create_user_agent_session("memory")

    saved = EpisodicMemoryRepo.save(
        user_id=user.id,
        session_id=session.id,
        summary="Practiced vector decomposition.",
        turn_count=4,
        metadata={"subject": "physics"},
    )
    assert saved.get_meta() == {"subject": "physics"}
    assert EpisodicMemoryRepo.get_recent(user.id)[0].summary == saved.summary

    UserProfileRepo.get_or_create(user.id)
    profile = UserProfileRepo.update(
        user.id,
        preferences={"pace": "slow"},
        facts={"grade": 11},
    )
    assert profile is not None
    assert profile.preferences == {"pace": "slow"}
    assert profile.facts == {"grade": 11}

    DecisionMemoryRepo.log(user.id, "search", success=False, context="No result")
    assert DecisionMemoryRepo.has_recent_failure(user.id, "search") is True

    ToolRepo.upsert(
        name="calculator",
        description="Calculate an expression.",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
        handler_module="tools.calculator",
        handler_function="calculate",
    )
    assert ToolRepo.to_openai_format()[0]["function"]["description"] == ("Calculate an expression.")

    ToolRepo.upsert(
        name="calculator",
        description="Safely calculate an expression.",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}},
        handler_module="tools.calculator",
        handler_function="calculate",
    )
    assert ToolRepo.to_openai_format()[0]["function"]["description"] == (
        "Safely calculate an expression."
    )


def test_resource_repository_ranks_and_deletes_chunks() -> None:
    user, agent, _ = _create_user_agent_session("resource")
    resource = ResourceRepo.create(
        user_id=user.id,
        agent_id=agent.id,
        name="mechanics.txt",
        resource_type="text",
        status="ready",
    )
    ResourceChunkRepo.create_batch(
        [
            ResourceChunkModel(
                resource_id=resource.id,
                chunk_index=0,
                content="An incline resolves weight into parallel and normal components.",
            ),
            ResourceChunkModel(
                resource_id=resource.id,
                chunk_index=1,
                content="A pendulum oscillates around its equilibrium point.",
            ),
        ]
    )

    results = ResourceChunkRepo.search(agent.id, "weight components on an incline")
    assert [result.chunk_index for result in results] == [0]

    assert ResourceRepo.delete(resource.id) is True
    assert ResourceRepo.get_by_id(resource.id) is None
    assert ResourceChunkRepo.search(agent.id, "incline") == []


def test_observability_aggregates_remain_user_scoped() -> None:
    owner, _, owner_session = _create_user_agent_session("metrics-owner")
    other, _, other_session = _create_user_agent_session("metrics-other")

    for latency in (100, 300):
        LLMCallLogRepo.save(
            session_id=owner_session.id,
            user_id=owner.id,
            user_message="owner message",
            llm_provider="test",
            llm_model="test-model",
            latency_llm_ms=latency,
            latency_total_ms=latency,
        )
    LLMCallLogRepo.save(
        session_id=other_session.id,
        user_id=other.id,
        user_message="other message",
        llm_provider="test",
        llm_model="test-model",
        latency_llm_ms=999,
        latency_total_ms=999,
    )

    llm_stats = LLMCallLogRepo.get_stats(owner.id)
    assert llm_stats["total_calls"] == 2
    assert llm_stats["avg_llm_latency_ms"] == 200
    assert llm_stats["p95_llm_latency_ms"] == 300

    voice_log = VoicePipelineLogRepo.save(
        session_id=owner_session.id,
        user_id=owner.id,
        user_message="owner voice message",
        latency_total_ms=250,
    )
    VoicePipelineLogRepo.save(
        session_id=other_session.id,
        user_id=other.id,
        user_message="other voice message",
        latency_total_ms=999,
    )
    TTSResilienceLogRepo.save(
        voice_log_id=voice_log.id,
        provider_used="elevenlabs",
        retry_count=2,
        fallback_used=True,
        fallback_provider="kokoro",
    )

    voice_stats = VoicePipelineLogRepo.get_stats(owner.id)
    assert voice_stats["total_turns"] == 1
    assert voice_stats["avg_total_ms"] == 250
    assert voice_stats["retry_turns"] == 1
    assert voice_stats["fallback_rate"] == 100


def test_importing_models_does_not_create_database_file(tmp_path) -> None:
    database_path = tmp_path / "import-side-effect.db"
    environment = os.environ.copy()
    environment["MURMUR_DATABASE_URL"] = f"sqlite:///{database_path}"

    subprocess.run(
        [sys.executable, "-c", "import murmur.persistence.models"],
        cwd=tmp_path,
        env=environment,
        check=True,
    )

    assert not database_path.exists()
