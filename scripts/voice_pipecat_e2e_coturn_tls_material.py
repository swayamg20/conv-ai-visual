"""Pre-owned publication slots for one generated Coturn TLS bundle."""

from __future__ import annotations

import re
import threading
from datetime import datetime

from scripts import voice_pipecat_e2e_coturn_tls_values as _tls_values
from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_host import CoturnRuntimePaths
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import TlsMaterialLifetimeAuthority
from scripts.voice_pipecat_e2e_coturn_tls_private import ControlSignal, CoturnTlsError
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SPKI_B64 = re.compile(r"^[A-Za-z0-9+/]{43}=$")
_RECEIPT_TOKEN = object()
_SLOT_TOKEN = object()
_RESERVATION_TOKEN = object()
_REGISTRY_LOCK = threading.RLock()
_REGISTRY: dict[object, _SlotRecord] = {}
_MAX_GENERATION_SLOTS = 64


class TlsMaterialReceipt(_tls_values.LinearTlsAuthority):
    """Sanitized evidence plus hidden exact-inode cleanup authority."""

    __slots__ = (
        "_certificate_sha256",
        "_lifetime",
        "_lifetime_lock",
        "_not_after",
        "_not_before",
        "_pin",
    )

    def __init__(
        self,
        token: object,
        *,
        certificate_sha256: str,
        chromium_spki_sha256_b64: str,
        not_before: datetime,
        not_after: datetime,
    ) -> None:
        if token is not _RECEIPT_TOKEN:
            raise TypeError("Coturn TLS receipt is factory-owned")
        if (
            not _SHA256_HEX.fullmatch(certificate_sha256)
            or not _SPKI_B64.fullmatch(chromium_spki_sha256_b64)
            or not_before.tzinfo is None
            or not_after.tzinfo is None
            or not_before >= not_after
        ):
            raise CoturnTlsError("Coturn TLS receipt is invalid")
        self._certificate_sha256 = certificate_sha256
        self._pin = chromium_spki_sha256_b64
        self._not_before = not_before
        self._not_after = not_after
        self._lifetime: TlsMaterialLifetimeAuthority | None = None
        self._lifetime_lock = threading.Lock()

    @property
    def certificate_sha256(self) -> str:
        return self._certificate_sha256

    @property
    def chromium_spki_sha256_b64(self) -> str:
        return self._pin

    @property
    def not_before(self) -> datetime:
        return self._not_before

    @property
    def not_after(self) -> datetime:
        return self._not_after

    @property
    def has_cleanup_authority(self) -> bool:
        with self._lifetime_lock:
            return self._lifetime is not None and self._lifetime.retained

    def _bind_lifetime(self, lifetime: TlsMaterialLifetimeAuthority) -> bool:
        with self._lifetime_lock:
            if (
                self._lifetime is not None
                or type(lifetime) is not TlsMaterialLifetimeAuthority
                or not lifetime.retain()
            ):
                return False
            self._lifetime = lifetime
            return True

    def _cleanup_lifetime(
        self,
    ) -> tuple[bool, ControlSignal | None, TlsMaterialLifetimeAuthority | None]:
        with self._lifetime_lock:
            if self._lifetime is None:
                return True, None, None
            authority = self._lifetime
            failed, control = authority.cleanup()
            if not failed:
                self._lifetime = None
                authority = None
            return failed, control, authority

    def _retained_lifetime(self) -> TlsMaterialLifetimeAuthority | None:
        with self._lifetime_lock:
            return self._lifetime

    def __repr__(self) -> str:
        return "TlsMaterialReceipt()"


class _SlotRecord:
    __slots__ = ("fingerprint", "snapshot")

    def __init__(self, fingerprint: bytes) -> None:
        self.fingerprint = fingerprint
        self.snapshot = ("empty", None, None)


class TlsMaterialGenerationReservation:
    """Opaque identity for one in-flight slot generation attempt."""

    __slots__ = ("_token",)

    def __init__(self, token: object) -> None:
        if token is not _RESERVATION_TOKEN:
            raise TypeError("Coturn TLS generation reservation is factory-owned")
        self._token = object()

    def __repr__(self) -> str:
        return "TlsMaterialGenerationReservation()"


class TlsMaterialGenerationSlot(_tls_values.LinearTlsAuthority):
    """Opaque, pre-owned destination for a generated TLS lifetime."""

    __slots__ = ("_handle",)

    def __init__(self, token: object, handle: object) -> None:
        if token is not _SLOT_TOKEN:
            raise TypeError("Coturn TLS generation slot is factory-owned")
        self._handle = handle

    @property
    def has_material(self) -> bool:
        return _slot_state(self) == "retained"

    @property
    def cleanup_complete(self) -> bool:
        return _slot_state(self) == "cleaned"

    @property
    def certificate_sha256(self) -> str:
        return _slot_evidence(self, "certificate_sha256")  # type: ignore[return-value]

    @property
    def chromium_spki_sha256_b64(self) -> str:
        return _slot_evidence(self, "chromium_spki_sha256_b64")  # type: ignore[return-value]

    @property
    def not_before(self) -> datetime:
        return _slot_evidence(self, "not_before")  # type: ignore[return-value]

    @property
    def not_after(self) -> datetime:
        return _slot_evidence(self, "not_after")  # type: ignore[return-value]

    def __repr__(self) -> str:
        return "TlsMaterialGenerationSlot()"

    def __del__(self) -> None:
        complete = False
        while not complete:
            try:
                _slot_transition_hook("finalizer-entry")
                handle = object.__getattribute__(self, "_handle")
                with _REGISTRY_LOCK:
                    record = _REGISTRY.get(handle)
                    if record is not None and record.snapshot[0] in {"empty", "cleaned"}:
                        _REGISTRY.pop(handle, None)
                _slot_transition_hook("finalizer-return")
                complete = True
            except (KeyboardInterrupt, SystemExit):
                continue
            except BaseException:
                complete = True


def new_tls_material_receipt(
    *,
    certificate_sha256: str,
    chromium_spki_sha256_b64: str,
    not_before: datetime,
    not_after: datetime,
) -> TlsMaterialReceipt:
    return TlsMaterialReceipt(
        _RECEIPT_TOKEN,
        certificate_sha256=certificate_sha256,
        chromium_spki_sha256_b64=chromium_spki_sha256_b64,
        not_before=not_before,
        not_after=not_after,
    )


def new_tls_material_generation_reservation() -> TlsMaterialGenerationReservation:
    return TlsMaterialGenerationReservation(_RESERVATION_TOKEN)


def new_tls_material_generation_slot(
    *,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
) -> TlsMaterialGenerationSlot:
    """Create one harmless empty slot bound to an exact run and topology."""

    control = TlsControlLatch()
    fingerprint: bytes | None = None
    handle: object | None = None
    slot: TlsMaterialGenerationSlot | None = None
    failed = False
    while fingerprint is None and not failed:
        try:
            fingerprint = _tls_values.lineage_fingerprint(paths, topology)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
    while slot is None and not failed:
        try:
            handle = object()
            slot = TlsMaterialGenerationSlot(_SLOT_TOKEN, handle)
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
    published = False
    while slot is not None and fingerprint is not None and not failed and not published:
        try:
            _slot_transition_hook("factory-entry")
            with _REGISTRY_LOCK:
                if handle not in _REGISTRY:
                    if len(_REGISTRY) >= _MAX_GENERATION_SLOTS:
                        failed = True
                    else:
                        _REGISTRY[handle] = _SlotRecord(fingerprint)
                published = type(_REGISTRY.get(handle)) is _SlotRecord
            _slot_transition_hook("factory-return")
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
    terminal_error: KeyboardInterrupt | SystemExit | None = None
    while True:
        try:
            observed_control = control.value()
            removed = handle is None
            if (failed or observed_control is not None) and handle is not None:
                with _REGISTRY_LOCK:
                    record = _REGISTRY.get(handle)
                    if record is not None and record.snapshot[0] == "empty":
                        _REGISTRY.pop(handle, None)
                    removed = _REGISTRY.get(handle) is None
            paths = topology = None  # type: ignore[assignment]
            fingerprint = record = None
            if observed_control is None:
                if failed or slot is None or not published:
                    slot = handle = None
                    raise CoturnTlsError("Coturn TLS generation slot is invalid") from None
                return slot
            if not removed:
                continue
            slot = handle = None
            if terminal_error is None:
                terminal_error = _tls_values.new_tls_control_error(observed_control)
            raise terminal_error from None
        except (KeyboardInterrupt, SystemExit) as error:
            if error is terminal_error:
                raise
            control.record_error(error)


def cleanup_tls_material_generation_slot(slot: TlsMaterialGenerationSlot) -> None:
    """Retryably consume the exact lifetime retained by one generation slot."""

    receipt: TlsMaterialReceipt | None = None
    control = TlsControlLatch()
    operation = object()
    failed = False
    cleanup_failed = False
    cleanup_invoked = False
    retained = False
    published: TlsMaterialReceipt | None = None
    _authority: TlsMaterialLifetimeAuthority | None = None
    phase = 0
    while phase < 5:
        try:
            if phase == 0:
                _slot_transition_hook("cleanup-entry")
                with _REGISTRY_LOCK:
                    record = _record_for(slot)
                    state, owner, published = record.snapshot
                    if state == "cleaned" and published is None:
                        phase = 3
                    elif (
                        state == "retained"
                        and type(published) is TlsMaterialReceipt
                        and owner is None
                    ):
                        receipt = published
                        record.snapshot = ("cleaning", operation, receipt)
                        phase = 1
                    elif (
                        state == "cleaning"
                        and owner is operation
                        and type(published) is TlsMaterialReceipt
                    ):
                        receipt = published
                        phase = 1
                    else:
                        failed = True
                        phase = 3
                _slot_transition_hook("cleanup-owned")
            elif phase == 1:
                if cleanup_invoked:
                    cleanup_failed = True
                    retained = _receipt_retained(receipt, control)  # type: ignore[arg-type]
                    phase = 2
                    continue
                cleanup_invoked = True
                cleanup_failed, observed, _authority = receipt._cleanup_lifetime()  # type: ignore[union-attr]
                if observed is not None:
                    control.record(observed)
                retained = _receipt_retained(receipt, control)  # type: ignore[arg-type]
                phase = 2
            elif phase == 2:
                with _REGISTRY_LOCK:
                    record = _record_for(slot)
                    state, owner, published = record.snapshot
                    if owner is not operation or state != "cleaning" or published is not receipt:
                        failed = True
                    elif retained:
                        record.snapshot = ("retained", None, receipt)
                        failed = True
                    else:
                        record.snapshot = ("cleaned", None, None)
                        failed = bool(failed or cleanup_failed)
                    phase = 3
                _slot_transition_hook("cleanup-state-safe")
            elif phase == 3:
                _slot_transition_hook("cleanup-return")
                phase = 4
            else:
                slot = receipt = record = operation = published = _authority = None  # type: ignore[assignment]
                phase = 5
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
            if phase == 1 and cleanup_invoked:
                cleanup_failed = True
                retained = _receipt_retained(receipt, control)  # type: ignore[arg-type]
                phase = 2
        except BaseException:
            failed = True
            if phase in {1, 2} or receipt is not None:
                cleanup_failed = True
                retained = bool(receipt is not None and _receipt_retained(receipt, control))
                phase = 2
            else:
                phase = 3
    terminal_error: KeyboardInterrupt | SystemExit | None = None
    while True:
        try:
            observed_control = control.value()
            if observed_control is None:
                break
            if terminal_error is None:
                terminal_error = _tls_values.new_tls_control_error(observed_control)
            raise terminal_error from None
        except (KeyboardInterrupt, SystemExit) as error:
            if error is terminal_error:
                raise
            control.record_error(error)
    if failed:
        raise CoturnTlsError("Coturn TLS generation slot cleanup failed") from None


def reserve_tls_material_generation_slot(
    slot: TlsMaterialGenerationSlot,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
    reservation: TlsMaterialGenerationReservation,
) -> tuple[bool, ControlSignal | None]:
    """Reserve an exact empty slot before generation can cause a side effect."""

    control = TlsControlLatch()
    reserved = False
    failed = False
    phase = 0
    fingerprint: bytes | None = None
    record: _SlotRecord | None = None
    try:
        fingerprint = _tls_values.lineage_fingerprint(paths, topology)
        while phase < 3 and not failed:
            try:
                with _REGISTRY_LOCK:
                    record = _record_for(slot)
                    state, owner, receipt = record.snapshot
                    if phase == 0:
                        already_reserved = (
                            state == "reserved" and owner is reservation and receipt is None
                        )
                        if not already_reserved and (
                            record.fingerprint != fingerprint
                            or state != "empty"
                            or receipt is not None
                            or owner is not None
                            or type(reservation) is not TlsMaterialGenerationReservation
                        ):
                            failed = True
                            break
                        if not already_reserved:
                            record.snapshot = ("reserved", reservation, None)
                        phase = 1
                    elif phase == 1:
                        if state != "reserved" or owner is not reservation or receipt is not None:
                            failed = True
                            break
                        phase = 2
                    else:
                        reserved = bool(
                            state == "reserved" and owner is reservation and receipt is None
                        )
                        phase = 3
                _slot_transition_hook(
                    ("reserve-begin", "reserve-inside", "reserve-complete")[phase - 1]
                )
            except (KeyboardInterrupt, SystemExit) as error:
                control.record_error(error)
            except BaseException:
                failed = True
    except (KeyboardInterrupt, SystemExit) as error:
        control.record_error(error)
    except BaseException:
        failed = True
    paths = topology = fingerprint = record = None  # type: ignore[assignment]
    if failed:
        reserved = False
    return reserved, control.value()


def adopt_tls_material_generation_slot(
    slot: TlsMaterialGenerationSlot,
    receipt: TlsMaterialReceipt,
    reservation: TlsMaterialGenerationReservation,
) -> tuple[bool, ControlSignal | None]:
    """Publish a bound receipt completely before reporting adoption."""

    control = TlsControlLatch()
    failed = False
    phase = 0
    record: _SlotRecord | None = None
    while phase < 3:
        try:
            _slot_transition_hook(("adopt-begin", "adopt-inside", "adopt-complete")[phase])
            with _REGISTRY_LOCK:
                record = _record_for(slot)
                state, owner, published = record.snapshot
                if phase == 0:
                    already_adopted = state == "retained" and owner is None and published is receipt
                    if not already_adopted and (
                        state != "reserved"
                        or published is not None
                        or owner is not reservation
                        or type(reservation) is not TlsMaterialGenerationReservation
                        or type(receipt) is not TlsMaterialReceipt
                        or not receipt.has_cleanup_authority
                    ):
                        failed = True
                        break
                    if not already_adopted:
                        record.snapshot = ("retained", None, receipt)
                    phase = 1
                elif phase == 1:
                    if state != "retained" or owner is not None or published is not receipt:
                        failed = True
                        break
                    phase = 2
                else:
                    if state != "retained" or owner is not None or published is not receipt:
                        failed = True
                        break
                    phase = 3
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
            if phase >= 2:
                continue
            break
    receipt = record = None  # type: ignore[assignment]
    return bool(phase == 3 and not failed), control.value()


def release_tls_material_generation_slot(
    slot: TlsMaterialGenerationSlot,
    reservation: TlsMaterialGenerationReservation,
) -> tuple[bool, ControlSignal | None]:
    """Return a reservation with no published material to its harmless state."""

    control = TlsControlLatch()
    released = False
    done = False
    while not done:
        try:
            _slot_transition_hook("release-entry")
            with _REGISTRY_LOCK:
                record = _record_for(slot)
                state, owner, receipt = record.snapshot
                if state == "empty" and receipt is None and owner is None:
                    released = True
                elif (
                    state == "reserved"
                    and receipt is None
                    and owner is reservation
                    and type(reservation) is TlsMaterialGenerationReservation
                ):
                    record.snapshot = ("empty", None, None)
                    released = True
                done = True
            _slot_transition_hook("release-return")
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except CoturnTlsError:
            done = True
        except BaseException:
            continue
    slot = record = reservation = None  # type: ignore[assignment]
    return released, control.value()


def tls_material_generation_slot_owns_receipt(
    slot: TlsMaterialGenerationSlot,
) -> tuple[str, ControlSignal | None]:
    control = TlsControlLatch()
    ownership = "unknown"
    done = False
    while not done:
        try:
            _slot_transition_hook("owned-check-entry")
            with _REGISTRY_LOCK:
                record = _record_for(slot)
                state, owner, receipt = record.snapshot
                owned = type(receipt) is TlsMaterialReceipt and (
                    (state == "retained" and owner is None)
                    or (state == "cleaning" and owner is not None)
                )
                ownership = "owned" if owned else "unowned"
                done = True
            _slot_transition_hook("owned-check-return")
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except CoturnTlsError:
            ownership = "invalid"
            done = True
        except BaseException:
            done = True
    slot = record = None  # type: ignore[assignment]
    return ownership, control.value()


def _record_for(slot: TlsMaterialGenerationSlot) -> _SlotRecord:
    if type(slot) is not TlsMaterialGenerationSlot:
        raise CoturnTlsError("Coturn TLS generation slot is invalid")
    handle = object.__getattribute__(slot, "_handle")
    record = _REGISTRY.get(handle)
    if type(record) is not _SlotRecord:
        raise CoturnTlsError("Coturn TLS generation slot is invalid")
    return record


def _slot_state(slot: TlsMaterialGenerationSlot) -> str:
    control = TlsControlLatch()
    state = "invalid"
    failed = False
    done = False
    while not done:
        try:
            _slot_transition_hook("state-entry")
            with _REGISTRY_LOCK:
                state = _record_for(slot).snapshot[0]
                done = True
            _slot_transition_hook("state-return")
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
            done = True
    slot = None  # type: ignore[assignment]
    terminal_error: KeyboardInterrupt | SystemExit | None = None
    while True:
        try:
            observed_control = control.value()
            if observed_control is None:
                break
            if terminal_error is None:
                terminal_error = _tls_values.new_tls_control_error(observed_control)
            raise terminal_error from None
        except (KeyboardInterrupt, SystemExit) as error:
            if error is terminal_error:
                raise
            control.record_error(error)
    control = None  # type: ignore[assignment]
    if failed or state == "invalid":
        raise CoturnTlsError("Coturn TLS generation slot is invalid") from None
    return state


def _slot_evidence(slot: TlsMaterialGenerationSlot, name: str) -> object:
    control = TlsControlLatch()
    value: object = None
    receipt: TlsMaterialReceipt | None = None
    failed = False
    done = False
    while not done:
        try:
            _slot_transition_hook("evidence-entry")
            with _REGISTRY_LOCK:
                record = _record_for(slot)
                state, owner, receipt = record.snapshot
                if (
                    state != "retained"
                    or owner is not None
                    or type(receipt) is not TlsMaterialReceipt
                ):
                    failed = True
                else:
                    value = getattr(receipt, name)
                done = True
            _slot_transition_hook("evidence-return")
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            failed = True
            done = True
    scrubbed = False
    while not scrubbed:
        try:
            slot = record = receipt = None  # type: ignore[assignment]
            scrubbed = True
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            continue
    terminal_error: KeyboardInterrupt | SystemExit | None = None
    while True:
        try:
            observed_control = control.value()
            if observed_control is None:
                break
            value = None
            if terminal_error is None:
                terminal_error = _tls_values.new_tls_control_error(observed_control)
            raise terminal_error from None
        except (KeyboardInterrupt, SystemExit) as error:
            if error is terminal_error:
                raise
            control.record_error(error)
    control = None  # type: ignore[assignment]
    if failed:
        value = None
        raise CoturnTlsError("Coturn TLS generation slot is unavailable") from None
    return value


def _receipt_retained(receipt: TlsMaterialReceipt, control: TlsControlLatch) -> bool:
    while True:
        try:
            return receipt.has_cleanup_authority
        except (KeyboardInterrupt, SystemExit) as error:
            control.record_error(error)
        except BaseException:
            return True


def _slot_transition_hook(_phase: str) -> None:
    """Secret-free deterministic seam for finite-control tests."""


__all__ = [
    "TlsMaterialGenerationSlot",
    "TlsMaterialReceipt",
    "cleanup_tls_material_generation_slot",
    "new_tls_material_generation_slot",
]
