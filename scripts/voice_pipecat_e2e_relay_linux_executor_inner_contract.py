"""Exact input derivation for the private consumed-build relay aggregate."""

from __future__ import annotations

import math
import os
import secrets
from collections.abc import Callable
from datetime import datetime

from scripts.voice_pipecat_e2e_coturn import CoturnContractPaths
from scripts.voice_pipecat_e2e_coturn_host import (
    CoturnRuntimePaths,
    RuntimeIdentity,
    TrustedHostTools,
    require_owned_directory,
)
from scripts.voice_pipecat_e2e_relay_invocation import (
    RelayInvocationDriver,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _TOOLS_TOKEN,
    _synthetic_invocation_driver_matches,
    _synthetic_invocation_pair_matches,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
    _concrete_invocation_pair_matches_inputs,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_values import (
    _is_concrete_invocation_selection,
    _RelayConcreteInvocationSelection,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_binding import (
    _RelayLinuxExecutorBuiltBinding,
    _RelayLinuxExecutorBuiltEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_build_contract import (
    _cleanup_evidence_matches,
    _consumed_binding_matches,
    _evidence_for_binding,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_preparation import (
    _resolve_or_preown_inner_preparation,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_inner_state import (
    _inner_record,
    _inner_replay_inputs_match,
    _intend_inner_owner,
    _new_inner_evidence,
    _recover_live_inner_evidence,
    _RelayLinuxExecutorInnerEvidence,
)
from scripts.voice_pipecat_e2e_relay_linux_executor_state import (
    _canonical_executor_key,
    _RelayLinuxExecutorDestination,
    _RelayLinuxExecutorError,
    _RelayLinuxExecutorKey,
    _RelayLinuxExecutorOwner,
)
from scripts.voice_pipecat_e2e_relay_owner_state import _owner_binding

_FAILURE = "Relay Linux executor inner ownership is invalid"
_MAX_RUNTIME_SECONDS = 60.0
_MAX_CLEANUP_SECONDS = 60.0


def _resolve_or_intend_inner_evidence(
    *,
    executor: _RelayLinuxExecutorOwner,
    destination: _RelayLinuxExecutorDestination,
    binding: _RelayLinuxExecutorBuiltBinding,
    runner: object,
    bridge_probe: object,
    tools: TrustedHostTools,
    invocation_selection: RelayInvocationDriver | _RelayConcreteInvocationSelection,
    static_auth_secret: object,
    now: datetime,
    browser_timeout_seconds: float,
    runtime_timeout_seconds: float,
    cleanup_timeout_seconds: float,
    clock: Callable[[], float],
    wait: Callable[[float], None],
    epoch_clock: Callable[[], float],
) -> _RelayLinuxExecutorInnerEvidence:
    key = _canonical_executor_key(executor, destination)
    if type(key) is not _RelayLinuxExecutorKey:
        raise _RelayLinuxExecutorError(_FAILURE)
    existing = _inner_record(key)
    if existing is not None:
        if not (
            type(existing) is tuple
            and len(existing) == 3
            and type(existing[0]) is _RelayLinuxExecutorInnerEvidence
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        evidence = existing[0]
        if not _inner_inputs_match(
            evidence,
            executor,
            destination,
            binding,
            runner,
            bridge_probe,
            tools,
            invocation_selection,
            static_auth_secret,
            now,
            browser_timeout_seconds,
            runtime_timeout_seconds,
            clock,
            wait,
            epoch_clock,
        ):
            raise _RelayLinuxExecutorError(_FAILURE)
        return evidence
    recovered = _recover_live_inner_evidence(key)
    if recovered is not None:
        if not _inner_inputs_match(
            recovered,
            executor,
            destination,
            binding,
            runner,
            bridge_probe,
            tools,
            invocation_selection,
            static_auth_secret,
            now,
            browser_timeout_seconds,
            runtime_timeout_seconds,
            clock,
            wait,
            epoch_clock,
        ) or not _intend_inner_owner(recovered):
            raise _RelayLinuxExecutorError(_FAILURE)
        return recovered
    build = _evidence_for_binding(binding)
    if not (
        type(build) is _RelayLinuxExecutorBuiltEvidence
        and build.executor is executor
        and build.destination is destination
        and _consumed_binding_matches(build)
        and _cleanup_evidence_matches(build)
        and type(now) is datetime
        and type(browser_timeout_seconds) is float
        and math.isfinite(browser_timeout_seconds)
        and browser_timeout_seconds > 0.0
        and type(runtime_timeout_seconds) is float
        and math.isfinite(runtime_timeout_seconds)
        and 0.0 < runtime_timeout_seconds <= _MAX_RUNTIME_SECONDS
        and type(cleanup_timeout_seconds) is float
        and math.isfinite(cleanup_timeout_seconds)
        and 0.0 < cleanup_timeout_seconds <= _MAX_CLEANUP_SECONDS
        and (
            _synthetic_invocation_driver_matches(invocation_selection)
            or _is_concrete_invocation_selection(invocation_selection)
        )
        and callable(clock)
        and callable(wait)
        and callable(epoch_clock)
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    preparation = _resolve_or_preown_inner_preparation(
        key,
        binding=binding,
        runner=runner,
        bridge_probe=bridge_probe,
        tools=tools,
        invocation_selection=invocation_selection,
        static_auth_secret=static_auth_secret,
        now=now,
        browser_timeout_seconds=browser_timeout_seconds,
        runtime_timeout_seconds=runtime_timeout_seconds,
        clock=clock,
        wait=wait,
        epoch_clock=epoch_clock,
    )
    if preparation is None:
        raise _RelayLinuxExecutorError(_FAILURE)
    result_destination, replay_descriptor, replay_values = preparation
    concrete = _is_concrete_invocation_selection(invocation_selection)
    pair_destination = None
    pair_values: dict[str, object] | None = None
    if concrete:
        from scripts.voice_pipecat_e2e_relay_invocation_process_pair import (
            _recover_concrete_pair_destination,
            _resolve_or_mint_concrete_invocation_pair,
            _resolve_or_preown_concrete_pair_destination,
        )

        stable_pair_values = {
            "build": build,
            "binding": binding,
            "selection": invocation_selection,
            "runtime_timeout_seconds": runtime_timeout_seconds,
            "cleanup_timeout_seconds": cleanup_timeout_seconds,
            "clock": clock,
            "wait": wait,
            "epoch_clock": epoch_clock,
        }
        pair_destination = _recover_concrete_pair_destination(**stable_pair_values)
        if pair_destination is not None:
            runtime_deadline = pair_destination._runtime_deadline
            pair_values = {
                **stable_pair_values,
                "runtime_deadline": runtime_deadline,
                "cleanup_timeout_seconds": pair_destination._cleanup_timeout_seconds,
            }
    if pair_destination is None:
        runtime_deadline = _sample_runtime_deadline(clock, runtime_timeout_seconds)
        if concrete:
            pair_values = {**stable_pair_values, "runtime_deadline": runtime_deadline}
            pair_destination = _resolve_or_preown_concrete_pair_destination(**pair_values)
    request = build.request
    run_id = request._run_id
    workspace = request._workspace
    paths = CoturnRuntimePaths.for_contract(
        CoturnContractPaths.for_run_dir(run_id, workspace / run_id)
    )
    _prepare_runtime_directories(paths)
    identity = RuntimeIdentity.create(
        run_id=run_id,
        owner_nonce=_new_runtime_owner_nonce(),
    )
    if concrete:
        if pair_destination is None or pair_values is None:
            raise _RelayLinuxExecutorError(_FAILURE)
        _grant, effective_invocation_driver, invocation_tools = (
            _resolve_or_mint_concrete_invocation_pair(pair_destination, **pair_values)
        )
        cleanup_timeout_seconds = pair_values["cleanup_timeout_seconds"]  # type: ignore[assignment]
    else:
        effective_invocation_driver = invocation_selection
        invocation_tools = _new_workspace_invocation_tools(build, epoch_clock)
    owner_destination = executor._relay_owner_destination
    owner_binding = _owner_binding(
        build.source,
        runner,
        bridge_probe,
        tools,
        identity,
        paths,
        effective_invocation_driver,
        invocation_tools,
        runtime_deadline,
        clock,
        wait,
    )
    evidence = _new_inner_evidence(
        key=key,
        build=build,
        paths=paths,
        identity=identity,
        runner=runner,
        bridge_probe=bridge_probe,
        browser_timeout_seconds=browser_timeout_seconds,
        tools=tools,
        invocation_selection=invocation_selection,
        effective_invocation_driver=effective_invocation_driver,
        effective_invocation_tools=invocation_tools,
        runtime_deadline=runtime_deadline,
        runtime_timeout_seconds=runtime_timeout_seconds,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        static_auth_secret=static_auth_secret,
        now=now,
        clock=clock,
        wait=wait,
        epoch_clock=epoch_clock,
        owner_binding=owner_binding,
        owner_destination=owner_destination,
        replay_descriptor=replay_descriptor,
        replay_values=replay_values,
        result_destination=result_destination,
    )
    try:
        intended = _intend_inner_owner(evidence)
    except BaseException:
        existing = _inner_record(key)
        if existing is not None and existing[0] is evidence:
            raise
        raise
    if not intended:
        existing = _inner_record(key)
        if existing is None or existing[0] is not evidence:
            raise _RelayLinuxExecutorError(_FAILURE)
    return evidence


def _sample_runtime_deadline(
    clock: Callable[[], float],
    runtime_timeout_seconds: float,
) -> float:
    try:
        runtime_now = clock()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        raise _RelayLinuxExecutorError(_FAILURE) from None
    if type(runtime_now) is not float or not math.isfinite(runtime_now):
        raise _RelayLinuxExecutorError(_FAILURE)
    runtime_deadline = runtime_now + runtime_timeout_seconds
    if not math.isfinite(runtime_deadline) or runtime_deadline <= runtime_now:
        raise _RelayLinuxExecutorError(_FAILURE)
    return runtime_deadline


def _inner_inputs_match(
    evidence: object,
    executor: object,
    destination: object,
    binding: object,
    runner: object,
    bridge_probe: object,
    tools: object,
    invocation_selection: object,
    static_auth_secret: object,
    now: object,
    browser_timeout_seconds: object,
    runtime_timeout_seconds: object,
    clock: object,
    wait: object,
    epoch_clock: object,
) -> bool:
    if type(evidence) is not _RelayLinuxExecutorInnerEvidence:
        return False
    build = evidence.build
    return bool(
        type(build) is _RelayLinuxExecutorBuiltEvidence
        and build.executor is executor
        and build.destination is destination
        and build.binding is binding
        and evidence.runner is runner
        and evidence.bridge_probe is bridge_probe
        and evidence.tools is tools
        and evidence.invocation_selection is invocation_selection
        and _effective_invocation_pair_matches(evidence)
        and _inner_replay_inputs_match(
            evidence.key,
            binding=binding,
            runner=runner,
            bridge_probe=bridge_probe,
            tools=tools,
            invocation_selection=invocation_selection,
            static_auth_secret=static_auth_secret,
            now=now,
            browser_timeout_seconds=browser_timeout_seconds,
            runtime_timeout_seconds=runtime_timeout_seconds,
            clock=clock,
            wait=wait,
            epoch_clock=epoch_clock,
            require_terminal=False,
        )
        and _consumed_binding_matches(build)
        and _cleanup_evidence_matches(build)
    )


def _effective_invocation_pair_matches(
    evidence: _RelayLinuxExecutorInnerEvidence,
) -> bool:
    if _is_concrete_invocation_selection(evidence.invocation_selection):
        return _concrete_invocation_pair_matches_inputs(
            evidence.effective_invocation_driver,
            evidence.effective_invocation_tools,
            build=evidence.build,
            binding=evidence.build.binding,
            selection=evidence.invocation_selection,
            runtime_deadline=evidence.runtime_deadline,
            runtime_timeout_seconds=evidence.runtime_timeout_seconds,
            cleanup_timeout_seconds=evidence.cleanup_timeout_seconds,
            clock=evidence.clock,
            wait=evidence.wait,
            epoch_clock=evidence.epoch_clock,
        )
    return bool(
        evidence.effective_invocation_driver is evidence.invocation_selection
        and _synthetic_invocation_pair_matches(
            evidence.effective_invocation_driver,
            evidence.effective_invocation_tools,
        )
    )


def _new_workspace_invocation_tools(
    build: _RelayLinuxExecutorBuiltEvidence,
    epoch_clock: Callable[[], float],
) -> RelayInvocationTools:
    request = build.request
    workspace = request._workspace
    node_modules = workspace / "node_modules"
    next_cli = node_modules / "next/dist/bin/next"
    playwright_cli = node_modules / "@playwright/test/cli.js"
    if not (
        request._source_root is build.request_values[0]
        and request._workspace is build.request_values[3]
        and request._node is build.request_values[4]
        and request._node_modules is build.request_values[5]
        and request._next_cli is build.request_values[6]
        and request._dist_path is build.request_values[7]
        and request._run_id is build.request_values[8]
        and request._node_modules == request._source_root / "node_modules"
        and next_cli == workspace / request._next_cli.relative_to(request._source_root)
        and playwright_cli
        == workspace
        / request._node_modules.relative_to(request._source_root)
        / "@playwright/test/cli.js"
    ):
        raise _RelayLinuxExecutorError(_FAILURE)
    return RelayInvocationTools(
        _TOOLS_TOKEN,
        node=request._node,
        web_root=workspace,
        next_cli=next_cli,
        playwright_cli=playwright_cli,
        epoch_clock=epoch_clock,
    )


def _new_runtime_owner_nonce() -> str:
    return secrets.token_hex(32)


def _prepare_runtime_directories(paths: CoturnRuntimePaths) -> None:
    for directory in (
        paths.contract.run_dir,
        paths.contract.coturn_dir,
        paths.control_dir,
        paths.docker_config,
    ):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise _RelayLinuxExecutorError(_FAILURE) from None
        require_owned_directory(directory)
        details = directory.stat(follow_symlinks=False)
        if details.st_uid != os.geteuid():
            raise _RelayLinuxExecutorError(_FAILURE)


__all__: list[str] = []
