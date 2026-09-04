"""Offline guards for the paid semantic live-scene corpus."""

from __future__ import annotations

import json
import runpy
import stat
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manual" / "probe_semantic_live_scene.py"
PROBE = runpy.run_path(str(SCRIPT))


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _beat_line(
    *,
    stage: str = "triangle",
    act: str = "introduce",
    narration: str = "Start with the right triangle.",
) -> str:
    return (
        json.dumps(
            {
                "v": 1,
                "beatId": "probe-beat",
                "narration": narration,
                "act": act,
                "directive": {
                    "kind": "pythagorean_area_identity",
                    "id": "areas",
                    "revealThrough": stage,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


class _FakeDelegate:
    def __init__(self, streams: list[list[str]]) -> None:
        self._streams = list(streams)
        self.calls = 0
        self.closed = False

    def stream(self, *_args, **_kwargs):
        self.calls += 1
        items = self._streams.pop(0)

        async def iterate():
            for item in items:
                yield item

        return iterate()

    async def aclose(self) -> None:
        self.closed = True


def _offline_service(case_id: str, streams: list[list[str]]):
    from murmur.live_scene.service import SceneAuthoringService

    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=500_000_000,
        max_tokens=2_048,
    )
    delegate = _FakeDelegate(streams)
    client = PROBE["BudgetedSceneModelClient"](delegate, ledger, case_id)
    service = SceneAuthoringService(
        client=client,
        temperature=0.2,
        max_tokens=2_048,
        timeout_seconds=5,
    )
    return service, client, delegate, ledger


def test_semantic_probe_dry_run_pins_and_bounds_the_full_corpus() -> None:
    result = _run(
        "--max-cost-usd",
        "0.08",
        "--case-limit",
        "20",
        "--max-tokens",
        "2048",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary == {
        "caseCount": 20,
        "corpusSha256": "194b34ee5a2f6432585a0b55dd8e6ed2ed1b782f64587646d4cc7e630e61cb55",
        "maxCostUsd": "0.080000000",
        "mode": "dry-run",
        "pricingProfile": "azure-gpt-oss-120b-global-standard-2026-03-01",
        "reservedMaxCostUsd": "0.075716400",
        "reservedMaxInputTokens": 177_096,
        "reservedMaxOutputTokens": 81_920,
        "reservedMaxProviderAttempts": 40,
    }


def test_semantic_probe_refuses_below_the_exact_conservative_corpus_bound() -> None:
    result = _run(
        "--max-cost-usd",
        "0.075",
        "--case-limit",
        "20",
        "--dry-run",
    )

    assert result.returncode == 2
    assert "provider cost ceiling reached" in result.stderr
    assert result.stdout == ""


def test_semantic_probe_refuses_live_mode_without_exact_cost_acknowledgement() -> None:
    result = _run(
        "--max-cost-usd",
        "0.08",
        "--case-limit",
        "1",
    )

    assert result.returncode == 2
    assert "I_ACCEPT_PROVIDER_COST" in result.stderr
    assert result.stdout == ""


def test_budget_ledger_refuses_one_nano_usd_before_delegate_dispatch() -> None:
    messages = [{"role": "user", "content": "bounded"}]
    input_bound = PROBE["_message_input_token_bound"](messages)
    cost = PROBE["_reservation_cost_nano_usd"](input_bound, 2_048)
    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=cost - 1,
        max_tokens=2_048,
    )
    delegate = _FakeDelegate([["unused"]])
    client = PROBE["BudgetedSceneModelClient"](delegate, ledger, "case")

    with pytest.raises(PROBE["ProbeRefusal"], match="cost ceiling"):
        client.stream(messages, max_tokens=2_048)

    assert delegate.calls == 0
    assert ledger.reservations == []


def test_semantic_sse_decoder_accepts_every_byte_boundary_and_rejects_truncation() -> None:
    from murmur.live_scene.contracts import SceneStreamStartedEvent
    from murmur.live_scene.semantic_wire import encode_semantic_scene_stream_event

    encoded = encode_semantic_scene_stream_event(
        SceneStreamStartedEvent(generation=1, attempt=1, base_revision=0)
    ).encode("utf-8")
    decoder = PROBE["SemanticSseDecoder"]()
    decoded = []
    for byte in encoded:
        decoded.extend(decoder.feed(bytes((byte,))))
    decoder.finish()

    assert len(decoded) == 1
    assert decoded[0].type == "scene_stream_started"

    truncated = PROBE["SemanticSseDecoder"]()
    truncated.feed(encoded[:-1])
    with pytest.raises(PROBE["ProbeProtocolError"], match="truncated_sse_record"):
        truncated.finish()


@pytest.mark.asyncio
async def test_semantic_probe_records_first_attempt_success_without_prompt_text() -> None:
    secret_prompt = "SECRET_SENTINEL introduce only a triangle"
    case = PROBE["EvaluationCase"](
        "offline_success",
        "test",
        secret_prompt,
        0,
        "triangle",
        ("introduce",),
    )
    service, client, delegate, ledger = _offline_service(
        case.case_id,
        [[_beat_line()]],
    )

    record = await PROBE["_run_case"](
        case,
        generation=1,
        service=service,
        ledger=ledger,
    )
    await client.aclose()

    assert record["terminal"] == "completed"
    assert record["providerAttempts"] == 1
    assert record["roles"] == ["triangle"]
    assert record["semanticExpectationMet"] is True
    assert "SECRET_SENTINEL" not in json.dumps(record)
    assert delegate.calls == 1
    assert delegate.closed is True


@pytest.mark.asyncio
async def test_semantic_probe_counts_the_service_owned_repair_attempt() -> None:
    case = PROBE["CASES"][0]
    service, client, delegate, ledger = _offline_service(
        case.case_id,
        [["not-json\n"], [_beat_line()]],
    )

    record = await PROBE["_run_case"](
        case,
        generation=1,
        service=service,
        ledger=ledger,
    )
    await client.aclose()

    assert record["terminal"] == "completed"
    assert record["repaired"] is True
    assert record["providerAttempts"] == 2
    assert record["eventTypes"] == [
        "scene_stream_started",
        "scene_stream_repairing",
        "semantic_scene_patch",
        "scene_stream_completed",
    ]
    assert delegate.calls == 2


@pytest.mark.asyncio
async def test_semantic_probe_accepts_the_expected_completed_prefix_rejection() -> None:
    case = PROBE["CASES"][-1]
    backward = _beat_line(stage="triangle")
    service, client, delegate, ledger = _offline_service(
        case.case_id,
        [[backward], [backward]],
    )

    record = await PROBE["_run_case"](
        case,
        generation=20,
        service=service,
        ledger=ledger,
    )
    await client.aclose()

    assert record["terminal"] == "failed"
    assert record["failureCode"] == "invalid_scene_stream"
    assert record["atomCount"] == 0
    assert record["providerAttempts"] == 2
    assert record["semanticExpectationMet"] is True
    assert delegate.calls == 2


def test_report_writer_is_atomic_private_and_contains_only_supplied_fields(tmp_path: Path) -> None:
    report_path = tmp_path / "nested" / "report.json"
    PROBE["_write_report"](report_path, {"safe": "value"})

    assert json.loads(report_path.read_text()) == {"safe": "value"}
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert list(report_path.parent.glob(".*.tmp")) == []


def test_output_path_must_stay_inside_ignored_evaluation_directory(tmp_path: Path) -> None:
    with pytest.raises(PROBE["ProbeRefusal"], match="var/live-scene/evaluations"):
        PROBE["_safe_output_path"](str(tmp_path / "report.json"))
