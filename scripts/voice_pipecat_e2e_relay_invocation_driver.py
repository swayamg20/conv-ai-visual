"""Synthetic driver capability values for staged relay invocation tests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import (
    RelayPrebootstrapDestination,
    RelayPrebootstrapRequest,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    RelayChildRequest,
    RelayFinishRequest,
    RelayPlaywrightExitDestination,
)

_TOOLS_TOKEN = object()
_DRIVER_TOKEN = object()
_CHILD_DESTINATION_TOKEN = object()
_START_DESTINATION_TOKEN = object()
_START_RECEIPT_TOKEN = object()
_STOP_DESTINATION_TOKEN = object()
_STOP_RECEIPT_TOKEN = object()
_OWNER_DESTINATION_TOKEN = object()
_ROLES = frozenset({"app", "web", "browser"})


class _RelayChildAuthorityDestination:
    """Caller-owned publication slot that survives callback return loss."""

    __slots__ = ("_authority", "_lock", "_role")

    def __init__(self, token: object, *, role: str) -> None:
        if token is not _CHILD_DESTINATION_TOKEN or role not in _ROLES:
            raise TypeError("Relay child authority destination is factory-owned")
        self._role = role
        self._authority: object | None = None
        self._lock = threading.Lock()

    def publish(self, authority: object) -> None:
        with self._lock:
            if authority is None:
                raise TypeError("Relay child authority is invalid")
            if self._authority is None:
                self._authority = authority
            elif self._authority is not authority:
                raise TypeError("Relay child authority is invalid")

    def _read(self, role: str) -> object:
        with self._lock:
            if role != self._role or self._authority is None:
                raise TypeError("Relay child authority is unavailable")
            return self._authority

    def _peek(self) -> object | None:
        with self._lock:
            return self._authority

    def _clear(self, authority: object) -> bool:
        with self._lock:
            if self._authority is not authority:
                return self._authority is None
            self._authority = None
            return True

    def __copy__(self) -> None:
        raise TypeError("Relay child authority destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child authority destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child authority destination cannot be serialized")


class _RelayChildStartReceipt:
    """Private fixed proof that one exact child start committed."""

    __slots__ = ("_owner_token", "_role")

    def __init__(self, token: object, *, owner_token: object, role: str) -> None:
        if token is not _START_RECEIPT_TOKEN or role not in _ROLES:
            raise TypeError("Relay child start receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_role", role)

    def _matches(self, owner_token: object, role: str) -> bool:
        return self._owner_token is owner_token and self._role == role

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay child start receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay child start receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child start receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child start receipt cannot be serialized")


class _RelayChildStartDestination:
    """Preowned idempotent publication sink for one child start."""

    __slots__ = ("_lock", "_owner_token", "_receipt", "_role")

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        role: str,
    ) -> None:
        if token is not _START_DESTINATION_TOKEN or role not in _ROLES:
            raise TypeError("Relay child start destination is factory-owned")
        self._owner_token = owner_token
        self._role = role
        self._receipt: _RelayChildStartReceipt | None = None
        self._lock = threading.Lock()

    def publish(self, started: object) -> None:
        with self._lock:
            if started is not True:
                raise TypeError("Relay child start publication is invalid")
            if self._receipt is None:
                self._receipt = _RelayChildStartReceipt(
                    _START_RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                    role=self._role,
                )

    def _read(self, owner_token: object, role: str) -> _RelayChildStartReceipt:
        with self._lock:
            if type(self._receipt) is not _RelayChildStartReceipt or not self._receipt._matches(
                owner_token, role
            ):
                raise TypeError("Relay child start receipt is unavailable")
            return self._receipt

    def __copy__(self) -> None:
        raise TypeError("Relay child start destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child start destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child start destination cannot be serialized")


class _RelayChildStopReceipt:
    """Private fixed proof that one exact child stop committed."""

    __slots__ = ("_owner_token", "_role")

    def __init__(self, token: object, *, owner_token: object, role: str) -> None:
        if token is not _STOP_RECEIPT_TOKEN or role not in _ROLES:
            raise TypeError("Relay child stop receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_role", role)

    def _matches(self, owner_token: object, role: str) -> bool:
        return self._owner_token is owner_token and self._role == role

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay child stop receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay child stop receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child stop receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child stop receipt cannot be serialized")


class _RelayChildStopDestination:
    """Preowned idempotent publication sink for one child stop."""

    __slots__ = ("_lock", "_owner_token", "_receipt", "_role")

    def __init__(self, token: object, *, owner_token: object, role: str) -> None:
        if token is not _STOP_DESTINATION_TOKEN or role not in _ROLES:
            raise TypeError("Relay child stop destination is factory-owned")
        self._owner_token = owner_token
        self._role = role
        self._receipt: _RelayChildStopReceipt | None = None
        self._lock = threading.Lock()

    def publish(self, stopped: object) -> None:
        with self._lock:
            if stopped is not True:
                raise TypeError("Relay child stop publication is invalid")
            if self._receipt is None:
                self._receipt = _RelayChildStopReceipt(
                    _STOP_RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                    role=self._role,
                )

    def _read(self, owner_token: object, role: str) -> _RelayChildStopReceipt:
        with self._lock:
            if type(self._receipt) is not _RelayChildStopReceipt or not self._receipt._matches(
                owner_token, role
            ):
                raise TypeError("Relay child stop receipt is unavailable")
            return self._receipt

    def __copy__(self) -> None:
        raise TypeError("Relay child stop destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child stop destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child stop destination cannot be serialized")


class _RelayInvocationOwnerDestination:
    """Caller-preowned canonical publication sink for one aggregate owner."""

    __slots__ = ("_lock", "_record")

    def __init__(self, token: object) -> None:
        if token is not _OWNER_DESTINATION_TOKEN:
            raise TypeError("Relay invocation owner destination is factory-owned")
        self._lock = threading.RLock()
        self._record: (
            tuple[
                object,
                RelayInvocationDriver,
                RelayInvocationTools,
                object,
                bool,
            ]
            | None
        ) = None

    def _publish_owner(
        self,
        run: object,
        driver: RelayInvocationDriver,
        tools: RelayInvocationTools,
        owner: object,
    ) -> None:
        with self._lock:
            record = self._record
            if record is None:
                self._record = (run, driver, tools, owner, False)
                return None
            elif not all(
                (record[0] is run, record[1] is driver, record[2] is tools, record[3] is owner)
            ):
                raise TypeError("Relay invocation owner publication is invalid")

    def _publish_ready(self, owner: object) -> None:
        with self._lock:
            record = self._record
            if record is None or record[3] is not owner:
                raise TypeError("Relay invocation owner publication is invalid")
            self._record = (*record[:4], True)
            return None

    def _read(
        self,
        run: object,
        driver: RelayInvocationDriver,
        tools: RelayInvocationTools,
    ) -> tuple[object | None, bool]:
        with self._lock:
            record = self._record
            if record is None:
                return None, False
            if not all((record[0] is run, record[1] is driver, record[2] is tools)):
                raise TypeError("Relay invocation owner publication is invalid")
            return record[3], record[4]

    def _clear(self, owner: object) -> bool:
        with self._lock:
            record = self._record
            if record is None:
                return True
            if record[3] is not owner:
                return False
            self._record = None
            return True

    def __copy__(self) -> None:
        raise TypeError("Relay invocation owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation owner destination cannot be serialized")


class RelayInvocationTools:
    """Factory-owned, explicitly synthetic executable and clock receipt."""

    __slots__ = ("_epoch_clock", "_next_cli", "_node", "_playwright_cli", "_web_root")

    def __init__(
        self,
        token: object,
        *,
        node: Path,
        web_root: Path,
        next_cli: Path,
        playwright_cli: Path,
        epoch_clock: Callable[[], float],
    ) -> None:
        if token is not _TOOLS_TOKEN:
            node = web_root = next_cli = playwright_cli = None  # type: ignore[assignment]
            epoch_clock = None  # type: ignore[assignment]
            raise TypeError("Relay invocation tools are factory-owned")
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_web_root", web_root)
        object.__setattr__(self, "_next_cli", next_cli)
        object.__setattr__(self, "_playwright_cli", playwright_cli)
        object.__setattr__(self, "_epoch_clock", epoch_clock)

    @property
    def concrete_adapter(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayInvocationTools(concrete_adapter=False)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay invocation tools are immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay invocation tools cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation tools cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation tools cannot be serialized")


class RelayInvocationDriver:
    """Synthetic adapter capability; never evidence of a concrete executable."""

    __slots__ = ("_finish", "_prebootstrap", "_preown", "_start", "_stop")

    def __init__(
        self,
        token: object,
        *,
        preown: Callable[[str, _RelayChildAuthorityDestination], None],
        start: Callable[[object, RelayChildRequest, _RelayChildStartDestination], None],
        prebootstrap: Callable[
            [object, RelayPrebootstrapRequest, RelayPrebootstrapDestination], None
        ],
        finish: Callable[[object, RelayFinishRequest, RelayPlaywrightExitDestination], None],
        stop: Callable[[object, _RelayChildStopDestination], None],
    ) -> None:
        if token is not _DRIVER_TOKEN or not all(
            callable(value) for value in (preown, start, prebootstrap, finish, stop)
        ):
            preown = start = prebootstrap = finish = stop = None  # type: ignore[assignment]
            raise TypeError("Relay invocation driver is factory-owned")
        object.__setattr__(self, "_preown", preown)
        object.__setattr__(self, "_start", start)
        object.__setattr__(self, "_prebootstrap", prebootstrap)
        object.__setattr__(self, "_finish", finish)
        object.__setattr__(self, "_stop", stop)

    @property
    def concrete_adapter(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayInvocationDriver(concrete_adapter=False)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay invocation driver is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay invocation driver cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation driver cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation driver cannot be serialized")


__all__ = ["RelayInvocationDriver", "RelayInvocationTools"]
