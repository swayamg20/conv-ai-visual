"""Canonical, process-serialized construction of one evidence pump."""

from __future__ import annotations

from scripts import voice_pipecat_e2e_coturn_runtime_process_claims as _claims
from scripts.voice_pipecat_e2e_coturn_evidence import (
    _PROBE_DESTINATION_TOKEN,
    COTURN_REALM,
    CoturnEvidenceParser,
    CoturnProbeResultSlot,
    new_coturn_probe_result_slot,
)
from scripts.voice_pipecat_e2e_coturn_evidence_destination import (
    new_probe_parser_destination,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_MAX_ATTEMPTS = 8


def create_attached_coturn_evidence_pump(
    *,
    process: AttachedCoturnProcess,
    expected_username: object,
    expected_topology: object,
    expected_realm: object = COTURN_REALM,
) -> object:
    """Recover or construct the one canonical pump admitted by a process."""

    key: object | None = None
    control: ControlSignal | None = None
    try:
        if type(process) is not AttachedCoturnProcess:
            raise CoturnRuntimeError("Coturn evidence pump input is invalid")
        with process._pump_operation_lock:
            key = _create_pump_key(
                process=process,
                expected_username=expected_username,
                expected_topology=expected_topology,
                expected_realm=expected_realm,
            )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    process = None  # type: ignore[assignment]
    expected_username = expected_topology = expected_realm = None
    if control is not None:
        key = None
        raise_control(control)
    if key is None:
        raise CoturnRuntimeError("Coturn evidence pump input is invalid") from None
    try:
        return _claims.return_canonical_pump(key)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    key = None
    if control is not None:
        raise_control(control)
    raise CoturnRuntimeError("Coturn evidence pump input is invalid") from None


def _create_pump_key(
    *,
    process: AttachedCoturnProcess,
    expected_username: object,
    expected_topology: object,
    expected_realm: object,
) -> object:

    from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
        _PUMP_TOKEN,
        AttachedCoturnEvidencePump,
    )

    fingerprint: bytes | None = None
    key: object | None = None
    owner: object | None = None
    parser: CoturnEvidenceParser | None = None
    result_slot: CoturnProbeResultSlot | None = None
    pump: AttachedCoturnEvidencePump | None = None
    control: ControlSignal | None = None
    outcome = "invalid"
    claimed = False
    if type(process) is AttachedCoturnProcess:
        owner = process._pump_owner
        operation_lock = process._pump_operation_lock
        try:
            with operation_lock:
                fingerprint = _claims.evidence_input_fingerprint(
                    process,
                    expected_username,
                    expected_topology,
                    expected_realm,
                )
                if fingerprint is None:
                    raise CoturnRuntimeError("Coturn evidence pump input is invalid")
                partial_ready, partial_control = _recover_partial_candidate(
                    process,
                    fingerprint,
                    owner,
                )
                control = control or partial_control
                if not partial_ready:
                    raise CoturnRuntimeError("Coturn evidence pump input is invalid")
                outcome, key = _claims.claim_evidence_pump(process, fingerprint, owner)
                claimed = outcome == "claimed"
                if claimed:
                    parser = new_probe_parser_destination(  # type: ignore[assignment]
                        CoturnEvidenceParser
                    )
                    pump = AttachedCoturnEvidencePump._new_destination()
                    if not _claims.retain_partial_pump(
                        process,
                        fingerprint,
                        owner,
                        key,
                        pump,
                        parser,
                    ):
                        raise CoturnRuntimeError("Coturn evidence pump input is invalid")
                    result_slot = new_coturn_probe_result_slot()
                    CoturnEvidenceParser._initialize_probe(
                        expected_username=expected_username,
                        expected_topology=expected_topology,
                        expected_realm=expected_realm,
                        result_slot=result_slot,
                        _destination=parser,
                        _destination_token=_PROBE_DESTINATION_TOKEN,
                    )
                    AttachedCoturnEvidencePump.__init__(
                        pump,
                        _PUMP_TOKEN,
                        process=process,
                        parser=parser,
                        result_slot=result_slot,
                        claim_owner=owner,
                    )
                    if not _claims.publish_evidence_pump(
                        process,
                        fingerprint,
                        owner,
                        key,
                        pump,
                    ):
                        raise CoturnRuntimeError("Coturn evidence pump input is invalid")
                    outcome = "published"
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            pass
        if fingerprint is not None and owner is not None:
            for _ in range(_MAX_ATTEMPTS):
                try:
                    with operation_lock:
                        recovered, recovered_key = _claims.claim_evidence_pump(
                            process,
                            fingerprint,
                            owner,
                        )
                    if recovered == "published":
                        outcome = recovered
                        key = recovered_key
                        claimed = True
                        break
                    if recovered == "claimed":
                        key = recovered_key
                        claimed = True
                        break
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    pass
        if outcome != "published" and claimed:
            status = "invalid"
            for _ in range(_MAX_ATTEMPTS):
                try:
                    status = _claims.evidence_pump_claim_status(
                        process,
                        fingerprint,
                        owner,
                        key,
                    )
                    if status in {"building", "published"}:
                        break
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                except BaseException:
                    pass
            if status == "published":
                outcome = "published"
            elif status == "building":
                retained = False
                if parser is not None:
                    for _ in range(_MAX_ATTEMPTS):
                        try:
                            if _claims.retain_partial_pump(
                                process,
                                fingerprint,
                                owner,
                                key,
                                pump,
                                parser,
                            ):
                                retained = True
                                break
                        except (KeyboardInterrupt, SystemExit) as error:
                            control = control or control_signal(error)
                        except BaseException:
                            pass
                scrubbed, scrub_control = _scrub_unpublished_pump(pump, parser)
                control = control or scrub_control
                if scrubbed:
                    release = (
                        _claims.release_scrubbed_partial_pump
                        if retained
                        else _claims.release_unpublished_pump
                    )
                    for _ in range(_MAX_ATTEMPTS):
                        try:
                            arguments = (
                                (process, fingerprint, owner) if retained else (process, owner, key)
                            )
                            if release(*arguments):
                                break
                        except (KeyboardInterrupt, SystemExit) as error:
                            control = control or control_signal(error)
                        except BaseException:
                            pass
    expected_username = expected_topology = expected_realm = None
    fingerprint = owner = pump = parser = result_slot = None
    if control is not None:
        raise_control(control)
    if outcome != "published" or key is None:
        key = None
        raise CoturnRuntimeError("Coturn evidence pump input is invalid") from None
    process = None  # type: ignore[assignment]
    return key


def _recover_partial_candidate(
    process: AttachedCoturnProcess,
    fingerprint: bytes,
    owner: object,
) -> tuple[bool, ControlSignal | None]:
    control: ControlSignal | None = None
    for _ in range(_MAX_ATTEMPTS):
        try:
            if _claims.finish_scrubbed_partial_pump(process, fingerprint, owner):
                return True, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
        except BaseException:
            pass
    partial: tuple[object, object] | None = None
    try:
        partial = _claims.retained_partial_pump(process, fingerprint, owner)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control or control_signal(error)
    except BaseException:
        pass
    if partial is None:
        return True, control
    pump, parser = partial
    scrubbed, scrub_control = _scrub_unpublished_pump(pump, parser)
    control = control or scrub_control
    if not scrubbed:
        pump = parser = None
        return False, control
    for _ in range(_MAX_ATTEMPTS):
        try:
            if _claims.release_scrubbed_partial_pump(process, fingerprint, owner):
                pump = parser = None
                return True, control
        except (KeyboardInterrupt, SystemExit) as error:
            control = control or control_signal(error)
        except BaseException:
            pass
    pump = parser = None
    return False, control


def _scrub_unpublished_pump(
    pump: object,
    parser: object,
) -> tuple[bool, ControlSignal | None]:
    from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
        AttachedCoturnEvidencePump,
        _force_scrub_parser,
    )

    if type(pump) is AttachedCoturnEvidencePump:
        with pump._lock:
            adopted = pump._parser
        if type(adopted) is CoturnEvidenceParser:
            parser = adopted
    scrubbed = parser is None
    control: ControlSignal | None = None
    if type(parser) is CoturnEvidenceParser:
        for _ in range(_MAX_ATTEMPTS):
            try:
                parser._terminalize(failed=True)
                scrubbed = True
                break
            except (KeyboardInterrupt, SystemExit) as error:
                control = control or control_signal(error)
            except BaseException:
                pass
        if not scrubbed:
            scrubbed, force_control = _force_scrub_parser(parser)
            control = control or force_control
    if scrubbed and type(pump) is AttachedCoturnEvidencePump:
        with pump._lock:
            pump._failed = True
            pump._finished = False
            pump._summary = None
            pump._process = None
            pump._claim_process = None
            pump._parser = None
            pump._result_slot = None
    adopted = parser = pump = None
    return scrubbed, control
