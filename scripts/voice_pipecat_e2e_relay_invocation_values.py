"""Opaque values for the synthetic staged relay invocation owner."""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from pathlib import Path

_REQUEST_TOKEN = object()
_EXIT_DESTINATION_TOKEN = object()
_EXIT_RECEIPT_TOKEN = object()
_FINISH_TOKEN = object()
_STOP_TOKEN = object()

_FAILURE = "Relay invocation failed"
_EXIT_KEYS = frozenset({"status", "returncode"})


class RelayInvocationError(RuntimeError):
    """The structural relay invocation could not preserve its contract."""


class RelayChildRequest:
    """Immutable exact command/environment request with discarded output."""

    __slots__ = (
        "_absolute_deadline",
        "_command",
        "_completion",
        "_cwd",
        "_environment",
        "_role",
    )

    def __init__(
        self,
        token: object,
        *,
        role: str,
        command: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        completion: str,
        absolute_deadline: float | None = None,
    ) -> None:
        if token is not _REQUEST_TOKEN or (
            absolute_deadline is not None
            and (
                type(absolute_deadline) is not float
                or not math.isfinite(absolute_deadline)
                or absolute_deadline <= 0.0
            )
        ):
            raise TypeError("Relay child request is factory-owned")
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_command", command)
        object.__setattr__(self, "_cwd", cwd)
        object.__setattr__(self, "_environment", tuple(sorted(environment.items())))
        object.__setattr__(self, "_completion", completion)
        object.__setattr__(self, "_absolute_deadline", absolute_deadline)

    @property
    def role(self) -> str:
        return self._role

    @property
    def command(self) -> tuple[str, ...]:
        return self._command

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def environment(self) -> dict[str, str]:
        return dict(self._environment)

    @property
    def completion(self) -> str:
        return self._completion

    @property
    def absolute_deadline(self) -> float | None:
        return self._absolute_deadline

    @property
    def output_policy(self) -> str:
        return "discard"

    def _scrub(self) -> None:
        object.__setattr__(self, "_command", ())
        object.__setattr__(self, "_cwd", Path("."))
        object.__setattr__(self, "_environment", ())
        object.__setattr__(self, "_absolute_deadline", None)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay child request is immutable")

    def __repr__(self) -> str:
        return f"RelayChildRequest(role={self._role!r}, output_policy='discard')"

    def __copy__(self) -> None:
        raise TypeError("Relay child request cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay child request cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay child request cannot be serialized")


class RelayPlaywrightExitReceipt:
    """Non-qualifying exact zero-exit proof for the structural browser child."""

    __slots__ = ("_owner_token",)

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _EXIT_RECEIPT_TOKEN:
            raise TypeError("Relay Playwright exit receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)

    @property
    def exited_successfully(self) -> bool:
        return True

    def _matches(self, owner_token: object) -> bool:
        return self._owner_token is owner_token

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayPlaywrightExitReceipt(exited_successfully=True)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Playwright exit receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Playwright exit receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Playwright exit receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Playwright exit receipt cannot be serialized")


class RelayFinishRequest:
    """Immutable absolute deadline for one structural browser wait."""

    __slots__ = ("_absolute_deadline",)

    def __init__(self, token: object, *, absolute_deadline: float) -> None:
        if token is not _FINISH_TOKEN:
            raise TypeError("Relay finish request is factory-owned")
        object.__setattr__(self, "_absolute_deadline", absolute_deadline)

    @property
    def absolute_deadline(self) -> float:
        return self._absolute_deadline

    def __repr__(self) -> str:
        return "RelayFinishRequest()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay finish request is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay finish request cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay finish request cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay finish request cannot be serialized")


class RelayStopRequest:
    """Immutable cleanup deadline bound to one private concrete pair."""

    __slots__ = ("_absolute_deadline", "_pair_key")

    def __init__(self, token: object, *, pair_key: object, absolute_deadline: float) -> None:
        if (
            token is not _STOP_TOKEN
            or type(pair_key) is not object
            or type(absolute_deadline) is not float
            or not math.isfinite(absolute_deadline)
            or absolute_deadline <= 0.0
        ):
            raise TypeError("Relay stop request is factory-owned")
        object.__setattr__(self, "_pair_key", pair_key)
        object.__setattr__(self, "_absolute_deadline", absolute_deadline)

    @property
    def absolute_deadline(self) -> float:
        return self._absolute_deadline

    def _matches(self, pair_key: object) -> bool:
        return self._pair_key is pair_key

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayStopRequest()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay stop request is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay stop request cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay stop request cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay stop request cannot be serialized")


class RelayPlaywrightExitDestination:
    """Preowned exact zero-exit publication boundary."""

    __slots__ = ("_lock", "_owner_token", "_receipt")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _EXIT_DESTINATION_TOKEN:
            raise TypeError("Relay Playwright exit destination is factory-owned")
        self._owner_token = owner_token
        self._receipt: RelayPlaywrightExitReceipt | None = None
        self._lock = threading.Lock()

    def publish(self, value: object) -> None:
        with self._lock:
            if type(value) is not dict:
                raise RelayInvocationError(_FAILURE)
            keys = tuple(value)
            if (
                len(keys) != len(_EXIT_KEYS)
                or any(type(key) is not str for key in keys)
                or frozenset(keys) != _EXIT_KEYS
                or type(value["status"]) is not str
                or value["status"] != "exited"
                or type(value["returncode"]) is not int
                or value["returncode"] != 0
            ):
                raise RelayInvocationError(_FAILURE)
            if self._receipt is None:
                self._receipt = RelayPlaywrightExitReceipt(
                    _EXIT_RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                )

    def _read(self, owner_token: object) -> RelayPlaywrightExitReceipt:
        with self._lock:
            if type(self._receipt) is not RelayPlaywrightExitReceipt or not self._receipt._matches(
                owner_token
            ):
                raise RelayInvocationError(_FAILURE)
            return self._receipt

    def __repr__(self) -> str:
        return "RelayPlaywrightExitDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay Playwright exit destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Playwright exit destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Playwright exit destination cannot be serialized")


__all__ = [
    "RelayInvocationError",
    "RelayPlaywrightExitReceipt",
]
