"""Static-admission tests for the Voice V2 evaluation CLI."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from murmur.voice.profile import ProfileAdmission
from murmur.voice.provider_profiles.livekit_cascade import (
    DIRECT_CASCADE_PROFILE_ID,
    DirectCascadeSettings,
    build_direct_cascade_provider,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOICE_EVAL_MODULE_PATH = PROJECT_ROOT / "scripts" / "voice_eval.py"
VOICE_EVAL_SPEC = importlib.util.spec_from_file_location("voice_eval", VOICE_EVAL_MODULE_PATH)
assert VOICE_EVAL_SPEC is not None and VOICE_EVAL_SPEC.loader is not None
voice_eval = importlib.util.module_from_spec(VOICE_EVAL_SPEC)
sys.modules[VOICE_EVAL_SPEC.name] = voice_eval
VOICE_EVAL_SPEC.loader.exec_module(voice_eval)

CONFIG_HASH = "a" * 64


class _AdmissionOnlyProvider:
    def __init__(self) -> None:
        self.admission_calls = 0
        self.prepare_calls = 0

    async def admit(self, scope: object) -> ProfileAdmission:
        self.admission_calls += 1
        assert scope.profile_id == DIRECT_CASCADE_PROFILE_ID
        return ProfileAdmission(
            profile_id=DIRECT_CASCADE_PROFILE_ID,
            required_components=("stt", "llm", "tts"),
            config_hash=CONFIG_HASH,
        )

    async def prepare(self, scope: object) -> object:
        del scope
        self.prepare_calls += 1
        raise AssertionError("static preflight must not prepare provider objects")


class _NoConstructionFactories:
    def __init__(self) -> None:
        self.calls = 0

    def make_stt(self, settings: object) -> object:
        del settings
        self.calls += 1
        raise AssertionError("static admission must not construct STT")

    def make_llm(self, settings: object) -> object:
        del settings
        self.calls += 1
        raise AssertionError("static admission must not construct LLM")

    def make_tts(self, settings: object) -> object:
        del settings
        self.calls += 1
        raise AssertionError("static admission must not construct TTS")


class _NoNetworkProbe:
    def __init__(self) -> None:
        self.calls = 0

    async def get_json(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        self.calls += 1
        raise AssertionError("static admission must not make provider requests")


def test_direct_profile_cli_uses_static_admission_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _AdmissionOnlyProvider()
    observed_configs: list[object] = []

    def build_provider(app_config: object) -> _AdmissionOnlyProvider:
        observed_configs.append(app_config)
        return provider

    monkeypatch.setattr(voice_eval, "_direct_profile_provider_factory", build_provider)
    args = voice_eval._parser().parse_args(["preflight", "--profile", DIRECT_CASCADE_PROFILE_ID])

    result = args.handler(args)
    report = json.loads(capsys.readouterr().out)

    assert result == 0
    assert observed_configs == [voice_eval.config]
    assert provider.admission_calls == 1
    assert provider.prepare_calls == 0
    assert report == {
        "schema_version": 1,
        "profile": DIRECT_CASCADE_PROFILE_ID,
        "status": "configured",
        "admission_mode": "static",
        "network_verified": False,
        "config_hash": CONFIG_HASH,
        "components": {
            component: {
                "status": "configured",
                "detail": "Required by the accepted static profile manifest",
                "required": True,
            }
            for component in ("stt", "llm", "tts")
        },
        "blocking_components": [],
        "degraded_components": [],
        "limitations": [voice_eval._STATIC_ADMISSION_LIMITATIONS[0]],
        "note": "Local static admission only; authoritative readiness requires the guarded live qualification path.",
    }


def test_direct_profile_static_preflight_never_constructs_or_probes() -> None:
    factories = _NoConstructionFactories()
    probe = _NoNetworkProbe()
    settings = DirectCascadeSettings(
        profile_id=DIRECT_CASCADE_PROFILE_ID,
        deepgram_api_key="dg_live_static_key",
        groq_api_key="gsk_live_static_key",
        elevenlabs_api_key="xi_live_static_key",
        elevenlabs_voice_id="voice_static_id",
    )
    provider = build_direct_cascade_provider(
        settings,
        factories=factories,
        probe_transport=probe,
    )

    report = voice_eval._direct_profile_preflight(
        SimpleNamespace(),
        provider_factory=lambda _config: provider,
    )

    assert report["status"] == "configured"
    assert report["network_verified"] is False
    assert report["config_hash"] == settings.config_hash()
    assert factories.calls == 0
    assert probe.calls == 0


def test_direct_profile_static_preflight_fails_closed_and_redacts() -> None:
    secret = "sk-must-not-appear"

    def fail_with_secret(_config: object) -> object:
        raise RuntimeError(f"bad provider credential: {secret}")

    report = voice_eval._direct_profile_preflight(
        SimpleNamespace(),
        provider_factory=fail_with_secret,
    )
    serialized = json.dumps(report)

    assert report["status"] == "blocked"
    assert report["network_verified"] is False
    assert report["blocking_components"] == ["profile_admission"]
    assert report["config_hash"] is None
    assert secret not in serialized
    assert "No provider call was made" in report["note"]


def test_existing_fake_and_live_paths_remain_provider_free(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        voice_eval,
        "_direct_profile_provider_factory",
        lambda _config: pytest.fail("unselected direct profile must not be constructed"),
    )

    fake_args = voice_eval._parser().parse_args(["preflight", "--profile", "fake"])
    assert fake_args.handler(fake_args) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "fake"

    legacy_report = {
        "schema_version": 1,
        "profile": "legacy",
        "status": "configured",
        "network_verified": False,
        "components": {},
        "blocking_components": [],
        "degraded_components": [],
        "note": "legacy sentinel",
    }
    monkeypatch.setattr(voice_eval, "_legacy_preflight", lambda: legacy_report)
    legacy_args = voice_eval._parser().parse_args(["preflight", "--profile", "legacy"])
    assert legacy_args.handler(legacy_args) == 0
    assert json.loads(capsys.readouterr().out) == legacy_report

    live_args = voice_eval._parser().parse_args(
        [
            "live",
            "--suite",
            "unused.json",
            "--profile",
            DIRECT_CASCADE_PROFILE_ID,
            "--gates",
            "unused.json",
            "--max-cost-usd",
            "1",
        ]
    )
    assert live_args.handler(live_args) == 2
    live_output = capsys.readouterr().out
    assert "adapter is not available" in live_output
    assert "no provider call was made" in live_output
