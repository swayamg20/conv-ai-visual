"""Assertions for the private executor driver's sanitized failure boundary."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _RelayLinuxExecutorError,
)
from tests.coturn_tls_traceback_helpers import traceback_contains

_FAILURE = "Relay Linux executor driver failed"


def assert_sanitized_driver_failure(
    actual: BaseException,
    source: BaseException,
    *secrets: str | bytes,
) -> None:
    assert actual is not source
    if isinstance(source, KeyboardInterrupt):
        assert type(actual) is KeyboardInterrupt and actual.args == ()
    elif isinstance(source, SystemExit):
        code = SystemExit.__dict__["code"].__get__(source, SystemExit)
        expected = code if code is None or type(code) is int else 1
        assert type(actual) is SystemExit and actual.code == expected
    else:
        assert type(actual) is _RelayLinuxExecutorError and actual.args == (_FAILURE,)
    assert actual.__cause__ is None
    assert actual.__context__ is None
    assert not getattr(actual, "__dict__", {})
    assert not getattr(actual, "__notes__", ())
    traceback = actual.__traceback__
    while traceback is not None and "/tests/" in traceback.tb_frame.f_code.co_filename:
        traceback = traceback.tb_next
    if traceback is None:
        probe = actual
    else:
        assert "/scripts/" in traceback.tb_frame.f_code.co_filename
        probe = type(actual)(*actual.args).with_traceback(traceback)
    assert not traceback_contains(probe, *secrets)


def assert_distinct_sanitized_driver_failures(
    actual: object,
    source: BaseException,
    *secrets: str | bytes,
) -> None:
    assert type(actual) in {list, tuple, dict}
    values = tuple(actual.values()) if type(actual) is dict else tuple(actual)
    assert len({id(value) for value in values}) == len(values)
    for value in values:
        assert isinstance(value, BaseException)
        assert_sanitized_driver_failure(value, source, *secrets)


__all__: list[str] = []
