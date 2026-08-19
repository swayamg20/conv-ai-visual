"""Synthetic normal Coturn runtime sequencing tests; no service is started."""

from __future__ import annotations

import copy
import pickle
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import voice_pipecat_e2e_coturn_runtime as runtime_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_container_absence as container_absence_module,
)
from scripts import voice_pipecat_e2e_coturn_runtime_lifecycle as lifecycle_module  # noqa: E402
from scripts import (  # noqa: E402
    voice_pipecat_e2e_coturn_runtime_private_cleanup as private_cleanup_module,
)
from scripts import voice_pipecat_e2e_coturn_runtime_tls as runtime_tls_module  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    ContainerCleanupAuthority,
    establish_container_cleanup_authority,
    validate_container_for_start,
)
from scripts.voice_pipecat_e2e_coturn_host import (  # noqa: E402
    CommandRequest,
    CommandResult,
    CoturnRuntimePaths,
)
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    AttachedCoturnProcess,
    CoturnRuntimeError,
    CoturnRuntimePrivateCleanupRequired,
    CoturnRuntimeTlsCleanupRequired,
    RuntimePrivateCleanupAuthority,
    RuntimeTlsMaterial,
    bind_runtime_tls_material_to_container,
    cleanup_owned_container,
    cleanup_runtime_private_authority,
    cleanup_runtime_tls_material,
    confirm_attached_coturn_clean_exit,
    create_runtime_readiness_budget,
    execute_openssl_readiness,
    finalize_container_absence,
    generate_runtime_tls_material,
    new_attached_coturn_process,
    new_runtime_tls_material,
    recover_container_cleanup_authority,
    remove_stopped_owned_container,
    start_owned_container_attached,
    stop_owned_container,
    validate_owned_container_running,
)
from scripts.voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    TlsMaterialGenerationSlot,
    cleanup_tls_material_generation_slot,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker_container import (  # noqa: E402
    CONTAINER_ID,
    container_inspection,
)
from tests.test_voice_pipecat_e2e_coturn_host import (  # noqa: E402
    QueueRunner as TlsRunner,
)
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _result, _tools  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime import _container_plan  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_runtime_process import (  # noqa: E402
    FakeAttached,
    RawChunk,
    StartRunner,
    _interrupt_before_line,
    _interrupt_on_return,
    _source_line,
)
from tests.test_voice_pipecat_e2e_coturn_tls import (  # noqa: E402
    CERTIFICATE,
    NOW,
    PRIVATE_KEY,
    SECRET,
    TOPOLOGY,
    _readiness_transcript,
    _tls_results,
)


class LifecycleRunner:
    def __init__(self, values: list[object]) -> None:
        self.values = values
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, CommandResult)
        return value

    def start_attached(self, request: CommandRequest) -> object:
        raise AssertionError("lifecycle command runner never starts attached")


@dataclass
class AdvancingClock:
    now: float
    jump_to: float | None = None
    waits: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now = self.jump_to if self.jump_to is not None else self.now + seconds


@pytest.fixture
def tls_factory() -> Callable[
    [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
]:
    retained: list[TlsMaterialGenerationSlot] = []

    def create(
        root: Path,
    ) -> tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]:
        root.mkdir(parents=True, exist_ok=True)
        paths = _paths(root)
        material = new_runtime_tls_material(paths=paths, topology=TOPOLOGY)
        generate_runtime_tls_material(
            material=material,
            runner=TlsRunner([_result(PRIVATE_KEY), _result(CERTIFICATE), *_tls_results()]),
            tools=_tools(),
            paths=paths,
            topology=TOPOLOGY,
            static_auth_secret=SECRET,
            now=NOW,
        )
        slot = material._slot
        assert type(slot) is TlsMaterialGenerationSlot
        retained.append(slot)
        return paths, slot, material

    yield create

    for slot in reversed(retained):
        if slot.has_material:
            cleanup_tls_material_generation_slot(slot)


def _container(
    paths: CoturnRuntimePaths,
    attached: FakeAttached,
) -> tuple[ContainerCleanupAuthority, AttachedCoturnProcess, object, object, object]:
    authority, process, validated = _started(paths, attached)
    running_inspection = container_inspection(authority.plan, running=True)
    readiness_budget = create_runtime_readiness_budget(
        absolute_deadline=110.0,
        clock=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    running = validate_owned_container_running(
        runner=LifecycleRunner([_json(running_inspection)]),
        tools=_tools(),
        authority=authority,
        readiness_budget=readiness_budget,
    )
    return authority, process, running, validated, readiness_budget


def _started(
    paths: CoturnRuntimePaths,
    attached: FakeAttached,
) -> tuple[ContainerCleanupAuthority, AttachedCoturnProcess, object]:
    plan = _container_plan(paths)
    created = container_inspection(plan)
    authority = establish_container_cleanup_authority(
        plan=plan,
        container_id=CONTAINER_ID,
        inspection=created,
    )
    validated = validate_container_for_start(authority, created)
    process = new_attached_coturn_process(validated)
    start_owned_container_attached(
        runner=StartRunner(attached),
        tools=_tools(),
        container=validated,
        process=process,
    )
    return authority, process, validated


def _json(value: object) -> CommandResult:
    import json

    return CommandResult(0, json.dumps(value).encode("ascii"), b"")


def _stop(running: object, process: AttachedCoturnProcess, running_inspection: object):
    runner = LifecycleRunner(
        [
            _json(running_inspection),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            _json(container_inspection(process._container_authority.plan)),
        ]
    )
    stopped = stop_owned_container(
        runner=runner,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        process=process,
    )
    return stopped, runner


def _remove(
    *,
    stopped: object,
    clean_exit: object,
    stopped_inspection: object,
    absence: CommandResult | None = None,
):
    runner = LifecycleRunner(
        [
            _json(stopped_inspection),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            absence or CommandResult(0, b"", b""),
        ]
    )
    receipt = remove_stopped_owned_container(
        runner=runner,
        tools=_tools(),
        stopped=stopped,  # type: ignore[arg-type]
        clean_exit=clean_exit,  # type: ignore[arg-type]
    )
    return receipt, runner


def test_running_validation_precedes_one_exact_readiness_request(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _receipt, material = tls_factory(tmp_path)
    authority, _process, running, validated, readiness_budget = _container(paths, FakeAttached())
    bind_runtime_tls_material_to_container(material, authority)
    readiness = CommandResult(
        0,
        b"",
        _readiness_transcript("TLSv1.3", "TLS_AES_256_GCM_SHA384"),
    )
    runner = LifecycleRunner([readiness])
    result = execute_openssl_readiness(
        runner=runner,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        tls_material=material,
        readiness_budget=readiness_budget,  # type: ignore[arg-type]
    )
    assert repr(result) == "OpenSslReadinessReceipt()"
    assert len(runner.requests) == 1
    assert runner.requests[0].argv == (
        "/usr/bin/openssl",
        "s_client",
        "-connect",
        "127.0.0.1:5349",
        "-CAfile",
        str(paths.contract.cert),
        "-verify_ip",
        "127.0.0.1",
        "-verify_return_error",
        "-brief",
    )

    refused = LifecycleRunner([readiness])
    with pytest.raises(CoturnRuntimeError, match="readiness failed"):
        execute_openssl_readiness(
            runner=refused,
            tools=_tools(),
            running=validated,  # type: ignore[arg-type]
            tls_material=material,
            readiness_budget=readiness_budget,  # type: ignore[arg-type]
        )
    assert refused.requests == []
    assert authority.plan.paths == paths


def test_bad_readiness_is_fixed_scrubbed_and_executes_no_retry(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _receipt, material = tls_factory(tmp_path)
    authority, _process, running, _validated, readiness_budget = _container(paths, FakeAttached())
    bind_runtime_tls_material_to_container(material, authority)
    raw = b"traceback-sentinel-runtime-readiness\n"
    runner = LifecycleRunner([CommandResult(0, b"", raw)])
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn OpenSSL readiness failed$") as error:
        execute_openssl_readiness(
            runner=runner,
            tools=_tools(),
            running=running,  # type: ignore[arg-type]
            tls_material=material,
            readiness_budget=readiness_budget,  # type: ignore[arg-type]
        )
    assert len(runner.requests) == 1
    assert not traceback_contains(error.value, raw)


def test_connection_refused_retry_classifier_rejects_prefixed_stderr(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _slot, material = tls_factory(tmp_path)
    authority, _process, running, _validated, readiness_budget = _container(
        paths,
        FakeAttached(),
    )
    bind_runtime_tls_material_to_container(material, authority)
    raw = b"untrusted-prefix\nBIO_connect:Connection refused\nconnect:errno=111\n"
    runner = LifecycleRunner([CommandResult(1, b"", raw)])
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn OpenSSL readiness failed$") as error:
        execute_openssl_readiness(
            runner=runner,
            tools=_tools(),
            running=running,  # type: ignore[arg-type]
            tls_material=material,
            readiness_budget=readiness_budget,  # type: ignore[arg-type]
        )
    assert len(runner.requests) == 1
    assert not traceback_contains(error.value, raw)


def test_running_proof_publication_cut_is_idempotently_recoverable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    authority, _process, _validated = _started(paths, FakeAttached())
    budget = create_runtime_readiness_budget(
        absolute_deadline=110.0,
        clock=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    runner = LifecycleRunner([_json(container_inspection(authority.plan, running=True))])
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=type(budget)._container_ready.__code__,
            operation=lambda: validate_owned_container_running(
                runner=runner,
                tools=_tools(),
                authority=authority,
                readiness_budget=budget,
            ),
        )
    assert str(error.value) == ""
    retry = LifecycleRunner([])
    running = validate_owned_container_running(
        runner=retry,
        tools=_tools(),
        authority=authority,
        readiness_budget=budget,
    )
    assert repr(running) == "ValidatedRunningContainer()"
    assert retry.requests == []


def test_running_proof_state_cut_replays_from_budget_without_reinspection(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, _process, _validated = _started(paths, FakeAttached())
    budget = create_runtime_readiness_budget(
        absolute_deadline=110.0,
        clock=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    runner = LifecycleRunner([_json(container_inspection(authority.plan, running=True))])
    transition_line = _source_line(
        type(budget)._container_ready,
        'self._state = "openssl"',
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=type(budget)._container_ready.__code__,
            line_number=transition_line,
            operation=lambda: validate_owned_container_running(
                runner=runner,
                tools=_tools(),
                authority=authority,
                readiness_budget=budget,
            ),
        )
    assert str(error.value) == ""
    retry = LifecycleRunner([])
    running = validate_owned_container_running(
        runner=retry,
        tools=_tools(),
        authority=authority,
        readiness_budget=budget,
    )
    assert repr(running) == "ValidatedRunningContainer()"
    assert retry.requests == []


def test_openssl_proof_publication_cut_is_idempotently_recoverable(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _slot, material = tls_factory(tmp_path)
    authority, _process, running, _validated, budget = _container(paths, FakeAttached())
    bind_runtime_tls_material_to_container(material, authority)
    result = CommandResult(
        0,
        b"",
        _readiness_transcript("TLSv1.3", "TLS_AES_256_GCM_SHA384"),
    )
    runner = LifecycleRunner([result])
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=type(budget)._openssl_ready.__code__,
            operation=lambda: execute_openssl_readiness(
                runner=runner,
                tools=_tools(),
                running=running,  # type: ignore[arg-type]
                tls_material=material,
                readiness_budget=budget,  # type: ignore[arg-type]
            ),
        )
    assert str(error.value) == ""
    retry = LifecycleRunner([])
    receipt = execute_openssl_readiness(
        runner=retry,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        tls_material=material,
        readiness_budget=budget,  # type: ignore[arg-type]
    )
    assert repr(receipt) == "OpenSslReadinessReceipt()"
    assert retry.requests == []


def test_openssl_proof_state_cut_replays_from_budget_without_second_command(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _slot, material = tls_factory(tmp_path)
    authority, _process, running, _validated, budget = _container(paths, FakeAttached())
    bind_runtime_tls_material_to_container(material, authority)
    runner = LifecycleRunner(
        [
            CommandResult(
                0,
                b"",
                _readiness_transcript("TLSv1.3", "TLS_AES_256_GCM_SHA384"),
            )
        ]
    )
    transition_line = _source_line(
        type(budget)._openssl_ready,
        'self._state = "complete"',
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_before_line(
            target_code=type(budget)._openssl_ready.__code__,
            line_number=transition_line,
            operation=lambda: execute_openssl_readiness(
                runner=runner,
                tools=_tools(),
                running=running,  # type: ignore[arg-type]
                tls_material=material,
                readiness_budget=budget,  # type: ignore[arg-type]
            ),
        )
    assert str(error.value) == ""
    retry = LifecycleRunner([])
    receipt = execute_openssl_readiness(
        runner=retry,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        tls_material=material,
        readiness_budget=budget,  # type: ignore[arg-type]
    )
    assert repr(receipt) == "OpenSslReadinessReceipt()"
    assert retry.requests == []


def test_one_shared_deadline_retries_created_then_connection_refused_in_order(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _receipt, material = tls_factory(tmp_path)
    authority, _process, _validated = _started(paths, FakeAttached())
    bind_runtime_tls_material_to_container(material, authority)
    clock = AdvancingClock(100.0)
    budget = create_runtime_readiness_budget(
        absolute_deadline=100.5,
        clock=clock,
        wait=clock.wait,
    )
    created = container_inspection(authority.plan)
    running_inspection = container_inspection(authority.plan, running=True)
    container_runner = LifecycleRunner([_json(created), _json(created), _json(running_inspection)])
    running = validate_owned_container_running(
        runner=container_runner,
        tools=_tools(),
        authority=authority,
        readiness_budget=budget,
    )
    refused = b"BIO_connect:Connection refused\nconnect:errno=111\n"
    openssl_runner = LifecycleRunner(
        [
            CommandResult(1, b"", refused),
            CommandResult(
                0,
                b"",
                _readiness_transcript("TLSv1.3", "TLS_AES_256_GCM_SHA384"),
            ),
        ]
    )
    result = execute_openssl_readiness(
        runner=openssl_runner,
        tools=_tools(),
        running=running,
        tls_material=material,
        readiness_budget=budget,
    )
    assert repr(result) == "OpenSslReadinessReceipt()"
    assert [request.argv[5:7] for request in container_runner.requests] == [
        ("container", "inspect"),
        ("container", "inspect"),
        ("container", "inspect"),
    ]
    assert len(openssl_runner.requests) == 2
    assert clock.waits == [0.05, 0.05, 0.05]
    assert container_runner.requests[0].timeout_seconds == pytest.approx(0.5)
    assert openssl_runner.requests[-1].timeout_seconds == pytest.approx(0.35)


def test_readiness_deadline_and_attempt_limit_are_bounded(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    authority, _process, _validated = _started(paths, FakeAttached())
    created = _json(container_inspection(authority.plan))
    deadline_clock = AdvancingClock(200.0, jump_to=200.2)
    deadline_budget = create_runtime_readiness_budget(
        absolute_deadline=200.2,
        clock=deadline_clock,
        wait=deadline_clock.wait,
    )
    deadline_runner = LifecycleRunner([created, created])
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn running container validation failed$",
    ):
        validate_owned_container_running(
            runner=deadline_runner,
            tools=_tools(),
            authority=authority,
            readiness_budget=deadline_budget,
        )
    assert len(deadline_runner.requests) == 1
    assert len(deadline_clock.waits) == 1

    attempt_budget = create_runtime_readiness_budget(
        absolute_deadline=310.0,
        clock=lambda: 300.0,
        wait=lambda _seconds: None,
    )
    attempt_runner = LifecycleRunner([created] * 16)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn running container validation failed$",
    ):
        validate_owned_container_running(
            runner=attempt_runner,
            tools=_tools(),
            authority=authority,
            readiness_budget=attempt_budget,
        )
    assert len(attempt_runner.requests) == 16


def test_stop_revalidates_running_target_before_command(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    tampered = copy.deepcopy(container_inspection(authority.plan, running=True))
    tampered[0]["Config"]["Labels"] = {"foreign": "true"}  # type: ignore[index]
    runner = LifecycleRunner([_json(tampered)])
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn container stop failed$"):
        stop_owned_container(
            runner=runner,
            tools=_tools(),
            running=running,  # type: ignore[arg-type]
            process=process,
        )
    assert [request.argv[5:7] for request in runner.requests] == [("container", "inspect")]


def test_stop_rejects_malformed_result_without_reflecting_raw_output(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    raw = b"traceback-sentinel-stop-result"
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan, running=True)),
            CommandResult(0, raw, b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match=r"^Coturn container stop failed$") as error:
        stop_owned_container(
            runner=runner,
            tools=_tools(),
            running=running,  # type: ignore[arg-type]
            process=process,
        )
    assert len(runner.requests) == 2
    assert not traceback_contains(error.value, raw)


@pytest.mark.parametrize(
    ("cut", "expected"),
    [
        (KeyboardInterrupt("untrusted-stop-publication-cut"), KeyboardInterrupt),
        (MemoryError("untrusted-stop-publication-cut"), CoturnRuntimeError),
    ],
)
def test_stop_commit_before_receipt_is_reconciled_without_a_second_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: BaseException,
    expected: type[BaseException],
) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    real_validate = lifecycle_module.validate_container_removal_target
    calls = 0

    def validate_then_cut(*arguments: object, **keywords: object):
        nonlocal calls
        result = real_validate(*arguments, **keywords)
        calls += 1
        if calls == 1:
            raise cut
        return result

    monkeypatch.setattr(
        lifecycle_module,
        "validate_container_removal_target",
        validate_then_cut,
    )
    first = LifecycleRunner(
        [
            _json(container_inspection(authority.plan, running=True)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            _json(container_inspection(authority.plan)),
        ]
    )
    with pytest.raises(expected) as error:
        stop_owned_container(
            runner=first,
            tools=_tools(),
            running=running,  # type: ignore[arg-type]
            process=process,
        )
    assert not traceback_contains(error.value, "untrusted-stop-publication-cut")
    assert [request.argv[5:7] for request in first.requests] == [
        ("container", "inspect"),
        ("container", "stop"),
        ("container", "inspect"),
    ]

    retry = LifecycleRunner([_json(container_inspection(authority.plan))])
    stopped = stop_owned_container(
        runner=retry,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        process=process,
    )
    assert repr(stopped) == "StoppedCoturnReceipt()"
    assert stopped is process._stop_receipt
    assert [request.argv[5:7] for request in retry.requests] == [("container", "inspect")]


def test_concurrent_stop_calls_publish_one_receipt_and_one_stop(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    values = [
        _json(container_inspection(authority.plan, running=True)),
        CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
        _json(container_inspection(authority.plan)),
    ]
    runners = [LifecycleRunner(list(values)), LifecycleRunner(list(values))]
    barrier = threading.Barrier(2)
    outcomes: list[object] = []

    def stop(runner: LifecycleRunner) -> None:
        barrier.wait()
        try:
            outcomes.append(
                stop_owned_container(
                    runner=runner,
                    tools=_tools(),
                    running=running,  # type: ignore[arg-type]
                    process=process,
                )
            )
        except BaseException as error:
            outcomes.append(error)

    threads = [threading.Thread(target=stop, args=(runner,)) for runner in runners]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(1.0)
    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert outcomes[0] is outcomes[1] is process._stop_receipt
    assert sorted(len(runner.requests) for runner in runners) == [0, 3]


def test_stop_receipt_publication_and_return_loss_recover_exact_identity(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    empty = LifecycleRunner([])
    line = _source_line(stop_owned_container, "if receipt._removal_target is None:")
    with pytest.raises(KeyboardInterrupt) as first:
        _interrupt_before_line(
            target_code=stop_owned_container.__code__,
            line_number=line,
            operation=lambda: stop_owned_container(
                runner=empty,
                tools=_tools(),
                running=running,  # type: ignore[arg-type]
                process=process,
            ),
        )
    assert str(first.value) == ""
    canonical = process._stop_receipt
    assert type(canonical) is lifecycle_module.StoppedCoturnReceipt
    assert canonical._removal_target is None
    assert empty.requests == []

    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan, running=True)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            _json(container_inspection(authority.plan)),
        ]
    )
    with pytest.raises(KeyboardInterrupt) as second:
        _interrupt_on_return(
            target_code=stop_owned_container.__code__,
            operation=lambda: stop_owned_container(
                runner=runner,
                tools=_tools(),
                running=running,  # type: ignore[arg-type]
                process=process,
            ),
        )
    assert str(second.value) == "untrusted-return-publication-cut"
    retry = LifecycleRunner([])
    recovered = stop_owned_container(
        runner=retry,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        process=process,
    )
    assert recovered is canonical
    assert retry.requests == []


def test_stop_target_publication_return_cut_recovers_same_receipt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    authority, process, running, _validated, _budget = _container(paths, FakeAttached())
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan, running=True)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            _json(container_inspection(authority.plan)),
        ]
    )
    with pytest.raises(KeyboardInterrupt) as error:
        _interrupt_on_return(
            target_code=lifecycle_module.StoppedCoturnReceipt._publish_removal_target.__code__,
            operation=lambda: stop_owned_container(
                runner=runner,
                tools=_tools(),
                running=running,  # type: ignore[arg-type]
                process=process,
            ),
        )
    assert str(error.value) == ""
    canonical = process._stop_receipt
    assert type(canonical) is lifecycle_module.StoppedCoturnReceipt
    assert type(canonical._removal_target).__name__ == "ValidatedContainerRemoval"
    retry = LifecycleRunner([])
    recovered = stop_owned_container(
        runner=retry,
        tools=_tools(),
        running=running,  # type: ignore[arg-type]
        process=process,
    )
    assert recovered is canonical
    assert retry.requests == []


def test_stop_then_terminal_drain_then_clean_exit_gates_removal(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    terminal = b"terminal-after-stop\n"
    attached = FakeAttached(
        chunks=[RawChunk("stdout", terminal)],
        returncode=0,
        drain_state=True,
    )
    authority, process, running, _validated, _budget = _container(paths, attached)
    running_inspection = container_inspection(authority.plan, running=True)
    stopped, stop_runner = _stop(running, process, running_inspection)
    assert [request.argv[5:7] for request in stop_runner.requests] == [
        ("container", "inspect"),
        ("container", "stop"),
        ("container", "inspect"),
    ]

    premature = LifecycleRunner([])
    with pytest.raises(CoturnRuntimeError, match="stopped container removal failed"):
        remove_stopped_owned_container(
            runner=premature,
            tools=_tools(),
            stopped=stopped,
            clean_exit=object(),  # type: ignore[arg-type]
        )
    assert premature.requests == []

    assert process.read_chunk(timeout_seconds=0.1) == terminal
    assert process.drained is True
    clean = confirm_attached_coturn_clean_exit(process)
    removed, remove_runner = _remove(
        stopped=stopped,
        clean_exit=clean,
        stopped_inspection=container_inspection(authority.plan),
    )
    assert repr(removed) == "ContainerAbsenceReceipt()"
    assert [request.argv[5:7] for request in remove_runner.requests] == [
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]


def test_stop_receipt_rejects_copy_and_serialization(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    for operation in (
        lambda: copy.copy(stopped),
        lambda: copy.deepcopy(stopped),
        lambda: pickle.dumps(stopped),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(process),
        stopped_inspection=container_inspection(authority.plan),
    )
    for operation in (
        lambda: copy.copy(removed),
        lambda: copy.deepcopy(removed),
        lambda: pickle.dumps(removed),
    ):
        with pytest.raises(TypeError, match="cannot be"):
            operation()
    finalize_container_absence(removed)


def test_same_stop_receipt_serializes_removal_and_caches_exact_outcome(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    entered = threading.Event()
    release = threading.Event()

    class BlockingRunner(LifecycleRunner):
        def run(self, request: CommandRequest) -> CommandResult:
            if not self.requests:
                entered.set()
                assert release.wait(1.0)
            return super().run(request)

    first = BlockingRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    second = LifecycleRunner([])
    outcomes: list[object] = []

    def remove(runner: LifecycleRunner) -> None:
        try:
            outcomes.append(
                remove_stopped_owned_container(
                    runner=runner,
                    tools=_tools(),
                    stopped=stopped,
                    clean_exit=clean,
                )
            )
        except BaseException as error:
            outcomes.append(error)

    first_thread = threading.Thread(target=lambda: remove(first))
    second_thread = threading.Thread(target=lambda: remove(second))
    first_thread.start()
    assert entered.wait(1.0)
    second_thread.start()
    release.set()
    first_thread.join(1.0)
    second_thread.join(1.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert len(outcomes) == 2
    assert type(outcomes[0]) is type(outcomes[1])
    assert repr(outcomes[0]) == "ContainerAbsenceReceipt()"
    assert outcomes[0] is outcomes[1]
    assert [request.argv[5:7] for request in first.requests] == [
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]
    assert second.requests == []
    cached = remove_stopped_owned_container(
        runner=LifecycleRunner([]),
        tools=_tools(),
        stopped=stopped,
        clean_exit=clean,
    )
    assert cached is outcomes[0]
    finalize_container_absence(cached)


def test_clean_exit_from_an_earlier_attached_run_cannot_remove_a_later_run(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, first, running, validated, _budget = _container(
        paths,
        FakeAttached(returncode=0, drain_state=True),
    )
    old_exit = confirm_attached_coturn_clean_exit(first)
    second = new_attached_coturn_process(validated)  # type: ignore[arg-type]
    start_owned_container_attached(
        runner=StartRunner(FakeAttached(returncode=0, drain_state=True)),
        tools=_tools(),
        container=validated,  # type: ignore[arg-type]
        process=second,
    )
    stopped, _runner = _stop(
        running,
        second,
        container_inspection(authority.plan, running=True),
    )
    refused = LifecycleRunner([])
    with pytest.raises(CoturnRuntimeError, match="stopped container removal failed"):
        remove_stopped_owned_container(
            runner=refused,
            tools=_tools(),
            stopped=stopped,
            clean_exit=old_exit,
        )
    assert refused.requests == []

    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(second),
        stopped_inspection=container_inspection(authority.plan),
    )
    assert repr(removed) == "ContainerAbsenceReceipt()"


@pytest.mark.parametrize(
    ("cut", "expected"),
    [
        (SystemExit(81), SystemExit),
        (MemoryError("untrusted-removal-unlink-cut"), CoturnRuntimeError),
    ],
)
def test_absence_finalizer_retries_after_private_receipt_unlink_cut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: BaseException,
    expected: type[BaseException],
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    for path in (paths.cidfile, paths.container_receipt):
        path.write_text("private-receipt\n", encoding="ascii")
        path.chmod(0o600)
    real_unlink = Path.unlink
    interrupted = False

    def unlink_then_cut(path: Path, *arguments: object, **keywords: object) -> None:
        nonlocal interrupted
        real_unlink(path, *arguments, **keywords)
        if path == paths.cidfile and not interrupted:
            interrupted = True
            raise cut

    monkeypatch.setattr(Path, "unlink", unlink_then_cut)
    first = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    removed = remove_stopped_owned_container(
        runner=first,
        tools=_tools(),
        stopped=stopped,
        clean_exit=clean,
    )
    with pytest.raises(expected) as error:
        finalize_container_absence(removed)
    assert not traceback_contains(error.value, "untrusted-removal-unlink-cut")
    assert [request.argv[5:7] for request in first.requests] == [
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]

    finalize_container_absence(removed)
    assert removed.finalization_complete
    assert not paths.cidfile.exists()
    assert not paths.container_receipt.exists()
    assert not paths.container_absence_receipt.exists()


def test_container_absence_marker_private_failure_retains_retry_and_exact_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    failure = MemoryError("untrusted-container-marker-private")
    private = object()
    original_write = container_absence_module.write_owned_file_exclusive
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: private if error is failure else None,
    )
    monkeypatch.setattr(
        container_absence_module,
        "write_owned_file_exclusive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    first = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(CoturnRuntimePrivateCleanupRequired) as caught:
        remove_stopped_owned_container(
            runner=first,
            tools=_tools(),
            stopped=stopped,
            clean_exit=clean,
        )
    cleanup_authority = caught.value.cleanup_authority
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    assert not traceback_contains(caught.value, *failure.args)
    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        lambda _candidate: None,
    )
    cleanup_runtime_private_authority(cleanup_authority)

    monkeypatch.setattr(
        container_absence_module,
        "write_owned_file_exclusive",
        original_write,
    )
    retry = LifecycleRunner(
        [
            CommandResult(1, b"", b"untrusted-already-removed"),
            CommandResult(0, b"", b""),
        ]
    )
    removed = remove_stopped_owned_container(
        runner=retry,
        tools=_tools(),
        stopped=stopped,
        clean_exit=clean,
    )
    assert [request.argv[5:7] for request in retry.requests] == [
        ("container", "inspect"),
        ("container", "ls"),
    ]
    assert paths.container_absence_receipt.exists()
    finalize_container_absence(removed)


def test_container_marker_write_without_directory_sync_publishes_no_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    original_sync = container_absence_module.sync_owned_directory
    calls = 0

    def fail_first(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("untrusted-container-marker-directory-sync")
        original_sync(path)

    monkeypatch.setattr(container_absence_module, "sync_owned_directory", fail_first)
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn stopped container removal failed$",
    ) as caught:
        remove_stopped_owned_container(
            runner=runner,
            tools=_tools(),
            stopped=stopped,
            clean_exit=clean,
        )
    assert paths.container_absence_receipt.exists()
    assert not traceback_contains(caught.value, "untrusted-container-marker-directory-sync")

    recovered = recover_container_cleanup_authority(
        runner=LifecycleRunner([CommandResult(0, b"", b"")]),
        tools=_tools(),
        plan=authority.plan,
    )
    assert repr(recovered) == "ContainerAbsenceReceipt()"
    finalize_container_absence(recovered)  # type: ignore[arg-type]


@pytest.mark.parametrize("failed_sync", [1, 2])
def test_container_absence_finalizer_sync_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_sync: int,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(process),
        stopped_inspection=container_inspection(authority.plan),
    )
    for path in (paths.cidfile, paths.container_receipt):
        path.write_text("private-receipt\n", encoding="ascii")
        path.chmod(0o600)
    original_sync = container_absence_module.sync_owned_directory
    calls = 0

    def fail_selected(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_sync:
            raise RuntimeError("untrusted-container-finalizer-sync")
        original_sync(path)

    monkeypatch.setattr(container_absence_module, "sync_owned_directory", fail_selected)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn container absence finalization failed$",
    ) as caught:
        finalize_container_absence(removed)
    assert not removed.finalization_complete
    assert not traceback_contains(caught.value, "untrusted-container-finalizer-sync")
    assert paths.container_absence_receipt.exists() is (failed_sync == 1)

    finalize_container_absence(removed)
    assert removed.finalization_complete
    assert not paths.cidfile.exists()
    assert not paths.container_receipt.exists()
    assert not paths.container_absence_receipt.exists()


def test_container_absence_finalizer_preserves_private_read_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(process),
        stopped_inspection=container_inspection(authority.plan),
    )
    failure = MemoryError("untrusted-container-finalizer-private")
    private = object()
    original_read = container_absence_module._read_container_absence_marker
    monkeypatch.setattr(
        private_cleanup_module,
        "tls_private_cleanup_authority",
        lambda error: private if error is failure else None,
    )
    monkeypatch.setattr(
        container_absence_module,
        "_read_container_absence_marker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(CoturnRuntimePrivateCleanupRequired) as caught:
        finalize_container_absence(removed)
    cleanup_authority = caught.value.cleanup_authority
    assert type(cleanup_authority) is RuntimePrivateCleanupAuthority
    assert not removed.finalization_complete
    assert paths.container_absence_receipt.exists()
    assert not traceback_contains(caught.value, *failure.args)
    monkeypatch.setattr(
        private_cleanup_module,
        "cleanup_tls_private_authority",
        lambda _candidate: None,
    )
    cleanup_runtime_private_authority(cleanup_authority)
    monkeypatch.setattr(
        container_absence_module,
        "_read_container_absence_marker",
        original_read,
    )
    finalize_container_absence(removed)
    assert removed.finalization_complete


@pytest.mark.parametrize(
    ("operation", "cut", "expected"),
    [
        (
            ("container", "rm"),
            KeyboardInterrupt("untrusted-container-rm-return-cut"),
            KeyboardInterrupt,
        ),
        (
            ("container", "ls"),
            MemoryError("untrusted-container-absence-return-cut"),
            CoturnRuntimeError,
        ),
    ],
)
def test_remove_and_absence_return_cuts_retry_without_second_removal(
    tmp_path: Path,
    operation: tuple[str, str],
    cut: BaseException,
    expected: type[BaseException],
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)

    class CommitCutRunner(LifecycleRunner):
        fired = False

        def run(self, request: CommandRequest) -> CommandResult:
            result = super().run(request)
            if request.argv[5:7] == operation and not self.fired:
                self.fired = True
                raise cut
            return result

    values = [
        _json(container_inspection(authority.plan)),
        CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
    ]
    if operation == ("container", "ls"):
        values.append(CommandResult(0, b"", b""))
    first = CommitCutRunner(values)
    with pytest.raises(expected) as caught:
        remove_stopped_owned_container(
            runner=first,
            tools=_tools(),
            stopped=stopped,
            clean_exit=clean,
        )
    if type(cut) is KeyboardInterrupt:
        assert str(caught.value) == ""
    assert not traceback_contains(caught.value, *cut.args)

    retry = LifecycleRunner(
        [
            CommandResult(1, b"", b"untrusted-already-removed"),
            CommandResult(0, b"", b""),
        ]
    )
    removed = remove_stopped_owned_container(
        runner=retry,
        tools=_tools(),
        stopped=stopped,
        clean_exit=clean,
    )
    assert repr(removed) == "ContainerAbsenceReceipt()"
    assert [request.argv[5:7] for request in retry.requests] == [
        ("container", "inspect"),
        ("container", "ls"),
    ]


@pytest.mark.parametrize("boundary", ["persist", "public-remove"])
def test_container_absence_receipt_return_cut_recovers_from_durable_marker(
    tmp_path: Path,
    boundary: str,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, b"", b""),
        ]
    )
    target_code = (
        container_absence_module._persist_container_absence.__code__
        if boundary == "persist"
        else remove_stopped_owned_container.__code__
    )
    with pytest.raises(KeyboardInterrupt):
        _interrupt_on_return(
            target_code=target_code,
            operation=lambda: remove_stopped_owned_container(
                runner=runner,
                tools=_tools(),
                stopped=stopped,
                clean_exit=clean,
            ),
        )
    assert paths.container_absence_receipt.exists()
    assert [request.argv[5:7] for request in runner.requests] == [
        ("container", "inspect"),
        ("container", "rm"),
        ("container", "ls"),
    ]

    restart = LifecycleRunner([CommandResult(0, b"", b"")])
    recovered = recover_container_cleanup_authority(
        runner=restart,
        tools=_tools(),
        plan=authority.plan,
    )
    assert repr(recovered) == "ContainerAbsenceReceipt()"
    assert [request.argv[5:7] for request in restart.requests] == [("container", "ls")]
    finalize_container_absence(recovered)  # type: ignore[arg-type]


def test_remove_rejects_malformed_result_before_absence_and_scrubs_output(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    raw = b"traceback-sentinel-remove-result"
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, raw, b""),
        ]
    )
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn stopped container removal failed$",
    ) as error:
        remove_stopped_owned_container(
            runner=runner,
            tools=_tools(),
            stopped=stopped,
            clean_exit=confirm_attached_coturn_clean_exit(process),
        )
    assert len(runner.requests) == 2
    assert not traceback_contains(error.value, raw)


def test_tampered_stopped_target_is_refused_before_remove(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    tampered = copy.deepcopy(container_inspection(authority.plan))
    tampered[0]["Name"] = "foreign-container"  # type: ignore[index]
    runner = LifecycleRunner([_json(tampered)])
    with pytest.raises(CoturnRuntimeError, match="stopped container removal failed"):
        remove_stopped_owned_container(
            runner=runner,
            tools=_tools(),
            stopped=stopped,
            clean_exit=clean,
        )
    assert [request.argv[5:7] for request in runner.requests] == [("container", "inspect")]


def test_absence_failure_retains_tls_material(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, slot, material = tls_factory(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    bind_runtime_tls_material_to_container(material, authority)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    runner = LifecycleRunner(
        [
            _json(container_inspection(authority.plan)),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
            CommandResult(0, (CONTAINER_ID + "\n").encode(), b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match="stopped container removal failed"):
        remove_stopped_owned_container(
            runner=runner,
            tools=_tools(),
            stopped=stopped,
            clean_exit=clean,
        )
    assert slot.has_material
    assert not material.cleanup_complete
    assert paths.contract.private_key.exists()


def test_failure_before_drain_uses_recovery_absence_to_release_tls(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _receipt, material = tls_factory(tmp_path)
    attached = FakeAttached(
        chunks=[RawChunk("stdout", b"terminal-output-not-drained")],
        returncode=None,
        drain_state=False,
    )
    authority, process, _running, _validated, _budget = _container(paths, attached)
    bind_runtime_tls_material_to_container(material, authority)
    process.terminate()
    assert attached.terminations == 1
    with pytest.raises(CoturnRuntimeError, match="clean exit was not proven"):
        confirm_attached_coturn_clean_exit(process)

    runtime_module._write_container_plan_receipt(authority.plan)
    paths.cidfile.write_text(CONTAINER_ID + "\n", encoding="ascii")
    paths.cidfile.chmod(0o600)
    running_inspection = container_inspection(authority.plan, running=True)
    recovery = recover_container_cleanup_authority(
        runner=LifecycleRunner([_json(running_inspection)]),
        tools=_tools(),
        plan=authority.plan,
    )
    absence = cleanup_owned_container(
        runner=LifecycleRunner(
            [
                _json(running_inspection),
                CommandResult(0, CONTAINER_ID.encode(), b""),
                _json(container_inspection(authority.plan)),
                CommandResult(0, CONTAINER_ID.encode(), b""),
                CommandResult(0, b"", b""),
            ]
        ),
        tools=_tools(),
        authority=recovery,
    )
    cleanup_runtime_tls_material(material, container_removal=absence)
    assert material.cleanup_complete
    assert not paths.contract.private_key.exists()


def test_persisted_recovery_cannot_bypass_a_live_same_process_child(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    authority, process, _running, _validated, _budget = _container(paths, FakeAttached())
    runtime_module._write_container_plan_receipt(authority.plan)
    paths.cidfile.write_text(CONTAINER_ID + "\n", encoding="ascii")
    paths.cidfile.chmod(0o600)
    inspection = container_inspection(authority.plan, running=True)
    live_runner = LifecycleRunner([_json(inspection)])
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn container recovery is unavailable$",
    ):
        recover_container_cleanup_authority(
            runner=live_runner,
            tools=_tools(),
            plan=authority.plan,
        )
    assert [request.argv[5:7] for request in live_runner.requests] == [("container", "inspect")]

    process.terminate()
    recovery = recover_container_cleanup_authority(
        runner=LifecycleRunner([_json(inspection)]),
        tools=_tools(),
        plan=authority.plan,
    )
    assert repr(recovery) == "RecoveredContainerCleanupAuthority()"


def test_confirmed_absence_allows_tls_cleanup_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _receipt, material = tls_factory(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    bind_runtime_tls_material_to_container(material, authority)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    clean = confirm_attached_coturn_clean_exit(process)
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=clean,
        stopped_inspection=container_inspection(authority.plan),
    )
    real_cleanup = runtime_tls_module.cleanup_tls_material_generation_slot
    calls = 0

    def counted(slot: TlsMaterialGenerationSlot) -> None:
        nonlocal calls
        calls += 1
        real_cleanup(slot)

    monkeypatch.setattr(
        runtime_tls_module,
        "cleanup_tls_material_generation_slot",
        counted,
    )
    cleanup_runtime_tls_material(material, container_removal=removed)
    cleanup_runtime_tls_material(material, container_removal=removed)
    assert calls == 1
    assert material.cleanup_complete
    assert not paths.contract.private_key.exists()
    assert not paths.contract.cert.exists()
    assert not paths.contract.config.exists()


def test_mismatched_removal_path_is_refused_and_tls_authority_is_retained(
    tmp_path: Path,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    first_paths, first_receipt, first_material = tls_factory(tmp_path / "first")
    first_plan = _container_plan(first_paths)
    first_inspection = container_inspection(first_plan)
    first_authority = establish_container_cleanup_authority(
        plan=first_plan,
        container_id=CONTAINER_ID,
        inspection=first_inspection,
    )
    bind_runtime_tls_material_to_container(first_material, first_authority)
    second_paths = _paths((tmp_path / "second").mkdir() or tmp_path / "second")
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(second_paths, attached)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(process),
        stopped_inspection=container_inspection(authority.plan),
    )
    with pytest.raises(CoturnRuntimeError, match="cleanup receipt is invalid"):
        cleanup_runtime_tls_material(first_material, container_removal=removed)
    assert first_receipt.has_material
    assert first_paths.contract.private_key.exists()


def test_tls_cleanup_retry_authority_is_propagated_opaque_and_not_reinvoked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_factory: Callable[
        [Path], tuple[CoturnRuntimePaths, TlsMaterialGenerationSlot, RuntimeTlsMaterial]
    ],
) -> None:
    paths, _slot, material = tls_factory(tmp_path)
    attached = FakeAttached(returncode=0, drain_state=True)
    authority, process, running, _validated, _budget = _container(paths, attached)
    bind_runtime_tls_material_to_container(material, authority)
    stopped, _runner = _stop(
        running,
        process,
        container_inspection(authority.plan, running=True),
    )
    removed, _runner = _remove(
        stopped=stopped,
        clean_exit=confirm_attached_coturn_clean_exit(process),
        stopped_inspection=container_inspection(authority.plan),
    )
    calls = 0
    raw = "traceback-sentinel-runtime-tls-cleanup"
    real_cleanup = runtime_tls_module.cleanup_tls_material_generation_slot

    def fail_once(slot: TlsMaterialGenerationSlot) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(raw)
        real_cleanup(slot)

    monkeypatch.setattr(
        runtime_tls_module,
        "cleanup_tls_material_generation_slot",
        fail_once,
    )
    with pytest.raises(CoturnRuntimeTlsCleanupRequired) as captured:
        cleanup_runtime_tls_material(material, container_removal=removed)
    assert captured.value.cleanup_authority is material
    assert raw not in repr(captured.value)
    cleanup_runtime_tls_material(material, container_removal=removed)
    assert calls == 2
    assert material.cleanup_complete
