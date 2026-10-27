"""Shape checks for live and terminal concrete process-grant admissions."""

from __future__ import annotations

import weakref


def _process_grant_admission_has_shape(
    admission: object,
    parent: object,
    process_destination: object,
    claim: object,
    pair_key: object,
    roles: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
        _RelayConcreteInvocationGrant,
    )
    from scripts.voice_pipecat_e2e_relay_invocation_process_state import (
        _RelayProcessOwner,
    )

    try:
        if type(admission) is not tuple or len(admission) != 6:
            return False
        reference = admission[5]
        owner = reference() if type(reference) is weakref.ReferenceType else None
        return bool(
            admission[0] is process_destination
            and admission[1] is claim
            and admission[2] is pair_key
            and admission[3] is roles
            and type(admission[4]) is _RelayConcreteInvocationGrant
            and admission[4]._destination is parent
            and admission[4]._pair_key is pair_key
            and (reference is None or type(reference) is weakref.ReferenceType)
            and (
                owner is None
                or (
                    type(owner) is _RelayProcessOwner
                    and owner._destination is process_destination
                    and owner._claim is claim
                )
            )
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


def _terminal_process_grant_admission_matches(
    admission: object,
    parent: object,
    process_destination: object,
    claim: object,
    grant: object,
    roles: object,
) -> bool:
    from scripts.voice_pipecat_e2e_relay_invocation_process_pair_values import (
        _RelayConcreteInvocationGrant,
    )
    from scripts.voice_pipecat_e2e_relay_invocation_process_state import (
        _RelayProcessOwner,
    )

    try:
        if (
            type(admission) is not tuple
            or len(admission) != 6
            or type(admission[0]) is not str
            or type(admission[3]) is not weakref.ReferenceType
            or type(admission[4]) is not tuple
            or (admission[5] is not None and type(admission[5]) is not weakref.ReferenceType)
            or type(roles) is not tuple
            or len(admission[4]) != len(roles)
            or len(roles) != 3
        ):
            return False
        owner = admission[5]() if type(admission[5]) is weakref.ReferenceType else None
        return bool(
            admission[0] == "sealed"
            and admission[1] is process_destination
            and admission[2] is claim
            and admission[3]() is grant
            and all(
                type(reference) is weakref.ReferenceType
                and type(entry) is tuple
                and len(entry) == 2
                and reference() is entry[1]
                for reference, entry in zip(admission[4], roles, strict=True)
            )
            and type(grant) is _RelayConcreteInvocationGrant
            and grant._destination is parent
            and (admission[5] is not None or process_destination._is_sealed_empty())
            and (
                owner is None
                or (
                    type(owner) is _RelayProcessOwner
                    and owner._destination is process_destination
                    and owner._claim is claim
                )
            )
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False


__all__: list[str] = []
