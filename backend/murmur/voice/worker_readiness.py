"""Registration-aware readiness for the production LiveKit worker process."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aiohttp import web

logger = logging.getLogger(__name__)

WORKER_READY_HOST = "0.0.0.0"
WORKER_READY_PORT = 8082


class EventServer(Protocol):
    def on(self, event: str, callback: Callable[..., object]) -> object: ...


@dataclass
class WorkerRegistrationReadiness:
    registered: bool = False
    runner: web.AppRunner | None = None
    startup_task: asyncio.Task[None] | None = None

    def mark_registered(self, *_args: object) -> None:
        self.registered = True

    async def handle(self, _request: web.Request) -> web.Response:
        if not self.registered:
            return web.Response(status=503, text="waiting for livekit registration")
        return web.Response(text="OK")

    async def start(self, *, host: str = WORKER_READY_HOST, port: int = WORKER_READY_PORT) -> None:
        if self.runner is not None:
            return
        app = web.Application()
        app.add_routes([web.get("/", self.handle)])
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, host, port).start()
        except BaseException:
            await runner.cleanup()
            raise
        self.runner = runner

    def schedule_start(self, *_args: object) -> None:
        if self.startup_task is not None:
            return
        self.startup_task = asyncio.create_task(self.start(), name="voice-v2-readiness")
        self.startup_task.add_done_callback(_observe_startup)


def install_worker_registration_readiness(
    server: EventServer,
) -> WorkerRegistrationReadiness:
    """Expose readiness only after LiveKit confirms named-worker registration."""

    readiness = WorkerRegistrationReadiness()
    server.on("worker_started", readiness.schedule_start)
    server.on("worker_registered", readiness.mark_registered)
    return readiness


def _observe_startup(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("voice_v2 readiness server failed", exc_info=True)
