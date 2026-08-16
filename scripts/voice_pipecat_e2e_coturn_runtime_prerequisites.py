"""Sanitized Docker prerequisite execution for the Coturn relay probe."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_docker import (
    CoturnImageReceipt,
    build_docker_info_request,
    build_image_inspect_request,
    build_image_pull_request,
    decode_inspection_result,
    validate_docker_info_result,
    validate_image_inspection,
)
from scripts.voice_pipecat_e2e_coturn_docker_inventory import (
    MAX_NETWORK_INVENTORY_SECONDS,
    CompletedNetworkInventory,
    NetworkInventoryBudget,
    complete_network_inventory,
)
from scripts.voice_pipecat_e2e_coturn_docker_network import (
    build_network_inventory_inspect_request,
    build_network_inventory_request,
    parse_network_inventory_ids,
    parse_network_inventory_subnets,
)
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRequest,
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
    execute_checked,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    CoturnRuntimeError,
    control_signal,
    raise_control,
)

_PREREQUISITES_TOKEN = object()


class DockerPrerequisites:
    """Factory-owned image and completed full-network-inventory proof."""

    __slots__ = ("_image", "_network_inventory")

    def __init__(
        self,
        token: object,
        *,
        image: CoturnImageReceipt,
        network_inventory: CompletedNetworkInventory,
    ) -> None:
        if token is not _PREREQUISITES_TOKEN:
            raise TypeError("Docker prerequisites are factory-owned")
        self._image = image
        self._network_inventory = network_inventory

    @property
    def image(self) -> CoturnImageReceipt:
        return self._image

    @property
    def network_inventory(self) -> CompletedNetworkInventory:
        return self._network_inventory

    def __repr__(self) -> str:
        return "DockerPrerequisites()"


def pull_and_validate_image(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CoturnImageReceipt:
    """Pull and validate through a fresh boundary that retains no raw result."""

    receipt: CoturnImageReceipt | None = None
    control: ControlSignal | None = None
    try:
        receipt = _pull_and_validate_image(runner=runner, tools=tools, paths=paths)
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    runner = tools = paths = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn image preparation failed") from None
    return receipt


def _pull_and_validate_image(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
) -> CoturnImageReceipt:
    """Pull only the pinned digest/platform, then validate its exact child."""

    execute_checked(
        runner,
        build_image_pull_request(tools, paths),
        failure="Coturn image pull failed",
    )
    inspection = execute_checked(
        runner,
        build_image_inspect_request(tools, paths),
        failure="Coturn image inspection failed",
    )
    return validate_image_inspection(decode_inspection_result(inspection, label="image"))


def prepare_docker_prerequisites(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    absolute_deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> DockerPrerequisites:
    """Execute the exact daemon, image, and complete inventory sequence."""

    receipt: DockerPrerequisites | None = None
    control: ControlSignal | None = None
    try:
        receipt = _prepare_docker_prerequisites(
            runner=runner,
            tools=tools,
            paths=paths,
            absolute_deadline=absolute_deadline,
            clock=clock,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control = control_signal(error)
    except BaseException:
        pass
    runner = tools = paths = clock = None  # type: ignore[assignment]
    absolute_deadline = 0.0
    if control is not None:
        raise_control(control)
    if receipt is None:
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid") from None
    return receipt


def _prepare_docker_prerequisites(
    *,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    absolute_deadline: float,
    clock: Callable[[], float],
) -> DockerPrerequisites:
    _validate_prerequisite_deadline(absolute_deadline, clock)
    info = execute_checked(
        runner,
        _deadline_request(
            build_docker_info_request(tools, paths),
            absolute_deadline=absolute_deadline,
            clock=clock,
        ),
        failure="Coturn Docker daemon validation failed",
    )
    validate_docker_info_result(info)
    info = None

    pulled = execute_checked(
        runner,
        _deadline_request(
            build_image_pull_request(tools, paths),
            absolute_deadline=absolute_deadline,
            clock=clock,
        ),
        failure="Coturn image pull failed",
    )
    if pulled.stderr:
        raise CoturnRuntimeError("Coturn image pull failed")
    pulled = None

    inspected_image = execute_checked(
        runner,
        _deadline_request(
            build_image_inspect_request(tools, paths),
            absolute_deadline=absolute_deadline,
            clock=clock,
        ),
        failure="Coturn image inspection failed",
    )
    image = validate_image_inspection(decode_inspection_result(inspected_image, label="image"))
    inspected_image = None

    inventory = execute_checked(
        runner,
        _deadline_request(
            build_network_inventory_request(tools, paths),
            absolute_deadline=absolute_deadline,
            clock=clock,
        ),
        failure="Coturn Docker network inventory failed",
    )
    network_ids = parse_network_inventory_ids(inventory)
    inventory = None
    budget = NetworkInventoryBudget(
        network_ids=network_ids,
        absolute_deadline=absolute_deadline,
        clock=clock,
    )
    for network_id in network_ids:
        inspected_network = execute_checked(
            runner,
            build_network_inventory_inspect_request(
                tools,
                paths,
                network_id,
                budget,
            ),
            failure="Coturn Docker network inventory failed",
        )
        parse_network_inventory_subnets(
            inspected_network,
            expected_network_id=network_id,
            budget=budget,
        )
        inspected_network = None
    completed = complete_network_inventory(budget)
    network_ids = ()
    budget = None
    return DockerPrerequisites(
        _PREREQUISITES_TOKEN,
        image=image,
        network_inventory=completed,
    )


def _validate_prerequisite_deadline(
    absolute_deadline: object,
    clock: object,
) -> None:
    if type(absolute_deadline) is not float or not math.isfinite(absolute_deadline):
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid")
    if not callable(clock):
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid")
    try:
        now = clock()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid") from None
    if (
        type(now) is not float
        or not math.isfinite(now)
        or not 0.1 <= absolute_deadline - now <= MAX_NETWORK_INVENTORY_SECONDS
    ):
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid")


def _deadline_request(
    request: CommandRequest,
    *,
    absolute_deadline: float,
    clock: Callable[[], float],
) -> CommandRequest:
    now = clock()
    if type(now) is not float or not math.isfinite(now):
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid")
    remaining = absolute_deadline - now
    if remaining < 0.1:
        raise CoturnRuntimeError("Coturn Docker prerequisites are invalid")
    return CommandRequest(
        argv=request.argv,
        environment=request.environment,
        stdin=request.stdin,
        timeout_seconds=min(request.timeout_seconds, remaining),
        maximum_output_bytes=request.maximum_output_bytes,
        umask=request.umask,
    )


__all__ = [
    "DockerPrerequisites",
    "prepare_docker_prerequisites",
    "pull_and_validate_image",
]
