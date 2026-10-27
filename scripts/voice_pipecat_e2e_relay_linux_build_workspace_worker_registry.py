"""Private registry owning one opaque dormant workspace worker thread."""

from __future__ import annotations

import threading
import weakref

from scripts.voice_pipecat_e2e_relay_linux_build_workspace import (
    _RelayLinuxBuildWorkspaceOwner,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_active import (
    _workspace_worker_active_capacity_occupied,
    _workspace_worker_ownership_locked,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_control import (
    _bind_workspace_worker_control_bridge,
    _clear_workspace_worker_control_bridge_raw,
    _new_workspace_worker_control_bridge,
    _workspace_worker_control_bridge_belongs,
    _workspace_worker_control_bridge_controller,
    _workspace_worker_control_bridge_matches,
    _workspace_worker_thread_configuration_is_complete,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_raw import (
    _THREAD_NAME,
    _THREAD_TOKEN,
    _inert_workspace_worker_target,
    _WorkspaceWorkerThread,
)
from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_state import (
    _scrub_control_minimal,
    _WorkspaceWorkerBundle,
    _WorkspaceWorkerController,
)

_BINDING_TOKEN = object()
_RECEIPT_TOKEN = object()
_RECORD_TOKEN = object()
_REGISTERED = "registered"
_INITIALIZING = "initializing"
_INITIALIZED = "initialized"
_FAILED = "failed"
_POISONED = "poisoned"
_MAX_LIVE_RECORDS = 1
_REGISTRY_LOCK = threading.RLock()
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


class _WorkspaceWorkerThreadRecord:
    """Registry-owned canonical record; its raw thread never leaves this module."""

    __slots__ = (
        "_control_bridge",
        "_control_token",
        "_entry",
        "_lifecycle",
        "_lock",
        "_owner_token",
        "_record_token",
    )

    def __init__(
        self,
        token: object,
        *,
        owner_token: object,
        controller: _WorkspaceWorkerController,
    ) -> None:
        if (
            token is not _RECORD_TOKEN
            or type(owner_token) is not object
            or type(controller) is not _WorkspaceWorkerController
            or not controller._matches(owner_token)
        ):
            raise TypeError("Relay Linux workspace worker record is registry-owned")
        object.__setattr__(self, "_owner_token", owner_token)
        object.__setattr__(self, "_record_token", object())
        object.__setattr__(self, "_control_token", object())
        object.__setattr__(
            self,
            "_control_bridge",
            _new_workspace_worker_control_bridge(
                self._record_token,
                self._control_token,
                owner_token,
                controller,
            ),
        )
        object.__setattr__(self, "_entry", None)
        object.__setattr__(self, "_lifecycle", None)
        object.__setattr__(self, "_lock", threading.RLock())

    def _matches(self, binding: _WorkspaceWorkerThreadBinding) -> bool:
        bridge = self._control_bridge
        controller = binding._controller
        release_phase = getattr(self._lifecycle, "_release_phase", None)
        return bool(
            type(binding) is _WorkspaceWorkerThreadBinding
            and binding._owner_token is self._owner_token
            and controller._matches(self._owner_token)
            and (
                _workspace_worker_control_bridge_matches(
                    bridge,
                    self._record_token,
                    self._control_token,
                    controller,
                )
                or (
                    release_phase in {"intended", "scrubbed", "complete"}
                    and _workspace_worker_control_bridge_belongs(
                        bridge,
                        self._record_token,
                        self._control_token,
                    )
                    and bridge._state is None
                )
            )
        )

    def _advance(
        self,
        binding: _WorkspaceWorkerThreadBinding,
    ) -> tuple[_WorkspaceWorkerThreadReceipt, bool]:
        if not self._matches(binding):
            raise TypeError("Relay Linux workspace worker record binding is invalid")
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
            _workspace_worker_locked_before,
            _workspace_worker_operation_deadline,
        )

        deadline = _workspace_worker_operation_deadline(None)
        with _workspace_worker_locked_before(self._lock, deadline):
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
                            or not _thread_initialization_is_complete(
                                candidate,
                                self._record_token,
                                self._control_bridge,
                                self._control_token,
                                binding._controller,
                            )
                        )
                    )
                    or (
                        state == _FAILED
                        and (
                            receipt._coherent is not False
                            or _thread_initialization_is_complete(
                                candidate,
                                self._record_token,
                                self._control_bridge,
                                self._control_token,
                                binding._controller,
                            )
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

            worker_token = _bind_workspace_worker_control_bridge(
                self._control_bridge,
                self._record_token,
                self._control_token,
                binding._controller,
                candidate,
            )
            object.__setattr__(candidate, "_workspace_control_token", worker_token)
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
                    args=(self._control_bridge,),
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
        object.__setattr__(candidate, "_workspace_sealed", True)
        controller = _workspace_worker_control_bridge_controller(
            self._control_bridge,
            self._record_token,
            self._control_token,
        )
        coherent = _thread_initialization_is_complete(
            candidate,
            self._record_token,
            self._control_bridge,
            self._control_token,
            controller,
        )
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
                        or _thread_initialization_is_complete(
                            candidate,
                            self._record_token,
                            self._control_bridge,
                            self._control_token,
                            binding._controller,
                        )
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
        from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
            _workspace_worker_locked_before,
            _workspace_worker_operation_deadline,
        )

        deadline = _workspace_worker_operation_deadline(None)
        with _workspace_worker_locked_before(self._lock, deadline):
            entry = self._entry
            if entry is not None and entry[0] == _POISONED:
                receipt = entry[2]
                if type(receipt) is not _WorkspaceWorkerThreadReceipt:
                    raise TypeError("Relay Linux workspace worker record is invalid")
                return receipt
            candidate = None if entry is None else entry[1]
            if type(candidate) is _WorkspaceWorkerThread:
                _scrub_workspace_worker_no_effect(self, candidate)
                object.__setattr__(candidate, "_workspace_sealed", True)
            elif candidate is not None:
                raise TypeError("Relay Linux workspace worker record is invalid")
            else:
                _scrub_workspace_worker_no_effect(self, None)
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
    deadline: float | None = None,
) -> _WorkspaceWorkerThreadBinding:
    """Resolve the exact canonical owner/bundle without retaining its paths."""

    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
        _resolve_workspace_worker_thread_binding as resolve,
    )

    return resolve(owner, bundle, deadline)


def _advance_workspace_worker_thread(
    binding: _WorkspaceWorkerThreadBinding,
    deadline: float | None = None,
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
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
        _workspace_worker_binding_deadline,
        _workspace_worker_locked_before,
        _workspace_worker_operation_deadline,
    )

    deadline = _workspace_worker_operation_deadline(deadline)
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_contract import (
        _workspace_worker_bundle_allows_record,
    )

    with _workspace_worker_binding_deadline(deadline):
        with _workspace_worker_ownership_locked(deadline):
            occupied = _workspace_worker_active_capacity_occupied(deadline)
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                if not _workspace_worker_bundle_allows_record(bundle, binding._owner_token):
                    raise RuntimeError("Relay Linux workspace worker lifecycle is complete")
                record = _RECORDS.get(bundle)
                if record is None:
                    if occupied:
                        raise RuntimeError("Relay Linux workspace worker registry is occupied")
                    if len(_RECORDS) >= _MAX_LIVE_RECORDS:
                        raise RuntimeError("Relay Linux workspace worker registry is occupied")
                    candidate = _WorkspaceWorkerThreadRecord(
                        _RECORD_TOKEN,
                        owner_token=binding._owner_token,
                        controller=binding._controller,
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
    deadline: float | None = None,
) -> _WorkspaceWorkerThreadReceipt:
    """Fail closed after cancellation-state failure without exposing the raw thread."""

    if type(binding) is not _WorkspaceWorkerThreadBinding:
        raise TypeError("Relay Linux workspace worker binding is invalid")
    bundle = binding._bundle_ref()
    if type(bundle) is not _WorkspaceWorkerBundle:
        raise TypeError("Relay Linux workspace worker bundle is no longer owned")
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_binding import (
        _workspace_worker_binding_deadline,
        _workspace_worker_locked_before,
        _workspace_worker_operation_deadline,
    )

    deadline = _workspace_worker_operation_deadline(deadline)
    from scripts.voice_pipecat_e2e_relay_linux_build_workspace_worker_contract import (
        _workspace_worker_bundle_allows_record,
    )

    with _workspace_worker_binding_deadline(deadline):
        with _workspace_worker_ownership_locked(deadline):
            occupied = _workspace_worker_active_capacity_occupied(deadline)
            with _workspace_worker_locked_before(_REGISTRY_LOCK, deadline):
                if not _workspace_worker_bundle_allows_record(bundle, binding._owner_token):
                    raise RuntimeError("Relay Linux workspace worker lifecycle is complete")
                record = _RECORDS.get(bundle)
                if record is None:
                    if occupied:
                        raise RuntimeError("Relay Linux workspace worker registry is occupied")
                    if len(_RECORDS) >= _MAX_LIVE_RECORDS:
                        raise RuntimeError("Relay Linux workspace worker registry is occupied")
                    record = _WorkspaceWorkerThreadRecord(
                        _RECORD_TOKEN,
                        owner_token=binding._owner_token,
                        controller=binding._controller,
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


def _thread_initialization_is_complete(
    candidate: object,
    record_token: object,
    control_bridge: object,
    bridge_token: object,
    controller: _WorkspaceWorkerController,
) -> bool:
    """Recognize only the full CPython ``Thread.__init__`` postcondition."""

    return _workspace_worker_thread_configuration_is_complete(
        candidate,
        thread_type=_WorkspaceWorkerThread,
        target=_inert_workspace_worker_target,
        name=_THREAD_NAME,
        record_token=record_token,
        bridge_token=bridge_token,
        control_bridge=control_bridge,
        controller=controller,
    )


def _scrub_workspace_worker_no_effect(
    record: _WorkspaceWorkerThreadRecord,
    raw: _WorkspaceWorkerThread | None,
) -> None:
    """Clear the exact never-started raw configuration and weak bridge binding."""

    if type(record) is not _WorkspaceWorkerThreadRecord or (
        raw is not None and type(raw) is not _WorkspaceWorkerThread
    ):
        raise TypeError("Relay Linux workspace worker record is invalid")
    _clear_workspace_worker_control_bridge_raw(
        record._control_bridge,
        record._record_token,
        record._control_token,
        raw,
    )
    if raw is not None:
        object.__setattr__(raw, "_target", None)
        object.__setattr__(raw, "_args", ())
        object.__setattr__(raw, "_kwargs", {})
        object.__setattr__(raw, "_workspace_control_token", None)
        for shadow in {"is_alive", "join", "run", "start"}.intersection(vars(raw)):
            object.__delattr__(raw, shadow)


__all__: list[str] = []
