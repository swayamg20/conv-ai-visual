"""Composition root for the standalone authenticated Pipecat process."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

from murmur.persistence.models import AgentModel, SessionModel
from murmur.persistence.repositories.identities import AgentRepo
from murmur.persistence.repositories.sessions import SessionRepo
from murmur.voice.blocking import (
    BoundedSyncRunner,
    BoundedSyncRunnerUnavailable,
    default_repository_runner,
)
from murmur.voice.pipecat_bootstrap import (
    PipecatBootstrapService,
    PipecatBootstrapSettings,
)
from murmur.voice.pipecat_ice import (
    PipecatIceLease,
    PipecatIceLeaseIssuer,
    resolve_pipecat_ice_lease_issuer,
)
from murmur.voice.pipecat_runtime import (
    PipecatProfileProvider,
    PipecatRuntimeStarter,
)
from murmur.voice.pipecat_signaling import (
    PipecatCorsContract,
    PipecatOfferAnswer,
    PipecatOfferRequest,
    PipecatPatchRequest,
    PipecatPeerHandler,
    PipecatPeerHandlerAdapter,
    PipecatSignalingService,
    PipecatSignalingSettings,
)
from murmur.voice.profile import VoiceProfileScope
from murmur.voice.provider_profiles.pipecat_cascade import (
    PIPECAT_DIRECT_CASCADE_PROFILE_ID,
    PipecatCascadeSettings,
    build_pipecat_cascade_provider,
)
from murmur.voice.runtime_contracts import (
    PipecatVoiceRuntimeAssignment,
    VoiceCallClaims,
    VoiceRuntimeKind,
    VoiceRuntimeTerminalResult,
)
from murmur.voice.runtime_projection import (
    PipecatBrowserVoiceAssignment,
    PipecatRuntimeProjectionForbidden,
    PipecatRuntimeProjectionUnavailable,
    project_pipecat_assignment_for_browser,
)

_RepositoryResult = TypeVar("_RepositoryResult")
_PIPECAT_SIGNALING_PATH = "/api/voice/pipecat/signal"
_DEFAULT_SIGNALING_BASE_URL = f"http://127.0.0.1:8001{_PIPECAT_SIGNALING_PATH}"
_PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE = "pipecat.transports.smallwebrtc.request_handler"
_AIORTC_PEER_CONNECTION_LOG_NAMESPACE = "aiortc.rtcpeerconnection"


class _PipecatConnectionWarningFloorLogger:
    """Keep the pinned connection logger below WARNING from emitting secrets."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def trace(self, *_args: object, **_kwargs: object) -> None:
        return None

    def debug(self, *_args: object, **_kwargs: object) -> None:
        return None

    def info(self, *_args: object, **_kwargs: object) -> None:
        return None

    def success(self, *_args: object, **_kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> Any:
        return self._delegate.opt(depth=1).warning(*args, **kwargs)

    def error(self, *args: object, **kwargs: object) -> Any:
        return self._delegate.opt(depth=1).error(*args, **kwargs)

    def critical(self, *args: object, **kwargs: object) -> Any:
        return self._delegate.opt(depth=1).critical(*args, **kwargs)

    def exception(self, *args: object, **kwargs: object) -> Any:
        return self._delegate.opt(depth=1).exception(*args, **kwargs)


class PipecatCompositionUnavailable(RuntimeError):
    """The dedicated Pipecat process cannot preserve its ownership contract."""


class SessionRepository(Protocol):
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None: ...


class AgentRepository(Protocol):
    @staticmethod
    def get_by_id(agent_id: str) -> AgentModel | None: ...


class PipecatBootstrapControl(Protocol):
    async def bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> object: ...

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult | None: ...

    async def aclose(self) -> None: ...


class PipecatSignalingControl(Protocol):
    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer: ...

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None: ...

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> VoiceRuntimeTerminalResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class PipecatCompositionSettings:
    """Validated process-local settings for the exact Pipecat challenger."""

    signaling: PipecatSignalingSettings
    bootstrap: PipecatBootstrapSettings
    cascade: PipecatCascadeSettings | None = None
    projection_cleanup_timeout_seconds: float = 5.0
    runtime_cleanup_timeout_seconds: float = 5.0
    runtime_readiness_timeout_seconds: float = 10.0
    active_call_idle_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        profile_ids = {self.signaling.profile_id, self.bootstrap.profile_id}
        if len(profile_ids) != 1:
            raise ValueError("Pipecat composition profile IDs must match")
        if self.cascade is not None:
            profile_ids.add(self.cascade.profile_id)
            if profile_ids != {PIPECAT_DIRECT_CASCADE_PROFILE_ID}:
                raise ValueError("Pipecat composition requires its exact direct-cascade profile")
        if self.signaling.reservation_ttl_seconds != self.bootstrap.assignment_ttl_seconds:
            raise ValueError("Pipecat bootstrap and signaling TTL policies must match")
        if not self.signaling.allowed_origins:
            raise ValueError("Pipecat composition requires explicit browser origins")
        _require_secure_browser_origins(self.signaling.allowed_origins)
        signaling_path = urlsplit(self.signaling.signaling_base_url).path.rstrip("/")
        if signaling_path != _PIPECAT_SIGNALING_PATH:
            raise ValueError("Pipecat signaling base URL must target the dedicated opaque route")
        for name, value, maximum in (
            ("projection cleanup", self.projection_cleanup_timeout_seconds, 15.0),
            ("runtime cleanup", self.runtime_cleanup_timeout_seconds, 15.0),
            ("runtime readiness", self.runtime_readiness_timeout_seconds, 30.0),
            ("active-call idle", self.active_call_idle_timeout_seconds, 900.0),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or not 0 < value <= maximum
            ):
                raise ValueError(f"Pipecat {name} timeout is invalid")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> PipecatCompositionSettings:
        """Read the dedicated process configuration without mutating legacy config."""

        source = os.environ if environment is None else environment
        runtime = source.get("VOICE_RUNTIME", "legacy").strip().lower()
        if runtime != VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1.value:
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat process requires VOICE_RUNTIME=pipecat_smallwebrtc_v1"
            )
        profile_id = source.get(
            "VOICE_V2_PROFILE_ID",
            PIPECAT_DIRECT_CASCADE_PROFILE_ID,
        ).strip()
        if profile_id != PIPECAT_DIRECT_CASCADE_PROFILE_ID:
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat process requires its exact direct-cascade profile"
            )

        ttl_seconds = _environment_int(source, "VOICE_V2_TOKEN_TTL_SECONDS", 300)
        repository_timeout = _environment_float(
            source,
            "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS",
            2.0,
        )
        operation_timeout = _environment_float(
            source,
            "VOICE_V2_CONNECT_TIMEOUT_SECONDS",
            10.0,
        )
        cleanup_timeout = _environment_float(
            source,
            "VOICE_V2_CONTROL_PLANE_TIMEOUT_SECONDS",
            5.0,
        )
        allowed_origins = _environment_origins(source)
        try:
            signaling = PipecatSignalingSettings(
                signaling_base_url=source.get(
                    "PIPECAT_SIGNALING_BASE_URL",
                    _DEFAULT_SIGNALING_BASE_URL,
                ).strip(),
                profile_id=profile_id,
                reservation_ttl_seconds=ttl_seconds,
                repository_timeout_seconds=repository_timeout,
                signaling_timeout_seconds=operation_timeout,
                cleanup_timeout_seconds=cleanup_timeout,
                max_reservations=_environment_int(
                    source,
                    "VOICE_V2_MAX_CALL_ASSIGNMENTS",
                    10_000,
                ),
                max_active_calls=_environment_int(
                    source,
                    "VOICE_V2_MAX_ACTIVE_CALLS",
                    1,
                ),
                allowed_origins=allowed_origins,
            )
            bootstrap = PipecatBootstrapSettings(
                profile_id=profile_id,
                assignment_ttl_seconds=ttl_seconds,
                repository_timeout_seconds=repository_timeout,
                operation_timeout_seconds=operation_timeout,
                coordination_timeout_seconds=cleanup_timeout,
                max_concurrent_bootstraps=_environment_int(
                    source,
                    "VOICE_V2_MAX_CONCURRENT_BOOTSTRAPS",
                    100,
                ),
                max_active_calls=_environment_int(
                    source,
                    "VOICE_V2_MAX_ACTIVE_CALLS",
                    1,
                ),
                max_call_assignments=_environment_int(
                    source,
                    "VOICE_V2_MAX_CALL_ASSIGNMENTS",
                    10_000,
                ),
            )
            cascade = PipecatCascadeSettings(
                profile_id=profile_id,
                deepgram_api_key=source.get("DEEPGRAM_KEY", "").strip(),
                groq_api_key=source.get("GROQ_API_KEY", "").strip(),
                elevenlabs_api_key=source.get("ELEVENLABS_API_KEY", "").strip(),
                elevenlabs_voice_id=source.get("ELEVENLABS_VOICE_ID", "").strip(),
                probe_timeout_seconds=_environment_float(
                    source,
                    "VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS",
                    4.0,
                ),
            )
            return cls(
                signaling=signaling,
                bootstrap=bootstrap,
                cascade=cascade,
                projection_cleanup_timeout_seconds=_environment_float(
                    source,
                    "PIPECAT_PROJECTION_CLEANUP_TIMEOUT_SECONDS",
                    5.0,
                ),
                runtime_cleanup_timeout_seconds=cleanup_timeout,
                runtime_readiness_timeout_seconds=operation_timeout,
                active_call_idle_timeout_seconds=_environment_float(
                    source,
                    "PIPECAT_ACTIVE_CALL_IDLE_TIMEOUT_SECONDS",
                    300.0,
                ),
            )
        except (TypeError, ValueError):
            raise PipecatCompositionUnavailable(
                "The dedicated Pipecat process configuration is invalid"
            ) from None


class RepositoryPipecatScopeFactory:
    """Build one trusted provider scope from authoritative persistent records."""

    def __init__(
        self,
        *,
        profile_id: str,
        repository_timeout_seconds: float,
        session_repo: SessionRepository = SessionRepo,
        agent_repo: AgentRepository = AgentRepo,
        repository_runner: BoundedSyncRunner = default_repository_runner,
    ) -> None:
        self._profile_id = profile_id
        self._repository_timeout_seconds = repository_timeout_seconds
        self._session_repo = session_repo
        self._agent_repo = agent_repo
        self._repository_runner = repository_runner

    async def __call__(self, claims: VoiceCallClaims) -> VoiceProfileScope:
        session = await self._lookup(self._session_repo.get_by_id, claims.session_id)
        if session is None:
            raise PipecatCompositionUnavailable("Pipecat profile scope is unavailable")
        agent = await self._lookup(self._agent_repo.get_by_id, claims.agent_id)
        if agent is None:
            raise PipecatCompositionUnavailable("Pipecat profile scope is unavailable")
        if (
            claims.runtime is not VoiceRuntimeKind.PIPECAT_SMALLWEBRTC_V1
            or claims.profile_id != self._profile_id
            or session.id != claims.session_id
            or session.user_id != claims.user_id
            or session.agent_id != claims.agent_id
            or agent.id != claims.agent_id
            or agent.user_id != claims.user_id
            or not isinstance(agent.system_prompt, str)
            or not agent.system_prompt.strip()
        ):
            raise PipecatCompositionUnavailable("Pipecat profile scope is unavailable")
        return VoiceProfileScope(
            profile_id=self._profile_id,
            user_id=claims.user_id,
            session_id=claims.session_id,
            agent_id=claims.agent_id,
            voice_call_id=claims.voice_call_id,
            trace_id=claims.trace_id,
            system_prompt=agent.system_prompt,
        )

    async def _lookup(
        self,
        lookup: Callable[[str], _RepositoryResult],
        identifier: str,
    ) -> _RepositoryResult:
        try:
            return await self._repository_runner.run(
                lookup,
                identifier,
                timeout_seconds=self._repository_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, BoundedSyncRunnerUnavailable) as exc:
            raise PipecatCompositionUnavailable(
                "Pipecat profile repository is unavailable"
            ) from exc
        except Exception as exc:
            raise PipecatCompositionUnavailable(
                "Pipecat profile repository is unavailable"
            ) from exc


class PipecatApplicationComposition:
    """Own bootstrap, signaling, projection rollback, and ordered shutdown."""

    def __init__(
        self,
        bootstrap_service: PipecatBootstrapControl,
        signaling_service: PipecatSignalingControl,
        cors: PipecatCorsContract,
        *,
        projection_cleanup_timeout_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(projection_cleanup_timeout_seconds, bool)
            or not isinstance(projection_cleanup_timeout_seconds, int | float)
            or not math.isfinite(projection_cleanup_timeout_seconds)
            or not 0 < projection_cleanup_timeout_seconds <= 15
        ):
            raise ValueError("Pipecat projection cleanup timeout is invalid")
        if not isinstance(cors, PipecatCorsContract):
            raise TypeError("Pipecat composition requires a strict CORS contract")
        self.bootstrap_service = bootstrap_service
        self.signaling_service = signaling_service
        self.cors = cors
        self._projection_cleanup_timeout_seconds = projection_cleanup_timeout_seconds
        self._projection_cleanup_tasks: set[asyncio.Task[object]] = set()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    async def bootstrap_browser_assignment(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> PipecatBrowserVoiceAssignment:
        result = await self.bootstrap_service.bootstrap(
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )
        assignment = getattr(result, "assignment", None)
        ice_lease = getattr(result, "ice_lease", None)
        try:
            if not isinstance(assignment, PipecatVoiceRuntimeAssignment) or not isinstance(
                ice_lease,
                PipecatIceLease,
            ):
                raise PipecatRuntimeProjectionUnavailable("Pipecat assignment is unavailable")
            return project_pipecat_assignment_for_browser(
                authenticated_user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
                assignment=assignment,
                ice_lease=ice_lease,
            )
        except asyncio.CancelledError:
            raise
        except PipecatRuntimeProjectionForbidden:
            await self._bounded_projection_release(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            raise
        except PipecatRuntimeProjectionUnavailable:
            await self._bounded_projection_release(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            raise
        except Exception as exc:
            await self._bounded_projection_release(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            raise PipecatRuntimeProjectionUnavailable("Pipecat assignment is unavailable") from exc

    async def release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> VoiceRuntimeTerminalResult | None:
        return await self.bootstrap_service.release(
            user_id=user_id,
            session_id=session_id,
            voice_call_id=voice_call_id,
        )

    async def offer(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatOfferRequest,
    ) -> PipecatOfferAnswer:
        return await self.signaling_service.offer(
            token=token,
            user_id=user_id,
            request=request,
        )

    async def patch(
        self,
        *,
        token: str,
        user_id: str,
        request: PipecatPatchRequest,
    ) -> None:
        await self.signaling_service.patch(
            token=token,
            user_id=user_id,
            request=request,
        )

    async def delete(
        self,
        *,
        token: str,
        user_id: str,
        pc_id: str | None,
    ) -> VoiceRuntimeTerminalResult:
        return await self.signaling_service.delete(
            token=token,
            user_id=user_id,
            pc_id=pc_id,
        )

    async def aclose(self) -> None:
        """Hand ordered shutdown to one owned, shielded, retryable task."""

        async with self._close_lock:
            if self._closed:
                return
            task = self._close_task
            if task is None or task.cancelled() or (task.done() and task.exception() is not None):
                task = asyncio.create_task(
                    self._close_owned(),
                    name="pipecat-application-composition-close",
                )
                task.add_done_callback(_consume_task_result)
                self._close_task = task
        await asyncio.shield(task)

    async def _close_owned(self) -> None:
        """Close bootstrap before signaling and never report partial success."""

        if self._closed:
            return
        try:
            await self.bootstrap_service.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PipecatCompositionUnavailable(
                "Pipecat bootstrap shutdown is incomplete"
            ) from None
        try:
            await self.signaling_service.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PipecatCompositionUnavailable(
                "Pipecat signaling shutdown is incomplete"
            ) from None
        cleanup_tasks = tuple(self._projection_cleanup_tasks)
        for task in cleanup_tasks:
            task.cancel()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        self._closed = True

    async def _bounded_projection_release(
        self,
        *,
        user_id: str,
        session_id: str,
        voice_call_id: str,
    ) -> None:
        task = asyncio.create_task(
            self.bootstrap_service.release(
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            ),
            name=f"pipecat-projection-release:{voice_call_id}",
        )
        self._projection_cleanup_tasks.add(task)
        task.add_done_callback(self._projection_cleanup_done)
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._projection_cleanup_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The owned task remains live after the bounded HTTP wait. Bootstrap
            # itself preserves negative intent and cancellation-safe handoff.
            return

    def _projection_cleanup_done(self, task: asyncio.Task[object]) -> None:
        self._projection_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        task.exception()


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    task.exception()


def create_pipecat_peer_handler(ice_lease: PipecatIceLease) -> PipecatPeerHandler:
    """Construct the pinned SDK handler from one exact immutable lease."""

    _disable_unsafe_pipecat_request_logging()
    from pipecat.transports.smallwebrtc.request_handler import (
        ConnectionMode,
        SmallWebRTCRequestHandler,
    )

    _install_pipecat_connection_warning_floor()
    handler = SmallWebRTCRequestHandler(
        ice_servers=ice_lease.to_pipecat_ice_servers(),
        connection_mode=ConnectionMode.SINGLE,
    )
    return PipecatPeerHandlerAdapter(handler)


def create_pipecat_composition(
    settings: PipecatCompositionSettings,
    *,
    ice_lease_issuer: PipecatIceLeaseIssuer | None = None,
    profile_provider: PipecatProfileProvider | None = None,
    handler_factory: Callable[[PipecatIceLease], PipecatPeerHandler] | None = None,
    session_repo: SessionRepository = SessionRepo,
    agent_repo: AgentRepository = AgentRepo,
    repository_runner: BoundedSyncRunner = default_repository_runner,
) -> PipecatApplicationComposition:
    """Wire one same-process bootstrap, handler, runtime, and signaling owner."""

    _disable_unsafe_pipecat_request_logging()
    if profile_provider is None:
        if settings.cascade is None:
            raise PipecatCompositionUnavailable(
                "Pipecat composition requires an explicit profile provider"
            )
        provider = build_pipecat_cascade_provider(settings.cascade)
    else:
        provider = profile_provider
    scope_factory = RepositoryPipecatScopeFactory(
        profile_id=settings.bootstrap.profile_id,
        repository_timeout_seconds=settings.bootstrap.repository_timeout_seconds,
        session_repo=session_repo,
        agent_repo=agent_repo,
        repository_runner=repository_runner,
    )
    runtime_starter = PipecatRuntimeStarter(
        provider,
        scope_factory,
        cleanup_timeout_seconds=settings.runtime_cleanup_timeout_seconds,
        readiness_timeout_seconds=settings.runtime_readiness_timeout_seconds,
        active_call_idle_timeout_seconds=settings.active_call_idle_timeout_seconds,
    )
    signaling = PipecatSignalingService(
        settings.signaling,
        handler_factory=handler_factory or create_pipecat_peer_handler,
        runtime_starter=runtime_starter,
        session_repo=session_repo,
        agent_repo=agent_repo,
        repository_runner=repository_runner,
    )
    cors = signaling.cors
    if cors is None:  # protected by PipecatCompositionSettings
        raise PipecatCompositionUnavailable("Pipecat browser origins are unavailable")
    issuer = resolve_pipecat_ice_lease_issuer(
        settings.signaling.signaling_base_url,
        ice_lease_issuer,
    )
    bootstrap = PipecatBootstrapService(
        settings.bootstrap,
        signaling=signaling,
        ice_lease_issuer=issuer,
        session_repo=session_repo,
        agent_repo=agent_repo,
        repository_runner=repository_runner,
    )
    return PipecatApplicationComposition(
        bootstrap,
        signaling,
        cors,
        projection_cleanup_timeout_seconds=settings.projection_cleanup_timeout_seconds,
    )


def _disable_unsafe_pipecat_request_logging() -> None:
    """Suppress pinned SDK namespaces that log full SDP or ICE credentials."""

    from loguru import logger as pipecat_logger

    pipecat_logger.disable(_PIPECAT_REQUEST_HANDLER_LOG_NAMESPACE)
    aiortc_logger = logging.getLogger(_AIORTC_PEER_CONNECTION_LOG_NAMESPACE)
    if aiortc_logger.level < logging.WARNING:
        aiortc_logger.setLevel(logging.WARNING)


def _install_pipecat_connection_warning_floor() -> None:
    """Suppress pinned connection details below WARNING without muting failures."""

    from pipecat.transports.smallwebrtc import connection

    if isinstance(connection.logger, _PipecatConnectionWarningFloorLogger):
        return
    connection.logger = _PipecatConnectionWarningFloorLogger(connection.logger)


def _environment_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        raise PipecatCompositionUnavailable(
            "The dedicated Pipecat process configuration is invalid"
        ) from None


def _environment_float(source: Mapping[str, str], name: str, default: float) -> float:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        raise PipecatCompositionUnavailable(
            "The dedicated Pipecat process configuration is invalid"
        ) from None


def _environment_origins(source: Mapping[str, str]) -> tuple[str, ...]:
    raw_value = source.get("ALLOWED_CORS_ORIGINS", "http://localhost:3000")
    origins = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    if not origins:
        raise PipecatCompositionUnavailable("Pipecat browser origins are unavailable")
    return origins


def _require_secure_browser_origins(origins: tuple[str, ...]) -> None:
    for origin in origins:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        if parsed.scheme != "http":
            continue
        if hostname is not None and (
            hostname.casefold() == "localhost" or _is_loopback_ip(hostname)
        ):
            continue
        raise ValueError("Pipecat browser origins require HTTPS or loopback HTTP")


def _is_loopback_ip(hostname: str) -> bool:
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


__all__ = [
    "AgentRepository",
    "PipecatApplicationComposition",
    "PipecatBootstrapControl",
    "PipecatCompositionSettings",
    "PipecatCompositionUnavailable",
    "PipecatSignalingControl",
    "RepositoryPipecatScopeFactory",
    "SessionRepository",
    "create_pipecat_composition",
    "create_pipecat_peer_handler",
]
