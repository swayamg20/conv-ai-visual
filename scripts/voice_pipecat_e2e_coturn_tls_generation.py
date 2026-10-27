"""Retry-closed boundary for publishing generated TLS into a prior slot.

Finite controls raised by owned operations are retried without bound. Arbitrary
caller-line tracing is not masked; raw arguments are scrubbed before propagation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn import CoturnBridgeTopology
from scripts.voice_pipecat_e2e_coturn_host import (
    CommandRunner,
    CoturnRuntimePaths,
    TrustedHostTools,
)
from scripts.voice_pipecat_e2e_coturn_tls_lifetime import (
    CoturnTlsCleanupRequired,
    TlsCombinedCleanupAuthority,
    TlsMaterialLifetimeAuthority,
    combine_tls_cleanup_authorities,
    new_tls_material_lifetime_authority,
)
from scripts.voice_pipecat_e2e_coturn_tls_material import (
    TlsMaterialGenerationReservation,
    TlsMaterialGenerationSlot,
    TlsMaterialReceipt,
    new_tls_material_generation_reservation,
    release_tls_material_generation_slot,
    reserve_tls_material_generation_slot,
    tls_material_generation_slot_owns_receipt,
)
from scripts.voice_pipecat_e2e_coturn_tls_private import (
    ControlSignal,
    CoturnTlsError,
    CoturnTlsPrivateCleanupRequired,
)
from scripts.voice_pipecat_e2e_coturn_tls_receipt import (
    PrivateDescriptorCleanupAuthority,
    PrivateFileCleanupReceipt,
)
from scripts.voice_pipecat_e2e_coturn_tls_values import safe_tls_failure
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch, sanitize_control

_Generator = Callable[..., TlsMaterialReceipt | None]


class _TlsGenerationCall:
    """Retain generation and rollback authority until one terminal outcome."""

    __slots__ = (
        "_cleanup_failed",
        "_cleanup_inflight",
        "_cleanup_skip",
        "_control",
        "_failure",
        "_generator",
        "_invoked",
        "_lifetime",
        "_now",
        "_owned",
        "_paths",
        "_phase",
        "_private_adopted",
        "_private_recovery",
        "_recovery",
        "_reservation",
        "_reservation_attempted",
        "_runner",
        "_secret",
        "_slot",
        "_succeeded",
        "_terminal",
        "_tools",
        "_topology",
    )

    def __init__(
        self,
        generator: _Generator,
        *,
        slot: TlsMaterialGenerationSlot,
        runner: CommandRunner,
        tools: TrustedHostTools,
        paths: CoturnRuntimePaths,
        topology: CoturnBridgeTopology,
        static_auth_secret: object,
        now: datetime,
    ) -> None:
        self._generator: _Generator | None = generator
        self._slot: TlsMaterialGenerationSlot | None = slot
        self._runner: CommandRunner | None = runner
        self._tools: TrustedHostTools | None = tools
        self._paths: CoturnRuntimePaths | None = paths
        self._topology: CoturnBridgeTopology | None = topology
        self._secret: object | None = static_auth_secret
        self._now: datetime | None = now
        self._control = TlsControlLatch()
        self._reservation: TlsMaterialGenerationReservation | None = (
            new_tls_material_generation_reservation()
        )
        self._failure: str | None = None
        self._lifetime: TlsMaterialLifetimeAuthority | None = None
        self._private_recovery: object | None = None
        self._recovery: object | None = None
        self._phase = 0
        self._reservation_attempted = False
        self._invoked = False
        self._succeeded = False
        self._owned = False
        self._private_adopted = False
        self._cleanup_inflight = False
        self._cleanup_skip = False
        self._cleanup_failed = False
        self._terminal = False

    def advance(self) -> bool:
        if self._terminal:
            return True
        if self._control.value() is not None or self._failure is not None:
            self._reconcile()
            return self._terminal
        if self._phase == 0:
            self._reservation_attempted = True
            reserved, observed = reserve_tls_material_generation_slot(
                self._slot,  # type: ignore[arg-type]
                self._paths,  # type: ignore[arg-type]
                self._topology,  # type: ignore[arg-type]
                self._reservation,  # type: ignore[arg-type]
            )
            if observed is not None:
                self._control.record(observed)
            if not reserved:
                self._capture_message("Coturn TLS generation slot is invalid")
            else:
                self._phase = 1
            _generation_boundary_hook("reserve-return")
        elif self._phase == 1:
            self._lifetime = new_tls_material_lifetime_authority()
            self._phase = 2
            _generation_boundary_hook("lifetime-return")
        elif self._phase == 2:
            self._invoked = True
            generator = self._generator
            if generator is None:
                self._capture_message("Coturn TLS material is invalid")
            else:
                result = generator(
                    runner=self._runner,
                    tools=self._tools,
                    paths=self._paths,
                    topology=self._topology,
                    static_auth_secret=self._secret,
                    now=self._now,
                    lifetime=self._lifetime,
                    generation_slot=self._slot,
                    generation_reservation=self._reservation,
                )
                if result is not None:
                    self._capture_message("Coturn TLS cleanup failed")
                else:
                    self._succeeded = True
                    self._phase = 3
            _generation_boundary_hook("generator-return")
        elif self._phase == 3:
            ownership, observed = tls_material_generation_slot_owns_receipt(
                self._slot,  # type: ignore[arg-type]
            )
            if observed is not None:
                self._control.record(observed)
            if ownership == "unknown":
                return False
            if ownership == "invalid":
                self._capture_message("Coturn TLS generation slot is invalid")
                return False
            owned = ownership == "owned"
            self._owned = owned
            if not owned:
                self._capture_message("Coturn TLS cleanup failed")
            else:
                self._phase = 4
            _generation_boundary_hook("owned-return")
        else:
            self._scrub_raw()
            self._terminal = True
        return self._terminal

    def capture_control(self, error: KeyboardInterrupt | SystemExit) -> None:
        self._control.record_error(error)
        self._capture_private_recovery(error)

    def capture_failure(self, error: BaseException) -> None:
        self._capture_private_recovery(error)
        if type(error) is CoturnTlsCleanupRequired and self._recovery is None:
            self._recovery = error.cleanup_authority
        if self._cleanup_inflight:
            self._cleanup_inflight = False
            self._cleanup_skip = True
            self._cleanup_failed = True
        self._capture_message(safe_tls_failure(error, "Coturn TLS material is invalid"))

    def _capture_private_recovery(self, error: BaseException) -> None:
        recovery = private_cleanup_candidate(error)
        if recovery is not None and self._private_recovery is None:
            self._private_recovery = recovery

    def _capture_message(self, message: str) -> None:
        if self._failure is None:
            self._failure = message

    def _reconcile(self) -> None:
        if self._phase < 5:
            ownership, observed = tls_material_generation_slot_owns_receipt(
                self._slot,  # type: ignore[arg-type]
            )
            if observed is not None:
                self._control.record(observed)
            if ownership == "unknown":
                return
            if ownership == "invalid":
                self._capture_message("Coturn TLS generation slot is invalid")
                self._reservation_attempted = False
                self._phase = 5
                return
            owned = ownership == "owned"
            self._owned = owned
            self._phase = 8 if owned else 5
            _generation_boundary_hook("abort-owned-return")
            return
        if self._phase == 5:
            self._settle_unpublished_lifetime()
            self._phase = 6
            _generation_boundary_hook("abort-cleanup-return")
            return
        if self._phase == 6:
            if self._reservation_attempted:
                released, observed = release_tls_material_generation_slot(
                    self._slot,  # type: ignore[arg-type]
                    self._reservation,  # type: ignore[arg-type]
                )
                if observed is not None:
                    self._control.record(observed)
                if not released:
                    self._cleanup_failed = True
            self._phase = 7
            _generation_boundary_hook("abort-release-return")
            return
        if self._phase == 7:
            self._finish_recovery()
            self._phase = 8
            return
        self._scrub_raw()
        self._terminal = True

    def _settle_unpublished_lifetime(self) -> None:
        lifetime = self._lifetime
        if lifetime is None:
            return
        if not lifetime.active:
            self._publish_settled_lifetime(lifetime)
            return
        if self._private_recovery is not None and not self._private_adopted:
            self._private_adopted = lifetime.retain_private_authority(self._private_recovery)
        if self._cleanup_skip:
            self._cleanup_failed = True
            self._recovery = self._recovery or lifetime
            return
        self._cleanup_inflight = True
        cleanup_failed, observed = lifetime.cleanup(initial_control=self._control.value())
        self._cleanup_inflight = False
        if observed is not None:
            self._control.record(observed)
        if cleanup_failed:
            self._cleanup_failed = True
            self._recovery = self._recovery or lifetime
        else:
            self._publish_settled_lifetime(lifetime)

    def _publish_settled_lifetime(self, lifetime: TlsMaterialLifetimeAuthority) -> None:
        if self._recovery is lifetime:
            self._recovery = None
        self._cleanup_inflight = False
        self._cleanup_skip = False
        self._cleanup_failed = self._recovery is not None
        self._lifetime = None

    def _finish_recovery(self) -> None:
        if self._lifetime is not None and not self._lifetime.active:
            self._publish_settled_lifetime(self._lifetime)
        unadopted = None if self._private_adopted else self._private_recovery
        if self._cleanup_failed and self._lifetime is not None and unadopted is not None:
            self._recovery = combine_tls_cleanup_authorities(self._lifetime, unadopted)
        elif self._cleanup_failed and self._recovery is None:
            self._recovery = self._lifetime
        elif self._recovery is None:
            self._recovery = unadopted
        if (
            type(self._recovery)
            in {
                TlsCombinedCleanupAuthority,
                TlsMaterialLifetimeAuthority,
            }
            and not self._recovery.active
        ):
            self._recovery = None
        self._private_recovery = None
        self._lifetime = None

    def _scrub_raw(self) -> None:
        self._generator = None
        self._runner = None
        self._tools = None
        self._paths = None
        self._topology = None
        self._secret = None
        self._now = None
        self._reservation = None
        if self._owned:
            self._lifetime = None
            self._private_recovery = None
            self._recovery = None

    def outcome(self) -> tuple[bool, ControlSignal | None, str | None, object | None, bool]:
        return (
            self._terminal,
            self._control.value(),
            self._failure,
            self._recovery,
            bool(self._succeeded and self._owned),
        )

    def scrub_terminal(self) -> None:
        self._scrub_raw()
        self._slot = None

    def __repr__(self) -> str:
        return "_TlsGenerationCall()"


def bind_tls_material_slot_generator(generator: _Generator) -> Callable[..., None]:
    """Bind the private generator to one control-closed public slot API."""

    def generate(
        *,
        slot: TlsMaterialGenerationSlot,
        runner: CommandRunner,
        tools: TrustedHostTools,
        paths: CoturnRuntimePaths,
        topology: CoturnBridgeTopology,
        static_auth_secret: object,
        now: datetime,
    ) -> None:
        try: call = _make_generation_call(generator, slot=slot, runner=runner, tools=tools, paths=paths, topology=topology, static_auth_secret=static_auth_secret, now=now)  # protected generation-owner bootstrap  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            control = sanitize_control(error)
            error = None  # type: ignore[assignment]
            runner = tools = paths = topology = now = None  # type: ignore[assignment]
            static_auth_secret = None
            slot = None  # type: ignore[assignment]
            terminal_error = _prepare_generation_error(control, None, None, False)
            return _publish_generation_terminal(terminal_error, TlsControlLatch(control))
        except BaseException:
            runner = tools = paths = topology = now = None  # type: ignore[assignment]
            static_auth_secret = None
            slot = None  # type: ignore[assignment]
            terminal_error = _prepare_generation_error(
                None, "Coturn TLS material is invalid", None, False
            )
            return _publish_generation_terminal(terminal_error, TlsControlLatch())
        try: runner = tools = paths = topology = now = None; static_auth_secret = None; slot = None; terminal_error, publication_control = _finish_generation_call(call)  # noqa: E701, E702  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as error:
            _capture_generation_control(call, error)
            runner = tools = paths = topology = now = None  # type: ignore[assignment]
            static_auth_secret = None
            slot = None  # type: ignore[assignment]
            terminal_error, publication_control = _finish_generation_call(call)
        except BaseException as error:
            _capture_generation_failure(call, error)
            runner = tools = paths = topology = now = None  # type: ignore[assignment]
            static_auth_secret = None
            slot = None  # type: ignore[assignment]
            terminal_error, publication_control = _finish_generation_call(call)
        try: return _publish_generation_terminal(terminal_error, publication_control)  # protected terminal publication trampoline  # noqa: E701  # fmt: skip
        except (KeyboardInterrupt, SystemExit) as observed:
            if observed is terminal_error:
                raise
            publication_control.record_error(observed)
            if not isinstance(terminal_error, (KeyboardInterrupt, SystemExit)):
                recovery = getattr(terminal_error, "cleanup_authority", None)
                terminal_error = _prepare_generation_error(
                    publication_control.value(), None, recovery, False
                )
            recovery = getattr(terminal_error, "cleanup_authority", None)
            if recovery is not None:
                observed.cleanup_authority = recovery  # type: ignore[attr-defined]
                observed.material_committed = False  # type: ignore[attr-defined]
            return _publish_generation_terminal(terminal_error, publication_control)

    generate.__name__ = "generate_tls_and_config_material_into_slot"
    generate.__qualname__ = generate.__name__
    generate.__module__ = generator.__module__
    generate.__doc__ = "Generate into a caller-owned slot without returning cleanup authority."
    return generate


def _finish_generation_call(
    call: _TlsGenerationCall,
) -> tuple[BaseException | None, TlsControlLatch]:
    try:
        while True:
            try:
                if not call.advance():
                    continue
                _generation_boundary_hook("pre-final-scrub")
                terminal, final_control, final_failure, recovery, succeeded = call.outcome()
                call.scrub_terminal()
                _generation_boundary_hook("final-raise")
                terminal_error = _prepare_generation_error(
                    final_control,
                    final_failure,
                    recovery,
                    succeeded,
                )
                _generation_boundary_hook("final-publication-return")
                if terminal:
                    publication_control = TlsControlLatch(
                        sanitize_control(terminal_error)
                        if isinstance(terminal_error, (KeyboardInterrupt, SystemExit))
                        else None
                    )
                    return terminal_error, publication_control
            except (KeyboardInterrupt, SystemExit) as error:
                _capture_generation_control(call, error)
            except BaseException as error:
                _capture_generation_failure(call, error)
    except (KeyboardInterrupt, SystemExit) as error:
        _capture_generation_control(call, error)
        return _finish_generation_call(call)
    except BaseException as error:
        _capture_generation_failure(call, error)
        return _finish_generation_call(call)


def _publish_generation_terminal(
    terminal_error: BaseException | None,
    publication_control: TlsControlLatch,
) -> None:
    try:
        while True:
            try:
                _emit_generation_error(terminal_error)
                return
            except (KeyboardInterrupt, SystemExit) as observed:
                while True:
                    try:
                        publication_control.record_error(observed)  # inner publication control
                        break
                    except (KeyboardInterrupt, SystemExit):
                        continue
                while True:
                    try:
                        replay_terminal = observed is terminal_error
                        if not replay_terminal and not isinstance(
                            terminal_error, (KeyboardInterrupt, SystemExit)
                        ):
                            recovery = getattr(terminal_error, "cleanup_authority", None)  # inner publication recovery  # fmt: skip
                            terminal_error = _prepare_generation_error(
                                publication_control.value(),
                                None,
                                recovery,
                                False,  # inner publication rebuild
                            )
                        break
                    except (KeyboardInterrupt, SystemExit) as later:
                        publication_control.record_error(later)
                try:
                    if replay_terminal:
                        raise terminal_error from None
                except (KeyboardInterrupt, SystemExit) as later:
                    if later is terminal_error:
                        raise
                    publication_control.record_error(later)
    except (KeyboardInterrupt, SystemExit) as observed:
        if observed is terminal_error:
            raise
        publication_control.record_error(observed)
        if not isinstance(terminal_error, (KeyboardInterrupt, SystemExit)):
            recovery = getattr(terminal_error, "cleanup_authority", None)
            terminal_error = _prepare_generation_error(
                publication_control.value(), None, recovery, False
            )
        return _publish_generation_terminal(terminal_error, publication_control)


def _prepare_generation_error(
    control: ControlSignal | None,
    failure: str | None,
    recovery: object | None,
    succeeded: bool,
) -> BaseException | None:
    """Build one sanitized terminal publication without raising through raw frames."""

    if (
        type(recovery)
        in {
            TlsCombinedCleanupAuthority,
            TlsMaterialLifetimeAuthority,
        }
        and not recovery.active
    ):
        recovery = None
    if control is not None:
        kind, code = control
        error: BaseException = (
            KeyboardInterrupt() if kind is KeyboardInterrupt else SystemExit(code)
        )
        if recovery is not None:
            error.cleanup_authority = recovery  # type: ignore[attr-defined]
            error.material_committed = bool(  # type: ignore[attr-defined]
                type(recovery) is PrivateFileCleanupReceipt and recovery.committed
            )
        return error
    if recovery is not None:
        if type(recovery) in {TlsMaterialLifetimeAuthority, TlsCombinedCleanupAuthority}:
            return CoturnTlsCleanupRequired(recovery)
        return CoturnTlsPrivateCleanupRequired(recovery)
    if failure is not None or not succeeded:
        return CoturnTlsError(failure or "Coturn TLS material is invalid")
    return None


def _emit_generation_error(error: BaseException | None) -> None:
    """Emit the retained sanitized terminal object through the public frame."""

    if error is not None:
        raise error from None


def private_cleanup_candidate(error: BaseException) -> object | None:
    """Walk only Python's effective bounded exception chain for exact private authority."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _depth in range(4):
        if current is None:
            break
        identifier = id(current)
        if identifier in visited:
            break
        visited.add(identifier)
        candidate: object | None = None
        if type(current) is CoturnTlsPrivateCleanupRequired:
            candidate = object.__getattribute__(current, "_cleanup_authority")
        elif type(current) in {KeyboardInterrupt, SystemExit}:
            namespace = object.__getattribute__(current, "__dict__")
            if type(namespace) is dict:
                candidate = namespace.get("cleanup_authority")
        if type(candidate) in {PrivateDescriptorCleanupAuthority, PrivateFileCleanupReceipt}:
            return candidate
        cause = object.__getattribute__(current, "__cause__")
        if isinstance(cause, BaseException):
            current = cause
            continue
        if cause is not None or object.__getattribute__(current, "__suppress_context__"):
            break
        context = object.__getattribute__(current, "__context__")
        current = context if isinstance(context, BaseException) else None
    return None


def _capture_generation_control(
    call: _TlsGenerationCall,
    error: KeyboardInterrupt | SystemExit,
) -> None:
    while True:
        try:
            call.capture_control(error)
            return
        except (KeyboardInterrupt, SystemExit):
            continue
        except BaseException:
            continue


def _capture_generation_failure(call: _TlsGenerationCall, error: BaseException) -> None:
    pending_error: BaseException | None = error
    pending_control: KeyboardInterrupt | SystemExit | None = None
    while True:
        try:
            if pending_control is not None:
                call.capture_control(pending_control)
                pending_control = None
            if pending_error is None:
                call._capture_message("Coturn TLS material is invalid")
            else:
                call.capture_failure(pending_error)
                pending_error = None
            return
        except (KeyboardInterrupt, SystemExit) as nested:
            if pending_control is None:
                pending_control = nested
        except BaseException:
            pending_error = None


def _make_generation_call(
    generator: _Generator,
    *,
    slot: TlsMaterialGenerationSlot,
    runner: CommandRunner,
    tools: TrustedHostTools,
    paths: CoturnRuntimePaths,
    topology: CoturnBridgeTopology,
    static_auth_secret: object,
    now: datetime,
    control_error: KeyboardInterrupt | SystemExit | None = None,
    failure: str | None = None,
) -> _TlsGenerationCall:
    first_control = control_error
    first_failure = failure
    while True:
        try:
            call = _TlsGenerationCall(
                generator,
                slot=slot,
                runner=runner,
                tools=tools,
                paths=paths,
                topology=topology,
                static_auth_secret=static_auth_secret,
                now=now,
            )
            if first_control is not None:
                call.capture_control(first_control)
            if first_failure is not None:
                call._capture_message(first_failure)
            return call
        except (KeyboardInterrupt, SystemExit) as error:
            if first_control is None:
                first_control = error
        except BaseException:
            first_failure = first_failure or "Coturn TLS material is invalid"


def _generation_boundary_hook(_phase: str) -> None:
    """Secret-free deterministic seam for finite-control boundary tests."""


__all__ = ["bind_tls_material_slot_generator"]
