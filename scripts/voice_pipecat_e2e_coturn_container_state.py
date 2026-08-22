"""Pure Docker container state predicates for the Coturn lifecycle."""

from __future__ import annotations

import re

_ZERO_TIME = "0001-01-01T00:00:00Z"
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$"
)
_STATE_KEYS = {
    "Dead",
    "Error",
    "ExitCode",
    "FinishedAt",
    "OOMKilled",
    "Paused",
    "Pid",
    "Restarting",
    "Running",
    "StartedAt",
    "Status",
}


def is_created_state(value: object) -> bool:
    expected = {
        "Status": "created",
        "Running": False,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "Pid": 0,
        "ExitCode": 0,
        "Error": "",
        "StartedAt": _ZERO_TIME,
        "FinishedAt": _ZERO_TIME,
    }
    return bool(isinstance(value, dict) and value.keys() == _STATE_KEYS and value == expected)


def is_running_state(value: object) -> bool:
    expected = {
        "Status": "running",
        "Running": True,
        "Paused": False,
        "Restarting": False,
        "OOMKilled": False,
        "Dead": False,
        "ExitCode": 0,
        "Error": "",
        "FinishedAt": _ZERO_TIME,
    }
    return bool(
        isinstance(value, dict)
        and value.keys() == _STATE_KEYS
        and all(value.get(key) == item for key, item in expected.items())
        and isinstance(value.get("Pid"), int)
        and not isinstance(value["Pid"], bool)
        and value["Pid"] > 0
        and isinstance(value.get("StartedAt"), str)
        and value["StartedAt"] != _ZERO_TIME
        and _TIMESTAMP.fullmatch(value["StartedAt"])
    )


def cleanup_running_state(value: object) -> bool | None:
    """Return the running bit only for a coherent removable/stoppable state."""

    if not isinstance(value, dict) or value.keys() != _STATE_KEYS:
        return None
    if is_running_state(value):
        return True
    if is_created_state(value):
        return False
    status = value.get("Status")
    pid = value.get("Pid")
    if (
        value.get("Running") is not False
        or value.get("Paused") is not False
        or value.get("Restarting") is not False
        or not isinstance(value.get("OOMKilled"), bool)
        or not isinstance(value.get("Dead"), bool)
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid != 0
        or isinstance(value.get("ExitCode"), bool)
        or not isinstance(value.get("ExitCode"), int)
        or not isinstance(value.get("Error"), str)
        or len(value["Error"]) > 1_024
        or not isinstance(value.get("StartedAt"), str)
        or not _TIMESTAMP.fullmatch(value["StartedAt"])
        or not isinstance(value.get("FinishedAt"), str)
        or not _TIMESTAMP.fullmatch(value["FinishedAt"])
        or status not in {"exited", "dead"}
        or (status == "dead") != value["Dead"]
        or value["StartedAt"] == _ZERO_TIME
        or value["FinishedAt"] == _ZERO_TIME
    ):
        return None
    return False


__all__ = ["cleanup_running_state", "is_created_state", "is_running_state"]
