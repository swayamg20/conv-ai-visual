"""Synthetic transaction tests for Coturn network absence; no Docker is run."""

from __future__ import annotations

import copy
import json
import pickle
import stat
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_runtime as runtime_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_runtime_directory as directory_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_runtime_network as network_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_private_cleanup as private_cleanup_module,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    establish_network_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    CommandRequest,
    CommandResult,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    CoturnDirectorySyncCleanupRequired,
    CoturnRuntimeError,
    CoturnRuntimePrivateCleanupRequired,
    RuntimePrivateCleanupAuthority,
    cleanup_directory_sync_authority,
    cleanup_owned_network,
    cleanup_runtime_private_authority,
    finalize_network_absence,
    recover_network_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_network import (  # noqa: E402
    NetworkAbsenceReceipt,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    NETWORK_ID,
    network_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (  # noqa: E402
    plan as make_network_plan,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    _interrupt_before_line,
    _interrupt_on_return,
    _source_line,
)


@dataclass
class NetworkRunner:
    values: list[object]
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert type(value) is CommandResult
        return value

    def start_attached(self, request: CommandRequest) -> object:
        raise AssertionError("network transaction tests never start attached")


def _json(value: object) -> CommandResult:
    return CommandResult(0, json.dumps(value).encode("ascii"), b"")


def _owned(tmp_path: Path):
    paths = _paths(tmp_path)
    plan = make_network_plan(paths)
    inspection = network_inspection(plan)
    authority = establish_network_cleanup_authority(
        plan=plan,
        network_id=NETWORK_ID,
        inspection=inspection,
    )
    runtime_module._write_network_plan_receipt(paths, plan=plan)
    runtime_module._write_network_receipt(paths, plan=plan, network_id=NETWORK_ID)
    return paths, plan, inspection, authority


def _operations(runner: NetworkRunner) -> list[tuple[str, str]]:
    return [request.argv[5:7] for request in runner.requests]


def test_network_removal_commits_durable_absence_before_finalizing_receipts(
    tmp_path: Path,
) -> None:
    paths, _plan, inspection, authority = _owned(tmp_path)
    runner = NetworkRunner(
        [
            _json(inspection),
            CommandResult(0, (NETWORK_ID + "\n").encode("ascii"), b""),
            CommandResult(0, b"", b""),
        ]
    )

    absence = cleanup_owned_network(runner=runner, tools=_tools(), authority=authority)

    assert type(absence) is NetworkAbsenceReceipt
    assert _operations(runner) == [
        ("network", "inspect"),
        ("network", "rm"),
        ("network", "ls"),
    ]
    assert paths.network_absence_receipt.exists()
    assert stat.S_IMODE(paths.network_absence_receipt.stat().st_mode) == 0o600
    marker = json.loads(paths.network_absence_receipt.read_text(encoding="ascii"))
    assert marker["full_id"] == NETWORK_ID
    assert marker["state"] == "absent"
    assert paths.network_receipt.exists()
    assert paths.network_plan_receipt.exists()

    for operation in (
        lambda: copy.copy(absence),
        lambda: copy.deepcopy(absence),
        lambda: pickle.dumps(absence),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()

    finalize_network_absence(absence)
    assert absence.finalization_complete
    assert not paths.network_absence_receipt.exists()
    assert not paths.network_receipt.exists()
    assert not paths.network_plan_receipt.exists()


def test_restart_after_remove_before_marker_reconciles_only_from_exact_id_receipt(
    tmp_path: Path,
) -> None:
    paths, plan, _inspection, _authority = _owned(tmp_path)
    raw = b"traceback-sentinel-network-inspect-missing"
    runner = NetworkRunner(
        [
            CommandResult(1, b"", raw),
            CommandResult(0, b"", b""),
        ]
    )

    absence = recover_network_cleanup_authority(
        runner=runner,
        tools=_tools(),
        plan=plan,
    )

    assert type(absence) is NetworkAbsenceReceipt
    assert _operations(runner) == [("network", "inspect"), ("network", "ls")]
    assert runner.requests[-1].argv[-1] == f"id={NETWORK_ID}"
    assert paths.network_absence_receipt.exists()
    assert raw.decode() not in repr(absence)


def test_name_only_recovery_never_infers_absence_from_missing_inspect(
    tmp_path: Path,
) -> None:
    paths, plan, _inspection, _authority = _owned(tmp_path)
    paths.network_receipt.unlink()
    raw = b"traceback-sentinel-name-only-missing"
    runner = NetworkRunner(
        [
            CommandResult(1, b"", raw),
            CommandResult(0, b"", b""),
        ]
    )

    with pytest.raises(RuntimeError, match="network recovery inspection failed") as error:
        recover_network_cleanup_authority(runner=runner, tools=_tools(), plan=plan)

    assert len(runner.requests) == 1
    assert runner.requests[0].argv[-1] == plan.identity.network_name
    assert not paths.network_absence_receipt.exists()
    assert not traceback_contains(error.value, raw)


def test_marker_write_without_directory_sync_publishes_no_receipt_and_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, plan, inspection, authority = _owned(tmp_path)
    real_sync = network_module._sync_control_directory
    sync_calls = 0

    def fail_first_sync(candidate) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise KeyboardInterrupt("untrusted-directory-sync-cut")
        real_sync(candidate)

    monkeypatch.setattr(network_module, "_sync_control_directory", fail_first_sync)
    runner = NetworkRunner(
        [
            _json(inspection),
            CommandResult(0, NETWORK_ID.encode("ascii"), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(KeyboardInterrupt) as error:
        cleanup_owned_network(runner=runner, tools=_tools(), authority=authority)

    assert str(error.value) == ""
    assert paths.network_absence_receipt.exists()
    assert paths.network_receipt.exists()
    assert paths.network_plan_receipt.exists()

    recovered = recover_network_cleanup_authority(
        runner=NetworkRunner([CommandResult(0, b"", b"")]),
        tools=_tools(),
        plan=plan,
    )
    assert type(recovered) is NetworkAbsenceReceipt
    assert sync_calls == 2


def test_worker_return_control_publishes_no_receipt_and_leaks_no_fd_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, plan, inspection, authority = _owned(tmp_path)
    real_sync = network_module._sync_control_directory
    real_fstat = directory_module.os.fstat
    real_wait = directory_module.TlsOwnerService._wait_for_task
    armed = False
    opened = directory_module.threading.Event()
    release = directory_module.threading.Event()
    interrupted = False

    def arm_then_sync(path: Path) -> None:
        nonlocal armed
        armed = True
        real_sync(path)

    def pause_after_open(descriptor: int):
        details = real_fstat(descriptor)
        if armed and not opened.is_set():
            opened.set()
            assert release.wait(1.0)
        return details

    def interrupt_owner_wait(service, task) -> bool:
        nonlocal interrupted
        if type(task).__module__ == directory_module.__name__ and not interrupted:
            assert opened.wait(1.0)
            interrupted = True
            release.set()
            raise KeyboardInterrupt("untrusted-open-return-cut")
        return real_wait(service, task)

    monkeypatch.setattr(network_module, "_sync_control_directory", arm_then_sync)
    monkeypatch.setattr(directory_module.os, "fstat", pause_after_open)
    monkeypatch.setattr(
        directory_module.TlsOwnerService,
        "_wait_for_task",
        interrupt_owner_wait,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    assert str(error.value) == ""
    assert not hasattr(error.value, "cleanup_authority")
    assert interrupted
    assert paths.network_absence_receipt.exists()
    assert paths.network_receipt.exists()

    monkeypatch.setattr(network_module, "_sync_control_directory", real_sync)
    monkeypatch.setattr(directory_module.os, "fstat", real_fstat)
    monkeypatch.setattr(directory_module.TlsOwnerService, "_wait_for_task", real_wait)
    recovered = recover_network_cleanup_authority(
        runner=NetworkRunner([CommandResult(0, b"", b"")]),
        tools=_tools(),
        plan=plan,
    )
    assert type(recovered) is NetworkAbsenceReceipt


def test_ambiguous_directory_close_returns_only_opaque_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, plan, inspection, authority = _owned(tmp_path)
    real_close = directory_module.close_owned_descriptor

    def ambiguous_close(_descriptor, _identity, _control) -> bool:
        return False

    monkeypatch.setattr(directory_module, "close_owned_descriptor", ambiguous_close)
    with pytest.raises(CoturnDirectorySyncCleanupRequired) as error:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    cleanup_authority = error.value.cleanup_authority
    assert repr(cleanup_authority) == "DirectorySyncCleanupAuthority()"
    assert paths.network_absence_receipt.exists()
    assert paths.network_receipt.exists()

    monkeypatch.setattr(directory_module, "close_owned_descriptor", real_close)
    cleanup_directory_sync_authority(cleanup_authority)
    recovered = recover_network_cleanup_authority(
        runner=NetworkRunner([CommandResult(0, b"", b"")]),
        tools=_tools(),
        plan=plan,
    )
    assert type(recovered) is NetworkAbsenceReceipt


def test_directory_control_with_ambiguous_close_preserves_first_control_and_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, plan, inspection, authority = _owned(tmp_path)
    real_close = directory_module.close_owned_descriptor

    def controlled_ambiguous_close(_descriptor, _identity, control) -> bool:
        control.record((KeyboardInterrupt, None))
        return False

    monkeypatch.setattr(
        directory_module,
        "close_owned_descriptor",
        controlled_ambiguous_close,
    )
    with pytest.raises(KeyboardInterrupt) as error:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    cleanup_authority = error.value.cleanup_authority  # type: ignore[attr-defined]
    assert str(error.value) == ""
    assert repr(cleanup_authority) == "DirectorySyncCleanupAuthority()"
    assert paths.network_absence_receipt.exists()

    monkeypatch.setattr(directory_module, "close_owned_descriptor", real_close)
    cleanup_directory_sync_authority(cleanup_authority)
    recovered = recover_network_cleanup_authority(
        runner=NetworkRunner([CommandResult(0, b"", b"")]),
        tools=_tools(),
        plan=plan,
    )
    assert type(recovered) is NetworkAbsenceReceipt


def test_directory_close_return_cut_reconciles_closing_state_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, _plan, inspection, authority = _owned(tmp_path)
    real_close = directory_module.close_owned_descriptor
    monkeypatch.setattr(
        directory_module,
        "close_owned_descriptor",
        lambda _descriptor, _identity, _control: False,
    )
    with pytest.raises(CoturnDirectorySyncCleanupRequired) as first:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    cleanup_authority = first.value.cleanup_authority
    monkeypatch.setattr(directory_module, "close_owned_descriptor", real_close)

    with pytest.raises(KeyboardInterrupt) as cut:
        _interrupt_on_return(
            target_code=real_close.__code__,
            operation=lambda: cleanup_directory_sync_authority(cleanup_authority),
        )
    assert str(cut.value) == ""
    assert cut.value.cleanup_authority is cleanup_authority  # type: ignore[attr-defined]
    cleanup_directory_sync_authority(cleanup_authority)


def test_directory_cleaned_state_cut_clears_descriptor_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, _plan, inspection, authority = _owned(tmp_path)
    real_close = directory_module.close_owned_descriptor
    monkeypatch.setattr(
        directory_module,
        "close_owned_descriptor",
        lambda _descriptor, _identity, _control: False,
    )
    with pytest.raises(CoturnDirectorySyncCleanupRequired) as first:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    cleanup_authority = first.value.cleanup_authority
    monkeypatch.setattr(directory_module, "close_owned_descriptor", real_close)
    clear_line = _source_line(
        type(cleanup_authority)._close,
        "self._descriptor = None",
        occurrence=-1,
    )

    with pytest.raises(KeyboardInterrupt) as cut:
        _interrupt_before_line(
            target_code=type(cleanup_authority)._close.__code__,
            line_number=clear_line,
            operation=lambda: cleanup_directory_sync_authority(cleanup_authority),
        )

    assert str(cut.value) == ""
    assert cut.value.cleanup_authority is cleanup_authority  # type: ignore[attr-defined]
    cleanup_directory_sync_authority(cleanup_authority)


def test_concurrent_directory_cleanup_closes_exact_descriptor_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, _plan, inspection, authority = _owned(tmp_path)
    monkeypatch.setattr(
        directory_module,
        "close_owned_descriptor",
        lambda _descriptor, _identity, _control: False,
    )
    with pytest.raises(CoturnDirectorySyncCleanupRequired) as first:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    cleanup_authority = first.value.cleanup_authority
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls = 0
    errors: list[BaseException] = []

    def close_once(_descriptor, _identity, _control) -> bool:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1.0)
        return True

    def cleanup(*, second: bool = False) -> None:
        if second:
            second_started.set()
        try:
            cleanup_directory_sync_authority(cleanup_authority)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(directory_module, "close_owned_descriptor", close_once)
    first_thread = threading.Thread(target=cleanup)
    second_thread = threading.Thread(target=lambda: cleanup(second=True))
    first_thread.start()
    assert entered.wait(1.0)
    second_thread.start()
    assert second_started.wait(1.0)
    release.set()
    first_thread.join(1.0)
    second_thread.join(1.0)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert errors == []
    assert calls == 1


def test_concurrent_runtime_private_cleanup_invokes_hidden_authority_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden = object()
    source = RuntimeError("untrusted-private-source")
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: hidden if error is source else None,
    )
    cleanup_authority = private_cleanup_module._runtime_private_cleanup_authority(source)
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    calls = 0
    errors: list[BaseException] = []

    def cleanup_hidden(candidate: object) -> None:
        nonlocal calls
        assert candidate is hidden
        calls += 1
        entered.set()
        assert release.wait(1.0)

    def cleanup(*, second: bool = False) -> None:
        if second:
            second_started.set()
        try:
            cleanup_runtime_private_authority(cleanup_authority)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        cleanup_hidden,
    )
    first_thread = threading.Thread(target=cleanup)
    second_thread = threading.Thread(target=lambda: cleanup(second=True))
    first_thread.start()
    assert entered.wait(1.0)
    second_thread.start()
    assert second_started.wait(1.0)
    release.set()
    first_thread.join(1.0)
    second_thread.join(1.0)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert errors == []
    assert calls == 1


def test_finalizer_retries_unlink_control_and_removes_absence_marker_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _plan, inspection, authority = _owned(tmp_path)
    absence = cleanup_owned_network(
        runner=NetworkRunner(
            [
                _json(inspection),
                CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                CommandResult(0, b"", b""),
            ]
        ),
        tools=_tools(),
        authority=authority,
    )
    real_unlink = Path.unlink
    calls: list[Path] = []
    cut = False

    def unlink_then_cut(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal cut
        calls.append(path)
        real_unlink(path, *args, **kwargs)
        if path == paths.network_receipt and not cut:
            cut = True
            raise SystemExit(73)

    monkeypatch.setattr(Path, "unlink", unlink_then_cut)
    with pytest.raises(SystemExit) as error:
        finalize_network_absence(absence)
    assert error.value.code == 73
    assert paths.network_absence_receipt.exists()
    assert not paths.network_receipt.exists()
    assert paths.network_plan_receipt.exists()
    assert not absence.finalization_complete

    finalize_network_absence(absence)
    assert absence.finalization_complete
    assert calls[-1] == paths.network_absence_receipt
    assert not paths.network_absence_receipt.exists()


def test_finalizer_sync_failure_never_claims_completion_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _plan, inspection, authority = _owned(tmp_path)
    absence = cleanup_owned_network(
        runner=NetworkRunner(
            [
                _json(inspection),
                CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                CommandResult(0, b"", b""),
            ]
        ),
        tools=_tools(),
        authority=authority,
    )
    real_sync = network_module._sync_control_directory
    sync_calls = 0

    def fail_once(path: Path) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise MemoryError("untrusted-finalizer-sync-cut")
        real_sync(path)

    monkeypatch.setattr(network_module, "_sync_control_directory", fail_once)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn network absence finalization failed$",
    ) as error:
        finalize_network_absence(absence)
    assert not absence.finalization_complete
    assert not traceback_contains(error.value, "untrusted-finalizer-sync-cut")
    assert paths.network_absence_receipt.exists()
    assert not paths.network_receipt.exists()
    assert not paths.network_plan_receipt.exists()

    finalize_network_absence(absence)
    assert absence.finalization_complete
    assert sync_calls == 3


def test_malformed_absence_result_and_marker_never_escape_raw_values(tmp_path: Path) -> None:
    paths, plan, inspection, authority = _owned(tmp_path)
    raw = b"traceback-sentinel-network-absence"
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn network cleanup failed$") as error:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, raw, b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )
    assert not traceback_contains(error.value, raw)
    assert not paths.network_absence_receipt.exists()

    paths.network_absence_receipt.write_text(
        '{"full_id":"traceback-sentinel-marker"}\n',
        encoding="ascii",
    )
    paths.network_absence_receipt.chmod(0o600)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn network absence recovery failed$",
    ) as marker_error:
        recover_network_cleanup_authority(
            runner=NetworkRunner([]),
            tools=_tools(),
            plan=plan,
        )
    assert not traceback_contains(marker_error.value, "traceback-sentinel-marker")


def test_private_marker_failure_returns_only_runtime_owned_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _paths_value, _plan, inspection, authority = _owned(tmp_path)
    raw = "traceback-sentinel-private-marker"
    failure = MemoryError(raw)
    private_authority = object()
    cleanup_calls: list[object] = []

    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: private_authority if error is failure else None,
    )
    monkeypatch.setattr(
        network_module,
        "write_owned_file_exclusive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(CoturnRuntimePrivateCleanupRequired) as caught:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )

    cleanup_authority = caught.value.cleanup_authority
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    assert repr(cleanup_authority) == "RuntimePrivateCleanupAuthority()"
    assert repr(caught.value) == (
        "CoturnRuntimePrivateCleanupRequired('Coturn runtime private-file cleanup failed')"
    )
    assert not traceback_contains(caught.value, raw)

    def clean(candidate: object) -> None:
        cleanup_calls.append(candidate)

    monkeypatch.setattr(private_cleanup_module, "cleanup_tls_private_authority", clean)
    cleanup_runtime_private_authority(cleanup_authority)
    cleanup_runtime_private_authority(cleanup_authority)
    assert cleanup_calls == [private_authority]


@pytest.mark.parametrize(
    "failure",
    [
        KeyboardInterrupt("traceback-sentinel-private-control"),
        SystemExit(23),
    ],
)
def test_private_marker_control_preserves_sanitized_control_and_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: KeyboardInterrupt | SystemExit,
) -> None:
    _paths_value, _plan, inspection, authority = _owned(tmp_path)
    raw = "traceback-sentinel-private-control"
    private_authority = object()
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: private_authority if error is failure else None,
    )
    monkeypatch.setattr(
        network_module,
        "write_owned_file_exclusive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(type(failure)) as caught:
        cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=authority,
        )

    cleanup_authority = caught.value.cleanup_authority  # type: ignore[attr-defined]
    if type(failure) is SystemExit:
        assert caught.value.code == 23
    else:
        assert str(caught.value) == ""
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    assert not traceback_contains(caught.value, raw)
    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        lambda candidate: None,
    )
    cleanup_runtime_private_authority(cleanup_authority)


def test_runtime_private_cleanup_retries_replacement_and_reconciles_publish_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = object()
    replacement = object()
    source = MemoryError("untrusted-private-source")
    cleanup_failure = MemoryError("untrusted-private-cleanup")
    cleanup_calls: list[object] = []
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: initial if error is source else replacement,
    )
    authority = private_cleanup_module._runtime_private_cleanup_authority(source)
    assert type(authority) is RuntimePrivateCleanupAuthority

    def fail_once(candidate: object) -> None:
        cleanup_calls.append(candidate)
        raise cleanup_failure

    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        fail_once,
    )
    with pytest.raises(CoturnRuntimePrivateCleanupRequired) as first:
        cleanup_runtime_private_authority(authority)
    assert first.value.cleanup_authority is authority

    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        lambda candidate: cleanup_calls.append(candidate),
    )
    clear_line = _source_line(
        type(authority)._cleanup,
        "self._authority = None",
        occurrence=-1,
    )
    with pytest.raises(KeyboardInterrupt) as cut:
        _interrupt_before_line(
            target_code=type(authority)._cleanup.__code__,
            line_number=clear_line,
            operation=lambda: cleanup_runtime_private_authority(authority),
        )
    assert str(cut.value) == ""
    assert cut.value.cleanup_authority is authority  # type: ignore[attr-defined]

    cleanup_runtime_private_authority(authority)
    cleanup_runtime_private_authority(authority)
    assert cleanup_calls == [initial, replacement]


@pytest.mark.parametrize(
    "boundary",
    ["recover-owner", "recover-marker", "recover-id", "finalize"],
)
def test_every_network_recovery_boundary_preserves_private_cleanup_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    _paths_value, plan, inspection, network_authority = _owned(tmp_path)
    absence: NetworkAbsenceReceipt | None = None
    if boundary in {"recover-marker", "finalize"}:
        absence = cleanup_owned_network(
            runner=NetworkRunner(
                [
                    _json(inspection),
                    CommandResult(0, NETWORK_ID.encode("ascii"), b""),
                    CommandResult(0, b"", b""),
                ]
            ),
            tools=_tools(),
            authority=network_authority,
        )

    raw = f"traceback-sentinel-private-{boundary}"
    failure = MemoryError(raw)
    private_authority = object()
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: private_authority if error is failure else None,
    )
    if boundary == "recover-owner":
        monkeypatch.setattr(
            network_module,
            "_recover_network_cleanup_authority",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

        def operation() -> object:
            return recover_network_cleanup_authority(
                runner=NetworkRunner([]),
                tools=_tools(),
                plan=plan,
            )
    elif boundary == "recover-marker":
        monkeypatch.setattr(
            network_module,
            "_read_absence_marker",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

        def operation() -> object:
            return network_module.recover_network_absence(
                runner=NetworkRunner([]),
                tools=_tools(),
                plan=plan,
            )
    elif boundary == "recover-id":
        monkeypatch.setattr(
            network_module,
            "_write_or_validate_absence_marker",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

        def operation() -> object:
            return network_module._recover_network_absence_from_id(
                runner=NetworkRunner([CommandResult(0, b"", b"")]),
                tools=_tools(),
                plan=plan,
                network_id=NETWORK_ID,
            )
    else:
        assert absence is not None
        monkeypatch.setattr(
            network_module,
            "_read_absence_marker",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )

        def operation() -> object:
            return finalize_network_absence(absence)

    with pytest.raises(CoturnRuntimePrivateCleanupRequired) as caught:
        operation()

    cleanup_authority = caught.value.cleanup_authority
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    assert not traceback_contains(caught.value, raw)
    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        lambda _candidate: None,
    )
    cleanup_runtime_private_authority(cleanup_authority)


def test_runtime_private_cleanup_rejects_forged_and_hostile_authorities() -> None:
    raw = "traceback-sentinel-hostile-private-authority"

    class Hostile:
        def __getattribute__(self, _name: str) -> object:
            raise AssertionError(raw)

    with pytest.raises(TypeError, match=r"factory-owned"):
        RuntimePrivateCleanupAuthority(object(), object())
    with pytest.raises(TypeError, match=r"factory-owned"):
        CoturnRuntimePrivateCleanupRequired(Hostile())  # type: ignore[arg-type]
    for candidate in (object(), Hostile()):
        with pytest.raises(
            CoturnRuntimeError,
            match=r"^Coturn runtime private cleanup authority is invalid$",
        ) as caught:
            cleanup_runtime_private_authority(candidate)  # type: ignore[arg-type]
        assert not traceback_contains(caught.value, raw)
