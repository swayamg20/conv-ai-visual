"""CRUD routes for user-owned conversational agents."""

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from murmur.agents.prompting import compile_agent_prompt
from murmur.api.dependencies import CurrentUserDependency, OwnedAgentDependency
from murmur.api.errors import ApiError
from murmur.api.schemas import CreateAgentRequest, UpdateAgentRequest
from murmur.persistence.models import AgentModel
from murmur.persistence.repositories.identities import AgentRepo

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _serialize_agent(agent: AgentModel) -> dict[str, Any]:
    return {
        "id": agent.id,
        "user_id": agent.user_id,
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "persona": agent.get_persona(),
        "capabilities": agent.get_capabilities(),
        "icon": agent.icon,
        "is_default": agent.is_default,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


@router.post("")
async def create_agent(body: CreateAgentRequest, user: CurrentUserDependency) -> JSONResponse:
    persona = body.persona or {}
    agent = AgentRepo.create(
        user_id=user["id"],
        name=body.name,
        description=body.description,
        system_prompt=compile_agent_prompt(persona, body.capabilities),
        persona_json=json.dumps(persona) if persona else None,
        capabilities_json=json.dumps(body.capabilities),
        icon=body.icon,
    )
    return JSONResponse(_serialize_agent(agent), status_code=201)


@router.get("")
async def list_agents(user: CurrentUserDependency) -> JSONResponse:
    agents = AgentRepo.list_by_user(user["id"])
    return JSONResponse({"agents": [_serialize_agent(agent) for agent in agents]})


@router.get("/{agent_id}")
async def get_agent(agent: OwnedAgentDependency) -> JSONResponse:
    return JSONResponse(_serialize_agent(agent))


@router.put("/{agent_id}")
async def update_agent(
    body: UpdateAgentRequest,
    user: CurrentUserDependency,
    agent: OwnedAgentDependency,
) -> JSONResponse:
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.icon is not None:
        updates["icon"] = body.icon

    if body.persona is not None or body.capabilities is not None:
        persona = body.persona if body.persona is not None else agent.get_persona()
        capabilities = (
            body.capabilities if body.capabilities is not None else agent.get_capabilities()
        )
        updates["system_prompt"] = compile_agent_prompt(persona, capabilities)
        if body.persona is not None:
            updates["persona_json"] = json.dumps(persona)
        if body.capabilities is not None:
            updates["capabilities_json"] = json.dumps(capabilities)

    if body.is_default is True:
        AgentRepo.set_default(agent.id, user["id"])

    updated = AgentRepo.update(agent.id, **updates) if updates else AgentRepo.get_by_id(agent.id)
    if updated is None:
        raise ApiError(404, "Agent not found")
    return JSONResponse(_serialize_agent(updated))


@router.delete("/{agent_id}")
async def delete_agent(agent: OwnedAgentDependency) -> JSONResponse:
    AgentRepo.delete(agent.id)
    return JSONResponse({"status": "deleted"})
