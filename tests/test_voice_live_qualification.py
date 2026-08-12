"""Credential-free contracts for the protected live qualification scaffold."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from murmur.voice.live_qualification import (
    CAMPAIGN_CAP_USD,
    PAID_SERVICES_ACK,
    BudgetLedger,
    LiveQualificationError,
    LiveQualificationSettings,
    QualificationNetwork,
    QualificationRuntime,
    SourceState,
    atomic_write_json,
    build_static_report,
    redact_mapping,
    redact_text,
    required_secret_env_names,
    run_live_qualification,
    select_secret_environment,
    write_private_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "voice_live_stack.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("voice_live_stack", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
voice_live_stack = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = voice_live_stack
SCRIPT_SPEC.loader.exec_module(voice_live_stack)


@pytest.fixture
def input_files(tmp_path: Path) -> tuple[Path, Path]:
    suite = tmp_path / "qualification.jsonl"
    gates = tmp_path / "gates.json"
    suite.write_text('{"scenario_id":"one"}\n', encoding="utf-8")
    gates.write_text('{"schema_version":1}\n', encoding="utf-8")
    return suite, gates


def _settings(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    **changes: object,
) -> LiveQualificationSettings:
    suite, gates = input_files
    settings = LiveQualificationSettings(
        run_id="qualification-run-1",
        runtime=QualificationRuntime.LIVEKIT_V2,
        network=QualificationNetwork.DIRECT,
        environment="qualification",
        paid_services_ack=PAID_SERVICES_ACK,
        max_cost_usd=1.0,
        campaign_cap_usd=CAMPAIGN_CAP_USD,
        max_calls=1,
        max_turns=4,
        max_audio_seconds=120,
        max_wall_seconds=300,
        source_sha="a" * 40,
        source_dirty=False,
        control_plane_url="https://qualification.murmur.ai",
        runtime_url="wss://project-a.livekit.cloud",
        turn_url=None,
        suite_path=suite,
        gates_path=gates,
        output_root=tmp_path / "artifacts",
        ledger_path=tmp_path / "budget.json",
    )
    return replace(settings, **changes)


@pytest.mark.parametrize(
    ("runtime", "network", "runtime_url", "turn_url"),
    [
        (
            QualificationRuntime.LIVEKIT_V2,
            QualificationNetwork.DIRECT,
            "wss://a.livekit.cloud",
            None,
        ),
        (
            QualificationRuntime.LIVEKIT_V2,
            QualificationNetwork.RELAY_TLS,
            "wss://a.livekit.cloud",
            None,
        ),
        (
            QualificationRuntime.LIVEKIT_V2,
            QualificationNetwork.DISCONNECT,
            "wss://a.livekit.cloud",
            None,
        ),
        (
            QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
            QualificationNetwork.DIRECT,
            "https://voice-rtc.murmur.ai",
            None,
        ),
        (
            QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
            QualificationNetwork.RELAY_TLS,
            "https://voice-rtc.murmur.ai",
            "turns://turn.murmur.ai:5349",
        ),
        (
            QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
            QualificationNetwork.DISCONNECT,
            "https://voice-rtc.murmur.ai",
            None,
        ),
    ],
)
def test_strict_settings_accept_only_declared_runtime_network_topologies(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    runtime: QualificationRuntime,
    network: QualificationNetwork,
    runtime_url: str,
    turn_url: str | None,
) -> None:
    settings = _settings(
        tmp_path,
        input_files,
        runtime=runtime,
        network=network,
        runtime_url=runtime_url,
        turn_url=turn_url,
    )

    assert settings.environment == "qualification"
    assert settings.max_cost_usd == 1.0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"paid_services_ack": "yes"}, "acknowledgement"),
        ({"environment": "production"}, "environment=qualification"),
        ({"max_cost_usd": 0.24}, "between USD 0.25 and 2.00"),
        ({"max_cost_usd": 2.01}, "between USD 0.25 and 2.00"),
        ({"campaign_cap_usd": 24.99}, "equal USD 25.00"),
        ({"max_calls": 5}, "between 1 and 4"),
        ({"max_turns": 0}, "between 1 and 20"),
        ({"max_audio_seconds": 181}, "between 1 and 180"),
        ({"max_wall_seconds": 601}, "between 1 and 600"),
        ({"max_calls": 4, "max_turns": 3}, "cannot be below"),
        ({"source_sha": "abc"}, "40-character Git SHA"),
        ({"source_dirty": True}, "dirty source tree"),
    ],
)
def test_settings_refuse_bad_ack_environment_budget_source_and_caps(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(LiveQualificationError, match=message):
        _settings(tmp_path, input_files, **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"control_plane_url": "http://qualification.murmur.ai"}, "control_plane_url"),
        ({"control_plane_url": "https://127.0.0.1:8000"}, "public non-loopback"),
        ({"control_plane_url": "https://10.2.3.4"}, "public non-loopback"),
        ({"runtime_url": "ws://a.livekit.cloud"}, "runtime_url"),
        ({"runtime_url": "wss://localhost:7880"}, "public non-loopback"),
        ({"runtime_url": "wss://rtc.other-cloud.ai"}, "livekit.cloud"),
        ({"runtime_url": "wss://key:secret@a.livekit.cloud"}, "embed credentials"),
        ({"runtime_url": "wss://a.livekit.cloud?signature=TOPSECRET"}, "query or fragment"),
        ({"control_plane_url": "https://qualification.murmur.ai/#token"}, "query or fragment"),
        ({"turn_url": "turns://turn.murmur.ai"}, "must not accept"),
        (
            {
                "runtime": QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
                "runtime_url": "http://voice-rtc.murmur.ai",
            },
            "runtime_url",
        ),
        (
            {
                "runtime": QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
                "runtime_url": "https://localhost:8443",
            },
            "public non-loopback",
        ),
        (
            {
                "runtime": QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
                "network": QualificationNetwork.RELAY_TLS,
                "runtime_url": "https://voice-rtc.murmur.ai",
            },
            "requires turn_url",
        ),
        (
            {
                "runtime": QualificationRuntime.PIPECAT_SMALLWEBRTC_V1,
                "network": QualificationNetwork.RELAY_TLS,
                "runtime_url": "https://voice-rtc.murmur.ai",
                "turn_url": "turn://turn.murmur.ai:3478",
            },
            "turn_url",
        ),
    ],
)
def test_settings_refuse_loopback_insecure_embedded_and_cross_runtime_urls(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(LiveQualificationError, match=message):
        _settings(tmp_path, input_files, **changes)


def test_static_report_is_hashed_credential_free_and_honest(
    tmp_path: Path,
    input_files: tuple[Path, Path],
) -> None:
    report = build_static_report(_settings(tmp_path, input_files))
    serialized = json.dumps(report)

    assert report["status"] == "configured_static_only"
    assert report["source_sha"] == "a" * 40
    assert len(report["suite_sha256"]) == 64
    assert len(report["gates_sha256"]) == 64
    assert report["credentials_read"] is False
    assert report["network_attempted"] is False
    assert report["provider_calls"] is False
    assert PAID_SERVICES_ACK not in serialized
    assert any(
        limitation.startswith("No credential value was read")
        for limitation in report["limitations"]
    )


def test_secret_allowlist_has_real_staging_auth_and_no_test_bypass() -> None:
    livekit = required_secret_env_names(QualificationRuntime.LIVEKIT_V2)
    pipecat = required_secret_env_names(QualificationRuntime.PIPECAT_SMALLWEBRTC_V1)

    assert "MURMUR_QUALIFICATION_FIREBASE_STORAGE_STATE_PATH" in livekit & pipecat
    assert "LIVEKIT_API_SECRET" in livekit
    assert "MURMUR_TURN_PASSWORD" in pipecat
    assert "LIVEKIT_API_SECRET" not in pipecat
    assert all("TEST_AUTH" not in name and "E2E" not in name for name in livekit | pipecat)

    ambient = {
        "DEEPGRAM_KEY": "deepgram-secret",
        "LIVEKIT_API_SECRET": "livekit-secret",
        "MURMUR_TURN_PASSWORD": "turn-secret",
        "AWS_SECRET_ACCESS_KEY": "must-not-pass",
        "PATH": "/bin",
    }
    selected = select_secret_environment(QualificationRuntime.LIVEKIT_V2, ambient)
    assert selected == {
        "DEEPGRAM_KEY": "deepgram-secret",
        "LIVEKIT_API_SECRET": "livekit-secret",
    }


def test_redaction_covers_nested_keys_values_headers_userinfo_and_query() -> None:
    secret = "s3cr3t-value"
    storage_state = "/private/staging-firebase-state.json"
    text = (
        f"Authorization: Bearer {secret} DEEPGRAM_KEY={secret} "
        f"MURMUR_QUALIFICATION_FIREBASE_STORAGE_STATE_PATH={storage_state} "
        f"https://user:{secret}@host.ai/path?token={secret}&ok=1 raw={secret}"
    )

    redacted = redact_text(text, secret_values=(secret,))
    mapping = redact_mapping(
        {
            "api_key": secret,
            "DEEPGRAM_KEY": secret,
            "MURMUR_QUALIFICATION_FIREBASE_STORAGE_STATE_PATH": storage_state,
            "nested": {"message": text, "safe": "kept"},
            "items": [{"password": secret}, text],
        },
        secret_values=(secret,),
    )
    serialized = json.dumps(mapping)

    assert secret not in redacted
    assert storage_state not in redacted
    assert secret not in serialized
    assert storage_state not in serialized
    assert "<redacted>" in redacted
    assert mapping["api_key"] == "<redacted>"
    assert mapping["nested"]["safe"] == "kept"  # type: ignore[index]


def test_budget_ledger_reserves_reconciles_releases_and_refuses_reuse(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "private" / "budget.json")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    reservation = ledger.reserve(
        run_id="run-one",
        amount_usd=2.0,
        reservation_id="reservation-one",
        now=now,
    )
    assert reservation.reserved_usd == 2.0
    assert ledger.summary()["committed_or_reserved_usd"] == 2.0

    ledger.reconcile(
        reservation_id="reservation-one",
        actual_cost_usd=0.75,
        outcome="passed",
        now=now,
    )
    assert ledger.summary() == {
        "campaign_cap_usd": 25.0,
        "committed_usd": 0.75,
        "committed_or_reserved_usd": 0.75,
        "remaining_usd": 24.25,
    }

    with pytest.raises(LiveQualificationError, match="already reconciled"):
        ledger.reconcile(
            reservation_id="reservation-one",
            actual_cost_usd=0.5,
            outcome="failed",
        )
    with pytest.raises(LiveQualificationError, match="already exists"):
        ledger.reserve(
            run_id="run-two",
            amount_usd=1,
            reservation_id="reservation-one",
        )
    with pytest.raises(LiveQualificationError, match="already has a reservation"):
        ledger.reserve(
            run_id="run-one",
            amount_usd=1,
            reservation_id="reservation-two",
        )


def test_budget_ledger_enforces_per_run_and_cumulative_caps(tmp_path: Path) -> None:
    ledger = BudgetLedger(tmp_path / "budget.json")

    for index in range(12):
        ledger.reserve(
            run_id=f"run-{index}",
            amount_usd=2.0,
            reservation_id=f"reservation-{index}",
        )
    ledger.reserve(run_id="run-last", amount_usd=1.0, reservation_id="reservation-last")
    assert ledger.summary()["remaining_usd"] == 0.0

    with pytest.raises(LiveQualificationError, match="campaign cap"):
        ledger.reserve(run_id="run-over", amount_usd=0.25, reservation_id="reservation-over")
    with pytest.raises(LiveQualificationError, match=r"between USD 0\.25 and 2\.00"):
        BudgetLedger(tmp_path / "other.json").reserve(
            run_id="run-over",
            amount_usd=2.01,
            reservation_id="reservation-over",
        )


def test_budget_ledger_serializes_concurrent_reservations(tmp_path: Path) -> None:
    path = tmp_path / "concurrent" / "budget.json"
    workers = 12
    start = Barrier(workers)

    def reserve(index: int) -> None:
        start.wait(timeout=5)
        BudgetLedger(path).reserve(
            run_id=f"run-{index}",
            amount_usd=1.0,
            reservation_id=f"reservation-{index}",
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(reserve, range(workers)))

    document = json.loads(path.read_text(encoding="utf-8"))
    assert [event["sequence"] for event in document["events"]] == list(range(1, workers + 1))
    assert BudgetLedger(path).summary()["committed_or_reserved_usd"] == workers


def test_budget_ledger_rejects_overspend_and_insecure_or_tampered_files(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    ledger = BudgetLedger(path)
    ledger.reserve(run_id="run-one", amount_usd=1.0, reservation_id="reservation-one")

    with pytest.raises(LiveQualificationError, match="exceeds the per-run reservation"):
        ledger.reconcile(
            reservation_id="reservation-one",
            actual_cost_usd=1.01,
            outcome="failed",
        )

    os.chmod(path, 0o644)
    with pytest.raises(LiveQualificationError, match="permissions must be 0600"):
        ledger.summary()

    os.chmod(path, 0o600)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["events"][0]["sequence"] = 9
    path.write_text(json.dumps(document), encoding="utf-8")
    os.chmod(path, 0o600)
    with pytest.raises(LiveQualificationError, match="sequence is invalid"):
        ledger.summary()


def test_atomic_write_preserves_old_ledger_if_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "private" / "artifact.json"
    atomic_write_json(path, {"version": 1})

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        atomic_write_json(path, {"version": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_private_artifacts_and_ledger_use_0700_and_0600(tmp_path: Path) -> None:
    report = tmp_path / "run" / "preflight.json"
    write_private_report(report, {"status": "configured", "api_key": "must-redact"})
    ledger = BudgetLedger(tmp_path / "ledger" / "budget.json")
    ledger.reserve(run_id="run-one", amount_usd=1, reservation_id="reservation-one")

    assert stat.S_IMODE(report.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert "must-redact" not in report.read_text(encoding="utf-8")
    assert stat.S_IMODE(ledger.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600
    assert stat.S_IMODE((ledger.path.parent / ".budget.json.lock").stat().st_mode) == 0o600


def test_private_write_refuses_existing_public_parent_without_changing_it(
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)

    with pytest.raises(LiveQualificationError, match="permissions must be 0700"):
        atomic_write_json(shared / "artifact.json", {"status": "refused"})

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert not (shared / "artifact.json").exists()


def test_actual_run_refuses_before_budget_artifacts_credentials_or_network(
    tmp_path: Path,
    input_files: tuple[Path, Path],
) -> None:
    settings = _settings(tmp_path, input_files)

    with pytest.raises(LiveQualificationError, match="adapter is not implemented") as error:
        run_live_qualification(settings)

    assert "no credentials were read" in str(error.value)
    assert "no budget was reserved" in str(error.value)
    assert "no network or provider call was made" in str(error.value)
    assert not settings.output_root.exists()
    assert not settings.ledger_path.exists()


def _cli_arguments(
    command: str,
    tmp_path: Path,
    input_files: tuple[Path, Path],
) -> list[str]:
    suite, gates = input_files
    return [
        command,
        "--run-id",
        "cli-run",
        "--runtime",
        "livekit_v2",
        "--network",
        "direct",
        "--environment",
        "qualification",
        "--ack",
        PAID_SERVICES_ACK,
        "--max-cost-usd",
        "1",
        "--campaign-cap-usd",
        "25",
        "--control-plane-url",
        "https://qualification.murmur.ai",
        "--runtime-url",
        "wss://project-a.livekit.cloud",
        "--suite",
        str(suite),
        "--gates",
        str(gates),
        "--output-root",
        str(tmp_path / "output"),
        "--budget-ledger",
        str(tmp_path / "ledger.json"),
    ]


def test_cli_dry_run_is_side_effect_free_and_does_not_print_ambient_secrets(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ambient_secret = "must-never-be-read-or-printed"
    monkeypatch.setenv("LIVEKIT_API_SECRET", ambient_secret)
    monkeypatch.setattr(
        voice_live_stack,
        "inspect_source_state",
        lambda _root: SourceState(sha="a" * 40, dirty=False),
    )
    args = voice_live_stack._parser().parse_args(_cli_arguments("dry-run", tmp_path, input_files))

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert ambient_secret not in output
    assert json.loads(output)["credentials_read"] is False
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "ledger.json").exists()


def test_cli_preflight_writes_only_private_redacted_static_report(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        voice_live_stack,
        "inspect_source_state",
        lambda _root: SourceState(sha="b" * 40, dirty=False),
    )
    args = voice_live_stack._parser().parse_args(_cli_arguments("preflight", tmp_path, input_files))

    assert args.handler(args) == 0
    summary = json.loads(capsys.readouterr().out)
    report_path = Path(summary["report_path"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["credentials_read"] is False
    assert report["network_attempted"] is False
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert not (tmp_path / "ledger.json").exists()


def test_cli_run_refuses_before_creating_report_or_ledger(
    tmp_path: Path,
    input_files: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        voice_live_stack,
        "inspect_source_state",
        lambda _root: SourceState(sha="c" * 40, dirty=False),
    )
    args = voice_live_stack._parser().parse_args(_cli_arguments("run", tmp_path, input_files))

    with pytest.raises(LiveQualificationError, match="adapter is not implemented"):
        args.handler(args)
    assert not (tmp_path / "output").exists()
    assert not (tmp_path / "ledger.json").exists()
