"""Pure validation and opaque transfer for one trusted child request."""

from __future__ import annotations

import os

from scripts.voice_pipecat_e2e_coturn_host import (
    DOCKER_EXECUTABLE,
    OPENSSL_EXECUTABLE,
    CommandRequest,
)
from scripts.voice_pipecat_e2e_coturn_subprocess_values import MAX_IO_BYTES

_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 65_536
_TRUSTED_EXECUTABLES = {os.fspath(DOCKER_EXECUTABLE), os.fspath(OPENSSL_EXECUTABLE)}
_REQUEST_TOKEN = object()


class SupervisorRequest:
    """Mutable, redacted transfer value scrubbed by the spawn owner."""

    __slots__ = (
        "argv",
        "environment",
        "maximum_output_bytes",
        "stdin",
        "timeout_seconds",
        "umask",
    )

    def __init__(
        self,
        token: object,
        *,
        argv: tuple[str, ...],
        environment: tuple[tuple[str, str], ...],
        stdin: bytes,
        timeout_seconds: float,
        maximum_output_bytes: int,
        umask: int,
    ) -> None:
        if token is not _REQUEST_TOKEN:
            raise TypeError("Coturn supervisor request is factory-owned")
        self.argv = argv
        self.environment = environment
        self.stdin = bytearray(stdin)
        self.timeout_seconds = timeout_seconds
        self.maximum_output_bytes = maximum_output_bytes
        self.umask = umask

    def scrub_spawn_fields(self) -> None:
        self.argv = ()
        self.environment = ()

    def scrub_all(self) -> None:
        self.scrub_spawn_fields()
        self.stdin.clear()

    def __repr__(self) -> str:
        return "SupervisorRequest()"


def validate_request(request: object) -> SupervisorRequest | None:
    """Copy only an exact immutable low-level request into opaque ownership."""

    try:
        if type(request) is not CommandRequest:
            return None
        argv = request.argv
        environment = request.environment
        stdin = request.stdin
        timeout = request.timeout_seconds
        maximum = request.maximum_output_bytes
        umask = request.umask
        if (
            type(argv) is not tuple
            or not 1 <= len(argv) <= _MAX_ARGUMENTS
            or not _valid_argv(argv)
            or type(environment) is not tuple
            or not _valid_environment(environment)
            or type(stdin) is not bytes
            or len(stdin) > MAX_IO_BYTES
            or type(timeout) not in {int, float}
            or not 0.1 <= timeout <= 60.0
            or type(maximum) is not int
            or not 1 <= maximum <= MAX_IO_BYTES
            or type(umask) is not int
            or umask != 0o077
        ):
            return None
        return SupervisorRequest(
            _REQUEST_TOKEN,
            argv=argv,
            environment=environment,
            stdin=stdin,
            timeout_seconds=float(timeout),
            maximum_output_bytes=maximum,
            umask=umask,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _valid_environment(environment: tuple[object, ...]) -> bool:
    if len(environment) != 2:
        return False
    for pair in environment:
        if type(pair) is not tuple or len(pair) != 2:
            return False
        if type(pair[0]) is not str or type(pair[1]) is not str:
            return False
    return environment == (("LANG", "C"), ("LC_ALL", "C"))


def _valid_argv(argv: tuple[object, ...]) -> bool:
    """Validate at most the fixed argument/UTF-8 budgets without large copies."""

    remaining = _MAX_ARGUMENT_BYTES
    for index, value in enumerate(argv):
        if type(value) is not str or not value:
            return False
        character_count = len(value)
        if character_count + 1 > remaining:
            return False
        if index == 0 and value not in _TRUSTED_EXECUTABLES:
            return False
        encoded_bytes = 0
        for character in value:
            _argument_character_checked()
            if character == "\x00":
                return False
            encoded_bytes += len(character.encode("utf-8"))
            if encoded_bytes + 1 > remaining:
                return False
        remaining -= encoded_bytes + 1
    return True


def _argument_character_checked() -> None:
    """Deterministic evidence seam for bounded incremental UTF-8 validation."""


def valid_seconds(value: object, *, minimum: float) -> bool:
    try:
        return type(value) in {int, float} and minimum <= value <= 60.0
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


__all__ = ["SupervisorRequest", "valid_seconds", "validate_request"]
