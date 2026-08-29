"""Synthetic TLS generation-slot ownership tests; no OpenSSL is executed."""

from __future__ import annotations

import base64
import copy
import gc
import hashlib
import inspect
import os
import pickle
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_tls as tls_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_generation as generation_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_material as material_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_receipt as receipt_module  # noqa: E402
from scripts import voice_pipecat_e2e_coturn_tls_values as values_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CoturnTlsCleanupRequired,
    CoturnTlsError,
    TlsMaterialGenerationSlot,
    cleanup_tls_material_authority,
    cleanup_tls_material_generation_slot,
    cleanup_tls_private_authority,
    generate_tls_and_config_material_into_slot,
    new_tls_material_generation_slot,
    tls_private_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (  # noqa: E402
    TlsMaterialLifetimeAuthority,
)
from scripts.voice_pipecat_e2e_coturn_tls_private import (  # noqa: E402
    CoturnTlsPrivateCleanupRequired,
)
from tests.coturn_tls_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_host import (  # noqa: E402
    QueueRunner,
    _paths,
    _result,
    _tools,
)
from tests.test_voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CERTIFICATE,
    DER_SPKI,
    NOW,
    PRIVATE_KEY,
    SECRET,
    TOPOLOGY,
    _tls_results,
)

TOPOLOGY_B = CoturnBridgeTopology.parse(
    network="172.28.45.0/29",
    gateway="172.28.45.1",
    container="172.28.45.2",
)


def _finite_controls(count: int, mode: str) -> list[BaseException]:
    controls: list[BaseException] = [SystemExit(23)]
    for index in range(count - 1):
        if mode == "alternating" and index % 2 == 0:
            controls.append(KeyboardInterrupt())
        else:
            controls.append(SystemExit(47))
    return controls


def _source_line(function: object, marker: str, *, after: bool = False) -> int:
    lines, first = inspect.getsourcelines(function)
    matches = [index for index, line in enumerate(lines) if marker in line]
    assert len(matches) == 1, (marker, matches)
    return first + matches[0] + int(after)


@contextmanager
def _control_at_line(
    function: object,
    line: int,
    error: KeyboardInterrupt | SystemExit,
) -> Iterator[list[bool]]:
    target = function.__code__  # type: ignore[attr-defined]
    previous = sys.gettrace()
    injected = [False]

    def trace(frame: object, event: str, _argument: object) -> object:
        if (
            not injected[0]
            and event == "line"
            and frame.f_code is target  # type: ignore[attr-defined]
            and frame.f_lineno == line  # type: ignore[attr-defined]
        ):
            injected[0] = True
            sys.settrace(None)
            raise error
        return trace

    sys.settrace(trace)
    try:
        yield injected
    finally:
        sys.settrace(previous)


def _private_extractor_trace(error: BaseException) -> BaseException:
    private = "private-extractor-frame-sentinel"
    try:
        raise error
    except BaseException as caught:
        assert private
        return caught


def _successful_runner() -> QueueRunner:
    return QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()])


def test_generate_synthetic(slot: TlsMaterialGenerationSlot, paths: object) -> QueueRunner:
    runner = _successful_runner()
    result = generate_tls_and_config_material_into_slot(
        slot=slot,
        runner=runner,
        tools=_tools(),
        paths=paths,  # type: ignore[arg-type]
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    assert result is None
    return runner


test_generate_synthetic.__test__ = False


def _material_paths(paths: object) -> tuple[Path, Path, Path]:
    contract = paths.contract  # type: ignore[attr-defined]
    return contract.private_key, contract.cert, contract.config


def _material_graph_secrets(paths: object) -> tuple[str | bytes, ...]:
    contract = paths.contract  # type: ignore[attr-defined]
    material_paths = _material_paths(paths)
    return (
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(contract.run_dir),
        *(os.fspath(path) for path in material_paths),
        *(path.name for path in material_paths),
    )


def _owned_private_descriptor(
    tmp_path: Path,
    name: str,
) -> tuple[receipt_module.PrivateDescriptorCleanupAuthority, int, Path]:
    target = tmp_path / name
    target.write_bytes(b"private-descriptor-sentinel")
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    authority = receipt_module.new_private_descriptor_cleanup_authority()
    assert authority.begin()
    assert authority.publish(((descriptor, (details.st_dev, details.st_ino)),))
    return authority, descriptor, target


def test_slot_and_hidden_receipt_reject_copy_deepcopy_and_pickle(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    handle = object.__getattribute__(slot, "_handle")
    with material_module._REGISTRY_LOCK:
        receipt = material_module._REGISTRY[handle].snapshot[2]
    assert type(receipt) is material_module.TlsMaterialReceipt

    operations = (copy.copy, copy.deepcopy, pickle.dumps)
    for value in (slot, receipt):
        for operation in operations:
            with pytest.raises(TypeError, match=r"^Coturn TLS authority is linear$"):
                operation(value)

    with material_module._REGISTRY_LOCK:
        assert material_module._REGISTRY[handle].snapshot[2] is receipt
    assert slot.has_material and receipt.has_cleanup_authority
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete and not receipt.has_cleanup_authority
    assert not any(path.exists() for path in _material_paths(paths))
    for value in (slot, receipt):
        for operation in operations:
            with pytest.raises(TypeError, match=r"^Coturn TLS authority is linear$"):
                operation(value)


def test_empty_generation_slot_is_factory_owned_redacted_and_harmless_to_lose(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    gc.collect()
    baseline = len(material_module._REGISTRY)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    handle = slot._handle
    assert repr(slot) == "TlsMaterialGenerationSlot()"
    assert not slot.has_material and not slot.cleanup_complete
    assert handle in material_module._REGISTRY
    with pytest.raises(TypeError, match=r"factory-owned"):
        TlsMaterialGenerationSlot(object(), object())
    del slot
    gc.collect()
    assert handle not in material_module._REGISTRY
    assert len(material_module._REGISTRY) == baseline


@pytest.mark.parametrize("mismatch", ["paths", "topology"])
def test_slot_rejects_cross_run_or_topology_reuse_before_generation_side_effects(
    tmp_path: Path,
    mismatch: str,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    paths_a = _paths(root_a)
    paths_b = _paths(root_b)
    slot = new_tls_material_generation_slot(paths=paths_a, topology=TOPOLOGY)
    selected_paths = paths_b if mismatch == "paths" else paths_a
    selected_topology = TOPOLOGY if mismatch == "paths" else TOPOLOGY_B
    runner = QueueRunner([])
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn TLS generation slot is invalid$",
    ) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=runner,
            tools=_tools(),
            paths=selected_paths,
            topology=selected_topology,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert runner.requests == []
    assert not slot.has_material
    assert not any(path.exists() for path in _material_paths(selected_paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        os.fspath(paths_a.contract.run_dir),
        os.fspath(paths_b.contract.run_dir),
    )


@pytest.mark.parametrize("kind", ["wrong-type", "stale-record"])
def test_invalid_public_slot_fixed_fails_without_hot_spin_or_side_effect(
    tmp_path: Path,
    kind: str,
) -> None:
    paths = _paths(tmp_path)
    if kind == "wrong-type":
        slot: object = object()
    else:
        slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
        material_module._REGISTRY.pop(slot._handle)
    runner = QueueRunner([])
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn TLS generation slot is invalid$",
    ) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,  # type: ignore[arg-type]
            runner=runner,
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert runner.requests == []
    assert not any(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        os.fspath(paths.contract.run_dir),
    )


def test_success_publishes_only_sanitized_evidence_and_cleanup_authority(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    runner = test_generate_synthetic(slot, paths)
    expected_pin = base64.b64encode(hashlib.sha256(DER_SPKI).digest()).decode("ascii")
    assert slot.has_material
    assert slot.certificate_sha256 == hashlib.sha256(CERTIFICATE).hexdigest()
    assert slot.chromium_spki_sha256_b64 == expected_pin
    assert slot.not_before == datetime(2026, 8, 16, 11, 59, tzinfo=UTC)
    assert slot.not_after == datetime(2026, 8, 17, 11, 59, tzinfo=UTC)
    assert len(runner.requests) == 7
    assert SECRET not in repr(slot)
    assert os.fspath(paths.contract.run_dir) not in repr(slot)
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete and not slot.has_material
    assert not any(path.exists() for path in _material_paths(paths))
    cleanup_tls_material_generation_slot(slot)


def test_reserved_or_populated_slot_cannot_generate_twice(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    runner = QueueRunner([])
    try:
        with pytest.raises(
            CoturnTlsError,
            match=r"^Coturn TLS generation slot is invalid$",
        ):
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert runner.requests == []
        assert slot.has_material
    finally:
        cleanup_tls_material_generation_slot(slot)


def test_failed_competing_reservation_cannot_release_the_active_owner(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    owner = material_module.new_tls_material_generation_reservation()
    competitor = material_module.new_tls_material_generation_reservation()
    reserved, control = material_module.reserve_tls_material_generation_slot(
        slot,
        paths,
        TOPOLOGY,
        owner,
    )
    assert reserved and control is None
    competing, control = material_module.reserve_tls_material_generation_slot(
        slot,
        paths,
        TOPOLOGY,
        competitor,
    )
    assert not competing and control is None
    released, control = material_module.release_tls_material_generation_slot(slot, competitor)
    assert not released and control is None
    released, control = material_module.release_tls_material_generation_slot(slot, owner)
    assert released and control is None
    reserved, control = material_module.reserve_tls_material_generation_slot(
        slot,
        paths,
        TOPOLOGY,
        competitor,
    )
    assert reserved and control is None
    released, control = material_module.release_tls_material_generation_slot(slot, competitor)
    assert released and control is None


def test_reserve_publication_line_control_returns_exact_owned_reservation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    reservation = material_module.new_tls_material_generation_reservation()
    line = _source_line(
        material_module.reserve_tls_material_generation_slot,
        'record.snapshot = ("reserved", reservation, None)',
        after=True,
    )
    with _control_at_line(
        material_module.reserve_tls_material_generation_slot,
        line,
        SystemExit(23),
    ) as injected:
        reserved, control = material_module.reserve_tls_material_generation_slot(
            slot, paths, TOPOLOGY, reservation
        )
    assert injected == [True]
    assert reserved and control == (SystemExit, 23)
    released, observed = material_module.release_tls_material_generation_slot(slot, reservation)
    assert released and observed is None and not slot.has_material


def test_release_publication_line_control_reconciles_to_empty(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    reservation = material_module.new_tls_material_generation_reservation()
    reserved, control = material_module.reserve_tls_material_generation_slot(
        slot, paths, TOPOLOGY, reservation
    )
    assert reserved and control is None
    line = _source_line(
        material_module.release_tls_material_generation_slot,
        'record.snapshot = ("empty", None, None)',
        after=True,
    )
    with _control_at_line(
        material_module.release_tls_material_generation_slot,
        line,
        KeyboardInterrupt(),
    ) as injected:
        released, control = material_module.release_tls_material_generation_slot(slot, reservation)
    assert injected == [True]
    assert released and control == (KeyboardInterrupt, None)
    replacement = material_module.new_tls_material_generation_reservation()
    reserved, control = material_module.reserve_tls_material_generation_slot(
        slot, paths, TOPOLOGY, replacement
    )
    assert reserved and control is None
    released, control = material_module.release_tls_material_generation_slot(slot, replacement)
    assert released and control is None


def test_ordinary_release_entry_failure_retries_until_slot_is_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    handle = slot._handle
    failed_once = False

    def fail_release_entry(phase: str) -> None:
        nonlocal failed_once
        if phase == "release-entry" and not failed_once:
            failed_once = True
            raise RuntimeError("ordinary-release-entry-sentinel")

    def fail_generator(**_arguments: object) -> None:
        raise CoturnTlsError("Coturn TLS material is invalid")

    generator = generation_module.bind_tls_material_slot_generator(fail_generator)
    monkeypatch.setattr(material_module, "_slot_transition_hook", fail_release_entry)
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS material is invalid$"):
        generator(
            slot=slot,
            runner=QueueRunner([]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert failed_once and not slot.has_material
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)
    del slot
    gc.collect()
    assert handle not in material_module._REGISTRY


def test_adoption_refusal_rolls_back_material_and_releases_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    monkeypatch.setattr(
        tls_module,
        "adopt_tls_material_generation_slot",
        lambda _slot, _receipt, _reservation: (False, None),
    )
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS cleanup failed$"):
        test_generate_synthetic(slot, paths)
    assert not slot.has_material
    assert not any(path.exists() for path in _material_paths(paths))
    monkeypatch.undo()
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    ("phase", "control", "exit_code"),
    [
        ("adopt-begin", KeyboardInterrupt, None),
        ("adopt-inside", SystemExit, None),
        ("adopt-complete", SystemExit, 23),
    ],
)
def test_control_during_adoption_is_deferred_until_slot_owns_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    control: type[KeyboardInterrupt] | type[SystemExit],
    exit_code: int | None,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    injected = False

    def interrupt(selected: str) -> None:
        nonlocal injected
        if selected != phase or injected:
            return
        injected = True
        if control is KeyboardInterrupt:
            raise KeyboardInterrupt
        raise SystemExit(exit_code)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(control) as captured:
        test_generate_synthetic(slot, paths)
    assert injected and getattr(captured.value, "code", None) == exit_code
    assert slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    cleanup_tls_material_generation_slot(slot)


def test_repeated_controls_preserve_first_signal_after_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    controls: list[BaseException] = [SystemExit(31), KeyboardInterrupt(), SystemExit(47)]

    def interrupt(phase: str) -> None:
        if phase.startswith("adopt-") and controls:
            raise controls.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert captured.value.code == 31
    assert controls == [] and slot.has_material
    cleanup_tls_material_generation_slot(slot)


def test_control_on_atomic_adoption_publication_line_reconciles_to_owned(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    line = _source_line(
        material_module.adopt_tls_material_generation_slot,
        'record.snapshot = ("retained", None, receipt)',
        after=True,
    )
    with _control_at_line(
        material_module.adopt_tls_material_generation_slot,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            test_generate_synthetic(slot, paths)
    assert injected == [True] and captured.value.code == 23
    assert slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    cleanup_tls_material_generation_slot(slot)


def test_core_retries_unknown_owned_probe_after_adoption_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    adoption_interrupted = False
    inspection_failed = False

    def interrupt(phase: str) -> None:
        nonlocal adoption_interrupted, inspection_failed
        if phase == "adopt-complete" and not adoption_interrupted:
            adoption_interrupted = True
            raise SystemExit(23)
        if phase == "owned-check-entry" and not inspection_failed:
            inspection_failed = True
            raise RuntimeError("ordinary-core-owned-inspection-sentinel")

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert captured.value.code == 23
    assert adoption_interrupted and inspection_failed and slot.has_material
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert all(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    cleanup_tls_material_generation_slot(slot)


def test_concurrent_cleanup_claim_after_adoption_remains_the_only_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    adoption_reached = threading.Event()
    cleanup_claimed = threading.Event()
    allow_cleanup = threading.Event()
    cleanup_errors: list[BaseException] = []
    interrupted = False
    cleanup_calls = 0
    original_cleanup = TlsMaterialLifetimeAuthority.cleanup

    def count_cleanup(
        authority: TlsMaterialLifetimeAuthority,
        *,
        initial_control: object = None,
    ) -> tuple[bool, object]:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return original_cleanup(authority, initial_control=initial_control)  # type: ignore[arg-type]

    def synchronize(phase: str) -> None:
        nonlocal interrupted
        if phase == "adopt-complete" and not interrupted:
            adoption_reached.set()
            assert cleanup_claimed.wait(2.0)
            interrupted = True
            raise SystemExit(23)
        if phase == "cleanup-owned":
            cleanup_claimed.set()
            assert allow_cleanup.wait(2.0)

    def clean() -> None:
        assert adoption_reached.wait(2.0)
        try:
            cleanup_tls_material_generation_slot(slot)
        except BaseException as error:
            cleanup_errors.append(error)

    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", count_cleanup)
    monkeypatch.setattr(material_module, "_slot_transition_hook", synchronize)
    cleaner = threading.Thread(target=clean, name="synthetic-tls-slot-cleaner")
    cleaner.start()
    try:
        with pytest.raises(SystemExit) as captured:
            test_generate_synthetic(slot, paths)
    finally:
        allow_cleanup.set()
        cleaner.join(2.0)
    assert not cleaner.is_alive() and cleanup_errors == []
    assert captured.value.code == 23 and interrupted and cleanup_calls == 1
    assert slot.cleanup_complete and not slot.has_material
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert not any(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )


def test_control_from_refused_adoption_wins_before_ordinary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    monkeypatch.setattr(
        tls_module,
        "adopt_tls_material_generation_slot",
        lambda _slot, _receipt, _reservation: (False, (SystemExit, 23)),
    )
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert captured.value.code == 23
    assert not slot.has_material
    assert not any(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )


@pytest.mark.parametrize(
    ("phase", "retained"),
    [
        ("adopt-begin", False),
        ("adopt-inside", True),
        ("adopt-complete", True),
    ],
)
def test_ordinary_adoption_transition_failure_has_one_retryable_safe_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    retained: bool,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    injected = False

    def fail(selected: str) -> None:
        nonlocal injected
        if selected == phase and not injected:
            injected = True
            raise RuntimeError("ordinary-adoption-sentinel")

    monkeypatch.setattr(material_module, "_slot_transition_hook", fail)
    with pytest.raises(CoturnTlsError, match=r"^Coturn TLS cleanup failed$") as captured:
        test_generate_synthetic(slot, paths)
    assert injected and slot.has_material is retained
    assert not traceback_contains(
        captured.value,
        "ordinary-adoption-sentinel",
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    if retained:
        cleanup_tls_material_generation_slot(slot)
    else:
        assert not any(path.exists() for path in _material_paths(paths))


def test_control_on_inner_generator_return_keeps_preexisting_slot_authority(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    target = tls_module._generate_tls_and_config_material.__code__
    injected = False

    def trace(frame: object, event: str, argument: object) -> object:
        nonlocal injected
        code = frame.f_code  # type: ignore[attr-defined]
        if code is target and event == "return" and argument is None and not injected:
            injected = True
            sys.settrace(None)
            raise SystemExit(61)
        return trace

    sys.settrace(trace)
    try:
        with pytest.raises(SystemExit) as captured:
            test_generate_synthetic(slot, paths)
    finally:
        sys.settrace(None)
    assert injected and captured.value.code == 61
    assert slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    cleanup_tls_material_generation_slot(slot)


def test_ordinary_generation_failure_is_sanitized_and_slot_is_reusable(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    secret = "ordinary-generation-private-sentinel"
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn private-key generation failed$",
    ) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=QueueRunner([RuntimeError(secret)]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert not slot.has_material
    assert not traceback_contains(
        captured.value,
        secret,
        SECRET,
        os.fspath(paths.contract.run_dir),
    )
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


def test_successful_cleanup_retry_never_emits_stale_inactive_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    original = TlsMaterialLifetimeAuthority.cleanup
    attempts = 0

    def transient_cleanup(
        authority: TlsMaterialLifetimeAuthority,
        *,
        initial_control: object = None,
    ) -> tuple[bool, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return True, initial_control
        return original(authority, initial_control=initial_control)  # type: ignore[arg-type]

    def fail_generator(**_arguments: object) -> None:
        raise CoturnTlsError("Coturn TLS material is invalid")

    generator = generation_module.bind_tls_material_slot_generator(fail_generator)
    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", transient_cleanup)
    line = _source_line(
        generation_module._TlsGenerationCall._reconcile,
        "self._settle_unpublished_lifetime()",
        after=True,
    )
    with _control_at_line(
        generation_module._TlsGenerationCall._reconcile,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            generator(
                slot=slot,
                runner=QueueRunner([]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
    assert injected == [True] and captured.value.code == 23 and attempts == 2
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert not slot.has_material
    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", original)
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


def test_combined_generation_recovery_uses_public_tls_cleanup_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    target = paths.control_dir / "slot-combined-recovery.bin"
    secret = b"slot-combined-recovery-sentinel"
    target.write_bytes(secret)
    descriptor = os.open(target, os.O_RDONLY)
    details = os.fstat(descriptor)
    private = receipt_module.new_private_descriptor_cleanup_authority()
    assert private.begin()
    assert private.publish(((descriptor, (details.st_dev, details.st_ino)),))
    real_remove = receipt_module.remove_owned_inode

    def fail_validation(*_args: object, **_kwargs: object) -> object:
        raise CoturnTlsPrivateCleanupRequired(private)

    monkeypatch.setattr(tls_module, "validate_tls_material", fail_validation)
    monkeypatch.setattr(
        TlsMaterialLifetimeAuthority,
        "retain_private_authority",
        lambda *_args: False,
    )
    monkeypatch.setattr(receipt_module, "remove_owned_inode", lambda *_args: False)
    combined = None
    try:
        with pytest.raises(CoturnTlsCleanupRequired) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        combined = captured.value.cleanup_authority
        assert type(combined) is tls_module.TlsCombinedCleanupAuthority
        assert combined.active and private.active and os.fstat(descriptor)
        assert not slot.has_material
        assert not traceback_contains(
            captured.value,
            secret,
            SECRET,
            PRIVATE_KEY,
            CERTIFICATE,
            os.fspath(paths.contract.run_dir),
        )
        monkeypatch.setattr(receipt_module, "remove_owned_inode", real_remove)
        cleanup_tls_material_authority(combined)
        combined = None
    finally:
        monkeypatch.setattr(receipt_module, "remove_owned_inode", real_remove)
        if combined is not None and combined.active:
            cleanup_tls_material_authority(combined)
        if private.active:
            cleanup_tls_private_authority(private)
    assert slot.has_material is False
    assert not any(path.exists() for path in _material_paths(paths))


@pytest.mark.parametrize("control_kind", ["exit", "keyboard"])
def test_control_private_authority_is_adopted_and_cleaned_with_lifetime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority, descriptor, target = _owned_private_descriptor(
        tmp_path,
        f"control-adopted-{control_kind}.bin",
    )

    def control_validation(*_args: object, **_kwargs: object) -> object:
        error: KeyboardInterrupt | SystemExit = (
            SystemExit(23) if control_kind == "exit" else KeyboardInterrupt()
        )
        error.cleanup_authority = authority  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(tls_module, "validate_tls_material", control_validation)
    expected = SystemExit if control_kind == "exit" else KeyboardInterrupt
    try:
        with pytest.raises(expected) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert type(captured.value) is expected
        assert getattr(captured.value, "code", None) == (23 if control_kind == "exit" else None)
        assert getattr(captured.value, "cleanup_authority", None) is None
        assert tls_private_cleanup_authority(captured.value) is None
        assert not authority.active and target.exists()
        with pytest.raises(OSError):
            os.fstat(descriptor)
        assert not slot.has_material
        assert not any(path.exists() for path in _material_paths(paths))
        assert not traceback_contains(
            captured.value,
            *_material_graph_secrets(paths),
            os.fspath(target),
            "private-descriptor-sentinel",
        )
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("control_kind", ["exit", "keyboard"])
def test_control_private_authority_adoption_refusal_returns_exact_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority, descriptor, target = _owned_private_descriptor(
        tmp_path,
        f"control-refused-{control_kind}.bin",
    )

    def control_validation(*_args: object, **_kwargs: object) -> object:
        error: KeyboardInterrupt | SystemExit = (
            SystemExit(23) if control_kind == "exit" else KeyboardInterrupt()
        )
        error.cleanup_authority = authority  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(tls_module, "validate_tls_material", control_validation)
    monkeypatch.setattr(
        TlsMaterialLifetimeAuthority,
        "retain_private_authority",
        lambda *_args: False,
    )
    expected = SystemExit if control_kind == "exit" else KeyboardInterrupt
    try:
        with pytest.raises(expected) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE)]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert type(captured.value) is expected
        assert getattr(captured.value, "code", None) == (23 if control_kind == "exit" else None)
        assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
        assert tls_private_cleanup_authority(captured.value) is authority
        assert authority.active and os.fstat(descriptor) and target.exists()
        assert not slot.has_material
        assert not any(path.exists() for path in _material_paths(paths))
        assert not traceback_contains(
            captured.value,
            *_material_graph_secrets(paths),
            os.fspath(target),
            "private-descriptor-sentinel",
        )
        cleanup_tls_private_authority(authority)
        assert not authority.active and target.exists()
        with pytest.raises(OSError):
            os.fstat(descriptor)
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


def test_bound_generator_adopts_active_authority_from_explicit_cause(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    runner = QueueRunner([])
    authority, descriptor, target = _owned_private_descriptor(
        tmp_path,
        "explicit-cause-private.bin",
    )

    def fail_generator(**_arguments: object) -> None:
        try:
            raise RuntimeError("unrelated-context-sentinel")
        except RuntimeError:
            raise RuntimeError("bound-generator-failure") from CoturnTlsPrivateCleanupRequired(
                authority
            )

    generator = generation_module.bind_tls_material_slot_generator(fail_generator)
    try:
        with pytest.raises(CoturnTlsError) as captured:
            generator(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert not authority.active
        with pytest.raises(OSError):
            os.fstat(descriptor)
        assert runner.requests == [] and not slot.has_material
        assert not traceback_contains(
            captured.value,
            *_material_graph_secrets(paths),
            os.fspath(target),
            "private-descriptor-sentinel",
            "unrelated-context-sentinel",
        )
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


def test_bound_generator_does_not_adopt_suppressed_context_authority(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    runner = QueueRunner([])
    authority, descriptor, target = _owned_private_descriptor(
        tmp_path,
        "suppressed-context-private.bin",
    )

    def fail_generator(**_arguments: object) -> None:
        try:
            raise CoturnTlsPrivateCleanupRequired(authority)
        except CoturnTlsPrivateCleanupRequired:
            raise RuntimeError("bound-generator-failure") from None

    generator = generation_module.bind_tls_material_slot_generator(fail_generator)
    try:
        with pytest.raises(CoturnTlsError) as captured:
            generator(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert authority.active and os.fstat(descriptor)
        assert runner.requests == [] and not slot.has_material
        assert not traceback_contains(
            captured.value,
            *_material_graph_secrets(paths),
            os.fspath(target),
            "private-descriptor-sentinel",
        )
    finally:
        if authority.active:
            cleanup_tls_private_authority(authority)


def test_cleanup_failure_retains_slot_for_exact_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    original = TlsMaterialLifetimeAuthority.cleanup
    failed_once = False

    def fail_once(
        authority: TlsMaterialLifetimeAuthority,
        *,
        initial_control: object = None,
    ) -> tuple[bool, object]:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            return True, initial_control
        return original(authority, initial_control=initial_control)  # type: ignore[arg-type]

    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", fail_once)
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn TLS generation slot cleanup failed$",
    ):
        cleanup_tls_material_generation_slot(slot)
    assert failed_once and slot.has_material
    assert all(path.exists() for path in _material_paths(paths))
    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", original)
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete
    assert not any(path.exists() for path in _material_paths(paths))


@pytest.mark.parametrize(
    ("kind", "expected", "code"),
    [
        ("ordinary", CoturnTlsError, None),
        ("keyboard", KeyboardInterrupt, None),
        ("exit", SystemExit, 23),
    ],
)
def test_public_cleanup_failure_graph_scrubs_exact_material_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected: type[BaseException],
    code: int | None,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    original = TlsMaterialLifetimeAuthority.cleanup

    def fail_with_control(
        _authority: TlsMaterialLifetimeAuthority,
        *,
        initial_control: object = None,
    ) -> tuple[bool, object]:
        if kind == "keyboard":
            return True, (KeyboardInterrupt, None)
        if kind == "exit":
            return True, (SystemExit, 23)
        return True, initial_control

    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", fail_with_control)
    with pytest.raises(expected) as captured:
        cleanup_tls_material_generation_slot(slot)
    assert type(captured.value) is expected
    assert getattr(captured.value, "code", None) == code
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", original)
    assert slot.has_material
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete


def test_cleanup_claim_publication_line_control_finishes_safe_cleanup(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    line = _source_line(
        material_module.cleanup_tls_material_generation_slot,
        'record.snapshot = ("cleaning", operation, receipt)',
        after=True,
    )
    with _control_at_line(
        material_module.cleanup_tls_material_generation_slot,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            cleanup_tls_material_generation_slot(slot)
    assert injected == [True] and captured.value.code == 23
    assert slot.cleanup_complete and not slot.has_material
    assert not any(path.exists() for path in _material_paths(paths))


def test_cleanup_restore_publication_line_control_keeps_retry_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    original = TlsMaterialLifetimeAuthority.cleanup
    monkeypatch.setattr(
        TlsMaterialLifetimeAuthority,
        "cleanup",
        lambda _authority, *, initial_control=None: (True, initial_control),
    )
    line = _source_line(
        material_module.cleanup_tls_material_generation_slot,
        'record.snapshot = ("retained", None, receipt)',
        after=True,
    )
    with _control_at_line(
        material_module.cleanup_tls_material_generation_slot,
        line,
        KeyboardInterrupt(),
    ) as injected:
        with pytest.raises(KeyboardInterrupt):
            cleanup_tls_material_generation_slot(slot)
    assert injected == [True] and slot.has_material
    assert all(path.exists() for path in _material_paths(paths))
    monkeypatch.setattr(TlsMaterialLifetimeAuthority, "cleanup", original)
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete


@pytest.mark.parametrize(
    "marker",
    [
        "observed_control = control.value()",
        "if observed_control is None:",
        "terminal_error = _tls_values.new_tls_control_error(observed_control)",
        "raise terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_cleanup_terminal_lines_preserve_first_control_after_state_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    first = True

    def interrupt(phase: str) -> None:
        nonlocal first
        if phase == "cleanup-state-safe" and first:
            first = False
            raise SystemExit(23)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    line = _source_line(cleanup_tls_material_generation_slot, marker)
    with _control_at_line(
        cleanup_tls_material_generation_slot,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            cleanup_tls_material_generation_slot(slot)
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert slot.cleanup_complete and not slot.has_material
    assert not any(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    cleanup_tls_material_generation_slot(slot)


def test_factory_preserves_first_control_and_discards_unpublished_empty_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    gc.collect()
    baseline = len(material_module._REGISTRY)
    controls: dict[str, list[BaseException]] = {
        "factory-entry": [SystemExit(23)],
        "factory-return": [KeyboardInterrupt()],
    }

    def interrupt(phase: str) -> None:
        pending = controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    assert captured.value.code == 23
    assert all(not values for values in controls.values())
    gc.collect()
    assert len(material_module._REGISTRY) == baseline
    assert not traceback_contains(
        captured.value,
        os.fspath(paths.contract.run_dir),
    )


@pytest.mark.parametrize(
    "marker",
    [
        "observed_control = control.value()",
        "if (failed or observed_control is not None) and handle is not None:",
        "removed = _REGISTRY.get(handle) is None",
        "paths = topology = None",
        "if observed_control is None:",
        "if not removed:",
        "terminal_error = _tls_values.new_tls_control_error(observed_control)",
        "raise terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_factory_terminal_lines_preserve_first_control_and_remove_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    gc.collect()
    baseline = len(material_module._REGISTRY)
    first = True

    def interrupt(phase: str) -> None:
        nonlocal first
        if phase == "factory-return" and first:
            first = False
            raise SystemExit(23)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    line = _source_line(new_tls_material_generation_slot, marker)
    with _control_at_line(
        new_tls_material_generation_slot,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    gc.collect()
    assert len(material_module._REGISTRY) == baseline
    assert not traceback_contains(
        captured.value,
        os.fspath(paths.contract.run_dir),
        *(os.fspath(path) for path in _material_paths(paths)),
    )


def test_state_and_evidence_lookups_preserve_first_control_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    state_controls: dict[str, list[BaseException]] = {
        "state-entry": [SystemExit(23)],
        "state-return": [KeyboardInterrupt()],
    }

    def interrupt_state(phase: str) -> None:
        pending = state_controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt_state)
    with pytest.raises(SystemExit) as state_error:
        _ = slot.has_material
    assert state_error.value.code == 23
    assert all(not values for values in state_controls.values())

    evidence_controls: dict[str, list[BaseException]] = {
        "evidence-entry": [KeyboardInterrupt()],
        "evidence-return": [SystemExit(47)],
    }

    def interrupt_evidence(phase: str) -> None:
        pending = evidence_controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt_evidence)
    with pytest.raises(KeyboardInterrupt):
        _ = slot.certificate_sha256
    assert all(not values for values in evidence_controls.values())
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    assert slot.has_material
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    "marker",
    [
        "observed_control = control.value()",
        "if observed_control is None:",
        "if terminal_error is None:",
        "terminal_error = _tls_values.new_tls_control_error(observed_control)",
        "raise terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_slot_state_terminal_lines_preserve_first_control_and_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    first = True

    def interrupt(phase: str) -> None:
        nonlocal first
        if phase == "state-return" and first:
            first = False
            raise SystemExit(23)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    line = _source_line(material_module._slot_state, marker)
    with _control_at_line(
        material_module._slot_state,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            _ = slot.has_material
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    assert slot.has_material
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    "marker",
    [
        "observed_control = control.value()",
        "if observed_control is None:",
        "if terminal_error is None:",
        "terminal_error = _tls_values.new_tls_control_error(observed_control)",
        "raise terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_slot_evidence_terminal_lines_preserve_first_control_and_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    first = True

    def interrupt(phase: str) -> None:
        nonlocal first
        if phase == "evidence-return" and first:
            first = False
            raise SystemExit(23)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    line = _source_line(material_module._slot_evidence, marker)
    with _control_at_line(
        material_module._slot_evidence,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            _ = slot.certificate_sha256
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    assert slot.certificate_sha256 == hashlib.sha256(CERTIFICATE).hexdigest()
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    ("kind", "expected", "code"),
    [
        ("ordinary", CoturnTlsError, None),
        ("keyboard", KeyboardInterrupt, None),
        ("exit", SystemExit, 23),
    ],
)
def test_public_evidence_failure_graph_scrubs_exact_material_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected: type[BaseException],
    code: int | None,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    pending = True

    def interrupt(phase: str) -> None:
        nonlocal pending
        if phase != "evidence-return" or not pending:
            return
        pending = False
        if kind == "keyboard":
            raise KeyboardInterrupt
        if kind == "exit":
            raise SystemExit(23)
        raise RuntimeError("hostile evidence lookup")

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(expected) as captured:
        _ = slot.certificate_sha256
    assert not pending
    assert type(captured.value) is expected
    assert getattr(captured.value, "code", None) == code
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    assert slot.has_material
    cleanup_tls_material_generation_slot(slot)


def test_reserved_mutation_and_release_controls_preserve_first_without_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    controls: dict[str, list[BaseException]] = {
        "reserve-inside": [SystemExit(23)],
        "release-entry": [KeyboardInterrupt()],
        "release-return": [SystemExit(47)],
    }

    def interrupt(phase: str) -> None:
        pending = controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    runner = QueueRunner([])
    with pytest.raises(SystemExit) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=runner,
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert captured.value.code == 23
    assert all(not values for values in controls.values())
    assert runner.requests == [] and not slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        os.fspath(paths.contract.run_dir),
    )


def test_boundary_construction_controls_preserve_first_before_any_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    original = generation_module._TlsGenerationCall
    controls: list[BaseException] = [SystemExit(23), KeyboardInterrupt()]

    def construct(*args: object, **kwargs: object) -> object:
        if controls:
            raise controls.pop(0)
        return original(*args, **kwargs)

    monkeypatch.setattr(generation_module, "_TlsGenerationCall", construct)
    runner = QueueRunner([])
    with pytest.raises(SystemExit) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=runner,
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert captured.value.code == 23 and controls == []
    assert runner.requests == [] and not slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        os.fspath(paths.contract.run_dir),
    )


@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_constructor_caller_line_control_scrubs_arguments_and_leaves_empty_slot(
    tmp_path: Path,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    line = _source_line(
        generate_tls_and_config_material_into_slot,
        "protected generation-owner bootstrap",
    )
    runner = QueueRunner([])
    with _control_at_line(
        generate_tls_and_config_material_into_slot,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises((KeyboardInterrupt, SystemExit)) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
    assert injected == [True]
    if late_kind == "opposite":
        assert type(captured.value) is KeyboardInterrupt
    else:
        assert type(captured.value) is SystemExit and captured.value.code == 47
    assert runner.requests == [] and not slot.has_material
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize("count", [3, 65])
@pytest.mark.parametrize("mode", ["same", "alternating"])
def test_generation_owner_bootstrap_preserves_first_across_finite_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    mode: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    original = generation_module._TlsGenerationCall
    controls = _finite_controls(count, mode)

    def construct(*args: object, **kwargs: object) -> object:
        if controls:
            raise controls.pop(0)
        return original(*args, **kwargs)

    monkeypatch.setattr(generation_module, "_TlsGenerationCall", construct)
    runner = QueueRunner([])
    with pytest.raises(SystemExit) as captured:
        generate_tls_and_config_material_into_slot(
            slot=slot,
            runner=runner,
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
    assert controls == []
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert runner.requests == [] and not slot.has_material
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    monkeypatch.setattr(generation_module, "_TlsGenerationCall", original)
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


def test_generation_reconcile_entry_control_scrubs_raw_arguments_before_emission(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    runner = _successful_runner()
    line = _source_line(
        generate_tls_and_config_material_into_slot,
        "try: runner = tools = paths",
    )
    with _control_at_line(
        generate_tls_and_config_material_into_slot,
        line,
        SystemExit(23),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
    assert injected == [True]
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert runner.requests == [] and not slot.has_material
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    test_generate_synthetic(slot, paths)
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_generation_terminal_entry_preserves_latched_control_and_direct_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority = tls_module.new_tls_material_lifetime_authority()
    private = authority.new_slot()
    assert private.mark_unsubmitted_empty() and authority.retain()

    class TerminalCall:
        def advance(self) -> bool:
            return True

        def outcome(self) -> tuple[bool, object, None, object, bool]:
            return True, (SystemExit, 23), None, authority, False

        def scrub_terminal(self) -> None:
            return None

    monkeypatch.setattr(
        generation_module,
        "_TlsGenerationCall",
        lambda *_args, **_kwargs: TerminalCall(),
    )
    line = _source_line(
        generate_tls_and_config_material_into_slot,
        "protected terminal publication trampoline",
    )
    runner = QueueRunner([])
    try:
        with _control_at_line(
            generate_tls_and_config_material_into_slot,
            line,
            KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
        ) as injected:
            with pytest.raises(SystemExit) as captured:
                generate_tls_and_config_material_into_slot(
                    slot=slot,
                    runner=runner,
                    tools=_tools(),
                    paths=paths,
                    topology=TOPOLOGY,
                    static_auth_secret=SECRET,
                    now=NOW,
                )
        assert injected == [True]
        assert type(captured.value) is SystemExit and captured.value.code == 23
        assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
        assert authority.active and runner.requests == [] and not slot.has_material
        assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    finally:
        if authority.active:
            cleanup_tls_material_authority(authority)


@pytest.mark.parametrize("count", [3, 65])
@pytest.mark.parametrize("mode", ["same", "alternating"])
def test_generation_publication_owner_preserves_first_across_finite_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    mode: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority = tls_module.new_tls_material_lifetime_authority()
    private = authority.new_slot()
    assert private.mark_unsubmitted_empty() and authority.retain()
    controls = _finite_controls(count, mode)[1:]
    original_emit = generation_module._emit_generation_error

    class TerminalCall:
        def advance(self) -> bool:
            return True

        def outcome(self) -> tuple[bool, object, None, object, bool]:
            return True, (SystemExit, 23), None, authority, False

        def scrub_terminal(self) -> None:
            return None

    def emit(error: BaseException | None) -> None:
        if controls:
            raise controls.pop(0)
        original_emit(error)

    monkeypatch.setattr(
        generation_module,
        "_TlsGenerationCall",
        lambda *_args, **_kwargs: TerminalCall(),
    )
    monkeypatch.setattr(generation_module, "_emit_generation_error", emit)
    runner = QueueRunner([])
    try:
        with pytest.raises(SystemExit) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert controls == []
        assert type(captured.value) is SystemExit and captured.value.code == 23
        assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
        assert authority.active and runner.requests == [] and not slot.has_material
        assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    finally:
        if authority.active:
            cleanup_tls_material_authority(authority)


def test_generation_caller_line_recontrol_keeps_authority_recoverable_and_scrubbed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority = tls_module.new_tls_material_lifetime_authority()
    private = authority.new_slot()
    assert private.mark_unsubmitted_empty() and authority.retain()
    controls: list[BaseException] = [KeyboardInterrupt(), SystemExit(47)]

    class TerminalCall:
        def advance(self) -> bool:
            return True

        def outcome(self) -> tuple[bool, object, None, object, bool]:
            return True, (SystemExit, 23), None, authority, False

        def scrub_terminal(self) -> None:
            return None

    def caller_line_control(*_args: object) -> None:
        raise controls.pop(0)

    monkeypatch.setattr(
        generation_module,
        "_TlsGenerationCall",
        lambda *_args, **_kwargs: TerminalCall(),
    )
    monkeypatch.setattr(generation_module, "_publish_generation_terminal", caller_line_control)
    runner = QueueRunner([])
    try:
        with pytest.raises(SystemExit) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        assert controls == [] and captured.value.code == 47
        context = captured.value.__context__
        assert type(context) is KeyboardInterrupt
        assert context.cleanup_authority is authority  # type: ignore[attr-defined]
        assert authority.active and runner.requests == [] and not slot.has_material
        assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    finally:
        if authority.active:
            cleanup_tls_material_authority(authority)


def test_generation_caller_line_recontrol_leaves_adopted_slot_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    controls: list[BaseException] = [KeyboardInterrupt(), SystemExit(47)]
    interrupted = False

    def generation_control(phase: str) -> None:
        nonlocal interrupted
        if phase == "generator-return" and not interrupted:
            interrupted = True
            raise SystemExit(23)

    def caller_line_control(*_args: object) -> None:
        raise controls.pop(0)

    monkeypatch.setattr(generation_module, "_generation_boundary_hook", generation_control)
    monkeypatch.setattr(generation_module, "_publish_generation_terminal", caller_line_control)
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert controls == [] and interrupted and captured.value.code == 47
    assert slot.has_material and all(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    cleanup_tls_material_generation_slot(slot)
    assert slot.cleanup_complete


def test_owned_check_scrub_and_final_raise_controls_keep_first_and_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    controls: dict[str, list[BaseException]] = {
        "generator-return": [SystemExit(23)],
        "abort-owned-return": [KeyboardInterrupt()],
        "pre-final-scrub": [SystemExit(47)],
        "final-raise": [KeyboardInterrupt()],
    }

    def interrupt(phase: str) -> None:
        pending = controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(generation_module, "_generation_boundary_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert captured.value.code == 23
    assert all(not values for values in controls.values())
    assert slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    cleanup_tls_material_generation_slot(slot)


def test_unknown_owned_probe_retries_without_cleanup_or_duplicate_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    generation_interrupted = False
    inspection_failed = False

    def interrupt_generation(phase: str) -> None:
        nonlocal generation_interrupted
        if phase == "generator-return" and not generation_interrupted:
            generation_interrupted = True
            raise SystemExit(23)

    def fail_owned_probe(phase: str) -> None:
        nonlocal inspection_failed
        if phase == "owned-check-entry" and not inspection_failed:
            inspection_failed = True
            raise RuntimeError("ordinary-owned-inspection-sentinel")

    monkeypatch.setattr(generation_module, "_generation_boundary_hook", interrupt_generation)
    monkeypatch.setattr(material_module, "_slot_transition_hook", fail_owned_probe)
    with pytest.raises(SystemExit) as captured:
        test_generate_synthetic(slot, paths)
    assert captured.value.code == 23
    assert generation_interrupted and inspection_failed and slot.has_material
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert all(path.exists() for path in _material_paths(paths))
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_exact_final_publication_line_cannot_replace_latched_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    first = True

    def interrupt(phase: str) -> None:
        nonlocal first
        if phase == "generator-return" and first:
            first = False
            raise SystemExit(23)

    monkeypatch.setattr(generation_module, "_generation_boundary_hook", interrupt)
    line = _source_line(
        generate_tls_and_config_material_into_slot,
        "protected terminal publication trampoline",
    )
    with _control_at_line(
        generate_tls_and_config_material_into_slot,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            test_generate_synthetic(slot, paths)
    assert injected == [True] and captured.value.code == 23 and not first
    assert slot.has_material
    assert not traceback_contains(
        captured.value,
        SECRET,
        PRIVATE_KEY,
        CERTIFICATE,
        os.fspath(paths.contract.run_dir),
    )
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    "marker",
    [
        "inner publication control",
        "replay_terminal = observed is terminal_error",
        "if not replay_terminal and not isinstance(",
        "if replay_terminal:",
        "raise terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_generation_terminal_handler_preserves_latched_exit_and_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    first = True
    late = True
    original_emit = generation_module._emit_generation_error

    def interrupt_generation(phase: str) -> None:
        nonlocal first
        if phase == "generator-return" and first:
            first = False
            raise SystemExit(23)

    def interrupt_emit(error: BaseException | None) -> None:
        nonlocal late
        if late:
            late = False
            if late_kind == "opposite":
                raise KeyboardInterrupt
            raise SystemExit(47)
        original_emit(error)

    monkeypatch.setattr(generation_module, "_generation_boundary_hook", interrupt_generation)
    monkeypatch.setattr(generation_module, "_emit_generation_error", interrupt_emit)
    line = _source_line(generation_module._publish_generation_terminal, marker)
    with _control_at_line(
        generation_module._publish_generation_terminal,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            test_generate_synthetic(slot, paths)
    assert injected == [True] and not first and not late
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert getattr(captured.value, "cleanup_authority", None) is None
    assert slot.has_material
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize(
    ("marker", "after"),
    [
        ("inner publication recovery", False),
        ("inner publication recovery", True),
        ("inner publication rebuild", False),
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_generation_terminal_handler_preserves_first_control_and_direct_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    after: bool,
    late_kind: str,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    authority = tls_module.new_tls_material_lifetime_authority()
    private = authority.new_slot()
    assert private.mark_unsubmitted_empty() and authority.retain()
    first = True
    original_emit = generation_module._emit_generation_error

    class TerminalCall:
        def advance(self) -> bool:
            return True

        def outcome(self) -> tuple[bool, None, None, object, bool]:
            return True, None, None, authority, False

        def scrub_terminal(self) -> None:
            return None

    def interrupt_emit(error: BaseException | None) -> None:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        original_emit(error)

    monkeypatch.setattr(
        generation_module, "_TlsGenerationCall", lambda *_args, **_kwargs: TerminalCall()
    )
    monkeypatch.setattr(generation_module, "_emit_generation_error", interrupt_emit)
    line = _source_line(
        generation_module._publish_generation_terminal,
        marker,
        after=after,
    )
    runner = QueueRunner([])
    with _control_at_line(
        generation_module._publish_generation_terminal,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=runner,
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert authority.active and not slot.has_material and runner.requests == []
    assert not traceback_contains(captured.value, *_material_graph_secrets(paths))
    cleanup_tls_material_authority(authority)


def test_cleanup_controls_finish_state_then_preserve_first_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    controls: dict[str, list[BaseException]] = {
        "cleanup-owned": [SystemExit(23)],
        "cleanup-state-safe": [KeyboardInterrupt()],
        "cleanup-return": [SystemExit(47)],
    }

    def interrupt(phase: str) -> None:
        pending = controls.get(phase, [])
        if pending:
            raise pending.pop(0)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    with pytest.raises(SystemExit) as captured:
        cleanup_tls_material_generation_slot(slot)
    assert captured.value.code == 23
    assert all(not values for values in controls.values())
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    assert slot.cleanup_complete
    cleanup_tls_material_generation_slot(slot)


def test_ordinary_cleanup_transition_failure_restores_retryable_retained_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    injected = False

    def fail_after_ownership(phase: str) -> None:
        nonlocal injected
        if phase == "cleanup-owned" and not injected:
            injected = True
            raise RuntimeError("private-transition-sentinel")

    monkeypatch.setattr(material_module, "_slot_transition_hook", fail_after_ownership)
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn TLS generation slot cleanup failed$",
    ) as captured:
        cleanup_tls_material_generation_slot(slot)
    assert injected and slot.has_material
    assert not traceback_contains(captured.value, "private-transition-sentinel")
    monkeypatch.setattr(material_module, "_slot_transition_hook", lambda _phase: None)
    cleanup_tls_material_generation_slot(slot)


def test_repeated_cleaned_slot_drop_returns_registry_to_baseline(tmp_path: Path) -> None:
    gc.collect()
    baseline = len(material_module._REGISTRY)
    for index in range(3):
        root = tmp_path / f"run-{index}"
        root.mkdir()
        paths = _paths(root)
        slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
        handle = slot._handle
        test_generate_synthetic(slot, paths)
        cleanup_tls_material_generation_slot(slot)
        del slot
        gc.collect()
        assert handle not in material_module._REGISTRY
    assert len(material_module._REGISTRY) == baseline


def test_finite_control_in_each_empty_slot_finalizer_never_consumes_registry_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    gc.collect()
    baseline = len(material_module._REGISTRY)
    armed = False
    interrupted = 0

    def interrupt(phase: str) -> None:
        nonlocal armed, interrupted
        if phase == "finalizer-entry" and armed:
            armed = False
            interrupted += 1
            raise SystemExit(23)

    monkeypatch.setattr(material_module, "_slot_transition_hook", interrupt)
    count = material_module._MAX_GENERATION_SLOTS + 8
    for _index in range(count):
        slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
        handle = slot._handle
        armed = True
        del slot
        gc.collect()
        assert handle not in material_module._REGISTRY
    assert interrupted == count
    assert len(material_module._REGISTRY) == baseline


def test_slot_registry_has_a_hard_empty_slot_cap(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    gc.collect()
    baseline = len(material_module._REGISTRY)
    available = material_module._MAX_GENERATION_SLOTS - baseline
    slots = [
        new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
        for _index in range(available)
    ]
    with pytest.raises(
        CoturnTlsError,
        match=r"^Coturn TLS generation slot is invalid$",
    ):
        new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    slots.clear()
    gc.collect()
    assert len(material_module._REGISTRY) == baseline


def test_exception_and_slot_graphs_hide_secret_path_and_receipt_internals(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    slot = new_tls_material_generation_slot(paths=paths, topology=TOPOLOGY)
    test_generate_synthetic(slot, paths)
    try:
        with pytest.raises(CoturnTlsError) as captured:
            generate_tls_and_config_material_into_slot(
                slot=slot,
                runner=QueueRunner([]),
                tools=_tools(),
                paths=paths,
                topology=TOPOLOGY,
                static_auth_secret=SECRET,
                now=NOW,
            )
        captured.value.slot = slot  # type: ignore[attr-defined]
        assert not traceback_contains(
            captured.value,
            SECRET,
            PRIVATE_KEY,
            CERTIFICATE,
            os.fspath(paths.contract.run_dir),
            "key.pem",
            "TlsMaterialReceipt",
        )
    finally:
        cleanup_tls_material_generation_slot(slot)


@pytest.mark.parametrize("kind", ["ordinary", "keyboard", "exit"])
@pytest.mark.parametrize("authority_kind", ["descriptor", "file"])
def test_public_private_authority_extractor_returns_only_exact_factory_authority(
    kind: str,
    authority_kind: str,
) -> None:
    if authority_kind == "descriptor":
        authority = receipt_module.new_private_descriptor_cleanup_authority()
    else:
        authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    if kind == "ordinary":
        error: BaseException = CoturnTlsPrivateCleanupRequired(authority)
    elif kind == "keyboard":
        error = KeyboardInterrupt()
        error.cleanup_authority = authority  # type: ignore[attr-defined]
    else:
        error = SystemExit(23)
        error.cleanup_authority = authority  # type: ignore[attr-defined]
    error = _private_extractor_trace(error)
    extracted = tls_private_cleanup_authority(error)
    assert extracted is authority
    sanitized = CoturnTlsPrivateCleanupRequired(extracted)
    assert not traceback_contains(sanitized, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(extracted)


@pytest.mark.parametrize("boundary", ["public", "retry-loop"])
def test_public_private_authority_extractor_entry_preserves_control_and_authority(
    boundary: str,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    if boundary == "public":
        function = tls_private_cleanup_authority
        marker = "guarded extractor entry"
    else:
        function = values_module.retry_tls_private_candidate
        marker = "while True:"
    line = _source_line(function, marker)
    with _control_at_line(function, line, SystemExit(23)) as injected:
        with pytest.raises(SystemExit) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True]
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("boundary", ["public-transition", "runner-entry"])
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_private_authority_publication_trampoline_preserves_first_control(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    late_kind: str,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original = tls_module._private_cleanup_candidate
    first = True

    def interrupt(candidate: BaseException) -> object:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        return original(candidate)

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", interrupt)
    if boundary == "public-transition":
        function = tls_private_cleanup_authority
        marker = "protected extractor-publication transition"
    else:
        function = values_module.TlsPrivateControlPublication.publish
        marker = "protected private-publication trampoline"
    line = _source_line(function, marker)
    with _control_at_line(
        function,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("count", [3, 65])
@pytest.mark.parametrize("mode", ["same", "alternating"])
def test_private_publication_owner_preserves_first_across_finite_controls(
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    mode: str,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original_candidate = tls_module._private_cleanup_candidate
    original_attempt = values_module.TlsPrivateControlPublication._attempt
    first = True
    controls = _finite_controls(count, mode)[1:]

    def candidate(value: BaseException) -> object:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        return original_candidate(value)

    def attempt(publication: object) -> object:
        if controls:
            raise controls.pop(0)
        return original_attempt(publication)  # type: ignore[arg-type]

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", candidate)
    monkeypatch.setattr(values_module.TlsPrivateControlPublication, "_attempt", attempt)
    with pytest.raises(SystemExit) as captured:
        tls_private_cleanup_authority(error)
    assert controls == [] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


def test_private_caller_line_recontrol_keeps_authority_in_effective_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original_candidate = tls_module._private_cleanup_candidate
    original_publish = values_module.TlsPrivateControlPublication.publish
    first = True
    controls: list[BaseException] = [KeyboardInterrupt(), SystemExit(47)]

    def candidate(value: BaseException) -> object:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        return original_candidate(value)

    def caller_line_control(publication: object) -> object:
        if controls:
            raise controls.pop(0)
        return original_publish(publication)  # type: ignore[arg-type]

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", candidate)
    monkeypatch.setattr(values_module.TlsPrivateControlPublication, "publish", caller_line_control)
    with pytest.raises(SystemExit) as captured:
        tls_private_cleanup_authority(error)
    assert controls == [] and not first and captured.value.code == 47
    context = captured.value.__context__
    assert type(context) is KeyboardInterrupt
    assert context.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    monkeypatch.setattr(values_module.TlsPrivateControlPublication, "publish", original_publish)
    assert tls_private_cleanup_authority(captured.value) is authority
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize(
    ("controls", "expected", "code"),
    [
        ([KeyboardInterrupt()], KeyboardInterrupt, None),
        ([SystemExit(23), KeyboardInterrupt()], SystemExit, 23),
        ([SystemExit(23), SystemExit(47)], SystemExit, 23),
    ],
)
def test_public_private_authority_extractor_defers_finite_controls_until_scrubbed(
    monkeypatch: pytest.MonkeyPatch,
    controls: list[BaseException],
    expected: type[KeyboardInterrupt] | type[SystemExit],
    code: int | None,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original = tls_module._private_cleanup_candidate

    def interrupt(candidate: BaseException) -> object:
        if controls:
            raise controls.pop(0)
        return original(candidate)

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", interrupt)
    with pytest.raises(expected) as captured:
        tls_private_cleanup_authority(error)
    assert getattr(captured.value, "code", None) == code
    assert controls == []
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize(
    ("injected_control", "expected", "code"),
    [
        (KeyboardInterrupt(), KeyboardInterrupt, None),
        (SystemExit(23), SystemExit, 23),
    ],
)
def test_public_private_authority_extractor_preserves_control_inside_graph_walk(
    injected_control: KeyboardInterrupt | SystemExit,
    expected: type[KeyboardInterrupt] | type[SystemExit],
    code: int | None,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    line = _source_line(
        tls_module._private_cleanup_candidate,
        "if type(candidate) in {",
    )
    with _control_at_line(
        tls_module._private_cleanup_candidate,
        line,
        injected_control,
    ) as injected:
        with pytest.raises(expected) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True]
    assert type(captured.value) is expected
    assert getattr(captured.value, "code", None) == code
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize(
    "marker",
    [
        "observed_control = self.control.value()",
        "if observed_control is None:",
        "if self.terminal_error is None:",
        "self.terminal_error = new_tls_control_error(observed_control)",
        "if not self.terminal_ready:",
        "if self.authority is not None:",
        "self.terminal_error.cleanup_authority = self.authority",
        "self.terminal_error.material_committed = self.committed",
        "self.terminal_ready = True",
        "raise self.terminal_error from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_public_private_authority_terminal_lines_preserve_first_control(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original = tls_module._private_cleanup_candidate
    first = True

    def interrupt_candidate(candidate: BaseException) -> object:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        return original(candidate)

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", interrupt_candidate)
    line = _source_line(values_module.TlsPrivateControlPublication._attempt, marker)
    with _control_at_line(
        values_module.TlsPrivateControlPublication._attempt,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize(
    "marker",
    [
        "terminal publication control",
        "replay_terminal = self.is_terminal(observed)",
        "if replay_terminal:",
        "raise self.terminal_error.with_traceback(None) from None",
    ],
)
@pytest.mark.parametrize("late_kind", ["opposite", "same"])
def test_public_private_authority_handler_preserves_first_control_and_authority(
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    late_kind: str,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    original_candidate = tls_module._private_cleanup_candidate
    first = True

    def interrupt_candidate(candidate: BaseException) -> object:
        nonlocal first
        if first:
            first = False
            raise SystemExit(23)
        return original_candidate(candidate)

    monkeypatch.setattr(tls_module, "_private_cleanup_candidate", interrupt_candidate)
    line = _source_line(values_module.TlsPrivateControlPublication._publish_loop, marker)
    with _control_at_line(
        values_module.TlsPrivateControlPublication._publish_loop,
        line,
        KeyboardInterrupt() if late_kind == "opposite" else SystemExit(47),
    ) as injected:
        with pytest.raises(SystemExit) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True] and not first
    assert type(captured.value) is SystemExit and captured.value.code == 23
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


def test_public_private_authority_return_line_promotes_late_control() -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    error = _private_extractor_trace(CoturnTlsPrivateCleanupRequired(authority))
    line = _source_line(
        values_module.TlsPrivateControlPublication._attempt,
        "if observed_control is None:",
        after=True,
    )
    with _control_at_line(
        values_module.TlsPrivateControlPublication._attempt,
        line,
        KeyboardInterrupt(),
    ) as injected:
        with pytest.raises(KeyboardInterrupt) as captured:
            tls_private_cleanup_authority(error)
    assert injected == [True] and type(captured.value) is KeyboardInterrupt
    assert captured.value.cleanup_authority is authority  # type: ignore[attr-defined]
    assert not traceback_contains(captured.value, "private-extractor-frame-sentinel")
    cleanup_tls_private_authority(authority)


def test_public_private_authority_prefers_explicit_cause_over_context() -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    outer = RuntimeError("fixed")
    outer.__context__ = RuntimeError("unrelated context")
    outer.__cause__ = CoturnTlsPrivateCleanupRequired(authority)
    assert tls_private_cleanup_authority(outer) is authority
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("explicit_cause", [False, True])
def test_public_private_authority_ignores_suppressed_context(
    explicit_cause: bool,
) -> None:
    authority = receipt_module.new_private_file_cleanup_receipt()
    assert authority.mark_unsubmitted_empty()
    outer = RuntimeError("fixed")
    outer.__context__ = CoturnTlsPrivateCleanupRequired(authority)
    if explicit_cause:
        outer.__cause__ = RuntimeError("unrelated cause")
    else:
        outer.__suppress_context__ = True
    assert tls_private_cleanup_authority(outer) is None
    cleanup_tls_private_authority(authority)


@pytest.mark.parametrize("chain", ["cause", "context"])
def test_public_private_authority_graph_walk_bounds_cycles(chain: str) -> None:
    outer = RuntimeError("outer")
    inner = RuntimeError("inner")
    if chain == "cause":
        outer.__cause__ = inner
        inner.__cause__ = outer
    else:
        outer.__context__ = inner
        inner.__context__ = outer
    assert tls_private_cleanup_authority(outer) is None


def test_public_private_authority_extractor_drops_hostile_and_invalid_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Hostile:
        def __getattribute__(self, _name: str) -> object:
            raise SystemExit(91)

    outer = RuntimeError("fixed")
    inner = RuntimeError("fixed")
    outer.__context__ = inner
    inner.__context__ = outer
    outer.cleanup_authority = Hostile()  # type: ignore[attr-defined]
    inner.cleanup_authority = object()  # type: ignore[attr-defined]
    assert tls_private_cleanup_authority(outer) is None
    assert tls_private_cleanup_authority(KeyboardInterrupt()) is None
    monkeypatch.setattr(
        tls_module,
        "_private_cleanup_candidate",
        lambda _error: (_ for _ in ()).throw(GeneratorExit()),
    )
    assert tls_private_cleanup_authority(RuntimeError("fixed")) is None
