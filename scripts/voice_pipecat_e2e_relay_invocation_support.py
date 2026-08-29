"""Private construction, command, and sensitive-state support for relay invocation."""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _DRIVER_TOKEN,
    _OWNER_DESTINATION_TOKEN,
    _TOOLS_TOKEN,
    RelayInvocationDriver,
    RelayInvocationTools,
    _RelayChildAuthorityDestination,
    _RelayChildStartDestination,
    _RelayChildStopDestination,
    _RelayInvocationOwnerDestination,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import (
    _DESTINATION_TOKEN,
    RelayPrebootstrapDestination,
    RelayPrebootstrapReceipt,
    RelayPrebootstrapRequest,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import (
    _REQUEST_TOKEN as _PREBOOTSTRAP_REQUEST_TOKEN,
)
from scripts.voice_pipecat_e2e_relay_invocation_values import (
    _EXIT_DESTINATION_TOKEN,
    _FAILURE,
    _REQUEST_TOKEN,
    RelayChildRequest,
    RelayFinishRequest,
    RelayInvocationError,
    RelayPlaywrightExitDestination,
    RelayPlaywrightExitReceipt,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeRun,
    replacement_relay_backend_environment,
    replacement_relay_web_environment,
)
from scripts.voice_pipecat_e2e_stack import (
    PIPECAT_HOST,
    PIPECAT_PORT,
    PROJECT_ROOT,
    WEB_HOST,
    WEB_PORT,
    WEB_ROOT,
)

_SECRET_LOCK = threading.Lock()
_SECRET_RECORDS: dict[object, _InvocationSecrets] = {}
_MAX_ACTIVE_INVOCATIONS = 32
_ROLES = ("app", "web", "browser")


@dataclass(slots=True)
class _InvocationSecrets:
    run: RelayProbeRun | None
    backend: RelayChildRequest | None
    web: RelayChildRequest | None
    prebootstrap_request: RelayPrebootstrapRequest | None
    prebootstrap_destination: RelayPrebootstrapDestination | None
    exit_destination: RelayPlaywrightExitDestination | None
    browser: RelayChildRequest | None = None

    def scrub(self) -> None:
        for request in (self.backend, self.web, self.browser):
            if request is not None:
                request._scrub()
        if self.prebootstrap_request is not None:
            self.prebootstrap_request._scrub()
        if self.prebootstrap_destination is not None:
            self.prebootstrap_destination._scrub()
        self.run = None
        self.backend = self.web = self.browser = None
        self.prebootstrap_request = None
        self.prebootstrap_destination = None
        self.exit_destination = None


def new_synthetic_relay_invocation_tools(
    *,
    node_executable: Path,
    epoch_clock: Callable[[], float] = time.time,
) -> RelayInvocationTools:
    """Build an explicitly non-qualifying tools receipt for structural tests."""

    tools: RelayInvocationTools | None = None
    control: ControlSignal | None = None
    try:
        node = _canonical_file(node_executable, executable=True)
        web_root = _canonical_directory(WEB_ROOT)
        next_cli = _canonical_file(web_root / "node_modules/next/dist/bin/next")
        playwright_cli = _canonical_file(web_root / "node_modules/@playwright/test/cli.js")
        if not callable(epoch_clock):
            raise RelayInvocationError(_FAILURE)
        tools = RelayInvocationTools(
            _TOOLS_TOKEN,
            node=node,
            web_root=web_root,
            next_cli=next_cli,
            playwright_cli=playwright_cli,
            epoch_clock=epoch_clock,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    node_executable = epoch_clock = None  # type: ignore[assignment]
    node = web_root = next_cli = playwright_cli = None
    if control is not None:
        tools = None
        raise_control(control)
    if tools is None:
        raise RelayInvocationError(_FAILURE) from None
    return tools


def new_relay_invocation_driver(
    *,
    preown: Callable[[str, _RelayChildAuthorityDestination], None],
    start: Callable[[object, RelayChildRequest, _RelayChildStartDestination], None],
    prebootstrap: Callable[[object, RelayPrebootstrapRequest, RelayPrebootstrapDestination], None],
    finish: Callable[[object, RelayFinishRequest, RelayPlaywrightExitDestination], None],
    stop: Callable[[object, _RelayChildStopDestination], None],
) -> RelayInvocationDriver:
    """Wrap structural callbacks in a falsey, non-qualifying capability."""

    driver: RelayInvocationDriver | None = None
    control: ControlSignal | None = None
    try:
        driver = RelayInvocationDriver(
            _DRIVER_TOKEN,
            preown=preown,
            start=start,
            prebootstrap=prebootstrap,
            finish=finish,
            stop=stop,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    preown = start = prebootstrap = finish = stop = None  # type: ignore[assignment]
    if control is not None:
        driver = None
        raise_control(control)
    if driver is None:
        raise RelayInvocationError(_FAILURE) from None
    return driver


def _new_relay_invocation_owner_destination() -> _RelayInvocationOwnerDestination:
    return _RelayInvocationOwnerDestination(_OWNER_DESTINATION_TOKEN)


def _read_invocation_owner_destination(
    destination: _RelayInvocationOwnerDestination,
    run: object,
    driver: RelayInvocationDriver,
    tools: RelayInvocationTools,
) -> tuple[object | None, bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            owner, ready = destination._read(run, driver, tools)
            return owner, ready, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            raise RelayInvocationError(_FAILURE) from None
    return None, False, control


def _prepare_invocation_secrets(
    run: RelayProbeRun,
    tools: RelayInvocationTools,
    secret_key: object,
    owner_token: object,
) -> None:
    backend_environment = replacement_relay_backend_environment(run)
    web_environment = replacement_relay_web_environment(run)
    call_id = backend_environment.get("MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID")
    if type(call_id) is not str:
        raise RelayInvocationError(_FAILURE)
    backend = _child_request(
        role="app",
        command=_app_command(),
        cwd=_canonical_directory(PROJECT_ROOT),
        environment=backend_environment,
        completion="ready",
    )
    web = _child_request(
        role="web",
        command=_web_command(tools),
        cwd=tools._web_root,
        environment=web_environment,
        completion="ready",
    )
    _store_secrets(
        secret_key,
        _InvocationSecrets(
            run=run,
            backend=backend,
            web=web,
            prebootstrap_request=RelayPrebootstrapRequest(
                _PREBOOTSTRAP_REQUEST_TOKEN, call_id=call_id
            ),
            prebootstrap_destination=RelayPrebootstrapDestination(
                _DESTINATION_TOKEN,
                owner_token=owner_token,
                call_id=call_id,
                clock=tools._epoch_clock,
            ),
            exit_destination=RelayPlaywrightExitDestination(
                _EXIT_DESTINATION_TOKEN, owner_token=owner_token
            ),
        ),
    )
    backend_environment = web_environment = backend = web = None
    call_id = ""


def _child_request(
    *,
    role: str,
    command: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    completion: str,
    absolute_deadline: float | None = None,
) -> RelayChildRequest:
    expected_completion = "started" if role == "browser" else "ready"
    if (
        role not in _ROLES
        or completion != expected_completion
        or not command
        or not all(type(value) is str and value for value in command)
        or not cwd.is_absolute()
        or type(environment) is not dict
        or not all(type(key) is str and type(value) is str for key, value in environment.items())
    ):
        raise RelayInvocationError(_FAILURE)
    return RelayChildRequest(
        _REQUEST_TOKEN,
        role=role,
        command=command,
        cwd=cwd,
        environment=environment,
        completion=completion,
        absolute_deadline=absolute_deadline,
    )


def _app_command() -> tuple[str, ...]:
    executable = _canonical_file(Path(sys.executable).resolve(), executable=True)
    return (
        str(executable),
        "-m",
        "uvicorn",
        "scripts.voice_pipecat_e2e_app:app",
        "--host",
        PIPECAT_HOST,
        "--port",
        str(PIPECAT_PORT),
        "--workers",
        "1",
        "--no-access-log",
        "--no-server-header",
        "--limit-concurrency",
        "100",
        "--lifespan",
        "on",
    )


def _web_command(tools: RelayInvocationTools) -> tuple[str, ...]:
    return (
        str(tools._node),
        str(tools._next_cli),
        "start",
        "--hostname",
        WEB_HOST,
        "--port",
        str(WEB_PORT),
    )


def _browser_command(tools: RelayInvocationTools) -> tuple[str, ...]:
    return (
        str(tools._node),
        str(tools._playwright_cli),
        "test",
        "e2e/voice-pipecat-rtc.spec.ts",
    )


def _canonical_directory(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise RelayInvocationError(_FAILURE)
    _require_no_symlink_components(value)
    try:
        details = value.stat(follow_symlinks=False)
    except OSError:
        raise RelayInvocationError(_FAILURE) from None
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise RelayInvocationError(_FAILURE)
    return value


def _canonical_file(value: object, *, executable: bool = False) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise RelayInvocationError(_FAILURE)
    _require_no_symlink_components(value)
    try:
        details = value.stat(follow_symlinks=False)
    except OSError:
        raise RelayInvocationError(_FAILURE) from None
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_uid != os.geteuid()
        or details.st_nlink != 1
        or (executable and not os.access(value, os.X_OK))
    ):
        raise RelayInvocationError(_FAILURE)
    return value


def _require_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            details = current.lstat()
        except OSError:
            raise RelayInvocationError(_FAILURE) from None
        if stat.S_ISLNK(details.st_mode):
            raise RelayInvocationError(_FAILURE)


def _store_secrets(key: object, secrets: _InvocationSecrets) -> None:
    with _SECRET_LOCK:
        if key in _SECRET_RECORDS or len(_SECRET_RECORDS) >= _MAX_ACTIVE_INVOCATIONS:
            raise RelayInvocationError(_FAILURE)
        _SECRET_RECORDS[key] = secrets


def _load_secrets(key: object) -> _InvocationSecrets:
    with _SECRET_LOCK:
        secrets = _SECRET_RECORDS.get(key)
    if type(secrets) is not _InvocationSecrets:
        raise RelayInvocationError(_FAILURE)
    return secrets


def _preown_child_authorities(owner: object) -> None:
    driver = getattr(owner, "_driver", None)
    if type(driver) is not RelayInvocationDriver:
        raise RelayInvocationError(_FAILURE)
    observed: list[object] = []
    for role, attribute in (("app", "_app"), ("web", "_web"), ("browser", "_browser")):
        destination = getattr(owner, attribute, None)
        if type(destination) is not _RelayChildAuthorityDestination:
            raise RelayInvocationError(_FAILURE)
        control: ControlSignal | None = None
        try:
            driver._preown(role, destination)
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
        authority, read_control = _read_child_authority(destination)
        control = control or read_control
        if authority is None:
            if control is not None:
                raise_control(control)
            raise RelayInvocationError(_FAILURE) from None
        if any(authority is current for current in observed):
            raise RelayInvocationError(_FAILURE)
        observed.append(authority)
        destination = authority = None
        if control is not None:
            observed.clear()
            raise_control(control)
    observed.clear()


def _call_child_start(
    owner: object,
    authority_name: str,
    destination_name: str,
    role: str,
    request: RelayChildRequest,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    driver = getattr(owner, "_driver", None)
    if type(driver) is not RelayInvocationDriver:
        raise RelayInvocationError(_FAILURE)
    authority = _child_authority(owner, authority_name, role)
    destination = getattr(owner, destination_name, None)
    if type(destination) is not _RelayChildStartDestination:
        raise RelayInvocationError(_FAILURE)
    try:
        driver._start(authority, request, destination)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    committed, read_control = _read_start_receipt(owner, destination, role)
    control = control or read_control
    authority = destination = driver = request = None  # type: ignore[assignment]
    return committed, control


def _reconcile_child_start(
    owner: object,
    destination_name: str,
    role: str,
    complete_state: str,
) -> tuple[bool, ControlSignal | None]:
    destination = getattr(owner, destination_name, None)
    if type(destination) is not _RelayChildStartDestination:
        return False, None
    committed, control = _read_start_receipt(owner, destination, role)
    if committed:
        if getattr(owner, "_state", None) not in {"cleanup-required", "cleaned"}:
            owner._state = complete_state
    return committed, control


def _child_authority(owner: object, attribute: str, role: str) -> object:
    destination = getattr(owner, attribute, None)
    if type(destination) is not _RelayChildAuthorityDestination:
        raise RelayInvocationError(_FAILURE)
    control: ControlSignal | None = None
    authority: object | None = None
    for _attempt in range(2):
        try:
            authority = destination._read(role)
            break
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    if control is not None:
        authority = None
        raise_control(control)
    if authority is None:
        raise RelayInvocationError(_FAILURE) from None
    return authority


def _read_child_authority(
    destination: _RelayChildAuthorityDestination,
) -> tuple[object | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._read(destination._role), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return None, control


def _read_start_receipt(
    owner: object,
    destination: _RelayChildStartDestination,
    role: str,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            destination._read(getattr(owner, "_owner_token", None), role)
            return True, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _require_relay_driver(owner: object) -> RelayInvocationDriver:
    driver = getattr(owner, "_driver", None)
    if type(driver) is not RelayInvocationDriver:
        raise RelayInvocationError(_FAILURE)
    return driver


def _require_relay_tools(owner: object) -> RelayInvocationTools:
    tools = getattr(owner, "_tools", None)
    if type(tools) is not RelayInvocationTools:
        raise RelayInvocationError(_FAILURE)
    return tools


def _read_prebootstrap_receipt(
    owner: object, valid_states: frozenset[str]
) -> RelayPrebootstrapReceipt:
    receipt = getattr(owner, "_prebootstrap_receipt", None)
    if (
        type(receipt) is not RelayPrebootstrapReceipt
        or getattr(owner, "_state", None) not in valid_states
        or not receipt._matches(getattr(owner, "_owner_token", None))
    ):
        raise RelayInvocationError(_FAILURE)
    return receipt


def _read_prebootstrap_publication(
    destination: RelayPrebootstrapDestination,
    owner_token: object,
) -> tuple[RelayPrebootstrapReceipt | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._read(owner_token), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return None, control


def _read_exit_publication(
    destination: RelayPlaywrightExitDestination,
    owner_token: object,
) -> tuple[RelayPlaywrightExitReceipt | None, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._read(owner_token), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return None, control


def _call_relay_prebootstrap(
    owner: object,
) -> tuple[RelayPrebootstrapReceipt | None, ControlSignal | None]:
    control: ControlSignal | None = None
    secrets = _load_secrets(getattr(owner, "_secret_key", None))
    request = secrets.prebootstrap_request
    destination = secrets.prebootstrap_destination
    if (
        type(request) is not RelayPrebootstrapRequest
        or type(destination) is not RelayPrebootstrapDestination
    ):
        raise RelayInvocationError(_FAILURE)
    driver = _require_relay_driver(owner)
    app = _child_authority(owner, "_app", "app")
    owner._state = "prebootstrap-running"  # type: ignore[attr-defined]
    try:
        driver._prebootstrap(app, request, destination)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    receipt, read_control = _read_prebootstrap_publication(
        destination, getattr(owner, "_owner_token", None)
    )
    control = control or read_control
    if receipt is not None:
        _commit_prebootstrap(owner, receipt)
    app = driver = request = destination = secrets = None
    return receipt, control


def _reconcile_relay_backend(
    owner: object,
) -> tuple[RelayPrebootstrapReceipt | None, ControlSignal | None]:
    _committed, control = _reconcile_child_start(owner, "_app_start", "app", "app-ready")
    try:
        secrets = _load_secrets(getattr(owner, "_secret_key", None))
        destination = secrets.prebootstrap_destination
        if type(destination) is not RelayPrebootstrapDestination:
            return None, control
        receipt, read_control = _read_prebootstrap_publication(
            destination, getattr(owner, "_owner_token", None)
        )
        control = control or read_control
        if receipt is not None:
            _commit_prebootstrap(owner, receipt)
        return receipt, control
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or control_signal(error)
        _scrub_exception(error)
        return None, control
    except BaseException as error:
        _scrub_exception(error)
        return None, control


def _commit_prebootstrap(owner: object, receipt: RelayPrebootstrapReceipt) -> None:
    owner._prebootstrap_receipt = receipt  # type: ignore[attr-defined]
    owner._state = "backend-ready"  # type: ignore[attr-defined]
    secrets = _load_secrets(getattr(owner, "_secret_key", None))
    request = secrets.prebootstrap_request
    if request is not None:
        request._scrub()
        secrets.prebootstrap_request = None


def _reconcile_username_adoption(
    destination: RelayPrebootstrapDestination,
    owner_token: object,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _attempt in range(2):
        try:
            return destination._reconcile_adoption(owner_token), control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return False, control


def _scrub_exception(error: BaseException) -> None:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            candidate
            for candidate in (current.__cause__, current.__context__)
            if isinstance(candidate, BaseException)
        )
        trace = current.__traceback__
        current.__traceback__ = None
        current.__cause__ = None
        current.__context__ = None
        current.__suppress_context__ = True
        if trace is not None:
            try:
                traceback.clear_frames(trace)
            except BaseException:
                pass
        trace = None
        current = None
    pending.clear()
    seen.clear()


__all__: list[str] = []
