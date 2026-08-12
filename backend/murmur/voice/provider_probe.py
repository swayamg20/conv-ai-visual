"""Small, injectable HTTP boundary for direct-provider metadata probes.

The Voice V2 profile adapters use provider catalogs to prove that credentials
are accepted and that their selected model or voice is visible.  This module
owns only transport mechanics; provider-specific response interpretation stays
with the profile that knows the corresponding contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class ProviderProbeError(RuntimeError):
    """A metadata request could not provide trustworthy readiness evidence."""


class ProviderProbeTransport(Protocol):
    """Minimal async JSON transport used by provider metadata probes."""

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Any: ...


@dataclass(frozen=True)
class MetadataProbeEvidence:
    """Bounded evidence returned by one provider catalog probe.

    ``proves`` and ``limitations`` are explicit because a successful metadata
    request is not proof of streaming audio quality, latency, quotas, or a
    complete end-to-end call.
    """

    component: str
    provider: str
    selected_resource: str
    observed_resources: tuple[str, ...]
    proves: tuple[str, ...]
    limitations: tuple[str, ...]


class HttpxProviderProbeTransport:
    """Production metadata transport with no retries or shared mutable client."""

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Any:
        try:
            timeout = httpx.Timeout(timeout_seconds)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0),
            ) as client:
                response = await client.get(url, headers=dict(headers))
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderProbeError("provider metadata request failed") from exc
        if not isinstance(payload, dict | list):
            raise ProviderProbeError("provider metadata response must be a JSON object or array")
        return payload


def visible_string_ids(
    payload: Mapping[str, Any],
    *,
    collection_key: str,
    id_keys: tuple[str, ...] = ("id",),
) -> tuple[str, ...]:
    """Extract unique, non-empty identifiers from a catalog response."""

    collection = payload.get(collection_key)
    if not isinstance(collection, list):
        raise ProviderProbeError(
            f"provider metadata response is missing list field {collection_key!r}"
        )
    identifiers: list[str] = []
    for item in collection:
        if not isinstance(item, dict):
            continue
        identifier = next(
            (
                value.strip()
                for key in id_keys
                if isinstance((value := item.get(key)), str) and value.strip()
            ),
            None,
        )
        if identifier is not None and identifier not in identifiers:
            identifiers.append(identifier)
    return tuple(identifiers)
