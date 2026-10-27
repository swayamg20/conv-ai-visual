"""Local seam ownership and fail-closed checks with no real child or network."""

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import voice_pipecat_e2e_relay_invocation_process_local as local


def test_spawn_registers_raw_child_before_initialization(monkeypatch):
    owned = []
    options = {}

    class Process:
        def __init__(self, argv, **kwargs):
            assert owned == [self]
            assert argv == ("/python", "entry.py")
            options.update(kwargs)

    monkeypatch.setattr(local.subprocess, "Popen", Process)
    monkeypatch.setattr(local.os, "umask", lambda *_: pytest.fail("parent umask changed"))
    environment = {"TOKEN": "owned"}
    process = local._registered_spawn(
        ("/python", "entry.py"),
        cwd=Path("/workspace"),
        environment=environment,
        owner_register=owned.append,
    )
    assert owned == [process]
    assert options["env"] == environment and options["env"] is not environment
    assert options["umask"] == 0o077
    assert options["shell"] is False
    assert options["close_fds"] is True
    assert options["start_new_session"] is True
    assert all(options[key] == local.subprocess.DEVNULL for key in ("stdin", "stdout", "stderr"))


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_spawn_initialization_loss_retains_exact_raw_child(monkeypatch, failure):
    owned = []

    class Process:
        def __init__(self, *_args, **_kwargs):
            assert owned == [self]
            raise failure()

    monkeypatch.setattr(local.subprocess, "Popen", Process)
    with pytest.raises(failure):
        local._registered_spawn(
            ("/python",),
            cwd=Path("/workspace"),
            environment={},
            owner_register=owned.append,
        )
    assert len(owned) == 1 and type(owned[0]) is Process


def test_unavailable_waitid_is_distinct_from_running(monkeypatch):
    monkeypatch.setattr(local.os, "waitid", None, raising=False)
    observation = local._observe_terminal_unreaped(123)
    assert observation is local._UNAVAILABLE


def test_waitid_checks_exact_pid_without_reaping(monkeypatch):
    class Result:
        si_pid = 456

    monkeypatch.setattr(local.os, "waitidResult", Result, raising=False)
    monkeypatch.setattr(local.os, "P_PID", 1, raising=False)
    monkeypatch.setattr(local.os, "WEXITED", 4, raising=False)
    monkeypatch.setattr(local.os, "waitid", lambda *_: Result(), raising=False)
    monkeypatch.setattr(local.os, "waitpid", lambda *_: pytest.fail("early reap"))
    with pytest.raises(RuntimeError):
        local._observe_terminal_unreaped(123)
    Result.si_pid = 123
    assert type(local._observe_terminal_unreaped(123)) is Result


@pytest.mark.parametrize("pid", [-1, 0, 1, True])
def test_invalid_pid_never_reaches_signal(monkeypatch, pid):
    monkeypatch.setattr(local.os, "killpg", lambda *_: pytest.fail("signal reached"))
    with pytest.raises(TypeError):
        local._signal_group(pid, local.signal.SIGTERM)


def test_proc_permission_failure_is_not_absence(monkeypatch):
    monkeypatch.setattr(local.os.path, "isdir", lambda path: True)
    monkeypatch.setattr(local.os, "listdir", lambda _: ["123"])

    def unreadable(*_args, **_kwargs):
        raise PermissionError()

    monkeypatch.setattr("builtins.open", unreadable)
    with pytest.raises(PermissionError):
        local._live_group_members(123)
    monkeypatch.setattr("builtins.open", lambda *_: io.BytesIO(b"malformed"))
    with pytest.raises(RuntimeError):
        local._live_group_members(123)


def test_missing_proc_returns_unavailable_not_error(monkeypatch):
    monkeypatch.setattr(local.os.path, "isdir", lambda path: False)
    assert local._live_group_members(123) is local._UNAVAILABLE


@pytest.mark.parametrize("status,payload", [(302, b"redirect"), (200, b"x" * 65537)])
def test_http_rejects_redirect_and_oversize_and_closes(monkeypatch, status, payload):
    closed = []

    class Connection:
        sock = SimpleNamespace(getpeername=lambda: ("127.0.0.1", 1234))

        def __init__(self, host, port, **_kwargs):
            assert (host, port) == ("127.0.0.1", 1234)

        def connect(self):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda limit: payload[:limit])

        def close(self):
            closed.append(True)

    monkeypatch.setattr(local.http.client, "HTTPConnection", Connection)
    with pytest.raises(RuntimeError):
        local._RelayLoopbackTransport("127.0.0.1").request(
            port=1234,
            method="GET",
            path="/health",
            headers={"Host": "127.0.0.1:1234"},
            body=None,
            timeout_seconds=1.0,
        )
    assert closed == [True]
