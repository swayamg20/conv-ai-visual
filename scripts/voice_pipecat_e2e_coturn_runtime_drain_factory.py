"""Canonical factory for one process-and-pump evidence drain."""

from __future__ import annotations

import time
from collections.abc import Callable

from scripts import voice_pipecat_e2e_coturn_runtime_drain_recovery as _recovery
from scripts import voice_pipecat_e2e_coturn_runtime_drain_registry as _registry
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import AttachedCoturnEvidencePump
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)


def new_attached_coturn_evidence_drain(
    *,
    process: AttachedCoturnProcess,
    pump: AttachedCoturnEvidencePump,
    absolute_deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> object:
    from scripts.voice_pipecat_e2e_coturn_runtime_drain import (
        _DRAIN_TOKEN,
        AttachedCoturnEvidenceDrain,
    )

    drain: AttachedCoturnEvidenceDrain | None = None
    return_key: object | None = None
    recovered: object | None = None
    control: ControlSignal | None = None
    claimed = False
    failed = False
    if (
        type(process) is AttachedCoturnProcess
        and type(pump) is AttachedCoturnEvidencePump
        and type(absolute_deadline) is float
    ):
        return_key, control = _recovery.recover_claimed_drain(
            pump,
            process,
            absolute_deadline,
            clock,
        )
    if control is None and return_key is None:
        try:
            if not _recovery.valid_drain_input(
                process,
                pump,
                absolute_deadline,
                clock,
            ):
                raise CoturnRuntimeError("Coturn evidence drain input is invalid")
            drain = AttachedCoturnEvidenceDrain(
                _DRAIN_TOKEN,
                process=process,
                pump=pump,
                absolute_deadline=absolute_deadline,
                clock=clock,
            )
            return_key = object()
            claimed = pump._claim_drain(
                process,
                drain._owner_token,
                drain,
                absolute_deadline,
                clock,
                return_key,
            )
            failed = not claimed
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            failed = True
        if not claimed and drain is not None and type(pump) is AttachedCoturnEvidencePump:
            recovered, recovery_control = _recovery.recover_claimed_drain(
                pump,
                process,
                absolute_deadline,
                clock,
            )
            return_key = recovered
            control = control or recovery_control
            if return_key is not None:
                failed = False
    process = pump = clock = None  # type: ignore[assignment]
    if control is not None:
        drain = recovered = None
        raise_control(control)
    if failed or return_key is None:
        drain = recovered = return_key = None
        raise CoturnRuntimeError("Coturn evidence drain input is invalid") from None
    drain = recovered = None
    try:
        return _registry.return_canonical_drain(return_key)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    return_key = None
    if control is not None:
        raise_control(control)
    raise CoturnRuntimeError("Coturn evidence drain input is invalid") from None
