"""Resource ingestion and mastery routes for user-owned agents."""

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from murmur.api.dependencies import OwnedAgentDependency
from murmur.api.errors import ApiError
from murmur.persistence import get_data_dir
from murmur.persistence.models import ResourceModel
from murmur.persistence.repositories.resources import ResourceRepo
from murmur.persistence.repositories.sessions import TopicMasteryRepo
from murmur.resources.service import ingest_pdf, ingest_url

router = APIRouter(tags=["resources"])


def _serialize_resource(resource: ResourceModel) -> dict[str, Any]:
    return {
        "id": resource.id,
        "agent_id": resource.agent_id,
        "name": resource.name,
        "resource_type": resource.resource_type,
        "chunk_count": resource.chunk_count,
        "size_bytes": resource.size_bytes,
        "status": resource.status,
        "created_at": resource.created_at.isoformat() if resource.created_at else None,
    }


@router.post("/api/agents/{agent_id}/resources")
async def add_resource(
    agent_id: str,
    agent: OwnedAgentDependency,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
) -> JSONResponse:
    if file and file.filename:
        upload_dir = get_data_dir() / "uploads" / agent.user_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / f"{uuid4()}.pdf"
        await asyncio.to_thread(file_path.write_bytes, await file.read())
        resource = ingest_pdf(str(file_path), agent_id=agent_id, user_id=agent.user_id)
        return JSONResponse(_serialize_resource(resource))

    if url:
        resource = await ingest_url(url, agent_id=agent_id, user_id=agent.user_id)
        return JSONResponse(_serialize_resource(resource))

    raise ApiError(400, "Provide either a file upload or a url parameter")


@router.get("/api/agents/{agent_id}/resources")
async def list_resources(agent: OwnedAgentDependency) -> JSONResponse:
    resources = ResourceRepo.list_by_agent(agent.id)
    return JSONResponse([_serialize_resource(resource) for resource in resources])


@router.delete("/api/agents/{agent_id}/resources/{resource_id}")
async def delete_resource(
    resource_id: str,
    agent: OwnedAgentDependency,
) -> JSONResponse:
    resource = ResourceRepo.get_by_id(resource_id)
    if not resource or resource.agent_id != agent.id or resource.user_id != agent.user_id:
        raise ApiError(404, "Resource not found")
    ResourceRepo.delete(resource_id)
    return JSONResponse({"status": "deleted"})


@router.get("/api/agents/{agent_id}/mastery")
async def get_mastery(agent: OwnedAgentDependency) -> JSONResponse:
    return JSONResponse(TopicMasteryRepo.get_summary(agent.user_id, agent.id))
