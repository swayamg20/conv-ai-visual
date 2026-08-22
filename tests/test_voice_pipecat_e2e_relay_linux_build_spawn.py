"""Synthetic tests for the dormant registered-before-init Next build seam."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import subprocess
import sys
import threading
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module
import scripts.voice_pipecat_e2e_relay_linux_build_spawn_local as local_module

RUN_ID = "relay-b0-build"


def _environment(run_id: str = RUN_ID) -> dict[str, str]:
    return {
        **spawn_module._FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{run_id}",
    }


def _spec(tmp_path: Path, *, environment: dict[str, str] | None = None):
    return spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id=RUN_ID,
        environment=_environment() if environment is None else environment,
    )


def _traceback_contains_identity(error: BaseException, target: object) -> bool:
    trace = error.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code.co_filename == local_module.__file__ and any(
            value is target for value in trace.tb_frame.f_locals.values()
        ):
            return True
        trace = trace.tb_next
    return False


class _PartialProcess:
    pass


def test_spec_is_exact_falsey_private_and_defensively_projects_environment(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    argv, cwd, environment = spec._spawn_values()

    assert not spec
    assert repr(spec) == "_RelayLinuxBuildSpec()"
    assert argv == (
        str((tmp_path / "node").resolve()),
        str((tmp_path / "next").resolve()),
        "build",
        "--webpack",
    )
    assert cwd == (tmp_path / "workspace").resolve()
    assert environment == _environment()
    environment["VOICE_E2E_NEXT_DIST_DIR"] = "changed"
    assert spec._spawn_values()[2] == _environment()
    assert spawn_module.__all__ == []
    assert local_module.__all__ == []


@pytest.mark.parametrize(
    "name",
    [
        "VOICE_E2E_CALL_ID",
        "VOICE_E2E_NETWORK",
        "VOICE_E2E_ARTIFACT_DIR",
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE",
        "VOICE_E2E_RESULT_PATH",
        "MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE",
        "MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID",
        "SSL_CERT_FILE",
        "TURN_USERNAME",
        "NODE_OPTIONS",
        "HTTP_PROXY",
    ],
)
def test_positive_environment_allowlist_rejects_sensitive_or_ambient_names(
    tmp_path: Path,
    name: str,
) -> None:
    environment = _environment()
    environment[name] = "poisoned"
    with pytest.raises(
        spawn_module._RelayLinuxBuildSpawnError,
        match=r"spawn contract is invalid$",
    ):
        _spec(tmp_path, environment=environment)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("CI", "0"),
        ("NEXT_PUBLIC_API_URL", "https://attacker.invalid"),
        ("NO_PROXY", "*"),
        ("VOICE_E2E_NEXT_DIST_DIR", ".next/direct"),
    ],
)
def test_fixed_environment_values_cannot_drift(
    tmp_path: Path,
    mutation: str,
    value: str,
) -> None:
    environment = _environment()
    environment[mutation] = value
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        _spec(tmp_path, environment=environment)


def test_spec_and_raw_destination_refuse_copy_pickle_and_mutation(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)

    assert not destination
    assert repr(destination) == "_RawBuildProcessDestination()"
    with pytest.raises(AttributeError):
        spec._cwd = tmp_path  # type: ignore[misc]
    for value in (spec, destination):
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)


def test_destination_is_single_assignment_and_identity_cleared(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)
    process = _PartialProcess()
    conflicting = _PartialProcess()

    destination.publish(process)
    destination.publish(process)
    assert destination._read(spec) is process
    with pytest.raises(TypeError, match=r"publication is invalid$"):
        destination.publish(conflicting)
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        destination._clear(spec, conflicting)
    assert destination._clear(spec, process) is True
    assert destination._read(spec) is None
    assert destination._clear(spec, process) is True


def test_destination_rejects_cross_spec_read_clear_and_adapter_use(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    other = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "other-workspace").resolve(),
        run_id=RUN_ID,
        environment=_environment(),
    )
    destination = spawn_module._new_raw_build_process_destination(spec)
    process = _PartialProcess()
    destination.publish(process)

    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        destination._read(other)
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        destination._clear(other, process)
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        local_module._spawn_registered_relay_linux_build(other, destination)
    assert destination._read(spec) is process


def test_adapter_publishes_preinit_identity_and_uses_only_exact_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)
    process = _PartialProcess()
    events: list[str] = []
    captured: dict[str, object] = {}

    def factory(argv: tuple[str, ...], **options: object) -> object:
        events.append("factory-entered")
        captured["argv"] = argv
        captured.update(options)
        register = options["owner_register"]
        assert callable(register)
        assert not hasattr(process, "initialized")
        register(process)
        events.append("registered-preinit")
        process.initialized = True
        events.append("initialized")
        return process

    monkeypatch.setattr(local_module, "registered_popen_factory", factory)
    local_module._spawn_registered_relay_linux_build(spec, destination)

    assert destination._read(spec) is process
    assert events == ["factory-entered", "registered-preinit", "initialized"]
    assert captured == {
        "argv": spec._spawn_values()[0],
        "owner_register": destination.publish,
        "executable": spec._spawn_values()[0][0],
        "cwd": spec._spawn_values()[1],
        "env": spec._spawn_values()[2],
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
        "close_fds": True,
        "start_new_session": True,
        "umask": 0o077,
    }


@pytest.mark.parametrize(
    "failure", [RuntimeError("lost return"), KeyboardInterrupt(), SystemExit(19)]
)
def test_callback_return_loss_retains_the_exact_partial_process(
    tmp_path: Path,
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)
    process = _PartialProcess()

    def factory(_argv: tuple[str, ...], **options: object) -> object:
        register = options["owner_register"]
        assert callable(register)
        register(process)
        raise failure

    monkeypatch.setattr(local_module, "registered_popen_factory", factory)
    expected = (
        spawn_module._RelayLinuxBuildSpawnError if type(failure) is RuntimeError else type(failure)
    )
    with pytest.raises(expected) as raised:
        local_module._spawn_registered_relay_linux_build(spec, destination)
    if isinstance(failure, SystemExit):
        assert raised.value.code == 19
    assert destination._read(spec) is process
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    for target in (failure, spec, destination, process):
        assert not _traceback_contains_identity(raised.value, target)


def test_unregistered_or_conflicting_factory_return_is_never_a_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)

    monkeypatch.setattr(
        local_module,
        "registered_popen_factory",
        lambda *_args, **_kwargs: _PartialProcess(),
    )
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        local_module._spawn_registered_relay_linux_build(spec, destination)
    assert destination._read(spec) is None

    registered = _PartialProcess()
    returned = _PartialProcess()

    def conflicting(_argv: tuple[str, ...], **options: object) -> object:
        register = options["owner_register"]
        assert callable(register)
        register(registered)
        return returned

    monkeypatch.setattr(local_module, "registered_popen_factory", conflicting)
    with pytest.raises(spawn_module._RelayLinuxBuildSpawnError):
        local_module._spawn_registered_relay_linux_build(spec, destination)
    assert destination._read(spec) is registered


def test_concurrent_publication_retains_exactly_one_identity(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    destination = spawn_module._new_raw_build_process_destination(spec)
    candidates = [_PartialProcess(), _PartialProcess()]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def publish(candidate: object) -> None:
        barrier.wait()
        try:
            destination.publish(candidate)
            outcomes.append("published")
        except TypeError:
            outcomes.append("refused")

    threads = [threading.Thread(target=publish, args=(candidate,)) for candidate in candidates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    retained = destination._read(spec)
    assert retained in candidates
    assert sorted(outcomes) == ["published", "refused"]


def test_private_constructors_reject_external_tokens(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"spec is factory-owned$"):
        spawn_module._RelayLinuxBuildSpec(
            object(),
            node=(tmp_path / "node").resolve(),
            next_cli=(tmp_path / "next").resolve(),
            workspace=(tmp_path / "workspace").resolve(),
            environment=tuple(sorted(_environment().items())),
            run_id=RUN_ID,
        )
