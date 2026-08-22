"""Synthetic tests for the dormant registry-owned workspace worker thread."""
# ruff: noqa: E402

from __future__ import annotations

import gc
import pickle
import sys
import threading
import weakref
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_thread as thread_module


def _graph(tmp_path: Path, run_id: str = "worker-thread"):
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=(tmp_path / "source").resolve(),
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id=run_id,
    )
    owner = destination._read(destination._request)
    bundle = state_module._new_relay_linux_build_workspace_worker_bundle(owner)
    return owner, bundle


def _record_and_raw(owner: object, bundle: object):
    binding = registry_module._resolve_workspace_worker_thread_binding(owner, bundle)
    record = registry_module._RECORDS[bundle]
    return binding, record, record._entry[1]


def _assert_scrubbed(control: KeyboardInterrupt | SystemExit) -> None:
    assert control.__traceback__ is None
    assert control.__cause__ is None
    assert control.__context__ is None


def test_registry_owns_dormant_thread_without_bundle_publication_or_effect(
    tmp_path: Path,
) -> None:
    owner, bundle = _graph(tmp_path)
    active_before = dict(threading._active)
    enumerated_before = tuple(threading.enumerate())

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )

    binding, record, raw = _record_and_raw(owner, bundle)
    owner_token = owner._cleanup_authority._key
    assert type(receipt) is registry_module._WorkspaceWorkerThreadReceipt
    assert coherent is True
    assert not receipt and not binding and not record and not raw
    assert receipt._matches(owner_token, record._record_token)
    assert receipt is not raw
    assert not hasattr(receipt, "_thread")
    assert type(raw) is registry_module._WorkspaceWorkerThread
    assert raw._initialized is True
    assert raw.name == registry_module._THREAD_NAME
    assert raw.daemon is False
    assert raw._target is registry_module._inert_workspace_worker_target
    assert raw.is_alive() is False
    assert bundle._thread_destination._read(owner_token) is None
    assert bundle._built_destination._read(owner_token) is None
    assert bundle._terminal_destination._read(owner_token) is None
    assert dict(threading._active) == active_before
    assert tuple(threading.enumerate()) == enumerated_before
    assert not owner._request._run_root.exists()


def test_candidate_is_registered_before_thread_init_and_only_initialized_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    binding = registry_module._resolve_workspace_worker_thread_binding(owner, bundle)
    original_init = threading.Thread.__init__
    initialized: list[object] = []

    def inspect_registration(candidate: threading.Thread, *args: object, **kwargs: object):
        assert registry_module._is_registered_workspace_worker_thread(
            binding,
            candidate,
        )
        assert bundle._thread_destination._read(owner._cleanup_authority._key) is None
        initialized.append(candidate)
        original_init(candidate, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "__init__", inspect_registration)
    before = set(threading._dangling)

    first = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    after_first = set(threading._dangling)
    second = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    after_second = set(threading._dangling)
    _binding, _record, raw = _record_and_raw(owner, bundle)

    assert first[0] is second[0]
    assert first[1] is True and second[1] is True
    assert initialized == [raw]
    assert after_first - before == {raw}
    assert after_second == after_first


def test_mid_init_control_terminalizes_failed_without_replay_or_dangling_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()
    init_calls = 0

    def interrupt_mid_init(candidate: threading.Thread, *args: object, **kwargs: object):
        nonlocal init_calls
        del args, kwargs
        init_calls += 1
        candidate._target = registry_module._inert_workspace_worker_target
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", interrupt_mid_init)
    before = set(threading._dangling)

    first = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    after_first = set(threading._dangling)
    second = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert type(first[0]) is registry_module._WorkspaceWorkerThreadReceipt
    assert first[0] is second[0] is record._entry[2]
    assert first[1] is False and second[1] is False
    assert record._entry[0] == registry_module._FAILED
    assert "_initialized" not in vars(raw)
    assert init_calls == 1
    assert after_first == before
    assert set(threading._dangling) == before
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_partial_init_after_initialized_flag_is_not_coherent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()

    def interrupt_partial_init(
        candidate: threading.Thread,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        candidate._target = registry_module._inert_workspace_worker_target
        candidate._name = registry_module._THREAD_NAME
        candidate._args = ()
        candidate._kwargs = {}
        candidate._daemonic = False
        candidate._ident = None
        candidate._started = threading.Event()
        candidate._initialized = True
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", interrupt_partial_init)
    before = set(threading._dangling)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert receipt._coherent is False and coherent is False
    assert record._entry[0] == registry_module._FAILED
    assert vars(raw)["_initialized"] is True
    assert "_stderr" not in vars(raw)
    assert raw not in threading._dangling
    assert set(threading._dangling) == before
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_post_init_control_reconciles_initialized_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = SystemExit(71)
    original_init = threading.Thread.__init__
    init_calls = 0

    def interrupt_after_init(candidate: threading.Thread, *args: object, **kwargs: object):
        nonlocal init_calls
        init_calls += 1
        original_init(candidate, *args, **kwargs)
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", interrupt_after_init)
    before = set(threading._dangling)

    first = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    after_first = set(threading._dangling)
    second = thread_module._new_relay_linux_build_workspace_worker_thread(owner, bundle)
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert first[0] is second[0] is record._entry[2]
    assert first[1] is False and second[1] is False
    assert first[0]._coherent is True
    assert record._entry[0] == registry_module._INITIALIZED
    assert raw._initialized is True
    assert init_calls == 1
    assert after_first - before == {raw}
    assert set(threading._dangling) == after_first
    retained = bundle._controller._control_value()
    assert retained is not None and (retained.kind, retained.code) == ("system-exit", 71)
    _assert_scrubbed(control)


def test_real_init_then_target_mutation_is_terminalized_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = SystemExit(72)
    original_init = threading.Thread.__init__

    def mutate_after_init(
        candidate: threading.Thread,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(candidate, *args, **kwargs)
        candidate._target = lambda: None
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", mutate_after_init)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert receipt._coherent is False and coherent is False
    assert record._entry[0] == registry_module._FAILED
    assert raw in threading._dangling
    assert raw._target is not registry_module._inert_workspace_worker_target
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_control_during_full_init_validation_is_propagated_and_latched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()
    original_is_set = threading.Event.is_set
    calls = 0

    def interrupt_validation(event: threading.Event) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise control
        return original_is_set(event)

    monkeypatch.setattr(threading.Event, "is_set", interrupt_validation)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert receipt._coherent is True and coherent is False
    assert record._entry[0] == registry_module._INITIALIZED
    assert calls >= 2
    assert bundle._controller._cancellation_requested() is True
    retained = bundle._controller._control_value()
    assert retained is not None and retained.kind == "keyboard"
    _assert_scrubbed(control)


def test_terminal_receipt_return_control_preserves_canonical_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()
    original_terminalize = registry_module._WorkspaceWorkerThreadRecord._terminalize
    terminalize_calls = 0

    def interrupt_terminal_return(record: object, candidate: object):
        nonlocal terminalize_calls
        terminalize_calls += 1
        outcome = original_terminalize(record, candidate)
        if terminalize_calls == 1:
            raise control
        return outcome

    monkeypatch.setattr(
        registry_module._WorkspaceWorkerThreadRecord,
        "_terminalize",
        interrupt_terminal_return,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert receipt._coherent is True and coherent is False
    assert terminalize_calls == 1
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_init_control_precedes_nested_terminalization_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    first = SystemExit(75)
    nested = KeyboardInterrupt()
    original_init = threading.Thread.__init__
    original_is_set = threading.Event.is_set
    validation_calls = 0

    def interrupt_after_init(
        candidate: threading.Thread,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(candidate, *args, **kwargs)
        raise first

    def interrupt_terminalization(event: threading.Event) -> bool:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise nested
        return original_is_set(event)

    monkeypatch.setattr(threading.Thread, "__init__", interrupt_after_init)
    monkeypatch.setattr(threading.Event, "is_set", interrupt_terminalization)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    retained = bundle._controller._control_value()
    assert receipt is record._entry[2]
    assert receipt._coherent is True and coherent is False
    assert retained is not None
    assert (retained.kind, retained.code) == ("system-exit", 75)
    assert validation_calls >= 2
    _assert_scrubbed(first)
    _assert_scrubbed(nested)


def test_init_control_survives_dynamic_controller_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = SystemExit(63)
    original_init = threading.Thread.__init__
    capture_calls = 0

    def interrupt_after_init(
        candidate: threading.Thread,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(candidate, *args, **kwargs)
        raise control

    def fail_dynamic_capture(
        _controller: object,
        _control: object,
    ) -> None:
        nonlocal capture_calls
        capture_calls += 1
        raise RuntimeError("dynamic capture unavailable")

    monkeypatch.setattr(threading.Thread, "__init__", interrupt_after_init)
    monkeypatch.setattr(
        state_module._WorkspaceWorkerController,
        "_capture_control",
        fail_dynamic_capture,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    retained = bundle._controller._control_value()
    assert receipt is record._entry[2]
    assert receipt._coherent is True and coherent is False
    assert retained is not None
    assert (retained.kind, retained.code) == ("system-exit", 63)
    assert capture_calls == 1
    _assert_scrubbed(control)


def test_full_init_validation_rejects_hostile_name_without_equality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = SystemExit(76)
    original_init = threading.Thread.__init__
    equality_calls = 0

    class HostileName(str):
        def __eq__(self, other: object) -> bool:
            nonlocal equality_calls
            del other
            equality_calls += 1
            raise AssertionError("hostile equality ran")

    def mutate_name(
        candidate: threading.Thread,
        *args: object,
        **kwargs: object,
    ) -> None:
        original_init(candidate, *args, **kwargs)
        candidate._name = HostileName(registry_module._THREAD_NAME)
        raise control

    monkeypatch.setattr(threading.Thread, "__init__", mutate_name)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert receipt._coherent is False and coherent is False
    assert equality_calls == 0
    _assert_scrubbed(control)


def test_control_before_controller_resolution_is_latched_and_scrubbed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()
    original_resolve = thread_module._resolve_workspace_worker_thread_binding
    calls = 0

    def interrupt_resolution(owner_value: object, bundle_value: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise control
        return original_resolve(owner_value, bundle_value)

    monkeypatch.setattr(
        thread_module,
        "_resolve_workspace_worker_thread_binding",
        interrupt_resolution,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )

    assert type(receipt) is registry_module._WorkspaceWorkerThreadReceipt
    assert coherent is False
    assert calls >= 3
    assert bundle._controller._cancellation_requested() is True
    retained = bundle._controller._control_value()
    assert retained is not None and retained.kind == "keyboard"
    _assert_scrubbed(control)


def test_pre_controller_controls_preserve_fifo_first_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    first = KeyboardInterrupt()
    second = SystemExit(79)
    original_resolve = thread_module._resolve_workspace_worker_thread_binding
    calls = 0

    def interrupt_resolution(owner_value: object, bundle_value: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise first
        if calls == 2:
            raise second
        return original_resolve(owner_value, bundle_value)

    monkeypatch.setattr(
        thread_module,
        "_resolve_workspace_worker_thread_binding",
        interrupt_resolution,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )

    retained = bundle._controller._control_value()
    assert receipt is not None and coherent is False
    assert retained is not None and retained.kind == "keyboard"
    _assert_scrubbed(first)
    _assert_scrubbed(second)


def test_persistent_final_resolution_failure_is_cancelled_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    original_resolve = thread_module._resolve_workspace_worker_thread_binding
    calls = 0

    def fail_final_resolution(owner_value: object, bundle_value: object):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("final resolution unavailable")
        return original_resolve(owner_value, bundle_value)

    monkeypatch.setattr(
        thread_module,
        "_resolve_workspace_worker_thread_binding",
        fail_final_resolution,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert coherent is False
    assert calls == 2
    assert bundle._controller._cancellation_requested() is True


def test_control_after_registry_return_keeps_canonical_receipt_and_no_loser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = SystemExit(73)
    original_advance = thread_module._advance_workspace_worker_thread
    original_init = threading.Thread.__init__
    advance_calls = 0
    init_calls = 0

    def count_init(candidate: threading.Thread, *args: object, **kwargs: object):
        nonlocal init_calls
        init_calls += 1
        original_init(candidate, *args, **kwargs)

    def interrupt_return(binding: object):
        nonlocal advance_calls
        advance_calls += 1
        outcome = original_advance(binding)
        if advance_calls == 1:
            raise control
        return outcome

    monkeypatch.setattr(threading.Thread, "__init__", count_init)
    monkeypatch.setattr(thread_module, "_advance_workspace_worker_thread", interrupt_return)

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert coherent is False
    assert advance_calls == 2
    assert init_calls == 1
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_two_lost_advance_returns_cancel_before_bounded_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    original_advance = thread_module._advance_workspace_worker_thread
    original_init = threading.Thread.__init__
    advance_calls = 0
    init_calls = 0

    def count_init(candidate: threading.Thread, *args: object, **kwargs: object):
        nonlocal init_calls
        init_calls += 1
        original_init(candidate, *args, **kwargs)

    def lose_return(binding: object):
        nonlocal advance_calls
        advance_calls += 1
        original_advance(binding)
        raise RuntimeError("advance return lost")

    monkeypatch.setattr(threading.Thread, "__init__", count_init)
    monkeypatch.setattr(thread_module, "_advance_workspace_worker_thread", lose_return)

    outcome = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )

    assert outcome == (None, False)
    assert advance_calls == 2
    assert init_calls == 1
    assert bundle._controller._cancellation_requested() is True


def test_control_at_final_handoff_is_captured_without_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    control = KeyboardInterrupt()
    original_handoff = thread_module._handoff_workspace_worker_thread_result
    original_init = threading.Thread.__init__
    handoffs = 0
    init_calls = 0

    def count_init(candidate: threading.Thread, *args: object, **kwargs: object):
        nonlocal init_calls
        init_calls += 1
        original_init(candidate, *args, **kwargs)

    def interrupt_handoff(outcome: object):
        nonlocal handoffs
        handoffs += 1
        if handoffs <= 2:
            raise control
        return original_handoff(outcome)

    monkeypatch.setattr(threading.Thread, "__init__", count_init)
    monkeypatch.setattr(
        thread_module,
        "_handoff_workspace_worker_thread_result",
        interrupt_handoff,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, _raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert coherent is False
    assert handoffs == 3
    assert init_calls == 1
    assert bundle._controller._cancellation_requested() is True
    _assert_scrubbed(control)


def test_persistent_cancel_failure_poison_terminalizes_without_hot_spin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    original_advance = thread_module._advance_workspace_worker_thread
    advance_calls = 0
    cancel_calls = 0

    def lose_once(binding: object):
        nonlocal advance_calls
        advance_calls += 1
        outcome = original_advance(binding)
        if advance_calls == 1:
            raise RuntimeError("force cancellation")
        return outcome

    def fail_cancel(_controller: object) -> None:
        nonlocal cancel_calls
        cancel_calls += 1
        raise RuntimeError("cancel unavailable")

    monkeypatch.setattr(thread_module, "_advance_workspace_worker_thread", lose_once)
    monkeypatch.setattr(
        state_module._WorkspaceWorkerController,
        "_request_cancel",
        fail_cancel,
    )

    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert receipt is record._entry[2]
    assert coherent is False and receipt._coherent is True
    assert record._entry[0] == registry_module._POISONED
    assert raw._target is None
    assert cancel_calls == 1
    assert advance_calls == 2


def test_prior_success_receipt_identity_survives_later_poison_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, bundle = _graph(tmp_path)
    first_receipt, first_coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner, bundle
    )
    original_advance = thread_module._advance_workspace_worker_thread
    advance_calls = 0

    def lose_returns(binding: object):
        nonlocal advance_calls
        advance_calls += 1
        original_advance(binding)
        raise RuntimeError("duplicate return lost")

    def fail_cancel(_controller: object) -> None:
        raise RuntimeError("cancel unavailable")

    monkeypatch.setattr(thread_module, "_advance_workspace_worker_thread", lose_returns)
    monkeypatch.setattr(
        state_module._WorkspaceWorkerController,
        "_request_cancel",
        fail_cancel,
    )

    second_receipt, second_coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner, bundle
    )
    _binding, record, raw = _record_and_raw(owner, bundle)

    assert first_receipt is second_receipt is record._entry[2]
    assert first_coherent is True and second_coherent is False
    assert first_receipt is not None and first_receipt._coherent is True
    assert record._entry[0] == registry_module._POISONED
    assert raw._target is None
    assert advance_calls == 2


def test_raw_thread_and_all_opaque_values_reject_copy_pickle_and_tamper(
    tmp_path: Path,
) -> None:
    owner, bundle = _graph(tmp_path)
    receipt, coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        owner,
        bundle,
    )
    binding, record, raw = _record_and_raw(owner, bundle)
    assert receipt is not None and coherent is True

    for value in (binding, record, receipt, raw):
        assert not value
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)

    for operation in (
        raw.start,
        raw.join,
        raw.run,
    ):
        with pytest.raises(RuntimeError, match="lifecycle is not installed"):
            operation()
    for name, value in (
        ("name", "changed"),
        ("daemon", True),
        ("_target", lambda: None),
        ("_args", (object(),)),
        ("_kwargs", {"unsafe": object()}),
    ):
        with pytest.raises(AttributeError, match="thread is sealed"):
            setattr(raw, name, value)
    with pytest.raises(AttributeError, match="thread is sealed"):
        del raw._target
    with pytest.raises(AttributeError, match="thread is sealed"):
        raw._workspace_sealed = False
    with pytest.raises(AttributeError, match="thread is sealed"):
        del raw._workspace_sealed

    with pytest.raises(TypeError, match="cannot be subclassed"):

        class _ForgedWorkspaceWorkerThread(registry_module._WorkspaceWorkerThread):
            pass


def test_wrong_owner_bundle_and_forged_values_are_rejected_without_record(
    tmp_path: Path,
) -> None:
    first_owner, first_bundle = _graph(tmp_path / "first", "first")
    _second_owner, second_bundle = _graph(tmp_path / "second", "second")

    outcome = thread_module._new_relay_linux_build_workspace_worker_thread(
        first_owner,
        second_bundle,
    )

    assert outcome == (None, False)
    assert len(registry_module._RECORDS) == 0
    assert first_bundle._controller._cancellation_requested() is False
    assert second_bundle._controller._cancellation_requested() is False
    with pytest.raises(TypeError):
        registry_module._WorkspaceWorkerThreadBinding(
            object(),
            owner_token=first_owner._cleanup_authority._key,
            controller=first_bundle._controller,
            bundle=first_bundle,
        )
    with pytest.raises(TypeError):
        registry_module._WorkspaceWorkerThread(object())
    with pytest.raises(TypeError):
        registry_module._WorkspaceWorkerThreadRecord(
            object(),
            owner_token=first_owner._cleanup_authority._key,
        )


def test_registry_is_single_live_owner_and_reclaims_record_with_bundle(
    tmp_path: Path,
) -> None:
    first_owner, first_bundle = _graph(tmp_path / "first", "first")
    first_receipt, first_coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        first_owner,
        first_bundle,
    )
    _binding, _record, first_raw = _record_and_raw(first_owner, first_bundle)
    raw_reference = weakref.ref(first_raw)
    second_owner, second_bundle = _graph(tmp_path / "second", "second")

    blocked = thread_module._new_relay_linux_build_workspace_worker_thread(
        second_owner,
        second_bundle,
    )

    assert registry_module._MAX_LIVE_RECORDS == 1
    assert first_receipt is not None and first_coherent is True
    assert blocked == (None, False)
    assert len(registry_module._RECORDS) == 1
    del _binding, _record, first_raw, first_owner, first_bundle
    gc.collect()
    assert raw_reference() is None
    assert len(registry_module._RECORDS) == 0

    second_receipt, second_coherent = thread_module._new_relay_linux_build_workspace_worker_thread(
        second_owner,
        second_bundle,
    )
    assert type(second_receipt) is registry_module._WorkspaceWorkerThreadReceipt
    assert second_receipt._coherent is True
    assert second_coherent is False
    assert len(registry_module._RECORDS) == 1


def test_checkpoint_exposes_no_lifecycle_effect_or_public_surface() -> None:
    assert registry_module.__all__ == []
    assert thread_module.__all__ == []
    for module in (registry_module, thread_module):
        for name in (
            "start",
            "join",
            "prepare",
            "cleanup",
            "spawn",
            "publish_prepared",
            "publish_built",
            "publish_terminal",
        ):
            assert not hasattr(module, name)
