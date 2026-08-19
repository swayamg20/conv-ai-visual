"""Synthetic same-run browser projection tests for the relay probe."""
# ruff: noqa: E402

from __future__ import annotations

import pickle
import sys
import threading
import uuid
from copy import copy, deepcopy
from dataclasses import replace
from inspect import getsourcefile, getsourcelines
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.voice_pipecat_e2e_coturn_runtime_tls as runtime_tls_module
import scripts.voice_pipecat_e2e_relay_probe as relay_probe_module
from scripts.voice_pipecat_e2e_coturn_docker_container import (
    establish_container_cleanup_authority,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (
    CoturnRuntimeTlsCleanupRequired,
    bind_runtime_tls_material_to_container,
    cleanup_unpublished_runtime_tls_material,
    create_runtime_readiness_budget,
    execute_openssl_readiness,
    generate_runtime_tls_material,
    new_runtime_tls_material,
    validate_owned_container_running,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    cleanup_tls_material_generation_slot,
    validate_openssl_readiness_result,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeError,
    RelayProbeRun,
    RelayProbeSource,
    authorize_relay_backend,
    authorize_relay_browser,
    capture_relay_probe_source,
    new_relay_probe_run,
    replacement_relay_backend_environment,
    replacement_relay_playwright_environment,
    replacement_relay_web_environment,
    revalidate_relay_probe_source,
)
from scripts.voice_pipecat_e2e_stack import build_environment, build_web_environment
from tests.coturn_traceback_helpers import traceback_contains
from tests.test_voice_pipecat_e2e_coturn_docker_container import (
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_host import QueueRunner, _paths, _result, _tools
from tests.test_voice_pipecat_e2e_coturn_runtime import _container_plan
from tests.test_voice_pipecat_e2e_coturn_runtime_lifecycle import LifecycleRunner, _json
from tests.test_voice_pipecat_e2e_coturn_tls import (
    CERTIFICATE,
    NOW,
    PRIVATE_KEY,
    SECRET,
    TOPOLOGY,
    _readiness_transcript,
    _tls_results,
)

SOURCE_SHA = "a" * 40
PLAYWRIGHT_ENVIRONMENT_NAMES = {
    "CI",
    "NO_COLOR",
    "NO_PROXY",
    "VOICE_E2E_API_URL",
    "VOICE_E2E_ARTIFACT_DIR",
    "VOICE_E2E_BROWSER_AUDIO_FIXTURE",
    "VOICE_E2E_CALL_ID",
    "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4",
    "VOICE_E2E_COTURN_SPKI_SHA256_B64",
    "VOICE_E2E_NETWORK",
    "VOICE_E2E_RESULT_PATH",
    "VOICE_E2E_WEB_URL",
    "no_proxy",
}
POISONED_AMBIENT_NAMES = (
    "PW_TEST_REPORTER",
    "PW_TEST_SOURCE_TRANSFORM",
    "PLAYWRIGHT_BROWSERS_PATH",
    "NODE_OPTIONS",
    "NODE_PATH",
    "DEBUG",
    "DEBUG_FILE",
    "TS_NODE_PROJECT",
    "BABEL_ENV",
    "SWC_BINARY_PATH",
    "NYC_CONFIG",
    "C8_CONFIG",
    "HTTP_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "NODE_EXTRA_CA_CERTS",
    "SSLKEYLOGFILE",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "NPM_CONFIG_USERCONFIG",
    "npm_config_prefix",
    "VOICE_E2E_UNRECOGNIZED",
    "NEXT_UNRECOGNIZED",
    "MURMUR_UNRECOGNIZED",
)


def _source_line(function: object, marker: str) -> int:
    lines, first = getsourcelines(function)
    matches = [first + index for index, line in enumerate(lines) if marker in line]
    assert getsourcefile(function) == relay_probe_module.__file__
    assert len(matches) == 1
    return matches[0]


def _source(monkeypatch: pytest.MonkeyPatch) -> RelayProbeSource:
    monkeypatch.setattr(
        relay_probe_module,
        "_read_source_provenance",
        lambda: {
            "commit_sha": SOURCE_SHA,
            "repository_clean": True,
            "dirty_state_refused": True,
        },
    )
    return capture_relay_probe_source()


def _ready_runtime(tmp_path: Path):
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    generate_runtime_tls_material(
        material=material,
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    plan = _container_plan(paths)
    created = container_inspection(plan)
    authority = establish_container_cleanup_authority(
        plan=plan,
        container_id=CONTAINER_ID,
        inspection=created,
    )
    bind_runtime_tls_material_to_container(material, authority)
    budget = create_runtime_readiness_budget(
        absolute_deadline=110.0,
        clock=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    running_result = container_inspection(plan, running=True)
    running = validate_owned_container_running(
        runner=LifecycleRunner([_json(running_result)]),
        tools=_tools(),
        authority=authority,
        readiness_budget=budget,
    )
    readiness_result = _result(stderr=_readiness_transcript("TLSv1.3", "TLS_AES_256_GCM_SHA384"))
    readiness = execute_openssl_readiness(
        runner=LifecycleRunner([readiness_result]),
        tools=_tools(),
        running=running,
        tls_material=material,
        readiness_budget=budget,
    )
    return paths, material, running, readiness, readiness_result


@pytest.fixture
def ready_runtime(tmp_path: Path):
    retained = _ready_runtime(tmp_path)
    yield retained
    material = retained[1]
    if material._slot.has_material:
        cleanup_tls_material_generation_slot(material._slot)


def test_backend_projection_is_available_before_container_start_or_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    generate_runtime_tls_material(
        material=material,
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    try:
        run = new_relay_probe_run(
            runtime_paths=paths,
            source=_source(monkeypatch),
        )
        authorize_relay_backend(run, tls_material=material)
        backend = replacement_relay_backend_environment(run)
        web = replacement_relay_web_environment(run)
        assert backend["MURMUR_PIPECAT_E2E_NETWORK"] == "relay-tls"
        assert backend["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"]
        assert backend == build_environment(
            run._stack_paths,
            {},
            network="relay-tls",
            turn_configuration_file=paths.contract.config,
            turn_tls_ca_file=paths.contract.cert,
            expected_relay_call_id=backend["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"],
        )
        assert web["VOICE_E2E_NETWORK"] == "relay-tls"
        assert web["VOICE_E2E_CALL_ID"] == backend["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"]
        with pytest.raises(RelayProbeError, match=r"browser environment is unavailable$"):
            replacement_relay_playwright_environment(run)
    finally:
        cleanup_tls_material_generation_slot(material._slot)


@pytest.mark.parametrize("poison_value", ["", "poisoned-value"])
def test_same_run_readiness_projects_only_the_relay_browser_values(
    ready_runtime: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
    poison_value: str,
) -> None:
    paths, material, running, readiness, _result_value = ready_runtime
    for name in POISONED_AMBIENT_NAMES:
        monkeypatch.setenv(name, poison_value)
    monkeypatch.setenv("VOICE_E2E_NETWORK", "direct")
    run = new_relay_probe_run(
        runtime_paths=paths,
        source=_source(monkeypatch),
    )
    authorize_relay_backend(run, tls_material=material)
    authorize_relay_browser(
        run,
        running=running,
        tls_material=material,
        readiness=readiness,
    )

    backend = replacement_relay_backend_environment(run)
    web = replacement_relay_web_environment(run)
    browser = replacement_relay_playwright_environment(run)
    expected_call_id = backend["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"]
    parsed = uuid.UUID(expected_call_id)
    assert parsed.version == 4
    assert str(parsed) == expected_call_id
    assert backend["MURMUR_PIPECAT_E2E_NETWORK"] == "relay-tls"
    assert backend["MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE"] == str(paths.contract.config)
    assert backend["SSL_CERT_FILE"] == str(paths.contract.cert)
    expected_web = build_web_environment(run._stack_paths, {})
    expected_web.update(
        {
            "VOICE_E2E_CALL_ID": expected_call_id,
            "VOICE_E2E_NETWORK": "relay-tls",
        }
    )
    for playwright_only in (
        "VOICE_E2E_ARTIFACT_DIR",
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE",
        "VOICE_E2E_RESULT_PATH",
    ):
        expected_web.pop(playwright_only)
    assert web == expected_web
    assert browser == {
        "CI": "1",
        "NO_COLOR": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "VOICE_E2E_API_URL": expected_web["VOICE_E2E_API_URL"],
        "VOICE_E2E_ARTIFACT_DIR": str(run._stack_paths.playwright_dir),
        "VOICE_E2E_BROWSER_AUDIO_FIXTURE": build_web_environment(run._stack_paths, {})[
            "VOICE_E2E_BROWSER_AUDIO_FIXTURE"
        ],
        "VOICE_E2E_CALL_ID": expected_call_id,
        "VOICE_E2E_COTURN_BRIDGE_GATEWAY_IPV4": str(TOPOLOGY.gateway),
        "VOICE_E2E_COTURN_SPKI_SHA256_B64": material.chromium_spki_sha256_b64,
        "VOICE_E2E_NETWORK": "relay-tls",
        "VOICE_E2E_RESULT_PATH": str(run._stack_paths.browser_result),
        "VOICE_E2E_WEB_URL": expected_web["VOICE_E2E_WEB_URL"],
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    assert browser.keys() == PLAYWRIGHT_ENVIRONMENT_NAMES
    assert not set(POISONED_AMBIENT_NAMES) & browser.keys()
    assert not {"MURMUR_PIPECAT_E2E_COTURN_CONFIG_FILE", "SSL_CERT_FILE"} & browser.keys()
    assert SECRET not in repr(run)
    assert str(paths.contract.config) not in repr(run)

    browser["VOICE_E2E_NETWORK"] = "direct"
    assert replacement_relay_playwright_environment(run)["VOICE_E2E_NETWORK"] == "relay-tls"


def test_loose_or_cross_run_readiness_cannot_authorize_browser(
    ready_runtime: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, material, running, readiness, readiness_result = ready_runtime
    run = new_relay_probe_run(
        runtime_paths=paths,
        source=_source(monkeypatch),
    )
    authorize_relay_backend(run, tls_material=material)
    unrelated = validate_openssl_readiness_result(readiness_result)
    assert unrelated is not readiness
    with pytest.raises(RelayProbeError, match=r"^Relay probe browser authorization failed$"):
        authorize_relay_browser(
            run,
            running=running,
            tls_material=material,
            readiness=unrelated,
        )
    with pytest.raises(RelayProbeError, match=r"browser environment is unavailable$"):
        replacement_relay_playwright_environment(run)


def test_tls_material_can_be_adopted_by_only_one_probe_run(
    ready_runtime: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, material, _running, _readiness, _readiness_result = ready_runtime
    source = _source(monkeypatch)
    first = new_relay_probe_run(runtime_paths=paths, source=source)
    second = new_relay_probe_run(runtime_paths=paths, source=source)
    authorize_relay_backend(first, tls_material=material)

    with pytest.raises(
        RelayProbeError,
        match=r"^Relay probe backend authorization failed$",
    ):
        authorize_relay_backend(second, tls_material=material)
    assert (
        replacement_relay_backend_environment(first)["MURMUR_PIPECAT_E2E_EXPECTED_CALL_ID"]
        != second._call_id
    )


def test_backend_control_after_material_claim_is_retryable_from_run(
    ready_runtime: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, material, _running, _readiness, _readiness_result = ready_runtime
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    original = relay_probe_module.build_environment

    def interrupt(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise SystemExit(23)

    monkeypatch.setattr(relay_probe_module, "build_environment", interrupt)
    with pytest.raises(SystemExit) as captured:
        authorize_relay_backend(run, tls_material=material)
    assert captured.value.code == 23
    assert captured.value.__context__ is None
    assert run._state == "backend-authorizing"
    assert run._tls_material is material
    assert material._matches_probe_owner(run._owner_token)

    monkeypatch.setattr(relay_probe_module, "build_environment", original)
    authorize_relay_backend(run, tls_material=material)
    assert replacement_relay_backend_environment(run)["MURMUR_PIPECAT_E2E_NETWORK"] == "relay-tls"


def test_backend_claim_cut_retains_only_the_first_material(
    ready_runtime: tuple[object, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, material, _running, _readiness, _readiness_result = ready_runtime
    alien_root = tmp_path / "alien"
    alien_root.mkdir()
    alien_retained = _ready_runtime(alien_root)
    alien = alien_retained[1]
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    target = _source_line(
        RelayProbeRun._claim_backend_material,
        'self._state = "backend-authorizing"',
    )
    previous = sys.gettrace()
    fired = False

    def trace(frame: object, event: str, _arg: object):
        nonlocal fired
        if (
            not fired
            and getattr(frame, "f_code", None) is RelayProbeRun._claim_backend_material.__code__
            and event == "line"
            and getattr(frame, "f_lineno", None) == target
        ):
            fired = True
            raise SystemExit(23)
        return trace

    try:
        sys.settrace(trace)
        with pytest.raises(SystemExit) as error:
            authorize_relay_backend(run, tls_material=material)
    finally:
        sys.settrace(previous)
    try:
        assert error.value.code == 23
        assert error.value.__context__ is None
        assert fired
        assert run._state == "created"
        assert run._tls_material is material
        with pytest.raises(
            RelayProbeError,
            match=r"^Relay probe backend authorization failed$",
        ):
            run._claim_backend_material(alien)
        authorize_relay_backend(run, tls_material=material)
        assert run._state == "backend-ready"
        assert run._tls_material is material
        assert replacement_relay_backend_environment(run)["MURMUR_PIPECAT_E2E_NETWORK"] == (
            "relay-tls"
        )
    finally:
        if alien._slot.has_material:
            cleanup_tls_material_generation_slot(alien._slot)


def test_cross_run_runtime_authority_cannot_be_spliced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _ready_runtime(first_root)
    second = _ready_runtime(second_root)
    first_paths, first_material, _first_running, first_readiness, _ = first
    _second_paths, second_material, second_running, second_readiness, _ = second
    try:
        run = new_relay_probe_run(
            runtime_paths=first_paths,
            source=_source(monkeypatch),
        )
        authorize_relay_backend(run, tls_material=first_material)
        for material, readiness in (
            (first_material, first_readiness),
            (second_material, second_readiness),
        ):
            with pytest.raises(
                RelayProbeError,
                match=r"^Relay probe browser authorization failed$",
            ):
                authorize_relay_browser(
                    run,
                    running=second_running,
                    tls_material=material,
                    readiness=readiness,
                )
    finally:
        for material in (first_material, second_material):
            if material._slot.has_material:
                cleanup_tls_material_generation_slot(material._slot)


def test_tls_cleanup_invalidates_every_projection(
    ready_runtime: tuple[object, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, material, running, readiness, _readiness_result = ready_runtime
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    authorize_relay_backend(run, tls_material=material)
    authorize_relay_browser(
        run,
        running=running,
        tls_material=material,
        readiness=readiness,
    )
    cleanup_tls_material_generation_slot(material._slot)
    assert revalidate_relay_probe_source(run) is None

    for project in (
        replacement_relay_backend_environment,
        replacement_relay_web_environment,
        replacement_relay_playwright_environment,
    ):
        with pytest.raises(RelayProbeError, match=r"environment is unavailable$"):
            project(run)


@pytest.mark.parametrize("failure_kind", ["ordinary", "control"])
def test_failed_tls_cleanup_irreversibly_revokes_probe_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    generate_runtime_tls_material(
        material=material,
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    authorize_relay_backend(run, tls_material=material)
    original = runtime_tls_module.cleanup_tls_material_generation_slot

    def fail_cleanup(_slot: object) -> None:
        if failure_kind == "control":
            raise SystemExit(23)
        raise RuntimeError("raw-cleanup-failure")

    monkeypatch.setattr(
        runtime_tls_module,
        "cleanup_tls_material_generation_slot",
        fail_cleanup,
    )
    try:
        if failure_kind == "control":
            with pytest.raises(SystemExit) as captured:
                cleanup_unpublished_runtime_tls_material(material)
            assert captured.value.code == 23
            assert captured.value.__context__ is None
            assert captured.value.cleanup_authority is material  # type: ignore[attr-defined]
        else:
            with pytest.raises(CoturnRuntimeTlsCleanupRequired) as captured:
                cleanup_unpublished_runtime_tls_material(material)
            assert captured.value.cleanup_authority is material
            assert captured.value.__context__ is None
        assert material._state == "generated"
        assert material._slot.has_material
        assert not material._matches_generated(paths, TOPOLOGY)
        assert not material._matches_probe_owner(run._owner_token)
        for project in (
            replacement_relay_backend_environment,
            replacement_relay_web_environment,
            replacement_relay_playwright_environment,
        ):
            with pytest.raises(RelayProbeError, match=r"environment is unavailable$"):
                project(run)
        with pytest.raises(
            RelayProbeError,
            match=r"^Relay probe backend authorization failed$",
        ):
            authorize_relay_backend(run, tls_material=material)
    finally:
        monkeypatch.setattr(
            runtime_tls_module,
            "cleanup_tls_material_generation_slot",
            original,
        )
        cleanup_unpublished_runtime_tls_material(material)
    assert material.cleanup_complete


def test_concurrent_tls_cleanup_calls_the_underlying_owner_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
    generate_runtime_tls_material(
        material=material,
        runner=QueueRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
        tools=_tools(),
        paths=paths,
        topology=TOPOLOGY,
        static_auth_secret=SECRET,
        now=NOW,
    )
    run = new_relay_probe_run(runtime_paths=paths, source=_source(monkeypatch))
    authorize_relay_backend(run, tls_material=material)
    original = runtime_tls_module.cleanup_tls_material_generation_slot
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    outcomes: list[BaseException | None] = []

    def blocked_cleanup(slot: object) -> None:
        nonlocal calls
        calls += 1
        original(slot)  # type: ignore[arg-type]
        entered.set()
        assert release.wait(2.0)

    def cleanup() -> None:
        try:
            cleanup_unpublished_runtime_tls_material(material)
        except BaseException as error:
            outcomes.append(error)
        else:
            outcomes.append(None)

    monkeypatch.setattr(
        runtime_tls_module,
        "cleanup_tls_material_generation_slot",
        blocked_cleanup,
    )
    first = threading.Thread(target=cleanup)
    second = threading.Thread(target=cleanup)
    first.start()
    assert entered.wait(2.0)
    second.start()
    try:
        assert second.is_alive()
    finally:
        release.set()
        first.join(2.0)
        second.join(2.0)
    assert not first.is_alive()
    assert not second.is_alive()
    assert outcomes == [None, None]
    assert calls == 1
    assert material.cleanup_complete
    with pytest.raises(RelayProbeError, match=r"backend environment is unavailable$"):
        replacement_relay_backend_environment(run)


def test_probe_run_cannot_be_copied_or_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = new_relay_probe_run(
        runtime_paths=_paths(tmp_path),
        source=_source(monkeypatch),
    )
    for clone in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match=r"cannot be (?:copied|serialized)$"):
            clone(run)


def test_runtime_tls_owner_cannot_be_copied_or_serialized(
    ready_runtime: tuple[object, ...],
) -> None:
    material = ready_runtime[1]
    for clone in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match=r"cannot be (?:copied|serialized)$"):
            clone(material)


def test_probe_source_is_clean_factory_owned_and_raw_graph_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"relay-probe-source-sentinel"

    def fail() -> object:
        raise RuntimeError(raw.decode("ascii"))

    monkeypatch.setattr(relay_probe_module, "_read_source_provenance", fail)
    with pytest.raises(
        RelayProbeError,
        match=r"^Relay probe source is unavailable$",
    ) as error:
        capture_relay_probe_source()
    assert not traceback_contains(error.value, raw)
    with pytest.raises(TypeError, match="factory-owned"):
        RelayProbeSource(object(), commit_sha=SOURCE_SHA)


def test_probe_source_cannot_be_replaced_copied_mutated_or_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(monkeypatch)
    assert source.commit_sha == SOURCE_SHA
    with pytest.raises(TypeError, match=r"dataclass instance"):
        replace(source, commit_sha="b" * 40)
    for clone in (copy, deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match=r"cannot be (?:copied|serialized)$"):
            clone(source)
    with pytest.raises(AttributeError, match=r"immutable$"):
        source._commit_sha = "b" * 40
    assert source.commit_sha == SOURCE_SHA


def test_probe_source_revalidation_requires_same_clean_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(monkeypatch)
    run = new_relay_probe_run(runtime_paths=_paths(tmp_path), source=source)

    assert revalidate_relay_probe_source(run) is None

    for observed in (
        {
            "commit_sha": "b" * 40,
            "repository_clean": True,
            "dirty_state_refused": True,
        },
        {
            "commit_sha": SOURCE_SHA,
            "repository_clean": False,
            "dirty_state_refused": True,
        },
        {"commit_sha": SOURCE_SHA},
    ):
        monkeypatch.setattr(
            relay_probe_module,
            "_read_source_provenance",
            lambda observed=observed: observed,
        )
        with pytest.raises(
            RelayProbeError,
            match=r"^Relay probe source revalidation failed$",
        ) as error:
            revalidate_relay_probe_source(run)
        assert error.value.__cause__ is None
        assert error.value.__context__ is None
        assert not traceback_contains(error.value, SOURCE_SHA, str(tmp_path))


def test_probe_source_revalidation_scrubs_reader_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = new_relay_probe_run(
        runtime_paths=_paths(tmp_path),
        source=_source(monkeypatch),
    )

    def fail() -> object:
        raise RuntimeError(f"source-reader-secret-{tmp_path}")

    monkeypatch.setattr(relay_probe_module, "_read_source_provenance", fail)
    with pytest.raises(
        RelayProbeError,
        match=r"^Relay probe source revalidation failed$",
    ) as error:
        revalidate_relay_probe_source(run)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not traceback_contains(error.value, "source-reader-secret", str(tmp_path))


def test_probe_source_revalidation_rejects_hostile_sha_equality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = new_relay_probe_run(
        runtime_paths=_paths(tmp_path),
        source=_source(monkeypatch),
    )

    class SpoofedSha(str):
        def __eq__(self, _other: object) -> bool:
            return True

        __hash__ = str.__hash__

    monkeypatch.setattr(
        relay_probe_module,
        "_read_source_provenance",
        lambda: {
            "commit_sha": SpoofedSha("b" * 40),
            "repository_clean": True,
            "dirty_state_refused": True,
        },
    )
    with pytest.raises(
        RelayProbeError,
        match=r"^Relay probe source revalidation failed$",
    ):
        revalidate_relay_probe_source(run)

    class HostileEquality:
        def __eq__(self, _other: object) -> bool:
            raise AssertionError("hostile equality must not execute")

    monkeypatch.setattr(
        relay_probe_module,
        "_validate_source_provenance",
        lambda _value: {
            "commit_sha": HostileEquality(),
            "repository_clean": True,
            "dirty_state_refused": True,
        },
    )
    with pytest.raises(
        RelayProbeError,
        match=r"^Relay probe source revalidation failed$",
    ):
        revalidate_relay_probe_source(run)


@pytest.mark.parametrize("control", [KeyboardInterrupt("source-secret"), SystemExit(23)])
def test_probe_source_revalidation_preserves_sanitized_control(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    run = new_relay_probe_run(
        runtime_paths=_paths(tmp_path),
        source=_source(monkeypatch),
    )

    def interrupt() -> object:
        raise control

    monkeypatch.setattr(relay_probe_module, "_read_source_provenance", interrupt)
    with pytest.raises(type(control)) as error:
        revalidate_relay_probe_source(run)
    if type(control) is SystemExit:
        assert error.value.code == 23
    else:
        assert str(error.value) == ""
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not traceback_contains(
        error.value,
        "source-secret",
        SOURCE_SHA,
        str(tmp_path),
    )


def test_probe_run_and_projection_are_factory_owned_and_fixed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(TypeError, match="factory-owned"):
        RelayProbeRun(  # type: ignore[call-arg]
            object(),
            stack_paths=relay_probe_module._derive_stack_paths(paths),
            runtime_paths=paths,
            source=_source(monkeypatch),
            call_id="50000000-0000-4000-8000-000000000005",
        )
    with pytest.raises(RelayProbeError, match=r"browser environment is unavailable$"):
        replacement_relay_playwright_environment(object())  # type: ignore[arg-type]
