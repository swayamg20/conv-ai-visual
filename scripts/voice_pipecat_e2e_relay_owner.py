"""Factory-owned executable composition for the non-qualifying relay B0 probe."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn_host import (
    BridgeHostProbe,
    CommandRunner,
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (
    RuntimeReadinessBudget,
    create_runtime_readiness_budget,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_support import (
    _new_relay_invocation_owner_destination,
)
from scripts.voice_pipecat_e2e_relay_owner_cleanup import _drive_cleanup
from scripts.voice_pipecat_e2e_relay_owner_forward import _run_forward
from scripts.voice_pipecat_e2e_relay_owner_public import _sanitize_owner_boundary
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwner,
    RelayProbeOwnerDestination,
    _new_owner,
    _owner_binding,
    _owner_registered,
    _poison_registered_destination,
    _release_owner,
    _resolve_owner,
    _scrub_exception,
    new_relay_probe_owner_destination,
)
from scripts.voice_pipecat_e2e_relay_owner_values import (
    RelayProbeCleanupAuthority,
    RelayProbeCleanupRequired,
    RelayProbeObservation,
    RelayProbeOwnerError,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeSource,
    new_relay_probe_run,
)

_MAX_CLEANUP_PASSES = 16
_MAX_RELEASE_ATTEMPTS = 4
_FAILURE = "Relay probe execution failed"


def new_relay_probe_owner(
    *,
    destination: RelayProbeOwnerDestination,
    paths: CoturnRuntimePaths,
    identity: RuntimeIdentity,
    source: RelayProbeSource,
    runner: CommandRunner,
    bridge_probe: BridgeHostProbe,
    tools: TrustedHostTools,
    invocation_driver: RelayInvocationDriver,
    invocation_tools: RelayInvocationTools,
    absolute_deadline: float,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> RelayProbeOwner:
    """Preown the aggregate, run identity, and every publication destination."""

    owner: RelayProbeOwner | None = None
    control = None
    authority: RelayProbeCleanupAuthority | None = None
    run = invocation_destination = budget = binding = None
    try:
        if type(destination) is not RelayProbeOwnerDestination:
            raise RelayProbeOwnerError(_FAILURE)
        binding = _owner_binding(
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
        owner = destination._read(binding)
        if owner is None:
            run = new_relay_probe_run(runtime_paths=paths, source=source)
            invocation_destination = _new_relay_invocation_owner_destination()
            owner = _new_owner(
                run=run,
                runner=runner,
                bridge_probe=bridge_probe,
                tools=tools,
                identity=identity,
                paths=paths,
                invocation_driver=invocation_driver,
                invocation_tools=invocation_tools,
                invocation_destination=invocation_destination,
                owner_destination=destination,
                owner_binding=binding,
                absolute_deadline=absolute_deadline,
                clock=clock,
                wait=wait,
            )
        with owner._operation_lock:
            with owner._lock:
                if owner._cleanup_only or owner._state != "created":
                    raise RelayProbeCleanupRequired(owner._cleanup_authority)
            budget = owner._read("readiness_budget", RuntimeReadinessBudget)
            if budget is None:
                budget = create_runtime_readiness_budget(
                    absolute_deadline=absolute_deadline,
                    clock=clock,
                    wait=wait,
                )
                owner._publish("readiness_budget", budget, RuntimeReadinessBudget)
            with owner._lock:
                if owner._cleanup_only or owner._state != "created":
                    raise RelayProbeCleanupRequired(owner._cleanup_authority)
    except (KeyboardInterrupt, SystemExit) as error:
        namespace = getattr(error, "__dict__", None)
        candidate = namespace.get("cleanup_authority") if type(namespace) is dict else None
        owner, recovery_control = _recover_factory_owner(destination, binding, owner)
        if owner is not None:
            owner._remember_exception(error)
            owner._remember_control(recovery_control)
            _mark_factory_cleanup_only(owner)
            control = owner._control
            authority = owner._cleanup_authority
        else:
            control = control_signal(error)
            if type(candidate) is RelayProbeCleanupAuthority:
                authority = candidate
            _scrub_exception(error)
    except RelayProbeCleanupRequired as error:
        authority = error.cleanup_authority
        _scrub_exception(error)
    except BaseException as error:
        owner, recovery_control = _recover_factory_owner(destination, binding, owner)
        if owner is not None:
            owner._remember_exception(error)
            owner._remember_control(recovery_control)
            _mark_factory_cleanup_only(owner)
            control = owner._control
            authority = owner._cleanup_authority
        else:
            _scrub_exception(error)
    destination = paths = identity = source = runner = bridge_probe = tools = None  # type: ignore[assignment]
    invocation_driver = invocation_tools = None  # type: ignore[assignment]
    clock = wait = run = invocation_destination = budget = binding = None  # type: ignore[assignment]
    absolute_deadline = 0.0
    if control is not None:
        if type(owner) is RelayProbeOwner:
            with owner._lock:
                owner._control = None
        owner = None
        raise_control(control, authority)
    if owner is None:
        if type(authority) is RelayProbeCleanupAuthority:
            raise RelayProbeCleanupRequired(authority) from None
        raise RelayProbeOwnerError(_FAILURE) from None
    if type(authority) is RelayProbeCleanupAuthority:
        owner = None
        raise RelayProbeCleanupRequired(authority) from None
    return owner


@_sanitize_owner_boundary(_FAILURE)
def run_relay_probe(
    owner: RelayProbeOwner,
    *,
    static_auth_secret: object,
    now: datetime,
    browser_timeout_seconds: float,
) -> RelayProbeObservation:
    """Run once, then complete all reverse cleanup before any observation."""

    resolved = _resolve_owner(owner)
    if type(resolved) is not RelayProbeOwner or resolved is not owner:
        raise RelayProbeOwnerError(_FAILURE)
    with resolved._operation_lock:
        return _run_resolved(
            resolved,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
        )


def _run_resolved(
    resolved: RelayProbeOwner,
    *,
    static_auth_secret: object,
    now: datetime,
    browser_timeout_seconds: float,
) -> RelayProbeObservation:
    with resolved._lock:
        existing = resolved._observation
    forward_complete = type(existing) is RelayProbeObservation
    if not forward_complete:
        try:
            _run_forward(
                resolved,
                static_auth_secret=static_auth_secret,
                now=now,
                browser_timeout_seconds=browser_timeout_seconds,
            )
            forward_complete = True
        except BaseException as error:
            resolved._remember_exception(error)
            with resolved._lock:
                resolved._cleanup_only = True
                resolved._publish_requested = False
                resolved._state = "cleanup-only"
    static_auth_secret = now = None
    browser_timeout_seconds = 0.0
    complete, cleanup_boundary_failed = _cleanup_boundary(
        resolved,
        publish=forward_complete,
    )
    with resolved._lock:
        observation = resolved._observation
        first_control = resolved._control
        authority = resolved._cleanup_authority
    released = False
    release_control = None
    release_boundary_failed = False
    if complete:
        released, release_control, release_boundary_failed = _public_release_boundary(resolved)
    control = first_control or release_control
    if control is not None:
        with resolved._lock:
            if resolved._control is first_control:
                resolved._control = None
    resolved = None  # type: ignore[assignment]
    if control is not None:
        observation = None
        raise_control(control, None if complete and released else authority)
    if cleanup_boundary_failed or release_boundary_failed:
        observation = None
        raise RelayProbeOwnerError(_FAILURE) from None
    if type(observation) is RelayProbeObservation and complete and released:
        return observation
    if not complete or not released:
        raise RelayProbeCleanupRequired(authority) from None
    raise RelayProbeOwnerError(_FAILURE) from None


@_sanitize_owner_boundary("Relay probe cleanup failed")
def cleanup_relay_probe(
    owner: RelayProbeOwner | RelayProbeCleanupAuthority,
) -> None:
    """Retry only reverse cleanup through the aggregate's opaque root."""

    resolved = _resolve_owner(owner)
    if resolved is None and type(owner) is RelayProbeCleanupAuthority:
        if owner._is_authentic():
            return
        raise RelayProbeOwnerError("Relay probe cleanup failed")
    if type(resolved) is not RelayProbeOwner:
        raise RelayProbeOwnerError("Relay probe cleanup failed")
    with resolved._operation_lock:
        _cleanup_resolved(resolved)


def _cleanup_resolved(resolved: RelayProbeOwner) -> None:
    complete, cleanup_boundary_failed = _cleanup_boundary(resolved, publish=False)
    with resolved._lock:
        first_control = resolved._control
        authority = resolved._cleanup_authority
    released = False
    release_control = None
    release_boundary_failed = False
    if complete:
        released, release_control, release_boundary_failed = _public_release_boundary(resolved)
    control = first_control or release_control
    if control is not None:
        with resolved._lock:
            if resolved._control is first_control:
                resolved._control = None
    resolved = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, None if complete and released else authority)
    if cleanup_boundary_failed or release_boundary_failed:
        raise RelayProbeOwnerError("Relay probe cleanup failed") from None
    if not complete or not released:
        raise RelayProbeCleanupRequired(authority) from None


def _bounded_cleanup(owner: RelayProbeOwner, *, publish: bool) -> bool:
    complete = False
    for _ in range(_MAX_CLEANUP_PASSES):
        try:
            before_phase = owner._cleanup_phase
            before_authority = owner._pending_authority
            complete = _drive_cleanup(owner, publish=publish)
            if complete:
                break
            after_phase = owner._cleanup_phase
            after_authority = owner._pending_authority
        except BaseException as error:
            owner._remember_exception(error)
            with owner._lock:
                owner._cleanup_only = True
                owner._publish_requested = False
                if owner._state not in {"observed", "cleaned"}:
                    owner._state = "cleanup-only"
            return False
        if before_phase == after_phase and before_authority is after_authority:
            break
        publish = False
    return complete


def _cleanup_boundary(
    owner: RelayProbeOwner,
    *,
    publish: bool,
) -> tuple[bool, bool]:
    failed = False
    for _attempt in range(3):
        try:
            return _bounded_cleanup(owner, publish=publish), failed
        except BaseException as error:
            failed = failed or not isinstance(error, (KeyboardInterrupt, SystemExit))
            owner._remember_exception(error)
            with owner._lock:
                owner._cleanup_only = True
                if owner._state != "observed":
                    owner._publish_requested = False
                    if owner._state != "cleaned":
                        owner._state = "cleanup-only"
            publish = False
    return False, failed


def _recover_factory_owner(
    destination: object,
    binding: object,
    current: RelayProbeOwner | None,
) -> tuple[RelayProbeOwner | None, ControlSignal | None]:
    if type(current) is RelayProbeOwner and _owner_registered(current):
        _mark_factory_cleanup_only(current)
        return current, None
    control: ControlSignal | None = None
    candidate: RelayProbeOwner | None = None
    observed: object | None = None
    if type(destination) is RelayProbeOwnerDestination:
        for _attempt in range(3):
            try:
                candidate = _poison_registered_destination(destination)
                if candidate is not None:
                    return candidate, control
                break
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                _scrub_exception(error)
            except BaseException as error:
                _scrub_exception(error)
                break
    if type(destination) is RelayProbeOwnerDestination and type(binding) is tuple:
        for _attempt in range(3):
            try:
                observed = destination._read(binding)
                if type(observed) is RelayProbeOwner and _owner_registered(observed):
                    candidate = observed
                    _mark_factory_cleanup_only(candidate)
                break
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
                _scrub_exception(error)
                observed = None
            except BaseException as error:
                _scrub_exception(error)
                break
    destination = binding = current = observed = None
    return candidate, control


def _mark_factory_cleanup_only(owner: RelayProbeOwner) -> None:
    with owner._lock:
        owner._cleanup_only = True
        owner._publish_requested = False
        owner._state = "cleanup-only"


def _release_boundary(owner: RelayProbeOwner) -> tuple[bool, ControlSignal | None]:
    released = False
    control: ControlSignal | None = None
    for _attempt in range(_MAX_RELEASE_ATTEMPTS):
        try:
            released = bool(_release_owner(owner))
            if released:
                break
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    if released:
        return True, control
    for _attempt in range(_MAX_RELEASE_ATTEMPTS):
        try:
            return not _owner_registered(owner), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _public_release_boundary(
    owner: RelayProbeOwner,
) -> tuple[bool, ControlSignal | None, bool]:
    control: ControlSignal | None = None
    failed = False
    for _attempt in range(3):
        try:
            released, observed = _release_boundary(owner)
            control = control or observed
            return released, control, failed
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            failed = True
            _scrub_exception(error)
    return False, control, failed


__all__ = [
    "RelayProbeCleanupAuthority",
    "RelayProbeCleanupRequired",
    "RelayProbeObservation",
    "RelayProbeOwner",
    "RelayProbeOwnerDestination",
    "RelayProbeOwnerError",
    "cleanup_relay_probe",
    "new_relay_probe_owner",
    "new_relay_probe_owner_destination",
    "run_relay_probe",
]
