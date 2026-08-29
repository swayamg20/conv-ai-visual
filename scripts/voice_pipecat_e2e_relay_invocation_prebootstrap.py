"""Private exact relay prebootstrap request and single-use identity handoff."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn import derive_turn_rest_username
from scripts.voice_pipecat_e2e_relay_invocation_values import _FAILURE, RelayInvocationError
from scripts.voice_pipecat_e2e_stack import AUTHORIZATION, E2E_SESSION_ID, PIPECAT_BASE_URL

_REQUEST_TOKEN = object()
_DESTINATION_TOKEN = object()
_RECEIPT_TOKEN = object()
_ADOPTION_DESTINATION_TOKEN = object()
_ADOPTION_RECEIPT_TOKEN = object()
_MAX_ASSIGNMENT_TTL_SECONDS = 120
_PREBOOTSTRAP_KEYS = frozenset({"schema_version", "status", "expires_at_epoch_seconds"})


class RelayPrebootstrapRequest:
    """Private exact authenticated request passed only to the structural driver."""

    __slots__ = ("_authorization", "_call_id", "_session_id", "_url")

    def __init__(self, token: object, *, call_id: str) -> None:
        if token is not _REQUEST_TOKEN:
            raise TypeError("Relay prebootstrap request is factory-owned")
        object.__setattr__(self, "_url", f"{PIPECAT_BASE_URL}/_e2e/pipecat/prebootstrap")
        object.__setattr__(self, "_authorization", AUTHORIZATION)
        object.__setattr__(self, "_session_id", E2E_SESSION_ID)
        object.__setattr__(self, "_call_id", call_id)

    @property
    def url(self) -> str:
        return self._url

    @property
    def authorization(self) -> str:
        return self._authorization

    @property
    def body(self) -> dict[str, str]:
        return {"session_id": self._session_id, "voice_call_id": self._call_id}

    def _scrub(self) -> None:
        object.__setattr__(self, "_authorization", "")
        object.__setattr__(self, "_call_id", "")
        object.__setattr__(self, "_session_id", "")
        object.__setattr__(self, "_url", "")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay prebootstrap request is immutable")

    def __repr__(self) -> str:
        return "RelayPrebootstrapRequest()"

    def __copy__(self) -> None:
        raise TypeError("Relay prebootstrap request cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay prebootstrap request cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay prebootstrap request cannot be serialized")


class RelayPrebootstrapReceipt:
    """Non-sensitive proof of one exact request-bound prepared reservation."""

    __slots__ = ("_owner_token",)

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Relay prebootstrap receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)

    @property
    def prepared(self) -> bool:
        return True

    @property
    def reservation_bound(self) -> bool:
        return True

    def _matches(self, owner_token: object) -> bool:
        return self._owner_token is owner_token

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayPrebootstrapReceipt(prepared=True, reservation_bound=True)"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay prebootstrap receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay prebootstrap receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay prebootstrap receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay prebootstrap receipt cannot be serialized")


class _RelayUsernameAdoptionReceipt:
    """Fixed boolean-only proof that the preowned adopter committed."""

    __slots__ = ("_owner_token",)

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _ADOPTION_RECEIPT_TOKEN:
            raise TypeError("Relay username adoption receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)

    @property
    def adopted(self) -> bool:
        return True

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay username adoption receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay username adoption receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay username adoption receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay username adoption receipt cannot be serialized")


class _RelayUsernameAdoptionDestination:
    """Preowned idempotent receipt sink for a private username adopter."""

    __slots__ = ("_lock", "_owner_token", "_receipt")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _ADOPTION_DESTINATION_TOKEN:
            raise TypeError("Relay username adoption destination is factory-owned")
        self._owner_token = owner_token
        self._receipt: _RelayUsernameAdoptionReceipt | None = None
        self._lock = threading.Lock()

    def publish(self, adopted: object) -> None:
        with self._lock:
            if adopted is not True:
                raise RelayInvocationError(_FAILURE)
            if self._receipt is None:
                self._receipt = _RelayUsernameAdoptionReceipt(
                    _ADOPTION_RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                )

    def _read(self, owner_token: object) -> _RelayUsernameAdoptionReceipt:
        with self._lock:
            if (
                type(self._receipt) is not _RelayUsernameAdoptionReceipt
                or self._receipt._owner_token is not owner_token
            ):
                raise RelayInvocationError(_FAILURE)
            return self._receipt

    def __copy__(self) -> None:
        raise TypeError("Relay username adoption destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay username adoption destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay username adoption destination cannot be serialized")


class RelayPrebootstrapDestination:
    """Preowned response sink; sensitive identity stays off public owners."""

    __slots__ = (
        "_adopted",
        "_adoption_destination",
        "_call_id",
        "_clock",
        "_expected_username",
        "_lock",
        "_owner_token",
        "_receipt",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        call_id: str,
        clock: Callable[[], float],
    ) -> None:
        if token is not _DESTINATION_TOKEN:
            raise TypeError("Relay prebootstrap destination is factory-owned")
        self._owner_token = owner_token
        self._call_id = call_id
        self._clock = clock
        self._expected_username: bytearray | None = None
        self._receipt: RelayPrebootstrapReceipt | None = None
        self._adopted = False
        self._adoption_destination = _RelayUsernameAdoptionDestination(
            _ADOPTION_DESTINATION_TOKEN,
            owner_token=owner_token,
        )
        self._lock = threading.RLock()

    def publish(self, value: object) -> None:
        expected_username = ""
        with self._lock:
            if type(value) is not dict:
                raise RelayInvocationError(_FAILURE)
            keys = tuple(value)
            if (
                len(keys) != len(_PREBOOTSTRAP_KEYS)
                or any(type(key) is not str for key in keys)
                or frozenset(keys) != _PREBOOTSTRAP_KEYS
            ):
                raise RelayInvocationError(_FAILURE)
            schema = value["schema_version"]
            status = value["status"]
            expiry = value["expires_at_epoch_seconds"]
            now = self._clock()
            if (
                type(schema) is not int
                or schema != 1
                or type(status) is not str
                or status != "prepared"
                or type(expiry) is not int
                or type(now) is not float
                or not math.isfinite(now)
                or not int(now) < expiry <= int(now) + _MAX_ASSIGNMENT_TTL_SECONDS
            ):
                raise RelayInvocationError(_FAILURE)
            expected_username = derive_turn_rest_username(
                voice_call_id=self._call_id,
                expires_at_epoch_seconds=expiry,
            )
            candidate = expected_username.encode("ascii")
            if self._receipt is None:
                self._expected_username = bytearray(candidate)
                self._receipt = RelayPrebootstrapReceipt(
                    _RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                )
            elif self._expected_username != candidate:
                raise RelayInvocationError(_FAILURE)
            candidate = b""
        expected_username = ""

    def _read(self, owner_token: object) -> RelayPrebootstrapReceipt:
        with self._lock:
            if (
                type(self._receipt) is not RelayPrebootstrapReceipt
                or not self._receipt._matches(owner_token)
                or not self._expected_username
            ):
                raise RelayInvocationError(_FAILURE)
            return self._receipt

    def _adopt_username(
        self,
        owner_token: object,
        sink: object,
    ) -> bool:
        username = ""
        with self._lock:
            method = getattr(type(sink), "_accept_relay_turn_username", None)
            if owner_token is not self._owner_token or not callable(method):
                raise RelayInvocationError(_FAILURE)
            if self._adopted:
                return True
            value = self._expected_username
            if value is None or not value:
                raise RelayInvocationError(_FAILURE)
            username = bytes(value).decode("ascii")
            returned = method(sink, username, self._adoption_destination)
            if returned is not None:
                raise RelayInvocationError(_FAILURE)
            committed = self._reconcile_adoption(owner_token)
        method = sink = None
        username = ""
        return committed

    def _reconcile_adoption(self, owner_token: object) -> bool:
        with self._lock:
            if owner_token is not self._owner_token:
                return False
            if self._adopted:
                return True
            try:
                receipt = self._adoption_destination._read(owner_token)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                return False
            if not receipt.adopted or bool(receipt):
                return False
            if self._expected_username is not None:
                _wipe(self._expected_username)
            self._expected_username = None
            self._call_id = ""
            self._adopted = True
            return True

    def _scrub(self) -> None:
        with self._lock:
            if self._expected_username is not None:
                _wipe(self._expected_username)
            self._expected_username = None
            self._call_id = ""
            self._clock = _invalid_clock

    def __repr__(self) -> str:
        return "RelayPrebootstrapDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay prebootstrap destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay prebootstrap destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay prebootstrap destination cannot be serialized")


def _wipe(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)
    value.clear()


def _invalid_clock() -> float:
    raise RelayInvocationError(_FAILURE)


__all__ = ["RelayPrebootstrapReceipt"]
