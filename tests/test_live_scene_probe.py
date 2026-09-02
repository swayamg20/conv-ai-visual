"""Offline guards for the paid Gate 1 live-scene probe."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manual" / "probe_live_scene.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_probe_refuses_before_importing_configuration_without_budget() -> None:
    result = _run(
        "--input-price-per-million-usd",
        "1",
        "--output-price-per-million-usd",
        "1",
        "--dry-run",
    )

    assert result.returncode == 2
    assert "--max-cost-usd" in result.stderr
    assert result.stdout == ""


def test_probe_dry_run_estimates_a_bounded_single_prompt_without_network() -> None:
    result = _run(
        "--max-cost-usd",
        "0.50",
        "--input-price-per-million-usd",
        "1",
        "--output-price-per-million-usd",
        "1",
        "--prompt-limit",
        "1",
        "--max-tokens",
        "256",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["mode"] == "dry-run"
    assert summary["promptCount"] == 1
    assert 0 < summary["estimatedMaxCostUsd"] <= 0.50
    assert summary["maxOutputTokens"] == 512


def test_probe_refuses_a_live_run_without_exact_cost_acknowledgement() -> None:
    result = _run(
        "--max-cost-usd",
        "0.50",
        "--input-price-per-million-usd",
        "1",
        "--output-price-per-million-usd",
        "1",
        "--prompt-limit",
        "1",
    )

    assert result.returncode == 2
    assert "I_ACCEPT_PROVIDER_COST" in result.stderr
    assert result.stdout == ""


def test_probe_token_ceiling_uses_utf8_bytes_for_punctuation_heavy_input() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    token_upper_bound = namespace["_utf8_token_upper_bound"]
    payload = ("{}[],:;\\\"" * 500) + ("😀" * 100)

    assert token_upper_bound(payload, framing_reserve=37) == len(payload.encode("utf-8")) + 37
