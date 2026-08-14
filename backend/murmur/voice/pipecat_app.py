"""One-worker Uvicorn entrypoint for the standalone Pipecat ASGI process."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI

from murmur.api.pipecat_application import (
    FirebasePipecatAuthenticator,
    PipecatAuthenticator,
    create_pipecat_application,
)
from murmur.api.routers.pipecat_voice import PipecatHttpComposition
from murmur.persistence import init_db
from murmur.voice.pipecat_composition import (
    PipecatCompositionSettings,
    PipecatCompositionUnavailable,
    create_pipecat_composition,
)


@dataclass(frozen=True)
class PipecatServerSettings:
    """Non-negotiable process shape plus its loopback-friendly bind address."""

    host: str = "127.0.0.1"
    port: int = 8001
    workers: int = 1
    access_log: bool = False
    limit_concurrency: int = 100

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host
            or self.host != self.host.strip()
            or len(self.host) > 253
            or any(character.isspace() for character in self.host)
            or any(character in self.host for character in "/\\?#@")
        ):
            raise ValueError("Pipecat server host is invalid")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Pipecat server port is invalid")
        if self.workers != 1:
            raise ValueError("Pipecat process-local signaling requires exactly one worker")
        if self.access_log:
            raise ValueError("Pipecat opaque signaling requires access logs to stay disabled")
        if self.limit_concurrency != 100:
            raise ValueError("Pipecat server concurrency limit must remain fixed at 100")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> PipecatServerSettings:
        source = os.environ if environment is None else environment
        try:
            port = int(source.get("PIPECAT_PORT", "8001"))
        except (TypeError, ValueError):
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat server configuration is invalid"
            ) from None
        try:
            return cls(
                host=source.get("PIPECAT_HOST", "127.0.0.1"),
                port=port,
            )
        except (TypeError, ValueError):
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat server configuration is invalid"
            ) from None


def create_app(
    *,
    composition: PipecatHttpComposition | None = None,
    authenticator: PipecatAuthenticator | None = None,
    composition_settings: PipecatCompositionSettings | None = None,
    database_initializer: Callable[[], object] | None = init_db,
) -> FastAPI:
    """Uvicorn factory with injectable seams for deterministic process tests."""

    settings = composition_settings
    if composition is None:
        settings = settings or PipecatCompositionSettings.from_environment()
        composition = create_pipecat_composition(settings)
    if authenticator is None:
        source = os.environ
        try:
            auth_timeout = float(source.get("PIPECAT_AUTH_TIMEOUT_SECONDS", "2"))
        except (TypeError, ValueError):
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat authentication configuration is invalid"
            ) from None
        authenticator = FirebasePipecatAuthenticator(timeout_seconds=auth_timeout)
    return create_pipecat_application(
        composition,
        authenticator=authenticator,
        database_initializer=database_initializer,
    )


def uvicorn_options(settings: PipecatServerSettings) -> dict[str, object]:
    """Return the auditable fixed runner contract without starting a process."""

    return {
        "factory": True,
        "host": settings.host,
        "port": settings.port,
        "workers": 1,
        "access_log": False,
        "limit_concurrency": 100,
        "server_header": False,
    }


def main() -> None:
    settings = PipecatServerSettings.from_environment()
    uvicorn.run(
        "murmur.voice.pipecat_app:create_app",
        **uvicorn_options(settings),
    )


if __name__ == "__main__":  # pragma: no cover - exercised as a process entrypoint
    main()


__all__ = [
    "PipecatServerSettings",
    "create_app",
    "main",
    "uvicorn_options",
]
