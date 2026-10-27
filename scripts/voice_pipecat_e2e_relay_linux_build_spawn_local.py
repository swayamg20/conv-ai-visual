"""Dormant synchronous Popen registration primitive for a future build worker.

This function has no timeout or cleanup owner and must not be called on an
executor/control thread. A later slice will place it inside a dedicated build
controller before anything can invoke it.
"""

from __future__ import annotations

import subprocess
import traceback

from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import registered_popen_factory
from scripts.voice_pipecat_e2e_relay_linux_build_spawn import (
    _RawBuildProcessDestination,
    _RelayLinuxBuildSpawnError,
    _RelayLinuxBuildSpec,
)

_FAILURE = "Relay Linux build spawn contract is invalid"


def _spawn_registered_relay_linux_build(
    spec: _RelayLinuxBuildSpec,
    destination: _RawBuildProcessDestination,
) -> None:
    """Invoke the sole trusted registered-Popen helper; retain before init."""

    invalid = bool(
        type(spec) is not _RelayLinuxBuildSpec
        or type(destination) is not _RawBuildProcessDestination
    )
    if not invalid:
        invalid = not spec._matches_destination(destination)
    status = "failed" if invalid else "pending"
    exit_code: int | None = None
    if not invalid:
        status, exit_code = _invoke_registered_spawn(spec, destination)
    spec = destination = None  # type: ignore[assignment]
    if status == "keyboard-interrupt":
        raise KeyboardInterrupt() from None
    if status == "system-exit":
        raise SystemExit(exit_code) from None
    if status != "complete":
        raise _RelayLinuxBuildSpawnError(_FAILURE) from None


def _invoke_registered_spawn(
    spec: _RelayLinuxBuildSpec,
    destination: _RawBuildProcessDestination,
) -> tuple[str, int | None]:
    argv, cwd, environment = spec._spawn_values()
    returned: object | None = None
    status = "pending"
    exit_code: int | None = None
    try:
        returned = registered_popen_factory(
            argv,
            owner_register=destination.publish,
            executable=argv[0],
            cwd=cwd,
            env=environment.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            start_new_session=True,
            umask=0o077,
        )
    except KeyboardInterrupt as error:
        status = "keyboard-interrupt"
        _scrub_exception(error)
    except SystemExit as error:
        status = "system-exit"
        exit_code = error.code if error.code is None or type(error.code) is int else 1
        _scrub_exception(error)
    except BaseException as error:
        status = "failed"
        _scrub_exception(error)
    try:
        registered = destination._read(spec)
    except KeyboardInterrupt as error:
        status = "keyboard-interrupt"
        registered = None
        _scrub_exception(error)
    except SystemExit as error:
        status = "system-exit"
        exit_code = error.code if error.code is None or type(error.code) is int else 1
        registered = None
        _scrub_exception(error)
    except BaseException as error:
        status = "failed"
        registered = None
        _scrub_exception(error)
    argv = ()
    cwd = None  # type: ignore[assignment]
    environment.clear()
    spec = destination = None  # type: ignore[assignment]
    if status == "pending":
        status = "complete" if returned is registered and registered is not None else "failed"
    returned = registered = None
    return status, exit_code


def _scrub_exception(error: BaseException) -> None:
    trace = error.__traceback__
    error.__traceback__ = None
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    if trace is not None:
        try:
            traceback.clear_frames(trace)
        except BaseException:
            pass


__all__: list[str] = []
