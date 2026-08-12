#!/usr/bin/env python3
"""Statically qualify or honestly refuse Murmur's protected live voice stack.

``dry-run`` performs clean-source, topology, input, budget, and limit validation
without reading credentials, writing artifacts, or accessing the network.
``preflight`` performs the same static validation and writes one private report.
``run`` is deliberately unavailable until the protected browser/runtime adapter
exists; it refuses before credential reads, budget reservation, artifact writes,
or network/provider/Cloud activity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from murmur.voice.live_qualification import (
    PAID_SERVICES_ACK,
    LiveQualificationError,
    LiveQualificationSettings,
    QualificationNetwork,
    QualificationRuntime,
    build_static_report,
    inspect_source_state,
    redact_text,
    run_live_qualification,
    write_private_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "var" / "evals" / "live-qualification"
DEFAULT_LEDGER_PATH = DEFAULT_OUTPUT_ROOT / "m1-runtime-budget.json"


def _settings(args: argparse.Namespace) -> LiveQualificationSettings:
    source = inspect_source_state(PROJECT_ROOT)
    return LiveQualificationSettings(
        run_id=args.run_id,
        runtime=QualificationRuntime(args.runtime),
        network=QualificationNetwork(args.network),
        environment=args.environment,
        paid_services_ack=args.ack,
        max_cost_usd=args.max_cost_usd,
        campaign_cap_usd=args.campaign_cap_usd,
        max_calls=args.max_calls,
        max_turns=args.max_turns,
        max_audio_seconds=args.max_audio_seconds,
        max_wall_seconds=args.max_wall_seconds,
        source_sha=source.sha,
        source_dirty=source.dirty,
        control_plane_url=args.control_plane_url,
        runtime_url=args.runtime_url,
        turn_url=args.turn_url,
        suite_path=Path(args.suite).expanduser().resolve(),
        gates_path=Path(args.gates).expanduser().resolve(),
        output_root=Path(args.output_root).expanduser().resolve(),
        ledger_path=Path(args.budget_ledger).expanduser().resolve(),
    )


def _dry_run(args: argparse.Namespace) -> int:
    report = build_static_report(_settings(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _preflight(args: argparse.Namespace) -> int:
    settings = _settings(args)
    report = build_static_report(settings)
    report_path = settings.output_root / settings.run_id / "preflight.json"
    write_private_report(report_path, report)
    print(
        json.dumps(
            {
                "mode": "preflight",
                "status": report["status"],
                "run_id": settings.run_id,
                "source_sha": settings.source_sha,
                "report_path": str(report_path),
                "credentials_read": False,
                "network_attempted": False,
                "provider_calls": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    run_live_qualification(_settings(args))
    raise AssertionError("unreachable live qualification return")


def _add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runtime", choices=tuple(QualificationRuntime), required=True)
    parser.add_argument("--network", choices=tuple(QualificationNetwork), required=True)
    parser.add_argument("--environment", choices=("qualification",), required=True)
    parser.add_argument(
        "--ack",
        required=True,
        help=f"must exactly equal {PAID_SERVICES_ACK}",
    )
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--campaign-cap-usd", type=float, required=True)
    parser.add_argument("--max-calls", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-audio-seconds", type=int, default=120)
    parser.add_argument("--max-wall-seconds", type=int, default=300)
    parser.add_argument("--control-plane-url", required=True)
    parser.add_argument("--runtime-url", required=True)
    parser.add_argument("--turn-url")
    parser.add_argument("--suite", required=True)
    parser.add_argument("--gates", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--budget-ledger", default=str(DEFAULT_LEDGER_PATH))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, handler, help_text in (
        ("dry-run", _dry_run, "Validate and print a side-effect-free static plan"),
        ("preflight", _preflight, "Write one credential-free private static report"),
        ("run", _run, "Refuse until the protected live adapter is implemented"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        _add_contract_arguments(child)
        child.set_defaults(handler=handler)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return args.handler(args)
    except LiveQualificationError as exc:
        print(f"Live qualification refused: {redact_text(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
