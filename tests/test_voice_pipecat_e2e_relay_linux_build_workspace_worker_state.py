"""Synthetic tests for inert relay build workspace-worker state."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import sys
from copy import copy, deepcopy
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_linux_build_workspace as workspace_module
import scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state as state_module


def _bundle(tmp_path: Path):
    destination = workspace_module._new_relay_linux_build_workspace_destination(
        source_root=(tmp_path / "source").resolve(),
        run_parent=(tmp_path / "runs").resolve(),
        node=(tmp_path / "node").resolve(),
        run_id="worker-state",
    )
    owner = destination._read(destination._request)
    return owner, state_module._new_relay_linux_build_workspace_worker_bundle(owner)


def test_bundle_preowns_only_inert_cross_thread_state(tmp_path: Path) -> None:
    owner, bundle = _bundle(tmp_path)

    owner_token = owner._cleanup_authority._key
    assert bundle._matches(owner_token, owner._receipt_destination)
    assert bundle._prepared_destination is owner._receipt_destination
    assert bundle._thread_destination._read(owner_token) is None
    assert bundle._built_destination._read(owner_token) is None
    assert bundle._terminal_destination._read(owner_token) is None
    assert bundle._controller._control_value() is None
    assert bundle._controller._cancellation_requested() is False
    assert not owner._request._run_root.exists()


def test_cross_owner_destination_read_is_rejected(tmp_path: Path) -> None:
    first_owner, first = _bundle(tmp_path / "first")
    second_owner, _second = _bundle(tmp_path / "second")

    assert first._matches(
        first_owner._cleanup_authority._key,
        first_owner._receipt_destination,
    )
    with pytest.raises(TypeError):
        first._thread_destination._read(second_owner._cleanup_authority._key)


def test_same_owner_always_returns_one_canonical_bundle(tmp_path: Path) -> None:
    owner, first = _bundle(tmp_path)

    second = state_module._new_relay_linux_build_workspace_worker_bundle(owner)

    assert second is first
    assert owner._worker_bundle_destination._read(owner._request) is first


def test_cross_wired_prepared_destination_cannot_be_installed(
    tmp_path: Path,
) -> None:
    first_owner, _first = _bundle(tmp_path / "first")
    second_owner, _second = _bundle(tmp_path / "second")
    fresh_destination = workspace_module._WorkspaceWorkerBundleDestination(
        workspace_module._WORKER_BUNDLE_DESTINATION_TOKEN,
        request=first_owner._request,
        owner_token=first_owner._cleanup_authority._key,
        prepared_destination=first_owner._receipt_destination,
    )
    cross_wired = state_module._WorkspaceWorkerBundle(
        state_module._BUNDLE_TOKEN,
        owner_token=first_owner._cleanup_authority._key,
        prepared_destination=second_owner._receipt_destination,
    )

    with pytest.raises(workspace_module._RelayLinuxBuildWorkspaceContractError):
        fresh_destination._publish(first_owner._request, cross_wired)
    assert fresh_destination._read(first_owner._request) is None


def test_sanitized_controller_and_terminal_slots_retain_no_owner_or_request(
    tmp_path: Path,
) -> None:
    owner, bundle = _bundle(tmp_path)

    for value in (
        bundle._controller,
        bundle._thread_destination,
        bundle._built_destination,
        bundle._terminal_destination,
    ):
        assert not hasattr(value, "_owner")
        assert not hasattr(value, "_request")
        assert value._owner_token is owner._cleanup_authority._key


@pytest.mark.parametrize(
    ("control", "kind", "code"),
    [
        (KeyboardInterrupt(), "keyboard", None),
        (SystemExit(73), "system-exit", 73),
        (SystemExit("unsafe"), "system-exit", 1),
    ],
)
def test_first_control_is_sanitized_and_requests_cancellation(
    tmp_path: Path,
    control: KeyboardInterrupt | SystemExit,
    kind: str,
    code: int | None,
) -> None:
    _owner, bundle = _bundle(tmp_path)
    controller = bundle._controller

    try:
        raise control
    except (KeyboardInterrupt, SystemExit) as captured:
        controller._capture_control(captured)

    retained = controller._control_value()
    assert retained is not None
    assert (retained.kind, retained.code) == (kind, code)
    assert controller._cancellation_requested() is True
    assert control.__traceback__ is None
    assert control.__context__ is None
    assert control.__cause__ is None


def test_first_control_wins_over_later_control(tmp_path: Path) -> None:
    _owner, bundle = _bundle(tmp_path)
    controller = bundle._controller

    controller._capture_control(SystemExit(41))
    controller._capture_control(KeyboardInterrupt())

    retained = controller._control_value()
    assert retained is not None
    assert (retained.kind, retained.code) == ("system-exit", 41)


def test_control_conversion_failure_uses_fallback_and_scrubs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _owner, bundle = _bundle(tmp_path)
    control = SystemExit(59)

    def fail_conversion(_error: object) -> object:
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(state_module, "_control_signal", fail_conversion)
    try:
        raise control
    except SystemExit as captured:
        bundle._controller._capture_control(captured)

    retained = bundle._controller._control_value()
    assert retained is not None
    assert (retained.kind, retained.code) == ("system-exit", 59)
    assert control.__traceback__ is None


def test_nested_control_during_conversion_fallback_is_retained_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _owner, bundle = _bundle(tmp_path)
    control = SystemExit(61)
    nested = KeyboardInterrupt()
    fallback_calls = 0

    def fail_conversion(_error: object) -> object:
        raise RuntimeError("conversion failed")

    original_fallback = state_module._fallback_control_signal

    def interrupt_fallback(error: KeyboardInterrupt | SystemExit):
        nonlocal fallback_calls
        fallback_calls += 1
        if fallback_calls == 1:
            raise nested
        return original_fallback(error)

    monkeypatch.setattr(state_module, "_control_signal", fail_conversion)
    monkeypatch.setattr(state_module, "_fallback_control_signal", interrupt_fallback)
    try:
        raise control
    except SystemExit as captured:
        bundle._controller._capture_control(captured)

    retained = bundle._controller._control_value()
    assert fallback_calls == 2
    assert retained is not None
    assert (retained.kind, retained.code) == ("system-exit", 61)
    assert control.__traceback__ is None
    assert nested.__traceback__ is None


def test_control_scrub_failure_is_bounded_and_minimally_scrubbed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _owner, bundle = _bundle(tmp_path)
    control = KeyboardInterrupt()

    def fail_scrub(_error: object) -> None:
        raise RuntimeError("scrub failed")

    monkeypatch.setattr(state_module, "_scrub_control", fail_scrub)
    try:
        raise control
    except KeyboardInterrupt as captured:
        bundle._controller._capture_control(captured)

    retained = bundle._controller._control_value()
    assert retained is not None and retained.kind == "keyboard"
    assert control.__traceback__ is None


def test_all_values_are_falsey_immutable_noncopyable_and_nonserializable(
    tmp_path: Path,
) -> None:
    _owner, bundle = _bundle(tmp_path)
    controller = bundle._controller
    controller._capture_control(KeyboardInterrupt())
    control = controller._control_value()
    assert control is not None
    values = (
        bundle,
        controller,
        control,
        bundle._thread_destination,
        bundle._built_destination,
        bundle._terminal_destination,
    )

    for value in values:
        assert not value
        with pytest.raises(TypeError):
            copy(value)
        with pytest.raises(TypeError):
            deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(AttributeError):
            value._owner = object()  # type: ignore[attr-defined]


def test_checkpoint_exposes_no_worker_effect_or_public_surface() -> None:
    assert state_module.__all__ == []
    for name in (
        "start",
        "join",
        "prepare",
        "publish_prepared",
        "publish_built",
        "publish_terminal",
        "cleanup",
    ):
        assert not hasattr(state_module, name)
