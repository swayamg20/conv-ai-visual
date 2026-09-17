"""Bounded, redacted values for the Coturn subprocess supervisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

CHUNK_BYTES = 4_096
MAX_QUEUED_CHUNKS = 256
MAX_LIFETIME_CHUNKS = 4_096
MAX_IO_BYTES = 1_048_576
TERMINATION_GRACE_SECONDS = 0.5
KILL_VERIFICATION_SECONDS = 2.0

ControlSignal: TypeAlias = tuple[
    type[KeyboardInterrupt] | type[SystemExit],
    int | None,
]


class CoturnSubprocessError(RuntimeError):
    """A bounded subprocess contract failed without reflecting raw data."""


@dataclass(frozen=True, init=False)
class SubprocessChunk:
    """One transient output chunk whose bytes are hidden from repr."""

    stream: Literal["stdout", "stderr"]
    data: bytes = field(repr=False)

    def __init__(self, stream: object, data: object) -> None:
        control: ControlSignal | None = None
        try:
            _chunk_validation_entry()
            valid = (
                type(stream) is str
                and stream in {"stdout", "stderr"}
                and type(data) is bytes
                and 1 <= len(data) <= CHUNK_BYTES
            )
            if not valid:
                raise ValueError
            object.__setattr__(self, "stream", stream)
            _chunk_stream_published()
            object.__setattr__(self, "data", data)
            return
        except (KeyboardInterrupt, SystemExit) as error:
            control = control_signal(error)
        except BaseException:
            pass
        while True:
            try:
                _chunk_scrub_entry()
                object.__setattr__(self, "stream", "stdout")
                object.__setattr__(self, "data", b"")
                stream = None
                data = None
                break
            except (KeyboardInterrupt, SystemExit) as error:
                if control is None:
                    control = control_signal(error)
            except BaseException:
                continue
        self = None  # type: ignore[assignment]
        if control is not None:
            raise_control(control)
        raise_subprocess_error("Coturn subprocess chunk is invalid")


def control_signal(error: KeyboardInterrupt | SystemExit) -> ControlSignal:
    """Reduce a control exception to its non-reflective, exact exit contract."""

    try: return _control_signal_retry(error)  # noqa: E701  # fmt: skip
    except (KeyboardInterrupt, SystemExit):
        return _control_signal_retry(error)
    except BaseException:
        return _control_signal_retry(error)


def _control_signal_retry(error: KeyboardInterrupt | SystemExit) -> ControlSignal:
    """Preserve the first raw control across any finite nested delivery."""

    while True:
        try:
            _control_normalization_entry()
            if isinstance(error, KeyboardInterrupt):
                return KeyboardInterrupt, None
            code = error.code
            if code is not None and type(code) is not int:
                code = 1
            return SystemExit, code
        except (KeyboardInterrupt, SystemExit):
            continue
        except BaseException:
            continue


def raise_control(control: ControlSignal) -> None:
    kind, code = control
    if kind is KeyboardInterrupt:
        raise KeyboardInterrupt from None
    raise SystemExit(code) from None


def raise_subprocess_error(message: str) -> None:
    raise CoturnSubprocessError(message) from None


def _control_normalization_entry() -> None:
    """Deterministic nested-control seam inside the sole normalizer."""


def _chunk_validation_entry() -> None:
    """Deterministic first-control seam while raw chunk inputs are retained."""


def _chunk_scrub_entry() -> None:
    """Deterministic nested-control seam before raw chunk inputs are scrubbed."""


def _chunk_stream_published() -> None:
    """Control seam after stream publication while raw bytes remain guarded."""


__all__ = [
    "CHUNK_BYTES",
    "KILL_VERIFICATION_SECONDS",
    "MAX_IO_BYTES",
    "MAX_LIFETIME_CHUNKS",
    "MAX_QUEUED_CHUNKS",
    "TERMINATION_GRACE_SECONDS",
    "ControlSignal",
    "CoturnSubprocessError",
    "SubprocessChunk",
    "control_signal",
    "raise_control",
    "raise_subprocess_error",
]
