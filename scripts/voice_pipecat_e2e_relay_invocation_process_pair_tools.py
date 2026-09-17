"""Concrete invocation tools derived from one consumed workspace build."""

from __future__ import annotations

from scripts.voice_pipecat_e2e_relay_invocation_driver import (
    _CONCRETE_TOOLS_TOKEN,
    RelayInvocationTools,
)
from scripts.voice_pipecat_e2e_relay_invocation_process_pair_callback import (
    _RelayConcreteInvocationCallback,
)

_FAILURE = "Relay concrete invocation pair is invalid"


def _new_concrete_tools(
    build: object,
    epoch_clock: object,
    pair_key: object,
) -> RelayInvocationTools:
    request = build.request  # type: ignore[attr-defined]
    workspace = request._workspace
    node_modules = workspace / "node_modules"
    next_cli = node_modules / "next/dist/bin/next"
    playwright_cli = node_modules / "@playwright/test/cli.js"
    if not (
        callable(epoch_clock)
        and request._source_root is build.request_values[0]  # type: ignore[attr-defined]
        and request._workspace is build.request_values[3]  # type: ignore[attr-defined]
        and request._node is build.request_values[4]  # type: ignore[attr-defined]
        and request._node_modules is build.request_values[5]  # type: ignore[attr-defined]
        and request._next_cli is build.request_values[6]  # type: ignore[attr-defined]
        and request._dist_path is build.request_values[7]  # type: ignore[attr-defined]
        and request._run_id is build.request_values[8]  # type: ignore[attr-defined]
        and request._node_modules == request._source_root / "node_modules"
        and next_cli == workspace / request._next_cli.relative_to(request._source_root)
        and playwright_cli
        == workspace
        / request._node_modules.relative_to(request._source_root)
        / "@playwright/test/cli.js"
    ):
        raise TypeError(_FAILURE)
    return RelayInvocationTools(
        _CONCRETE_TOOLS_TOKEN,
        node=request._node,
        web_root=workspace,
        next_cli=next_cli,
        playwright_cli=playwright_cli,
        epoch_clock=epoch_clock,
        pair_key=pair_key,
    )


def _live_pair_capabilities_match(record: object) -> bool:
    try:
        if type(record) is not tuple or len(record) != 5:
            return False
        grant, driver, tools = record[:3]
        request = grant._build.request
        expected_next = request._workspace / request._next_cli.relative_to(request._source_root)
        expected_playwright = (
            request._workspace
            / request._node_modules.relative_to(request._source_root)
            / "@playwright/test/cli.js"
        )
        return bool(
            all(
                type(getattr(driver, name, None)) is _RelayConcreteInvocationCallback
                and getattr(driver, name)._matches(grant._pair_key, operation)
                for name, operation in zip(
                    ("_preown", "_start", "_prebootstrap", "_finish", "_stop"),
                    ("preown", "start", "prebootstrap", "finish", "stop"),
                    strict=True,
                )
            )
            and tools._node is request._node
            and tools._web_root is request._workspace
            and type(tools._next_cli) is type(expected_next)
            and tools._next_cli == expected_next
            and type(tools._playwright_cli) is type(expected_playwright)
            and tools._playwright_cli == expected_playwright
            and tools._epoch_clock is grant._epoch_clock
        )
    except BaseException:
        return False


__all__: list[str] = []
