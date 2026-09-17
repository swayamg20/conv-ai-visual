"""Context-bound adoption of child cleanup authority for the relay aggregate."""

from __future__ import annotations

from collections.abc import Callable

from scripts.voice_pipecat_e2e_coturn_runtime import (
    AttachedCoturnProcess,
    CoturnAttachedCleanupRequired,
    CoturnAttachedProcessCleanupRequired,
    CoturnDirectorySyncCleanupRequired,
    CoturnRuntimePrivateCleanupRequired,
    DirectorySyncCleanupAuthority,
    RuntimePrivateCleanupAuthority,
    RuntimeTlsMaterial,
    UnpublishedAttachedCleanupAuthority,
)
from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    ControlSignal,
    control_signal,
)
from scripts.voice_pipecat_e2e_coturn_tls import (
    CoturnTlsCleanupRequired,
    CoturnTlsPrivateCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
)
from scripts.voice_pipecat_e2e_relay_owner_state import RelayProbeOwner


class _PendingAuthorityQueue:
    """Private immutable queue when two exact child recoveries coexist."""

    __slots__ = ("_items",)

    def __init__(self, items: tuple[object, ...]) -> None:
        retained: list[object] = []
        for candidate in items:
            if not any(candidate is current for current in retained):
                retained.append(candidate)
        self._items = tuple(retained)

    def _contains(self, candidate: object) -> bool:
        return any(candidate is current for current in self._items)

    def _append(self, candidate: object) -> _PendingAuthorityQueue:
        return (
            self if self._contains(candidate) else _PendingAuthorityQueue((*self._items, candidate))
        )

    def _remove(self, candidate: object) -> object | None:
        retained = tuple(current for current in self._items if current is not candidate)
        if not retained:
            return None
        if len(retained) == 1:
            return retained[0]
        return _PendingAuthorityQueue(retained)

    def __copy__(self) -> None:
        raise TypeError("Relay probe pending authority cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay probe pending authority cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay probe pending authority cannot be serialized")

    def __repr__(self) -> str:
        return "_PendingAuthorityQueue()"


def _retain_attached_cleanup_authority(
    owner: RelayProbeOwner,
    error: BaseException,
    *,
    process: AttachedCoturnProcess,
    runner: object,
) -> None:
    """Adopt only an authority minted for this exact start call and parent."""

    def extract() -> object | None:
        candidate = _exact_exception_authority(
            error,
            {
                CoturnAttachedCleanupRequired: (
                    AttachedCoturnProcess,
                    UnpublishedAttachedCleanupAuthority,
                ),
                CoturnAttachedProcessCleanupRequired: AttachedCoturnProcess,
            },
        )
        if candidate is process:
            return candidate
        if type(candidate) is UnpublishedAttachedCleanupAuthority:
            with candidate._lock:
                if candidate._runner is runner and candidate._state in {
                    "armed",
                    "retained",
                    "terminating",
                }:
                    return candidate
        return None

    _retain_transaction(owner, error, extract)


def _retain_runtime_persistence_authority(
    owner: RelayProbeOwner,
    error: BaseException,
) -> None:
    """Adopt only a persistence authority emitted by its exact runtime call."""

    _retain_transaction(
        owner,
        error,
        lambda: _exact_exception_authority(
            error,
            {
                CoturnDirectorySyncCleanupRequired: DirectorySyncCleanupAuthority,
                CoturnRuntimePrivateCleanupRequired: RuntimePrivateCleanupAuthority,
            },
        ),
    )


def _retain_tls_generation_authority(
    owner: RelayProbeOwner,
    error: BaseException,
    *,
    material: RuntimeTlsMaterial,
) -> None:
    """Adopt raw TLS cleanup only at the exact preowned generation boundary."""

    if type(material) is not RuntimeTlsMaterial:
        return
    _retain_transaction(
        owner,
        error,
        lambda: _exact_exception_authority(
            error,
            {
                CoturnTlsCleanupRequired: (
                    TlsCombinedCleanupAuthority,
                    TlsMaterialLifetimeAuthority,
                ),
                CoturnTlsPrivateCleanupRequired: (
                    PrivateDescriptorCleanupAuthority,
                    PrivateFileCleanupReceipt,
                ),
            },
        ),
    )


def _retain_transaction(
    owner: RelayProbeOwner,
    error: BaseException,
    extract: Callable[[], object | None],
) -> None:
    """Reconcile publication across every finite internal control cut."""

    first_control = (
        control_signal(error) if isinstance(error, (KeyboardInterrupt, SystemExit)) else None
    )
    while True:
        try:
            candidate = extract()
            _publish_pending(owner, candidate, first_control)
            return
        except (KeyboardInterrupt, SystemExit) as interrupted:
            first_control = first_control or control_signal(interrupted)
        except BaseException:
            pass


def _exact_exception_authority(
    error: BaseException,
    expected: dict[type[BaseException], type[object] | tuple[type[object], ...]],
) -> object | None:
    candidate: object | None = None
    allowed = expected.get(type(error))
    if allowed is not None:
        candidate = object.__getattribute__(error, "_cleanup_authority")
    elif isinstance(error, (KeyboardInterrupt, SystemExit)):
        namespace = object.__getattribute__(error, "__dict__")
        if type(namespace) is dict:
            candidate = namespace.get("cleanup_authority")
        allowed = tuple(value for item in expected.values() for value in _as_types(item))
    return candidate if allowed is not None and type(candidate) in _as_types(allowed) else None


def _as_types(value: type[object] | tuple[type[object], ...]) -> tuple[type[object], ...]:
    return value if type(value) is tuple else (value,)


def _publish_pending(
    owner: RelayProbeOwner,
    candidate: object | None,
    control: ControlSignal | None,
) -> None:
    _pending_publication_hook("entry")
    with owner._lock:
        current = owner._pending_authority
        if candidate is not None and current is None:
            _pending_publication_hook("before-store")
            owner._pending_authority = candidate
            _pending_publication_hook("after-store")
        elif candidate is not None and current is not candidate:
            if type(current) is _PendingAuthorityQueue:
                _pending_publication_hook("before-store")
                owner._pending_authority = current._append(candidate)
            else:
                _pending_publication_hook("before-store")
                owner._pending_authority = _PendingAuthorityQueue((current, candidate))
            _pending_publication_hook("after-store")
        if owner._control is None and control is not None:
            owner._control = control
        published = owner._pending_authority
        if candidate is not None and not (
            published is candidate
            or (type(published) is _PendingAuthorityQueue and published._contains(candidate))
        ):
            raise RuntimeError("Relay probe pending authority publication failed")
    _pending_publication_hook("return")


def _pending_publication_hook(_position: str) -> None:
    return None


__all__: list[str] = []
