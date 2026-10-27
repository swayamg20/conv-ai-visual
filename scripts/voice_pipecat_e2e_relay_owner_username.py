"""Friend-only TURN username adoption into canonical pump and drain owners."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_coturn_docker_network import NetworkPlan
from scripts.voice_pipecat_e2e_coturn_runtime_drain import (
    AttachedCoturnEvidenceDrain,
    new_attached_coturn_evidence_drain,
)
from scripts.voice_pipecat_e2e_coturn_runtime_evidence import (
    AttachedCoturnEvidencePump,
    create_attached_coturn_evidence_pump,
)
from scripts.voice_pipecat_e2e_coturn_runtime_process import AttachedCoturnProcess
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
    raise_control,
)
from scripts.voice_pipecat_e2e_relay_owner_state import RelayProbeOwner, _scrub_exception
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeOwnerError

_SINK_TOKEN = object()
_RECOVERY_ATTEMPTS = 4
_FAILURE = "Relay probe username adoption failed"


class _RelayTurnUsernameSink:
    """Build both evidence owners before acknowledging username receipt."""

    __slots__ = ("_owner",)

    def __init__(self, token: object, owner: RelayProbeOwner) -> None:
        if token is not _SINK_TOKEN or type(owner) is not RelayProbeOwner:
            raise TypeError("Relay TURN username sink is factory-owned")
        self._owner: RelayProbeOwner | None = owner

    def _accept_relay_turn_username(self, username: str, destination: object) -> None:
        owner = self._owner
        process: AttachedCoturnProcess | None = None
        plan: NetworkPlan | None = None
        pump: AttachedCoturnEvidencePump | None = None
        drain: AttachedCoturnEvidenceDrain | None = None
        control: ControlSignal | None = None
        failed = False
        try:
            process, plan = _adoption_inputs(owner, username)
            pump, drain = _ensure_evidence_owners(owner, process, plan, username)
            destination.publish(True)  # type: ignore[attr-defined]
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
            _scrub_exception(error)
        except BaseException as error:
            failed = True
            _scrub_exception(error)
        if (
            type(owner) is RelayProbeOwner
            and type(process) is AttachedCoturnProcess
            and type(plan) is NetworkPlan
        ):
            for _attempt in range(_RECOVERY_ATTEMPTS):
                try:
                    pump, drain = _ensure_evidence_owners(owner, process, plan, username)
                    break
                except (KeyboardInterrupt, SystemExit) as error:
                    control = control or control_signal(error)
                    _scrub_exception(error)
                except BaseException as error:
                    failed = True
                    _scrub_exception(error)
                    continue
        if (
            type(pump) is not AttachedCoturnEvidencePump
            or type(drain) is not AttachedCoturnEvidenceDrain
        ):
            failed = True
        username = ""
        owner = process = plan = pump = drain = destination = None
        if control is not None:
            raise_control(control)
        if failed:
            raise RelayProbeOwnerError(_FAILURE) from None

    def _clear(self) -> None:
        self._owner = None


def _adoption_inputs(
    owner: object,
    username: object,
) -> tuple[AttachedCoturnProcess, NetworkPlan]:
    if type(owner) is not RelayProbeOwner or type(username) is not str or not username:
        raise RelayProbeOwnerError(_FAILURE)
    process = owner._read("process", AttachedCoturnProcess)
    plan = owner._read("network_plan", NetworkPlan)
    if type(process) is not AttachedCoturnProcess or type(plan) is not NetworkPlan:
        raise RelayProbeOwnerError(_FAILURE)
    return process, plan


def _ensure_evidence_owners(
    owner: RelayProbeOwner,
    process: AttachedCoturnProcess,
    plan: NetworkPlan,
    username: str,
) -> tuple[AttachedCoturnEvidencePump, AttachedCoturnEvidenceDrain]:
    pump = owner._read("pump", AttachedCoturnEvidencePump)
    if pump is None:
        pump = create_attached_coturn_evidence_pump(
            process=process,
            expected_username=username,
            expected_topology=plan.topology,
        )
        pump = owner._publish("pump", pump, AttachedCoturnEvidencePump)  # type: ignore[assignment]
    drain = owner._read("drain", AttachedCoturnEvidenceDrain)
    if drain is None:
        drain = new_attached_coturn_evidence_drain(
            process=process,
            pump=pump,
            absolute_deadline=owner._absolute_deadline,
            clock=owner._clock,  # type: ignore[arg-type]
        )
        drain = owner._publish("drain", drain, AttachedCoturnEvidenceDrain)  # type: ignore[assignment]
    if (
        type(pump) is not AttachedCoturnEvidencePump
        or type(drain) is not AttachedCoturnEvidenceDrain
    ):
        raise RelayProbeOwnerError(_FAILURE)
    return pump, drain


def _new_username_sink(owner: RelayProbeOwner) -> _RelayTurnUsernameSink:
    return _RelayTurnUsernameSink(_SINK_TOKEN, owner)


__all__: list[str] = []
