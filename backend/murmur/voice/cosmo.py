"""Server-only Cosmo credential exchange for the opt-in voice experiment."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, ValidationError

COSMO_TOKEN_URL = "https://platform.askcosmo.ai/api/v1/external/auth/token"
TOKEN_TTL_SECONDS = 900
MAX_RESPONSE_BYTES = 16_384


class CosmoUnavailable(RuntimeError):
    """An intentionally sanitized, user-facing configuration/provider failure."""


class CosmoToken(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    jwt: SecretStr = Field(min_length=1, max_length=8192)
    expires_at: AwareDatetime

    def browser_payload(self) -> dict[str, str]:
        return {
            "jwt": self.jwt.get_secret_value(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class CosmoTokenBroker:
    enabled: bool
    api_key: str = field(repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    async def mint(self, user_id: str) -> CosmoToken:
        if not self.enabled:
            raise CosmoUnavailable("Enable MURMUR_COSMO_ENABLED to use the Cosmo lab.")
        if not self.configured:
            raise CosmoUnavailable("Set COSMO_API_KEY on the Murmur server, then restart it.")

        try:
            async with (
                asyncio.timeout(12),
                httpx.AsyncClient(
                    transport=self.transport, timeout=10, follow_redirects=False
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    COSMO_TOKEN_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"external_user_id": user_id, "ttl_seconds": TOKEN_TTL_SECONDS},
                ) as response:
                    if response.status_code in (401, 403):
                        raise CosmoUnavailable(
                            "Cosmo rejected token minting. Check the server key and "
                            "its user_tokens:mint scope."
                        )
                    if response.status_code != 200:
                        raise CosmoUnavailable("Cosmo token service is unavailable. Try again.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise CosmoUnavailable("Cosmo returned an invalid token response.")
            token = CosmoToken.model_validate_json(bytes(body))
            now = datetime.now(UTC)
            if not now < token.expires_at <= now + timedelta(seconds=TOKEN_TTL_SECONDS + 60):
                raise CosmoUnavailable("Cosmo returned an invalid token expiry. Try again.")
            if any(character.isspace() for character in token.jwt.get_secret_value()):
                raise CosmoUnavailable("Cosmo returned an invalid token response.")
            return token
        except (httpx.HTTPError, TimeoutError):
            raise CosmoUnavailable("Could not reach Cosmo. Try connecting again.") from None
        except ValidationError:
            raise CosmoUnavailable("Cosmo returned an invalid token response.") from None
