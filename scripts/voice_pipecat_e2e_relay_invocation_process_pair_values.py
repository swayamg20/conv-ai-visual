"""Private values for one canonical concrete invocation capability pair."""

from __future__ import annotations

import threading
from collections.abc import Callable

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    RelayInvocationDriver,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _is_concrete_invocation_selection,
    _RelayConcreteInvocationSelection,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayStopRequest

_DESTINATION_TOKEN = object()
_GRANT_TOKEN = object()
_NO_OWNER_TOMBSTONE = object()
_FAILURE = "Relay concrete invocation pair is invalid"


class _RelayConcreteInvocationGrant:
    __slots__ = (
        "_binding",
        "_build",
        "_cleanup_timeout_seconds",
        "_clock",
        "_destination",
        "_epoch_clock",
        "_pair_key",
        "_runtime_deadline",
        "_runtime_timeout_seconds",
        "_selection",
        "_wait",
    )

    def __init__(
        self,
        token: object,
        *,
        build: object,
        binding: object,
        selection: _RelayConcreteInvocationSelection,
        destination: _RelayConcreteInvocationPairDestination,
        pair_key: object,
        runtime_deadline: float,
        runtime_timeout_seconds: float,
        cleanup_timeout_seconds: float,
        clock: Callable[[], float],
        wait: Callable[[float], None],
        epoch_clock: Callable[[], float],
    ) -> None:
        if token is not _GRANT_TOKEN or type(pair_key) is not object:
            raise TypeError(_FAILURE)
        values = locals().copy()
        for name in self.__slots__:
            object.__setattr__(self, name, values[name[1:]])

    def _matches(
        self,
        *,
        build: object,
        binding: object,
        selection: object,
        destination: object,
        runtime_deadline: object,
        runtime_timeout_seconds: object,
        cleanup_timeout_seconds: object,
        clock: object,
        wait: object,
        epoch_clock: object,
    ) -> bool:
        return bool(
            self._build is build
            and self._binding is binding
            and getattr(build, "binding", None) is binding
            and self._selection is selection
            and _is_concrete_invocation_selection(selection)
            and self._destination is destination
            and self._runtime_deadline == runtime_deadline
            and self._runtime_timeout_seconds == runtime_timeout_seconds
            and self._cleanup_timeout_seconds == cleanup_timeout_seconds
            and self._clock is clock
            and self._wait is wait
            and self._epoch_clock is epoch_clock
            and _live_consumed_build_matches(build)
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayConcreteInvocationGrant()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay concrete invocation grant is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay concrete invocation grant cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete invocation grant cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete invocation grant cannot be serialized")


class _RelayConcreteInvocationPairDestination:
    __slots__ = (
        "_binding",
        "_build",
        "_cleanup_timeout_seconds",
        "_clock",
        "_effect_lock",
        "_epoch_clock",
        "_lock",
        "_owner_destination",
        "_owner_graph",
        "_owner_token",
        "_phase",
        "_preown_intended_roles",
        "_preowned_roles",
        "_record",
        "_retirement_owner_identity",
        "_retiring_record",
        "_runtime_deadline",
        "_runtime_timeout_seconds",
        "_selection",
        "_stop_request",
        "_stopped_roles",
        "_wait",
    )

    def __init__(
        self,
        token: object,
        *,
        build: object,
        binding: object,
        selection: _RelayConcreteInvocationSelection,
        runtime_deadline: float,
        runtime_timeout_seconds: float,
        cleanup_timeout_seconds: float,
        clock: Callable[[], float],
        wait: Callable[[float], None],
        epoch_clock: Callable[[], float],
    ) -> None:
        if token is not _DESTINATION_TOKEN:
            raise TypeError(_FAILURE)
        self._build = build
        self._binding = binding
        self._selection = selection
        self._runtime_deadline = runtime_deadline
        self._runtime_timeout_seconds = runtime_timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._clock = clock
        self._wait = wait
        self._epoch_clock = epoch_clock
        self._effect_lock = threading.RLock()
        self._stop_request: RelayStopRequest | None = None
        self._owner_destination: object | None = None
        self._owner_graph: tuple[object, ...] | None = None
        self._owner_token: object | None = None
        self._phase = "live"
        self._preown_intended_roles = (False, False, False)
        self._preowned_roles = (False, False, False)
        self._stopped_roles = (False, False, False)
        self._retirement_owner_identity: object | None = None
        self._retiring_record: tuple[object, object, object] | None = None
        self._record: (
            tuple[_RelayConcreteInvocationGrant, RelayInvocationDriver, RelayInvocationTools] | None
        ) = None
        self._lock = threading.RLock()

    def _matches(self, **values: object) -> bool:
        return bool(
            self._build is values.get("build")
            and self._binding is values.get("binding")
            and self._selection is values.get("selection")
            and self._runtime_deadline == values.get("runtime_deadline")
            and self._runtime_timeout_seconds == values.get("runtime_timeout_seconds")
            and self._cleanup_timeout_seconds == values.get("cleanup_timeout_seconds")
            and self._clock is values.get("clock")
            and self._wait is values.get("wait")
            and self._epoch_clock is values.get("epoch_clock")
        )

    def _publish(
        self,
        grant: _RelayConcreteInvocationGrant,
        driver: RelayInvocationDriver,
        tools: RelayInvocationTools,
    ) -> None:
        candidate = (grant, driver, tools)
        with self._lock:
            if self._phase != "live":
                raise TypeError(_FAILURE)
            if self._record is None:
                self._record = candidate
            elif not all(
                self._record[index] is candidate[index] for index in range(len(candidate))
            ):
                raise TypeError(_FAILURE)

    def _read(
        self,
    ) -> tuple[_RelayConcreteInvocationGrant, RelayInvocationDriver, RelayInvocationTools] | None:
        with self._lock:
            return self._record

    def _clear(
        self,
        expected: tuple[object, object, object],
        owner_destination: object | None,
    ) -> bool:
        with self._lock:
            expected_identity = (
                _NO_OWNER_TOMBSTONE if owner_destination is None else owner_destination
            )
            if self._phase == "cleared":
                if self._retirement_owner_identity is not expected_identity:
                    return False
                self._record = None
                self._retiring_record = None
                self._owner_destination = None
                self._owner_graph = None
                self._owner_token = None
                self._preown_intended_roles = (False, False, False)
                self._preowned_roles = (False, False, False)
                self._stop_request = None
                self._stopped_roles = (False, False, False)
                return True
            if (
                self._phase != "retiring"
                or self._retiring_record is None
                or not all(self._retiring_record[index] is expected[index] for index in range(3))
                or (
                    self._record is not None
                    and not all(self._record[index] is expected[index] for index in range(3))
                )
            ):
                return False
            if self._retirement_owner_identity not in (None, expected_identity):
                return False
            self._retirement_owner_identity = expected_identity
            self._phase = "cleared"
            self._record = None
            self._retiring_record = None
            self._owner_destination = None
            self._owner_graph = None
            self._owner_token = None
            self._preown_intended_roles = (False, False, False)
            self._preowned_roles = (False, False, False)
            self._stop_request = None
            self._stopped_roles = (False, False, False)
            return True

    def _bind_owner_destination(self, destination: object) -> bool:
        with self._lock:
            if self._phase != "live":
                return False
            if self._owner_destination is None:
                self._owner_destination = destination
            return self._owner_destination is destination

    def _owner_destination_matches(self, destination: object | None) -> bool:
        with self._lock:
            return self._owner_destination is destination

    def _read_owner_destination(self) -> object | None:
        with self._lock:
            return self._owner_destination

    def _retirement_owner_matches(self, destination: object | None) -> bool:
        with self._lock:
            expected = _NO_OWNER_TOMBSTONE if destination is None else destination
            if self._phase != "cleared" or self._retirement_owner_identity is not expected:
                return False
            self._record = None
            self._retiring_record = None
            self._owner_destination = None
            self._owner_graph = None
            self._owner_token = None
            self._preown_intended_roles = (False, False, False)
            self._preowned_roles = (False, False, False)
            self._stop_request = None
            self._stopped_roles = (False, False, False)
            return True

    def _bind_owner_graph(self, graph: tuple[object, ...], owner_token: object) -> bool:
        with self._lock:
            if (
                self._phase != "live"
                or not graph
                or self._owner_destination is not graph[0]
                or type(owner_token) is not object
            ):
                return False
            if self._owner_token is None:
                self._owner_token = owner_token
            elif self._owner_token is not owner_token:
                return False
            if self._owner_graph is None:
                self._owner_graph = graph
            return bool(
                type(self._owner_graph) is tuple
                and len(self._owner_graph) == len(graph)
                and all(self._owner_graph[index] is graph[index] for index in range(len(graph)))
                and self._owner_token is owner_token
            )

    def _intend_role_preown(self, role_index: int) -> bool:
        with self._lock:
            if self._phase != "live" or not 0 <= role_index < 3:
                return False
            values = list(self._preown_intended_roles)
            values[role_index] = True
            self._preown_intended_roles = tuple(values)
            return self._preown_intended_roles[role_index]

    def _owner_graph_role_matches(
        self,
        role_index: int,
        authority_destination: object | None = None,
        stop_destination: object | None = None,
    ) -> bool:
        with self._lock:
            graph = self._owner_graph
            authority_index = 3 + role_index
            stop_index = 9 + role_index
            return bool(
                type(graph) is tuple
                and len(graph) == 12
                and 0 <= role_index < 3
                and type(self._owner_token) is object
                and (
                    authority_destination is None or graph[authority_index] is authority_destination
                )
                and (stop_destination is None or graph[stop_index] is stop_destination)
                and all(
                    getattr(graph[9 + index], "_owner_token", None) is self._owner_token
                    for index in range(3)
                )
            )

    def _mark_role_preowned(self, role_index: int) -> bool:
        with self._lock:
            if self._phase not in {"live", "retiring"} or not 0 <= role_index < 3:
                return False
            values = list(self._preowned_roles)
            values[role_index] = True
            self._preowned_roles = tuple(values)
            return self._preowned_roles[role_index]

    def _mark_role_stopped(self, role_index: int) -> bool:
        with self._lock:
            if (
                self._phase not in {"cleanup", "retiring"}
                or not 0 <= role_index < 3
                or not self._preowned_roles[role_index]
            ):
                return False
            values = list(self._stopped_roles)
            values[role_index] = True
            self._stopped_roles = tuple(values)
            return self._stopped_roles[role_index]

    def _read_retirement_state(self) -> tuple[object, ...]:
        with self._lock:
            return (
                self._phase,
                self._owner_destination,
                self._owner_graph,
                self._owner_token,
                self._preown_intended_roles,
                self._preowned_roles,
                self._stopped_roles,
                self._stop_request,
                self._retirement_owner_identity,
            )

    def _bind_stop_request(self, request: RelayStopRequest) -> bool:
        with self._lock:
            if self._phase not in {"live", "cleanup"}:
                return False
            if self._stop_request is None:
                self._stop_request = request
            if self._stop_request is request:
                self._phase = "cleanup"
            return self._stop_request is request and self._phase == "cleanup"

    def _read_stop_request(self) -> RelayStopRequest | None:
        with self._lock:
            return self._stop_request

    def _stop_request_matches(self, request: object) -> bool:
        with self._lock:
            return self._stop_request is request

    def _phase_matches(self, phase: str) -> bool:
        with self._lock:
            return self._phase == phase

    def _begin_retirement(self, expected: tuple[object, object, object]) -> bool:
        with self._lock:
            if self._phase == "retiring":
                return bool(
                    self._record is None
                    and self._retiring_record is not None
                    and all(self._retiring_record[index] is expected[index] for index in range(3))
                )
            if self._phase in {"live", "cleanup"} and self._record is not None:
                if not all(self._record[index] is expected[index] for index in range(3)):
                    return False
                self._retiring_record = expected
                self._phase = "retiring"
                self._record = None
            return self._retirement_in_progress(expected)

    def _retirement_in_progress(self, expected: tuple[object, object, object]) -> bool:
        with self._lock:
            if (
                self._phase != "retiring"
                or self._retiring_record is None
                or not all(self._retiring_record[index] is expected[index] for index in range(3))
            ):
                return False
            if self._record is not None:
                if not all(self._record[index] is expected[index] for index in range(3)):
                    return False
                self._record = None
            return True

    def _read_owner_token(self) -> object | None:
        with self._lock:
            return self._owner_token

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayConcreteInvocationPairDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay concrete invocation pair destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay concrete invocation pair destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay concrete invocation pair destination cannot be serialized")


def _live_consumed_build_matches(build: object) -> bool:
    from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
        _RelayLinuxExecutorBuiltEvidence,
    )
    from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
        _cleanup_evidence_matches,
        _consumed_binding_matches,
    )

    return bool(
        type(build) is _RelayLinuxExecutorBuiltEvidence
        and _consumed_binding_matches(build)
        and _cleanup_evidence_matches(build)
    )


__all__: list[str] = []
