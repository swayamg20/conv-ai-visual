#!/usr/bin/env python3
"""Run provider-free replay now and guarded live qualification later."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from murmur.core.config import config
from murmur.voice.evaluation import (
    evaluate_replay_gates,
    run_replay_suite,
    write_replay_artifacts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIRECT_CASCADE_PROFILE_ID = "livekit-agents-cascade-v1"

_STATIC_ADMISSION_LIMITATIONS = (
    "Static admission does not verify provider authentication, model visibility, "
    "streaming audio, quota, latency, browser media, or RTC connectivity.",
)


def _is_real_secret(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().casefold()
    return not (
        normalized.startswith("your_")
        or normalized.startswith("your-")
        or normalized.startswith("test-")
        or normalized in {"changeme", "placeholder"}
    )


def _component(status: str, detail: str, *, required: bool) -> dict[str, Any]:
    return {"status": status, "detail": detail, "required": required}


def _legacy_preflight() -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    components["stt"] = _component(
        "configured" if _is_real_secret(config.DEEPGRAM_KEY) else "blocked",
        "Deepgram credential is configured"
        if _is_real_secret(config.DEEPGRAM_KEY)
        else "DEEPGRAM_KEY is missing or a placeholder",
        required=True,
    )

    provider = config.LLM_PROVIDER.casefold()
    llm_key = {
        "openai": config.OPENAI_API_KEY,
        "groq": config.GROQ_API_KEY,
        "gemini": config.GEMINI_API_KEY,
    }.get(provider)
    components["llm"] = _component(
        "configured" if _is_real_secret(llm_key) else "blocked",
        f"{provider} credential is configured"
        if _is_real_secret(llm_key)
        else f"credential for LLM_PROVIDER={provider!r} is missing or a placeholder",
        required=True,
    )

    if config.TTS_PROVIDER == "kokoro":
        kokoro_available = importlib.util.find_spec("kokoro_onnx") is not None
        components["tts"] = _component(
            "configured" if kokoro_available else "blocked",
            "kokoro-onnx is importable"
            if kokoro_available
            else "TTS_PROVIDER=kokoro but kokoro-onnx is not installed",
            required=True,
        )
    else:
        elevenlabs_configured = _is_real_secret(config.ELEVENLABS_API_KEY)
        components["tts"] = _component(
            "configured" if elevenlabs_configured else "blocked",
            "ElevenLabs credential is configured"
            if elevenlabs_configured
            else "ELEVENLABS_API_KEY is missing or a placeholder",
            required=True,
        )
        if config.TTS_FALLBACK_TO_KOKORO:
            fallback_available = importlib.util.find_spec("kokoro_onnx") is not None
            components["tts_fallback"] = _component(
                "configured" if fallback_available else "degraded",
                "kokoro-onnx fallback is importable"
                if fallback_available
                else "Kokoro fallback is enabled but kokoro-onnx is not installed",
                required=False,
            )

    if config.SMART_TURN_ENABLED:
        model_path = (
            Path(config.SMART_TURN_MODEL_PATH).expanduser()
            if config.SMART_TURN_MODEL_PATH
            else None
        )
        model_available = bool(model_path and model_path.is_file())
        download_deps_available = all(
            importlib.util.find_spec(package) is not None
            for package in ("huggingface_hub", "transformers")
        )
        smart_turn_available = model_available or download_deps_available
        components["turn_detection"] = _component(
            "configured" if smart_turn_available else "blocked",
            "Smart Turn model/dependencies are available"
            if smart_turn_available
            else "Smart Turn is selected but its model/download dependencies are absent; install them or explicitly set SMART_TURN_ENABLED=false to select Deepgram endpointing",
            required=True,
        )
    else:
        components["turn_detection"] = _component(
            "configured",
            "Smart Turn is disabled; Deepgram endpointing is selected",
            required=True,
        )

    firebase_path = (
        Path(config.FIREBASE_SERVICE_ACCOUNT_PATH).expanduser()
        if config.FIREBASE_SERVICE_ACCOUNT_PATH
        else None
    )
    firebase_path_configured = bool(firebase_path and firebase_path.is_file())
    firebase_project_configured = bool(config.FIREBASE_PROJECT_ID)
    firebase_configured = firebase_path_configured or firebase_project_configured
    if firebase_path_configured:
        firebase_detail = "Firebase service-account file exists"
    elif config.FIREBASE_SERVICE_ACCOUNT_PATH:
        firebase_detail = "FIREBASE_SERVICE_ACCOUNT_PATH does not name a file"
    elif firebase_project_configured:
        firebase_detail = (
            "FIREBASE_PROJECT_ID is configured; Application Default Credentials remain "
            "unverified until runtime"
        )
    else:
        firebase_detail = "Firebase service-account path or project ID is required"
    components["authentication"] = _component(
        "configured" if firebase_configured else "blocked",
        firebase_detail,
        required=True,
    )

    blocked = sorted(
        name
        for name, component in components.items()
        if component["required"] and component["status"] == "blocked"
    )
    degraded = sorted(
        name for name, component in components.items() if component["status"] == "degraded"
    )
    return {
        "schema_version": 1,
        "profile": "legacy",
        "status": "blocked" if blocked else ("configured_degraded" if degraded else "configured"),
        "network_verified": False,
        "components": components,
        "blocking_components": blocked,
        "degraded_components": degraded,
        "note": "Local configuration only; provider authentication and browser audio remain unverified until a budgeted live run.",
    }


def _direct_profile_provider_factory(app_config: object) -> object:
    """Construct the direct profile while keeping optional imports off replay paths."""

    from murmur.voice.provider_profiles.livekit_cascade import (
        build_direct_cascade_provider_from_config,
    )

    return build_direct_cascade_provider_from_config(app_config)


def _direct_profile_preflight(
    app_config: object = config,
    *,
    provider_factory: Callable[[object], object] | None = None,
) -> dict[str, Any]:
    """Perform local-only admission for the named direct-provider profile.

    This path intentionally calls ``admit`` rather than ``prepare``. The direct
    profile's admission contract validates the exact profile ID, non-placeholder
    configuration, and installed adapter surface without probing a provider.
    """

    from murmur.voice.profile import VoiceProfileScope

    scope = VoiceProfileScope(
        profile_id=DIRECT_CASCADE_PROFILE_ID,
        user_id="voice_eval_static_user",
        session_id="voice_eval_static_session",
        agent_id="voice_eval_static_agent",
        voice_call_id="voice_eval_static_call",
        trace_id="voice_eval_static_trace",
        system_prompt="Validate static Voice V2 profile admission only.",
    )
    try:
        provider = (provider_factory or _direct_profile_provider_factory)(app_config)
        admit = getattr(provider, "admit", None)
        if not callable(admit):
            raise TypeError("direct profile provider has no admission contract")
        admission = asyncio.run(admit(scope))
        if admission.profile_id != DIRECT_CASCADE_PROFILE_ID:
            raise ValueError("direct profile admission returned a different profile")
        required_components = tuple(admission.required_components)
        if not required_components:
            raise ValueError("direct profile admission returned no required components")
        config_hash = admission.config_hash
        if (
            not isinstance(config_hash, str)
            or len(config_hash) != 64
            or any(character not in "0123456789abcdef" for character in config_hash)
        ):
            raise ValueError("direct profile admission returned an invalid config hash")
    except Exception:
        # Admission failures can originate in environment-backed configuration.
        # Never echo exception text, which could accidentally contain credentials.
        return {
            "schema_version": 1,
            "profile": DIRECT_CASCADE_PROFILE_ID,
            "status": "blocked",
            "admission_mode": "static",
            "network_verified": False,
            "config_hash": None,
            "components": {},
            "blocking_components": ["profile_admission"],
            "degraded_components": [],
            "limitations": list(_STATIC_ADMISSION_LIMITATIONS),
            "note": "Static admission failed; inspect local Voice V2 configuration and installed adapters. No provider call was made.",
        }

    components = {
        component: _component(
            "configured",
            "Required by the accepted static profile manifest",
            required=True,
        )
        for component in required_components
    }
    return {
        "schema_version": 1,
        "profile": DIRECT_CASCADE_PROFILE_ID,
        "status": "configured",
        "admission_mode": "static",
        "network_verified": False,
        "config_hash": config_hash,
        "components": components,
        "blocking_components": [],
        "degraded_components": [],
        "limitations": list(_STATIC_ADMISSION_LIMITATIONS),
        "note": "Local static admission only; authoritative readiness requires the guarded live qualification path.",
    }


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "unknown"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_preflight(args: argparse.Namespace) -> int:
    if args.profile == "fake":
        report = {
            "schema_version": 1,
            "profile": "fake",
            "status": "configured",
            "network_verified": False,
            "components": {},
            "blocking_components": [],
            "degraded_components": [],
            "note": "Provider-free deterministic profile.",
        }
    elif args.profile == "legacy":
        report = _legacy_preflight()
    else:
        report = _direct_profile_preflight()
    print(json.dumps(report, indent=2))
    return 1 if report["blocking_components"] else 0


def _run_replay(args: argparse.Namespace) -> int:
    suite_path = Path(args.suite)
    if not suite_path.is_absolute():
        suite_path = PROJECT_ROOT / suite_path
    gates_path = Path(args.gates)
    if not gates_path.is_absolute():
        gates_path = PROJECT_ROOT / gates_path

    summary = run_replay_suite(
        suite_path,
        project_root=PROJECT_ROOT,
        repeats=args.repeats,
    )
    summary["profile"] = args.profile
    gates = json.loads(gates_path.read_text())
    gate_result = evaluate_replay_gates(summary, gates)
    summary["gate_result"] = gate_result
    summary["evidence"] = {
        "commit_sha": _git_commit(),
        "runtime_version": "voice-v2-m0-replay-v1",
        "profile": args.profile,
        "provider_mode": "fake",
        "provider_models": {},
        "region": "local",
        "browser": "none",
        "network_profile": "provider_event_replay",
        "scenario_count": summary["scenario_count"],
        "turn_count": sum(len(result["committed_turns"]) for result in summary["results"]),
        "gate_file_sha256": _file_sha256(gates_path),
    }

    output_root = Path(args.output_root)
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output_dir = output_root / run_id
    write_replay_artifacts(output_dir, summary)

    print(
        json.dumps(
            {
                "mode": "replay",
                "profile": args.profile,
                "passed": gate_result["passed"],
                "scenario_count": summary["scenario_count"],
                "combined_trace_hash": summary["combined_trace_hash"],
                "output_dir": str(output_dir),
                "unmeasured_gate_groups": gate_result["unmeasured_gate_groups"],
            },
            indent=2,
        )
    )
    return 1 if args.assert_gates and not gate_result["passed"] else 0


def _run_live(args: argparse.Namespace) -> int:
    budget = args.max_cost_usd
    if budget is None:
        raw_budget = os.getenv("MURMUR_EVAL_BUDGET_USD", "")
        try:
            budget = float(raw_budget)
        except ValueError:
            budget = 0.0
    if budget <= 0:
        print("Live evaluation refused: set a positive explicit cost budget.")
        return 2
    print("Live evaluation adapter is not available in Milestone 0; no provider call was made.")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="Check local profile readiness")
    preflight.add_argument(
        "--profile",
        choices=("fake", "legacy", DIRECT_CASCADE_PROFILE_ID),
        required=True,
    )
    preflight.set_defaults(handler=_run_preflight)

    replay = subparsers.add_parser("replay", help="Replay provider events deterministically")
    replay.add_argument("--suite", required=True)
    replay.add_argument("--profile", default="fake")
    replay.add_argument("--gates", required=True)
    replay.add_argument("--repeats", type=int, default=2)
    replay.add_argument("--output-root", default="var/evals")
    replay.add_argument("--run-id")
    replay.add_argument("--assert-gates", action="store_true")
    replay.set_defaults(handler=_run_replay)

    live = subparsers.add_parser("live", help="Run a guarded live profile")
    live.add_argument("--suite", required=True)
    live.add_argument("--profile", required=True)
    live.add_argument("--gates", required=True)
    live.add_argument("--max-cost-usd", type=float)
    live.add_argument("--assert-gates", action="store_true")
    live.set_defaults(handler=_run_live)
    return parser


def main() -> int:
    args = _parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
