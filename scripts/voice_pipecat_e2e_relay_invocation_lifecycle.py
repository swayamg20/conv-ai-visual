"""Lifecycle for a synthetic, categorically non-qualifying relay invocation."""

from __future__ import annotations

import math
import threading
import time

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_invocation_cleanup import (
    RelayInvocationCleanupAuthority,
    RelayInvocationCleanupRequired,
    _cleanup_invocation_locked,
    _cleanup_invocation_owner,
    _drop_secrets,
    _invocation_recovery,
    _new_cleanup_authority,
    _raise_invocation_outcome,
    _recover_invocation_owner_publication,
    _register_cleanup_owner,
    _resolve_cleanup_owner,
    _sanitize_invocation_boundary,
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
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import (
    RelayPrebootstrapDestination,
    RelayPrebootstrapReceipt,
)
from scripts.voice_pipecat_e2e_relay_invocation_support import (
    _browser_command,
    _call_child_start,
    _call_relay_prebootstrap,
    _child_authority,
    _child_request,
    _load_secrets,
    _preown_child_authorities,
    _prepare_invocation_secrets,
    _read_exit_publication,
    _read_invocation_owner_destination,
    _read_prebootstrap_receipt,
    _reconcile_child_start,
    _reconcile_relay_backend,
    _reconcile_username_adoption,
    _require_relay_driver,
    _require_relay_tools,
    _scrub_exception,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    _FAILURE,
    _FINISH_TOKEN,
    RelayChildRequest,
    RelayFinishRequest,
    RelayInvocationError,
    RelayPlaywrightExitDestination,
    RelayPlaywrightExitReceipt,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeRun,
    replacement_relay_playwright_environment,
)

_OWNER_TOKEN = object()
_CLEANUP_FAILURE = "Relay invocation cleanup failed"
_MIN_FINISH_SECONDS, _MAX_FINISH_SECONDS = 0.01, 600.0
_RECEIPT_STATES = frozenset(
    {"backend-ready", "backend-bound", "web-ready", "browser-started", "browser-finished"}
)


class RelayInvocationOwner:
    """Caller-preowned staged owner retaining every child cleanup authority."""

    __slots__ = (
        "_app",
        "_app_start",
        "_app_stop",
        "_browser",
        "_browser_start",
        "_browser_stop",
        "_cleanup_authority",
        "_cleanup_phase",
        "_control",
        "_destination",
        "_driver",
        "_operation_lock",
        "_owner_token",
        "_prebootstrap_receipt",
        "_secret_key",
        "_state",
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
    ) -> None:
        if (
            token is not _OWNER_TOKEN
            or type(driver) is not RelayInvocationDriver
            or type(tools) is not RelayInvocationTools
            or type(run) is not RelayProbeRun
            or type(destination) is not _RelayInvocationOwnerDestination
            or type(cleanup_authority) is not RelayInvocationCleanupAuthority
        ):
            raise TypeError("Relay invocation owner is factory-owned")
        self._driver: RelayInvocationDriver | None = driver
        self._destination: _RelayInvocationOwnerDestination | None = destination
        self._tools: RelayInvocationTools | None = tools
        self._secret_key: object | None = secret_key
        self._owner_token = owner_token
        self._cleanup_authority = cleanup_authority
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
        _register_cleanup_owner(cleanup_authority, self)

    @property
    def concrete_adapter(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "RelayInvocationOwner(concrete_adapter=False)"

    def __copy__(self) -> None:
        raise TypeError("Relay invocation owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay invocation owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay invocation owner cannot be serialized")


def _new_relay_invocation_owner(
    run: RelayProbeRun,
    *,
    driver: RelayInvocationDriver,
    tools: RelayInvocationTools,
    destination: _RelayInvocationOwnerDestination,
) -> RelayInvocationOwner:
    """Return the owner published to one caller-preowned canonical sink."""

    owner: RelayInvocationOwner | None = None
    control: ControlSignal | None = None
    recovery: RelayInvocationCleanupAuthority | None = None
    failed = False
    try:
        if type(destination) is not _RelayInvocationOwnerDestination:
            raise RelayInvocationError(_FAILURE)
        with destination._lock:
            owner = _construct_relay_invocation_owner(run, driver, tools, destination)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        candidate = getattr(error, "cleanup_authority", None)
        if type(candidate) is RelayInvocationCleanupAuthority:
            recovery = candidate
        _scrub_exception(error)
    except RelayInvocationCleanupRequired as error:
        recovery = error.cleanup_authority
        _scrub_exception(error)
    except BaseException as error:
        failed = True
        _scrub_exception(error)
    run = driver = tools = destination = None  # type: ignore[assignment]
    if control is not None:
        owner = None
        raise_control(control, recovery)
    if recovery is not None:
        owner = None
        raise RelayInvocationCleanupRequired(recovery) from None
    if failed or type(owner) is not RelayInvocationOwner:
        owner = None
        raise RelayInvocationError(_FAILURE) from None
    return owner


def _construct_relay_invocation_owner(
    run: RelayProbeRun,
    driver: RelayInvocationDriver,
    tools: RelayInvocationTools,
    owner_destination: _RelayInvocationOwnerDestination,
) -> RelayInvocationOwner:
    existing, ready, control = _read_invocation_owner_destination(
        owner_destination, run, driver, tools
    )
    existing = _recover_invocation_owner_publication(existing, ready, control, RelayInvocationOwner)
    if type(existing) is RelayInvocationOwner:
        return existing
    secret_key = object()
    cleanup_authority = _new_cleanup_authority()
    failed = False
    owner: RelayInvocationOwner | None = None
    try:
        if (
            type(run) is not RelayProbeRun
            or type(driver) is not RelayInvocationDriver
            or type(tools) is not RelayInvocationTools
            or driver.concrete_adapter
            or tools.concrete_adapter
        ):
            raise RelayInvocationError(_FAILURE)
        owner_token = object()
        _prepare_invocation_secrets(run, tools, secret_key, owner_token)
        owner = RelayInvocationOwner(
            _OWNER_TOKEN,
            driver=driver,
            tools=tools,
            run=run,
            destination=owner_destination,
            secret_key=secret_key,
            owner_token=owner_token,
            cleanup_authority=cleanup_authority,
        )
        _preown_child_authorities(owner)
        owner._state = "created"
        owner_destination._publish_ready(owner)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        failed = True
        _scrub_exception(error)
    published, ready, read_control = _read_invocation_owner_destination(
        owner_destination, run, driver, tools
    )
    control = control or read_control
    if owner is None and type(published) is RelayInvocationOwner:
        owner = published
    if ready and type(owner) is RelayInvocationOwner:
        run = driver = tools = published = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control)
        return owner
    published = None
    if owner is not None and (failed or control is not None):
        cleanup_failed, cleanup_control = _cleanup_invocation_owner(owner)
        control = control or cleanup_control
        recovery = owner._cleanup_authority if cleanup_failed else None
        owner = None
        _raise_invocation_outcome(True, recovery, control)
    if owner is None:
        _drop_secrets(secret_key)
    if control is not None:
        raise_control(control)
    if owner is None or failed:
        raise RelayInvocationError(_FAILURE) from None
    return owner


@_sanitize_invocation_boundary(RelayInvocationOwner)
def stage_relay_backend(owner: RelayInvocationOwner) -> RelayPrebootstrapReceipt:
    """Start the guarded app, then publish its exact prebootstrap outcome."""
    receipt, failed, cleanup_failed, control = _forward_backend(owner)
    recovery = _invocation_recovery(owner, cleanup_failed)
    owner = None  # type: ignore[assignment]
    _raise_invocation_outcome(failed, recovery, control)
    assert receipt is not None
    return receipt


@_sanitize_invocation_boundary(RelayInvocationOwner)
def relay_prebootstrap_result(owner: RelayInvocationOwner) -> RelayPrebootstrapReceipt:
    """Read the same non-sensitive prebootstrap receipt without consuming it."""

    receipt: RelayPrebootstrapReceipt | None = None
    control: ControlSignal | None = None
    failed = False
    try:
        if type(owner) is not RelayInvocationOwner:
            raise RelayInvocationError(_FAILURE)
        with owner._operation_lock:
            receipt = _read_prebootstrap_receipt(owner, _RECEIPT_STATES)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
        failed = True
    owner = None  # type: ignore[assignment]
    if control is not None:
        receipt = None
        raise_control(control)
    if failed or receipt is None:
        receipt = None
        raise RelayInvocationError(_FAILURE) from None
    return receipt


@_sanitize_invocation_boundary(RelayInvocationOwner)
def _adopt_expected_turn_username(owner: RelayInvocationOwner, sink: object) -> None:
    """Trusted friend seam for one preowned, idempotent aggregate sink."""
    control: ControlSignal | None = None
    failed = False
    cleanup_failed = False
    destination: RelayPrebootstrapDestination | None = None
    if type(owner) is not RelayInvocationOwner:
        sink = owner = None
        raise RelayInvocationError(_FAILURE) from None
    with owner._operation_lock:
        try:
            if owner._state == "backend-bound":
                return
            if owner._state != "backend-ready":
                raise RelayInvocationError(_FAILURE)
            destination = _load_secrets(owner._secret_key).prebootstrap_destination
            if type(destination) is not RelayPrebootstrapDestination:
                raise RelayInvocationError(_FAILURE)
            committed, reconcile_control = _reconcile_username_adoption(
                destination, owner._owner_token
            )
            control = control or reconcile_control
            for _attempt in range(2):
                if committed:
                    break
                try:
                    committed = destination._adopt_username(owner._owner_token, sink)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                    _scrub_exception(error)
                except BaseException as error:
                    _scrub_exception(error)
                committed, reconcile_control = _reconcile_username_adoption(
                    destination, owner._owner_token
                )
                control = control or reconcile_control
            if committed:
                owner._state = "backend-bound"
            else:
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
        if failed:
            cleanup_failed, cleanup_control = _cleanup_invocation_locked(owner)
            control = control or cleanup_control
    recovery = _invocation_recovery(owner, cleanup_failed)
    owner = sink = destination = None
    _raise_invocation_outcome(failed, recovery, control)


@_sanitize_invocation_boundary(RelayInvocationOwner)
def stage_relay_web(owner: RelayInvocationOwner) -> None:
    """Start the exact web server only after the private username handoff."""
    failed, cleanup_failed, control = _start_stage(
        owner,
        expected_state="backend-bound",
        committed_states={"web-ready", "browser-started", "browser-finished"},
        starting_state="web-starting",
        complete_state="web-ready",
        authority_name="_web",
        destination_name="_web_start",
        request_name="web",
    )
    recovery = _invocation_recovery(owner, cleanup_failed)
    owner = None  # type: ignore[assignment]
    _raise_invocation_outcome(failed, recovery, control)


@_sanitize_invocation_boundary(RelayInvocationOwner)
def start_relay_playwright(owner: RelayInvocationOwner) -> None:
    """Start Playwright nonblocking only after exact browser authorization."""
    failed, cleanup_failed, control = _forward_browser(owner)
    recovery = _invocation_recovery(owner, cleanup_failed)
    owner = None  # type: ignore[assignment]
    _raise_invocation_outcome(failed, recovery, control)


@_sanitize_invocation_boundary(RelayInvocationOwner)
def finish_relay_playwright(
    owner: RelayInvocationOwner,
    *,
    timeout_seconds: float,
) -> RelayPlaywrightExitReceipt:
    """Wait to one immutable deadline and publish exact successful child exit."""

    receipt, failed, cleanup_failed, control = _finish_browser(owner, timeout_seconds)
    recovery = _invocation_recovery(owner, cleanup_failed)
    owner = None  # type: ignore[assignment]
    timeout_seconds = 0.0
    if control is not None:
        receipt = None
    _raise_invocation_outcome(failed, recovery, control)
    assert receipt is not None
    return receipt


@_sanitize_invocation_boundary(RelayInvocationOwner, always_recovery=True)
def cleanup_relay_invocation(
    owner: RelayInvocationOwner | RelayInvocationCleanupAuthority,
) -> None:
    """Idempotently settle all published child authorities in reverse order."""

    owned = _resolve_cleanup_owner(owner, RelayInvocationOwner)
    if owned is None and type(owner) is RelayInvocationCleanupAuthority:
        return
    if type(owned) is not RelayInvocationOwner:
        owner = owned = None  # type: ignore[assignment]
        raise RelayInvocationError(_CLEANUP_FAILURE) from None
    failed, control = _cleanup_invocation_owner(owned)
    recovery = owned._cleanup_authority if failed else None
    owner = owned = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control, recovery)
    if recovery is not None:
        raise RelayInvocationCleanupRequired(recovery) from None


def _forward_backend(
    owner: RelayInvocationOwner,
) -> tuple[RelayPrebootstrapReceipt | None, bool, bool, ControlSignal | None]:
    if type(owner) is not RelayInvocationOwner:
        return None, True, False, None
    receipt: RelayPrebootstrapReceipt | None = None
    control: ControlSignal | None = None
    failed = False
    cleanup_failed = False
    with owner._operation_lock:
        try:
            if owner._state in _RECEIPT_STATES:
                receipt = _read_prebootstrap_receipt(owner, _RECEIPT_STATES)
            elif owner._state not in {"created", "app-ready"}:
                raise RelayInvocationError(_FAILURE)
            else:
                if owner._state == "created":
                    secrets = _load_secrets(owner._secret_key)
                    request = secrets.backend
                    if type(request) is not RelayChildRequest:
                        raise RelayInvocationError(_FAILURE)
                    owner._state = "app-starting"
                    committed, step_control = _call_child_start(
                        owner, "_app", "_app_start", "app", request
                    )
                    control = control or step_control
                    if committed:
                        owner._state = "app-ready"
                    else:
                        failed = True
                if not failed and control is None and owner._state == "app-ready":
                    receipt, step_control = _call_relay_prebootstrap(owner)
                    control = control or step_control
                    failed = receipt is None
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
        reconciled, reconcile_control = _reconcile_relay_backend(owner)
        control = control or reconcile_control
        if type(reconciled) is RelayPrebootstrapReceipt:
            receipt = reconciled
            failed = False
        elif owner._state == "app-ready" and control is not None:
            failed = False
        if failed:
            cleanup_failed, cleanup_control = _cleanup_invocation_locked(owner)
            control = control or cleanup_control
    return receipt, failed, cleanup_failed, control


def _forward_browser(owner: RelayInvocationOwner) -> tuple[bool, bool, ControlSignal | None]:
    if type(owner) is not RelayInvocationOwner:
        return True, False, None
    control: ControlSignal | None = None
    failed = False
    cleanup_failed = False
    with owner._operation_lock:
        try:
            if owner._state in {"browser-started", "browser-finished"}:
                return False, False, None
            if owner._state != "web-ready":
                raise RelayInvocationError(_FAILURE)
            tools = _require_relay_tools(owner)
            secrets = _load_secrets(owner._secret_key)
            run = secrets.run
            if type(run) is not RelayProbeRun:
                raise RelayInvocationError(_FAILURE)
            request = _child_request(
                role="browser",
                command=_browser_command(tools),
                cwd=tools._web_root,
                environment=replacement_relay_playwright_environment(run),
                completion="started",
            )
            secrets.browser = request
            owner._state = "browser-starting"
            committed, control = _call_child_start(
                owner, "_browser", "_browser_start", "browser", request
            )
            if committed:
                owner._state = "browser-started"
            else:
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
        reconciled, reconcile_control = _reconcile_child_start(
            owner, "_browser_start", "browser", "browser-started"
        )
        control = control or reconcile_control
        if reconciled:
            failed = False
        if failed:
            cleanup_failed, cleanup_control = _cleanup_invocation_locked(owner)
            control = control or cleanup_control
    return failed, cleanup_failed, control


def _start_stage(
    owner: RelayInvocationOwner,
    *,
    expected_state: str,
    committed_states: set[str],
    starting_state: str,
    complete_state: str,
    authority_name: str,
    destination_name: str,
    request_name: str,
) -> tuple[bool, bool, ControlSignal | None]:
    if type(owner) is not RelayInvocationOwner:
        return True, False, None
    control: ControlSignal | None = None
    failed = False
    cleanup_failed = False
    with owner._operation_lock:
        try:
            if owner._state in committed_states:
                return False, False, None
            if owner._state != expected_state:
                raise RelayInvocationError(_FAILURE)
            request = getattr(_load_secrets(owner._secret_key), request_name)
            if type(request) is not RelayChildRequest:
                raise RelayInvocationError(_FAILURE)
            owner._state = starting_state
            committed, control = _call_child_start(
                owner, authority_name, destination_name, request_name, request
            )
            if committed:
                owner._state = complete_state
            else:
                failed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
        reconciled, reconcile_control = _reconcile_child_start(
            owner, destination_name, request_name, complete_state
        )
        control = control or reconcile_control
        if reconciled:
            failed = False
        if failed:
            cleanup_failed, cleanup_control = _cleanup_invocation_locked(owner)
            control = control or cleanup_control
    return failed, cleanup_failed, control


def _finish_browser(
    owner: RelayInvocationOwner,
    timeout_seconds: float,
) -> tuple[RelayPlaywrightExitReceipt | None, bool, bool, ControlSignal | None]:
    if type(owner) is not RelayInvocationOwner:
        return None, True, False, None
    receipt: RelayPlaywrightExitReceipt | None = None
    control: ControlSignal | None = None
    failed = False
    cleanup_failed = False
    browser = driver = request = None
    destination: RelayPlaywrightExitDestination | None = None
    with owner._operation_lock:
        try:
            destination = _load_secrets(owner._secret_key).exit_destination
            if type(destination) is not RelayPlaywrightExitDestination:
                raise RelayInvocationError(_FAILURE)
            if owner._state == "browser-finished":
                receipt, read_control = _read_exit_publication(destination, owner._owner_token)
                control = control or read_control
            else:
                if (
                    owner._state != "browser-started"
                    or type(timeout_seconds) is not float
                    or not math.isfinite(timeout_seconds)
                    or not _MIN_FINISH_SECONDS <= timeout_seconds <= _MAX_FINISH_SECONDS
                ):
                    raise RelayInvocationError(_FAILURE)
                now = time.monotonic()
                deadline = now + timeout_seconds
                if not math.isfinite(deadline) or deadline <= now:
                    raise RelayInvocationError(_FAILURE)
                request = RelayFinishRequest(_FINISH_TOKEN, absolute_deadline=deadline)
                driver = _require_relay_driver(owner)
                browser = _child_authority(owner, "_browser", "browser")
                owner._state = "browser-finishing"
                try:
                    driver._finish(browser, request, destination)
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control_signal(error)
                    _scrub_exception(error)
                except BaseException as error:
                    _scrub_exception(error)
                receipt, read_control = _read_exit_publication(destination, owner._owner_token)
                control = control or read_control
                if receipt is not None:
                    owner._state = "browser-finished"
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
            failed = True
        except BaseException as error:
            _scrub_exception(error)
            failed = True
        if destination is not None:
            receipt, read_control = _read_exit_publication(destination, owner._owner_token)
            control = control or read_control
            if receipt is not None:
                owner._state = "browser-finished"
                failed = False
            else:
                failed = True
        browser = destination = driver = request = None
        timeout_seconds = 0.0
        if failed:
            cleanup_failed, cleanup_control = _cleanup_invocation_locked(owner)
            control = control or cleanup_control
    return receipt, failed, cleanup_failed, control


__all__: list[str] = []
