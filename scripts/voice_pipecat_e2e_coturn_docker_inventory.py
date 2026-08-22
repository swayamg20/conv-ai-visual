"""Global Docker network-inventory budget for the Coturn relay probe."""

from __future__ import annotations

import math
import threading
import time
from typing import Callable

from scripts.voice_pipecat_e2e_coturn_docker import CoturnDockerError
from scripts.voice_pipecat_e2e_coturn_host import require_full_resource_id

MAX_NETWORK_INVENTORY_ITEMS = 4_096
MAX_NETWORK_INVENTORY_SECONDS = 60.0
_RECEIPT_TOKEN = object()


class CoturnDockerNetworkError(CoturnDockerError):
    """An owned Docker bridge or inventory contract is malformed or unsafe."""


class CompletedNetworkInventory:
    """Factory-owned proof that every exact inventory ID was inspected."""

    __slots__ = ("_ipv4_subnets",)

    def __init__(self, token: object, ipv4_subnets: tuple[str, ...]) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Completed network inventory is factory-owned")
        self._ipv4_subnets = ipv4_subnets

    @property
    def ipv4_subnets(self) -> tuple[str, ...]:
        return self._ipv4_subnets

    def __repr__(self) -> str:
        return "CompletedNetworkInventory()"


class NetworkInventoryBudget:
    """One serial, deadline-bound budget across an exact Docker inventory."""

    __slots__ = (
        "_clock",
        "_deadline",
        "_failed",
        "_ipv4_subnets",
        "_lock",
        "_pending",
        "_remaining_ids",
        "_remaining_subnets",
    )

    def __init__(
        self,
        *,
        network_ids: tuple[str, ...],
        absolute_deadline: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        validated_ids = _validated_network_ids(network_ids)
        clock_failed = False
        try:
            now = clock()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            clock_failed = True
            now = 0.0
        if (
            validated_ids is None
            or clock_failed
            or not callable(clock)
            or isinstance(absolute_deadline, bool)
            or not isinstance(absolute_deadline, (int, float))
            or not math.isfinite(absolute_deadline)
            or isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or not 0.1 <= absolute_deadline - now <= MAX_NETWORK_INVENTORY_SECONDS
        ):
            network_ids = ()
            validated_ids = None
            clock = time.monotonic
            _raise_budget_error()
        self._clock = clock
        self._deadline = float(absolute_deadline)
        self._remaining_ids = set(validated_ids)
        self._remaining_subnets = MAX_NETWORK_INVENTORY_ITEMS
        self._ipv4_subnets: set[str] = set()
        self._pending: str | None = None
        self._failed = False
        self._lock = threading.Lock()

    @property
    def remaining_networks(self) -> int:
        with self._lock:
            return len(self._remaining_ids)

    @property
    def remaining_subnets(self) -> int:
        with self._lock:
            return self._remaining_subnets

    def __repr__(self) -> str:
        return "NetworkInventoryBudget()"

    def begin_inspection(self, network_id: str) -> float | None:
        now = self._read_clock()
        if now is None:
            return None
        with self._lock:
            remaining = self._deadline - now
            if (
                self._failed
                or self._pending is not None
                or network_id not in self._remaining_ids
                or self._remaining_subnets == 0
                or remaining < 0.1
            ):
                self._failed = True
                return None
            self._pending = network_id
            return min(10.0, remaining)

    def commit_inspection(
        self,
        network_id: str,
        *,
        ipam_entries: int,
        ipv4_subnets: tuple[str, ...],
    ) -> bool:
        now = self._read_clock()
        if now is None:
            return False
        with self._lock:
            if (
                self._failed
                or self._pending != network_id
                or now > self._deadline
                or isinstance(ipam_entries, bool)
                or not isinstance(ipam_entries, int)
                or not 0 <= ipam_entries <= self._remaining_subnets
                or not isinstance(ipv4_subnets, tuple)
                or len(ipv4_subnets) > ipam_entries
                or not all(isinstance(value, str) for value in ipv4_subnets)
            ):
                self._failed = True
                return False
            self._pending = None
            self._remaining_ids.remove(network_id)
            self._remaining_subnets -= ipam_entries
            self._ipv4_subnets.update(ipv4_subnets)
            return True

    def complete(self) -> CompletedNetworkInventory | None:
        now = self._read_clock()
        if now is None:
            return None
        with self._lock:
            if (
                self._failed
                or self._pending is not None
                or self._remaining_ids
                or now > self._deadline
            ):
                self._failed = True
                return None
            return CompletedNetworkInventory(
                _RECEIPT_TOKEN,
                tuple(sorted(self._ipv4_subnets)),
            )

    def abort(self) -> None:
        with self._lock:
            self._failed = True

    def _read_clock(self) -> float | None:
        failed = False
        try:
            value = self._clock()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            failed = True
            value = 0.0
        if (
            failed
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            self.abort()
            return None
        return float(value)


def _raise_budget_error() -> None:
    raise CoturnDockerNetworkError("Coturn Docker network inventory budget is invalid") from None


def complete_network_inventory(
    budget: NetworkInventoryBudget,
) -> CompletedNetworkInventory:
    receipt = budget.complete() if isinstance(budget, NetworkInventoryBudget) else None
    budget = None  # type: ignore[assignment]
    if receipt is None:
        _raise_budget_error()
    return receipt


def _validated_network_ids(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, tuple) or len(value) > MAX_NETWORK_INVENTORY_ITEMS:
        return None
    try:
        if len(set(value)) != len(value):
            return None
        if not all(require_full_resource_id(identifier) == identifier for identifier in value):
            return None
    except (RuntimeError, TypeError):
        return None
    return value


__all__ = [
    "MAX_NETWORK_INVENTORY_ITEMS",
    "MAX_NETWORK_INVENTORY_SECONDS",
    "CompletedNetworkInventory",
    "CoturnDockerNetworkError",
    "NetworkInventoryBudget",
    "complete_network_inventory",
]
