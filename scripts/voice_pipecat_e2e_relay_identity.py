"""Fail-closed relay identity owner for the guarded Pipecat E2E app.

This module is deliberately imported only after the app has validated its
environment.  Risky gateway discovery, TURN credential construction, and
prebootstrap failure handling complete behind non-raising scrub workers so a
clean boundary never retains raw topology or lease material in traceback
locals.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from aioice.ice import get_host_addresses
from fastapi import Request
from murmur.api.pipecat_schemas import PipecatSessionRequest
from murmur.api.routers.pipecat_voice import PipecatHttpError, _read_dto
from murmur.voice.pipecat_bootstrap import (
    PipecatBootstrapConflict,
    PipecatBootstrapForbidden,
    PipecatBootstrapNotFound,
)
from murmur.voice.pipecat_ice import (
    PipecatIceLease,
    PipecatIceLeaseUnavailable,
    PipecatIceServer,
)
from murmur.voice.runtime_contracts import VoiceCallClaims

from scripts.voice_pipecat_e2e_coturn import (
    COTURN_TURNS_URL,
    CoturnBridgeTopology,
    derive_turn_rest_credentials,
)

_MAX_AIOICE_HOST_ADDRESSES = 256
_PREBOOTSTRAP_RELEASE_ATTEMPT_TIMEOUT_SECONDS = 15.0
_PREBOOTSTRAP_RELEASE_RETRY_BACKOFF_SECONDS = 0.1


class _BootstrapService(Protocol):
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
    ) -> object: ...


class _Composition(Protocol):
    bootstrap_service: _BootstrapService


def _scrub_exception_graph(error: BaseException) -> BaseException:
    """Detach recursive exception state before a safe boundary re-raises."""

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        linked = (current.__cause__, current.__context__)
        grouped = getattr(current, "exceptions", ())
        pending.extend(item for item in linked if isinstance(item, BaseException))
        if isinstance(grouped, tuple):
            pending.extend(item for item in grouped if isinstance(item, BaseException))
        trace = current.__traceback__
        current.__traceback__ = None
        current.__cause__ = None
        current.__context__ = None
        current.__suppress_context__ = True
        if trace is not None:
            try:
                traceback.clear_frames(trace)
            except BaseException as scrub_error:
                scrub_error.__traceback__ = None
                scrub_error.__cause__ = None
                scrub_error.__context__ = None
                scrub_error = None
        trace = None
        linked = ()
        grouped = ()
        current = None
    pending.clear()
    seen.clear()
    return error


def _build_relay_ice_lease(
    *,
    static_auth_secret: str,
    claims: VoiceCallClaims,
) -> tuple[PipecatIceLease | None, BaseException | None]:
    """Build a lease without letting credential-bearing locals enter a traceback."""

    credentials = None
    lease = None
    interrupt = None
    try:
        credentials = derive_turn_rest_credentials(
            static_auth_secret=static_auth_secret,
            voice_call_id=claims.voice_call_id,
            expires_at=claims.expires_at,
            now=datetime.now(UTC),
        )
        lease = PipecatIceLease(
            claims=claims,
            provider_id="e2e-coturn-rest-v1",
            expires_at=claims.expires_at,
            ice_servers=(
                PipecatIceServer(
                    urls=(COTURN_TURNS_URL,),
                    username=credentials.username,
                    credential=credentials.credential,
                ),
            ),
        )
    except BaseException as error:
        if not isinstance(error, Exception):
            interrupt = _scrub_exception_graph(error)
        else:
            _scrub_exception_graph(error)
        error = None
    outcome = (lease, interrupt)
    credentials = None
    lease = None
    interrupt = None
    static_auth_secret = ""
    claims = None
    return outcome


class RelayTlsIceLeaseIssuer:
    """Issue exactly one expected-call relay lease without reflective state."""

    __slots__ = ("_expected_voice_call_id", "_issue_count", "_static_auth_secret")

    def __init__(self, static_auth_secret: str, expected_voice_call_id: str) -> None:
        self._static_auth_secret = static_auth_secret
        self._expected_voice_call_id = expected_voice_call_id
        self._issue_count = 0

    @property
    def issue_count(self) -> int:
        return self._issue_count

    async def issue(self, claims: VoiceCallClaims) -> PipecatIceLease:
        issuer = self
        failed = False
        interrupt = None
        static_auth_secret = ""
        try:
            failed = (
                not hmac.compare_digest(
                    claims.voice_call_id.encode("ascii"),
                    issuer._expected_voice_call_id.encode("ascii"),
                )
                or issuer._issue_count != 0
            )
            if not failed:
                issuer._issue_count = 1
                static_auth_secret = issuer._static_auth_secret
        except BaseException as error:
            failed = True
            if not isinstance(error, Exception):
                interrupt = _scrub_exception_graph(error)
            else:
                _scrub_exception_graph(error)
            error = None
        issuer = None
        self = None
        if interrupt is not None:
            claims = None
            error_to_raise = interrupt
            interrupt = None
            static_auth_secret = ""
            raise error_to_raise from None
        if failed:
            claims = None
            static_auth_secret = ""
            raise PipecatIceLeaseUnavailable("Pipecat relay ICE lease is unavailable") from None
        lease, interrupt = _build_relay_ice_lease(
            static_auth_secret=static_auth_secret,
            claims=claims,
        )
        claims = None
        static_auth_secret = ""
        if interrupt is not None:
            lease = None
            error_to_raise = interrupt
            interrupt = None
            raise error_to_raise from None
        if lease is None:
            raise PipecatIceLeaseUnavailable("Pipecat relay ICE lease is unavailable") from None
        return lease

    def __repr__(self) -> str:
        return "RelayTlsIceLeaseIssuer()"


def _probe_aioice_gateway(
    topology: CoturnBridgeTopology,
    address_reader: Callable[[bool, bool], object],
) -> tuple[bool, BaseException | None]:
    """Return only scrubbed probe state so failures can use a clean boundary."""

    addresses: object = None
    canonical: tuple[str, ...] = ()
    parsed: list[str] | None = None
    iterator = None
    value = None
    address = None
    success = False
    interrupt = None
    try:
        addresses = address_reader(True, False)
        if type(addresses) is list and 1 <= len(addresses) <= _MAX_AIOICE_HOST_ADDRESSES:
            parsed = []
            iterator = iter(addresses)
            for value in iterator:
                if type(value) is not str:
                    break
                address = ipaddress.IPv4Address(value)
                if str(address) != value:
                    break
                parsed.append(value)
            else:
                canonical = tuple(parsed)
                success = (
                    canonical.count(str(topology.gateway)) == 1
                    and str(topology.container) not in canonical
                )
    except BaseException as error:
        if not isinstance(error, Exception):
            interrupt = _scrub_exception_graph(error)
        else:
            _scrub_exception_graph(error)
        error = None
    outcome = (success, interrupt)
    if parsed is not None:
        parsed.clear()
    addresses = None
    canonical = ()
    parsed = None
    iterator = None
    value = None
    address = None
    address_reader = None
    topology = None
    success = False
    interrupt = None
    return outcome


def require_aioice_gateway(
    topology: CoturnBridgeTopology,
    *,
    address_reader: Callable[[bool, bool], object] = get_host_addresses,
) -> None:
    """Require the owned bridge gateway in aioice's real gatherable set."""

    success, interrupt = _probe_aioice_gateway(topology, address_reader)
    address_reader = None
    if interrupt is not None:
        error_to_raise = interrupt
        interrupt = None
        success = False
        topology = None
        raise error_to_raise from None
    if not success:
        topology = None
        raise RuntimeError("relay-tls Pipecat E2E gateway is unavailable") from None


class _PrebootstrapFailure(Enum):
    FORBIDDEN = (403, "Forbidden")
    NOT_FOUND = (404, "Voice session was not found")
    CONFLICT = (409, "Voice call conflicts with retained state")
    UNAVAILABLE = (503, "Voice assignment is unavailable")


def _classify_prebootstrap_failure(error: BaseException) -> _PrebootstrapFailure:
    if isinstance(error, PipecatBootstrapForbidden):
        return _PrebootstrapFailure.FORBIDDEN
    if isinstance(error, PipecatBootstrapNotFound):
        return _PrebootstrapFailure.NOT_FOUND
    if isinstance(error, PipecatBootstrapConflict):
        return _PrebootstrapFailure.CONFLICT
    return _PrebootstrapFailure.UNAVAILABLE


async def _release_cancelled_prebootstrap_until_settled(
    bootstrap_service: _BootstrapService,
    *,
    user_id: str,
    session_id: str,
    voice_call_id: str,
) -> None:
    """Retry the idempotent trusted release until it authoritatively settles."""

    while True:
        released = False
        try:
            await asyncio.wait_for(
                bootstrap_service.release(
                    user_id=user_id,
                    session_id=session_id,
                    voice_call_id=voice_call_id,
                ),
                timeout=_PREBOOTSTRAP_RELEASE_ATTEMPT_TIMEOUT_SECONDS,
            )
            released = True
        except asyncio.CancelledError as error:
            detached_interrupt = _scrub_exception_graph(error)
            error = None
            bootstrap_service = None
            user_id = ""
            session_id = ""
            voice_call_id = ""
            raise detached_interrupt from None
        except BaseException as error:
            _scrub_exception_graph(error)
            error = None
        if released:
            bootstrap_service = None
            user_id = ""
            session_id = ""
            voice_call_id = ""
            return
        try:
            await asyncio.sleep(_PREBOOTSTRAP_RELEASE_RETRY_BACKOFF_SECONDS)
        except asyncio.CancelledError as error:
            detached_interrupt = _scrub_exception_graph(error)
            error = None
            bootstrap_service = None
            user_id = ""
            session_id = ""
            voice_call_id = ""
            raise detached_interrupt from None


async def _settle_cancelled_prebootstrap(
    bootstrap_service: _BootstrapService,
    *,
    user_id: str,
    session_id: str,
    voice_call_id: str,
) -> None:
    """Do not return cancellation while the trusted cleanup owner is live."""

    cleanup_task = None
    try:
        cleanup_task = asyncio.create_task(
            _release_cancelled_prebootstrap_until_settled(
                bootstrap_service,
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            ),
            name="pipecat-e2e-prebootstrap-cleanup",
        )
    except BaseException as error:
        _scrub_exception_graph(error)
        error = None
    while cleanup_task is not None and not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except BaseException as error:
            _scrub_exception_graph(error)
            error = None
    if cleanup_task is not None:
        try:
            cleanup_task.result()
        except BaseException as error:
            _scrub_exception_graph(error)
            error = None
    cleanup_task = None
    bootstrap_service = None
    user_id = ""
    session_id = ""
    voice_call_id = ""


@dataclass(frozen=True)
class _PrebootstrapOwner:
    composition: _Composition = field(repr=False)
    expected_voice_call_id: str = field(repr=False)
    user_id: str = field(repr=False)
    session_id: str = field(repr=False)


_PREBOOTSTRAP_OWNERS: dict[int, _PrebootstrapOwner] = {}
_NEXT_PREBOOTSTRAP_OWNER_KEY = 1


class _RelayPrebootstrapHandler:
    __slots__ = ("_owner_key",)

    def __init__(self, owner_key: int) -> None:
        self._owner_key = owner_key

    async def __call__(self, request: Request) -> dict[str, object]:
        body = await _read_dto(request, PipecatSessionRequest)
        owner = _PREBOOTSTRAP_OWNERS.get(self._owner_key)
        failure = None
        if owner is None:
            failure = _PrebootstrapFailure.UNAVAILABLE
        identity = getattr(request.state, "pipecat_user", None)
        if failure is None and (
            not isinstance(identity, Mapping) or identity.get("id") != owner.user_id
        ):
            failure = _PrebootstrapFailure.FORBIDDEN
        if failure is None and (
            body.session_id != owner.session_id
            or not hmac.compare_digest(
                body.voice_call_id.encode("ascii"),
                owner.expected_voice_call_id.encode("ascii"),
            )
        ):
            failure = _PrebootstrapFailure.NOT_FOUND
        if failure is not None:
            status_code, message = failure.value
            body = None
            owner = None
            request = None
            self = None
            identity = None
            failure = None
            raise PipecatHttpError(status_code, message) from None

        bootstrap_service = owner.composition.bootstrap_service
        user_id = owner.user_id
        result = None
        interrupt = None
        try:
            result = await bootstrap_service.bootstrap(
                user_id=user_id,
                session_id=body.session_id,
                voice_call_id=body.voice_call_id,
            )
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError) or not isinstance(error, Exception):
                interrupt = _scrub_exception_graph(error)
            else:
                failure = _classify_prebootstrap_failure(error)
                _scrub_exception_graph(error)
            error = None

        expires_at_epoch_seconds = 0
        if result is not None and failure is None and interrupt is None:
            try:
                expires_at_epoch_seconds = int(result.assignment.expires_at.timestamp())
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError) or not isinstance(error, Exception):
                    interrupt = _scrub_exception_graph(error)
                else:
                    failure = _PrebootstrapFailure.UNAVAILABLE
                    _scrub_exception_graph(error)
                error = None
            if expires_at_epoch_seconds <= 0 and failure is None and interrupt is None:
                failure = _PrebootstrapFailure.UNAVAILABLE

        if interrupt is not None:
            session_id = body.session_id
            voice_call_id = body.voice_call_id
            result = None
            body = None
            owner = None
            request = None
            identity = None
            await _settle_cancelled_prebootstrap(
                bootstrap_service,
                user_id=user_id,
                session_id=session_id,
                voice_call_id=voice_call_id,
            )
            bootstrap_service = None
            user_id = ""
            session_id = ""
            voice_call_id = ""
            self = None
            error_to_raise = interrupt
            interrupt = None
            failure = None
            raise error_to_raise from None

        if failure is not None:
            status_code, message = failure.value
            result = None
            body = None
            owner = None
            request = None
            identity = None
            bootstrap_service = None
            user_id = ""
            self = None
            failure = None
            raise PipecatHttpError(status_code, message) from None

        result = None
        return {
            "schema_version": 1,
            "status": "prepared",
            "expires_at_epoch_seconds": expires_at_epoch_seconds,
        }

    def __repr__(self) -> str:
        return "_RelayPrebootstrapHandler()"


def create_relay_prebootstrap_handler(
    *,
    composition: _Composition,
    expected_voice_call_id: str,
    user_id: str,
    session_id: str,
) -> Callable[[Request], object]:
    """Retain the sensitive owner outside the request traceback boundary."""

    global _NEXT_PREBOOTSTRAP_OWNER_KEY

    owner_key = _NEXT_PREBOOTSTRAP_OWNER_KEY
    _NEXT_PREBOOTSTRAP_OWNER_KEY += 1
    _PREBOOTSTRAP_OWNERS[owner_key] = _PrebootstrapOwner(
        composition=composition,
        expected_voice_call_id=expected_voice_call_id,
        user_id=user_id,
        session_id=session_id,
    )
    return _RelayPrebootstrapHandler(owner_key)


__all__ = [
    "RelayTlsIceLeaseIssuer",
    "create_relay_prebootstrap_handler",
    "require_aioice_gateway",
]
