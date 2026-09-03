"""Offline guards for the paid Gate 1 live-scene probe."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_probe_accepts_azure_scene_configuration_without_exposing_credential() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    configured_scene_provider = namespace["_configured_scene_provider"]

    provider, model = configured_scene_provider(
        SimpleNamespace(
            MURMUR_SCENE_LLM_PROVIDER="azure_openai",
            MURMUR_SCENE_LLM_MODEL="murmur-gpt-oss-120b",
            AZURE_OPENAI_API_KEY="server-only-test-key",
        )
    )

    assert (provider, model) == ("azure_openai", "murmur-gpt-oss-120b")


def test_probe_refuses_azure_scene_configuration_without_credential() -> None:
    namespace = runpy.run_path(str(SCRIPT))
    configured_scene_provider = namespace["_configured_scene_provider"]

    with pytest.raises(RuntimeError, match=r"credential.*azure_openai.*unavailable"):
        configured_scene_provider(
            SimpleNamespace(
                MURMUR_SCENE_LLM_PROVIDER="azure_openai",
                MURMUR_SCENE_LLM_MODEL="murmur-gpt-oss-120b",
                AZURE_OPENAI_API_KEY="",
            )
        )


@pytest.mark.asyncio
async def test_probe_constructs_azure_gpt_oss_with_shared_low_reasoning_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import murmur.live_scene as live_scene
    import murmur.llm.factory as llm_factory
    from murmur.core.config import config

    namespace = runpy.run_path(str(SCRIPT))
    run_corpus = namespace["_run_corpus"]
    client_calls: list[tuple[str, str | None, dict[str, object]]] = []

    def create_client(provider: str, *, model: str | None = None, **kwargs: object) -> object:
        client_calls.append((provider, model, kwargs))
        return object()

    class OfflineSceneAuthoringService:
        def __init__(self, *, client_factory, **_kwargs: object) -> None:
            client_factory()

        async def stream_events(self, _request):
            if False:
                yield

    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_PROVIDER", "azure_openai")
    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_MODEL", "murmur-gpt-oss-120b")
    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(config, "AZURE_OPENAI_API_KEY", "server-only-test-key")
    monkeypatch.setattr(live_scene, "SceneAuthoringService", OfflineSceneAuthoringService)
    monkeypatch.setattr(llm_factory, "create_llm_client", create_client)

    await run_corpus(
        SimpleNamespace(max_tokens=256, timeout_seconds=5.0),
        ("Explain a binary search.",),
    )

    assert client_calls == [
        ("azure_openai", "murmur-gpt-oss-120b", {"reasoning_effort": "low"})
    ]
