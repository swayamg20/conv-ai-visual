"""Own, consume, erase, and sanitize one relay browser result transaction."""

from __future__ import annotations

import re
import threading
import traceback
from collections.abc import Callable
from typing import NoReturn

from scripts.voice_pipecat_e2e_coturn_runtime_values import (
    raise_control,
)
from scripts.voice_pipecat_e2e_coturn_tls_worker import TlsControlLatch
from scripts.voice_pipecat_e2e_relay_artifact_cleanup import (
    _settle_relay_artifact_workspace,
)
from scripts.voice_pipecat_e2e_relay_artifact_owner import (
    RelayArtifactWorkspace,
    _capture_relay_artifacts,
    _discard_relay_artifact_contents,
    _new_relay_artifact_workspace,
    _prepare_relay_artifact_workspace,
)
from scripts.voice_pipecat_e2e_relay_browser_contract import (
    validate_relay_browser_artifacts,
)
from scripts.voice_pipecat_e2e_relay_probe import (
    RelayProbeRun,
    RelayProbeSource,
)
from scripts.voice_pipecat_e2e_stack import StackPaths

_OWNER_TOKEN = object()
_OBSERVATION_TOKEN = object()
_CALL_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_RESULT_FAILURE = "Relay browser result is unavailable"
_OWNER_FAILURE = "Relay browser result owner is unavailable"
_CLEANUP_FAILURE = "Relay browser result cleanup is required"


class RelayBrowserResultError(RuntimeError):
    """The exact relay browser artifact transaction did not complete."""

    def __repr__(self) -> str:
        return "RelayBrowserResultError()"


class RelayBrowserResultCleanupRequired(RelayBrowserResultError):
    """The exact run retains an owner that needs a cleanup-only retry."""

    def __repr__(self) -> str:
        return "RelayBrowserResultCleanupRequired()"


class RelayBrowserObservation:
    """Sanitized non-qualifying browser observation containing booleans only."""

    __slots__ = (
        "artifacts_deleted",
        "browser_cleanup_attested",
        "hidden_call_attested",
        "qualification_verified",
        "relay_candidate_attested",
        "result_schema_attested",
        "safe_report_attested",
        "terminal_cleanup_attested",
    )

    def __init__(
        self,
        token: object,
        publisher: Callable[[RelayBrowserObservation], bool] | None = None,
    ) -> None:
        authorized = token is _OBSERVATION_TOKEN
        token = None
        if not authorized or publisher is None:
            raise TypeError("Relay browser observation is factory-owned")
        for name, value in (
            ("result_schema_attested", True),
            ("hidden_call_attested", True),
            ("relay_candidate_attested", True),
            ("browser_cleanup_attested", True),
            ("terminal_cleanup_attested", True),
            ("safe_report_attested", True),
            ("artifacts_deleted", True),
            ("qualification_verified", False),
        ):
            object.__setattr__(self, name, value)
        if not publisher(self):
            raise TypeError("Relay browser observation publication failed")

    def __bool__(self) -> bool:
        return False

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("Relay browser observation is immutable")

    def __repr__(self) -> str:
        return "RelayBrowserObservation(qualification_verified=False)"

    def __copy__(self) -> None:
        raise TypeError("Relay browser observation cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay browser observation cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay browser observation cannot be serialized")


class RelayBrowserResultOwner:
    """Same-run artifact, validation, cleanup, and publication authority."""

    __slots__ = (
        "_call_id",
        "_failed",
        "_lock",
        "_observation",
        "_prepared",
        "_settled",
        "_source",
        "_validation_state",
        "_workspace",
    )

    def __init__(
        self,
        token: object,
        *,
        paths: StackPaths,
        call_id: str,
        source: RelayProbeSource,
        publisher: Callable[[object], bool],
    ) -> None:
        authorized = token is _OWNER_TOKEN
        token = None
        if (
            not authorized
            or type(call_id) is not str
            or not _CALL_ID.fullmatch(call_id)
            or type(source) is not RelayProbeSource
            or not callable(publisher)
        ):
            raise TypeError("Relay browser result owner is factory-owned")
        self._workspace = _new_relay_artifact_workspace(paths)
        self._call_id = call_id
        self._source = source
        self._lock = threading.Lock()
        self._observation: RelayBrowserObservation | None = None
        self._prepared = False
        self._settled = False
        self._validation_state: bool | None = None
        self._failed = False
        if not publisher(self):
            raise TypeError("Relay browser result owner publication failed")

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(self._prepared and not self._settled and not self._failed)

    @property
    def published(self) -> bool:
        with self._lock:
            return self._observation is not None

    def __repr__(self) -> str:
        return "RelayBrowserResultOwner()"

    def __copy__(self) -> None:
        raise TypeError("Relay browser result owner cannot be copied")

    def __deepcopy__(self, _memo: object) -> None:
        raise TypeError("Relay browser result owner cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("Relay browser result owner cannot be serialized")


def new_relay_browser_result_owner(run: RelayProbeRun) -> RelayBrowserResultOwner:
    """Bind and prepare the exact-run artifact owner before Playwright starts."""

    owner: object | None = None
    candidate: object | None = None
    control_latch = TlsControlLatch()
    failed = False
    preserve_unprepared = False
    phase = 0
    while phase < 2:
        try:
            if phase == 0:
                if type(run) is not RelayProbeRun:
                    raise RelayBrowserResultError(_OWNER_FAILURE)
                owner = run._claim_browser_artifact_owner(_new_owner)
                phase = 1
                _result_boundary_hook("owner-retained")
            else:
                if type(owner) is not RelayBrowserResultOwner or not _authorized(
                    run,
                    owner,
                    active=True,
                ):
                    raise RelayBrowserResultError(_OWNER_FAILURE)
                with owner._lock:
                    ready = _prepare_relay_artifact_workspace(
                        owner._workspace,
                        control_latch=control_latch,
                    )
                    owner._prepared = bool(owner._prepared or ready)
                phase = 2
        except (KeyboardInterrupt, SystemExit) as error:
            control_latch.record_error(error)
            _scrub_exception(error)
            candidate = (
                run._browser_artifact_cleanup_owner() if type(run) is RelayProbeRun else None
            )
            if (
                phase <= 1
                and type(candidate) is RelayBrowserResultOwner
                and not candidate._prepared
                and candidate._workspace._run_binding is None
            ):
                owner = candidate
                preserve_unprepared = True
                phase = 2
        except BaseException as error:
            candidate = (
                run._browser_artifact_cleanup_owner() if type(run) is RelayProbeRun else None
            )
            if phase <= 1 and type(candidate) is RelayBrowserResultOwner:
                owner = candidate
            failed = True
            phase = 2
            _scrub_exception(error)
    ready = bool(
        type(owner) is RelayBrowserResultOwner
        and owner._prepared
        and not owner._settled
        and not owner._failed
    )
    control = control_latch.value()
    if control is not None or failed or not ready:
        if preserve_unprepared and control is not None and not failed:
            run = owner = candidate = None  # type: ignore[assignment]
            control_latch = None  # type: ignore[assignment]
            raise_control(control)
        settled_complete = bool(type(owner) is RelayBrowserResultOwner and owner._settled)
        try:
            settled_complete, _ = _settle_owner(
                owner,
                control_latch=control_latch,
                revoke=True,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            control_latch.record_error(error)
            _scrub_exception(error)
            settled_complete = bool(type(owner) is RelayBrowserResultOwner and owner._settled)
        except BaseException as error:
            _scrub_exception(error)
        cleanup_required = bool(type(owner) is RelayBrowserResultOwner and not settled_complete)
        control = control_latch.value()
        run = owner = candidate = None  # type: ignore[assignment]
        control_latch = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control)
        if cleanup_required:
            _raise_cleanup()
        _raise_public(_OWNER_FAILURE)
    run = None  # type: ignore[assignment]
    return owner  # type: ignore[return-value]


def consume_relay_browser_result(
    run: RelayProbeRun,
    owner: RelayBrowserResultOwner,
) -> RelayBrowserObservation:
    """Validate and erase rich artifacts before publishing one observation."""

    result: RelayBrowserObservation | None = None
    control_latch = TlsControlLatch()
    failed = False
    retained: RelayBrowserResultOwner | None = None
    try:
        if not _authorized(run, owner, active=True):
            raise RelayBrowserResultError(_RESULT_FAILURE)
        retained = owner
        with owner._lock:
            result, failed = _consume_locked(run, owner, control_latch)
    except (KeyboardInterrupt, SystemExit) as error:
        control_latch.record_error(error)
        _scrub_exception(error)
    except BaseException as error:
        failed = True
        _scrub_exception(error)
    unsettled = bool(retained is not None and not retained._settled)
    run = owner = None  # type: ignore[assignment]
    retained = None
    control = control_latch.value()
    control_latch = None  # type: ignore[assignment]
    if control is not None:
        result = None
        raise_control(control)
    if failed or type(result) is not RelayBrowserObservation:
        result = None
        if unsettled:
            _raise_cleanup()
        _raise_public(_RESULT_FAILURE)
    return result


def cleanup_relay_browser_result_owner(
    run: RelayProbeRun,
) -> None:
    """Irreversibly settle a retained owner without publishing an observation."""

    control_latch = TlsControlLatch()
    candidate: object | None = None
    complete = False
    try:
        if type(run) is not RelayProbeRun:
            raise RelayBrowserResultError(_CLEANUP_FAILURE)
        candidate = run._browser_artifact_cleanup_owner()
        if type(candidate) is not RelayBrowserResultOwner or not _authorized(
            run,
            candidate,
            active=False,
        ):
            raise RelayBrowserResultError(_CLEANUP_FAILURE)
        complete, _ = _settle_owner(
            candidate,
            control_latch=control_latch,
            revoke=True,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        control_latch.record_error(error)
        _scrub_exception(error)
    except BaseException as error:
        _scrub_exception(error)
    control = control_latch.value()
    run = candidate = None  # type: ignore[assignment]
    control_latch = None  # type: ignore[assignment]
    if control is not None:
        raise_control(control)
    if not complete:
        _raise_cleanup()


def _new_owner(
    paths: StackPaths,
    call_id: str,
    source: RelayProbeSource,
    publisher: Callable[[object], bool],
) -> RelayBrowserResultOwner:
    return RelayBrowserResultOwner(
        _OWNER_TOKEN,
        paths=paths,
        call_id=call_id,
        source=source,
        publisher=publisher,
    )


def _consume_locked(
    run: RelayProbeRun,
    owner: RelayBrowserResultOwner,
    latch: TlsControlLatch,
) -> tuple[RelayBrowserObservation | None, bool]:
    if owner._observation is not None:
        return owner._observation, False
    if owner._failed or not owner._prepared:
        return None, True
    if owner._validation_state is None:
        raw_result, raw_report, structural, captured, _ = _capture_relay_artifacts(
            owner._workspace,
            control_latch=latch,
        )
        validated = False
        if captured:
            while True:
                try:
                    validated = bool(
                        structural
                        and validate_relay_browser_artifacts(
                            raw_result,
                            raw_report,
                            owner._call_id,
                        )
                    )
                    break
                except (KeyboardInterrupt, SystemExit) as error:
                    latch.record_error(error)
                    _scrub_exception(error)
                except BaseException as error:
                    _scrub_exception(error)
                    break
        hook_ok = _call_hook("rich-artifacts-validated", latch)
        raw_result = raw_report = b""
        published = _retain_validation_state(
            owner,
            bool(validated and hook_ok),
            latch,
        )
        _discard_relay_artifact_contents(owner._workspace)
        if not published:
            owner._failed = True
            return None, True
    complete, publication_safe, _ = _settle_relay_artifact_workspace(
        owner._workspace,
        control_latch=latch,
    )
    owner._settled = complete
    if not complete:
        owner._failed = True
        return None, True
    hook_ok = _call_hook("rich-artifacts-deleted", latch)
    if (
        owner._validation_state is not True
        or not publication_safe
        or not hook_ok
        or not _authorized(run, owner, active=True)
    ):
        owner._failed = True
        return None, True
    observation = _publish_observation(owner, latch)
    return observation, observation is None


def _retain_validation_state(
    owner: RelayBrowserResultOwner,
    validated: bool,
    latch: TlsControlLatch,
) -> bool:
    published = False
    while not published:
        try:
            if owner._validation_state is None:
                owner._validation_state = validated
            published = owner._validation_state is validated
            break
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            break
    return published


def _publish_observation(
    owner: RelayBrowserResultOwner,
    latch: TlsControlLatch,
) -> RelayBrowserObservation | None:
    observation = owner._observation
    if observation is not None:
        return observation
    while owner._observation is None:
        try:
            RelayBrowserObservation(
                _OBSERVATION_TOKEN,
                lambda value: _retain_observation(owner, value),
            )
            _result_boundary_hook("observation-published")
        except (KeyboardInterrupt, SystemExit) as error:
            latch.record_error(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            return owner._observation
    return owner._observation


def _retain_observation(
    owner: RelayBrowserResultOwner,
    observation: RelayBrowserObservation,
) -> bool:
    if owner._observation is None:
        owner._observation = observation
    return owner._observation is observation


def _settle_owner(
    owner: object,
    *,
    control_latch: TlsControlLatch,
    revoke: bool,
) -> tuple[bool, bool]:
    if type(owner) is not RelayBrowserResultOwner:
        return True, False
    complete = False
    publication_safe = False
    finished = False
    while not finished:
        try:
            with owner._lock:
                if owner._observation is not None:
                    return True, True
                if revoke:
                    owner._failed = True
                complete, publication_safe, _ = _settle_relay_artifact_workspace(
                    owner._workspace,
                    control_latch=control_latch,
                )
                owner._settled = complete
                finished = True
        except (KeyboardInterrupt, SystemExit) as error:
            control_latch.record_error(error)
            _scrub_exception(error)
        except BaseException as error:
            _scrub_exception(error)
            return False, False
    return complete, publication_safe


def _authorized(
    run: object,
    owner: object,
    *,
    active: bool,
) -> bool:
    if type(run) is not RelayProbeRun or type(owner) is not RelayBrowserResultOwner:
        return False
    retained = (
        run._matches_browser_artifact_owner(owner)
        if active
        else run._retains_browser_artifact_owner(owner)
    )
    workspace: RelayArtifactWorkspace = owner._workspace
    return bool(
        retained
        and owner._source is run._source
        and owner._call_id == run._call_id
        and workspace._paths is run._stack_paths
    )


def _call_hook(
    phase: str,
    latch: TlsControlLatch,
) -> bool:
    try:
        _result_boundary_hook(phase)
        return True
    except (KeyboardInterrupt, SystemExit) as error:
        latch.record_error(error)
        _scrub_exception(error)
        return True
    except BaseException as error:
        _scrub_exception(error)
        return False


def _scrub_exception(error: BaseException) -> None:
    traceback.clear_frames(error.__traceback__)
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True
    error.__dict__.clear()
    error.args = ()


def _result_boundary_hook(_phase: str) -> None:
    """Secret-free deterministic seam for control-publication tests."""


def _raise_public(message: str) -> NoReturn:
    raise RelayBrowserResultError(message) from None


def _raise_cleanup() -> NoReturn:
    raise RelayBrowserResultCleanupRequired(_CLEANUP_FAILURE) from None


__all__ = [
    "RelayBrowserObservation",
    "RelayBrowserResultCleanupRequired",
    "RelayBrowserResultError",
    "RelayBrowserResultOwner",
    "cleanup_relay_browser_result_owner",
    "consume_relay_browser_result",
    "new_relay_browser_result_owner",
]
