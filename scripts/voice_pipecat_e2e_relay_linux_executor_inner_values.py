"""Immutable values for the private consumed-build inner relay owner."""

from __future__ import annotations

import hashlib
import hmac
import secrets
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
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _synthetic_invocation_pair_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _canonical_concrete_invocation_pair_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _is_concrete_invocation_selection,
    _RelayConcreteInvocationSelection,
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
_REPLAY_TOKEN = object()
_FAILURE = "Relay Linux executor inner ownership is invalid"
_MAX_SECRET_BYTES = 4096


def _secret_tag(key: bytes, value: str) -> bytes:
    encoded = value.encode("utf-8")
    digest = hashlib.blake2b(digest_size=32, key=key, person=b"relay-secret-v1")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return digest.digest()


class _RelayLinuxExecutorInnerReplayDescriptor:
    """Opaque identity tags for terminal same-input replay."""

    __slots__ = (
        "_authentic",
        "_concrete_selection",
        "_key",
        "_references",
        "_secret_tag",
    )

    def __init__(self, token: object, values: tuple[object, ...]) -> None:
        if (
            token is not _REPLAY_TOKEN
            or type(values) is not tuple
            or len(values) != 8
            or any(value is None for value in values)
            or type(values[4]) is not str
            or not 0 < len(values[4].encode("utf-8")) <= _MAX_SECRET_BYTES
        ):
            raise TypeError(_FAILURE)
        key = secrets.token_bytes(32)
        if type(key) is not bytes or len(key) != 32:
            raise TypeError(_FAILURE)
        concrete_selection = _is_concrete_invocation_selection(values[3])
        try:
            references = tuple(
                None if index == 3 and concrete_selection else weakref.ref(values[index])
                for index in (0, 1, 2, 3, 5, 6, 7)
            )
        except TypeError:
            raise TypeError(_FAILURE) from None
        object.__setattr__(self, "_authentic", token)
        object.__setattr__(self, "_concrete_selection", concrete_selection)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_references", references)
        object.__setattr__(self, "_secret_tag", _secret_tag(key, values[4]))

    def _matches(self, values: tuple[object, ...]) -> bool:
        try:
            if (
                getattr(self, "_authentic", None) is not _REPLAY_TOKEN
                or type(values) is not tuple
                or len(values) != 8
                or any(value is None for value in values)
                or type(values[4]) is not str
                or not 0 < len(values[4].encode("utf-8")) <= _MAX_SECRET_BYTES
            ):
                return False
            key = getattr(self, "_key", None)
            references = getattr(self, "_references", None)
            concrete_selection = getattr(self, "_concrete_selection", None)
            expected_secret_tag = getattr(self, "_secret_tag", None)
            if not (
                type(key) is bytes
                and len(key) == 32
                and type(references) is tuple
                and len(references) == 7
                and type(concrete_selection) is bool
                and type(expected_secret_tag) is bytes
                and len(expected_secret_tag) == 32
            ):
                return False
            candidates = tuple(values[index] for index in (0, 1, 2, 3, 5, 6, 7))
            for index, (reference, candidate) in enumerate(
                zip(references, candidates, strict=True)
            ):
                if index == 3 and concrete_selection:
                    if reference is not None or not _is_concrete_invocation_selection(candidate):
                        return False
                elif type(reference) is not weakref.ReferenceType or reference() is not candidate:
                    return False
            return hmac.compare_digest(expected_secret_tag, _secret_tag(key, values[4]))
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_RelayLinuxExecutorInnerReplayDescriptor()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor inner replay descriptor is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux executor inner replay descriptor cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux executor inner replay descriptor cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux executor inner replay descriptor cannot be serialized")


class _RelayLinuxExecutorInnerResultDestination:
    """Preowned terminal sink retaining only replay-safe call identity."""

    __slots__ = (
        "_key_ref",
        "_lock",
        "_preparing_token",
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
        self._preparing_token = object()
        self._terminal_token = object()

    def _bind_replay_values(self, values: tuple[object, ...]) -> bool:
        if type(values) is not tuple or len(values) != 14 or values[12] is not None:
            return False
        with self._lock:
            if self._replay_values is None:
                self._replay_values = values
            return self._replay_values is values

    def _replay_values_are(self, values: object) -> bool:
        with self._lock:
            return bool(
                type(values) is tuple
                and len(values) == 14
                and values[12] is None
                and self._replay_values is values
            )

    def _read_replay_values(self) -> tuple[object, ...] | None:
        with self._lock:
            return self._replay_values

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
        "cleanup_timeout_seconds",
        "clock",
        "effective_invocation_driver",
        "effective_invocation_tools",
        "epoch_clock",
        "identity",
        "invocation_selection",
        "key",
        "now",
        "owner_binding",
        "owner_destination",
        "paths",
        "replay_descriptor",
        "replay_values",
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
        invocation_selection: RelayInvocationDriver | _RelayConcreteInvocationSelection,
        effective_invocation_driver: RelayInvocationDriver,
        effective_invocation_tools: RelayInvocationTools,
        runtime_deadline: float,
        runtime_timeout_seconds: float,
        cleanup_timeout_seconds: float,
        static_auth_secret: object,
        now: datetime,
        clock: object,
        wait: object,
        epoch_clock: object,
        owner_binding: tuple[object, ...],
        owner_destination: RelayProbeOwnerDestination,
        replay_descriptor: _RelayLinuxExecutorInnerReplayDescriptor,
        replay_values: tuple[object, ...],
        result_destination: _RelayLinuxExecutorInnerResultDestination,
    ) -> None:
        if (
            token is not _EVIDENCE_TOKEN
            or type(key) is not _RelayLinuxExecutorKey
            or type(build) is not _RelayLinuxExecutorBuiltEvidence
            or type(paths) is not CoturnRuntimePaths
            or type(identity) is not RuntimeIdentity
            or type(tools) is not TrustedHostTools
            or type(effective_invocation_driver) is not RelayInvocationDriver
            or type(effective_invocation_tools) is not RelayInvocationTools
            or not (
                (
                    type(invocation_selection) is RelayInvocationDriver
                    and invocation_selection is effective_invocation_driver
                    and _synthetic_invocation_pair_matches(
                        effective_invocation_driver,
                        effective_invocation_tools,
                    )
                )
                or (
                    _is_concrete_invocation_selection(invocation_selection)
                    and _canonical_concrete_invocation_pair_matches(
                        effective_invocation_driver,
                        effective_invocation_tools,
                    )
                )
            )
            or type(browser_timeout_seconds) is not float
            or type(runtime_deadline) is not float
            or type(runtime_timeout_seconds) is not float
            or type(cleanup_timeout_seconds) is not float
            or type(now) is not datetime
            or not callable(clock)
            or not callable(wait)
            or not callable(epoch_clock)
            or type(owner_binding) is not tuple
            or type(owner_destination) is not RelayProbeOwnerDestination
            or type(replay_descriptor) is not _RelayLinuxExecutorInnerReplayDescriptor
            or type(replay_values) is not tuple
            or len(replay_values) != 14
            or replay_values[1] is not replay_descriptor
            or replay_values[13] is not result_destination
            or type(result_destination) is not _RelayLinuxExecutorInnerResultDestination
        ):
            raise TypeError(_FAILURE)
        values = locals().copy()
        for name in self.__slots__:
            object.__setattr__(self, name, values[name])

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux executor inner evidence is immutable")


def _new_inner_replay_descriptor(
    values: tuple[object, ...],
) -> _RelayLinuxExecutorInnerReplayDescriptor:
    return _RelayLinuxExecutorInnerReplayDescriptor(_REPLAY_TOKEN, values)


__all__: list[str] = []
