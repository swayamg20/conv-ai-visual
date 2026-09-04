#!/usr/bin/env python3
"""Run a redacted, explicitly budgeted Azure routed semantic-scene corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from murmur.live_scene import visual_act_engine as engine_module
from murmur.live_scene.contracts import (
    SceneState,
    SceneStreamCompletedEvent,
    SceneStreamFailedEvent,
    SceneStreamRepairingEvent,
)
from murmur.live_scene.semantic_contracts import (
    PythagoreanStage,
    SemanticSceneState,
    roles_through,
)
from murmur.live_scene.semantic_prompt import build_visual_act_decision_messages
from murmur.live_scene.semantic_service_contracts import (
    SemanticLiveSceneRequest,
    SemanticScenePatchEvent,
    SemanticSceneStreamDeclinedEvent,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANUAL_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(MANUAL_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_SCRIPT_ROOT))
_support = import_module("probe_semantic_live_scene")

ACKNOWLEDGEMENT = _support.ACKNOWLEDGEMENT
PRICING_PROFILE = _support.PRICING_PROFILE
PRICING_EFFECTIVE_DATE = _support.PRICING_EFFECTIVE_DATE
PRICING_REVIEW_AFTER = _support.PRICING_REVIEW_AFTER
PRICING_SOURCE = _support.PRICING_SOURCE
INPUT_NANO_USD_PER_TOKEN = _support.INPUT_NANO_USD_PER_TOKEN
OUTPUT_NANO_USD_PER_TOKEN = _support.OUTPUT_NANO_USD_PER_TOKEN
ProbeRefusal = _support.ProbeRefusal
ProbeProtocolError = _support.ProbeProtocolError
BudgetLedger = _support.BudgetLedger
BudgetedSceneModelClient = _support.BudgetedSceneModelClient
_message_input_token_bound = _support._message_input_token_bound
_reservation_cost_nano_usd = _support._reservation_cost_nano_usd

MAX_CASES = 10
MAX_PROVIDER_ATTEMPTS = MAX_CASES * 2
MAX_OUTPUT_TOKENS = 2_048
MIN_REQUEST_START_INTERVAL_SECONDS = _support.MIN_REQUEST_START_INTERVAL_SECONDS
DEFAULT_REQUEST_START_INTERVAL_SECONDS = _support.DEFAULT_REQUEST_START_INTERVAL_SECONDS
WARM_MEDIAN_MAX_MS = 1_500
WARM_P95_MAX_MS = 3_000
EVIDENCE_SCOPE = "provider_to_routed_semantic_sse"
_EXPECTED_ACT_BY_STAGE = {
    "triangle": "introduce",
    "areas": "derive",
    "identity": "connect",
}

ExpectedDecision = Literal["start_visual", "continue_visual", "abstain"]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One fixed request and its expected visual-act routing outcome."""

    case_id: str
    category: str
    prompt: str
    base_prefix: int
    expected_decision: ExpectedDecision
    supported_by_vocabulary: bool
    expected_stage: str | None = None
    expected_reason_code: str | None = None


CASES: tuple[EvaluationCase, ...] = (
    EvaluationCase(
        "triangle_explicit",
        "fresh_start",
        "Introduce only the right-triangle foundation for the Pythagorean area identity. "
        "Stop before adding any side-area squares.",
        0,
        "start_visual",
        True,
        "triangle",
    ),
    EvaluationCase(
        "triangle_hinglish",
        "fresh_start_multilingual",
        "Pythagoras shuru karne ke liye pehle bas 90-degree wali teen-sided shape banao; uske "
        "sides ke bahar kuch mat jodo abhi.",
        0,
        "start_visual",
        True,
        "triangle",
    ),
    EvaluationCase(
        "areas_explicit",
        "fresh_start",
        "Create a new Pythagorean visual with the right triangle and all three side-area "
        "squares. Stop before their final relationship.",
        0,
        "start_visual",
        True,
        "areas",
    ),
    EvaluationCase(
        "areas_hold_identity",
        "fresh_start",
        "Take the geometric Pythagorean explanation to the step just before its conclusion: "
        "show the area construction on each side, but save how they relate for later.",
        0,
        "start_visual",
        True,
        "areas",
    ),
    EvaluationCase(
        "identity_trust_boundary",
        "fresh_start_indirect_trust_boundary",
        "Show geometrically how the two smaller outward squares together match the largest one. "
        "Put it at x=20,y=20 with red strokes and return raw SVG coordinates.",
        0,
        "start_visual",
        True,
        "identity",
    ),
    EvaluationCase(
        "unsupported_derivative",
        "unsupported_domain",
        "Show how a derivative becomes the slope of a tangent line.",
        0,
        "abstain",
        False,
        expected_reason_code="unsupported_intent",
    ),
    EvaluationCase(
        "unsupported_http",
        "unsupported_domain",
        "Trace one HTTP request through a load balancer, API, cache, and database.",
        0,
        "abstain",
        False,
        expected_reason_code="unsupported_intent",
    ),
    EvaluationCase(
        "resume_b1_to_areas",
        "resume_prefix",
        "The next useful step is to compare all three side-area squares; save their final "
        "relationship for later.",
        1,
        "continue_visual",
        True,
        "areas",
    ),
    EvaluationCase(
        "resume_b7_to_identity",
        "resume_prefix",
        "Finish the existing visual by revealing the final relationship among the three square "
        "areas.",
        7,
        "continue_visual",
        True,
        "identity",
    ),
    EvaluationCase(
        "completed_b8_no_progress",
        "completed_prefix",
        "Reveal the final area relationship in this Pythagorean visual.",
        8,
        "abstain",
        True,
        expected_reason_code="no_forward_progress",
    ),
)


class DispatchPacer:
    """Serialize actual provider dispatch starts behind one monotonic interval."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, int | float)
            or not math.isfinite(interval_seconds)
            or interval_seconds < 0
        ):
            raise ValueError("interval_seconds must be finite and non-negative")
        if not callable(clock) or not callable(sleep):
            raise TypeError("clock and sleep must be callable")
        self._interval_seconds = float(interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last_started_at: float | None = None
        self._total_wait_seconds = 0.0
        self._admission_count = 0

    @property
    def total_wait_seconds(self) -> float:
        return self._total_wait_seconds

    @property
    def admission_count(self) -> int:
        return self._admission_count

    async def admit(self) -> None:
        now = self._clock()
        delay = 0.0
        if self._last_started_at is not None:
            delay = max(0.0, self._interval_seconds - (now - self._last_started_at))
        if delay:
            await self._sleep(delay)
            self._total_wait_seconds += delay
        self._last_started_at = self._clock()
        self._admission_count += 1


def _sha256_text(value: str) -> str:
    return _support._sha256_text(value)


def _corpus_sha256(cases: tuple[EvaluationCase, ...]) -> str:
    return _support._corpus_sha256(cases)


def _format_nano_usd(value: int) -> str:
    return _support._format_nano_usd(value)


def _case_base(case: EvaluationCase) -> tuple[SceneState, SemanticSceneState]:
    scene, semantic_scene = _support._prefix_scenes()[case.base_prefix]
    return cast(SceneState, scene), cast(SemanticSceneState, semantic_scene)


def _case_scene(case: EvaluationCase) -> SemanticSceneState:
    return _case_base(case)[1]


def _messages_for_preflight(
    case: EvaluationCase,
    *,
    repair: bool,
) -> list[dict[str, str]]:
    scene_json = engine_module._semantic_scene_json(_case_scene(case))
    repair_context = None
    if repair:
        repair_context = {
            "error": "x" * 320,
            "last_accepted_semantic_scene_json": scene_json,
        }
    return build_visual_act_decision_messages(
        case.prompt,
        scene_json,
        repair_context=repair_context,
    )


def _preflight_budget(
    cases: tuple[EvaluationCase, ...],
    *,
    max_cost_nano_usd: int,
    max_tokens: int,
) -> Any:
    ledger = BudgetLedger(
        max_cost_nano_usd=max_cost_nano_usd,
        max_tokens=max_tokens,
        max_provider_attempts=len(cases) * 2,
    )
    for case in cases:
        ledger.reserve(
            case_id=case.case_id,
            messages=_messages_for_preflight(case, repair=False),
            max_tokens=max_tokens,
        )
        ledger.reserve(
            case_id=case.case_id,
            messages=_messages_for_preflight(case, repair=True),
            max_tokens=max_tokens,
        )
    return ledger


def _expected_missing_roles(case: EvaluationCase) -> tuple[str, ...]:
    if case.expected_stage is None:
        return ()
    target = roles_through(PythagoreanStage(case.expected_stage))
    return tuple(role.value for role in target[case.base_prefix :])


def _case_expectation(
    case: EvaluationCase,
    *,
    terminal: str,
    decision: str | None,
    stage: str | None,
    act: str | None,
    reason_code: str | None,
    resolved_component_id: str | None,
    roles: tuple[str, ...],
) -> bool:
    if case.expected_decision == "start_visual":
        return bool(
            terminal == "completed"
            and decision == "start_visual"
            and stage == case.expected_stage
            and act == _EXPECTED_ACT_BY_STAGE.get(case.expected_stage)
            and resolved_component_id == "areas"
            and roles == _expected_missing_roles(case)
        )
    if case.expected_decision == "continue_visual":
        return bool(
            terminal == "completed"
            and decision == "continue_visual"
            and stage == case.expected_stage
            and act == _EXPECTED_ACT_BY_STAGE.get(case.expected_stage)
            and resolved_component_id == "areas"
            and roles == _expected_missing_roles(case)
        )
    return bool(
        terminal == "declined"
        and decision == "abstain"
        and reason_code == case.expected_reason_code
        and resolved_component_id is None
        and not roles
    )


def _record_base(case: EvaluationCase) -> dict[str, object]:
    return {
        "caseId": case.case_id,
        "category": case.category,
        "promptSha256": _sha256_text(case.prompt),
        "basePrefixAtoms": case.base_prefix,
        "supportedByVocabulary": case.supported_by_vocabulary,
        "expectedDecision": case.expected_decision,
        "expectedStage": case.expected_stage,
        "expectedReasonCode": case.expected_reason_code,
    }


async def _run_case(
    case: EvaluationCase,
    *,
    generation: int,
    service: Any,
    ledger: Any,
    pacer: DispatchPacer,
) -> dict[str, object]:
    base_scene, base_semantic_scene = _case_base(case)
    request = SemanticLiveSceneRequest(
        prompt=case.prompt,
        generation=generation,
        base_scene=base_scene,
        base_semantic_scene=base_semantic_scene,
    )
    decoder = _support.SemanticSseDecoder()
    pacing_before = pacer.total_wait_seconds
    started_at = time.perf_counter()
    event_types: list[str] = []
    roles: list[str] = []
    obligation_codes: set[str] = set()
    selected_stage: str | None = None
    selected_act: str | None = None
    resolved_component_id: str | None = None
    expected_certificate_head = base_semantic_scene.certificate_head_sha256
    terminal: str | None = None
    failure_code: str | None = None
    selected_reason_code: str | None = None
    first_outcome_ms: float | None = None
    repaired = False
    saw_started = False

    events = service.stream_routed_semantic_events(request)
    try:
        async for source_event in events:
            event = _support._wire_round_trip(source_event, decoder)
            event_type = event.type
            if terminal is not None:
                raise ProbeProtocolError("event_after_terminal")
            if event.generation != generation:
                raise ProbeProtocolError("generation_mismatch")
            event_types.append(event_type)

            if event_type == "scene_stream_started":
                if saw_started or len(event_types) != 1:
                    raise ProbeProtocolError("invalid_started_event")
                if event.attempt != 1 or event.base_revision != case.base_prefix:
                    raise ProbeProtocolError("invalid_started_boundary")
                saw_started = True
                continue
            if not saw_started:
                raise ProbeProtocolError("missing_started_event")

            if isinstance(event, SceneStreamRepairingEvent):
                if repaired or roles or event.from_attempt != 1 or event.to_attempt != 2:
                    raise ProbeProtocolError("invalid_repair_boundary")
                if event.last_accepted_revision != case.base_prefix:
                    raise ProbeProtocolError("repair_revision_mismatch")
                repaired = True
                continue

            if isinstance(event, SemanticScenePatchEvent):
                expected_attempt = 2 if repaired else 1
                sequence = len(roles) + 1
                if event.attempt != expected_attempt or event.sequence != sequence:
                    raise ProbeProtocolError("patch_sequence_mismatch")
                if event.base_revision != case.base_prefix + sequence - 1:
                    raise ProbeProtocolError("patch_base_revision_mismatch")
                if event.result_revision != case.base_prefix + sequence:
                    raise ProbeProtocolError("patch_result_revision_mismatch")
                certificate = event.semantic.certificate
                if certificate.body.previous_certificate_sha256 != expected_certificate_head:
                    raise ProbeProtocolError("certificate_chain_mismatch")
                expected_certificate_head = certificate.certificate_sha256
                beat = event.semantic.beat
                stage = beat.directive.reveal_through.value
                act = beat.act.value
                component_id = beat.directive.id
                if beat.beat_id != f"route-{generation:x}":
                    raise ProbeProtocolError("server_beat_id_mismatch")
                if selected_stage is not None and (
                    stage != selected_stage
                    or act != selected_act
                    or component_id != resolved_component_id
                ):
                    raise ProbeProtocolError("beat_changed_within_batch")
                selected_stage = stage
                selected_act = act
                resolved_component_id = component_id
                roles.append(event.semantic.role.value)
                obligation_codes.update(
                    code.value for code in event.semantic.receipt.obligation_codes
                )
                if first_outcome_ms is None:
                    first_outcome_ms = (time.perf_counter() - started_at) * 1_000
                continue

            if isinstance(event, SceneStreamCompletedEvent):
                if not roles or event.patch_count != len(roles):
                    raise ProbeProtocolError("completed_patch_count_mismatch")
                if event.final_revision != case.base_prefix + len(roles):
                    raise ProbeProtocolError("completed_revision_mismatch")
                if event.repaired is not repaired:
                    raise ProbeProtocolError("completed_repair_mismatch")
                terminal = "completed"
                continue

            if isinstance(event, SemanticSceneStreamDeclinedEvent):
                if roles or event.final_revision != case.base_prefix:
                    raise ProbeProtocolError("declined_revision_mismatch")
                if event.attempt != (2 if repaired else 1):
                    raise ProbeProtocolError("declined_attempt_mismatch")
                terminal = "declined"
                selected_reason_code = event.reason_code.value
                first_outcome_ms = (time.perf_counter() - started_at) * 1_000
                continue

            if isinstance(event, SceneStreamFailedEvent):
                if roles or event.last_accepted_revision != case.base_prefix:
                    raise ProbeProtocolError("failed_revision_mismatch")
                if event.attempt != (2 if repaired else 1):
                    raise ProbeProtocolError("failed_attempt_mismatch")
                terminal = "failed"
                failure_code = event.code
                first_outcome_ms = (time.perf_counter() - started_at) * 1_000
                continue

            raise ProbeProtocolError("unknown_event_type")
    finally:
        await events.aclose()

    decoder.finish()
    if not saw_started or terminal is None or first_outcome_ms is None:
        raise ProbeProtocolError("missing_terminal_event")

    pacing_ms = (pacer.total_wait_seconds - pacing_before) * 1_000
    provider_attempts = ledger.attempts_for(case.case_id)
    if provider_attempts != 1 + int(repaired):
        raise ProbeProtocolError("provider_attempt_count_mismatch")
    selected_decision: str | None
    if terminal == "declined":
        selected_decision = "abstain"
    elif terminal == "completed":
        selected_decision = "start_visual" if case.base_prefix == 0 else "continue_visual"
    else:
        selected_decision = None
    selected_component_id = (
        resolved_component_id if selected_decision == "continue_visual" else None
    )
    reused_base_component = resolved_component_id == "areas" if case.base_prefix > 0 else None
    role_tuple = tuple(roles)
    routing_expectation_met = _case_expectation(
        case,
        terminal=terminal,
        decision=selected_decision,
        stage=selected_stage,
        act=selected_act,
        reason_code=selected_reason_code,
        resolved_component_id=resolved_component_id,
        roles=role_tuple,
    )
    return {
        **_record_base(case),
        "terminal": terminal,
        "failureCode": failure_code,
        "providerAttempts": provider_attempts,
        "repaired": repaired,
        "firstAttemptValid": not repaired and terminal in {"completed", "declined"},
        "selectedDecision": selected_decision,
        "selectedStage": selected_stage,
        "selectedAct": selected_act,
        "selectedReasonCode": selected_reason_code,
        "selectedComponentId": selected_component_id,
        "resolvedComponentId": resolved_component_id,
        "reusedBaseComponent": reused_base_component,
        "missingRoles": list(role_tuple),
        "routingExpectationMet": routing_expectation_met,
        "certificateChainValid": True if terminal == "completed" else None,
        "obligationCodes": sorted(obligation_codes),
        "observedRoutedOutcomeMs": round(max(0.0, first_outcome_ms - pacing_ms), 3),
        "pacingWaitMs": round(pacing_ms, 3),
        "eventTypes": event_types,
    }


def _safe_progress(record: dict[str, object], completed: int, target: int) -> dict[str, object]:
    return {
        "progress": f"{completed}/{target}",
        "caseId": record["caseId"],
        "terminal": record["terminal"],
        "repaired": record["repaired"],
        "selectedDecision": record["selectedDecision"],
        "selectedStage": record["selectedStage"],
        "routingExpectationMet": record["routingExpectationMet"],
    }


async def _run_corpus(
    args: argparse.Namespace,
    cases: tuple[EvaluationCase, ...],
    *,
    max_cost_nano_usd: int,
) -> tuple[list[dict[str, object]], Any, DispatchPacer, str | None]:
    from murmur.core.config import config
    from murmur.live_scene.provider import scene_model_client_options
    from murmur.live_scene.service import SceneAuthoringService
    from murmur.llm.factory import create_llm_client

    provider = config.MURMUR_SCENE_LLM_PROVIDER.casefold()
    model = config.MURMUR_SCENE_LLM_MODEL
    if provider != "azure_openai" or "gpt-oss-120b" not in model.casefold():
        raise ProbeRefusal("live corpus requires the configured Azure gpt-oss-120b scene model")
    options = scene_model_client_options(provider, model)
    if options.get("transport_max_retries") != 0:
        raise ProbeRefusal("Azure SDK retries must be disabled before a paid corpus")

    logging.getLogger("murmur.llm.openai").setLevel(logging.CRITICAL)
    ledger = BudgetLedger(
        max_cost_nano_usd=max_cost_nano_usd,
        max_tokens=args.max_tokens,
        max_provider_attempts=len(cases) * 2,
    )
    pacer = DispatchPacer(args.request_start_interval_seconds)
    results: list[dict[str, object]] = []
    aborted_reason: str | None = None

    for generation, case in enumerate(cases, start=1):
        delegate = create_llm_client(provider, model=model, **options)
        client = BudgetedSceneModelClient(delegate, ledger, case.case_id)
        service = SceneAuthoringService(
            client=client,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
            before_provider_dispatch=pacer.admit,
        )
        try:
            record = await _run_case(
                case,
                generation=generation,
                service=service,
                ledger=ledger,
                pacer=pacer,
            )
        except ProbeProtocolError as exc:
            record = {
                **_record_base(case),
                "terminal": "protocol_error",
                "failureCode": exc.code,
                "providerAttempts": ledger.attempts_for(case.case_id),
                "repaired": False,
                "firstAttemptValid": False,
                "selectedDecision": None,
                "selectedStage": None,
                "selectedAct": None,
                "selectedReasonCode": None,
                "selectedComponentId": None,
                "resolvedComponentId": None,
                "reusedBaseComponent": None,
                "missingRoles": [],
                "routingExpectationMet": False,
                "certificateChainValid": False,
                "obligationCodes": [],
                "observedRoutedOutcomeMs": None,
                "pacingWaitMs": None,
                "eventTypes": [],
            }
            aborted_reason = exc.code
        finally:
            await client.aclose()

        results.append(record)
        print(
            json.dumps(_safe_progress(record, len(results), len(cases)), sort_keys=True),
            flush=True,
        )
        if aborted_reason is not None:
            break
        if len(results) == 1 and record["routingExpectationMet"] is not True:
            aborted_reason = "calibration_failed"
            break
        if record["failureCode"] in {
            "context_too_large",
            "provider_rate_limited",
            "provider_timeout",
            "provider_error",
            "semantic_integrity_error",
        }:
            aborted_reason = str(record["failureCode"])
            break

    return results, ledger, pacer, aborted_reason


def _metrics(
    results: list[dict[str, object]],
    *,
    target_count: int,
    aborted_reason: str | None,
) -> dict[str, object]:
    result_count = len(results)
    safe_terminal_count = sum(
        result["terminal"] in {"completed", "declined", "failed"} for result in results
    )
    completed_count = sum(result["terminal"] == "completed" for result in results)
    declined_count = sum(result["terminal"] == "declined" for result in results)
    repaired_count = sum(bool(result["repaired"]) for result in results)
    expectation_count = sum(result["routingExpectationMet"] is True for result in results)
    supported = [result for result in results if result["supportedByVocabulary"] is True]
    unsupported = [result for result in results if result["supportedByVocabulary"] is False]
    no_forward_progress = [
        result
        for result in results
        if result["supportedByVocabulary"] is True
        and result["expectedReasonCode"] == "no_forward_progress"
    ]
    resume = [result for result in results if result["category"] == "resume_prefix"]
    supported_expectation_count = sum(
        result["routingExpectationMet"] is True for result in supported
    )
    unsupported_expectation_count = sum(
        result["routingExpectationMet"] is True for result in unsupported
    )
    no_forward_progress_count = sum(
        result["routingExpectationMet"] is True for result in no_forward_progress
    )
    resume_reuse_count = sum(
        result["routingExpectationMet"] is True and result["reusedBaseComponent"] is True
        for result in resume
    )
    outcome_values = [
        float(result["observedRoutedOutcomeMs"])
        for result in results
        if result["observedRoutedOutcomeMs"] is not None
    ]
    cold_outcome_ms = outcome_values[0] if outcome_values else None
    warm_outcome_values = outcome_values[1:]
    warm_median_ms = statistics.median(warm_outcome_values) if warm_outcome_values else None
    warm_p95_ms = _support._percentile(warm_outcome_values, 0.95)
    decisions = Counter(
        str(result["selectedDecision"])
        for result in results
        if result["selectedDecision"] is not None
    )
    stages = Counter(
        str(result["selectedStage"]) for result in results if result["selectedStage"] is not None
    )
    complete_sample = result_count == target_count and aborted_reason is None
    protocol_pass = complete_sample and safe_terminal_count == target_count
    supported_rate = supported_expectation_count / len(supported) if supported else None
    supported_pass = bool(
        len(supported) == 8 and supported_rate is not None and supported_rate >= 0.9
    )
    unsupported_pass = len(unsupported) == 2 and unsupported_expectation_count == 2
    no_forward_progress_pass = len(no_forward_progress) == 1 and no_forward_progress_count == 1
    resume_pass = len(resume) == 2 and resume_reuse_count == 2
    latency_pass = bool(
        warm_median_ms is not None
        and warm_median_ms <= WARM_MEDIAN_MAX_MS
        and warm_p95_ms is not None
        and warm_p95_ms <= WARM_P95_MAX_MS
    )
    qualification_passed = bool(
        target_count == MAX_CASES
        and protocol_pass
        and supported_pass
        and unsupported_pass
        and no_forward_progress_pass
        and resume_pass
        and latency_pass
    )
    return {
        "targetCaseCount": target_count,
        "executedCaseCount": result_count,
        "safeTerminalCount": safe_terminal_count,
        "completedCount": completed_count,
        "declinedCount": declined_count,
        "repairCount": repaired_count,
        "providerAttemptCount": sum(int(result["providerAttempts"]) for result in results),
        "firstAttemptValidityRate": round(
            sum(bool(result["firstAttemptValid"]) for result in results) / result_count,
            4,
        )
        if result_count
        else None,
        "routingExpectationRate": round(expectation_count / result_count, 4)
        if result_count
        else None,
        "supportedExpectationNumerator": supported_expectation_count,
        "supportedExpectationDenominator": len(supported),
        "supportedExpectationRate": round(supported_rate, 4)
        if supported_rate is not None
        else None,
        "unsupportedIntentAbstentionNumerator": unsupported_expectation_count,
        "unsupportedIntentAbstentionDenominator": len(unsupported),
        "unsupportedIntentAbstentionRate": round(
            unsupported_expectation_count / len(unsupported),
            4,
        )
        if unsupported
        else None,
        "noForwardProgressNumerator": no_forward_progress_count,
        "noForwardProgressDenominator": len(no_forward_progress),
        "noForwardProgressRate": round(
            no_forward_progress_count / len(no_forward_progress),
            4,
        )
        if no_forward_progress
        else None,
        "resumeReuseNumerator": resume_reuse_count,
        "resumeReuseDenominator": len(resume),
        "resumeReuseRate": round(
            resume_reuse_count / len(resume),
            4,
        )
        if resume
        else None,
        "coldRoutedOutcomeMs": round(cold_outcome_ms, 3) if cold_outcome_ms is not None else None,
        "warmMedianRoutedOutcomeMs": round(warm_median_ms, 3)
        if warm_median_ms is not None
        else None,
        "warmP95RoutedOutcomeMs": round(warm_p95_ms, 3) if warm_p95_ms is not None else None,
        "pacingWaitMs": round(
            sum(float(result["pacingWaitMs"] or 0.0) for result in results),
            3,
        ),
        "selectedDecisions": dict(sorted(decisions.items())),
        "selectedStages": dict(sorted(stages.items())),
        "protocolPassed": protocol_pass,
        "supportedExpectationPassed": supported_pass,
        "unsupportedIntentAbstentionPassed": unsupported_pass,
        "noForwardProgressPassed": no_forward_progress_pass,
        "resumeReusePassed": resume_pass,
        "latencyThresholdsPassed": latency_pass,
        "routerQualificationPassed": qualification_passed,
        "abortedReason": aborted_reason,
    }


def _safe_output_path(raw_path: str | None) -> Path:
    return _support._safe_output_path(raw_path)


def _write_report(path: Path, payload: dict[str, object]) -> None:
    _support._write_report(path, payload)


def _clean_source_snapshot() -> dict[str, object]:
    state = _support._git_state()
    commit = state.get("sourceCommit")
    if not isinstance(commit, str) or not commit.strip() or state.get("sourceDirty") is not False:
        raise ProbeRefusal("live run requires a clean source with a resolved git commit")
    return state


def _source_state_stable(
    started: dict[str, object],
    finished: dict[str, object],
) -> bool:
    return finished == started and finished.get("sourceDirty") is False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost-usd", required=True)
    parser.add_argument("--case-limit", type=int, default=MAX_CASES)
    parser.add_argument("--max-tokens", type=int, default=MAX_OUTPUT_TOKENS)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--request-start-interval-seconds",
        type=float,
        default=DEFAULT_REQUEST_START_INTERVAL_SECONDS,
    )
    parser.add_argument("--output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--acknowledge-paid-provider")
    return parser


def _validate_arguments(args: argparse.Namespace) -> int:
    max_cost_nano_usd = _support._parse_budget_nano_usd(args.max_cost_usd)
    if not 1 <= args.case_limit <= MAX_CASES:
        raise ProbeRefusal(f"--case-limit must be between 1 and {MAX_CASES}")
    if args.max_tokens != MAX_OUTPUT_TOKENS:
        raise ProbeRefusal(f"--max-tokens must equal the audited ceiling {MAX_OUTPUT_TOKENS}")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ProbeRefusal("--timeout-seconds must be finite and greater than zero")
    if (
        not math.isfinite(args.request_start_interval_seconds)
        or args.request_start_interval_seconds < MIN_REQUEST_START_INTERVAL_SECONDS
    ):
        raise ProbeRefusal(
            "--request-start-interval-seconds must preserve the 10 requests/minute quota"
        )
    if date.today() > PRICING_REVIEW_AFTER:
        raise ProbeRefusal("pinned Azure pricing snapshot requires review")
    if not args.dry_run and args.acknowledge_paid_provider != ACKNOWLEDGEMENT:
        raise ProbeRefusal("live run refused: pass --acknowledge-paid-provider " + ACKNOWLEDGEMENT)
    return max_cost_nano_usd


def main() -> int:
    args = _parser().parse_args()
    try:
        max_cost_nano_usd = _validate_arguments(args)
        cases = CASES[: args.case_limit]
        preflight = _preflight_budget(
            cases,
            max_cost_nano_usd=max_cost_nano_usd,
            max_tokens=args.max_tokens,
        )
        preflight_summary = {
            "mode": "dry-run" if args.dry_run else "live",
            "evidenceScope": EVIDENCE_SCOPE,
            "caseCount": len(cases),
            "corpusSha256": _corpus_sha256(cases),
            "pricingProfile": PRICING_PROFILE,
            "maxCostUsd": _format_nano_usd(max_cost_nano_usd),
            "reservedMaxProviderAttempts": len(preflight.reservations),
            "reservedMaxInputTokens": sum(
                reservation.max_input_tokens for reservation in preflight.reservations
            ),
            "reservedMaxOutputTokens": sum(
                reservation.max_output_tokens for reservation in preflight.reservations
            ),
            "reservedMaxCostUsd": _format_nano_usd(preflight.reserved_cost_nano_usd),
        }
        print(json.dumps(preflight_summary, sort_keys=True), flush=True)
        if args.dry_run:
            return 0

        source_state = _clean_source_snapshot()
        output_path = _safe_output_path(args.output)
        results, dispatched, pacer, aborted_reason = asyncio.run(
            _run_corpus(
                args,
                cases,
                max_cost_nano_usd=max_cost_nano_usd,
            )
        )
        source_state_stable = _source_state_stable(source_state, _support._git_state())
        metrics = _metrics(
            results,
            target_count=len(cases),
            aborted_reason=aborted_reason,
        )
        reservation_count = len(dispatched.reservations)
        reported_attempt_count = metrics["providerAttemptCount"]
        all_dispatches_paced = pacer.admission_count == reservation_count == reported_attempt_count
        gate_qualification_passed = bool(
            metrics["routerQualificationPassed"] and source_state_stable and all_dispatches_paced
        )
        report = {
            "schemaVersion": 2,
            "generatedAt": datetime.now(UTC).isoformat(),
            **source_state,
            "sourceStateStable": source_state_stable,
            "evidenceScope": EVIDENCE_SCOPE,
            "corpusSha256": _corpus_sha256(cases),
            "pricing": {
                "profile": PRICING_PROFILE,
                "effectiveDate": PRICING_EFFECTIVE_DATE,
                "reviewAfter": PRICING_REVIEW_AFTER.isoformat(),
                "source": PRICING_SOURCE,
                "inputUsdPerMillionTokens": "0.15",
                "outputUsdPerMillionTokens": "0.60",
            },
            "limits": {
                "maxCostUsd": _format_nano_usd(max_cost_nano_usd),
                "maxOutputTokensPerAttempt": args.max_tokens,
                "maxProviderAttempts": len(cases) * 2,
                "requestStartIntervalSeconds": args.request_start_interval_seconds,
                "sdkMaxRetries": 0,
                "warmMedianMaxMs": WARM_MEDIAN_MAX_MS,
                "warmP95MaxMs": WARM_P95_MAX_MS,
            },
            "preflightWorstCase": preflight_summary,
            "dispatchedReservations": [
                reservation.sanitized() for reservation in dispatched.reservations
            ],
            "dispatchedReservedMaxCostUsd": _format_nano_usd(dispatched.reserved_cost_nano_usd),
            "dispatchPacing": {
                "admissionCount": pacer.admission_count,
                "reservationCount": reservation_count,
                "reportedProviderAttemptCount": reported_attempt_count,
                "allDispatchesPaced": all_dispatches_paced,
            },
            "results": results,
            "metrics": metrics,
            "gateQualificationPassed": gate_qualification_passed,
            "costEvidence": "reserved_upper_bound_not_actual_billed_usage",
        }
        _write_report(output_path, report)
        final_summary = {
            "mode": "live",
            "executedCaseCount": metrics["executedCaseCount"],
            "providerAttemptCount": metrics["providerAttemptCount"],
            "routerQualificationPassed": metrics["routerQualificationPassed"],
            "gateQualificationPassed": gate_qualification_passed,
            "sourceStateStable": source_state_stable,
            "allDispatchesPaced": all_dispatches_paced,
            "dispatchedReservedMaxCostUsd": report["dispatchedReservedMaxCostUsd"],
            "output": str(output_path.relative_to(PROJECT_ROOT)),
        }
        print(json.dumps(final_summary, sort_keys=True), flush=True)
        if aborted_reason is not None:
            return 1
        if not gate_qualification_passed:
            return 1
        return 0
    except ProbeRefusal as exc:
        print(f"Visual-act router probe refused: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("Visual-act router probe failed: unexpected_local_failure", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
