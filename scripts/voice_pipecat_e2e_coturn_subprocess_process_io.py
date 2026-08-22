"""Nonblocking pipe and process inspection operations for the sole supervisor."""

from __future__ import annotations

import os
import selectors
import threading
from collections.abc import Callable
from typing import BinaryIO, Protocol

from scripts.voice_pipecat_e2e_coturn_subprocess_state import Lifecycle
from scripts.voice_pipecat_e2e_coturn_subprocess_values import (
    CHUNK_BYTES,
    MAX_LIFETIME_CHUNKS,
    SubprocessChunk,
)


class SelectorKeyLike(Protocol):
    fileobj: BinaryIO
    data: object
    events: int


class SelectorLike(Protocol):
    def register(self, fileobj: BinaryIO, events: int, data: object = None) -> object: ...

    def unregister(self, fileobj: BinaryIO) -> object: ...

    def get_key(self, fileobj: BinaryIO) -> SelectorKeyLike: ...

    def select(self, timeout: float | None = None) -> list[tuple[SelectorKeyLike, int]]: ...

    def close(self) -> None: ...


SelectorFactory = Callable[[], SelectorLike]


def local_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    return True


def local_group_identity(process_id: int) -> int:
    return os.getpgid(process_id)


def local_set_blocking(stream: BinaryIO, blocking: bool) -> None:
    os.set_blocking(stream.fileno(), blocking)


class SupervisorIOMixin:
    """Implementation mixin; instances remain private to the supervisor thread."""

    def _io_step(self, *, publish: bool) -> None:
        selector = self._selector
        if selector is None or self._selector_closed:
            return
        try:
            events = selector.select(self._poll_seconds)
            validated = self._validated_events(events)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return
        except BaseException:
            self._controller.fail("Coturn subprocess selector failed")
            return
        output_backpressured = False
        for name, stream in validated:
            if name == "stdin":
                self._write_input(stream)
            else:
                if name in self._eof:
                    if not self._close_stream(name, require_eof=True):
                        output_backpressured = True
                    continue
                if publish and self._controller.queue_full():
                    output_backpressured = True
                    continue
                self._read_output(name, stream, publish=publish)
        if output_backpressured:
            threading.Event().wait(self._poll_seconds)

    def _validated_events(self, events: object) -> tuple[tuple[str, BinaryIO], ...]:
        if type(events) is not list or len(events) > len(self._registered):
            raise TypeError
        validated: list[tuple[str, BinaryIO]] = []
        seen: set[str] = set()
        for event in events:
            if type(event) is not tuple or len(event) != 2:
                raise TypeError
            key, mask = event
            name = key.data
            stream = key.fileobj
            key_mask = key.events
            if type(name) is not str or name not in self._registered or name in seen:
                raise TypeError
            expected = selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ
            if (
                name not in {"stdin", "stdout", "stderr"}
                or stream is not self._streams[name]
                or type(mask) is not int
                or mask != expected
                or type(key_mask) is not int
                or key_mask != expected
            ):
                raise TypeError
            seen.add(name)
            validated.append((name, stream))
        return tuple(validated)

    def _write_input(self, stream: BinaryIO) -> None:
        try:
            if self._input_offset >= len(self._input):
                self._close_stream("stdin", require_eof=False)
                self._input.clear()
                return
            end = min(len(self._input), self._input_offset + CHUNK_BYTES)
            offered = end - self._input_offset
            view = memoryview(self._input)[self._input_offset : end]
            try:
                value = stream.write(view)
            finally:
                view.release()
            if value is None:
                return
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= offered:
                raise OSError
            self._input_offset += value
            if self._input_offset >= len(self._input):
                stream.flush()
                self._close_stream("stdin", require_eof=False)
                self._input.clear()
        except BlockingIOError:
            return
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
        except BaseException:
            self._controller.fail("Coturn subprocess input failed")

    def _read_output(self, name: str, stream: BinaryIO, *, publish: bool) -> None:
        try:
            chunk = stream.read(CHUNK_BYTES)
        except BlockingIOError:
            return
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return
        except BaseException:
            self._controller.fail("Coturn subprocess stream failed")
            return
        if chunk is None:
            return
        if type(chunk) is not bytes:
            self._controller.fail("Coturn subprocess stream failed")
            return
        if not chunk:
            self._eof.add(name)
            if not self._close_stream(name, require_eof=True):
                threading.Event().wait(self._poll_seconds)
            return
        if not 1 <= len(chunk) <= CHUNK_BYTES:
            self._controller.fail("Coturn subprocess stream failed")
            return
        if not publish:
            return
        self._output_bytes += len(chunk)
        self._lifetime_chunks += 1
        if self._output_bytes > self._maximum_output:
            self._controller.fail("Coturn subprocess output limit exceeded")
            return
        if self._lifetime_chunks > MAX_LIFETIME_CHUNKS:
            self._controller.fail("Coturn subprocess chunk limit exceeded")
            return
        try:
            published = self._controller.publish_chunk(SubprocessChunk(name, chunk))
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
        except BaseException:
            self._controller.fail("Coturn subprocess stream failed")
        else:
            if (
                not published
                and not self._controller.termination_requested()
                and self._controller.lifecycle() is Lifecycle.ACTIVE
            ):
                self._controller.fail("Coturn subprocess stream failed")

    def _inspect_process(self) -> bool:
        process = self._process
        if process is None:
            return False
        pid = self._control_retry(lambda: process.pid, None)
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return False
        pgid = self._control_retry(lambda: self._seams.group_identity(pid), None)
        if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid != pid:
            return False
        self._pid = pid
        self._pgid = pgid
        streams: dict[str, BinaryIO | None] = {}
        for name in ("stdin", "stdout", "stderr"):
            stream = self._control_retry(lambda name=name: getattr(process, name), None)
            if stream is None or not callable(getattr(stream, "close", None)):
                return False
            streams[name] = stream
        if not self._set_process_attribute("args", ()):
            return False
        self._streams.update(streams)
        return True

    def _prepare_io(self, *, require_stdin: bool) -> bool:
        selector = self._selector
        if selector is None:
            try:
                self._selector_closed = False
                selector = self._seams.selector_factory()
                self._selector = selector
                _selector_acquired()
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
                return False
            except BaseException:
                return False
        if not self._validate_selector(selector):
            return False
        targets = ("stdout", "stderr", "stdin") if require_stdin else ("stdout", "stderr")
        for name in targets:
            stream = self._streams[name]
            if name in self._eof:
                if stream is not None and not self._close_stream(name, require_eof=True):
                    return False
                continue
            if stream is None:
                if name == "stdin" and not require_stdin:
                    continue
                return False
            if not self._ensure_registered(selector, name, stream):
                return False
        return True

    def _validate_selector(self, selector: object) -> bool:
        methods = ("register", "unregister", "get_key", "select", "close")
        try:
            valid = all(callable(getattr(selector, name, None)) for name in methods)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            valid = False
        except BaseException:
            valid = False
        if valid:
            return True
        try:
            close = getattr(selector, "close", None)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        if not callable(close):
            return False
        try:
            close()
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        self._selector = None
        self._selector_closed = True
        return False

    def _ensure_registered(
        self,
        selector: SelectorLike,
        name: str,
        stream: BinaryIO,
    ) -> bool:
        try:
            self._seams.set_blocking(stream, False)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        try:
            key = selector.get_key(stream)
        except KeyError:
            key = None
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        mask = selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ
        if key is not None:
            if self._key_matches(key, name, stream, mask):
                self._registered.add(name)
                return True
            if self._controller.termination_requested():
                return False
            if not self._unregister_stream(selector, name, stream):
                return False
        try:
            selector.register(stream, mask, name)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        try:
            key = selector.get_key(stream)
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        if not self._key_matches(key, name, stream, mask):
            return False
        self._registered.add(name)
        return True

    def _key_matches(
        self,
        key: object,
        name: str,
        stream: BinaryIO,
        mask: int,
    ) -> bool:
        try:
            return (
                key.data == name
                and key.fileobj is stream
                and type(key.events) is int
                and key.events == mask
            )
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False

    def _unregister_stream(
        self,
        selector: SelectorLike,
        name: str,
        stream: BinaryIO,
    ) -> bool:
        try:
            selector.get_key(stream)
        except KeyError:
            self._registered.discard(name)
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        try:
            selector.unregister(stream)
        except KeyError:
            pass
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        try:
            selector.get_key(stream)
        except KeyError:
            self._registered.discard(name)
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
        except BaseException:
            pass
        return False

    def _reap_step(self) -> None:
        process = self._process
        if process is None or self._returncode is not None:
            return
        value = self._normalize_returncode(self._control_retry(process.poll, None))
        if value is None:
            return
        reaped = self._normalize_returncode(
            self._control_retry(lambda: process.wait(timeout=0.0), None)
        )
        if reaped is None:
            self._controller.fail("Coturn subprocess execution failed")
            return
        self._returncode = reaped
        self._controller.observe_returncode(reaped)

    def _normalize_returncode(self, value: object) -> int | None:
        if type(value) is int:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        while True:
            try:
                normalized = int(value)
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
            except BaseException:
                return None
            else:
                return normalized if type(normalized) is int else None

    def _group_exists(self) -> bool | None:
        if self._group_absent:
            return False
        pgid = self._pgid
        if pgid is None:
            return None
        exists = self._control_retry(lambda: self._seams.group_exists(pgid), None)
        if exists is False:
            self._group_absent = True
            self._pgid = None
        return exists

    def _known_no_child(self) -> bool:
        process = self._process
        if process is None:
            return True
        value = self._control_retry(lambda: process._child_created, None)
        return value is False

    def _close_stream(self, name: str, *, require_eof: bool) -> bool:
        stream = self._streams[name]
        if stream is None:
            return True
        if require_eof and name not in self._eof:
            return False
        selector = self._selector
        if selector is not None and not self._selector_closed:
            if not self._unregister_stream(selector, name, stream):
                return False
        try:
            stream.close()
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        if not self._set_process_attribute(name, None):
            return False
        self._streams[name] = None
        return True

    def _close_selector(self) -> bool:
        if self._selector_closed:
            return True
        selector = self._selector
        if selector is None:
            return False
        for name, stream in tuple(self._streams.items()):
            if stream is not None and name in self._registered:
                if not self._unregister_stream(selector, name, stream):
                    return False
        if self._registered:
            return False
        try:
            selector.close()
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False
        self._selector_closed = True
        self._selector = None
        return True

    def _scrub_process(self) -> bool:
        process = self._process
        if process is None:
            return True
        if not self._set_process_attribute("args", ()):
            return False
        if any(self._streams.values()):
            return False
        self._process = None
        self._pid = None
        self._pgid = None
        return True

    def _set_process_attribute(self, name: str, value: object) -> bool:
        process = self._process
        if process is None:
            return False
        try:
            setattr(process, name, value)
            return True
        except (KeyboardInterrupt, SystemExit) as error:
            self._controller.capture_control(error)
            return False
        except BaseException:
            return False

    def _control_retry(self, operation: Callable[[], object], default: object) -> object:
        while True:
            try:
                return operation()
            except (KeyboardInterrupt, SystemExit) as error:
                self._controller.capture_control(error)
            except BaseException:
                return default

    # Structural declarations for reviewers and static tooling. Concrete state is
    # initialized only by the private supervisor class.
    _controller: object
    _eof: set[str]
    _group_absent: bool
    _input: bytearray
    _input_offset: int
    _lifetime_chunks: int
    _maximum_output: int
    _output_bytes: int
    _pgid: int | None
    _pid: int | None
    _poll_seconds: float
    _process: object
    _returncode: int | None
    _registered: set[str]
    _seams: object
    _selector: object
    _selector_closed: bool
    _streams: dict[str, BinaryIO | None]


def _selector_acquired() -> None:
    """Deterministic control seam after selector authority is retained."""


__all__ = [
    "SelectorFactory",
    "SelectorLike",
    "SupervisorIOMixin",
    "local_group_exists",
    "local_group_identity",
    "local_set_blocking",
]
