"""Synthetic tests for dormant build-owner and kernel admission values."""
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
import scripts.voice_pipecat_e2e_relay_linux_build_spawn as spawn_module

RUN_ID = "relay-b0-owner"


@pytest.fixture(autouse=True)
def _isolated_registries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets an effect-free registry without a production reset API."""

    monkeypatch.setattr(registry_module, "_OWNERS", {})
    monkeypatch.setattr(registry_module, "_KERNELS", {})


def _environment(run_id: str = RUN_ID) -> dict[str, str]:
    return {
        **spawn_module._FIXED_BUILD_ENVIRONMENT,
        "VOICE_E2E_NEXT_DIST_DIR": f".next-voice-e2e/{run_id}",
    }


def _graph(tmp_path: Path, *, suffix: str = ""):
    spec = spawn_module._new_relay_linux_build_spec(
        node=(tmp_path / f"node{suffix}").resolve(),
        next_cli=(tmp_path / f"next{suffix}").resolve(),
        workspace=(tmp_path / f"workspace{suffix}").resolve(),
        run_id=RUN_ID,
        environment=_environment(),
    )
    raw = spawn_module._new_raw_build_process_destination(spec)
    destination = registry_module._new_build_owner_destination(spec, raw)
    return spec, raw, destination


def _preown(tmp_path: Path):
    spec, raw, destination = _graph(tmp_path)
    owner = registry_module._preown_build_process(
        spec=spec,
        raw_destination=raw,
        destination=destination,
    )
    return spec, raw, destination, owner


class _StoreThenRaise(dict[object, object]):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self._failure = failure
        self._raised = False

    def __setitem__(self, key: object, value: object) -> None:
        super().__setitem__(key, value)
        if not self._raised:
            self._raised = True
            raise self._failure


def test_destination_preowns_stable_owner_cleanup_and_empty_result(
    tmp_path: Path,
) -> None:
    spec, raw, destination = _graph(tmp_path)
    owner = destination._read()
    authority = owner._cleanup_authority

    assert not destination
    assert not owner
    assert not authority
    assert repr(destination) == "_BuildOwnerDestination()"
    assert repr(owner) == "_RelayLinuxBuildProcessOwner()"
    assert repr(authority) == "_RelayLinuxBuildCleanupAuthority()"
    assert owner._spec is spec
    assert owner._raw_destination is raw
    assert not hasattr(owner, "_cleanup_required")
    assert owner._thread_destination._read() is None
    assert owner._kernel_destination._read() is None
    assert owner._result_destination._read() is None
    assert registry_module._resolve_cleanup_authority(authority) is None
    assert state_module.__all__ == []
    assert registry_module.__all__ == []


def test_all_retained_values_refuse_copy_pickle_and_cleanup_mutation(
    tmp_path: Path,
) -> None:
    _spec, _raw, destination, owner = _preown(tmp_path)
    kernel = registry_module._reserve_worker_kernel(owner)
    receipt = state_module._RelayLinuxBuildProcessReceipt(
        state_module._RECEIPT_TOKEN,
        owner_token=owner._owner_token,
    )
    values = (
        destination,
        owner,
        owner._cleanup_authority,
        owner._thread_destination,
        owner._kernel_destination,
        owner._result_destination,
        kernel,
        receipt,
    )

    assert not receipt
    assert receipt._matches(owner._owner_token)
    for value in values:
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
    with pytest.raises(AttributeError):
        owner._cleanup_authority._key = object()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        receipt.status = "forged"  # type: ignore[misc]


def test_cleanup_required_is_fresh_and_never_retained_by_owner(tmp_path: Path) -> None:
    _spec, _raw, _destination, owner = _preown(tmp_path)
    first = state_module._RelayLinuxBuildCleanupRequired(
        state_module._OWNER_TOKEN,
        authority=owner._cleanup_authority,
    )
    second = state_module._RelayLinuxBuildCleanupRequired(
        state_module._OWNER_TOKEN,
        authority=owner._cleanup_authority,
    )

    assert first is not second
    assert first.cleanup_authority is second.cleanup_authority is owner._cleanup_authority
    assert not hasattr(owner, "_cleanup_required")
    with pytest.raises(AttributeError):
        first._cleanup_authority = object()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        first.args = ("forged",)
    for failure in (first, second):
        with pytest.raises(TypeError):
            copy(failure)
        with pytest.raises(TypeError):
            deepcopy(failure)
        with pytest.raises(TypeError):
            pickle.dumps(failure)
    with pytest.raises(TypeError, match=r"failure is factory-owned$"):
        state_module._RelayLinuxBuildCleanupRequired(
            object(),
            authority=owner._cleanup_authority,
        )


def test_structural_fields_trusted_by_registry_are_immutable(tmp_path: Path) -> None:
    spec, raw, destination, owner = _preown(tmp_path)
    kernel = registry_module._reserve_worker_kernel(owner)
    replacements = {
        destination: {
            "_owner": object(),
            "_spec": object(),
            "_raw_destination": object(),
        },
        owner: {
            "_spec": object(),
            "_raw_destination": object(),
            "_owner_token": object(),
            "_cleanup_authority": object(),
            "_controller_destination": object(),
            "_thread_destination": object(),
            "_kernel_destination": object(),
            "_result_destination": object(),
        },
        kernel: {
            "_cancelled": True,
            "_claim": object(),
            "_owner": object(),
            "_terminal": object(),
            "_token": object(),
            "_transition": object(),
            "_worker": object(),
        },
        owner._controller_destination: {
            "_kind": "thread",
            "_lock": object(),
            "_value": object(),
        },
        owner._thread_destination: {
            "_kind": "kernel",
            "_lock": object(),
            "_value": object(),
        },
        owner._kernel_destination: {
            "_kind": "thread",
            "_lock": object(),
            "_value": object(),
        },
        owner._result_destination: {
            "_owner_token": object(),
            "_lock": object(),
            "_receipt": object(),
        },
    }

    for value, fields in replacements.items():
        for name, replacement in fields.items():
            with pytest.raises(AttributeError):
                setattr(value, name, replacement)
    assert destination._read() is owner
    assert owner._spec is spec
    assert owner._raw_destination is raw
    assert owner._kernel_destination._read() is kernel
    assert registry_module._reserve_worker_kernel(owner) is kernel


def test_preown_is_exact_idempotent_and_never_adopts_a_second_destination(
    tmp_path: Path,
) -> None:
    spec, raw, destination, owner = _preown(tmp_path)

    assert (
        registry_module._preown_build_process(
            spec=spec,
            raw_destination=raw,
            destination=destination,
        )
        is owner
    )
    assert registry_module._resolve_cleanup_authority(owner._cleanup_authority) is owner

    second = registry_module._new_build_owner_destination(spec, raw)
    assert second._read() is not owner
    with pytest.raises(state_module._RelayLinuxBuildProcessError):
        registry_module._preown_build_process(
            spec=spec,
            raw_destination=raw,
            destination=second,
        )

    other_spec, other_raw, _other_destination = _graph(tmp_path, suffix="-other")
    with pytest.raises(state_module._RelayLinuxBuildProcessError):
        registry_module._preown_build_process(
            spec=other_spec,
            raw_destination=other_raw,
            destination=destination,
        )
    assert registry_module._resolve_cleanup_authority(owner._cleanup_authority) is owner


def test_owner_registry_store_return_loss_reconciles_exact_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, raw, destination = _graph(tmp_path)
    owner = destination._read()
    cutting_registry = _StoreThenRaise(KeyboardInterrupt())
    monkeypatch.setattr(registry_module, "_OWNERS", cutting_registry)

    with pytest.raises(KeyboardInterrupt):
        registry_module._preown_build_process(
            spec=spec,
            raw_destination=raw,
            destination=destination,
        )

    assert destination._read() is owner
    assert registry_module._resolve_cleanup_authority(owner._cleanup_authority) is owner
    assert (
        registry_module._preown_build_process(
            spec=spec,
            raw_destination=raw,
            destination=destination,
        )
        is owner
    )
    assert tuple(cutting_registry.values()) == (owner,)


def test_kernel_destination_store_control_is_reconciled_before_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, _destination, owner = _preown(tmp_path)
    original = state_module._IdentityDestination._publish
    cut = False

    def publish_then_interrupt(destination: object, value: object) -> None:
        nonlocal cut
        original(destination, value)  # type: ignore[arg-type]
        if not cut:
            cut = True
            raise KeyboardInterrupt

    monkeypatch.setattr(
        state_module._IdentityDestination,
        "_publish",
        publish_then_interrupt,
    )

    with pytest.raises(KeyboardInterrupt):
        registry_module._reserve_worker_kernel(owner)
    candidate = owner._kernel_destination._read()
    assert type(candidate) is registry_module._BuildWorkerKernel
    assert registry_module._KERNELS == {}

    assert registry_module._reserve_worker_kernel(owner) is candidate
    assert tuple(registry_module._KERNELS.values()) == (candidate,)


def test_kernel_registry_store_return_loss_reconciles_exact_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _spec, _raw, _destination, owner = _preown(tmp_path)
    cutting_registry = _StoreThenRaise(SystemExit(65))
    monkeypatch.setattr(registry_module, "_KERNELS", cutting_registry)

    with pytest.raises(SystemExit) as raised:
        registry_module._reserve_worker_kernel(owner)
    assert raised.value.code == 65
    candidate = owner._kernel_destination._read()
    assert type(candidate) is registry_module._BuildWorkerKernel
    assert tuple(cutting_registry.values()) == (candidate,)
    assert registry_module._reserve_worker_kernel(owner) is candidate


def test_concurrent_preown_and_kernel_reservation_retain_one_identity(
    tmp_path: Path,
) -> None:
    spec, raw, destination = _graph(tmp_path)
    barrier = threading.Barrier(8)
    owners: list[object] = []

    def preown() -> None:
        barrier.wait()
        owners.append(
            registry_module._preown_build_process(
                spec=spec,
                raw_destination=raw,
                destination=destination,
            )
        )

    threads = [threading.Thread(target=preown) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(owners) == 8
    assert all(owner is destination._read() for owner in owners)

    owner = destination._read()
    barrier = threading.Barrier(8)
    kernels: list[object] = []

    def reserve() -> None:
        barrier.wait()
        kernels.append(registry_module._reserve_worker_kernel(owner))

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(kernels) == 8
    assert all(kernel is owner._kernel_destination._read() for kernel in kernels)
    assert len(registry_module._OWNERS) == 1
    assert len(registry_module._KERNELS) == 1


def test_slice_has_no_spawn_facade_completion_or_release_surface() -> None:
    for name in (
        "_complete_worker_kernel",
        "_release_terminal_owner",
        "_run_build",
        "_cleanup_build",
    ):
        assert not hasattr(registry_module, name)
