"""Stable private resource identity for active relay aggregate exclusion."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from scripts.voice_pipecat_e2e_coturn_host import CoturnRuntimePaths, RuntimeIdentity

_ResourceKey = tuple[str, int, int, str, str, str, str, str]


def _resource_key(
    paths: CoturnRuntimePaths,
    identity: RuntimeIdentity,
) -> _ResourceKey:
    if type(paths) is not CoturnRuntimePaths or type(identity) is not RuntimeIdentity:
        raise TypeError("Relay probe resource identity is invalid")
    control_dir = paths.control_dir
    try:
        if not isinstance(control_dir, Path):
            raise TypeError
        resolved = control_dir.resolve(strict=True)
        details = resolved.stat(follow_symlinks=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise TypeError("Relay probe resource identity is invalid") from None
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
        or type(details.st_dev) is not int
        or details.st_dev < 0
        or type(details.st_ino) is not int
        or details.st_ino <= 0
    ):
        raise TypeError("Relay probe resource identity is invalid")
    key = (
        os.fspath(resolved),
        details.st_dev,
        details.st_ino,
        identity.run_id,
        identity.owner_nonce,
        identity.network_name,
        identity.container_name,
        identity.bridge_name,
    )
    if any(type(value) is not str or not value for value in (*key[:1], *key[3:])):
        raise TypeError("Relay probe resource identity is invalid")
    return key


def _resources_conflict(
    left: _ResourceKey,
    right: _ResourceKey,
) -> bool:
    if not _valid_key(left) or not _valid_key(right):
        return True
    return bool(
        left[0] == right[0]
        or (left[1], left[2]) == (right[1], right[2])
        or left[5] == right[5]
        or left[6] == right[6]
        or left[7] == right[7]
    )


def _valid_key(value: object) -> bool:
    return bool(
        type(value) is tuple
        and len(value) == 8
        and all(type(item) is str and item for item in (*value[:1], *value[3:]))
        and type(value[1]) is int
        and value[1] >= 0
        and type(value[2]) is int
        and value[2] > 0
    )


__all__: list[str] = []
