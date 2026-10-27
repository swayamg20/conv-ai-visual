"""Opaque, bounded canonical claims attached to one Coturn process."""

from __future__ import annotations

import hashlib
import threading

_MAX_ACTIVE_PUMPS = 256
_REGISTRY_LOCK = threading.Lock()
_PUMPS: dict[object, tuple[object, object]] = {}
_PARTIALS: dict[object, tuple[object, object, object]] = {}


class PumpClaim:
    """Complete process-local snapshot; it never retains evidence inputs."""

    __slots__ = ("fingerprint", "key", "owner", "state")

    def __init__(
        self,
        *,
        state: str = "empty",
        fingerprint: bytes | None = None,
        key: object | None = None,
        owner: object | None = None,
    ) -> None:
        self.state = state
        self.fingerprint = fingerprint
        self.key = key
        self.owner = owner


def new_pump_claim() -> PumpClaim:
    return PumpClaim()


def evidence_input_fingerprint(
    process: object,
    expected_username: object,
    expected_topology: object,
    expected_realm: object,
) -> bytes | None:
    """Validate primitive identities and retain only a one-way comparison value."""

    if type(expected_username) is not str or type(expected_realm) is not str:
        return None
    if not 1 <= len(expected_username) <= 512 or not 1 <= len(expected_realm) <= 127:
        return None
    try:
        username = expected_username.encode("ascii")
        realm = expected_realm.encode("ascii")
        matches = object.__getattribute__(process, "_matches_topology")(expected_topology)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None
    forbidden = b"<>\r\n"
    if (
        not matches
        or any(value < 0x21 or value > 0x7E or value in forbidden for value in username)
        or any(value < 0x21 or value > 0x7E or value in forbidden for value in realm)
    ):
        return None
    digest = hashlib.sha256()
    digest.update(len(username).to_bytes(2, "big"))
    digest.update(username)
    digest.update(len(realm).to_bytes(2, "big"))
    digest.update(realm)
    username = realm = b""
    return digest.digest()


def claim_evidence_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
) -> tuple[str, object | None]:
    """Preclaim construction or recover a published canonical pump key."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if type(claim) is not PumpClaim or type(fingerprint) is not bytes or owner is None:
            return "invalid", None
        if claim.state == "empty":
            record = PumpClaim(
                state="building",
                fingerprint=fingerprint,
                key=object(),
                owner=owner,
            )
            object.__setattr__(process, "_pump_claim", record)
            return "claimed", record.key
        if claim.fingerprint != fingerprint or claim.key is None:
            return "invalid", None
        with _REGISTRY_LOCK:
            published = _PUMPS.get(claim.key)
            partial = _PARTIALS.get(claim.key)
        if published is not None:
            published_owner, _pump = published
            if published_owner is not claim.owner:
                return "invalid", None
            if partial is not None:
                if partial[:2] != published:
                    return "invalid", None
                with _REGISTRY_LOCK:
                    _PARTIALS.pop(claim.key, None)
            if claim.state != "published":
                claim = PumpClaim(
                    state="published",
                    fingerprint=claim.fingerprint,
                    key=claim.key,
                    owner=claim.owner,
                )
                object.__setattr__(process, "_pump_claim", claim)
            return "published", claim.key
        if claim.state == "building" and claim.owner is owner:
            return "claimed", claim.key
        return "busy", None


def publish_evidence_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
    key: object,
    pump: object,
) -> bool:
    """Publish behind the already process-owned key, reconciling return loss."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if (
            type(claim) is not PumpClaim
            or claim.state not in {"building", "published"}
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is not key
            or pump is None
        ):
            return False
        with _REGISTRY_LOCK:
            current = _PUMPS.get(key)
            partial = _PARTIALS.get(key)
            if current is None:
                if partial is not None and (partial[0] is not owner or partial[1] is not pump):
                    return False
                if partial is None and len(_PUMPS.keys() | _PARTIALS.keys()) >= _MAX_ACTIVE_PUMPS:
                    return False
                _PUMPS[key] = (owner, pump)
            elif current != (owner, pump):
                return False
            if partial is not None:
                _PARTIALS.pop(key, None)
        record = PumpClaim(
            state="published",
            fingerprint=fingerprint,
            key=key,
            owner=owner,
        )
        object.__setattr__(process, "_pump_claim", record)
        return True


def release_unpublished_pump(process: object, owner: object, key: object) -> bool:
    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if type(claim) is not PumpClaim:
            return False
        if claim.state == "empty":
            return True
        if claim.state != "building" or claim.owner is not owner or claim.key is not key:
            return False
        with _REGISTRY_LOCK:
            if key in _PUMPS or key in _PARTIALS:
                return False
        object.__setattr__(process, "_pump_claim", PumpClaim())
        return True


def evidence_pump_claim_status(
    process: object,
    fingerprint: bytes,
    owner: object,
    key: object,
) -> str:
    """Return one authoritative state before a caller may scrub a candidate."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if (
            type(claim) is not PumpClaim
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is not key
        ):
            return "invalid"
        with _REGISTRY_LOCK:
            published = _PUMPS.get(key)
        if published is not None:
            return "published" if published[0] is owner else "invalid"
        return claim.state


def retain_partial_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
    key: object,
    pump: object,
    parser: object,
) -> bool:
    """Graph-opaquely retain an un-scrubbed candidate behind its process key."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if (
            type(claim) is not PumpClaim
            or claim.state != "building"
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is not key
            or parser is None
        ):
            return False
        with _REGISTRY_LOCK:
            current = _PARTIALS.get(key)
            if current is None:
                if len(_PUMPS.keys() | _PARTIALS.keys()) >= _MAX_ACTIVE_PUMPS:
                    return False
                _PARTIALS[key] = (owner, pump, parser)
                return True
            return current == (owner, pump, parser)


def retained_partial_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
) -> tuple[object, object] | None:
    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if (
            type(claim) is not PumpClaim
            or claim.state != "building"
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is None
        ):
            return None
        with _REGISTRY_LOCK:
            current = _PARTIALS.get(claim.key)
        if current is None or current[0] is not owner:
            return None
        return current[1], current[2]


def release_scrubbed_partial_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
) -> bool:
    """Release a retained partial only after its caller proved raw-state scrub."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if type(claim) is not PumpClaim:
            return False
        if claim.state == "empty":
            return True
        if (
            claim.state not in {"building", "scrubbed"}
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is None
        ):
            return False
        if claim.state == "building":
            record = PumpClaim(
                state="scrubbed",
                fingerprint=claim.fingerprint,
                key=claim.key,
                owner=claim.owner,
            )
            object.__setattr__(process, "_pump_claim", record)
            claim = record
        with _REGISTRY_LOCK:
            _PARTIALS.pop(claim.key, None)
        object.__setattr__(process, "_pump_claim", PumpClaim())
        return True


def finish_scrubbed_partial_pump(
    process: object,
    fingerprint: bytes,
    owner: object,
) -> bool:
    """Reconcile a cut after scrub proof was atomically marked."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if type(claim) is not PumpClaim:
            return False
        if claim.state == "empty":
            return True
        if (
            claim.state != "scrubbed"
            or claim.fingerprint != fingerprint
            or claim.owner is not owner
            or claim.key is None
        ):
            return False
        with _REGISTRY_LOCK:
            _PARTIALS.pop(claim.key, None)
        object.__setattr__(process, "_pump_claim", PumpClaim())
        return True


def release_evidence_pump(
    process: object,
    owner: object,
    pump: object,
) -> bool:
    """Remove the graph-bearing entry only after the pump is terminal."""

    lock = object.__getattribute__(process, "_pump_operation_lock")
    with lock:
        claim = object.__getattribute__(process, "_pump_claim")
        if type(claim) is not PumpClaim or claim.owner is not owner:
            return bool(type(claim) is PumpClaim and claim.state == "terminal")
        key = claim.key
        if key is None:
            return False
        with _REGISTRY_LOCK:
            current = _PUMPS.get(key)
            if current is not None and current != (owner, pump):
                return False
            if current is not None:
                del _PUMPS[key]
            _PARTIALS.pop(key, None)
        record = PumpClaim(
            state="terminal",
            fingerprint=claim.fingerprint,
            key=key,
            owner=owner,
        )
        object.__setattr__(process, "_pump_claim", record)
        return True


def return_canonical_pump(key: object) -> object | None:
    with _REGISTRY_LOCK:
        current = _PUMPS.get(key)
        if current is None:
            raise RuntimeError("Coturn evidence pump is unavailable")
        partial = _PARTIALS.get(key)
        if partial is not None:
            if partial[:2] != current:
                raise RuntimeError("Coturn evidence pump is unavailable")
            _PARTIALS.pop(key, None)
        return current[1]


def active_pump_count() -> int:
    with _REGISTRY_LOCK:
        return len(_PUMPS.keys() | _PARTIALS.keys())
