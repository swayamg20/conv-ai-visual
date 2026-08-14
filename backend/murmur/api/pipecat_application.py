"""Dedicated, authenticated ASGI application for Pipecat SmallWebRTC."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import logging
import math
import threading
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from urllib.parse import urlsplit

import firebase_admin
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from murmur.api.routers.pipecat_voice import (
    DEFAULT_MAX_PIPECAT_REQUEST_BODY_BYTES,
    PipecatHttpComposition,
    PipecatHttpError,
    router,
)
from murmur.core.config import config
from murmur.persistence.repositories.identities import UserRepo
from murmur.voice.blocking import BoundedSyncRunner, BoundedSyncRunnerUnavailable
from murmur.voice.pipecat_signaling import PipecatCorsContract

logger = logging.getLogger(__name__)

_FIREBASE_APP_NAME = "murmur-pipecat"
_MAX_FIREBASE_TOKEN_LENGTH = 16_000
_MAX_USER_ID_LENGTH = 128
_firebase_lock = threading.Lock()
_user_provision_lock = threading.Lock()
_firebase_app: firebase_admin.App | None = None

PipecatAuthenticator = Callable[[Request], Awaitable[Mapping[str, object] | None]]


class PipecatAuthenticationUnavailable(RuntimeError):
    """Firebase verification or bounded user provisioning is unavailable."""


class PipecatApplicationShutdownError(RuntimeError):
    """One or more dedicated application owners did not close cleanly."""


class PipecatApplicationStartupError(RuntimeError):
    """The dedicated application could not initialize without exposing details."""


class FirebasePipecatAuthenticator:
    """Verify Firebase and provision identity without logging token failures."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 2.0,
        runner: BoundedSyncRunner | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 15
        ):
            raise ValueError("Pipecat authentication timeout is invalid")
        self._timeout_seconds = timeout_seconds
        self._runner = runner or BoundedSyncRunner(
            max_workers=8,
            thread_name_prefix="pipecat-auth",
        )
        self._owns_runner = runner is None

    async def __call__(self, request: Request) -> Mapping[str, object] | None:
        token = _firebase_bearer(request)
        if token is None:
            return None
        try:
            return await self._runner.run(
                _verify_and_provision_firebase_user,
                token,
                timeout_seconds=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, BoundedSyncRunnerUnavailable) as exc:
            logger.warning("Pipecat authentication capacity is unavailable")
            raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable") from exc
        except PipecatAuthenticationUnavailable:
            logger.warning("Pipecat authentication is unavailable")
            raise
        except Exception as exc:
            logger.warning("Pipecat authentication is unavailable")
            raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable") from exc

    async def aclose(self) -> None:
        if self._owns_runner:
            await self._runner.aclose()


class _SafeExceptionMiddleware:
    """Convert unexpected route failures before CORS sees the response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Dedicated Pipecat request failed unexpectedly")
            await _safe_json_response(500, "Internal server error")(scope, receive, send)


class _AuthenticationMiddleware:
    """Authenticate before the downstream route can receive any body bytes."""

    def __init__(self, app: ASGIApp, *, authenticator: PipecatAuthenticator) -> None:
        self._app = app
        self._authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        try:
            identity = await self._authenticator(request)
        except asyncio.CancelledError:
            raise
        except PipecatAuthenticationUnavailable:
            await _safe_json_response(503, "Authentication is unavailable")(
                scope,
                receive,
                send,
            )
            return
        except Exception:
            logger.error("Dedicated Pipecat authentication failed unexpectedly")
            await _safe_json_response(503, "Authentication is unavailable")(
                scope,
                receive,
                send,
            )
            return
        user_id = identity.get("id") if isinstance(identity, Mapping) else None
        if (
            not isinstance(user_id, str)
            or not user_id
            or len(user_id) > _MAX_USER_ID_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in user_id)
        ):
            await _safe_json_response(401, "Not authenticated")(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        if not isinstance(state, dict):
            await _safe_json_response(500, "Internal server error")(scope, receive, send)
            return
        state["pipecat_user"] = MappingProxyType({"id": user_id})
        await self._app(scope, receive, send)


class _SecurityHeadersMiddleware:
    """Apply non-cacheable, non-referring response policy to every HTTP result."""

    _HEADERS = (
        (b"cache-control", b"no-store"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-content-type-options", b"nosniff"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                protected = {name for name, _value in self._HEADERS}
                existing = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in protected
                ]
                message["headers"] = [*existing, *self._HEADERS]
            await send(message)

        try:
            await self._app(scope, receive, send_with_headers)
        except asyncio.CancelledError:
            raise
        except Exception:
            if response_started:
                raise
            logger.error("Dedicated Pipecat HTTP boundary failed unexpectedly")
            await _safe_json_response(500, "Internal server error")(
                scope,
                receive,
                send_with_headers,
            )


def create_pipecat_application(
    composition: PipecatHttpComposition,
    *,
    authenticator: PipecatAuthenticator,
    database_initializer: Callable[[], object] | None = None,
    max_request_body_bytes: int = DEFAULT_MAX_PIPECAT_REQUEST_BODY_BYTES,
    body_read_timeout_seconds: float = 10.0,
) -> FastAPI:
    """Build the isolated ASGI app without mounting the legacy application."""

    cors = getattr(composition, "cors", None)
    if not isinstance(cors, PipecatCorsContract):
        raise TypeError("Pipecat application requires a strict CORS contract")
    _require_secure_browser_origins(cors)
    if (
        isinstance(max_request_body_bytes, bool)
        or not isinstance(max_request_body_bytes, int)
        or not 1_024 <= max_request_body_bytes <= 10_000_000
    ):
        raise ValueError("Pipecat request body limit is invalid")
    if not callable(authenticator):
        raise TypeError("Pipecat application requires an authenticator")
    if (
        isinstance(body_read_timeout_seconds, bool)
        or not isinstance(body_read_timeout_seconds, int | float)
        or not math.isfinite(body_read_timeout_seconds)
        or not 0 < body_read_timeout_seconds <= 30
    ):
        raise ValueError("Pipecat request body timeout is invalid")

    shutdown_lock = asyncio.Lock()
    shutdown_task: asyncio.Task[None] | None = None

    async def close_application() -> None:
        nonlocal shutdown_task
        async with shutdown_lock:
            task = shutdown_task
            if task is None or task.cancelled() or (task.done() and task.exception() is not None):
                task = asyncio.create_task(
                    _close_application_components(composition, authenticator),
                    name="pipecat-http-application-close",
                )
                task.add_done_callback(_consume_task_result)
                shutdown_task = task
        await asyncio.shield(task)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            if database_initializer is not None:
                await _initialize_application_database(database_initializer)
            yield
        finally:
            await close_application()

    app = FastAPI(
        lifespan=lifespan,
        debug=False,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.router.redirect_slashes = False
    app.state.pipecat_composition = composition
    app.state.pipecat_max_request_body_bytes = max_request_body_bytes
    app.state.pipecat_body_read_timeout_seconds = float(body_read_timeout_seconds)
    app.include_router(router)
    app.add_exception_handler(PipecatHttpError, _pipecat_http_error_handler)
    # add_middleware inserts at the front. The resulting request order is:
    # security headers -> CORS -> authentication -> safe errors -> routes.
    app.add_middleware(_SafeExceptionMiddleware)
    app.add_middleware(_AuthenticationMiddleware, authenticator=authenticator)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors.allowed_origins),
        allow_credentials=False,
        allow_methods=list(cors.allowed_methods),
        allow_headers=list(cors.allowed_headers),
        max_age=cors.max_age_seconds,
    )
    app.add_middleware(_SecurityHeadersMiddleware)
    return app


async def _pipecat_http_error_handler(
    _request: Request,
    exc: PipecatHttpError,
) -> JSONResponse:
    return _safe_json_response(exc.status_code, exc.message)


def _safe_json_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


async def _initialize_application_database(
    database_initializer: Callable[[], object],
) -> None:
    failed = False
    try:
        initialized = database_initializer()
        if inspect.isawaitable(initialized):
            await initialized
    except asyncio.CancelledError:
        raise
    except Exception:
        failed = True
    if failed:
        # This raise occurs outside the active handler so the original startup
        # exception cannot survive as an implicit traceback context.
        raise PipecatApplicationStartupError(
            "Dedicated Pipecat application startup failed"
        ) from None


async def _close_application_components(
    composition: PipecatHttpComposition,
    authenticator: PipecatAuthenticator,
) -> None:
    failed = False
    try:
        await composition.aclose()
    except asyncio.CancelledError:
        raise
    except Exception:
        failed = True
    close_authenticator = getattr(authenticator, "aclose", None)
    if callable(close_authenticator):
        try:
            result = close_authenticator()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
    if failed:
        raise PipecatApplicationShutdownError(
            "Dedicated Pipecat application shutdown is incomplete"
        ) from None


def _consume_task_result(task: asyncio.Task[object]) -> None:
    if task.cancelled():
        return
    task.exception()


def _firebase_bearer(request: Request) -> str | None:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer "):
        return None
    token = values[0][7:]
    if (
        not token
        or len(token) > _MAX_FIREBASE_TOKEN_LENGTH
        or token != token.strip()
        or any(character.isspace() for character in token)
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        return None
    return token


def _verify_and_provision_firebase_user(token: str) -> Mapping[str, object] | None:
    try:
        app = _get_pipecat_firebase_app()
    except Exception as exc:
        raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable") from exc
    try:
        claims = firebase_auth.verify_id_token(
            token,
            app=app,
            check_revoked=True,
        )
    except (
        firebase_auth.InvalidIdTokenError,
        firebase_auth.UserDisabledError,
        firebase_auth.UserNotFoundError,
    ):
        return None
    except Exception as exc:
        raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable") from exc
    uid = claims.get("uid") or claims.get("user_id")
    if (
        not isinstance(uid, str)
        or not uid
        or len(uid) > _MAX_USER_ID_LENGTH
        or uid != uid.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in uid)
    ):
        return None
    verified_email = _verified_firebase_email(claims)
    raw_name = claims.get("name")
    name = (
        raw_name
        if isinstance(raw_name, str)
        and len(raw_name) <= 256
        and not any(ord(character) < 32 or ord(character) == 127 for character in raw_name)
        else None
    )
    try:
        with _user_provision_lock:
            if verified_email is None:
                user = UserRepo.get_or_create_exact_uid(uid=uid, name=name)
                if user.id != uid:
                    raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable")
            else:
                existing = UserRepo.get_by_id(uid)
                user = UserRepo.get_or_create(
                    uid=uid,
                    email=verified_email,
                    name=name,
                )
                if existing is not None and user.id != uid:
                    raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable")
    except Exception as exc:
        if isinstance(exc, PipecatAuthenticationUnavailable):
            raise
        raise PipecatAuthenticationUnavailable("Pipecat authentication is unavailable") from exc
    return MappingProxyType({"id": user.id})


def _verified_firebase_email(claims: Mapping[str, object]) -> str | None:
    raw_email = claims.get("email")
    if claims.get("email_verified") is not True or not isinstance(raw_email, str):
        return None
    if (
        not raw_email
        or raw_email != raw_email.strip()
        or len(raw_email) > 320
        or raw_email.count("@") != 1
        or any(character.isspace() for character in raw_email)
        or any(ord(character) < 32 or ord(character) == 127 for character in raw_email)
    ):
        return None
    local_part, domain = raw_email.rsplit("@", 1)
    return raw_email if local_part and domain else None


def _require_secure_browser_origins(cors: PipecatCorsContract) -> None:
    for origin in cors.allowed_origins:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        if parsed.scheme == "http" and (hostname is None or not _is_loopback_hostname(hostname)):
            raise ValueError("Pipecat browser origins require HTTPS or loopback HTTP")


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _get_pipecat_firebase_app() -> firebase_admin.App:
    global _firebase_app
    with _firebase_lock:
        if _firebase_app is not None:
            return _firebase_app
        try:
            _firebase_app = firebase_admin.get_app(_FIREBASE_APP_NAME)
            return _firebase_app
        except ValueError:
            pass
        credential = (
            credentials.Certificate(config.FIREBASE_SERVICE_ACCOUNT_PATH)
            if config.FIREBASE_SERVICE_ACCOUNT_PATH
            else None
        )
        options = {"projectId": config.FIREBASE_PROJECT_ID} if config.FIREBASE_PROJECT_ID else None
        _firebase_app = firebase_admin.initialize_app(
            credential,
            options=options,
            name=_FIREBASE_APP_NAME,
        )
        return _firebase_app


__all__ = [
    "FirebasePipecatAuthenticator",
    "PipecatApplicationShutdownError",
    "PipecatApplicationStartupError",
    "PipecatAuthenticationUnavailable",
    "PipecatAuthenticator",
    "create_pipecat_application",
]
