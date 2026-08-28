# ruff: noqa: E402

from __future__ import annotations

import copy
import math
import pickle
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_invocation as facade
import scripts.voice_pipecat_e2e_relay_invocation_process_values as process_values
import scripts.voice_pipecat_e2e_relay_invocation_values as values_module


def _child_request(deadline: float | None) -> object:
    return values_module.RelayChildRequest(
        values_module._REQUEST_TOKEN,
        role="app",
        command=("/private/python", "-m", "app"),
        cwd=Path("/private/source"),
        environment={"PRIVATE_TOKEN": "secret"},
        completion="ready",
        absolute_deadline=deadline,
    )


def test_concrete_selection_is_one_inert_unforgeable_singleton() -> None:
    first = process_values._concrete_invocation_selection()
    second = process_values._concrete_invocation_selection()

    assert first is second
    assert process_values._is_concrete_invocation_selection(first)
    assert not process_values._is_concrete_invocation_selection(
        object.__new__(process_values._RelayConcreteInvocationSelection)
    )
    assert not first
    assert repr(first) == "RelayConcreteInvocationSelection()"
    with pytest.raises(TypeError):
        vars(first)
    assert process_values.__all__ == []

    with pytest.raises(TypeError, match="factory-owned"):
        process_values._RelayConcreteInvocationSelection(object())
    with pytest.raises(AttributeError):
        first._forged = True
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(first)


def test_child_start_deadline_is_optional_exact_and_scrubbed() -> None:
    synthetic = _child_request(None)
    concrete = _child_request(120.0)

    assert synthetic.absolute_deadline is None
    assert concrete.absolute_deadline == 120.0
    assert "120" not in repr(concrete)
    concrete._scrub()
    assert concrete.absolute_deadline is None

    for invalid in (True, 120, 0.0, -1.0, math.inf, math.nan):
        with pytest.raises(TypeError, match="factory-owned"):
            _child_request(invalid)  # type: ignore[arg-type]


def test_stop_request_is_private_opaque_and_pair_bound() -> None:
    pair_key = object()
    request = values_module.RelayStopRequest(
        values_module._STOP_TOKEN,
        pair_key=pair_key,
        absolute_deadline=130.0,
    )

    assert request.absolute_deadline == 130.0
    assert request._matches(pair_key)
    assert not request._matches(object())
    assert not request
    assert repr(request) == "RelayStopRequest()"
    assert "RelayStopRequest" not in facade.__all__
    assert not hasattr(facade, "RelayStopRequest")

    with pytest.raises(AttributeError):
        request._absolute_deadline = 999.0
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(request)


@pytest.mark.parametrize(
    ("pair_key", "deadline"),
    [
        (None, 130.0),
        (object(), True),
        (object(), 130),
        (object(), 0.0),
        (object(), -1.0),
        (object(), math.inf),
        (object(), math.nan),
    ],
)
def test_stop_request_rejects_forged_values(pair_key: object, deadline: object) -> None:
    with pytest.raises(TypeError, match="factory-owned"):
        values_module.RelayStopRequest(
            values_module._STOP_TOKEN,
            pair_key=pair_key,
            absolute_deadline=deadline,
        )
