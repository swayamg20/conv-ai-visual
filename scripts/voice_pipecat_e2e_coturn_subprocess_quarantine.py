"""Round-robin retained cleanup for contradictory spawn identities."""

from __future__ import annotations

import signal
import threading
from typing import BinaryIO

from scripts.voice_pipecat_e2e_coturn_subprocess_spawn import ProcessLike
from scripts.voice_pipecat_e2e_coturn_subprocess_state import Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    TERMINATION_GRACE_SECONDS,
)


class _CandidateAuthority:
    """Private raw authority saved between sole-supervisor cleanup steps."""

    __slots__ = (
        "eof",
        "group_absent",
        "input",
        "input_offset",
        "pgid",
        "pid",
        "process",
        "registered",
        "returncode",
        "selector",
        "selector_closed",
        "streams",
        "term_deadline",
    )

    def __init__(self, process: ProcessLike) -> None:
        self.process: ProcessLike | None = process
        self.pid: int | None = None
        self.pgid: int | None = None
        self.returncode: int | None = None
        self.selector: object | None = None
        self.selector_closed = False
        self.registered: set[str] = set()
        self.streams: dict[str, BinaryIO | None] = {
            "stdin": None,
            "stdout": None,
            "stderr": None,
        }
        self.eof: set[str] = set()
        self.group_absent = False
        self.input = bytearray()
        self.input_offset = 0
        self.term_deadline: float | None = None

    def __repr__(self) -> str:
        return "_CandidateAuthority()"


class CandidateCleanupMixin:
    """Step every malformed candidate so one unknown PID cannot block another."""

    def _quarantine_unowned(self, candidates: tuple[ProcessLike, ...]) -> None:
        self._enter_quarantine()
        pending = [_CandidateAuthority(candidate) for candidate in candidates]
        returncodes: list[int] = []
        candidates = ()
        while pending and self._controller.lifecycle() is Lifecycle.QUARANTINED:
            for authority in tuple(pending):
                clean, returncode = self._candidate_step(authority)
                if not clean:
                    continue
                pending.remove(authority)
                if returncode is not None:
                    returncodes.append(returncode)
            if pending:
                self._quarantine_pause()
        pending.clear()
        self._reset_candidate_state()
        self._finish_discarded(tuple(returncodes))

    def _candidate_step(self, authority: _CandidateAuthority) -> tuple[bool, int | None]:
        self._load_candidate(authority)
        clean = False
        returncode: int | None = None
        if self._known_no_child():
            clean = self._close_partial_process()
        else:
            if self._pgid is None and not self._group_absent and not self._inspect_process():
                self._save_candidate(authority)
                return False, None
            if not self._prepare_io(require_stdin=self._streams["stdin"] is not None):
                self._save_candidate(authority)
                return False, None
            self._close_stream("stdin", require_eof=False)
            self._reap_step()
            exists = self._group_exists()
            if exists is not False and authority.term_deadline is None:
                self._signal(signal.SIGTERM)
                current = self._now()
                authority.term_deadline = (
                    None if current is None else current + TERMINATION_GRACE_SECONDS
                )
            current = self._now()
            if exists is not False and (
                authority.term_deadline is None
                or current is None
                or current >= authority.term_deadline
            ):
                self._signal(signal.SIGKILL)
            self._io_step(publish=False)
            self._reap_step()
            clean = self._candidate_clean()
            if clean:
                value = self._returncode
                if isinstance(value, bool) or not isinstance(value, int):
                    clean = False
                else:
                    returncode = value
        self._save_candidate(authority)
        return clean, returncode

    def _candidate_clean(self) -> bool:
        if self._group_exists() is not False or self._returncode is None or not self._io_drained():
            return False
        closed = all(self._close_stream(name, require_eof=True) for name in ("stdout", "stderr"))
        closed = self._close_stream("stdin", require_eof=False) and closed
        closed = self._close_selector() and closed
        if not closed or self._group_exists() is not False:
            return False
        return self._scrub_process()

    def _load_candidate(self, authority: _CandidateAuthority) -> None:
        previous_input = self._input
        if previous_input is not authority.input:
            previous_input.clear()
        self._process = authority.process
        self._pid = authority.pid
        self._pgid = authority.pgid
        self._returncode = authority.returncode
        self._selector = authority.selector
        self._selector_closed = authority.selector_closed
        self._registered = authority.registered
        self._streams = authority.streams
        self._eof = authority.eof
        self._group_absent = authority.group_absent
        self._input = authority.input
        self._input_offset = authority.input_offset

    def _save_candidate(self, authority: _CandidateAuthority) -> None:
        authority.process = self._process
        authority.pid = self._pid
        authority.pgid = self._pgid
        authority.returncode = self._returncode
        authority.selector = self._selector
        authority.selector_closed = self._selector_closed
        authority.registered = self._registered
        authority.streams = self._streams
        authority.eof = self._eof
        authority.group_absent = self._group_absent
        authority.input = self._input
        authority.input_offset = self._input_offset

    def _reset_candidate_state(self) -> None:
        self._process = None
        self._pid = None
        self._pgid = None
        self._returncode = None
        self._selector = None
        self._selector_closed = True
        self._registered = set()
        self._streams = {"stdin": None, "stdout": None, "stderr": None}
        self._eof = set()
        self._group_absent = False
        self._input = bytearray()
        self._input_offset = 0

    def _finish_discarded(self, returncodes: tuple[int, ...]) -> None:
        self._scrub_request()
        self._controller.transition(Lifecycle.VERIFYING)
        if not self._controller.complete_clean(returncodes, None):
            self._enter_quarantine()

    def _quarantine_pause(self) -> None:
        threading.Event().wait(self._quarantine_retry_seconds)


__all__ = ["CandidateCleanupMixin"]
