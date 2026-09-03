#!/usr/bin/env python3
"""Run a redacted, explicitly budgeted Gate 1 live-model corpus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VAR_ROOT = (PROJECT_ROOT / "var").resolve()
ACKNOWLEDGEMENT = "I_ACCEPT_PROVIDER_COST"
MAX_ALLOWED_BUDGET_USD = 2.0
MAX_PROMPTS = 10
MAX_OUTPUT_TOKENS = 4_096
MAX_SCENE_CONTEXT_BYTES = 64 * 1024
CHAT_FRAMING_TOKEN_RESERVE = 2_048
REPAIR_PROMPT_BYTE_RESERVE = 4_096
SUPPORTED_PROVIDERS = frozenset({"openai", "azure_openai", "groq", "gemini"})
PROVIDER_CREDENTIAL_FIELDS = {
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

DEFAULT_PROMPTS = (
    "Teach why the Pythagorean theorem works using a right triangle and areas.",
    "Show how a derivative becomes the slope of a tangent line.",
    "Explain insertion into a binary search tree with stable node identities.",
    "Trace one HTTP request through a load balancer, API, cache, and database.",
    "Show the energy flow through photosynthesis in three progressive steps.",
    "Explain supply and demand meeting at market equilibrium.",
    "Visualize how attention connects words inside a short sentence.",
    "Teach the water cycle as a loop with evaporation, condensation, and rain.",
    "Walk through Dijkstra's shortest-path algorithm on a small graph.",
    "Explain why a lunar eclipse happens using the Sun, Earth, and Moon.",
)


def _positive_finite(value: float, field: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be finite and greater than zero")
    return value


def _validate_arguments(args: argparse.Namespace) -> None:
    budget = _positive_finite(args.max_cost_usd, "--max-cost-usd")
    if budget > MAX_ALLOWED_BUDGET_USD:
        raise ValueError(f"--max-cost-usd must not exceed USD {MAX_ALLOWED_BUDGET_USD:.2f}")
    _positive_finite(args.input_price_per_million_usd, "--input-price-per-million-usd")
    _positive_finite(args.output_price_per_million_usd, "--output-price-per-million-usd")
    if not 1 <= args.prompt_limit <= MAX_PROMPTS:
        raise ValueError(f"--prompt-limit must be between 1 and {MAX_PROMPTS}")
    if not 1 <= args.max_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError(f"--max-tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be finite and greater than zero")
    if not args.dry_run and args.acknowledge_paid_provider != ACKNOWLEDGEMENT:
        raise ValueError(
            "live run refused: pass --acknowledge-paid-provider " + ACKNOWLEDGEMENT
        )


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _utf8_token_upper_bound(value: str, *, framing_reserve: int = 0) -> int:
    """Bound normal text tokens by bytes plus explicit provider framing reserve."""

    return len(value.encode("utf-8")) + framing_reserve


def _safe_output_path(raw_path: str | None) -> Path:
    if raw_path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        candidate = candidate.resolve()
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        candidate = VAR_ROOT / "live-scene" / f"{run_id}.json"
    if candidate != VAR_ROOT and VAR_ROOT not in candidate.parents:
        raise ValueError("--output must resolve inside the repository var/ directory")
    return candidate


def _estimate_cost(
    prompts: tuple[str, ...],
    *,
    max_tokens: int,
    input_price: float,
    output_price: float,
) -> dict[str, float | int]:
    # Import only after the explicit budget guard has passed. This module loads
    # local dotenv configuration, but performs no provider request.
    from murmur.live_scene.prompt import build_scene_messages

    max_input_tokens = 0
    for prompt in prompts:
        messages = build_scene_messages(
            prompt,
            '{"nodes":[],"revision":0}',
            8,
        )
        message_bytes = sum(
            _utf8_token_upper_bound(message["role"])
            + _utf8_token_upper_bound(message["content"])
            for message in messages
        )
        initial_attempt_bound = message_bytes + CHAT_FRAMING_TOKEN_RESERVE
        repair_attempt_bound = (
            message_bytes
            + MAX_SCENE_CONTEXT_BYTES
            + REPAIR_PROMPT_BYTE_RESERVE
            + CHAT_FRAMING_TOKEN_RESERVE
        )
        max_input_tokens += initial_attempt_bound + repair_attempt_bound

    # Every normal text token represents at least one UTF-8 byte. The explicit
    # framing/repair reserves cover provider message wrappers and repair prose,
    # without assuming a favorable characters-per-token ratio for JSON.
    max_output_tokens = len(prompts) * max_tokens * 2
    estimated_cost = (
        max_input_tokens * input_price + max_output_tokens * output_price
    ) / 1_000_000
    return {
        "maxInputTokens": max_input_tokens,
        "maxOutputTokens": max_output_tokens,
        "estimatedMaxCostUsd": round(estimated_cost, 6),
    }


def _configured_scene_provider(config: Any) -> tuple[str, str]:
    """Resolve one supported provider without returning or rendering its credential."""

    provider = config.MURMUR_SCENE_LLM_PROVIDER.lower()
    model = config.MURMUR_SCENE_LLM_MODEL
    if provider not in SUPPORTED_PROVIDERS or not model:
        raise RuntimeError("scene provider configuration is invalid")
    credential = getattr(config, PROVIDER_CREDENTIAL_FIELDS[provider], None)
    if not credential:
        raise RuntimeError(f"credential for configured scene provider {provider} is unavailable")
    return provider, model


async def _run_corpus(args: argparse.Namespace, prompts: tuple[str, ...]) -> dict[str, Any]:
    from murmur.core.config import config
    from murmur.live_scene import (
        LiveSceneRequest,
        SceneAuthoringService,
        SceneState,
        SceneStreamCompletedEvent,
        SceneStreamFailedEvent,
        SceneStreamRepairingEvent,
    )
    from murmur.live_scene.provider import scene_model_client_options
    from murmur.llm.factory import create_llm_client

    provider, model = _configured_scene_provider(config)
    client_options = scene_model_client_options(provider, model)

    results: list[dict[str, Any]] = []
    for generation, prompt in enumerate(prompts, start=1):
        service = SceneAuthoringService(
            client_factory=lambda: create_llm_client(
                provider,
                model=model,
                **client_options,
            ),
            temperature=config.MURMUR_SCENE_LLM_TEMPERATURE,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        request = LiveSceneRequest(
            prompt=prompt,
            generation=generation,
            base_scene=SceneState(revision=0, nodes=()),
        )
        started_at = time.perf_counter()
        patch_arrivals_ms: list[float] = []
        event_types: list[str] = []
        patch_count = 0
        repaired = False
        terminal = "exception"
        failure_code: str | None = None

        try:
            async for event in service.stream_events(request):
                event_types.append(event.type)
                if event.type == "scene_patch":
                    patch_count += 1
                    patch_arrivals_ms.append((time.perf_counter() - started_at) * 1_000)
                elif isinstance(event, SceneStreamRepairingEvent):
                    repaired = True
                elif isinstance(event, SceneStreamCompletedEvent):
                    terminal = "completed"
                elif isinstance(event, SceneStreamFailedEvent):
                    terminal = "failed"
                    failure_code = event.code
        except Exception as exc:  # Redact provider bodies and credential-bearing messages.
            failure_code = f"unhandled_{type(exc).__name__}"

        gaps = [
            patch_arrivals_ms[index] - patch_arrivals_ms[index - 1]
            for index in range(1, len(patch_arrivals_ms))
        ]
        results.append(
            {
                "promptHash": _prompt_hash(prompt),
                "generation": generation,
                "terminal": terminal,
                "failureCode": failure_code,
                "firstAttemptValid": terminal == "completed" and not repaired,
                "repaired": repaired,
                "patchCount": patch_count,
                "firstPatchMs": round(patch_arrivals_ms[0], 3) if patch_arrivals_ms else None,
                "patchGapMs": [round(value, 3) for value in gaps],
                "eventTypes": event_types,
            }
        )

    first_patch_values = [
        result["firstPatchMs"] for result in results if result["firstPatchMs"] is not None
    ]
    patch_gaps = [gap for result in results for gap in result["patchGapMs"]]
    first_attempt_rate = sum(result["firstAttemptValid"] for result in results) / len(results)
    terminal_rate = sum(result["terminal"] in {"completed", "failed"} for result in results) / len(results)
    completion_rate = sum(result["terminal"] == "completed" for result in results) / len(results)
    median_first_patch = statistics.median(first_patch_values) if first_patch_values else None
    p95_first_patch = _percentile(first_patch_values, 0.95)
    p95_patch_gap = _percentile(patch_gaps, 0.95)
    enough_prompts = len(results) >= 10
    server_thresholds_passed = bool(
        enough_prompts
        and first_attempt_rate >= 0.9
        and terminal_rate == 1.0
        and completion_rate == 1.0
        and median_first_patch is not None
        and median_first_patch <= 1_500
        and p95_first_patch is not None
        and p95_first_patch <= 3_000
        and (p95_patch_gap is None or p95_patch_gap <= 1_500)
    )
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(UTC).isoformat(),
        "provider": provider,
        "model": model,
        "promptCount": len(results),
        "results": results,
        "metrics": {
            "firstAttemptValidityRate": round(first_attempt_rate, 4),
            "safeTerminalRate": round(terminal_rate, 4),
            "completionRate": round(completion_rate, 4),
            "medianFirstPatchMs": round(median_first_patch, 3)
            if median_first_patch is not None
            else None,
            "p95FirstPatchMs": round(p95_first_patch, 3)
            if p95_first_patch is not None
            else None,
            "p95PatchGapMs": round(p95_patch_gap, 3) if p95_patch_gap is not None else None,
        },
        "serverProtocolThresholdsPassed": server_thresholds_passed,
        "evidenceScope": "provider_to_server_events_only",
        "overallVerdict": "requires_browser_latency_and_visual_usefulness_review",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--input-price-per-million-usd", type=float, required=True)
    parser.add_argument("--output-price-per-million-usd", type=float, required=True)
    parser.add_argument("--prompt-limit", type=int, default=MAX_PROMPTS)
    parser.add_argument("--max-tokens", type=int, default=MAX_OUTPUT_TOKENS)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-server-thresholds", action="store_true")
    parser.add_argument("--acknowledge-paid-provider")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        _validate_arguments(args)
        prompts = DEFAULT_PROMPTS[: args.prompt_limit]
        estimate = _estimate_cost(
            prompts,
            max_tokens=args.max_tokens,
            input_price=args.input_price_per_million_usd,
            output_price=args.output_price_per_million_usd,
        )
        if estimate["estimatedMaxCostUsd"] > args.max_cost_usd:
            raise ValueError(
                "estimated maximum cost exceeds --max-cost-usd; lower token/prompt limits "
                "or explicitly raise the budget"
            )
        preflight = {
            "mode": "dry-run" if args.dry_run else "live",
            "promptCount": len(prompts),
            "maxCostUsd": args.max_cost_usd,
            **estimate,
        }
        print(json.dumps(preflight, sort_keys=True))
        if args.dry_run:
            return 0

        output_path = _safe_output_path(args.output)
        report = asyncio.run(_run_corpus(args, prompts))
        report["budget"] = preflight
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(output_path, 0o600)
        print(
            json.dumps(
                {
                    "mode": "live",
                    "promptCount": report["promptCount"],
                    "serverProtocolThresholdsPassed": report[
                        "serverProtocolThresholdsPassed"
                    ],
                    "overallVerdict": report["overallVerdict"],
                    "output": str(output_path),
                },
                sort_keys=True,
            )
        )
        if args.require_server_thresholds and not report["serverProtocolThresholdsPassed"]:
            return 1
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Live scene probe refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
