"""Immutable values for the private consumed-build inner relay owner."""

from __future__ import annotations

import threading
import weakref
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import (
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import _RelayLinuxExecutorKey
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation

_EVIDENCE_TOKEN = object()
_RESULT_TOKEN = object()
_FAILURE = "Relay Linux executor inner ownership is invalid"


class _RelayLinuxExecutorInnerResultDestination:
    """Preowned terminal sink retained after the live inner graph is gone."""

    __slots__ = (
        "_key_ref",
        "_lock",
        "_record",
        "_replay_values",
        "_terminal_token",
    )

    def __init__(self, token: object, key: _RelayLinuxExecutorKey) -> None:
        if token is not _RESULT_TOKEN or type(key) is not _RelayLinuxExecutorKey:
            raise TypeError(_FAILURE)
        self._key_ref = weakref.ref(key)
        self._lock = threading.RLock()
        self._record: tuple[RelayProbeObservation | None, str] | None = None
        self._replay_values: tuple[object, ...] | None = None
        self._terminal_token = object()

    def _bind_replay_values(self, values: tuple[object, ...]) -> bool:
        if type(values) is not tuple:
            return False
        with self._lock:
            if self._replay_values is None:
                self._replay_values = values
            return self._replay_values is values

    def _replay_values_are(self, values: object) -> bool:
        with self._lock:
            return self._replay_values is values

    def _publish_observed(self, observation: RelayProbeObservation) -> bool:
        if type(observation) is not RelayProbeObservation:
            return False
        with self._lock:
            if self._record is None:
                self._record = (observation, "observed")
            return bool(
                type(self._record) is tuple
                and len(self._record) == 2
                and self._record[0] is observation
                and self._record[1] == "observed"
            )

    def _publish_failed(self) -> bool:
        with self._lock:
            if self._record is None:
                self._record = (None, "failed")
            return bool(
                type(self._record) is tuple
                and len(self._record) == 2
                and self._record[0] is None
                and type(self._record[1]) is str
                and self._record[1] == "failed"
            )

    def _read(self) -> tuple[RelayProbeObservation | None, str] | None:
        with self._lock:
            return self._record

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorInnerResultDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor inner result cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor inner result cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor inner result cannot be serialized")


class _RelayLinuxExecutorInnerEvidence:
    """Exact factory inputs retained across owner construction return loss."""

    __slots__ = (
        "bridge_probe",
        "browser_timeout_seconds",
        "build",
        "clock",
        "epoch_clock",
        "identity",
        "invocation_driver",
        "invocation_tools",
        "key",
        "now",
        "owner_binding",
        "owner_destination",
        "paths",
        "result_destination",
        "runner",
        "runtime_deadline",
        "runtime_timeout_seconds",
        "static_auth_secret",
        "tools",
        "wait",
    )

    def __init__(
        self,
        token: object,
        *,
        key: _RelayLinuxExecutorKey,
        build: _RelayLinuxExecutorBuiltEvidence,
        paths: CoturnRuntimePaths,
        identity: RuntimeIdentity,
        runner: object,
        bridge_probe: object,
        browser_timeout_seconds: float,
        tools: TrustedHostTools,
        invocation_driver: RelayInvocationDriver,
        invocation_tools: RelayInvocationTools,
        runtime_deadline: float,
        runtime_timeout_seconds: float,
        static_auth_secret: object,
        now: datetime,
        clock: object,
        wait: object,
        epoch_clock: object,
        owner_binding: tuple[object, ...],
        owner_destination: RelayProbeOwnerDestination,
        result_destination: _RelayLinuxExecutorInnerResultDestination,
    ) -> None:
        if (
            token is not _EVIDENCE_TOKEN
            or type(key) is not _RelayLinuxExecutorKey
            or type(build) is not _RelayLinuxExecutorBuiltEvidence
            or type(paths) is not CoturnRuntimePaths
            or type(identity) is not RuntimeIdentity
            or type(tools) is not TrustedHostTools
            or type(invocation_driver) is not RelayInvocationDriver
            or type(invocation_tools) is not RelayInvocationTools
            or type(browser_timeout_seconds) is not float
            or type(runtime_deadline) is not float
            or type(runtime_timeout_seconds) is not float
            or type(now) is not datetime
            or not callable(clock)
            or not callable(wait)
            or not callable(epoch_clock)
            or type(owner_binding) is not tuple
            or type(owner_destination) is not RelayProbeOwnerDestination
            or type(result_destination) is not _RelayLinuxExecutorInnerResultDestination
        ):
            raise TypeError(_FAILURE)
        values = locals().copy()
        for name in self.__slots__:
            object.__setattr__(self, name, values[name])

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor inner evidence is immutable")


__all__: list[str] = []
