"""Live owner value for one staged relay invocation."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_runtime_values import ControlSignal
from scripts.voice_pipecat_e2e_relay_invocation_cleanup import (
    RelayInvocationCleanupAuthority,
)
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _CHILD_DESTINATION_TOKEN,
    _START_DESTINATION_TOKEN,
    _STOP_DESTINATION_TOKEN,
    RelayInvocationDriver,
    RelayInvocationTools,
    _RelayChildAuthorityDestination,
    _RelayChildStartDestination,
    _RelayChildStopDestination,
    _RelayInvocationOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import RelayPrebootstrapReceipt
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _bind_concrete_invocation_owner_destinations,
    _canonical_concrete_invocation_pair_matches,
)
from scripts.voice_pipecat_e2e_relay_probe import RelayProbeRun

_OWNER_TOKEN = object()


class RelayInvocationOwner:
    """Caller-preowned staged owner retaining every child cleanup authority."""

    __slots__ = (
        "__weakref__",
        "_app",
        "_app_start",
        "_app_stop",
        "_browser",
        "_browser_start",
        "_browser_stop",
        "_cleanup_authority",
        "_cleanup_clock",
        "_cleanup_phase",
        "_cleanup_timeout_seconds",
        "_construction_lock",
        "_control",
        "_destination",
        "_driver",
        "_operation_lock",
        "_owner_token",
        "_prebootstrap_receipt",
        "_secret_key",
        "_state",
        "_stop_request",
        "_tools",
        "_web",
        "_web_start",
        "_web_stop",
    )

    def __init__(
        self,
        token: object,
        *,
        driver: RelayInvocationDriver,
        tools: RelayInvocationTools,
        run: RelayProbeRun,
        destination: _RelayInvocationOwnerDestination,
        secret_key: object,
        owner_token: object,
        cleanup_authority: RelayInvocationCleanupAuthority,
        cleanup_contract: tuple[object, float, object] | None,
        register_owner: object,
    ) -> None:
        if (
            token is not _OWNER_TOKEN
            or type(driver) is not RelayInvocationDriver
            or type(tools) is not RelayInvocationTools
            or type(run) is not RelayProbeRun
            or type(destination) is not _RelayInvocationOwnerDestination
            or type(cleanup_authority) is not RelayInvocationCleanupAuthority
            or not _cleanup_contract_has_shape(cleanup_contract)
            or not callable(register_owner)
        ):
            raise TypeError("Relay invocation owner is factory-owned")
        self._driver: RelayInvocationDriver | None = driver
        self._destination: _RelayInvocationOwnerDestination | None = destination
        self._tools: RelayInvocationTools | None = tools
        self._secret_key: object | None = secret_key
        self._owner_token = owner_token
        self._cleanup_authority = cleanup_authority
        self._construction_lock = destination._construction_lock
        self._cleanup_timeout_seconds = (
            cleanup_contract[1] if cleanup_contract is not None else None
        )
        self._cleanup_clock = cleanup_contract[2] if cleanup_contract is not None else None
        self._stop_request = None
        self._app = _RelayChildAuthorityDestination(_CHILD_DESTINATION_TOKEN, role="app")
        self._web = _RelayChildAuthorityDestination(_CHILD_DESTINATION_TOKEN, role="web")
        self._browser = _RelayChildAuthorityDestination(_CHILD_DESTINATION_TOKEN, role="browser")
        self._app_start = _RelayChildStartDestination(
            _START_DESTINATION_TOKEN, owner_token=owner_token, role="app"
        )
        self._web_start = _RelayChildStartDestination(
            _START_DESTINATION_TOKEN, owner_token=owner_token, role="web"
        )
        self._browser_start = _RelayChildStartDestination(
            _START_DESTINATION_TOKEN, owner_token=owner_token, role="browser"
        )
        self._app_stop = _RelayChildStopDestination(
            _STOP_DESTINATION_TOKEN, owner_token=owner_token, role="app"
        )
        self._web_stop = _RelayChildStopDestination(
            _STOP_DESTINATION_TOKEN, owner_token=owner_token, role="web"
        )
        self._browser_stop = _RelayChildStopDestination(
            _STOP_DESTINATION_TOKEN, owner_token=owner_token, role="browser"
        )
        self._prebootstrap_receipt: RelayPrebootstrapReceipt | None = None
        self._cleanup_phase = "active"
        self._control: ControlSignal | None = None
        self._state = "preowning"
        self._operation_lock = threading.RLock()
        destination._publish_owner(run, driver, tools, self)
        register_owner(cleanup_authority, self)
        if cleanup_contract is not None and not _bind_concrete_invocation_owner_destinations(
            driver,
            tools,
            destination,
            self,
            owner_token,
            (self._app, self._web, self._browser),
            (self._app_start, self._web_start, self._browser_start),
            (self._app_stop, self._web_stop, self._browser_stop),
        ):
            raise TypeError("Relay invocation owner is factory-owned")

    @property
    def concrete_adapter(self) -> bool:
        try:
            return _canonical_concrete_invocation_pair_matches(
                getattr(self, "_driver", None),
                getattr(self, "_tools", None),
            )
        except BaseException:
            return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"RelayInvocationOwner(concrete_adapter={self.concrete_adapter})"

    def __copy__(self) -> None:
        raise TypeError("Relay invocation owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation owner cannot be serialized")


def _cleanup_contract_has_shape(value: object) -> bool:
    return bool(
        value is None
        or (
            type(value) is tuple
            and len(value) == 3
            and type(value[0]) is object
            and type(value[1]) is float
            and callable(value[2])
        )
    )


__all__: list[str] = []
