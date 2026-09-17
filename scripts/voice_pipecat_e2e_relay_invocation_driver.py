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
    RelayStopRequest,
)

_TOOLS_TOKEN = object()
_DRIVER_TOKEN = object()
_CONCRETE_TOOLS_TOKEN = object()
_CONCRETE_DRIVER_TOKEN = object()
_SYNTHETIC_ADAPTER_SEAL = object()
_CONCRETE_ADAPTER_SEAL = object()
_CHILD_DESTINATION_TOKEN = object()
_START_DESTINATION_TOKEN = object()
_START_RECEIPT_TOKEN = object()
_STOP_DESTINATION_TOKEN = object()
_STOP_RECEIPT_TOKEN = object()
_OWNER_DESTINATION_TOKEN = object()
_ROLES = frozenset({"app", "web", "browser"})


class _RelayChildAuthorityDestination:
    """Caller-owned publication slot that survives callback return loss."""

    __slots__ = ("_authority", "_lock", "_role", "_sealed")

    def __init__(self, token: object, *, role: str) -> None:
        if token is not _CHILD_DESTINATION_TOKEN or role not in _ROLES:
            raise TypeError("Relay child authority destination is factory-owned")
        self._role = role
        self._authority: object | None = None
        self._sealed = False
        self._lock = threading.Lock()

    def publish(self, authority: object) -> None:
        with self._lock:
            if self._sealed or authority is None:
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

    def _seal_empty(self) -> bool:
        with self._lock:
            if self._authority is not None:
                return False
            self._sealed = True
            return self._sealed

    def _is_sealed_empty(self) -> bool:
        with self._lock:
            return self._sealed and self._authority is None

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

    __slots__ = ("_lock", "_owner_token", "_receipt", "_role", "_sealed")

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
        self._sealed = False
        self._lock = threading.Lock()

    def publish(self, started: object) -> None:
        with self._lock:
            if self._sealed or started is not True:
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

    def _seal(self, owner_token: object, role: str) -> bool:
        with self._lock:
            if self._role != role or (not self._sealed and self._owner_token is not owner_token):
                return False
            receipt = self._receipt
            if receipt is not None:
                if type(receipt) is not _RelayChildStartReceipt or not (
                    getattr(receipt, "_owner_token", None) in (None, owner_token)
                    and getattr(receipt, "_role", None) in (None, role)
                ):
                    return False
            self._sealed = True
            if receipt is not None:
                object.__setattr__(receipt, "_owner_token", None)
                object.__setattr__(receipt, "_role", None)
            self._owner_token = None
            self._receipt = None
            return self._is_sealed_unlocked()

    def _is_sealed_unlocked(self) -> bool:
        return self._sealed and self._owner_token is None and self._receipt is None

    def _is_sealed(self) -> bool:
        with self._lock:
            return self._is_sealed_unlocked()

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

    __slots__ = ("_lock", "_owner_token", "_receipt", "_role", "_sealed")

    def __init__(self, token: object, *, owner_token: object, role: str) -> None:
        if token is not _STOP_DESTINATION_TOKEN or role not in _ROLES:
            raise TypeError("Relay child stop destination is factory-owned")
        self._owner_token = owner_token
        self._role = role
        self._receipt: _RelayChildStopReceipt | None = None
        self._sealed = False
        self._lock = threading.Lock()

    def publish(self, stopped: object) -> None:
        with self._lock:
            if self._sealed or stopped is not True:
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

    def _seal(self, owner_token: object, role: str) -> bool:
        with self._lock:
            if self._role != role or (not self._sealed and self._owner_token is not owner_token):
                return False
            receipt = self._receipt
            if receipt is not None:
                if type(receipt) is not _RelayChildStopReceipt or not (
                    getattr(receipt, "_owner_token", None) in (None, owner_token)
                    and getattr(receipt, "_role", None) in (None, role)
                ):
                    return False
            self._sealed = True
            if receipt is not None:
                object.__setattr__(receipt, "_owner_token", None)
                object.__setattr__(receipt, "_role", None)
            self._owner_token = None
            self._receipt = None
            return self._is_sealed_unlocked()

    def _is_sealed_unlocked(self) -> bool:
        return self._sealed and self._owner_token is None and self._receipt is None

    def _is_sealed(self) -> bool:
        with self._lock:
            return self._is_sealed_unlocked()

    def __copy__(self) -> None:
        raise TypeError("Relay child stop destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child stop destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child stop destination cannot be serialized")


class _RelayInvocationOwnerDestination:
    """Caller-preowned canonical publication sink for one aggregate owner."""

    __slots__ = ("_construction_lock", "_lock", "_record", "_sealed")

    def __init__(self, token: object) -> None:
        if token is not _OWNER_DESTINATION_TOKEN:
            raise TypeError("Relay invocation owner destination is factory-owned")
        self._construction_lock = threading.RLock()
        self._lock = threading.RLock()
        self._sealed = False
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
            if self._sealed:
                raise TypeError("Relay invocation owner publication is invalid")
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
            if self._sealed:
                raise TypeError("Relay invocation owner publication is invalid")
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

    def _seal_empty(self) -> bool:
        with self._lock:
            if self._record is not None:
                return False
            self._sealed = True
            return self._sealed

    def _is_sealed_empty(self) -> bool:
        with self._lock:
            return self._sealed and self._record is None

    def __copy__(self) -> None:
        raise TypeError("Relay invocation owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation owner destination cannot be serialized")


class RelayInvocationTools:
    """Sealed executable receipt; concrete authority needs a canonical pair."""

    __slots__ = (
        "_adapter_seal",
        "_epoch_clock",
        "_next_cli",
        "_node",
        "_pair_key",
        "_playwright_cli",
        "_web_root",
    )

    def __init__(
        self,
        token: object,
        *,
        node: Path,
        web_root: Path,
        next_cli: Path,
        playwright_cli: Path,
        epoch_clock: Callable[[], float],
        pair_key: object | None = None,
    ) -> None:
        if token is _TOOLS_TOKEN and pair_key is None:
            adapter_seal = _SYNTHETIC_ADAPTER_SEAL
        elif token is _CONCRETE_TOOLS_TOKEN and type(pair_key) is object:
            adapter_seal = _CONCRETE_ADAPTER_SEAL
        else:
            node = web_root = next_cli = playwright_cli = None  # type: ignore[assignment]
            epoch_clock = None  # type: ignore[assignment]
            raise TypeError("Relay invocation tools are factory-owned")
        object.__setattr__(self, "_adapter_seal", adapter_seal)
        object.__setattr__(self, "_pair_key", pair_key)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_web_root", web_root)
        object.__setattr__(self, "_next_cli", next_cli)
        object.__setattr__(self, "_playwright_cli", playwright_cli)
        object.__setattr__(self, "_epoch_clock", epoch_clock)

    @property
    def concrete_adapter(self) -> bool:
        try:
            if getattr(self, "_adapter_seal", None) is not _CONCRETE_ADAPTER_SEAL:
                return False
            from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
                _concrete_invocation_pair_member_matches,
            )

            return _concrete_invocation_pair_member_matches(
                self, getattr(self, "_pair_key", None), "tools"
            )
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"RelayInvocationTools(concrete_adapter={self.concrete_adapter})"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay invocation tools are immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay invocation tools cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation tools cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation tools cannot be serialized")


class RelayInvocationDriver:
    """Sealed callback capability; its descriptive seal never authorizes work."""

    __slots__ = (
        "__weakref__",
        "_adapter_seal",
        "_finish",
        "_pair_key",
        "_prebootstrap",
        "_preown",
        "_start",
        "_stop",
    )

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
        stop: (
            Callable[[object, _RelayChildStopDestination], None]
            | Callable[[object, RelayStopRequest, _RelayChildStopDestination], None]
        ),
        pair_key: object | None = None,
    ) -> None:
        synthetic = token is _DRIVER_TOKEN and pair_key is None
        concrete = token is _CONCRETE_DRIVER_TOKEN and type(pair_key) is object
        if not (synthetic or concrete) or not all(
            callable(value) for value in (preown, start, prebootstrap, finish, stop)
        ):
            preown = start = prebootstrap = finish = stop = None  # type: ignore[assignment]
            raise TypeError("Relay invocation driver is factory-owned")
        if synthetic:
            legacy_stop = stop

            def stop_with_request(
                authority: object,
                _request: RelayStopRequest | None,
                destination: _RelayChildStopDestination,
            ) -> None:
                legacy_stop(authority, destination)  # type: ignore[call-arg]

            normalized_stop = stop_with_request
            adapter_seal = _SYNTHETIC_ADAPTER_SEAL
        else:
            normalized_stop = stop
            adapter_seal = _CONCRETE_ADAPTER_SEAL
        object.__setattr__(self, "_adapter_seal", adapter_seal)
        object.__setattr__(self, "_pair_key", pair_key)
        object.__setattr__(self, "_preown", preown)
        object.__setattr__(self, "_start", start)
        object.__setattr__(self, "_prebootstrap", prebootstrap)
        object.__setattr__(self, "_finish", finish)
        object.__setattr__(self, "_stop", normalized_stop)

    @property
    def concrete_adapter(self) -> bool:
        try:
            if getattr(self, "_adapter_seal", None) is not _CONCRETE_ADAPTER_SEAL:
                return False
            from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
                _concrete_invocation_pair_member_matches,
            )

            return _concrete_invocation_pair_member_matches(
                self, getattr(self, "_pair_key", None), "driver"
            )
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"RelayInvocationDriver(concrete_adapter={self.concrete_adapter})"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay invocation driver is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay invocation driver cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation driver cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation driver cannot be serialized")


def _synthetic_invocation_driver_matches(driver: object) -> bool:
    return bool(
        type(driver) is RelayInvocationDriver
        and getattr(driver, "_adapter_seal", None) is _SYNTHETIC_ADAPTER_SEAL
        and getattr(driver, "_pair_key", None) is None
        and all(
            callable(getattr(driver, name, None))
            for name in ("_preown", "_start", "_prebootstrap", "_finish", "_stop")
        )
    )


def _synthetic_invocation_pair_matches(driver: object, tools: object) -> bool:
    return bool(
        _synthetic_invocation_driver_matches(driver)
        and type(tools) is RelayInvocationTools
        and getattr(tools, "_adapter_seal", None) is _SYNTHETIC_ADAPTER_SEAL
        and getattr(tools, "_pair_key", None) is None
        and callable(getattr(tools, "_epoch_clock", None))
    )


__all__ = ["RelayInvocationDriver", "RelayInvocationTools"]
