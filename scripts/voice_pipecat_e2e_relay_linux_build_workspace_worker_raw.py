"""Private sealed raw Thread for the filesystem-inert workspace worker."""

from __future__ import annotations

import threading

from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_control import (
    _capture_worker_control,
    _inert_workspace_worker_target,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
)

_THREAD_TOKEN = object()
_THREAD_NAME = "relay-linux-build-workspace-worker"


class _WorkspaceWorkerThread(threading.Thread):
    """Sealed raw thread whose lifecycle remains registry-private."""

    _CONFIGURATION_FIELDS = frozenset(
        {
            "_args",
            "_context",
            "_daemonic",
            "_kwargs",
            "_name",
            "_target",
            "_workspace_control_token",
            "daemon",
            "is_alive",
            "join",
            "name",
            "run",
            "start",
        }
    )

    def __new__(cls, token: object) -> _WorkspaceWorkerThread:
        if cls is not _WorkspaceWorkerThread or token is not _THREAD_TOKEN:
            raise TypeError("Relay Linux workspace worker thread is registry-owned")
        value = super().__new__(cls)
        object.__setattr__(value, "_workspace_sealed", True)
        return value

    def __init__(self, token: object) -> None:
        if token is not _THREAD_TOKEN:
            raise TypeError("Relay Linux workspace worker thread is registry-owned")

    def __init_subclass__(cls, **_kwargs: object) -> None:
        del cls
        raise TypeError("Relay Linux workspace worker thread cannot be subclassed")

    def start(self) -> None:
        raise RuntimeError("Relay Linux workspace worker lifecycle is not installed")

    def join(self, timeout: float | None = None) -> None:
        del timeout
        raise RuntimeError("Relay Linux workspace worker lifecycle is not installed")

    def run(self) -> None:
        if threading.current_thread() is not self:
            raise RuntimeError("Relay Linux workspace worker lifecycle is not installed")
        bridge = None
        try:
            target = object.__getattribute__(self, "_target")
            args = object.__getattribute__(self, "_args")
            kwargs = object.__getattribute__(self, "_kwargs")
            if (
                target is not _inert_workspace_worker_target
                or type(args) is not tuple
                or len(args) != 1
            ):
                raise RuntimeError("Relay Linux workspace worker target is invalid")
            bridge = args[0]
            target(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit) as control:
            _capture_worker_control(bridge, control)
        except BaseException as failure:
            _scrub_control_minimal(failure)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerThread()"

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_workspace_sealed" or (
            name in self._CONFIGURATION_FIELDS
            and object.__getattribute__(self, "_workspace_sealed")
        ):
            raise AttributeError("Relay Linux workspace worker thread is sealed")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name == "_workspace_sealed" or (
            name in self._CONFIGURATION_FIELDS
            and object.__getattribute__(self, "_workspace_sealed")
        ):
            raise AttributeError("Relay Linux workspace worker thread is sealed")
        object.__delattr__(self, name)

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker thread cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker thread cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker thread cannot be serialized")

    def __reduce_ex__(self, _protocol: int) -> None:
        raise TypeError("Relay Linux workspace worker thread cannot be serialized")


__all__: list[str] = []
