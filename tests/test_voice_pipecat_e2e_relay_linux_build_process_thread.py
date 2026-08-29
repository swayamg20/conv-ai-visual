"""Synthetic tests for private controller, thread, and kernel registration."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import sys
import threading
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_process_registry as registry_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_state as state_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_thread as thread_module
import scripts.voice_pipecat_e2e_relay_linux_build_process_worker_state as worker_state_module
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module

RUN_ID = "relay-b0-worker-registration"


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "_OWNERS", {})
    monkeypatch.setattr(registry_module, "_KERNELS", {})


def _environment() -> dict[str, str]:
    return {
        **spawn_module._FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{RUN_ID}",
    }


def _owner_graph(tmp_path: Path, *, deadline: float = 100.0):
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id=RUN_ID,
        environment=_environment(),
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = registry_module._new_build_owner_destination(spec, raw)
    owner = registry_module._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )
    controller = registry_module._preown_worker_controller(owner, deadline)
    kernel = registry_module._reserve_worker_kernel(owner)
    return owner, controller, kernel


def _preowned_owner(tmp_path: Path):
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / "node").resolve(),
        next_cli=(tmp_path / "next").resolve(),
        workspace=(tmp_path / "workspace").resolve(),
        run_id=RUN_ID,
        environment=_environment(),
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = registry_module._new_build_owner_destination(spec, raw)
    return registry_module._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )


class _DerivedKeyboardInterrupt(KeyboardInterrupt):
    pass


class _DerivedSystemExit(SystemExit):
    def __getattribute__(self, name: str) -> object:
        if name == "code":
            raise AssertionError("subclass code hook must not run")
        return super().__getattribute__(name)


class _HostileControlMixin:
    setter_calls = 0

    def __setattr__(self, _name: str, _value: object) -> None:
        type(self).setter_calls += 1
        raise AssertionError("control subclass setter must not run")


class _HostileKeyboardInterrupt(_HostileControlMixin, KeyboardInterrupt):
    pass


class _HostileSystemExit(_HostileControlMixin, SystemExit):
    pass


class _HostileFactoryError(Exception):
    setter_calls = 0

    def __setattr__(self, _name: str, _value: object) -> None:
        type(self).setter_calls += 1
        raise AssertionError("factory exception setter must not run")


def _raise(error: BaseException) -> None:
    raise error


@pytest.mark.parametrize(
    ("first", "nested", "kind", "code"),
    [
        (SystemExit(73), KeyboardInterrupt(), "system-exit", 73),
        (KeyboardInterrupt(), SystemExit(91), "keyboard", None),
    ],
)
def test_first_control_survives_nested_conversion_and_scrubs_every_raw_error(
    first: KeyboardInterrupt | SystemExit,
    nested: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = worker_state_module._new_build_worker_controller(
        owner_token=object(),
        run_deadline=1.0,
    )
    original = worker_state_module._control_signal
    injected = False

    def convert(error: KeyboardInterrupt | SystemExit):
        nonlocal injected
        if not injected:
            injected = True
            raise nested
        return original(error)

    monkeypatch.setattr(worker_state_module, "_control_signal", convert)
    try:
        _raise(first)
    except (KeyboardInterrupt, SystemExit) as caught:
        controller._capture_control(caught)

    retained = controller._control_value()
    assert retained is not None
    assert retained.kind == kind
    assert retained.code == code
    for error in (first, nested):
        assert error.__traceback__ is None
        assert error.__cause__ is None
        assert error.__context__ is None


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (_DerivedKeyboardInterrupt(), "keyboard", None),
        (_DerivedSystemExit(73), "system-exit", 73),
    ],
)
def test_derived_controls_normalize_without_subclass_hooks(
    error: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
) -> None:
    controller = worker_state_module._new_build_worker_controller(
        owner_token=object(),
        run_deadline=1.0,
    )

    controller._capture_control(error)

    retained = controller._control_value()
    assert retained is not None
    assert retained.kind == kind
    assert retained.code == code
    assert error.__traceback__ is None


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (_HostileKeyboardInterrupt(), "keyboard", None),
        (_HostileSystemExit(73), "system-exit", 73),
    ],
)
def test_control_scrub_bypasses_hostile_subclass_setters(
    error: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
) -> None:
    type(error).setter_calls = 0
    controller = worker_state_module._new_build_worker_controller(
        owner_token=object(),
        run_deadline=1.0,
    )
    try:
        _raise(error)
    except (KeyboardInterrupt, SystemExit) as caught:
        controller._capture_control(caught)

    retained = controller._control_value()
    assert retained is not None
    assert retained.kind == kind
    assert retained.code == code
    assert type(error).setter_calls == 0
    assert BaseException.__getattribute__(error, "__traceback__") is None


def test_thread_factory_error_scrub_bypasses_hostile_subclass_setter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    error = _HostileFactoryError("private")
    _HostileFactoryError.setter_calls = 0

    def factory(**_options: object) -> object:
        raise error

    monkeypatch.setattr(thread_module, "_registered_thread_factory", factory)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=lambda _token: None,
        kernel_token=kernel._token,
    )

    assert retained is None
    assert coherent is False
    assert _HostileFactoryError.setter_calls == 0
    assert BaseException.__getattribute__(error, "__traceback__") is None


@pytest.mark.parametrize(
    ("factory_control", "read_control", "kind", "code"),
    [
        (SystemExit(73), KeyboardInterrupt(), "system-exit", 73),
        (KeyboardInterrupt(), SystemExit(91), "keyboard", None),
    ],
)
def test_thread_construction_controls_share_the_first_control_latch(
    tmp_path: Path,
    factory_control: KeyboardInterrupt | SystemExit,
    read_control: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    thread = object()

    def factory(**options: object) -> object:
        register = options["owner_register"]
        assert callable(register)
        register(thread)
        raise factory_control

    original_read = state_module._IdentityDestination._read
    injected = False

    def read(destination: object) -> object | None:
        nonlocal injected
        if destination is owner._thread_destination and not injected:
            injected = True
            raise read_control
        return original_read(destination)  # type: ignore[arg-type]

    monkeypatch.setattr(thread_module, "_registered_thread_factory", factory)
    monkeypatch.setattr(state_module._IdentityDestination, "_read", read)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=lambda _token: None,
        kernel_token=kernel._token,
    )

    control = controller._control_value()
    assert retained is thread
    assert coherent is False
    assert control is not None
    assert control.kind == kind
    assert control.code == code
    assert factory_control.__traceback__ is None
    assert read_control.__traceback__ is None


def test_thread_construction_requires_registered_return_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    registered = object()
    returned = object()

    def factory(**options: object) -> object:
        register = options["owner_register"]
        assert callable(register)
        register(registered)
        return returned

    monkeypatch.setattr(thread_module, "_registered_thread_factory", factory)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=lambda _token: None,
        kernel_token=kernel._token,
    )

    assert retained is registered
    assert coherent is False
    assert owner._thread_destination._read() is registered
    assert controller._thread() is registered


def test_thread_construction_rejects_noncanonical_same_owner_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, canonical, kernel = _owner_graph(tmp_path)
    forged = worker_state_module._new_build_worker_controller(
        owner_token=owner._owner_token,
        run_deadline=100.0,
    )
    called = False

    def factory(**_options: object) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(thread_module, "_registered_thread_factory", factory)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=forged,
        target=lambda _token: None,
        kernel_token=kernel._token,
    )

    assert retained is None
    assert coherent is False
    assert called is False
    assert owner._controller_destination._read() is canonical


def test_thread_construction_rejects_wrong_kernel_token_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, _kernel = _owner_graph(tmp_path)
    called = False

    def factory(**_options: object) -> object:
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(thread_module, "_registered_thread_factory", factory)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=lambda _token: None,
        kernel_token=object(),
    )

    assert retained is None
    assert coherent is False
    assert called is False
    assert owner._thread_destination._read() is None


@pytest.mark.parametrize(
    "deadline",
    [float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True, 1, None],
)
def test_invalid_deadline_never_publishes_or_poisons_controller_destination(
    tmp_path: Path,
    deadline: object,
) -> None:
    owner = _preowned_owner(tmp_path)

    with pytest.raises(state_module._RelayLinuxBuildProcessError):
        registry_module._preown_worker_controller(owner, deadline)  # type: ignore[arg-type]

    assert owner._controller_destination._read() is None
    recovered = registry_module._preown_worker_controller(owner, 100.0)
    assert recovered._matches(owner._owner_token, 100.0)


def test_kernel_take_and_terminal_store_return_loss_are_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker = threading.current_thread()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)
    claim_cut = False

    def interrupt_claim() -> None:
        nonlocal claim_cut
        if not claim_cut:
            claim_cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(registry_module, "_kernel_claim_published", interrupt_claim)
    with pytest.raises(KeyboardInterrupt):
        registry_module._take_worker_kernel(kernel._token)
    assert kernel._worker is worker
    assert type(kernel._claim) is worker_state_module._BuildWorkerClaim
    take = registry_module._take_worker_kernel(kernel._token)
    assert take is not None
    assert take.status == "claimed"
    assert take.kernel is kernel
    claim = take.claim
    assert claim is not None

    publish_cut = False

    def interrupt_publish() -> None:
        nonlocal publish_cut
        if not publish_cut:
            publish_cut = True
            raise SystemExit(62)

    monkeypatch.setattr(registry_module, "_kernel_report_published", interrupt_publish)
    with pytest.raises(SystemExit) as publish_error:
        registry_module._publish_worker_terminal(
            kernel,
            claim,
            returncode=None,
            succeeded=False,
        )
    assert publish_error.value.code == 62
    assert registry_module._publish_worker_terminal(
        kernel,
        claim,
        returncode=None,
        succeeded=False,
    )
    terminal = kernel._terminal
    assert terminal is not None
    settle_cut = False

    def interrupt_settle() -> None:
        nonlocal settle_cut
        if not settle_cut:
            settle_cut = True
            raise SystemExit(65)

    monkeypatch.setattr(registry_module, "_kernel_terminal_published", interrupt_settle)
    with pytest.raises(SystemExit) as raised:
        registry_module._settle_worker_kernel(kernel, claim)
    assert raised.value.code == 65
    assert kernel._terminal is terminal
    assert registry_module._settle_worker_kernel(kernel, claim)


def test_cancelled_kernel_returns_exact_late_worker_outcome(tmp_path: Path) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker = threading.current_thread()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)
    assert registry_module._cancel_unstarted_worker_kernel(kernel)
    terminal = kernel._terminal
    assert terminal is not None

    take = registry_module._take_worker_kernel(kernel._token)

    assert take is not None
    assert take.status == "cancelled"
    assert take.kernel is kernel
    assert kernel._worker is None
    assert kernel._terminal is terminal


def test_cancellation_store_return_loss_reconciles_failure_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker_outcomes: list[object] = []

    def worker_target(token: object) -> None:
        worker_outcomes.append(registry_module._take_worker_kernel(token))

    worker_thread, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=worker_target,
        kernel_token=kernel._token,
    )
    assert type(worker_thread) is threading.Thread
    assert coherent is True
    terminal_cut = False
    reservation_cut = False

    def interrupt_terminal() -> None:
        nonlocal terminal_cut
        if not terminal_cut:
            terminal_cut = True
            raise SystemExit(63)

    def interrupt_cancel() -> None:
        nonlocal reservation_cut
        if not reservation_cut:
            reservation_cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(registry_module, "_kernel_cancel_reported", interrupt_terminal)
    monkeypatch.setattr(registry_module, "_kernel_cancel_published", interrupt_cancel)
    with pytest.raises(KeyboardInterrupt):
        registry_module._cancel_unstarted_worker_kernel(kernel)
    assert kernel._transition.phase == "cancelling"
    assert kernel._terminal is None

    with pytest.raises(SystemExit) as terminal_error:
        registry_module._cancel_unstarted_worker_kernel(kernel)
    assert terminal_error.value.code == 63
    terminal = kernel._terminal
    assert type(terminal) is worker_state_module._BuildWorkerTerminal
    assert kernel._cancelled is False

    worker_thread.start()
    worker_thread.join(timeout=1.0)
    assert worker_thread.is_alive() is False
    assert worker_outcomes == [None]
    assert kernel._worker is None
    assert kernel._claim is None
    assert kernel._transition.phase == "reported"

    assert type(terminal) is worker_state_module._BuildWorkerTerminal
    assert terminal.succeeded is False
    assert terminal.returncode is None
    assert registry_module._cancel_unstarted_worker_kernel(kernel)
    assert kernel._cancelled is True
    assert kernel._terminal is terminal


def test_never_started_registered_thread_cannot_claim_or_publish_success(
    tmp_path: Path,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=lambda _token: None,
        kernel_token=kernel._token,
    )

    assert type(retained) is threading.Thread
    assert coherent is True
    assert retained.is_alive() is False
    assert retained is not threading.current_thread()
    assert registry_module._take_worker_kernel(kernel._token) is None
    assert not registry_module._publish_worker_terminal(
        kernel,
        object(),  # type: ignore[arg-type]
        returncode=0,
        succeeded=True,
    )
    assert kernel._worker is None
    assert kernel._terminal is None
    assert registry_module._cancel_unstarted_worker_kernel(kernel)


def test_exact_worker_cannot_publish_terminal_before_durable_claim(
    tmp_path: Path,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    outcomes: list[bool] = []

    def target(_token: object) -> None:
        forged = worker_state_module._new_build_worker_claim(
            owner_token=owner._owner_token,
            worker=threading.current_thread(),
        )
        outcomes.append(
            registry_module._publish_worker_terminal(
                kernel,
                forged,
                returncode=0,
                succeeded=True,
            )
        )
        uninitialized = object.__new__(worker_state_module._BuildWorkerClaim)
        outcomes.append(
            registry_module._publish_worker_terminal(
                kernel,
                uninitialized,
                returncode=0,
                succeeded=True,
            )
        )

    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=target,
        kernel_token=kernel._token,
    )
    assert type(retained) is threading.Thread
    assert coherent is True

    retained.start()
    retained.join(timeout=1.0)

    assert outcomes == [False, False]
    assert retained.is_alive() is False
    assert kernel._worker is None
    assert kernel._claim is None
    assert kernel._cancelled is False
    assert kernel._terminal is None
    assert registry_module._cancel_unstarted_worker_kernel(kernel)


def test_failed_prestart_terminal_publication_irreversibly_reserves_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    outcomes: list[object] = []

    def target(token: object) -> None:
        outcomes.append(registry_module._take_worker_kernel(token))

    worker_thread, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=target,
        kernel_token=kernel._token,
    )
    assert type(worker_thread) is threading.Thread
    assert coherent is True
    original_terminal_factory = registry_module._new_build_worker_terminal
    monkeypatch.setattr(
        registry_module,
        "_new_build_worker_terminal",
        lambda **_values: (_ for _ in ()).throw(RuntimeError("synthetic terminal cut")),
    )

    with pytest.raises(RuntimeError, match="synthetic terminal cut"):
        registry_module._cancel_unstarted_worker_kernel(kernel)
    assert kernel._cancelled is False
    assert kernel._terminal is None
    assert kernel._transition.phase == "cancelling"

    worker_thread.start()
    worker_thread.join(timeout=1.0)
    assert worker_thread.is_alive() is False
    assert outcomes == [None]
    assert kernel._worker is None
    assert kernel._claim is None

    monkeypatch.setattr(
        registry_module,
        "_new_build_worker_terminal",
        original_terminal_factory,
    )
    assert registry_module._cancel_unstarted_worker_kernel(kernel)
    assert kernel._cancelled is True
    assert type(kernel._terminal) is worker_state_module._BuildWorkerTerminal


def test_exact_running_registered_thread_claims_publishes_and_settles(
    tmp_path: Path,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    outcomes: list[object] = []

    def target(token: object) -> None:
        try:
            take = registry_module._take_worker_kernel(token)
            outcomes.append(take)
            claim = None if take is None else take.claim
            outcomes.append(claim)
            if claim is not None:
                outcomes.append(
                    registry_module._publish_worker_terminal(
                        kernel,
                        claim,
                        returncode=0,
                        succeeded=True,
                    )
                )
            terminal = kernel._terminal
            outcomes.append(terminal)
            if claim is not None and terminal is not None:
                outcomes.append(registry_module._settle_worker_kernel(kernel, claim))
        except BaseException as error:  # pragma: no cover - assertion reports the value
            outcomes.append(error)

    retained, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=target,
        kernel_token=kernel._token,
    )
    assert type(retained) is threading.Thread
    assert coherent is True

    retained.start()
    retained.join(timeout=1.0)

    assert retained.is_alive() is False
    assert len(outcomes) == 5
    take, claim, published, terminal, settled = outcomes
    assert type(take) is registry_module._BuildWorkerKernelTake
    assert take.status == "claimed"
    assert take.kernel is kernel
    assert type(claim) is worker_state_module._BuildWorkerClaim
    assert published is True
    assert type(terminal) is worker_state_module._BuildWorkerTerminal
    assert terminal.succeeded is True
    assert terminal.returncode == 0
    assert settled is True
    assert kernel._worker is retained
    assert kernel._terminal is terminal


def test_concurrent_take_and_cancel_have_one_atomic_transition_winner(
    tmp_path: Path,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def target(token: object) -> None:
        barrier.wait()
        take = registry_module._take_worker_kernel(token)
        outcomes.append(take)
        if take is not None and take.status == "claimed" and take.claim is not None:
            published = registry_module._publish_worker_terminal(
                kernel,
                take.claim,
                returncode=None,
                succeeded=False,
            )
            terminal = kernel._terminal
            outcomes.append(published)
            outcomes.append(terminal)
            if terminal is not None:
                outcomes.append(registry_module._settle_worker_kernel(kernel, take.claim))

    worker_thread, coherent = thread_module._construct_registered_build_thread(
        owner,
        controller=controller,
        target=target,
        kernel_token=kernel._token,
    )
    assert type(worker_thread) is threading.Thread
    assert coherent is True
    worker_thread.start()
    barrier.wait()
    cancelled = registry_module._cancel_unstarted_worker_kernel(kernel)
    worker_thread.join(timeout=1.0)

    assert worker_thread.is_alive() is False
    take = outcomes[0]
    assert type(take) is registry_module._BuildWorkerKernelTake
    if cancelled:
        assert len(outcomes) == 1
        assert take.status == "cancelled"
        assert kernel._worker is None
        assert kernel._cancelled is True
    else:
        assert len(outcomes) == 4
        assert take.status == "claimed"
        assert outcomes[1] is True
        assert type(outcomes[2]) is worker_state_module._BuildWorkerTerminal
        assert outcomes[3] is True
        assert kernel._worker is worker_thread
        assert kernel._cancelled is False
    assert kernel._transition.phase == "settled"


def test_take_cancel_and_settle_require_exact_current_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker = object()
    wrong_worker = object()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)

    monkeypatch.setattr(threading, "current_thread", lambda: wrong_worker)
    assert registry_module._take_worker_kernel(kernel._token) is None
    monkeypatch.setattr(threading, "current_thread", lambda: worker)
    take = registry_module._take_worker_kernel(kernel._token)
    assert take is not None
    retry = registry_module._take_worker_kernel(kernel._token)
    assert retry is not None
    assert retry.kernel is kernel
    claim = take.claim
    assert claim is not None
    monkeypatch.setattr(threading, "current_thread", lambda: wrong_worker)
    assert registry_module._take_worker_kernel(kernel._token) is None

    monkeypatch.setattr(threading, "current_thread", lambda: worker)
    assert registry_module._publish_worker_terminal(
        kernel,
        claim,
        returncode=None,
        succeeded=False,
    )
    terminal = kernel._terminal
    assert terminal is not None
    monkeypatch.setattr(threading, "current_thread", lambda: wrong_worker)
    assert not registry_module._settle_worker_kernel(kernel, claim)
    assert not registry_module._cancel_unstarted_worker_kernel(kernel)
    monkeypatch.setattr(threading, "current_thread", lambda: worker)
    assert registry_module._settle_worker_kernel(kernel, claim)


@pytest.mark.parametrize("phase", ["reported", "settled"])
@pytest.mark.parametrize("detached", ["kernel", "owner"])
def test_report_and_settle_fast_paths_reject_detached_registry_graphs(
    tmp_path: Path,
    phase: str,
    detached: str,
) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker = threading.current_thread()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)
    take = registry_module._take_worker_kernel(kernel._token)
    assert take is not None
    claim = take.claim
    assert claim is not None
    assert registry_module._publish_worker_terminal(
        kernel,
        claim,
        returncode=None,
        succeeded=False,
    )
    if phase == "settled":
        assert registry_module._settle_worker_kernel(kernel, claim)

    if detached == "kernel":
        assert registry_module._KERNELS.pop(kernel._token) is kernel
    else:
        key = owner._cleanup_authority._key
        assert registry_module._OWNERS.pop(key) is owner

    assert not registry_module._publish_worker_terminal(
        kernel,
        claim,
        returncode=None,
        succeeded=False,
    )
    assert not registry_module._settle_worker_kernel(kernel, claim)


def test_controller_terminal_control_and_take_refuse_copy_and_pickle(tmp_path: Path) -> None:
    owner, controller, kernel = _owner_graph(tmp_path)
    worker = threading.current_thread()
    owner._thread_destination._publish(worker)
    assert controller._publish_thread(worker)
    take = registry_module._take_worker_kernel(kernel._token)
    assert take is not None
    claim = take.claim
    assert claim is not None
    controller._capture_control(SystemExit(73))
    control = controller._control_value()
    assert control is not None
    assert registry_module._publish_worker_terminal(
        kernel,
        claim,
        returncode=None,
        succeeded=False,
    )
    terminal = kernel._terminal
    assert terminal is not None

    for value in (controller, control, claim, kernel._transition, terminal, take):
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)

    replacements = {
        controller: {
            "_condition": object(),
            "_control": object(),
            "_owner_token": object(),
            "_phase": "settled",
            "_run_deadline": 999.0,
            "_thread_identity": object(),
        },
        control: {"kind": "keyboard", "code": None},
        claim: {"_owner_token": object(), "_worker": object()},
        kernel._transition: {
            "claim": object(),
            "phase": "available",
            "terminal": object(),
            "worker": object(),
        },
        terminal: {"_owner_token": object(), "returncode": 0, "succeeded": True},
        take: {"claim": object(), "kernel": object(), "status": "cancelled"},
    }
    for value, fields in replacements.items():
        for name, replacement in fields.items():
            with pytest.raises(AttributeError):
                setattr(value, name, replacement)


def test_registered_thread_factory_publishes_before_init_with_exact_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = object()
    published: list[object] = []
    initialized: list[tuple[object, object, tuple[object, ...], str, bool]] = []
    token = object()

    def target(_token: object) -> None:
        return None

    def initialize(
        candidate: object,
        *,
        target: object,
        args: tuple[object, ...],
        name: str,
        daemon: bool,
    ) -> None:
        assert published == [candidate]
        initialized.append((candidate, target, args, name, daemon))

    class SyntheticThread:
        def __new__(cls) -> object:
            return thread

        __init__ = initialize

    # Replace the module attribute as one unit. Patching inherited
    # ``Thread.__new__`` directly would make pytest restore ``object.__new__``
    # as a concrete class attribute and corrupt later Thread construction.
    monkeypatch.setattr(thread_module.threading, "Thread", SyntheticThread)
    returned = thread_module._registered_thread_factory(
        owner_register=published.append,
        target=target,
        args=(token,),
        name="relay-linux-build-worker",
        daemon=True,
    )

    assert returned is thread
    assert initialized == [(thread, target, (token,), "relay-linux-build-worker", True)]
