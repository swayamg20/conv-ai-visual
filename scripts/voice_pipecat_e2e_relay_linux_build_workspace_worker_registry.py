"""Private registry for one dormant relay workspace worker thread per owner.

The registry, rather than the workspace bundle or its caller-visible
destinations, owns the raw ``threading.Thread``.  Construction initializes the
thread but never starts or joins it.  The record and receipt retain opaque keys
only; neither retains the workspace request or its path graph.
"""

from __future__ import annotations

import threading
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_BINDING_TOKEN = object()
_RECEIPT_TOKEN = object()
_RECORD_TOKEN = object()
_THREAD_TOKEN = object()
_REGISTERED = "registered"
_INITIALIZING = "initializing"
_INITIALIZED = "initialized"
_FAILED = "failed"
_POISONED = "poisoned"
_THREAD_NAME = "relay-linux-build-workspace-worker"
_MAX_LIVE_RECORDS = 1
_REGISTRY_LOCK = threading.Lock()
_CAPTURE_CONTROL = _WorkspaceWorkerController._capture_control
_RECORDS: weakref.WeakKeyDictionary[
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerThreadRecord,
] = weakref.WeakKeyDictionary()


class _WorkspaceWorkerThreadBinding:
    """Validated owner binding carrying no request or path authority."""

    __slots__ = ("_bundle_ref", "_controller", "_owner_token")

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        controller: _WorkspaceWorkerController,
        bundle: _WorkspaceWorkerBundle,
    ) -> None:
        if (
            token is not _BINDING_TOKEN
            or type(owner_token) is not object
            or type(controller) is not _WorkspaceWorkerController
            or type(bundle) is not _WorkspaceWorkerBundle
            or not controller._matches(owner_token)
        ):
            raise TypeError("Relay Linux workspace worker binding is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_controller", controller)
        object.__setattr__(self, "_bundle_ref", weakref.ref(bundle))

    def _matches(self, other: _WorkspaceWorkerThreadBinding) -> bool:
        return bool(
            type(other) is _WorkspaceWorkerThreadBinding
            and other._owner_token is self._owner_token
            and other._controller is self._controller
            and other._bundle_ref() is self._bundle_ref()
            and type(self._bundle_ref()) is _WorkspaceWorkerBundle
            and self._controller._matches(self._owner_token)
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerThreadBinding()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker binding is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker binding cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker binding cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker binding cannot be serialized")


class _WorkspaceWorkerThreadReceipt:
    """Opaque proof that the registry reached one terminal construction state."""

    __slots__ = ("_coherent", "_owner_token", "_record_token")

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        record_token: object,
        coherent: bool,
    ) -> None:
        if (
            token is not _RECEIPT_TOKEN
            or type(owner_token) is not object
            or type(record_token) is not object
            or type(coherent) is not bool
        ):
            raise TypeError("Relay Linux workspace worker receipt is factory-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", record_token)
        object.__setattr__(self, "_coherent", coherent)

    def _matches(self, owner_token: object, record_token: object) -> bool:
        return bool(
            owner_token is self._owner_token
            and record_token is self._record_token
            and type(self._coherent) is bool
        )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerThreadReceipt()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker receipt is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker receipt cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker receipt cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker receipt cannot be serialized")


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
            "daemon",
            "name",
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
        raise RuntimeError("Relay Linux workspace worker lifecycle is not installed")

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


class _WorkspaceWorkerThreadRecord:
    """Registry-owned canonical record; its raw thread never leaves this module."""

    __slots__ = ("_entry", "_lock", "_owner_token", "_record_token")

    def __init__(self, token: object, *, owner_token: object) -> None:
        if token is not _RECORD_TOKEN or type(owner_token) is not object:
            raise TypeError("Relay Linux workspace worker record is registry-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", object())
        object.__setattr__(self, "_entry", None)
        object.__setattr__(self, "_lock", threading.RLock())

    def _matches(self, binding: _WorkspaceWorkerThreadBinding) -> bool:
        return bool(
            type(binding) is _WorkspaceWorkerThreadBinding
            and binding._owner_token is self._owner_token
            and binding._controller._matches(self._owner_token)
        )

    def _advance(
        self,
        binding: _WorkspaceWorkerThreadBinding,
    ) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
        if not self._matches(binding):
            raise TypeError("Relay Linux workspace worker record binding is invalid")
        with self._lock:
            entry = self._entry
            if entry is None:
                candidate = _WorkspaceWorkerThread.__new__(
                    _WorkspaceWorkerThread,
                    _THREAD_TOKEN,
                )
                object.__setattr__(
                    self,
                    "_entry",
                    (_REGISTERED, candidate, None),
                )
                entry = self._entry
            state, candidate, receipt = entry
            if state in {_INITIALIZED, _FAILED, _POISONED}:
                if (
                    type(receipt) is not _WorkspaceWorkerThreadReceipt
                    or not receipt._matches(self._owner_token, self._record_token)
                    or (
                        state == _INITIALIZED
                        and (
                            receipt._coherent is not True
                            or not _thread_initialization_is_complete(candidate)
                        )
                    )
                    or (
                        state == _FAILED
                        and (
                            receipt._coherent is not False
                            or _thread_initialization_is_complete(candidate)
                        )
                    )
                    or (
                        state == _POISONED
                        and (
                            type(receipt._coherent) is not bool
                            or (
                                candidate is not None
                                and (
                                    type(candidate) is not _WorkspaceWorkerThread
                                    or vars(candidate).get("_target") is not None
                                    or object.__getattribute__(
                                        candidate,
                                        "_workspace_sealed",
                                    )
                                    is not True
                                )
                            )
                        )
                    )
                ):
                    raise TypeError("Relay Linux workspace worker record is invalid")
                return receipt, state == _INITIALIZED
            if state == _INITIALIZING:
                if type(candidate) is not _WorkspaceWorkerThread or receipt is not None:
                    raise TypeError("Relay Linux workspace worker record is invalid")
                return self._terminalize_with_controls(binding, candidate)
            if state != _REGISTERED or type(candidate) is not _WorkspaceWorkerThread:
                raise TypeError("Relay Linux workspace worker record is invalid")

            object.__setattr__(
                self,
                "_entry",
                (_INITIALIZING, candidate, None),
            )
            object.__setattr__(candidate, "_workspace_sealed", False)
            try:
                threading.Thread.__init__(
                    candidate,
                    target=_inert_workspace_worker_target,
                    name=_THREAD_NAME,
                    daemon=False,
                )
            except (KeyboardInterrupt, SystemExit) as control:
                return self._terminalize_with_controls(
                    binding,
                    candidate,
                    first=control,
                )
            except BaseException:
                self._terminalize_with_controls(binding, candidate)
                raise
            return self._terminalize_with_controls(binding, candidate)

    def _terminalize(
        self,
        candidate: _WorkspaceWorkerThread,
    ) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
        coherent = _thread_initialization_is_complete(candidate)
        object.__setattr__(candidate, "_workspace_sealed", True)
        receipt = _WorkspaceWorkerThreadReceipt(
            _RECEIPT_TOKEN,
            owner_token=self._owner_token,
            record_token=self._record_token,
            coherent=coherent,
        )
        state = _INITIALIZED if coherent else _FAILED
        object.__setattr__(self, "_entry", (state, candidate, receipt))
        return receipt, coherent

    def _terminalize_with_controls(
        self,
        binding: _WorkspaceWorkerThreadBinding,
        candidate: _WorkspaceWorkerThread,
        first: KeyboardInterrupt | SystemExit | None = None,
    ) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
        retained: list[KeyboardInterrupt | SystemExit] = [] if first is None else [first]
        while True:
            try:
                while retained:
                    control = retained[0]
                    try:
                        binding._controller._capture_control(control)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as failure:
                        _scrub_control_minimal(failure)
                        _capture_workspace_worker_control(
                            binding._controller,
                            control,
                        )
                    del retained[0]
                entry = self._entry
                if entry is not None and entry[0] in {_INITIALIZED, _FAILED}:
                    if (
                        entry[1] is not candidate
                        or type(entry[2]) is not _WorkspaceWorkerThreadReceipt
                        or not entry[2]._matches(
                            self._owner_token,
                            self._record_token,
                        )
                        or entry[2]._coherent is not (entry[0] == _INITIALIZED)
                        or _thread_initialization_is_complete(candidate)
                        is not (entry[0] == _INITIALIZED)
                    ):
                        raise TypeError("Relay Linux workspace worker record is invalid")
                    return entry[2], entry[0] == _INITIALIZED
                return self._terminalize(candidate)
            except (KeyboardInterrupt, SystemExit) as nested:
                retained.append(nested)

    def _poison(
        self,
        binding: _WorkspaceWorkerThreadBinding,
    ) -> _WorkspaceWorkerThreadReceipt:
        if not self._matches(binding):
            raise TypeError("Relay Linux workspace worker record binding is invalid")
        with self._lock:
            entry = self._entry
            if entry is not None and entry[0] == _POISONED:
                receipt = entry[2]
                if type(receipt) is not _WorkspaceWorkerThreadReceipt:
                    raise TypeError("Relay Linux workspace worker record is invalid")
                return receipt
            candidate = None if entry is None else entry[1]
            if type(candidate) is _WorkspaceWorkerThread:
                object.__setattr__(candidate, "_target", None)
                object.__setattr__(candidate, "_workspace_sealed", True)
            receipt = None if entry is None else entry[2]
            if type(receipt) is not _WorkspaceWorkerThreadReceipt or not receipt._matches(
                self._owner_token,
                self._record_token,
            ):
                receipt = _WorkspaceWorkerThreadReceipt(
                    _RECEIPT_TOKEN,
                    owner_token=self._owner_token,
                    record_token=self._record_token,
                    coherent=False,
                )
            object.__setattr__(self, "_entry", (_POISONED, candidate, receipt))
            return receipt

    def _contains(
        self,
        binding: _WorkspaceWorkerThreadBinding,
        candidate: object,
    ) -> bool:
        if not self._matches(binding):
            return False
        with self._lock:
            return bool(
                self._entry is not None
                and self._entry[0] in {_REGISTERED, _INITIALIZING, _INITIALIZED, _FAILED, _POISONED}
                and self._entry[1] is candidate
            )

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "_WorkspaceWorkerThreadRecord()"

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay Linux workspace worker record is immutable")

    def __copy__(self) -> None:
        raise TypeError("Relay Linux workspace worker record cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay Linux workspace worker record cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay Linux workspace worker record cannot be serialized")


def _resolve_workspace_worker_thread_binding(
    owner: _RelayLinuxBuildWorkspaceOwner,
    bundle: _WorkspaceWorkerBundle,
) -> _WorkspaceWorkerThreadBinding:
    """Resolve the exact canonical owner/bundle without retaining its paths."""

    try:
        if (
            type(owner) is not _RelayLinuxBuildWorkspaceOwner
            or type(bundle) is not _WorkspaceWorkerBundle
            or not owner._cleanup_authority._matches(owner._request)
        ):
            raise TypeError
        owner_token = owner._cleanup_authority._key
        if (
            type(owner_token) is not object
            or owner._worker_bundle_destination._read(owner._request) is not bundle
            or not bundle._matches(owner_token, owner._receipt_destination)
            or type(bundle._controller) is not _WorkspaceWorkerController
        ):
            raise TypeError
        return _WorkspaceWorkerThreadBinding(
            _BINDING_TOKEN,
            owner_token=owner_token,
            controller=bundle._controller,
            bundle=bundle,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as error:
        _scrub_control_minimal(error)
        raise TypeError("Relay Linux workspace worker owner binding is invalid") from None


def _advance_workspace_worker_thread(
    binding: _WorkspaceWorkerThreadBinding,
) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
    """Initialize, but never start, the binding's one registry-owned thread."""

    if (
        type(binding) is not _WorkspaceWorkerThreadBinding
        or type(binding._owner_token) is not object
        or type(binding._controller) is not _WorkspaceWorkerController
        or not binding._controller._matches(binding._owner_token)
    ):
        raise TypeError("Relay Linux workspace worker binding is invalid")
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        raise TypeError("Relay Linux workspace worker bundle is no longer owned")
    with _REGISTRY_LOCK:
        record = _RECORDS.get(bundle)
        if record is None:
            if len(_RECORDS) >= _MAX_LIVE_RECORDS:
                raise RuntimeError("Relay Linux workspace worker registry is occupied")
            candidate = _WorkspaceWorkerThreadRecord(
                _RECORD_TOKEN,
                owner_token=binding._owner_token,
            )
            _RECORDS[bundle] = candidate
            record = _RECORDS[bundle]
        if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(binding):
            raise TypeError("Relay Linux workspace worker registry is invalid")
    return record._advance(binding)


def _is_registered_workspace_worker_thread(
    binding: _WorkspaceWorkerThreadBinding,
    candidate: object,
) -> bool:
    """Return only registration identity, never the registered raw thread."""

    if type(binding) is not _WorkspaceWorkerThreadBinding:
        return False
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        return False
    with _REGISTRY_LOCK:
        record = _RECORDS.get(bundle)
        if type(record) is not _WorkspaceWorkerThreadRecord:
            return False
    return record._contains(binding, candidate)


def _poison_workspace_worker_thread(
    binding: _WorkspaceWorkerThreadBinding,
) -> _WorkspaceWorkerThreadReceipt:
    """Fail closed after cancellation-state failure without exposing the raw thread."""

    if type(binding) is not _WorkspaceWorkerThreadBinding:
        raise TypeError("Relay Linux workspace worker binding is invalid")
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        raise TypeError("Relay Linux workspace worker bundle is no longer owned")
    with _REGISTRY_LOCK:
        record = _RECORDS.get(bundle)
        if record is None:
            if len(_RECORDS) >= _MAX_LIVE_RECORDS:
                raise RuntimeError("Relay Linux workspace worker registry is occupied")
            record = _WorkspaceWorkerThreadRecord(
                _RECORD_TOKEN,
                owner_token=binding._owner_token,
            )
            _RECORDS[bundle] = record
        if type(record) is not _WorkspaceWorkerThreadRecord or not record._matches(binding):
            raise TypeError("Relay Linux workspace worker registry is invalid")
    return record._poison(binding)


def _capture_workspace_worker_control(
    controller: _WorkspaceWorkerController,
    control: KeyboardInterrupt | SystemExit,
) -> None:
    """Use the frozen controller implementation after a dynamic dispatch fault."""

    if type(controller) is not _WorkspaceWorkerController or not isinstance(
        control,
        (KeyboardInterrupt, SystemExit),
    ):
        raise TypeError("Relay Linux workspace worker control capture is invalid")
    _CAPTURE_CONTROL(controller, control)


def _inert_workspace_worker_target() -> None:
    """Hardcoded dormant target; this checkpoint exposes no way to run it."""


def _thread_initialization_is_complete(candidate: object) -> bool:
    """Recognize only the full CPython ``Thread.__init__`` postcondition."""

    try:
        if type(candidate) is not _WorkspaceWorkerThread:
            return False
        values = vars(candidate)
        started = values.get("_started")
        name = values.get("_name")
        args = values.get("_args")
        kwargs = values.get("_kwargs")
        return bool(
            values.get("_initialized") is True
            and values.get("_target") is _inert_workspace_worker_target
            and type(name) is str
            and name == _THREAD_NAME
            and type(args) is tuple
            and not args
            and type(kwargs) is dict
            and not kwargs
            and values.get("_daemonic") is False
            and values.get("_ident") is None
            and values.get("_native_id") is None
            and type(started) is threading.Event
            and not started.is_set()
            and values.get("_is_stopped", False) is False
            and values.get("_tstate_lock") is None
            and ("_tstate_lock" in values or "_os_thread_handle" in values)
            and "_stderr" in values
            and callable(values.get("_invoke_excepthook"))
            and candidate in threading._dangling
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


__all__: list[str] = []
