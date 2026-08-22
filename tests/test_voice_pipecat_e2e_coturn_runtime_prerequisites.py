"""Synthetic prerequisite execution tests; no Docker command is run."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.voice_pipecat_e2e_coturn_host import CommandRequest, CommandResult  # noqa: E402
from scripts.voice_pipecat_e2e_coturn_runtime import (  # noqa: E402
    CoturnRuntimeError,
    DockerPrerequisites,
    prepare_docker_prerequisites,
    pull_and_validate_image,
)
from tests.coturn_traceback_helpers import traceback_contains  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_docker import image_inspection  # noqa: E402
from tests.test_voice_pipecat_e2e_coturn_host import _paths, _tools  # noqa: E402

FIRST_ID = "1" * 64
SECOND_ID = "2" * 64


class FloatSubclass(float):
    pass


@dataclass
class QueueRunner:
    values: list[object]
    requests: list[CommandRequest] = field(default_factory=list)

    def run(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, CommandResult)
        return value

    def start_attached(self, request: CommandRequest) -> object:
        raise AssertionError("prerequisites never start an attached process")


def _json(value: object) -> CommandResult:
    return CommandResult(0, json.dumps(value).encode("ascii"), b"")


def _daemon() -> dict[str, object]:
    return {
        "OSType": "linux",
        "Architecture": "x86_64",
        "OperatingSystem": "Debian GNU/Linux 12",
        "Driver": "overlay2",
        "SecurityOptions": ["name=seccomp,profile=default"],
    }


def _network(identifier: str, *subnets: str) -> list[dict[str, object]]:
    return [
        {
            "Id": identifier,
            "IPAM": {"Config": [{"Subnet": subnet} for subnet in subnets]},
        }
    ]


def _successful_values() -> list[CommandResult]:
    return [
        _json(_daemon()),
        CommandResult(0, b"pinned digest pulled\n", b""),
        _json(image_inspection()),
        CommandResult(0, f"{FIRST_ID}\n{SECOND_ID}\n".encode("ascii"), b""),
        _json(_network(FIRST_ID, "10.10.0.0/16")),
        _json(_network(SECOND_ID, "172.30.0.0/16", "2001:db8::/64")),
    ]


def test_prerequisites_execute_exact_order_and_return_only_sanitized_receipts(
    tmp_path: Path,
) -> None:
    runner = QueueRunner(_successful_values())
    receipt = prepare_docker_prerequisites(
        runner=runner,
        tools=_tools(),
        paths=_paths(tmp_path),
        absolute_deadline=110.0,
        clock=lambda: 100.0,
    )

    assert repr(receipt) == "DockerPrerequisites()"
    assert repr(receipt.image) == "CoturnImageReceipt()"
    assert receipt.network_inventory.ipv4_subnets == (
        "10.10.0.0/16",
        "172.30.0.0/16",
    )
    assert "pinned digest pulled" not in repr(receipt)
    assert [request.argv[5:7] for request in runner.requests] == [
        ("info", "--format"),
        ("image", "pull"),
        ("image", "inspect"),
        ("network", "ls"),
        ("network", "inspect"),
        ("network", "inspect"),
    ]
    assert runner.requests[4].argv[-1] == FIRST_ID
    assert runner.requests[5].argv[-1] == SECOND_ID
    with pytest.raises(TypeError, match="factory-owned"):
        DockerPrerequisites(  # type: ignore[call-arg]
            object(),
            image=receipt.image,
            network_inventory=receipt.network_inventory,
        )


@pytest.mark.parametrize(
    ("replace_index", "replacement", "expected_calls"),
    [
        (0, _json({"OSType": "windows"}), 1),
        (1, CommandResult(0, b"pull", b"untrusted warning"), 2),
        (2, CommandResult(0, b"traceback-sentinel-image", b""), 3),
        (3, CommandResult(0, b"short-id\n", b""), 4),
        (4, _json(_network(SECOND_ID, "10.0.0.0/8")), 5),
    ],
)
def test_prerequisite_malformed_stage_fails_closed_without_later_commands(
    tmp_path: Path,
    replace_index: int,
    replacement: CommandResult,
    expected_calls: int,
) -> None:
    values = _successful_values()
    values[replace_index] = replacement
    runner = QueueRunner(values)
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn Docker prerequisites are invalid$",
    ) as captured:
        prepare_docker_prerequisites(
            runner=runner,
            tools=_tools(),
            paths=_paths(tmp_path),
            absolute_deadline=110.0,
            clock=lambda: 100.0,
        )
    assert len(runner.requests) == expected_calls
    assert captured.value.__context__ is None
    assert not traceback_contains(captured.value, b"traceback-sentinel-image")


def test_prerequisite_inventory_limit_and_shared_deadline_fail_closed(
    tmp_path: Path,
) -> None:
    (tmp_path / "limit").mkdir()
    (tmp_path / "deadline").mkdir()
    over_limit = (b"1" * 64 + b"\n") * 4_097
    limit_runner = QueueRunner(
        [
            _json(_daemon()),
            CommandResult(0, b"pull\n", b""),
            _json(image_inspection()),
            CommandResult(0, over_limit, b""),
        ]
    )
    with pytest.raises(CoturnRuntimeError, match="prerequisites are invalid"):
        prepare_docker_prerequisites(
            runner=limit_runner,
            tools=_tools(),
            paths=_paths(tmp_path / "limit"),
            absolute_deadline=110.0,
            clock=lambda: 100.0,
        )
    assert len(limit_runner.requests) == 4

    ticks = iter((100.0, 100.0, 111.0))
    deadline_runner = QueueRunner(_successful_values())
    with pytest.raises(CoturnRuntimeError, match="prerequisites are invalid"):
        prepare_docker_prerequisites(
            runner=deadline_runner,
            tools=_tools(),
            paths=_paths(tmp_path / "deadline"),
            absolute_deadline=110.0,
            clock=lambda: next(ticks),
        )
    assert len(deadline_runner.requests) == 1


def test_every_pre_inventory_request_is_clipped_to_shared_deadline(tmp_path: Path) -> None:
    runner = QueueRunner(
        [
            _json(_daemon()),
            CommandResult(0, b"pull\n", b""),
            _json(image_inspection()),
            CommandResult(0, b"", b""),
        ]
    )
    prepare_docker_prerequisites(
        runner=runner,
        tools=_tools(),
        paths=_paths(tmp_path),
        absolute_deadline=100.2,
        clock=lambda: 100.0,
    )

    assert len(runner.requests) == 4
    assert [request.timeout_seconds for request in runner.requests] == pytest.approx(
        [0.2, 0.2, 0.2, 0.2]
    )


def test_expired_shared_deadline_dispatches_no_later_prerequisite_command(
    tmp_path: Path,
) -> None:
    now = [100.0]

    class AdvancingRunner(QueueRunner):
        def run(self, request: CommandRequest) -> CommandResult:
            result = super().run(request)
            now[0] += request.timeout_seconds
            return result

    runner = AdvancingRunner(_successful_values())
    with pytest.raises(CoturnRuntimeError, match="prerequisites are invalid"):
        prepare_docker_prerequisites(
            runner=runner,
            tools=_tools(),
            paths=_paths(tmp_path),
            absolute_deadline=100.2,
            clock=lambda: now[0],
        )

    assert len(runner.requests) == 1
    assert runner.requests[0].timeout_seconds == pytest.approx(0.2)
    assert now[0] == pytest.approx(100.2)


@pytest.mark.parametrize(
    "deadline",
    [True, 110, FloatSubclass(110.0), float("nan"), float("inf"), 100.05, 160.1],
)
def test_invalid_prerequisite_deadline_is_rejected_before_first_command(
    tmp_path: Path,
    deadline: object,
) -> None:
    runner = QueueRunner(_successful_values())
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn Docker prerequisites are invalid$",
    ):
        prepare_docker_prerequisites(
            runner=runner,
            tools=_tools(),
            paths=_paths(tmp_path),
            absolute_deadline=deadline,  # type: ignore[arg-type]
            clock=lambda: 100.0,
        )
    assert runner.requests == []


def test_invalid_prerequisite_clock_value_is_rejected_before_first_command(
    tmp_path: Path,
) -> None:
    runner = QueueRunner(_successful_values())
    with pytest.raises(CoturnRuntimeError, match="prerequisites are invalid"):
        prepare_docker_prerequisites(
            runner=runner,
            tools=_tools(),
            paths=_paths(tmp_path),
            absolute_deadline=110.0,
            clock=lambda: 100,  # type: ignore[return-value]
        )
    assert runner.requests == []


def test_prerequisite_runner_exception_does_not_reflect_raw_sentinel(tmp_path: Path) -> None:
    runner = QueueRunner([RuntimeError("traceback-sentinel-prerequisite-runner")])
    with pytest.raises(CoturnRuntimeError) as captured:
        prepare_docker_prerequisites(
            runner=runner,
            tools=_tools(),
            paths=_paths(tmp_path),
            absolute_deadline=110.0,
            clock=lambda: 100.0,
        )
    assert "sentinel" not in str(captured.value)
    assert not traceback_contains(captured.value, "traceback-sentinel-prerequisite-runner")


def test_pull_only_compatibility_boundary_discards_malformed_image_graph(
    tmp_path: Path,
) -> None:
    raw = b"traceback-sentinel-pull-only-image"
    runner = QueueRunner(
        [
            CommandResult(0, b"pull\n", b""),
            CommandResult(0, raw, b""),
        ]
    )
    with pytest.raises(
        CoturnRuntimeError,
        match=r"^Coturn image preparation failed$",
    ) as captured:
        pull_and_validate_image(runner=runner, tools=_tools(), paths=_paths(tmp_path))

    assert not traceback_contains(captured.value, raw)
