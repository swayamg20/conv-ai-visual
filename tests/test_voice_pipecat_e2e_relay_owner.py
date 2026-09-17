"""Synthetic exact-order tests for the executable relay B0 aggregate."""
# ruff: noqa: E402

from __future__ import annotations

import copy
import os
import pickle
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_relay_owner_cleanup as cleanup_module
import scripts.voice_pipecat_e2e_relay_owner_forward as forward_module
import scripts.voice_pipecat_e2e_relay_owner_state as state_module
import scripts.voice_pipecat_e2e_relay_owner_terminal as terminal_module
from scripts.voice_pipecat_e2e_coturn_docker import validate_image_inspection
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    ContainerPlan,
    establish_container_cleanup_authority,
    validate_container_for_start,
    validate_container_running,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    establish_network_cleanup_authority,
    validate_network_for_container,
)
from scripts.voice_pipecat_e2e_coturn_evidence import CoturnProbeSummary
from scripts.voice_pipecat_e2e_coturn_host import RuntimeIdentity
from scripts.voice_pipecat_e2e_coturn_runtime import (
    ContainerAbsenceReceipt,
    DockerPrerequisites,
    NetworkAbsenceReceipt,
    OwnedContainer,
    OwnedNetwork,
)
from scripts.voice_pipecat_e2e_coturn_runtime_lifecycle import StoppedCoturnReceipt
from scripts.voice_pipecat_e2e_coturn_runtime_process import CleanCoturnExitReceipt
from scripts.voice_pipecat_e2e_coturn_runtime_tls import RuntimeTlsMaterial
from scripts.voice_pipecat_e2e_coturn_tls_readiness import OpenSslReadinessReceipt
from scripts.voice_pipecat_e2e_relay_browser_result import (
    RelayBrowserObservation,
    RelayBrowserResultOwner,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationOwner,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_prebootstrap import RelayPrebootstrapReceipt
from scripts.voice_pipecat_e2e_relay_invocation_values import RelayPlaywrightExitReceipt
from scripts.voice_pipecat_e2e_relay_owner import (
    RelayProbeCleanupAuthority,
    RelayProbeObservation,
    cleanup_relay_probe,
    new_relay_probe_owner,
    new_relay_probe_owner_destination,
    run_relay_probe,
)
from scripts.voice_pipecat_e2e_relay_owner_state import _SLOT_NAMES
from tests.test_voice_pipecat_e2e_coturn_docker import image_inspection
from tests.test_voice_pipecat_e2e_coturn_docker_container import (
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_docker_network import (
    NETWORK_ID,
    NONCE,
    TOPOLOGY,
    network_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools
from tests.test_voice_pipecat_e2e_relay_probe import _source

NOW = 100.0
DEADLINE = 110.0
USERNAME = "1786982460:123e4567-e89b-42d3-a456-426614174000"
SECRET = "synthetic-static-auth-secret-0123456789"
SCRUB_CUTS = tuple(
    (label, position)
    for label in (
        *(f"slot:{name}" for name in _SLOT_NAMES),
        *(f"root:{name}" for name in terminal_module._TERMINAL_ROOT_NAMES),
    )
    for position in ("before", "after")
)


class _Runner:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def run(self, _request: object) -> object:
        raise AssertionError("synthetic owner test executes no command")

    def start_attached(self, _request: object) -> object:
        raise AssertionError("synthetic owner test starts no command")

    def settle_owned(self) -> bool:
        self.events.append("settle-runner")
        return True


class _BridgeProbe:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def ipv4_routes(self) -> tuple[object, ...]:
        self.events.append("routes")
        return ()

    def interface_ipv4(self, _interface: str) -> object:
        raise AssertionError("create-network is synthetic")


class _AdoptionDestination:
    def __init__(self) -> None:
        self.published = False

    def publish(self, value: object) -> None:
        assert value is True
        self.published = True


def _object(kind: type[object], **values: object) -> object:
    result = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _summary() -> CoturnProbeSummary:
    return _object(
        CoturnProbeSummary,
        _grammar_verified=False,
        _allocation_count=1,
        _observed_categories=frozenset(),
        _unknown_info_records=0,
        _grammar_violation_records=0,
        _total_records=1,
    )  # type: ignore[return-value]


def _browser_observation() -> RelayBrowserObservation:
    values = {
        "result_schema_attested": True,
        "hidden_call_attested": True,
        "relay_candidate_attested": True,
        "browser_cleanup_attested": True,
        "terminal_cleanup_attested": True,
        "safe_report_attested": True,
        "artifacts_deleted": True,
        "qualification_verified": False,
    }
    return _object(RelayBrowserObservation, **values)  # type: ignore[return-value]


def _absence(kind: type[object], plan: object) -> object:
    names = (
        {"_container_id": CONTAINER_ID, "_plan": plan}
        if kind is ContainerAbsenceReceipt
        else {"_network_id": NETWORK_ID, "_plan": plan}
    )
    return _object(kind, **names, _finalized=False, _lock=threading.Lock())


def _install_synthetic_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    image = validate_image_inspection(image_inspection())
    prerequisites = _object(
        DockerPrerequisites,
        _image=image,
        _network_inventory=object(),
    )
    invocation = _object(RelayInvocationOwner)
    prebootstrap = _object(RelayPrebootstrapReceipt)
    playwright_exit = _object(RelayPlaywrightExitReceipt)
    artifact_owner = _object(RelayBrowserResultOwner)
    readiness = _object(
        OpenSslReadinessReceipt,
        _protocol="TLSv1.3",
        _cipher_suite="TLS_AES_256_GCM_SHA384",
    )

    def prepare(**_kwargs: object) -> object:
        events.append("prepare")
        return prerequisites

    def generate(**kwargs: object) -> None:
        assert kwargs["static_auth_secret"] == SECRET
        material = kwargs["material"]
        object.__setattr__(material, "_state", "generated")
        events.append("generate-tls")

    def create_network(**kwargs: object) -> OwnedNetwork:
        plan = kwargs["plan"]
        inspection = network_inspection(plan)
        authority = establish_network_cleanup_authority(
            plan=plan,
            network_id=NETWORK_ID,
            inspection=inspection,
        )
        events.append("create-network")
        return OwnedNetwork(
            authority=authority,
            validated=validate_network_for_container(authority, inspection),
        )

    def create_container(**kwargs: object) -> OwnedContainer:
        plan = kwargs["plan"]
        inspection = container_inspection(plan)
        authority = establish_container_cleanup_authority(
            plan=plan,
            container_id=CONTAINER_ID,
            inspection=inspection,
        )
        events.append("create-container")
        return OwnedContainer(
            authority=authority,
            validated=validate_container_for_start(authority, inspection),
        )

    def bind(material: RuntimeTlsMaterial, authority: object) -> None:
        object.__setattr__(material, "_container_id", authority.container_id)  # type: ignore[attr-defined]
        object.__setattr__(material, "_state", "bound")
        events.append("bind-tls")

    def adopt(_invocation: object, sink: object) -> None:
        events.append("adopt-username")
        destination = _AdoptionDestination()
        sink._accept_relay_turn_username(USERNAME, destination)  # type: ignore[attr-defined]
        assert destination.published

    def validate_running(**kwargs: object) -> object:
        authority = kwargs["authority"]
        events.append("validate-running")
        return validate_container_running(
            authority,
            container_inspection(authority.plan, running=True),
        )

    def cleanup_tls(material: RuntimeTlsMaterial, **_kwargs: object) -> None:
        object.__setattr__(material, "_state", "cleaned")
        events.append("cleanup-tls")

    monkeypatch.setattr(forward_module, "prepare_docker_prerequisites", prepare)
    monkeypatch.setattr(forward_module, "select_bridge_topology", lambda **_kwargs: TOPOLOGY)
    monkeypatch.setattr(forward_module, "generate_runtime_tls_material", generate)
    monkeypatch.setattr(
        forward_module,
        "authorize_relay_backend",
        lambda *_args, **_kwargs: events.append("authorize-backend"),
    )
    monkeypatch.setattr(
        forward_module,
        "_new_relay_invocation_owner",
        lambda *_args, **_kwargs: (events.append("new-invocation"), invocation)[1],
    )
    monkeypatch.setattr(
        forward_module,
        "stage_relay_backend",
        lambda _owner: (events.append("stage-backend"), prebootstrap)[1],
    )
    monkeypatch.setattr(forward_module, "create_owned_network", create_network)
    monkeypatch.setattr(forward_module, "create_owned_container", create_container)
    monkeypatch.setattr(forward_module, "bind_runtime_tls_material_to_container", bind)
    monkeypatch.setattr(forward_module, "_adopt_expected_turn_username", adopt)
    monkeypatch.setattr(
        forward_module,
        "start_owned_container_attached",
        lambda **_kwargs: events.append("start-coturn"),
    )
    monkeypatch.setattr(
        forward_module,
        "start_attached_coturn_evidence_drain",
        lambda _drain: events.append("start-drain"),
    )
    monkeypatch.setattr(forward_module, "validate_owned_container_running", validate_running)
    monkeypatch.setattr(
        forward_module,
        "execute_openssl_readiness",
        lambda **_kwargs: (events.append("openssl-ready"), readiness)[1],
    )
    monkeypatch.setattr(
        forward_module,
        "authorize_relay_browser",
        lambda *_args, **_kwargs: events.append("authorize-browser"),
    )
    monkeypatch.setattr(
        forward_module,
        "new_relay_browser_result_owner",
        lambda _run: (events.append("prepare-artifacts"), artifact_owner)[1],
    )
    monkeypatch.setattr(
        forward_module,
        "stage_relay_web",
        lambda _owner: events.append("stage-web"),
    )
    monkeypatch.setattr(
        forward_module,
        "start_relay_playwright",
        lambda _owner: events.append("start-browser"),
    )
    monkeypatch.setattr(
        forward_module,
        "finish_relay_playwright",
        lambda *_args, **_kwargs: (events.append("finish-browser"), playwright_exit)[1],
    )
    monkeypatch.setattr(
        forward_module,
        "consume_relay_browser_result",
        lambda *_args: (events.append("consume-artifacts"), _browser_observation())[1],
    )

    monkeypatch.setattr(
        cleanup_module,
        "cleanup_relay_invocation",
        lambda _owner: events.append("cleanup-invocation"),
    )
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_pump",
        lambda _process, retained: retained,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_recover_canonical_drain",
        lambda _process, _pump, retained, **_kwargs: retained,
    )
    monkeypatch.setattr(
        cleanup_module,
        "stop_owned_container",
        lambda **_kwargs: (events.append("stop-container"), _object(StoppedCoturnReceipt))[1],
    )
    monkeypatch.setattr(
        cleanup_module,
        "finish_attached_coturn_evidence_drain",
        lambda _drain: (events.append("finish-drain"), _summary())[1],
    )
    monkeypatch.setattr(
        cleanup_module,
        "confirm_attached_coturn_clean_exit",
        lambda _process: (events.append("clean-exit"), _object(CleanCoturnExitReceipt))[1],
    )

    def remove_container(**kwargs: object) -> object:
        events.append("remove-container")
        assert kwargs["stopped"] is not None
        plan = stopped_plan[0]
        return _absence(ContainerAbsenceReceipt, plan)

    stopped_plan: list[ContainerPlan] = []
    original_create_container = forward_module.create_owned_container

    def create_container_and_retain(**kwargs: object) -> OwnedContainer:
        stopped_plan.append(kwargs["plan"])  # type: ignore[arg-type]
        return original_create_container(**kwargs)

    monkeypatch.setattr(forward_module, "create_owned_container", create_container_and_retain)
    monkeypatch.setattr(cleanup_module, "remove_stopped_owned_container", remove_container)
    monkeypatch.setattr(cleanup_module, "cleanup_runtime_tls_material", cleanup_tls)

    def finalize_container(receipt: object) -> None:
        object.__setattr__(receipt, "_finalized", True)
        events.append("finalize-container")

    monkeypatch.setattr(cleanup_module, "finalize_container_absence", finalize_container)

    def remove_network(**kwargs: object) -> object:
        events.append("remove-network")
        return _absence(NetworkAbsenceReceipt, kwargs["authority"].plan)

    monkeypatch.setattr(cleanup_module, "cleanup_owned_network", remove_network)

    def finalize_network(receipt: object) -> None:
        object.__setattr__(receipt, "_finalized", True)
        events.append("finalize-network")

    monkeypatch.setattr(cleanup_module, "finalize_network_absence", finalize_network)
    monkeypatch.setattr(
        state_module,
        "revalidate_relay_probe_source",
        lambda _run: events.append("revalidate-source"),
    )


def _owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
):
    return _owner_and_destination(tmp_path, monkeypatch, events)[0]


def _owner_and_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
):
    paths = _paths(tmp_path)
    identity = RuntimeIdentity.create(run_id=paths.contract.run_id, owner_nonce=NONCE)
    destination = new_relay_probe_owner_destination()
    owner = new_relay_probe_owner(
        destination=destination,
        paths=paths,
        identity=identity,
        source=_source(monkeypatch),
        runner=_Runner(events),
        bridge_probe=_BridgeProbe(events),
        tools=_tools(),
        invocation_driver=_object(RelayInvocationDriver),  # type: ignore[arg-type]
        invocation_tools=_object(RelayInvocationTools),  # type: ignore[arg-type]
        absolute_deadline=DEADLINE,
        clock=lambda: NOW,
        wait=lambda _seconds: None,
    )
    return owner, destination


def test_exact_forward_reverse_order_and_falsey_terminal_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _install_synthetic_lifecycle(monkeypatch, events)
    owner, destination = _owner_and_destination(tmp_path, monkeypatch, events)
    observation = run_relay_probe(
        owner,
        static_auth_secret=SECRET,
        now=__import__("datetime").datetime(2026, 8, 19),
        browser_timeout_seconds=5.0,
    )
    assert events == [
        "prepare",
        "routes",
        "generate-tls",
        "authorize-backend",
        "new-invocation",
        "stage-backend",
        "create-network",
        "create-container",
        "bind-tls",
        "adopt-username",
        "start-coturn",
        "start-drain",
        "validate-running",
        "openssl-ready",
        "authorize-browser",
        "prepare-artifacts",
        "stage-web",
        "start-browser",
        "finish-browser",
        "consume-artifacts",
        "cleanup-invocation",
        "stop-container",
        "finish-drain",
        "clean-exit",
        "remove-container",
        "cleanup-tls",
        "finalize-container",
        "remove-network",
        "finalize-network",
        "settle-runner",
        "revalidate-source",
    ]
    assert type(observation) is RelayProbeObservation
    assert observation.status == "probe-observed"
    assert observation.cleanup_complete is True
    assert observation.artifacts_deleted is True
    assert observation.source_revalidated is True
    assert observation.coturn_grammar_verified is False
    assert observation.qualification_verified is False
    assert not observation
    assert not owner
    assert "probe-observed" in repr(observation)
    assert SECRET not in repr(observation)
    assert os.fspath(tmp_path) not in repr(observation)
    with pytest.raises(TypeError):
        copy.copy(observation)
    with pytest.raises(TypeError):
        pickle.dumps(observation)
    cleanup_relay_probe(owner)
    assert destination._record is None


@pytest.mark.parametrize("mode", ["publish", "cleanup-only"])
@pytest.mark.parametrize("control_kind", ["keyboard", "system-exit"])
@pytest.mark.parametrize(("label", "position"), SCRUB_CUTS)
def test_every_terminal_scrub_boundary_retains_one_retryable_opaque_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    control_kind: str,
    label: str,
    position: str,
) -> None:
    events: list[str] = []
    if mode == "publish":
        _install_synthetic_lifecycle(monkeypatch, events)
    owner, destination = _owner_and_destination(tmp_path, monkeypatch, events)
    fired = 0

    def cut(observed_label: str, observed_position: str) -> None:
        nonlocal fired
        if fired < 2 and (observed_label, observed_position) == (label, position):
            fired += 1
            if control_kind == "keyboard":
                raise KeyboardInterrupt("terminal-scrub-raw-sentinel")
            raise SystemExit(73)

    monkeypatch.setattr(terminal_module, "_terminal_scrub_hook", cut)
    with pytest.raises((KeyboardInterrupt, SystemExit)) as captured:
        if mode == "publish":
            run_relay_probe(
                owner,
                static_auth_secret=SECRET,
                now=__import__("datetime").datetime(2026, 8, 19),
                browser_timeout_seconds=5.0,
            )
        else:
            cleanup_relay_probe(owner)
    assert fired == 2
    if control_kind == "keyboard":
        assert type(captured.value) is KeyboardInterrupt
        assert str(captured.value) == ""
    else:
        assert type(captured.value) is SystemExit
        assert captured.value.code == 73
    authority = captured.value.cleanup_authority  # type: ignore[attr-defined]
    assert type(authority) is RelayProbeCleanupAuthority
    assert not authority
    assert "terminal-scrub-raw-sentinel" not in repr(authority)
    assert os.fspath(tmp_path) not in repr(authority)
    monkeypatch.setattr(terminal_module, "_terminal_scrub_hook", lambda *_args: None)
    cleanup_relay_probe(authority)
    assert owner._state == "cleaned"
    assert owner._terminal_roots_empty()
    assert owner._terminal_transition.phase == "scrubbed"
    assert owner._terminal_transition.run is None
    assert owner._observation is None
    assert destination._record is None
    cleanup_relay_probe(authority)
