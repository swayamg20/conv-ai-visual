"""Authenticated configuration and credential endpoints for the Cosmo lab."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from murmur.api.dependencies import CurrentUserDependency
from murmur.api.errors import ApiError
from murmur.core.config import config
from murmur.voice.cosmo import CosmoTokenBroker, CosmoUnavailable

router = APIRouter(prefix="/api/voice/cosmo", tags=["cosmo-experiment"])


class MintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_cosmo_broker() -> CosmoTokenBroker:
    return CosmoTokenBroker(enabled=config.MURMUR_COSMO_ENABLED, api_key=config.COSMO_API_KEY)


BrokerDependency = Annotated[CosmoTokenBroker, Depends(get_cosmo_broker)]


@router.get("/status")
async def cosmo_status(_user: CurrentUserDependency, broker: BrokerDependency) -> JSONResponse:
    # Configuration is not proof of provider or media readiness.
    return JSONResponse(
        {"enabled": broker.enabled, "configured": broker.configured},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/token")
async def cosmo_token(
    _body: MintRequest, user: CurrentUserDependency, broker: BrokerDependency
) -> JSONResponse:
    try:
        token = await broker.mint(user["id"])
    except CosmoUnavailable as exc:
        raise ApiError(503, str(exc)) from None
    return JSONResponse(token.browser_payload(), headers={"Cache-Control": "no-store"})
