"""Sanitized supervisor admission and raw-kernel handoff authorities."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import BinaryIO

from scripts.voice_pipecat_e2e_coturn_subprocess_process_io import SelectorFactory
from scripts.voice_pipecat_e2e_coturn_subprocess_request import SupervisorRequest
from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import (
    PopenFactory,
    ThreadFactory,
    resolve_uncommitted_thread,
    thread_started,
    wait_thread_receipt,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_state import ControllerState, Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    ControlSignal,
    control_signal,
    raise_control,
)

GroupSignal = Callable[[int, int], None]
GroupExists = Callable[[int], bool]
GroupIdentity = Callable[[int], int]
SetBlocking = Callable[[BinaryIO, bool], None]
Clock = Callable[[], float]
SupervisorEntry = Callable[[object], None]
_KERNEL_TAKE_TIMEOUT_SECONDS = 0.1


class SupervisorSeams:
    __slots__ = (
        "clock",
        "factory",
        "group_exists",
        "group_identity",
        "selector_factory",
        "set_blocking",
        "signal_group",
        "thread_factory",
    )

    def __init__(
        self,
        *,
        factory: PopenFactory,
        signal_group: GroupSignal,
        group_exists: GroupExists,
        group_identity: GroupIdentity,
        selector_factory: SelectorFactory,
        set_blocking: SetBlocking,
        clock: Clock,
        thread_factory: ThreadFactory,
    ) -> None:
        values = (
            factory,
            signal_group,
            group_exists,
            group_identity,
            selector_factory,
            set_blocking,
            clock,
            thread_factory,
        )
        if not all(callable(value) for value in values):
            raise TypeError
        self.factory = factory
        self.signal_group = signal_group
        self.group_exists = group_exists
        self.group_identity = group_identity
        self.selector_factory = selector_factory
        self.set_blocking = set_blocking
        self.clock = clock
        self.thread_factory = thread_factory


class SupervisorSlot:
    """Sanitized join authority reserved before any raw kernel exists."""

    __slots__ = (
        "_admission_cancelled",
        "_admission_open",
        "_lock",
        "_start_committed",
        "controller",
        "launch",
        "thread",
    )

    def __init__(
        self,
        *,
        controller: ControllerState,
        thread: threading.Thread | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._admission_open = True
        self._admission_cancelled = False
        self._start_committed = False
        self.controller = controller
        self.launch: SupervisorLaunch | None = None
        self.thread: threading.Thread | None = thread

    def publish_kernel_launch(
        self,
        launch: SupervisorLaunch,
        token: object,
        kernel: SupervisorKernel,
    ) -> bool:
        with self._lock:
            if not self._admission_open or self._admission_cancelled or self.launch is not None:
                self._admission_open = False
                return False
            self.launch = launch
            _kernel_publication_midpoint()
            with _KERNELS_LOCK:
                if token in _KERNELS:
                    self.launch = None
                    self._admission_open = False
                    return False
                _KERNELS[token] = kernel
            self._admission_open = False
            return True

    def cancel_admission(self) -> bool:
        """Tombstone an unpublished admission without releasing its runner slot."""

        with self._lock:
            if not self._admission_open or self.launch is not None or self.thread is not None:
                return False
            self._admission_cancelled = True
            return True

    def admission_cancelled(self) -> bool:
        with self._lock:
            return self._admission_cancelled

    def close_admission(self) -> None:
        while True:
            try:
                with self._lock:
                    self._admission_open = False
                return
            except (KeyboardInterrupt, SystemExit) as error:
                self.controller.capture_control(error)
            except BaseException:
                continue

    def reservation_removable(self) -> bool:
        with self._lock:
            return not self._admission_open and self.launch is None and self.thread is None

    def drop_reservation(
        self,
        runner_lock: threading.Lock,
        slots: list[SupervisorSlot],
        entry: Callable[[], None],
        dropped: Callable[[], None],
    ) -> None:
        try: return self._drop_reservation_retry(runner_lock, slots, entry, dropped)  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            self.controller.capture_control(error)
        except BaseException:
            pass
        self._drop_reservation_retry(runner_lock, slots, entry, dropped)

    def _drop_reservation_retry(
        self,
        runner_lock: threading.Lock,
        slots: list[SupervisorSlot],
        entry: Callable[[], None],
        dropped: Callable[[], None],
    ) -> None:
        while True:
            try:
                entry()
                with runner_lock:
                    if self not in slots or not self.reservation_removable():
                        return
                    slots.remove(self)
                    dropped()
                    return
            except (KeyboardInterrupt, SystemExit) as error:
                self.controller.capture_control(error)
            except BaseException:
                continue

    def pending_launch(self) -> SupervisorLaunch | None:
        with self._lock:
            return self.launch

    def cancel_pending_launch(self) -> bool:
        """Claim and finish cancellation while start cannot consume the token."""

        with self._lock:
            launch = self.launch
            if launch is None:
                return True
            cancel_supervisor_launch(launch)
            if launch.token is not None:
                return False
            self.launch = None
            while True:
                try:
                    _slot_launch_released()
                    return True
                except (KeyboardInterrupt, SystemExit) as error:
                    launch.controller.capture_control(error)
                except BaseException:
                    launch.controller.fail("Coturn subprocess supervisor start failed")
                    return True

    def start_launch(
        self,
        launch: SupervisorLaunch,
        entry: SupervisorEntry,
    ) -> tuple[threading.Thread | None, bool]:
        """Serialize launch consumption against cancellation of its kernel."""

        controller = launch.controller
        thread: threading.Thread | None = None
        with self._lock:
            token = launch.token
            if self.launch is not launch or token is None or self.thread is not None:
                return None, False
            try:
                thread = launch.thread_factory(
                    target=entry,
                    args=(token,),
                    name="coturn-subprocess-supervisor",
                    daemon=True,
                )
                if not all(
                    callable(getattr(thread, name, None)) for name in ("start", "join", "is_alive")
                ):
                    raise TypeError
                self.thread = thread
                try:
                    thread.start()
                except (KeyboardInterrupt, SystemExit) as error:
                    controller.capture_control(error)
                except BaseException:
                    controller.fail("Coturn subprocess supervisor start failed")
                if thread_started(thread, controller) and wait_thread_receipt(
                    thread,
                    launch.taken,
                    controller,
                    _KERNEL_TAKE_TIMEOUT_SECONDS,
                ):
                    self._commit_started_launch(launch, token)
                    return thread, True
            except (KeyboardInterrupt, SystemExit) as error:
                controller.capture_control(error)
            except BaseException:
                controller.fail("Coturn subprocess supervisor start failed")
            return thread, False

    def _commit_started_launch(self, launch: SupervisorLaunch, token: object) -> None:
        while True:
            try:
                _supervisor_thread_started()
                break
            except (KeyboardInterrupt, SystemExit) as error:
                launch.controller.capture_control(error)
            except BaseException:
                launch.controller.fail("Coturn subprocess supervisor start failed")
                break
        _clear_launch_token(launch, token)
        while self.launch is launch:
            try:
                self.launch = None
                _slot_launch_released()
            except (KeyboardInterrupt, SystemExit) as error:
                launch.controller.capture_control(error)
            except BaseException:
                launch.controller.fail("Coturn subprocess supervisor start failed")
        self._start_committed = True

    def launch_committed(self) -> bool:
        with self._lock:
            return self._start_committed

    def detach_unstarted_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            if self.thread is thread:
                self.thread = None

    def join_if_clean(self) -> bool:
        if self.controller.lifecycle() is not Lifecycle.CLEAN:
            return False
        with self._lock:
            thread = self.thread
        if thread is None:
            return self.controller.clean_joined()
        while True:
            try:
                thread.join(0.05)
                if not thread.is_alive():
                    break
            except (KeyboardInterrupt, SystemExit) as error:
                self.controller.capture_control(error)
            except BaseException:
                return False
        while not self.controller.clean_joined():
            try:
                if not self.controller.mark_joined():
                    return False
                _joined_receipt_published()
            except (KeyboardInterrupt, SystemExit) as error:
                self.controller.capture_control(error)
            except BaseException:
                if not self.controller.clean_joined():
                    return False
        with self._lock:
            if self.thread is thread:
                self.thread = None
        return self.controller.clean_joined()

    def __repr__(self) -> str:
        return "SupervisorSlot()"


class SupervisorKernel:
    """Raw request authority visible only in the private kernel registry/worker."""

    __slots__ = ("_taken", "controller", "request", "seams")

    def __init__(
        self,
        *,
        controller: ControllerState,
        request: SupervisorRequest,
        seams: SupervisorSeams,
        taken: threading.Event,
    ) -> None:
        self._taken = taken
        self.controller = controller
        self.request: SupervisorRequest | None = request
        self.seams = seams

    def scrub(self) -> None:
        request = self.request
        if request is not None:
            request.scrub_all()
            _kernel_request_scrubbed()
            self.request = None

    def publish_taken(self) -> None:
        while not self._taken.is_set():
            try:
                self._taken.set()
            except (KeyboardInterrupt, SystemExit) as error:
                self.controller.capture_control(error)
            except BaseException:
                continue

    def taken(self) -> bool:
        return self._taken.is_set()


_KERNELS: dict[object, SupervisorKernel] = {}
_KERNELS_LOCK = threading.Lock()


class SupervisorLaunch:
    """Sanitized one-shot token; it contains no raw request or process."""

    __slots__ = ("controller", "taken", "thread_factory", "token")

    def __init__(
        self,
        *,
        token: object,
        controller: ControllerState,
        thread_factory: ThreadFactory,
        taken: threading.Event,
    ) -> None:
        self.taken = taken
        self.token: object | None = token
        self.controller = controller
        self.thread_factory = thread_factory

    def __repr__(self) -> str:
        return "SupervisorLaunch()"


def prepare_supervisor(
    *,
    request: SupervisorRequest,
    controller: ControllerState,
    seams: SupervisorSeams,
    slot: SupervisorSlot,
) -> SupervisorLaunch:
    """Transfer the raw request into the registry before a worker starts."""

    token = object()
    taken = threading.Event()
    launch = SupervisorLaunch(
        token=token,
        controller=controller,
        thread_factory=seams.thread_factory,
        taken=taken,
    )
    kernel = SupervisorKernel(
        controller=controller,
        request=request,
        seams=seams,
        taken=taken,
    )
    try:
        if not slot.publish_kernel_launch(launch, token, kernel):
            raise TypeError
        _raw_kernel_registered()
        _kernel_pre_return()
        return launch
    except (KeyboardInterrupt, SystemExit) as error:
        controller.capture_control(error)
    except BaseException:
        pass
    slot.close_admission()
    cancel_supervisor_slot(slot)
    _scrub_unpublished_kernel(kernel)
    kernel = None  # type: ignore[assignment]
    stored = controller.control()
    if stored is not None:
        raise_control(stored)
    raise TypeError("Coturn supervisor registration failed") from None


def start_supervisor_thread(
    launch: SupervisorLaunch,
    slot: SupervisorSlot,
    entry: SupervisorEntry,
) -> bool:
    """Attach cleanup authority before starting the sole supervisor thread."""

    controller = launch.controller
    thread: threading.Thread | None = None
    try:
        thread, started = slot.start_launch(launch, entry)
        if not started:
            _supervisor_receipt_unproven()
            _resolve_uncommitted_launch(launch, slot, thread)
            return False
        _supervisor_launch_pre_return()
        return True
    except (KeyboardInterrupt, SystemExit) as error:
        controller.capture_control(error)
    except BaseException:
        controller.fail("Coturn subprocess supervisor start failed")
    if slot.launch_committed():
        return True
    _resolve_uncommitted_launch(launch, slot, thread)
    return False


def cancel_supervisor_launch(launch: SupervisorLaunch) -> None:
    try: return _cancel_supervisor_launch_retry(launch)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit) as error:
        launch.controller.capture_control(error)
    except BaseException:
        pass
    _cancel_supervisor_launch_retry(launch)


def _cancel_supervisor_launch_retry(launch: SupervisorLaunch) -> None:
    while True:
        try:
            _cancel_supervisor_launch_loop(launch)
            return
        except (KeyboardInterrupt, SystemExit) as error:
            launch.controller.capture_control(error)
        except BaseException:
            continue


def _cancel_supervisor_launch_loop(launch: SupervisorLaunch) -> None:
    _cancel_launch_entry()
    while True:
        try:
            token = launch.token
            _cancel_token_observed()
        except (KeyboardInterrupt, SystemExit) as error:
            launch.controller.capture_control(error)
            continue
        except BaseException:
            continue
        if token is None:
            return
        _cancel_kernel(token, launch.controller)
        try:
            _supervisor_launch_cancelled()
        except (KeyboardInterrupt, SystemExit) as error:
            launch.controller.capture_control(error)
            continue
        except BaseException:
            continue
        _clear_launch_token(launch, token)
        return


def cancel_supervisor_slot(slot: SupervisorSlot) -> None:
    try: return _cancel_supervisor_slot_retry(slot)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit) as error:
        slot.controller.capture_control(error)
    except BaseException:
        pass
    _cancel_supervisor_slot_retry(slot)


def _cancel_supervisor_slot_retry(slot: SupervisorSlot) -> None:
    while True:
        try:
            _cancel_supervisor_slot_loop(slot)
            return
        except (KeyboardInterrupt, SystemExit) as error:
            slot.controller.capture_control(error)
        except BaseException:
            continue


def _cancel_supervisor_slot_loop(slot: SupervisorSlot) -> None:
    while True:
        if slot.cancel_pending_launch():
            return


def take_supervisor_kernel(token: object) -> SupervisorKernel | None:
    retained: SupervisorKernel | None = None
    pending_control: ControlSignal | None = None
    while True:
        try:
            with _KERNELS_LOCK:
                if retained is None:
                    retained = _KERNELS.get(token)
                if retained is None:
                    return None
                if _KERNELS.get(token) is retained:
                    retained.publish_taken()
                    del _KERNELS[token]
            _kernel_taken()
            if pending_control is not None:
                retained.controller.capture_control_signal(pending_control)
            return retained
        except (KeyboardInterrupt, SystemExit) as error:
            if pending_control is None:
                pending_control = control_signal(error)
            if retained is not None:
                retained.controller.capture_control_signal(pending_control)
        except BaseException:
            continue


def _cancel_kernel(token: object, controller: ControllerState) -> None:
    _cancel_kernel_entry()
    pending_control: ControlSignal | None = None
    retained: SupervisorKernel | None = None
    while True:
        try:
            if retained is None:
                _kernel_lookup_entry()
                with _KERNELS_LOCK:
                    retained = _KERNELS.get(token)
        except (KeyboardInterrupt, SystemExit) as error:
            if pending_control is None:
                pending_control = control_signal(error)
            continue
        except BaseException:
            continue
        if retained is None:
            if pending_control is not None:
                controller.capture_control_signal(pending_control)
            return
        try:
            with _KERNELS_LOCK:
                current = _KERNELS.get(token)
                if current is retained:
                    del _KERNELS[token]
                elif current is None and retained.taken():
                    if pending_control is not None:
                        controller.capture_control_signal(pending_control)
                    return
                elif current is not None:
                    continue
            retained.scrub()
            _kernel_cancelled()
            if pending_control is not None:
                controller.capture_control_signal(pending_control)
            return
        except (KeyboardInterrupt, SystemExit) as error:
            if pending_control is None:
                pending_control = control_signal(error)
        except BaseException:
            continue


def _resolve_uncommitted_launch(
    launch: SupervisorLaunch,
    slot: SupervisorSlot,
    thread: threading.Thread | None,
) -> None:
    resolve_uncommitted_thread(
        thread,
        launch.taken,
        launch.controller,
        lambda: cancel_supervisor_slot(slot),
        lambda: None if thread is None else slot.detach_unstarted_thread(thread),
        _KERNEL_TAKE_TIMEOUT_SECONDS,
    )


def _scrub_unpublished_kernel(kernel: SupervisorKernel) -> None:
    while kernel.request is not None:
        try:
            kernel.scrub()
        except (KeyboardInterrupt, SystemExit) as error:
            kernel.controller.capture_control(error)
        except BaseException:
            continue


def _clear_launch_token(launch: SupervisorLaunch, token: object) -> None:
    while launch.token is token:
        try:
            launch.token = None
            _launch_token_cleared()
        except (KeyboardInterrupt, SystemExit) as error:
            launch.controller.capture_control(error)
        except BaseException:
            continue


# Deterministic lifecycle seams used only by synthetic phase-injection tests.
def _raw_kernel_registered() -> None: ...  # fmt: skip
def _kernel_publication_midpoint() -> None: ...  # fmt: skip
def _kernel_pre_return() -> None: ...  # fmt: skip
def _kernel_request_scrubbed() -> None: ...  # fmt: skip
def _kernel_cancelled() -> None: ...  # fmt: skip
def _kernel_taken() -> None: ...  # fmt: skip
def _supervisor_thread_started() -> None: ...  # fmt: skip
def _supervisor_launch_pre_return() -> None: ...  # fmt: skip
def _supervisor_receipt_unproven() -> None: ...  # fmt: skip
def _cancel_token_observed() -> None: ...  # fmt: skip
def _cancel_launch_entry() -> None: ...  # fmt: skip
def _cancel_kernel_entry() -> None: ...  # fmt: skip
def _kernel_lookup_entry() -> None: ...  # fmt: skip
def _supervisor_launch_cancelled() -> None: ...  # fmt: skip
def _launch_token_cleared() -> None: ...  # fmt: skip
def _slot_launch_released() -> None: ...  # fmt: skip
def _joined_receipt_published() -> None: ...  # fmt: skip


__all__ = [
    "Clock",
    "GroupExists",
    "GroupIdentity",
    "GroupSignal",
    "SetBlocking",
    "SupervisorKernel",
    "SupervisorLaunch",
    "SupervisorSeams",
    "SupervisorSlot",
    "cancel_supervisor_launch",
    "cancel_supervisor_slot",
    "prepare_supervisor",
    "start_supervisor_thread",
    "take_supervisor_kernel",
]
