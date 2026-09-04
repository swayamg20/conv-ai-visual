"""Offline guards for the paid visual-act routing corpus."""

from __future__ import annotations

import json
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from murmur.live_scene.visual_act_engine import VisualActRoutingEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manual" / "probe_visual_act_router.py"
PROBE = runpy.run_path(str(SCRIPT))


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _decision_line(
    decision: str,
    *,
    stage: str | None = None,
    component_id: str | None = None,
    reason_code: str | None = None,
) -> str:
    payload: dict[str, object] = {"v": 1, "decision": decision}
    if stage is not None:
        payload["targetStage"] = stage
    if component_id is not None:
        payload["componentId"] = component_id
    if reason_code is not None:
        payload["reasonCode"] = reason_code
    return json.dumps(payload, separators=(",", ":")) + "\n"


class _FakeDelegate:
    def __init__(self, streams: list[list[str]]) -> None:
        self._streams = list(streams)
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        items = self._streams.pop(0)

        async def iterate():
            for item in items:
                yield item

        return iterate()

    async def aclose(self) -> None:
        self.closed = True


class _FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _passing_record(case: object) -> dict[str, object]:
    expected_decision = case.expected_decision  # type: ignore[attr-defined]
    expected_stage = case.expected_stage  # type: ignore[attr-defined]
    expected_reason = case.expected_reason_code  # type: ignore[attr-defined]
    base_prefix = case.base_prefix  # type: ignore[attr-defined]
    return {
        "caseId": case.case_id,  # type: ignore[attr-defined]
        "category": case.category,  # type: ignore[attr-defined]
        "promptSha256": "0" * 64,
        "basePrefixAtoms": base_prefix,
        "supportedByVocabulary": case.supported_by_vocabulary,  # type: ignore[attr-defined]
        "expectedDecision": expected_decision,
        "expectedStage": expected_stage,
        "expectedReasonCode": expected_reason,
        "terminal": "completed",
        "failureCode": None,
        "providerAttempts": 1,
        "repaired": False,
        "firstAttemptValid": True,
        "selectedDecision": expected_decision,
        "selectedStage": expected_stage,
        "selectedReasonCode": expected_reason,
        "resolvedComponentId": "areas" if expected_decision != "abstain" else None,
        "reusedBaseComponent": True if base_prefix else None,
        "missingRoles": [],
        "routingExpectationMet": True,
        "observedDecisionMs": 100.0,
        "pacingWaitMs": 0.0,
    }


def test_router_probe_dry_run_pins_full_corpus_and_twenty_attempt_cost() -> None:
    result = _run(
        "--max-cost-usd",
        "0.041",
        "--case-limit",
        "10",
        "--max-tokens",
        "2048",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "caseCount": 10,
        "corpusSha256": "374aa407164cd9ca84ec8a311792c895ea39152dcdb2daa90dfec0a931e6e549",
        "maxCostUsd": "0.041000000",
        "mode": "dry-run",
        "pricingProfile": "azure-gpt-oss-120b-global-standard-2026-03-01",
        "reservedMaxCostUsd": "0.040441800",
        "reservedMaxInputTokens": 105_772,
        "reservedMaxOutputTokens": 40_960,
        "reservedMaxProviderAttempts": 20,
    }


def test_router_probe_refuses_below_the_exact_full_corpus_bound() -> None:
    result = _run("--max-cost-usd", "0.040", "--case-limit", "10", "--dry-run")

    assert result.returncode == 2
    assert "provider cost ceiling reached" in result.stderr
    assert result.stdout == ""


def test_router_probe_corpus_contract_is_exact_and_balanced() -> None:
    cases = PROBE["CASES"]

    assert [case.case_id for case in cases] == [
        "triangle_explicit",
        "triangle_hinglish",
        "areas_explicit",
        "areas_hold_identity",
        "identity_trust_boundary",
        "unsupported_derivative",
        "unsupported_http",
        "resume_b1_to_areas",
        "resume_b7_to_identity",
        "completed_b8_no_progress",
    ]
    assert [case.expected_decision for case in cases].count("start_visual") == 5
    assert [case.expected_decision for case in cases].count("abstain") == 3
    assert [case.expected_decision for case in cases].count("continue_visual") == 2
    starts = [case.expected_stage for case in cases if case.expected_decision == "start_visual"]
    assert {stage: starts.count(stage) for stage in ("triangle", "areas", "identity")} == {
        "triangle": 2,
        "areas": 2,
        "identity": 1,
    }
    mutating_stages = [case.expected_stage for case in cases if case.expected_decision != "abstain"]
    assert {stage: mutating_stages.count(stage) for stage in ("triangle", "areas", "identity")} == {
        "triangle": 2,
        "areas": 3,
        "identity": 2,
    }
    assert sum(case.supported_by_vocabulary for case in cases) == 8
    reasons = [case.expected_reason_code for case in cases if case.expected_reason_code]
    assert reasons.count("unsupported_intent") == 2
    assert reasons.count("no_forward_progress") == 1
    assert all(
        not case.supported_by_vocabulary
        for case in cases
        if case.expected_reason_code == "unsupported_intent"
    )
    assert cases[-1].supported_by_vocabulary is True
    assert cases[-1].base_prefix == 8
    assert PROBE["MAX_CASES"] == 10
    assert PROBE["MAX_PROVIDER_ATTEMPTS"] == 20


def test_router_probe_includes_indirect_language_at_each_stage() -> None:
    cases = {case.case_id: case for case in PROBE["CASES"]}

    assert "right triangle" not in cases["triangle_hinglish"].prompt.casefold()
    assert "all three side-area squares" not in cases["areas_hold_identity"].prompt.casefold()
    assert "a² + b² = c²" not in cases["identity_trust_boundary"].prompt.casefold()
    resume_prompt = cases["resume_b1_to_areas"].prompt.casefold()
    assert "continue" not in resume_prompt
    assert "existing" not in resume_prompt


def test_dry_run_never_enters_live_corpus_or_constructs_a_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    entered_live_path = False
    constructed_engine = False

    async def forbidden_live_path(*_args: object, **_kwargs: object) -> None:
        nonlocal entered_live_path
        entered_live_path = True
        raise AssertionError("dry-run entered live provider path")

    class ForbiddenEngine:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal constructed_engine
            constructed_engine = True
            raise AssertionError("dry-run constructed a routing client")

    monkeypatch.setitem(PROBE["main"].__globals__, "_run_corpus", forbidden_live_path)
    monkeypatch.setitem(
        PROBE["main"].__globals__,
        "VisualActRoutingEngine",
        ForbiddenEngine,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--max-cost-usd", "0.50", "--dry-run"],
    )

    assert PROBE["main"]() == 0
    assert entered_live_path is False
    assert constructed_engine is False
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"


def test_router_probe_refuses_live_mode_without_exact_acknowledgement() -> None:
    result = _run("--max-cost-usd", "0.50", "--case-limit", "1")

    assert result.returncode == 2
    assert "I_ACCEPT_PROVIDER_COST" in result.stderr
    assert result.stdout == ""


def test_budget_ledger_refuses_before_delegate_dispatch() -> None:
    messages = [{"role": "user", "content": "bounded"}]
    input_bound = PROBE["_message_input_token_bound"](messages)
    cost = PROBE["_reservation_cost_nano_usd"](input_bound, 2_048)
    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=cost - 1,
        max_tokens=2_048,
        max_provider_attempts=20,
    )
    delegate = _FakeDelegate([["unused"]])
    client = PROBE["BudgetedSceneModelClient"](delegate, ledger, "case")

    with pytest.raises(PROBE["ProbeRefusal"], match="cost ceiling"):
        client.stream(messages, max_tokens=2_048)

    assert delegate.calls == []
    assert ledger.reservations == []


def test_budget_ledger_enforces_its_total_attempt_ceiling() -> None:
    messages = [{"role": "user", "content": "bounded"}]
    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=500_000_000,
        max_tokens=2_048,
        max_provider_attempts=1,
    )

    ledger.reserve(case_id="case-a", messages=messages, max_tokens=2_048)
    with pytest.raises(PROBE["ProbeRefusal"], match="attempt ceiling"):
        ledger.reserve(case_id="case-b", messages=messages, max_tokens=2_048)

    assert len(ledger.reservations) == 1
    assert ledger.attempts_for("case-b") == 0


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--case-limit", "0"), "case-limit"),
        (("--case-limit", "11"), "case-limit"),
        (("--max-tokens", "2047"), "audited ceiling"),
        (("--timeout-seconds", "0"), "timeout-seconds"),
        (("--timeout-seconds", "nan"), "timeout-seconds"),
        (("--request-start-interval-seconds", "6.0"), "requests/minute"),
        (("--max-cost-usd", "0"), "greater than zero"),
        (("--max-cost-usd", "0.500000001"), "USD 0.50"),
    ],
)
def test_router_probe_refuses_unbounded_parameters(
    arguments: tuple[str, str],
    message: str,
) -> None:
    base = ["--max-cost-usd", "0.50", "--dry-run"]
    if arguments[0] == "--max-cost-usd":
        base[:2] = list(arguments)
    else:
        base.extend(arguments)

    result = _run(*base)

    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""


def test_report_writer_is_atomic_private_and_output_is_confined(tmp_path: Path) -> None:
    report_path = tmp_path / "nested" / "report.json"
    PROBE["_write_report"](report_path, {"safe": "value"})

    assert json.loads(report_path.read_text()) == {"safe": "value"}
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.parent.stat().st_mode) == 0o700
    assert list(report_path.parent.glob(".*.tmp")) == []

    with pytest.raises(PROBE["ProbeRefusal"], match="var/live-scene/evaluations"):
        PROBE["_safe_output_path"](str(tmp_path / "outside.json"))


def test_metrics_require_ninety_percent_plus_perfect_unsupported_and_resume_gates() -> None:
    passing = [_passing_record(case) for case in PROBE["CASES"]]
    metrics = PROBE["_metrics"](passing, target_count=10, aborted_reason=None)

    assert metrics["supportedExpectationNumerator"] == 8
    assert metrics["supportedExpectationDenominator"] == 8
    assert metrics["supportedExpectationRate"] == 1.0
    assert metrics["unsupportedIntentAbstentionNumerator"] == 2
    assert metrics["unsupportedIntentAbstentionDenominator"] == 2
    assert metrics["unsupportedIntentAbstentionRate"] == 1.0
    assert metrics["noForwardProgressNumerator"] == 1
    assert metrics["noForwardProgressDenominator"] == 1
    assert metrics["noForwardProgressRate"] == 1.0
    assert metrics["resumeReuseNumerator"] == 2
    assert metrics["resumeReuseDenominator"] == 2
    assert metrics["resumeReuseRate"] == 1.0
    assert metrics["routerQualificationPassed"] is True

    supported_miss = [dict(record) for record in passing]
    supported_miss[0]["routingExpectationMet"] = False
    failed_supported_metrics = PROBE["_metrics"](
        supported_miss,
        target_count=10,
        aborted_reason=None,
    )
    assert failed_supported_metrics["supportedExpectationNumerator"] == 7
    assert failed_supported_metrics["supportedExpectationDenominator"] == 8
    assert failed_supported_metrics["supportedExpectationRate"] == 0.875
    assert failed_supported_metrics["supportedExpectationPassed"] is False
    assert failed_supported_metrics["routerQualificationPassed"] is False

    unsupported_miss = [dict(record) for record in passing]
    unsupported_miss[5]["routingExpectationMet"] = False
    assert (
        PROBE["_metrics"](
            unsupported_miss,
            target_count=10,
            aborted_reason=None,
        )["unsupportedIntentAbstentionPassed"]
        is False
    )

    resume_miss = [dict(record) for record in passing]
    resume_miss[7]["reusedBaseComponent"] = False
    assert (
        PROBE["_metrics"](
            resume_miss,
            target_count=10,
            aborted_reason=None,
        )["resumeReusePassed"]
        is False
    )

    no_forward_miss = [dict(record) for record in passing]
    no_forward_miss[9]["routingExpectationMet"] = False
    no_forward_metrics = PROBE["_metrics"](
        no_forward_miss,
        target_count=10,
        aborted_reason=None,
    )
    assert no_forward_metrics["noForwardProgressRate"] == 0.0
    assert no_forward_metrics["noForwardProgressPassed"] is False
    assert no_forward_metrics["routerQualificationPassed"] is False


def test_metrics_gate_only_warm_latency_at_the_inclusive_boundaries() -> None:
    records = [_passing_record(case) for case in PROBE["CASES"]]
    records[0]["observedDecisionMs"] = 99_000.0
    for record in records[1:]:
        record["observedDecisionMs"] = 1_500.0
    records[-1]["observedDecisionMs"] = 3_000.0

    boundary = PROBE["_metrics"](records, target_count=10, aborted_reason=None)

    assert boundary["coldDecisionMs"] == 99_000.0
    assert boundary["warmMedianDecisionMs"] == 1_500.0
    assert boundary["warmP95DecisionMs"] == 3_000.0
    assert boundary["latencyThresholdsPassed"] is True
    assert boundary["routerQualificationPassed"] is True

    records[-1]["observedDecisionMs"] = 3_000.001
    over_p95 = PROBE["_metrics"](records, target_count=10, aborted_reason=None)

    assert over_p95["latencyThresholdsPassed"] is False
    assert over_p95["routerQualificationPassed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_index", "decision", "expected"),
    [
        (
            5,
            _decision_line("abstain", reason_code="unsupported_intent"),
            {
                "selectedDecision": "abstain",
                "selectedStage": None,
                "selectedReasonCode": "unsupported_intent",
                "selectedComponentId": None,
                "resolvedComponentId": None,
                "reusedBaseComponent": None,
                "missingRoles": [],
            },
        ),
        (
            7,
            _decision_line("continue_visual", stage="areas", component_id="areas"),
            {
                "selectedDecision": "continue_visual",
                "selectedStage": "areas",
                "selectedReasonCode": None,
                "selectedComponentId": "areas",
                "resolvedComponentId": "areas",
                "reusedBaseComponent": True,
                "missingRoles": [
                    "square_a",
                    "label_a2",
                    "square_b",
                    "label_b2",
                    "square_c",
                    "label_c2",
                ],
            },
        ),
        (
            9,
            _decision_line("abstain", reason_code="no_forward_progress"),
            {
                "selectedDecision": "abstain",
                "selectedStage": None,
                "selectedReasonCode": "no_forward_progress",
                "selectedComponentId": None,
                "resolvedComponentId": None,
                "reusedBaseComponent": False,
                "missingRoles": [],
            },
        ),
    ],
)
async def test_run_case_maps_abstain_and_resume_results_without_raw_model_data(
    case_index: int,
    decision: str,
    expected: dict[str, object],
) -> None:
    case = PROBE["CASES"][case_index]
    delegate = _FakeDelegate([[decision]])
    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=500_000_000,
        max_tokens=2_048,
        max_provider_attempts=20,
    )
    budgeted = PROBE["BudgetedSceneModelClient"](delegate, ledger, case.case_id)
    pacer = PROBE["DispatchPacer"](0)
    client = PROBE["PacedBudgetedSceneModelClient"](budgeted, pacer)
    engine = VisualActRoutingEngine(client, max_tokens=2_048, timeout_seconds=5)

    record = await PROBE["_run_case"](
        case,
        engine=engine,
        ledger=ledger,
        pacer=pacer,
    )
    await client.aclose()

    assert record["terminal"] == "completed"
    assert record["providerAttempts"] == 1
    assert record["routingExpectationMet"] is True
    assert {key: record[key] for key in expected} == expected
    assert decision.strip() not in json.dumps(record)


@pytest.mark.asyncio
async def test_engine_repair_is_budgeted_and_paced_as_a_second_dispatch() -> None:
    secret_prompt = "TOP-SECRET-PROMPT introduce only the right triangle."
    case = PROBE["EvaluationCase"](
        "offline_repair",
        "test",
        secret_prompt,
        0,
        "start_visual",
        True,
        "triangle",
    )
    delegate = _FakeDelegate(
        [
            ["not-json\n"],
            [_decision_line("start_visual", stage="triangle")],
        ]
    )
    ledger = PROBE["BudgetLedger"](
        max_cost_nano_usd=500_000_000,
        max_tokens=2_048,
        max_provider_attempts=20,
    )
    budgeted = PROBE["BudgetedSceneModelClient"](delegate, ledger, case.case_id)
    fake_time = _FakeTime()
    pacer = PROBE["DispatchPacer"](
        6.1,
        clock=fake_time.monotonic,
        sleep=fake_time.sleep,
    )
    client = PROBE["PacedBudgetedSceneModelClient"](budgeted, pacer)
    engine = VisualActRoutingEngine(
        client,
        max_tokens=2_048,
        timeout_seconds=5,
    )

    record = await PROBE["_run_case"](
        case,
        engine=engine,
        ledger=ledger,
        pacer=pacer,
    )
    await client.aclose()

    assert record["terminal"] == "completed"
    assert record["providerAttempts"] == 2
    assert record["repaired"] is True
    assert record["routingExpectationMet"] is True
    assert secret_prompt not in json.dumps(record)
    assert len(delegate.calls) == 2
    assert len(ledger.reservations) == 2
    assert fake_time.sleeps == [pytest.approx(6.1)]
    assert pacer.total_wait_seconds == pytest.approx(6.1)
    assert record["pacingWaitMs"] == pytest.approx(6_100.0)
    assert delegate.closed is True
