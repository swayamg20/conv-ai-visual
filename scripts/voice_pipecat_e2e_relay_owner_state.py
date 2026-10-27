"""Private state, publication slots, and recovery registry for relay B0."""

from __future__ import annotations

import math
import threading
import traceback
from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_host import (
    BridgeHostProbe,
    CommandRunner,
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_invocation import RelayInvocationDriver, RelayInvocationTools
from scripts.voice_pipecat_e2e_relay_owner_resource import (
    _resource_key,
    _ResourceKey,
    _resources_conflict,
)
from scripts.voice_pipecat_e2e_relay_owner_values import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeObservation,
    RelayProbeOwnerError,
    _new_cleanup_authority,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeRun,
    RelayProbeSource,
    revalidate_relay_probe_source,
)

_OWNER_TOKEN = object()
_OWNER_DESTINATION_TOKEN = object()
_MAX_ACTIVE_OWNERS = 32
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[
    object,
    tuple[
        RelayProbeOwner,
        RelayProbeOwnerDestination,
        _ResourceKey,
    ],
] = {}
_SLOT_NAMES = (
    "prerequisites",
    "network_plan",
    "network",
    "tls_material",
    "invocation",
    "container_plan",
    "container",
    "process",
    "pump",
    "drain",
    "running",
    "readiness_budget",
    "readiness",
    "artifact_owner",
    "playwright_exit",
    "browser_observation",
    "stopped",
    "summary",
    "clean_exit",
    "container_absence",
    "network_absence",
)


class _RelaySlot:
    """Preowned idempotent destination for one exact runtime value."""

    __slots__ = ("_lock", "_value")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: object | None = None

    def publish(self, value: object, expected: type[object]) -> object:
        if type(value) is not expected:
            raise RelayProbeOwnerError("Relay probe publication failed")
        with self._lock:
            if self._value is None:
                self._value = value
            elif self._value is not value:
                raise RelayProbeOwnerError("Relay probe publication failed")
            return self._value

    def read(self) -> object | None:
        with self._lock:
            return self._value

    def clear(self) -> None:
        with self._lock:
            self._value = None

    @property
    def empty(self) -> bool:
        with self._lock:
            return self._value is None


class _RelayTerminalTransition:
    """Atomic recovery snapshot spanning final graph scrub and publication."""

    __slots__ = ("facts_valid", "observation", "phase", "publish", "run")

    def __init__(
        self,
        *,
        phase: str,
        publish: bool,
        run: RelayProbeRun | None,
        facts_valid: bool,
        observation: RelayProbeObservation | None = None,
    ) -> None:
        if phase not in {"preparing", "scrubbed", "published"}:
            raise TypeError("Relay probe terminal transition is invalid")
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "publish", publish)
        object.__setattr__(self, "run", run)
        object.__setattr__(self, "facts_valid", facts_valid)
        object.__setattr__(self, "observation", observation)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("Relay probe terminal transition is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay probe terminal transition cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe terminal transition cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe terminal transition cannot be serialized")


class RelayProbeOwnerDestination:
    """Caller-preowned canonical sink for one aggregate owner."""

    __slots__ = ("_lock", "_record")

    def __init__(self, token: object) -> None:
        if token is not _OWNER_DESTINATION_TOKEN:
            raise TypeError("Relay probe owner destination is factory-owned")
        self._lock = threading.RLock()
        self._record: tuple[tuple[object, ...], RelayProbeOwner] | None = None

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayProbeOwnerDestination()"

    def __copy__(self) -> None:
        raise TypeError("Relay probe owner destination cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe owner destination cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe owner destination cannot be serialized")

    def _read(self, binding: tuple[object, ...]) -> RelayProbeOwner | None:
        with self._lock:
            record = self._record
            if record is None:
                return None
            if not _same_owner_binding(record[0], binding):
                raise RelayProbeOwnerError("Relay probe owner publication failed")
            return record[1]

    def _publish(self, binding: tuple[object, ...], owner: RelayProbeOwner) -> None:
        with self._lock:
            record = self._record
            if record is None:
                self._record = (binding, owner)
            elif not _same_owner_binding(record[0], binding) or record[1] is not owner:
                raise RelayProbeOwnerError("Relay probe owner publication failed")

    def _clear(self, owner: RelayProbeOwner) -> bool:
        with self._lock:
            record = self._record
            if record is None:
                return True
            if record[1] is not owner:
                return False
            self._record = None
            return True


class RelayProbeOwner:
    """One exact-run aggregate; all forward and cleanup entry is serialized."""

    __slots__ = (
        "_absolute_deadline",
        "_bridge_probe",
        "_cleanup_authority",
        "_cleanup_complete",
        "_cleanup_only",
        "_cleanup_phase",
        "_clock",
        "_control",
        "_factory_token",
        "_forward_phase",
        "_identity",
        "_invocation_destination",
        "_invocation_driver",
        "_invocation_tools",
        "_lock",
        "_observation",
        "_operation_lock",
        "_owner_destination",
        "_owner_destination_cleared",
        "_paths",
        "_pending_authority",
        "_publish_requested",
        "_run",
        "_runner",
        "_runner_settled",
        "_slots",
        "_state",
        "_terminal_transition",
        "_tools",
        "_wait",
    )

    def __init__(
        self,
        token: object,
        *,
        run: RelayProbeRun,
        runner: CommandRunner,
        bridge_probe: BridgeHostProbe,
        tools: TrustedHostTools,
        identity: RuntimeIdentity,
        paths: CoturnRuntimePaths,
        invocation_driver: RelayInvocationDriver,
        invocation_tools: RelayInvocationTools,
        invocation_destination: object,
        owner_destination: RelayProbeOwnerDestination,
        cleanup_key: object,
        absolute_deadline: float,
        clock: Callable[[], float],
        wait: Callable[[float], None],
    ) -> None:
        if (
            token is not _OWNER_TOKEN
            or type(run) is not RelayProbeRun
            or type(tools) is not TrustedHostTools
            or type(identity) is not RuntimeIdentity
            or type(paths) is not CoturnRuntimePaths
            or type(invocation_driver) is not RelayInvocationDriver
            or type(invocation_tools) is not RelayInvocationTools
            or type(owner_destination) is not RelayProbeOwnerDestination
            or cleanup_key is None
            or not _valid_runner(runner)
            or not _valid_bridge_probe(bridge_probe)
            or not _valid_deadline(absolute_deadline, clock, wait)
            or paths.contract.run_id != identity.run_id
        ):
            raise TypeError("Relay probe owner is factory-owned")
        self._cleanup_authority = _new_cleanup_authority(cleanup_key)
        self._factory_token = _OWNER_TOKEN
        self._run: RelayProbeRun | None = run
        self._runner: CommandRunner | None = runner
        self._bridge_probe: BridgeHostProbe | None = bridge_probe
        self._tools: TrustedHostTools | None = tools
        self._identity: RuntimeIdentity | None = identity
        self._paths: CoturnRuntimePaths | None = paths
        self._invocation_driver: RelayInvocationDriver | None = invocation_driver
        self._invocation_tools: RelayInvocationTools | None = invocation_tools
        self._invocation_destination: object | None = invocation_destination
        self._owner_destination: RelayProbeOwnerDestination | None = owner_destination
        self._owner_destination_cleared = False
        self._absolute_deadline = absolute_deadline
        self._clock: Callable[[], float] | None = clock
        self._wait: Callable[[float], None] | None = wait
        self._slots = {name: _RelaySlot() for name in _SLOT_NAMES}
        self._pending_authority: object | None = None
        self._publish_requested = False
        self._control: ControlSignal | None = None
        self._forward_phase = "created"
        self._cleanup_phase = "invocation"
        self._cleanup_only = False
        self._cleanup_complete = False
        self._runner_settled = False
        self._observation: RelayProbeObservation | None = None
        self._terminal_transition: _RelayTerminalTransition | None = None
        self._state = "created"
        self._lock = threading.RLock()
        self._operation_lock = threading.RLock()

    @property
    def qualification_verified(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayProbeOwner(qualification_verified=False)"

    def __copy__(self) -> None:
        raise TypeError("Relay probe owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe owner cannot be serialized")

    def _publish(self, name: str, value: object, expected: type[object]) -> object:
        slot = self._slots.get(name)
        if type(slot) is not _RelaySlot:
            raise RelayProbeOwnerError("Relay probe publication failed")
        return slot.publish(value, expected)

    def _read(self, name: str, expected: type[object]) -> object | None:
        slot = self._slots.get(name)
        value = slot.read() if type(slot) is _RelaySlot else None
        return value if type(value) is expected else None

    def _remember_control(self, control: ControlSignal | None) -> None:
        with self._lock:
            if self._control is None and control is not None:
                self._control = control

    def _remember_exception(self, error: BaseException) -> None:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            self._remember_control(control_signal(error))
        _scrub_exception(error)

    def _publish_observation(self, observation: RelayProbeObservation) -> bool:
        if type(observation) is not RelayProbeObservation:
            return False
        with self._operation_lock:
            with self._lock:
                if self._observation is not None:
                    return self._observation is observation
                transition = self._terminal_transition
                if (
                    type(transition) is _RelayTerminalTransition
                    and transition.phase == "published"
                    and type(transition.observation) is RelayProbeObservation
                ):
                    self._observation = transition.observation
                    self._state = "observed"
                    return self._observation is observation
                ready = bool(
                    type(transition) is _RelayTerminalTransition
                    and transition.phase == "scrubbed"
                    and transition.publish
                    and transition.facts_valid
                    and type(transition.run) is RelayProbeRun
                    and self._state == "publishing"
                    and self._terminal_roots_empty()
                )
                run = transition.run if ready else None
            if not ready or type(run) is not RelayProbeRun:
                return False
            revalidate_relay_probe_source(run)
            with self._lock:
                transition = self._terminal_transition
                if (
                    type(transition) is not _RelayTerminalTransition
                    or transition.phase != "scrubbed"
                    or transition.run is not run
                    or not self._terminal_roots_empty()
                ):
                    return False
                self._terminal_transition = _RelayTerminalTransition(
                    phase="published",
                    publish=True,
                    run=None,
                    facts_valid=True,
                    observation=observation,
                )
                self._observation = observation
                self._state = "observed"
                return self._observation is observation

    def _terminal_roots_empty(self) -> bool:
        return bool(
            all(slot.empty for slot in self._slots.values())
            and self._runner is None
            and self._bridge_probe is None
            and self._tools is None
            and self._identity is None
            and self._paths is None
            and self._invocation_driver is None
            and self._invocation_tools is None
            and self._invocation_destination is None
            and self._owner_destination is None
            and self._owner_destination_cleared
            and self._clock is None
            and self._wait is None
            and self._pending_authority is None
            and self._run is None
        )


def _new_owner(
    *,
    run: RelayProbeRun,
    runner: CommandRunner,
    bridge_probe: BridgeHostProbe,
    tools: TrustedHostTools,
    identity: RuntimeIdentity,
    paths: CoturnRuntimePaths,
    invocation_driver: RelayInvocationDriver,
    invocation_tools: RelayInvocationTools,
    invocation_destination: object,
    owner_destination: RelayProbeOwnerDestination,
    owner_binding: tuple[object, ...],
    absolute_deadline: float,
    clock: Callable[[], float],
    wait: Callable[[float], None],
) -> RelayProbeOwner:
    control: ControlSignal | None = None
    owner: RelayProbeOwner | None = None
    failed = False
    for _attempt in range(3):
        try:
            observed = owner_destination._read(owner_binding)
            if observed is not None:
                if owner is not None and observed is not owner:
                    raise RelayProbeOwnerError("Relay probe owner publication failed")
                owner = observed
            if owner is None:
                cleanup_key = object()
                owner = RelayProbeOwner(
                    _OWNER_TOKEN,
                    run=run,
                    runner=runner,
                    bridge_probe=bridge_probe,
                    tools=tools,
                    identity=identity,
                    paths=paths,
                    invocation_driver=invocation_driver,
                    invocation_tools=invocation_tools,
                    invocation_destination=invocation_destination,
                    owner_destination=owner_destination,
                    cleanup_key=cleanup_key,
                    absolute_deadline=absolute_deadline,
                    clock=clock,
                    wait=wait,
                )
            _register_owner(owner._cleanup_authority._key, owner, owner_destination)
            owner_destination._publish(owner_binding, owner)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            failed = True
            _scrub_exception(error)
            break
    if type(owner) is RelayProbeOwner and _owner_registered(owner):
        if control is not None or failed:
            with owner._lock:
                owner._cleanup_only = True
                owner._publish_requested = False
                owner._state = "cleanup-only"
        if control is not None:
            raise_control(control, owner._cleanup_authority)
        if failed:
            raise RelayProbeCleanupRequired(owner._cleanup_authority) from None
        return owner
    if type(owner) is RelayProbeOwner:
        try:
            owner_destination._clear(owner)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                control = control or control_signal(error)
            _scrub_exception(error)
    owner = None
    if control is not None:
        raise_control(control)
    raise RelayProbeOwnerError("Relay probe owner publication failed") from None


def new_relay_probe_owner_destination() -> RelayProbeOwnerDestination:
    """Preown the aggregate publication sink before owner construction."""

    return RelayProbeOwnerDestination(_OWNER_DESTINATION_TOKEN)


def _register_owner(
    key: object,
    owner: RelayProbeOwner,
    destination: RelayProbeOwnerDestination,
) -> None:
    resources = _resource_key(owner._paths, owner._identity)  # type: ignore[arg-type]
    with _REGISTRY_LOCK:
        current = _REGISTRY.get(key)
        if current is not None and current[0] is owner and current[1] is destination:
            return
        conflict = any(
            record[0] is not owner and _resources_conflict(record[2], resources)
            for record in _REGISTRY.values()
        )
        if current is not None or conflict or len(_REGISTRY) >= _MAX_ACTIVE_OWNERS:
            raise RelayProbeOwnerError("Relay probe owner capacity is exhausted")
        _REGISTRY[key] = (owner, destination, resources)


def _owner_registered(owner: RelayProbeOwner) -> bool:
    try:
        authentic = object.__getattribute__(owner, "_factory_token") is _OWNER_TOKEN
        authority = object.__getattribute__(owner, "_cleanup_authority")
        key = object.__getattribute__(authority, "_key")
    except BaseException:
        return False
    if not authentic or type(authority) is not RelayProbeCleanupAuthority:
        return False
    with _REGISTRY_LOCK:
        current = _REGISTRY.get(key)
        return bool(current is not None and current[0] is owner)


def _poison_registered_destination(
    destination: RelayProbeOwnerDestination,
) -> RelayProbeOwner | None:
    """Irreversibly poison the owner without trusting its fallible destination read."""

    with destination._lock:
        record = destination._record
        owner = record[1] if record is not None else None
        if type(owner) is not RelayProbeOwner:
            return None
        try:
            authority = object.__getattribute__(owner, "_cleanup_authority")
            key = object.__getattribute__(authority, "_key")
        except BaseException:
            return None
        with _REGISTRY_LOCK:
            registered = _REGISTRY.get(key)
            if registered is None or registered[0] is not owner or registered[1] is not destination:
                return None
        with owner._lock:
            owner._cleanup_only = True
            owner._publish_requested = False
            owner._state = "cleanup-only"
        return owner


def _resolve_owner(value: object) -> RelayProbeOwner | None:
    if type(value) is RelayProbeOwner:
        if _owner_registered(value) or _terminal_owner_valid(value):
            return value
        return None
    if type(value) is not RelayProbeCleanupAuthority:
        return None
    if not value._is_authentic():
        return None
    try:
        key = object.__getattribute__(value, "_key")
    except BaseException:
        return None
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(key)
        owner = record[0] if record is not None else None
    try:
        authority = object.__getattribute__(owner, "_cleanup_authority")
    except BaseException:
        return None
    return owner if type(owner) is RelayProbeOwner and authority is value else None


def _terminal_owner_valid(owner: RelayProbeOwner) -> bool:
    try:
        if object.__getattribute__(owner, "_factory_token") is not _OWNER_TOKEN:
            return False
        state = object.__getattribute__(owner, "_state")
        complete = object.__getattribute__(owner, "_cleanup_complete")
        transition = object.__getattribute__(owner, "_terminal_transition")
        if state not in {"observed", "cleaned"} or complete is not True:
            return False
        if type(transition) is not _RelayTerminalTransition:
            return False
        if state == "observed" and transition.phase != "published":
            return False
        if state == "cleaned" and (transition.phase != "scrubbed" or transition.publish):
            return False
        return owner._terminal_roots_empty()
    except BaseException:
        return False


def _release_owner(owner: RelayProbeOwner) -> bool:
    _registry_release_hook("entry")
    authority = owner._cleanup_authority
    with _REGISTRY_LOCK:
        current = _REGISTRY.get(authority._key)
        if current is not None and current[0] is owner:
            _registry_release_hook("before-store")
            del _REGISTRY[authority._key]
            _registry_release_hook("after-store")
        current = _REGISTRY.get(authority._key)
        released = current is None or current[0] is not owner
    _registry_release_hook("return")
    return released


def _registry_release_hook(_position: str) -> None:
    """Deterministic registry-release cut seam; production behavior is empty."""


def _valid_runner(value: object) -> bool:
    return all(
        callable(getattr(value, name, None)) for name in ("run", "start_attached", "settle_owned")
    )


def _valid_bridge_probe(value: object) -> bool:
    return all(callable(getattr(value, name, None)) for name in ("ipv4_routes", "interface_ipv4"))


def _valid_deadline(
    deadline: object,
    clock: object,
    wait: object,
) -> bool:
    if type(deadline) is not float or not math.isfinite(deadline):
        return False
    if not callable(clock) or not callable(wait):
        return False
    return bool(callable(clock) and callable(wait))


def _owner_binding(
    source: RelayProbeSource,
    runner: CommandRunner,
    bridge_probe: BridgeHostProbe,
    tools: TrustedHostTools,
    identity: RuntimeIdentity,
    paths: CoturnRuntimePaths,
    invocation_driver: RelayInvocationDriver,
    invocation_tools: RelayInvocationTools,
    absolute_deadline: float,
    clock: Callable[[], float],
    wait: Callable[[float], None],
) -> tuple[object, ...]:
    return (
        source,
        runner,
        bridge_probe,
        tools,
        identity,
        paths,
        invocation_driver,
        invocation_tools,
        absolute_deadline,
        clock,
        wait,
    )


def _same_owner_binding(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    return bool(
        len(left) == len(right) == 11
        and all(left[index] is right[index] for index in range(8))
        and type(left[8]) is float
        and type(right[8]) is float
        and left[8] == right[8]
        and left[9] is right[9]
        and left[10] is right[10]
    )


def _scrub_exception(error: BaseException) -> None:
    try:
        if error.__traceback__ is not None:
            traceback.clear_frames(error.__traceback__)
        error.__traceback__ = None
        error.__context__ = None
        error.__cause__ = None
        error.args = ()
    except BaseException:
        pass


__all__: list[str] = []
