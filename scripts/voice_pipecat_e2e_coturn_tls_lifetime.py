"""Aggregate exact-inode lifetime authority for generated Coturn TLS files."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_coturn_tls_private import CoturnTlsError
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
    new_private_file_cleanup_receipt,
    settle_private_file_receipts_owned,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import ControlSignal

_AUTHORITY_TOKEN = object()
_COMBINED_TOKEN = object()


class TlsMaterialLifetimeAuthority:
    """Opaque, retryable authority over every exact inode in one TLS bundle."""

    __slots__ = ("_descriptor_authorities", "_lock", "_receipts", "_state")

    def __init__(self, token: object) -> None:
        if token is not _AUTHORITY_TOKEN:
            raise TypeError("TLS lifetime authority is factory-owned")
        self._descriptor_authorities: list[PrivateDescriptorCleanupAuthority] = []
        self._lock = threading.Lock()
        self._receipts: list[PrivateFileCleanupReceipt] = []
        self._state = "building"

    def new_slot(self) -> PrivateFileCleanupReceipt:
        with self._lock:
            if self._state != "building" or len(self._receipts) >= 8:
                raise RuntimeError("TLS lifetime authority cannot accept a file")
            slot = new_private_file_cleanup_receipt()
            self._receipts.append(slot)
            return slot

    def retain_private_authority(
        self,
        authority: PrivateDescriptorCleanupAuthority | PrivateFileCleanupReceipt,
    ) -> bool:
        """Adopt an exact private-file recovery before bundle cleanup."""

        with self._lock:
            if self._state not in {"building", "retained"}:
                return False
            if type(authority) is PrivateDescriptorCleanupAuthority:
                if authority in self._descriptor_authorities or not authority.active:
                    return True
                if len(self._descriptor_authorities) >= 4:
                    return False
                self._descriptor_authorities.append(authority)
                return True
            if type(authority) is PrivateFileCleanupReceipt:
                if authority in self._receipts or not authority.owned:
                    return True
                if len(self._receipts) >= 8:
                    return False
                self._receipts.append(authority)
                return True
            return False

    def retain(self) -> bool:
        with self._lock:
            if self._state != "building" or not self._receipts:
                return False
            self._state = "retained"
            return True

    def cleanup(
        self,
        *,
        initial_control: ControlSignal | None = None,
    ) -> tuple[bool, ControlSignal | None]:
        with self._lock:
            if self._state == "cleaned":
                return True, initial_control
            previous = self._state
            self._state = "cleaning"
            try:
                control = initial_control
                remaining: list[PrivateDescriptorCleanupAuthority] = []
                descriptor_failed = False
                for authority in self._descriptor_authorities:
                    failed, observed = authority.cleanup(initial_control=control)
                    control = control or observed
                    if failed:
                        descriptor_failed = True
                        remaining.append(authority)
                self._descriptor_authorities = remaining
                receipts = tuple(reversed(self._receipts))
                receipt_failed, control = settle_private_file_receipts_owned(
                    receipts,
                    initial_control=control,
                )
                receipts = ()
                failed = bool(descriptor_failed or receipt_failed)
            except BaseException:
                self._state = previous
                raise
            if failed:
                self._state = previous
            else:
                self._descriptor_authorities.clear()
                self._receipts.clear()
                self._state = "cleaned"
            return failed, control

    @property
    def retained(self) -> bool:
        with self._lock:
            return self._state in {"retained", "cleaning"}

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state in {"building", "retained", "cleaning"}

    def __repr__(self) -> str:
        return "TlsMaterialLifetimeAuthority()"


class TlsCombinedCleanupAuthority:
    """Opaque one-shot recovery for a lifetime plus an unadopted private authority."""

    __slots__ = ("_lifetime", "_lock", "_private", "_state")

    def __init__(
        self,
        token: object,
        lifetime: TlsMaterialLifetimeAuthority,
        private: PrivateDescriptorCleanupAuthority | PrivateFileCleanupReceipt,
    ) -> None:
        if token is not _COMBINED_TOKEN:
            raise TypeError("TLS combined cleanup authority is factory-owned")
        self._lifetime: TlsMaterialLifetimeAuthority | None = lifetime
        self._lock = threading.Lock()
        self._private: PrivateDescriptorCleanupAuthority | PrivateFileCleanupReceipt | None = (
            private
        )
        self._state = "retained"

    def cleanup(
        self,
        *,
        initial_control: ControlSignal | None = None,
    ) -> tuple[bool, ControlSignal | None]:
        with self._lock:
            if self._state != "retained":
                return True, initial_control
            self._state = "cleaning"
            control = initial_control
            try:
                private_failed = False
                if type(self._private) is PrivateDescriptorCleanupAuthority:
                    private_failed, observed = self._private.cleanup(
                        initial_control=control,
                    )
                    control = control or observed
                elif type(self._private) is PrivateFileCleanupReceipt:
                    private_failed, control = settle_private_file_receipts_owned(
                        (self._private,),
                        initial_control=control,
                    )
                if not private_failed:
                    self._private = None
                lifetime_failed = False
                if self._lifetime is not None:
                    lifetime_failed, observed = self._lifetime.cleanup(
                        initial_control=control,
                    )
                    control = control or observed
                if not lifetime_failed:
                    self._lifetime = None
            except BaseException:
                self._state = "retained"
                raise
            failed = bool(private_failed or lifetime_failed)
            self._state = "retained" if failed else "cleaned"
            return failed, control

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state in {"retained", "cleaning"}

    def __repr__(self) -> str:
        return "TlsCombinedCleanupAuthority()"


TlsCleanupAuthority = TlsMaterialLifetimeAuthority | TlsCombinedCleanupAuthority


class CoturnTlsCleanupRequired(CoturnTlsError):
    """Fixed failure carrying only opaque exact-inode recovery authority."""

    __slots__ = ("_cleanup_authority",)

    def __init__(self, authority: TlsCleanupAuthority) -> None:
        super().__init__("Coturn TLS cleanup failed")
        self._cleanup_authority = authority

    @property
    def cleanup_authority(self) -> TlsCleanupAuthority:
        return self._cleanup_authority

    def __repr__(self) -> str:
        return "CoturnTlsCleanupRequired('Coturn TLS cleanup failed')"


def new_tls_material_lifetime_authority() -> TlsMaterialLifetimeAuthority:
    return TlsMaterialLifetimeAuthority(_AUTHORITY_TOKEN)


def combine_tls_cleanup_authorities(
    lifetime: TlsMaterialLifetimeAuthority,
    private: PrivateDescriptorCleanupAuthority | PrivateFileCleanupReceipt,
) -> TlsCombinedCleanupAuthority | None:
    if type(lifetime) is not TlsMaterialLifetimeAuthority or not lifetime.active:
        return None
    valid_private = bool(
        (type(private) is PrivateDescriptorCleanupAuthority and private.active)
        or (type(private) is PrivateFileCleanupReceipt and private.owned)
    )
    if not valid_private:
        return None
    return TlsCombinedCleanupAuthority(_COMBINED_TOKEN, lifetime, private)


__all__ = [
    "CoturnTlsCleanupRequired",
    "TlsCleanupAuthority",
    "TlsCombinedCleanupAuthority",
    "TlsMaterialLifetimeAuthority",
    "combine_tls_cleanup_authorities",
    "new_tls_material_lifetime_authority",
]
