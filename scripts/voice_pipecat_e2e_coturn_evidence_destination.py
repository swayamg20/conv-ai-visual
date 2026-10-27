"""Harmless preallocation for one exact Coturn probe parser destination."""

from __future__ import annotations

import threading

_TOKEN = object()


class _ProbeParserDestinationClaim:
    """One-shot identity claim issued with a harmless parser destination."""

    __slots__ = ("_lock", "_parser", "_state")

    def __init__(self, token: object, parser: object) -> None:
        if token is not _TOKEN:
            raise TypeError("Coturn probe parser destination is factory-owned")
        self._lock = threading.Lock()
        self._parser: object | None = parser
        self._state = "issued"

    def _claim(self, parser: object) -> bool:
        with self._lock:
            if self._state != "issued" or self._parser is not parser:
                return False
            try:
                valid = bool(
                    parser._state is None
                    and type(parser._line) is bytearray
                    and not parser._line
                    and parser._failed is True
                    and parser._finish_owner is None
                    and parser._finished is False
                    and parser._probe_only is False
                    and parser._probe_result_slot is None
                    and type(parser._record_count) is int
                    and parser._record_count == 0
                    and parser._probe_destination_claim is self
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                return False
            if not valid:
                return False
            self._state = "claimed"
            self._parser = None
            return True


def new_probe_parser_destination(parser_type: type[object]) -> object:
    """Build only scrub-safe empty slots before any evidence input is adopted."""

    parser = object.__new__(parser_type)
    parser._state = None
    parser._line = bytearray()
    parser._failed = True
    parser._finish_owner = None
    parser._finished = False
    parser._probe_only = False
    parser._probe_result_slot = None
    parser._record_count = 0
    parser._probe_destination_claim = _ProbeParserDestinationClaim(_TOKEN, parser)
    return parser


def claim_probe_parser_destination(parser: object) -> bool:
    """Consume the factory-issued identity claim for one harmless destination."""

    try:
        claim = parser._probe_destination_claim
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return bool(type(claim) is _ProbeParserDestinationClaim and claim._claim(parser))


__all__: list[str] = []
