"""Bounded first-control-preserving consume reconciliation."""

from __future__ import annotations

from collections.abc import Callable

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _RelayLinuxExecutorError,
)

_FAILURE = "Relay Linux executor built consumption is invalid"
_RECONCILE_ATTEMPTS = 4


def _reconcile(
    action: Callable[[], bool],
    failures: list[BaseException | None],
) -> bool:
    for _attempt in range(_RECONCILE_ATTEMPTS):
        try:
            if action():
                return True
        except BaseException as error:
            _retain_consume_failure(failures, error)
    _retain_consume_failure(failures, _RelayLinuxExecutorError(_FAILURE))
    return False


def _retain_consume_failure(
    failures: list[BaseException | None],
    error: BaseException,
) -> None:
    control = isinstance(error, (KeyboardInterrupt, SystemExit))
    index = 0 if control else 1
    if failures[index] is None:
        failures[index] = error
    else:
        _scrub_control_minimal(error)


def _raise_consume_failure(failures: list[BaseException | None]) -> None:
    if failures[0] is not None:
        raise failures[0]
    if failures[1] is not None:
        error = failures[1]
        if type(error) is _RelayLinuxExecutorError:
            raise error
        _scrub_control_minimal(error)
        raise _RelayLinuxExecutorError(_FAILURE) from None


__all__: list[str] = []
