"""Deterministic hashing primitives for semantic compiler certificates.

``murmur-json-v1`` is intentionally a small, server-local canonicalization
contract rather than a claim of RFC 8785 compatibility.  It sorts object keys,
uses compact separators, emits UTF-8 without ASCII escaping, and rejects
non-finite numbers.  Every digest is domain-separated so the same JSON value
cannot be substituted across artifact types.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Final

SEMANTIC_CANONICALIZATION: Final = "murmur-json-v1"
SEMANTIC_HASH_ALGORITHM: Final = "sha256"

SCENE_PATCH_HASH_DOMAIN: Final = "murmur:scene-patch:v1"
TEACHING_BEAT_HASH_DOMAIN: Final = "murmur:teaching-beat:v1"
VERIFICATION_RECEIPT_HASH_DOMAIN: Final = "murmur:verification-receipt:v1"
SEMANTIC_SCENE_HASH_DOMAIN: Final = "murmur:semantic-scene:v1"
COMPILER_CERTIFICATE_HASH_DOMAIN: Final = "murmur:compiler-certificate:v1"


def canonical_json_v1(value: object) -> bytes:
    """Serialize one JSON-compatible value according to ``murmur-json-v1``."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object, *, domain: str) -> str:
    """Return a domain-separated SHA-256 digest of canonical JSON bytes."""

    payload = domain.encode("utf-8") + b"\0" + canonical_json_v1(value)
    return hashlib.sha256(payload).hexdigest()


def digest_matches(actual: str, expected: str) -> bool:
    """Compare two hexadecimal digests without data-dependent early exit."""

    return hmac.compare_digest(actual, expected)
