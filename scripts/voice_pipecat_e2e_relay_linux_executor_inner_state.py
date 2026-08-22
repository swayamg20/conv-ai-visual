"""Canonical private state for one consumed executor's inner relay owner."""

from __future__ import annotations

import weakref

from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_anchor import (
    _executor_inner_authority_anchor,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_authority import (
    _authority_values_have_shape,
    _call_values_match,
    _inner_authority_values,
    _live_inner_authority_core_matches,
    _live_inner_authority_matches,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_values import (
    _EVIDENCE_TOKEN,
    _RESULT_TOKEN,
    _RelayLinuxExecutorInnerEvidence,
    _RelayLinuxExecutorInnerResultDestination,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _LOCK,
    _RETIRED_KEYS,
    _RelayLinuxExecutorKey,
)
from scripts.voice_pipecat_e2e_relay_owner_settlement import (
    _relay_probe_destination_and_registry_are_empty,
    _relay_probe_owner_settlement_matches,
)
from scripts.voice_pipecat_e2e_relay_owner_state import (
    RelayProbeOwner,
)
from scripts.voice_pipecat_e2e_relay_owner_values import RelayProbeObservation

_FAILURE = "Relay Linux executor inner ownership is invalid"


_INNER_RECORDS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[
        _RelayLinuxExecutorInnerEvidence,
        RelayProbeOwner | None,
        str,
    ],
] = weakref.WeakKeyDictionary()
_INNER_RESULTS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorInnerResultDestination,
] = weakref.WeakKeyDictionary()
_INNER_TERMINALS: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[
        _RelayLinuxExecutorInnerResultDestination,
        RelayProbeObservation | None,
        str,
        object,
        tuple[object, ...],
        weakref.ReferenceType[_RelayLinuxExecutorKey],
    ],
] = weakref.WeakKeyDictionary()
_INNER_AUTHORITIES: weakref.WeakKeyDictionary[
    _RelayLinuxExecutorKey,
    tuple[object, ...],
] = weakref.WeakKeyDictionary()


def _new_inner_result_destination(
    key: _RelayLinuxExecutorKey,
) -> _RelayLinuxExecutorInnerResultDestination:
    with _LOCK:
        existing = _INNER_RESULTS.get(key)
        if existing is not None:
            if existing._key_ref() is not key:
                raise TypeError(_FAILURE)
            return existing
        if any(
            type(candidate) is not _RelayLinuxExecutorKey
            or candidate not in _RETIRED_KEYS
            or not _terminal_entry_matches(
                candidate,
                destination,
                _INNER_TERMINALS.get(candidate),
            )
            for candidate, destination in _INNER_RESULTS.items()
        ) or any(candidate not in _INNER_RESULTS for candidate in _INNER_TERMINALS):
            raise TypeError(_FAILURE)
        if any(
            candidate not in _INNER_RESULTS
            or not _retired_terminal_authority_matches(candidate, authority)
            for candidate, authority in _INNER_AUTHORITIES.items()
        ):
            raise TypeError(_FAILURE)
        destination = _RelayLinuxExecutorInnerResultDestination(_RESULT_TOKEN, key)
        _store_inner_result(key, destination)
        if _INNER_RESULTS.get(key) is not destination:
            raise TypeError(_FAILURE)
        return destination


def _new_inner_evidence(**values: object) -> _RelayLinuxExecutorInnerEvidence:
    return _RelayLinuxExecutorInnerEvidence(_EVIDENCE_TOKEN, **values)  # type: ignore[arg-type]


def _intend_inner_owner(evidence: _RelayLinuxExecutorInnerEvidence) -> bool:
    with _LOCK:
        authority = _INNER_AUTHORITIES.get(evidence.key)
        if authority is None:
            if any(
                candidate is not evidence.key
                and not _retired_terminal_authority_matches(candidate, candidate_authority)
                for candidate, candidate_authority in _INNER_AUTHORITIES.items()
            ):
                return False
            _store_inner_authority(
                evidence.key,
                (evidence, _inner_authority_values(evidence), "live"),
            )
            authority = _INNER_AUTHORITIES.get(evidence.key)
        anchor = _executor_inner_authority_anchor(evidence.key)
        if not (
            type(authority) is tuple
            and len(authority) == 3
            and type(authority[1]) is tuple
            and anchor is not None
            and anchor._bind(authority[1]) is authority[1]
            and evidence.result_destination._bind_replay_values(authority[1])
        ):
            return False
        if not _live_inner_authority_matches(evidence, authority):
            return False
        current = _INNER_RECORDS.get(evidence.key)
        if current is None:
            if _INNER_RECORDS:
                return False
            _store_inner_record(evidence.key, (evidence, None, "inner-intended"))
            current = _INNER_RECORDS.get(evidence.key)
        return _inner_record_matches(current, evidence, None, "inner-intended")


def _retain_inner_owner(
    evidence: _RelayLinuxExecutorInnerEvidence,
    owner: RelayProbeOwner,
) -> bool:
    with _LOCK:
        current = _INNER_RECORDS.get(evidence.key)
        if _inner_record_matches(current, evidence, owner, "inner-owned"):
            return True
        if not _inner_record_matches(current, evidence, None, "inner-intended"):
            return False
        record = (evidence, owner, "inner-owned")
        _store_inner_record(evidence.key, record)
        return _inner_record_matches(
            _INNER_RECORDS.get(evidence.key),
            evidence,
            owner,
            "inner-owned",
        )


def _settle_inner_owner(
    evidence: _RelayLinuxExecutorInnerEvidence,
    owner: RelayProbeOwner | None,
    observation: RelayProbeObservation | None,
) -> bool:
    if not _inner_evidence_authority_matches(evidence):
        return False
    result = evidence.result_destination
    published = (
        result._publish_observed(observation)
        if type(observation) is RelayProbeObservation
        else result._publish_failed()
    )
    if not published:
        return False
    terminal = _store_or_match_terminal(evidence.key, result)
    if terminal is None:
        return False
    with _LOCK:
        current = _INNER_RECORDS.get(evidence.key)
        if _inner_record_matches(current, evidence, owner, "inner-settled"):
            return True
        if not (
            _inner_record_matches(current, evidence, owner, "inner-owned")
            or (
                owner is None
                and _inner_record_matches(current, evidence, None, "inner-intended")
                and observation is None
                and _relay_probe_destination_and_registry_are_empty(evidence.owner_destination)
            )
        ):
            return False
        record = (evidence, owner, "inner-settled")
        _store_inner_record(evidence.key, record)
        return _inner_record_matches(
            _INNER_RECORDS.get(evidence.key),
            evidence,
            owner,
            "inner-settled",
        )


def _retire_settled_inner(evidence: _RelayLinuxExecutorInnerEvidence) -> bool:
    with _LOCK:
        record = _INNER_RECORDS.get(evidence.key)
        if record is None:
            return bool(
                not _INNER_RECORDS
                and _INNER_RESULTS.get(evidence.key) is evidence.result_destination
                and _inner_result(evidence.key) is not None
                and _terminal_inner_authority_matches(
                    evidence.key,
                    _INNER_AUTHORITIES.get(evidence.key),
                )
            )
        if not (
            type(record) is tuple
            and len(record) == 3
            and record[0] is evidence
            and (type(record[1]) is RelayProbeOwner or record[1] is None)
            and record[2] == "inner-settled"
            and _INNER_RESULTS.get(evidence.key) is evidence.result_destination
            and _inner_result(evidence.key) is not None
        ):
            return False
        authority = _INNER_AUTHORITIES.get(evidence.key)
        if _live_inner_authority_matches(evidence, authority):
            _store_inner_authority(
                evidence.key,
                (
                    evidence.result_destination,
                    authority[1],
                    "terminal",
                    evidence.result_destination._terminal_token,
                ),
            )
            authority = _INNER_AUTHORITIES.get(evidence.key)
        if not _terminal_inner_authority_matches(evidence.key, authority):
            return False
        _pop_inner_record(evidence.key)
        return evidence.key not in _INNER_RECORDS


def _inner_record(
    key: _RelayLinuxExecutorKey,
) -> tuple[_RelayLinuxExecutorInnerEvidence, RelayProbeOwner | None, str] | None:
    with _LOCK:
        return _INNER_RECORDS.get(key)


def _inner_result(
    key: _RelayLinuxExecutorKey,
) -> tuple[RelayProbeObservation | None, str] | None:
    with _LOCK:
        destination = _INNER_RESULTS.get(key)
        terminal = _INNER_TERMINALS.get(key)
        authority = _INNER_AUTHORITIES.get(key)
        if not (
            _terminal_entry_matches(key, destination, terminal)
            and (
                _terminal_inner_authority_matches(key, authority)
                or _settled_live_authority_matches(key, authority, destination)
            )
        ):
            return None
        return destination._read()


def _recover_live_inner_evidence(
    key: _RelayLinuxExecutorKey,
) -> _RelayLinuxExecutorInnerEvidence | None:
    with _LOCK:
        authority = _INNER_AUTHORITIES.get(key)
        anchor = _executor_inner_authority_anchor(key)
        if (
            type(authority) is tuple
            and len(authority) == 3
            and type(authority[0]) is _RelayLinuxExecutorInnerEvidence
            and authority[0].key is key
            and _live_inner_authority_core_matches(authority[0], authority)
            and anchor is not None
            and anchor._bind(authority[1]) is authority[1]
            and authority[0].result_destination._bind_replay_values(authority[1])
            and _live_inner_authority_matches(authority[0], authority)
        ):
            return authority[0]
        return None


def _inner_evidence_authority_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> bool:
    with _LOCK:
        return _live_inner_authority_matches(
            evidence,
            _INNER_AUTHORITIES.get(evidence.key),
            allow_terminal=True,
        )


def _inner_replay_inputs_match(
    key: _RelayLinuxExecutorKey,
    *,
    binding: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_driver: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
    require_terminal: bool,
) -> bool:
    with _LOCK:
        authority = _INNER_AUTHORITIES.get(key)
        if require_terminal:
            if not _terminal_inner_authority_matches(key, authority):
                return False
        elif not (
            _terminal_inner_authority_matches(key, authority)
            or (
                type(authority) is tuple
                and len(authority) == 3
                and type(authority[0]) is _RelayLinuxExecutorInnerEvidence
                and _live_inner_authority_matches(authority[0], authority)
            )
        ):
            return False
        values = authority[1]
        return _call_values_match(
            values,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_driver=invocation_driver,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
        )


def _inner_live_evidence_is_absent(key: object) -> bool:
    if type(key) is not _RelayLinuxExecutorKey:
        return False
    with _LOCK:
        if key in _INNER_RECORDS or _INNER_RECORDS:
            return False
        if (
            any(candidate not in _INNER_AUTHORITIES for candidate in _INNER_RESULTS)
            or any(candidate not in _INNER_AUTHORITIES for candidate in _INNER_TERMINALS)
            or any(
                candidate not in _INNER_RESULTS or candidate not in _INNER_TERMINALS
                for candidate in _INNER_AUTHORITIES
            )
        ):
            return False
        for candidate, authority in _INNER_AUTHORITIES.items():
            if candidate is key:
                if not _terminal_inner_authority_matches(key, authority):
                    return False
            elif not _retired_terminal_authority_matches(candidate, authority):
                return False
        return True


def _inner_settlement_matches_build(build: object) -> bool:
    if type(build) is not _RelayLinuxExecutorBuiltEvidence:
        return False
    with _LOCK:
        record = _INNER_RECORDS.get(build.key)
        if not (
            type(record) is tuple
            and len(record) == 3
            and type(record[0]) is _RelayLinuxExecutorInnerEvidence
            and record[0].build is build
            and (type(record[1]) is RelayProbeOwner or record[1] is None)
            and record[2] == "inner-settled"
            and _INNER_RESULTS.get(build.key) is record[0].result_destination
        ):
            return False
        evidence, owner = record[:2]
        result = _inner_result(build.key)
    if type(result) is not tuple or len(result) != 2 or type(result[1]) is not str:
        return False
    observation = result[0]
    if not (
        (result[1] == "observed" and type(observation) is RelayProbeObservation)
        or (result[1] == "failed" and observation is None)
    ):
        return False
    if owner is None:
        return bool(
            observation is None
            and _relay_probe_destination_and_registry_are_empty(evidence.owner_destination)
        )
    return _relay_probe_owner_settlement_matches(owner, evidence.owner_destination, observation)


def _inner_record_matches(
    record: object,
    evidence: _RelayLinuxExecutorInnerEvidence,
    owner: RelayProbeOwner | None,
    phase: str,
) -> bool:
    return bool(
        type(record) is tuple
        and len(record) == 3
        and record[0] is evidence
        and record[1] is owner
        and type(record[2]) is str
        and record[2] == phase
        and _INNER_RESULTS.get(evidence.key) is evidence.result_destination
        and _live_inner_authority_matches(
            evidence,
            _INNER_AUTHORITIES.get(evidence.key),
            allow_terminal=True,
        )
    )


def _store_inner_record(key: object, record: tuple[object, ...]) -> None:
    _INNER_RECORDS[key] = record  # type: ignore[index,assignment]


def _pop_inner_record(key: object) -> None:
    _INNER_RECORDS.pop(key, None)


def _store_inner_result(
    key: object,
    destination: _RelayLinuxExecutorInnerResultDestination,
) -> None:
    _INNER_RESULTS[key] = destination  # type: ignore[index]


def _store_inner_terminal(key: object, terminal: tuple[object, ...]) -> None:
    _INNER_TERMINALS[key] = terminal  # type: ignore[index,assignment]


def _store_inner_authority(key: object, authority: tuple[object, ...]) -> None:
    _INNER_AUTHORITIES[key] = authority  # type: ignore[index,assignment]


def _store_or_match_terminal(
    key: _RelayLinuxExecutorKey,
    destination: _RelayLinuxExecutorInnerResultDestination,
) -> (
    tuple[
        _RelayLinuxExecutorInnerResultDestination,
        RelayProbeObservation | None,
        str,
        object,
        tuple[object, ...],
        weakref.ReferenceType[_RelayLinuxExecutorKey],
    ]
    | None
):
    with _LOCK:
        record = destination._read()
        if type(record) is not tuple or len(record) != 2 or type(record[1]) is not str:
            return None
        authority = _INNER_AUTHORITIES.get(key)
        if not _inner_authority_has_destination(key, authority, destination):
            return None
        values = authority[1]
        terminal = _INNER_TERMINALS.get(key)
        candidate = (
            destination,
            record[0],
            record[1],
            destination._terminal_token,
            values,
            weakref.ref(key),
        )
        if terminal is None:
            if any(
                candidate_key is not key
                and (
                    candidate_key not in _RETIRED_KEYS
                    or not _terminal_entry_matches(
                        candidate_key,
                        _INNER_RESULTS.get(candidate_key),
                        candidate_terminal,
                    )
                )
                for candidate_key, candidate_terminal in _INNER_TERMINALS.items()
            ):
                return None
            _store_inner_terminal(key, candidate)
            terminal = _INNER_TERMINALS.get(key)
        return terminal if _terminal_entry_matches(key, destination, terminal) else None


def _terminal_entry_matches(
    key: object,
    destination: object,
    terminal: object,
) -> bool:
    if (
        type(key) is not _RelayLinuxExecutorKey
        or type(destination) is not _RelayLinuxExecutorInnerResultDestination
        or type(terminal) is not tuple
        or len(terminal) != 6
        or terminal[0] is not destination
        or terminal[3] is not destination._terminal_token
        or destination._key_ref() is not key
        or type(terminal[5]) is not weakref.ReferenceType
        or terminal[5]() is not key
    ):
        return False
    record = destination._read()
    return bool(
        type(record) is tuple
        and len(record) == 2
        and record[0] is terminal[1]
        and type(record[1]) is str
        and type(terminal[2]) is str
        and record[1] == terminal[2]
        and _authority_values_have_shape(terminal[4])
        and (
            (record[1] == "observed" and type(record[0]) is RelayProbeObservation)
            or (record[1] == "failed" and record[0] is None)
        )
    )


def _terminal_inner_authority_matches(key: object, authority: object) -> bool:
    terminal = _INNER_TERMINALS.get(key)
    anchor = _executor_inner_authority_anchor(key)
    return bool(
        type(key) is _RelayLinuxExecutorKey
        and type(authority) is tuple
        and len(authority) == 4
        and type(authority[0]) is _RelayLinuxExecutorInnerResultDestination
        and _authority_values_have_shape(authority[1])
        and authority[1][13] is authority[0]
        and type(authority[2]) is str
        and authority[2] == "terminal"
        and authority[3] is authority[0]._terminal_token
        and anchor is not None
        and anchor._matches(authority[1])
        and authority[0]._key_ref() is key
        and authority[0]._replay_values_are(authority[1])
        and type(terminal) is tuple
        and len(terminal) == 6
        and terminal[4] is authority[1]
        and _INNER_RESULTS.get(key) is authority[0]
        and _terminal_entry_matches(
            key,
            authority[0],
            terminal,
        )
    )


def _retired_terminal_authority_matches(key: object, authority: object) -> bool:
    return bool(key in _RETIRED_KEYS and _terminal_inner_authority_matches(key, authority))


def _inner_authority_has_destination(
    key: _RelayLinuxExecutorKey,
    authority: object,
    destination: object,
) -> bool:
    if _terminal_inner_authority_matches(key, authority):
        return authority[0] is destination
    return bool(
        type(authority) is tuple
        and len(authority) == 3
        and type(authority[0]) is _RelayLinuxExecutorInnerEvidence
        and authority[0].key is key
        and authority[0].result_destination is destination
        and _live_inner_authority_matches(authority[0], authority)
    )


def _settled_live_authority_matches(
    key: _RelayLinuxExecutorKey,
    authority: object,
    destination: object,
) -> bool:
    if not (
        type(authority) is tuple
        and len(authority) == 3
        and type(authority[0]) is _RelayLinuxExecutorInnerEvidence
        and _live_inner_authority_matches(authority[0], authority)
        and authority[0].key is key
        and authority[0].result_destination is destination
    ):
        return False
    record = _INNER_RECORDS.get(key)
    return bool(
        type(record) is tuple
        and len(record) == 3
        and record[0] is authority[0]
        and (type(record[1]) is RelayProbeOwner or record[1] is None)
        and type(record[2]) is str
        and record[2] == "inner-settled"
    )


__all__: list[str] = []
