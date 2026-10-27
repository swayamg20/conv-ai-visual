"""Sole local OS and loopback HTTP effect boundary for the process adapter."""

from __future__ import annotations

import http.client
import math
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import registered_popen_factory

__all__: list[str] = []

_FAILURE = "Relay concrete process local effect is invalid"
_MAX_RESPONSE_BYTES = 65536
_UNAVAILABLE = object()


def _spawn_checkpoint(_stage: str) -> None:
    """Deterministic cut seam around durable registered spawn facts."""


def _local_monotonic() -> float:
    import time

    return time.monotonic()


def _registered_spawn(
    argv: tuple[str, ...],
    *,
    cwd: object,
    environment: dict[str, str],
    owner_register: Callable[[object], None],
) -> object:
    """Prepublish the raw Popen object before its fork/exec implementation."""

    if (
        type(argv) is not tuple
        or not argv
        or not all(type(part) is str and part for part in argv)
        or not isinstance(cwd, Path)
        or not cwd.is_absolute()
        or type(environment) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in environment.items())
        or not callable(owner_register)
    ):
        raise TypeError(_FAILURE)

    def register(process: object) -> None:
        owner_register(process)
        _spawn_checkpoint("registered")

    process = registered_popen_factory(
        argv,
        owner_register=register,
        executable=argv[0],
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        close_fds=True,
        start_new_session=True,
        umask=0o077,
    )
    _spawn_checkpoint("initialized")
    return process


def _require_pid(pid: object) -> None:
    if type(pid) is not int or pid <= 1:
        raise TypeError(_FAILURE)


def _process_pid(process: object) -> object:
    return process.pid  # type: ignore[attr-defined]


def _process_group(pid: int) -> object:
    _require_pid(pid)
    return os.getpgid(pid)


def _group_exists(pgid: int) -> bool:
    _require_pid(pgid)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_group(pgid: int, value: int) -> bool:
    _require_pid(pgid)
    if value not in (signal.SIGTERM, signal.SIGKILL):
        raise TypeError(_FAILURE)
    try:
        os.killpg(pgid, value)
    except ProcessLookupError:
        return False
    return True


def _observe_terminal_unreaped(pid: int) -> object:
    """Observe terminal status without reaping through WNOWAIT on Linux."""

    waitid = getattr(os, "waitid", None)
    waitid_result = getattr(os, "waitidResult", None)
    _require_pid(pid)
    if not callable(waitid) or waitid_result is None:
        return _UNAVAILABLE
    result = waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if result is None:
        return None
    if type(result) is not waitid_result or result.si_pid != pid:
        raise RuntimeError(_FAILURE)
    return result


def _reap_once(pid: int) -> object:
    """Reap the exact PID once and return its exit status snapshot."""

    _require_pid(pid)
    try:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
    except OSError:
        return None
    if type(waited_pid) is not int or waited_pid != pid:
        return None
    if os.WIFEXITED(status):
        return (os.WEXITSTATUS(status), None)
    if os.WIFSIGNALLED(status):
        return (None, os.WTERMSIG(status))
    return None


def _process_alive(pid: int) -> bool | None:
    _require_pid(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    return True


def _live_group_members(pgid: int) -> object:
    """Snapshot live members of one process group while the leader anchors it."""

    _require_pid(pgid)
    if not os.path.isdir("/proc"):
        return _UNAVAILABLE
    try:
        directory = os.listdir("/proc")
    except OSError:
        return _UNAVAILABLE
    if type(directory) is not list:
        raise RuntimeError(_FAILURE)
    members: list[int] = []
    for name in directory:
        if not name.isdigit():
            continue
        entry_pid = int(name)
        try:
            with open(f"/proc/{entry_pid}/stat", "rb") as handle:
                content = handle.read(65537)
        except FileNotFoundError:
            continue
        if len(content) > 65536 or b") " not in content:
            raise RuntimeError(_FAILURE)
        fields = content.rpartition(b") ")[2].split()
        if len(fields) < 3:
            raise RuntimeError(_FAILURE)
        try:
            observed_pgid = int(fields[2])
        except ValueError:
            raise RuntimeError(_FAILURE) from None
        if observed_pgid != pgid:
            continue
        if fields[0] in {b"Z", b"X", b"x"}:
            continue
        members.append(entry_pid)
    return members


def _process_handle(process: object, name: str) -> object:
    return getattr(process, name)


def _close_handle(handle: object) -> object:
    return handle.close()  # type: ignore[attr-defined]


def _handle_closed(handle: object) -> object:
    return handle.closed  # type: ignore[attr-defined]


class _RelayLoopbackTransport:
    """Private literal-loopback HTTP transport with proxy and redirect refusal."""

    __slots__ = ("_host",)

    def __init__(self, host: str) -> None:
        if host != "127.0.0.1":
            raise TypeError(_FAILURE)
        object.__setattr__(self, "_host", host)

    def request(
        self,
        *,
        port: int,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> object:
        if (
            type(port) is not int
            or not 0 < port < 65536
            or method not in {"GET", "POST"}
            or type(path) is not str
            or not path.startswith("/")
            or path.startswith("//")
            or type(headers) is not dict
            or headers.get("Host") != f"127.0.0.1:{port}"
            or (method == "GET") != (body is None)
            or (method == "POST" and type(body) is not bytes)
            or (method == "POST" and len(body) > _MAX_RESPONSE_BYTES)
            or type(timeout_seconds) is not float
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0.0
        ):
            raise TypeError(_FAILURE)
        connection = http.client.HTTPConnection(
            self._host,
            port,
            timeout=timeout_seconds,
        )
        try:
            connection.connect()
            if connection.sock is None or connection.sock.getpeername()[:2] != (self._host, port):
                raise RuntimeError(_FAILURE)
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            status = response.status
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if type(status) is not int or type(payload) is not bytes:
                raise RuntimeError(_FAILURE)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError(_FAILURE)
            if 300 <= status < 400:
                raise RuntimeError(_FAILURE)
            return (status, dict(response.getheaders()), payload)
        finally:
            connection.close()


def _new_loopback_transport(host: object) -> object:
    if host != "127.0.0.1":
        raise TypeError(_FAILURE)
    return _RelayLoopbackTransport("127.0.0.1")


def _signal_value(name: str) -> object:
    if name == "term":
        return signal.SIGTERM
    if name == "kill":
        return signal.SIGKILL
    raise TypeError(_FAILURE)


def _terminal_status_is_zero(observation: object) -> bool:
    """True only for an observed unreaped zero exit through waitidResult."""

    waitid_result = getattr(os, "waitidResult", None)
    if waitid_result is None or type(observation) is not waitid_result:
        return False
    exit_status = getattr(observation, "si_status", None)
    code = getattr(observation, "si_code", None)
    return bool(type(exit_status) is int and exit_status == 0 and code == os.CLD_EXITED)


def _members_include_only(members: object, pid: int) -> bool:
    """True when the snapshot contains only the unreaped leader."""

    if type(members) is not list:
        return False
    return all(type(member) is int and member == pid for member in members)
