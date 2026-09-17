"""Offline safety and scoring contract for the paid Gate 1.8 model probe."""

from __future__ import annotations

import dataclasses
import json
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manual" / "probe_semantic_storyboard.py"
PROBE = runpy.run_path(str(SCRIPT))

EXPECTED_CASE_IDS = (
    "lower_first_equal",
    "higher_first_equal",
    "formula_complement_equal",
    "paths_higher_then_lower",
    "lower_path_only",
    "higher_path_only",
    "height_then_flight",
    "apex_from_paths",
    "formula_unequal_30_45",
    "paths_unequal_30_45",
    "height_flight_45_60",
    "followup_math_from_paths",
    "followup_apex_from_paths",
    "followup_high_path_from_math",
    "unsupported_wind",
    "unsupported_launch_height",
    "unsupported_angles",
    "unsupported_svg_injection",
    "ambiguous_better",
    "already_present_lower",
)
MANDATORY_CASE_IDS = frozenset(
    {
        "lower_first_equal",
        "higher_first_equal",
        "followup_math_from_paths",
        "followup_apex_from_paths",
    }
)
NEGATIVE_CASE_IDS = frozenset(EXPECTED_CASE_IDS[-6:])
SAFE_HASH = "a" * 64
CHANGED_HASH = "b" * 64
AZURE_SUBSCRIPTION_ONE = "11111111-1111-4111-8111-111111111111"
AZURE_SUBSCRIPTION_TWO = "22222222-2222-4222-8222-222222222222"
AZURE_ENDPOINT = "https://storyboard-prod.openai.azure.com"
AZURE_SUBDOMAIN = "storyboard-prod"
AZURE_RESOURCE_GROUP = "private-storyboard-rg"
AZURE_ACCOUNT_NAME = "resource-name-differs-from-custom-domain"
AZURE_ACCOUNT_ID = (
    f"/subscriptions/{AZURE_SUBSCRIPTION_TWO}/resourceGroups/{AZURE_RESOURCE_GROUP}"
    f"/providers/Microsoft.CognitiveServices/accounts/{AZURE_ACCOUNT_NAME}"
)
AZURE_DEPLOYMENT_ID = f"{AZURE_ACCOUNT_ID}/deployments/{PROBE['EXPECTED_AZURE_DEPLOYMENT']}"
AZURE_DEPLOYMENT_ETAG = '"paid-probe-etag"'
AZURE_VERSION_UPGRADE_OPTION = "NoAutoUpgrade"
_MISSING = object()


def _azure_account(
    subscription_id: str,
    *,
    subdomain: str = AZURE_SUBDOMAIN,
    account_name: str = AZURE_ACCOUNT_NAME,
    account_id: str | None = None,
    account_type: str = "Microsoft.CognitiveServices/accounts",
    state: str = "Succeeded",
) -> dict[str, object]:
    resolved_id = account_id or (
        f"/subscriptions/{subscription_id}/resourceGroups/{AZURE_RESOURCE_GROUP}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
    )
    return {
        "id": resolved_id,
        "name": account_name,
        "type": account_type,
        "state": state,
        "subdomain": subdomain,
    }


def _azure_deployment(
    *,
    account_id: str = AZURE_ACCOUNT_ID,
    deployment_id: str | None = None,
    deployment_name: str = "murmur-gpt-oss-120b",
    deployment_type: str = "Microsoft.CognitiveServices/accounts/deployments",
    model_format: str = "OpenAI-OSS",
    model_name: str = "gpt-oss-120b",
    model_version: object = "1",
    sku_name: str = "GlobalStandard",
    state: str = "Succeeded",
    etag: object = AZURE_DEPLOYMENT_ETAG,
    version_upgrade_option: object = AZURE_VERSION_UPGRADE_OPTION,
) -> dict[str, object]:
    model: dict[str, object] = {"format": model_format, "name": model_name}
    if model_version is not _MISSING:
        model["version"] = model_version
    deployment: dict[str, object] = {
        "id": deployment_id or f"{account_id}/deployments/{PROBE['EXPECTED_AZURE_DEPLOYMENT']}",
        "name": deployment_name,
        "type": deployment_type,
        "sku": {"name": sku_name},
        "model": model,
        "state": state,
    }
    if etag is not _MISSING:
        deployment["etag"] = etag
    if version_upgrade_option is not _MISSING:
        deployment["versionUpgradeOption"] = version_upgrade_option
    return deployment


def _install_azure_inventory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subscriptions: list[object] | object | None = None,
    accounts_by_subscription: dict[str, object] | None = None,
    deployment: object | None = None,
    fail_account_subscription: str | None = None,
) -> list[tuple[str, ...]]:
    if subscriptions is None:
        subscriptions = [AZURE_SUBSCRIPTION_ONE, AZURE_SUBSCRIPTION_TWO]
    if accounts_by_subscription is None:
        accounts_by_subscription = {
            AZURE_SUBSCRIPTION_ONE: [
                _azure_account(
                    AZURE_SUBSCRIPTION_ONE,
                    subdomain="unrelated-account",
                    account_name="unrelated-resource",
                )
            ],
            AZURE_SUBSCRIPTION_TWO: [_azure_account(AZURE_SUBSCRIPTION_TWO)],
        }
    if deployment is None:
        deployment = _azure_deployment()
    calls: list[tuple[str, ...]] = []

    def fake_azure_cli_json(*args: str) -> object:
        calls.append(args)
        if args[:2] == ("account", "list"):
            return subscriptions
        if args[:4] == ("cognitiveservices", "account", "deployment", "show"):
            return deployment
        if args[:3] == ("cognitiveservices", "account", "list"):
            subscription_id = args[args.index("--subscription") + 1]
            if subscription_id == fail_account_subscription:
                raise PROBE["ProbeRefusal"]("azure_cli_command_failed")
            return accounts_by_subscription.get(subscription_id, [])
        raise AssertionError(f"unexpected Azure CLI call: {args!r}")

    monkeypatch.setitem(
        PROBE["_attest_azure_deployment"].__globals__,
        "_azure_cli_json",
        fake_azure_cli_json,
    )
    return calls


def _valid_attestation(endpoint: str = AZURE_ENDPOINT) -> Any:
    host = endpoint.removeprefix("https://").rstrip("/")
    return PROBE["AzureDeploymentAttestation"](
        deployment_name=PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        model_format=PROBE["EXPECTED_AZURE_MODEL_FORMAT"],
        model_name=PROBE["EXPECTED_AZURE_MODEL_NAME"],
        model_version=PROBE["EXPECTED_AZURE_MODEL_VERSION"],
        sku_name=PROBE["EXPECTED_AZURE_SKU"],
        provisioning_state=PROBE["EXPECTED_AZURE_PROVISIONING_STATE"],
        version_upgrade_option=AZURE_VERSION_UPGRADE_OPTION,
        enabled_subscription_count=2,
        endpoint_host_sha256=PROBE["_sha256_text"](host),
        account_resource_id_sha256=PROBE["_sha256_text"](AZURE_ACCOUNT_ID),
        deployment_resource_id_sha256=PROBE["_sha256_text"](AZURE_DEPLOYMENT_ID),
        deployment_etag_sha256=PROBE["_sha256_text"](AZURE_DEPLOYMENT_ETAG),
    )


def _record(act: str, target: str, *evidence_ids: str) -> Any:
    return PROBE["RecordSpec"](act, target, tuple(evidence_ids))


LOWER = _record("trace", "lower_angle")
HIGHER = _record("trace", "higher_angle")
RANGE_FORMULA = _record("reveal", "range_formula")
COMPLEMENTARY = _record("reveal", "complementary_angles")
EQUAL_PATHS = _record(
    "relate",
    "equal_range",
    "lower_trajectory",
    "higher_trajectory",
)
EQUAL_MATH = _record(
    "relate",
    "equal_range",
    "range_formula",
    "complementary_angles",
)
UNEQUAL_PATHS = _record(
    "relate",
    "unequal_range",
    "lower_trajectory",
    "higher_trajectory",
)
UNEQUAL_FORMULA = _record("relate", "unequal_range", "range_formula")
HIGHER_APEX = _record(
    "relate",
    "higher_apex",
    "lower_trajectory",
    "higher_trajectory",
)
LONGER_FLIGHT = _record(
    "relate",
    "longer_flight",
    "lower_trajectory",
    "higher_trajectory",
)


def _case(case_id: str) -> Any:
    return next(case for case in PROBE["CASES"] if case.case_id == case_id)


def _scheduled(round_index: int, case_id: str) -> Any:
    return next(
        scheduled
        for scheduled in PROBE["SCHEDULE"]
        if scheduled.round_index == round_index and scheduled.case.case_id == case_id
    )


def _observation(
    scheduled: Any,
    *,
    records: tuple[Any, ...] | None = None,
    terminal: str | None = None,
    abstain_reason: str | None = None,
    first_attempt_valid: bool = True,
    provider_call_count: int = 1,
    mutate: bool | None = None,
) -> Any:
    case = scheduled.case
    accepted = case.rubric.required_records if records is None else records
    is_abstention = case.rubric.abstain_reason is not None
    if terminal is None:
        terminal = "declined" if is_abstention else "model_stop"
    if abstain_reason is None and is_abstention:
        abstain_reason = case.rubric.abstain_reason
    if mutate is None:
        mutate = not is_abstention and bool(accepted)
    return PROBE["CaseObservation"](
        case_id=case.case_id,
        round_index=scheduled.round_index,
        terminal=terminal,
        accepted_records=tuple(accepted),
        decline_reason=abstain_reason,
        failure_code=None,
        checkpoint_count=len(accepted),
        base_scene_sha256=SAFE_HASH,
        result_scene_sha256=CHANGED_HASH if mutate else SAFE_HASH,
        base_semantic_sha256=SAFE_HASH,
        result_semantic_sha256=CHANGED_HASH if mutate else SAFE_HASH,
        program_sha256=SAFE_HASH,
        first_attempt_valid=first_attempt_valid,
        provider_call_count=provider_call_count,
        certificate_chain_valid=True,
        first_checkpoint_ms=None,
        total_ms=1.0,
    )


def _passing_scores() -> list[Any]:
    return [
        PROBE["_score_case"](scheduled.case, _observation(scheduled))
        for scheduled in PROBE["SCHEDULE"]
    ]


def test_corpus_and_two_round_schedule_are_exact_unique_and_pinned() -> None:
    cases = PROBE["CASES"]
    schedule = PROBE["SCHEDULE"]

    assert tuple(case.case_id for case in cases) == EXPECTED_CASE_IDS
    assert len({case.prompt for case in cases}) == 20
    assert all(
        case.prompt.strip() == case.prompt and 8 <= len(case.prompt) <= 2_000 for case in cases
    )
    assert [scheduled.round_index for scheduled in schedule] == [1] * 20 + [2] * 20
    assert [scheduled.ordinal for scheduled in schedule] == list(range(1, 41))
    assert [scheduled.case.case_id for scheduled in schedule[:20]] == list(EXPECTED_CASE_IDS)
    assert [scheduled.case.case_id for scheduled in schedule[20:]] == list(EXPECTED_CASE_IDS)
    assert [scheduled.reservation_id for scheduled in schedule] == [
        f"r{round_index}:{case_id}" for round_index in (1, 2) for case_id in EXPECTED_CASE_IDS
    ]
    assert len({scheduled.reservation_id for scheduled in schedule}) == 40
    assert PROBE["CORPUS_SHA256"] == (
        "71d94c67c01498bab9a4d931f2d33bf478290890b36f6d1a88c2ee3e5d994443"
    )
    assert PROBE["_corpus_sha256"](cases) == PROBE["CORPUS_SHA256"]


def test_corpus_pins_problem_prefix_and_deterministic_rubric_semantics() -> None:
    cases = {case.case_id: case for case in PROBE["CASES"]}

    assert cases["lower_first_equal"].rubric.required_records == (LOWER, HIGHER, EQUAL_PATHS)
    assert cases["lower_first_equal"].rubric.precedence == (
        (LOWER.effect_key, HIGHER.effect_key),
        (HIGHER.effect_key, EQUAL_PATHS.effect_key),
    )
    assert cases["higher_first_equal"].rubric.required_records == (HIGHER, LOWER, EQUAL_PATHS)
    assert cases["formula_complement_equal"].rubric.required_records == (
        RANGE_FORMULA,
        COMPLEMENTARY,
        EQUAL_MATH,
    )
    assert cases["height_then_flight"].rubric.required_records == (
        HIGHER,
        LOWER,
        HIGHER_APEX,
        LONGER_FLIGHT,
    )
    assert cases["formula_unequal_30_45"].problem_spec.angles_deg == (30, 45)
    assert cases["paths_unequal_30_45"].rubric.required_records == (
        HIGHER,
        LOWER,
        UNEQUAL_PATHS,
    )
    assert cases["height_flight_45_60"].problem_spec.angles_deg == (45, 60)

    assert cases["followup_math_from_paths"].base_records == (LOWER, HIGHER)
    assert cases["followup_math_from_paths"].rubric.required_records == (
        RANGE_FORMULA,
        COMPLEMENTARY,
        EQUAL_MATH,
    )
    assert cases["followup_apex_from_paths"].base_records == (LOWER, HIGHER)
    assert cases["followup_apex_from_paths"].rubric.required_records == (HIGHER_APEX,)
    assert cases["followup_high_path_from_math"].base_records == (
        RANGE_FORMULA,
        COMPLEMENTARY,
        EQUAL_MATH,
    )
    assert cases["followup_high_path_from_math"].rubric.required_records == (HIGHER,)
    assert cases["already_present_lower"].base_records == (LOWER,)

    assert {case_id: cases[case_id].rubric.abstain_reason for case_id in NEGATIVE_CASE_IDS} == {
        "unsupported_wind": "unsupported_physics",
        "unsupported_launch_height": "unsupported_initial_condition",
        "unsupported_angles": "unsupported_problem",
        "unsupported_svg_injection": "unsupported_intent",
        "ambiguous_better": "ambiguous_intent",
        "already_present_lower": "already_present",
    }
    assert all(cases[case_id].rubric.required_records == () for case_id in NEGATIVE_CASE_IDS)


def test_every_positive_case_forbids_all_unrequested_effects() -> None:
    all_effects = frozenset(PROBE["ALL_EFFECTS"])
    for case in PROBE["CASES"]:
        if case.rubric.abstain_reason is not None:
            continue
        requested = {record.effect_key for record in case.rubric.required_records}
        assert set(case.rubric.forbidden_effects) == all_effects - requested


def test_every_case_builds_a_certified_prefix_and_unique_director_messages() -> None:
    from murmur.live_scene.semantic_storyboard_verifier import (
        verify_semantic_storyboard_frontier,
    )

    message_payloads: set[str] = set()
    for case in PROBE["CASES"]:
        scene, semantic_scene = PROBE["_certified_frontier"](case)
        assert scene.revision == 1 + len(case.base_records)
        assert semantic_scene.revision == scene.revision
        verify_semantic_storyboard_frontier(
            PROBE["_pydantic_problem"](case.problem_spec),
            scene,
            semantic_scene,
        )

        messages = PROBE["_messages_for_case"](case)
        assert [message["role"] for message in messages] == ["system", "user"]
        assert (
            json.dumps(case.prompt, ensure_ascii=False, separators=(",", ":"))
            in messages[-1]["content"]
        )
        message_payloads.add(json.dumps(messages, separators=(",", ":"), sort_keys=True))

    assert len(message_payloads) == 20


def test_full_schedule_is_pre_reserved_as_one_atomic_plan() -> None:
    assert PROBE["MAX_PROVIDER_CALLS"] == 40
    ledger = PROBE["_preflight_budget"](
        PROBE["SCHEDULE"],
        max_cost_nano_usd=500_000_000,
        max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
    )

    assert len(ledger.reservations) == 40
    assert len({item.reservation_id for item in ledger.reservations}) == 40
    assert ledger.admitted_count == 0


def test_one_nano_shortfall_refuses_the_whole_plan_before_any_admission() -> None:
    dry_run = PROBE["_preflight_budget"](
        PROBE["SCHEDULE"],
        max_cost_nano_usd=500_000_000,
        max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
    )
    with pytest.raises(PROBE["ProbeRefusal"], match="cost ceiling"):
        PROBE["_preflight_budget"](
            PROBE["SCHEDULE"],
            max_cost_nano_usd=dry_run.reserved_cost_nano_usd - 1,
            max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
        )


def test_budget_parser_rejects_even_a_sub_nano_amount_above_fifty_cents() -> None:
    assert PROBE["_parse_budget_nano_usd"]("0.50") == 500_000_000
    with pytest.raises(PROBE["ProbeRefusal"], match=r"must not exceed USD 0\.50"):
        PROBE["_parse_budget_nano_usd"]("0.5000000001")


def test_live_entrypoint_never_constructs_provider_when_preflight_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    globals_ = PROBE["main"].__globals__
    provider_constructions = 0

    def refuse_preflight(**_kwargs: object) -> object:
        raise PROBE["ProbeRefusal"]("full paid corpus exceeds the approved provider cost ceiling")

    def provider_factory() -> object:
        nonlocal provider_constructions
        provider_constructions += 1
        return object()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--max-cost-usd",
            "0.50",
            "--acknowledge-paid-provider",
            PROBE["ACKNOWLEDGEMENT"],
        ],
    )
    monkeypatch.setitem(globals_, "_load_env_file", lambda _path: None)
    monkeypatch.setitem(globals_, "_validate_args", lambda _args: 500_000_000)
    monkeypatch.setitem(globals_, "_preflight_budget", refuse_preflight)
    monkeypatch.setitem(globals_, "_provider_factory", provider_factory)

    assert PROBE["main"]() == 2
    assert provider_constructions == 0


def test_provider_factory_captures_the_attested_target_and_zero_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from murmur.core.config import config
    from murmur.llm import openai as openai_module

    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_PROVIDER", "azure_openai")
    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_MODEL", "murmur-gpt-oss-120b")
    monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "murmur-gpt-oss-120b")
    monkeypatch.setattr(config, "AZURE_OPENAI_API_KEY", "CAPTURED_INITIAL_API_KEY")
    monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", AZURE_ENDPOINT)
    captured: dict[str, object] = {}
    constructed = object()

    def fake_client(**options: object) -> object:
        captured.update(options)
        return constructed

    monkeypatch.setattr(openai_module, "OpenAIClient", fake_client)
    client_factory = PROBE["_provider_factory"](_valid_attestation())

    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_PROVIDER", "changed-provider")
    monkeypatch.setattr(config, "MURMUR_SCENE_LLM_MODEL", "changed-model")
    monkeypatch.setattr(config, "AZURE_OPENAI_DEPLOYMENT", "changed-deployment")
    monkeypatch.setattr(config, "AZURE_OPENAI_API_KEY", "MUTATED_API_KEY")
    monkeypatch.setattr(
        config,
        "AZURE_OPENAI_ENDPOINT",
        "https://mutated-resource.openai.azure.com",
    )

    assert client_factory() is constructed
    assert captured["api_key"] == "CAPTURED_INITIAL_API_KEY"
    assert captured["model"] == "murmur-gpt-oss-120b"
    assert captured["base_url"] == f"{AZURE_ENDPOINT}/openai/v1/"
    assert captured["max_tokens_parameter"] == "max_completion_tokens"
    assert captured["transport_max_retries"] == 0
    assert captured["reasoning_effort"] == "low"
    assert "MUTATED_API_KEY" not in captured.values()
    assert not any("mutated-resource" in str(value) for value in captured.values())


def test_azure_attestation_finds_a_custom_subdomain_in_the_second_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_azure_inventory(monkeypatch)

    attestation = PROBE["_attest_azure_deployment"](
        AZURE_ENDPOINT,
        PROBE["EXPECTED_AZURE_DEPLOYMENT"],
    )

    assert attestation == _valid_attestation()
    assert AZURE_ACCOUNT_NAME != AZURE_SUBDOMAIN
    account_calls = [call for call in calls if call[:3] == ("cognitiveservices", "account", "list")]
    deployment_call = next(
        call for call in calls if call[:4] == ("cognitiveservices", "account", "deployment", "show")
    )
    assert len(account_calls) == 2
    assert {call[call.index("--subscription") + 1] for call in account_calls} == {
        AZURE_SUBSCRIPTION_ONE,
        AZURE_SUBSCRIPTION_TWO,
    }
    assert deployment_call[deployment_call.index("--subscription") + 1] == AZURE_SUBSCRIPTION_TWO
    assert deployment_call[deployment_call.index("--resource-group") + 1] == AZURE_RESOURCE_GROUP
    assert deployment_call[deployment_call.index("--name") + 1] == AZURE_ACCOUNT_NAME


@pytest.mark.parametrize(
    "inventory",
    [
        [],
        {},
        ["not-a-subscription-uuid"],
        [AZURE_SUBSCRIPTION_ONE, AZURE_SUBSCRIPTION_ONE],
        [AZURE_SUBSCRIPTION_ONE] * (PROBE["MAX_ENABLED_AZURE_SUBSCRIPTIONS"] + 1),
    ],
    ids=("empty", "not-list", "malformed-id", "duplicate", "oversized"),
)
def test_azure_attestation_rejects_invalid_subscription_inventory(
    inventory: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_azure_inventory(monkeypatch, subscriptions=inventory)

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_subscription_inventory_invalid"):
        PROBE["_attest_azure_deployment"](
            AZURE_ENDPOINT,
            PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        )


def test_azure_attestation_rejects_any_subscription_account_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_azure_inventory(
        monkeypatch,
        fail_account_subscription=AZURE_SUBSCRIPTION_ONE,
    )

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_cli_command_failed"):
        PROBE["_attest_azure_deployment"](
            AZURE_ENDPOINT,
            PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        )


@pytest.mark.parametrize("match_count", [0, 2], ids=("missing", "ambiguous"))
def test_azure_attestation_requires_exactly_one_endpoint_account_binding(
    match_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = {
        AZURE_SUBSCRIPTION_ONE: [
            _azure_account(
                AZURE_SUBSCRIPTION_ONE,
                subdomain=AZURE_SUBDOMAIN if match_count == 2 else "unrelated-one",
                account_name="first-resource",
            )
        ],
        AZURE_SUBSCRIPTION_TWO: [
            _azure_account(
                AZURE_SUBSCRIPTION_TWO,
                subdomain=AZURE_SUBDOMAIN if match_count == 2 else "unrelated-two",
            )
        ],
    }
    _install_azure_inventory(monkeypatch, accounts_by_subscription=accounts)

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_account_binding_not_unique"):
        PROBE["_attest_azure_deployment"](
            AZURE_ENDPOINT,
            PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        )


@pytest.mark.parametrize(
    ("case", "account"),
    [
        (
            "account-state",
            _azure_account(AZURE_SUBSCRIPTION_TWO, state="Failed"),
        ),
        (
            "account-type",
            _azure_account(AZURE_SUBSCRIPTION_TWO, account_type="Microsoft.Foo/accounts"),
        ),
        (
            "cross-subscription-arm-id",
            _azure_account(
                AZURE_SUBSCRIPTION_TWO,
                account_id=(
                    f"/subscriptions/{AZURE_SUBSCRIPTION_ONE}/resourceGroups/"
                    f"{AZURE_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/"
                    f"accounts/{AZURE_ACCOUNT_NAME}"
                ),
            ),
        ),
        (
            "malformed-arm-id",
            _azure_account(AZURE_SUBSCRIPTION_TWO, account_id="not-an-arm-resource-id"),
        ),
        (
            "arm-account-name-mismatch",
            _azure_account(
                AZURE_SUBSCRIPTION_TWO,
                account_id=(
                    f"/subscriptions/{AZURE_SUBSCRIPTION_TWO}/resourceGroups/"
                    f"{AZURE_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/"
                    "accounts/different-resource"
                ),
            ),
        ),
    ],
)
def test_azure_attestation_rejects_invalid_account_identity_or_state(
    case: str,
    account: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case
    accounts = {
        AZURE_SUBSCRIPTION_ONE: [],
        AZURE_SUBSCRIPTION_TWO: [account],
    }
    _install_azure_inventory(monkeypatch, accounts_by_subscription=accounts)

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_account_binding_invalid"):
        PROBE["_attest_azure_deployment"](
            AZURE_ENDPOINT,
            PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        )


@pytest.mark.parametrize(
    ("case", "deployment"),
    [
        ("wrong-alias", _azure_deployment(deployment_name="wrong-alias")),
        (
            "wrong-parent",
            _azure_deployment(
                deployment_id=(
                    f"/subscriptions/{AZURE_SUBSCRIPTION_TWO}/resourceGroups/"
                    f"{AZURE_RESOURCE_GROUP}/providers/Microsoft.CognitiveServices/"
                    "accounts/other/deployments/murmur-gpt-oss-120b"
                )
            ),
        ),
        ("wrong-type", _azure_deployment(deployment_type="Microsoft.Foo/deployments")),
        ("wrong-model-format", _azure_deployment(model_format="OpenAI")),
        ("wrong-model-name", _azure_deployment(model_name="gpt-oss-20b")),
        ("wrong-model-version", _azure_deployment(model_version="4")),
        ("integer-model-version", _azure_deployment(model_version=1)),
        ("missing-model-version", _azure_deployment(model_version=_MISSING)),
        ("wrong-sku", _azure_deployment(sku_name="Standard")),
        ("not-ready", _azure_deployment(state="Creating")),
        ("missing-etag", _azure_deployment(etag=_MISSING)),
        ("empty-etag", _azure_deployment(etag="")),
        ("non-string-etag", _azure_deployment(etag=42)),
        (
            "missing-version-upgrade-option",
            _azure_deployment(version_upgrade_option=_MISSING),
        ),
        (
            "unknown-version-upgrade-option",
            _azure_deployment(version_upgrade_option="AutoUpgradeImmediately"),
        ),
        ("non-string-version-upgrade-option", _azure_deployment(version_upgrade_option=42)),
        ("not-an-object", []),
    ],
)
def test_azure_attestation_rejects_every_deployment_metadata_mismatch(
    case: str,
    deployment: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del case
    _install_azure_inventory(monkeypatch, deployment=deployment)

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_deployment_attestation_mismatch"):
        PROBE["_attest_azure_deployment"](
            AZURE_ENDPOINT,
            PROBE["EXPECTED_AZURE_DEPLOYMENT"],
        )


def test_azure_cli_refuses_when_executable_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    globals_ = PROBE["_azure_cli_json"].__globals__
    monkeypatch.setattr(globals_["shutil"], "which", lambda _name: None)
    run_calls = 0

    def unexpected_run(*_args: object, **_kwargs: object) -> object:
        nonlocal run_calls
        run_calls += 1
        raise AssertionError("subprocess must not run without Azure CLI")

    monkeypatch.setattr(globals_["subprocess"], "run", unexpected_run)

    with pytest.raises(PROBE["ProbeRefusal"], match="azure_cli_unavailable"):
        PROBE["_azure_cli_json"]("account", "list")
    assert run_calls == 0


@pytest.mark.parametrize(
    "failure",
    ["nonzero", "malformed", "oversized", "empty", "timeout"],
)
def test_azure_cli_refuses_noncanonical_or_failed_responses_without_leaking_stderr(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    globals_ = PROBE["_azure_cli_json"].__globals__
    monkeypatch.setattr(globals_["shutil"], "which", lambda _name: "/safe/bin/az")
    private_stderr = b"PRIVATE_AZURE_STDERR_SENTINEL"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["az"], timeout=1, stderr=private_stderr)
        stdout = {
            "nonzero": b"{}",
            "malformed": b"{not-json",
            "oversized": b"x" * (PROBE["AZURE_CLI_MAX_JSON_BYTES"] + 1),
            "empty": b"",
        }[failure]
        return subprocess.CompletedProcess(
            ["az"],
            1 if failure == "nonzero" else 0,
            stdout=stdout,
            stderr=private_stderr,
        )

    monkeypatch.setattr(globals_["subprocess"], "run", fake_run)
    expected_code = (
        "azure_cli_command_failed"
        if failure in {"nonzero", "timeout"}
        else "azure_cli_response_invalid"
    )

    with pytest.raises(PROBE["ProbeRefusal"], match=expected_code) as caught:
        PROBE["_azure_cli_json"]("account", "list")

    captured = capsys.readouterr()
    assert "PRIVATE_AZURE_STDERR_SENTINEL" not in str(caught.value)
    assert "PRIVATE_AZURE_STDERR_SENTINEL" not in captured.out
    assert "PRIVATE_AZURE_STDERR_SENTINEL" not in captured.err


def test_azure_cli_uses_bounded_noninteractive_secret_safe_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    globals_ = PROBE["_azure_cli_json"].__globals__
    monkeypatch.setattr(globals_["shutil"], "which", lambda _name: "/safe/bin/az")
    provider_secrets = {
        "AZURE_OPENAI_API_KEY": "PRIVATE_AZURE_OPENAI_KEY",
        "OPENAI_API_KEY": "PRIVATE_OPENAI_KEY",
        "AZURE_CLIENT_SECRET": "PRIVATE_AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID": "PRIVATE_AZURE_TENANT",
        "MURMUR_SCENE_LLM_PROVIDER": "azure_openai",
        "MURMUR_SCENE_LLM_MODEL": "PRIVATE_MODEL_ALIAS",
    }
    for name, value in provider_secrets.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b"{}", stderr=b"")

    monkeypatch.setattr(globals_["subprocess"], "run", fake_run)

    assert PROBE["_azure_cli_json"]("account", "list", "--output", "json") == {}
    assert captured["command"] == [
        "/safe/bin/az",
        "account",
        "list",
        "--output",
        "json",
    ]
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True
    assert captured["check"] is False
    assert captured["timeout"] == PROBE["AZURE_CLI_TIMEOUT_SECONDS"]
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["AZURE_CORE_COLLECT_TELEMETRY"] == "no"
    assert environment["AZURE_EXTENSION_USE_DYNAMIC_INSTALL"] == "no"
    assert not provider_secrets.keys() & environment.keys()
    assert not set(provider_secrets.values()) & set(environment.values())
    assert set(environment) <= {
        "AZURE_CONFIG_DIR",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
        "AZURE_CORE_COLLECT_TELEMETRY",
        "AZURE_CORE_NO_COLOR",
        "AZURE_CORE_ONLY_SHOW_ERRORS",
        "AZURE_EXTENSION_USE_DYNAMIC_INSTALL",
    }


def test_git_uses_a_minimal_noninteractive_environment_without_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    globals_ = PROBE["_git"].__globals__
    retained_environment = {
        "HOME": "/safe/home",
        "PATH": "/safe/bin",
        "SSH_AUTH_SOCK": "/safe/ssh-agent.sock",
        "XDG_CONFIG_HOME": "/safe/xdg",
    }
    provider_secrets = {
        "AZURE_OPENAI_API_KEY": "PRIVATE_AZURE_OPENAI_KEY",
        "OPENAI_API_KEY": "PRIVATE_OPENAI_KEY",
        "AZURE_CLIENT_SECRET": "PRIVATE_AZURE_CLIENT_SECRET",
        "MURMUR_SCENE_LLM_PROVIDER": "azure_openai",
        "MURMUR_SCENE_LLM_MODEL": "PRIVATE_MODEL_ALIAS",
        "PRIVATE_SENTINEL_ENV": "PRIVATE_ENV_VALUE",
    }
    for name, value in retained_environment.items() | provider_secrets.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="clean\n", stderr="")

    monkeypatch.setattr(globals_["subprocess"], "run", fake_run)

    assert PROBE["_git"]("status", "--porcelain") == "clean"
    assert captured["command"] == ["git", "status", "--porcelain"]
    assert captured["cwd"] == PROJECT_ROOT
    assert captured["check"] is True
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == PROBE["GIT_TIMEOUT_SECONDS"]
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == retained_environment["HOME"]
    assert environment["PATH"] == retained_environment["PATH"]
    assert environment["SSH_AUTH_SOCK"] == retained_environment["SSH_AUTH_SOCK"]
    assert environment["XDG_CONFIG_HOME"] == retained_environment["XDG_CONFIG_HOME"]
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"
    assert not provider_secrets.keys() & environment.keys()
    assert not set(provider_secrets.values()) & set(environment.values())
    assert set(environment) <= {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
        "SSH_AUTH_SOCK",
        "XDG_CONFIG_HOME",
        "GIT_TERMINAL_PROMPT",
        "GCM_INTERACTIVE",
    }


def test_live_entrypoint_never_constructs_provider_after_attestation_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    globals_ = PROBE["main"].__globals__
    provider_factory_calls = 0

    def refuse_attestation(_endpoint: str, _deployment: str) -> object:
        raise PROBE["ProbeRefusal"]("azure_deployment_attestation_mismatch")

    def provider_factory(_attestation: object) -> object:
        nonlocal provider_factory_calls
        provider_factory_calls += 1
        return object()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--max-cost-usd",
            "0.50",
            "--acknowledge-paid-provider",
            PROBE["ACKNOWLEDGEMENT"],
        ],
    )
    monkeypatch.setitem(globals_, "_load_env_file", lambda _path: None)
    monkeypatch.setitem(globals_, "_validate_args", lambda _args: 500_000_000)
    monkeypatch.setitem(globals_, "_safe_output_path", lambda _path: tmp_path / "report.json")
    monkeypatch.setitem(
        globals_,
        "_assert_clean_pushed_head",
        lambda *_args, **_kwargs: PROBE["GitState"]("a" * 40, "branch", "origin/branch"),
    )
    monkeypatch.setitem(
        globals_,
        "_configured_azure_target",
        lambda: (AZURE_ENDPOINT, PROBE["EXPECTED_AZURE_DEPLOYMENT"]),
    )
    monkeypatch.setitem(globals_, "_attest_azure_deployment", refuse_attestation)
    monkeypatch.setitem(globals_, "_provider_factory", provider_factory)

    assert PROBE["main"]() == 2
    assert provider_factory_calls == 0
    captured = capsys.readouterr()
    assert "azure_deployment_attestation_mismatch" in captured.err
    assert not (tmp_path / "report.json").exists()


def _patch_live_main_after_preflight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_schedule: Any,
    attest_azure_deployment: Any,
) -> list[dict[str, object]]:
    globals_ = PROBE["main"].__globals__
    reports: list[dict[str, object]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--max-cost-usd",
            "0.50",
            "--acknowledge-paid-provider",
            PROBE["ACKNOWLEDGEMENT"],
        ],
    )
    monkeypatch.setitem(globals_, "_load_env_file", lambda _path: None)
    monkeypatch.setitem(globals_, "_validate_args", lambda _args: 500_000_000)
    monkeypatch.setitem(
        globals_,
        "_safe_output_path",
        lambda _path: (
            PROJECT_ROOT / "var/live-scene/evaluations/test-semantic-storyboard-report.json"
        ),
    )
    source = PROBE["GitState"]("a" * 40, "branch", "origin/branch")
    monkeypatch.setitem(
        globals_,
        "_assert_clean_pushed_head",
        lambda *_args, **_kwargs: source,
    )
    monkeypatch.setitem(
        globals_,
        "_configured_azure_target",
        lambda: (AZURE_ENDPOINT, PROBE["EXPECTED_AZURE_DEPLOYMENT"]),
    )
    monkeypatch.setitem(globals_, "_attest_azure_deployment", attest_azure_deployment)
    monkeypatch.setitem(globals_, "_provider_factory", lambda _attestation: lambda: object())
    monkeypatch.setitem(globals_, "_run_schedule", run_schedule)
    monkeypatch.setitem(
        globals_,
        "_write_private_report",
        lambda _path, payload: reports.append(payload),
    )
    return reports


@pytest.mark.parametrize(
    ("failure_kind", "expected_failure_code"),
    [
        ("refusal", "post_dispatch_safety_check_failed"),
        ("unexpected", "unexpected_post_dispatch_failure"),
    ],
)
def test_post_dispatch_exceptions_are_invalidated_without_automatic_retry(
    failure_kind: str,
    expected_failure_code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence: dict[str, object] = {}

    async def fail_after_admission(
        _schedule: object,
        *,
        ledger: Any,
        **_kwargs: object,
    ) -> Any:
        scheduled = PROBE["SCHEDULE"][0]
        ledger.admit(
            scheduled.reservation_id,
            PROBE["_messages_for_case"](scheduled.case),
            max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
        )
        evidence["reserved"] = PROBE["_format_nano_usd"](ledger.admitted_reserved_cost_nano_usd)
        if failure_kind == "refusal":
            raise PROBE["ProbeRefusal"]("PRIVATE_POST_DISPATCH_REFUSAL")
        raise RuntimeError("PRIVATE_POST_DISPATCH_EXCEPTION")

    reports = _patch_live_main_after_preflight(
        monkeypatch,
        run_schedule=fail_after_admission,
        attest_azure_deployment=lambda _endpoint, _deployment: _valid_attestation(),
    )

    assert PROBE["main"]() == 1
    captured = capsys.readouterr()
    failure = json.loads(captured.err)
    assert failure == {
        "mode": "live",
        "status": "post_dispatch_invalidated",
        "failureCode": expected_failure_code,
        "admittedProviderCallCount": 1,
        "admittedReservedMaxCostUsd": evidence["reserved"],
        "automaticRetryAllowed": False,
    }
    assert "Semantic storyboard probe refused" not in captured.err
    assert "PRIVATE_POST_DISPATCH" not in captured.err
    assert reports == []


@pytest.mark.parametrize(
    ("post_run_failure", "expected_failure_code", "post_attestation_present"),
    [
        ("changed", "azure_deployment_changed_during_run", True),
        ("cli-failure", "post_run_azure_attestation_failed", False),
    ],
)
def test_post_run_attestation_instability_is_reported_and_disqualifies_evidence(
    post_run_failure: str,
    expected_failure_code: str,
    post_attestation_present: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initial_attestation = _valid_attestation()
    changed_attestation = dataclasses.replace(
        initial_attestation,
        deployment_etag_sha256="f" * 64,
    )
    attestation_calls = 0

    def attest(_endpoint: str, _deployment: str) -> Any:
        nonlocal attestation_calls
        attestation_calls += 1
        if attestation_calls == 1:
            return initial_attestation
        if post_run_failure == "cli-failure":
            raise PROBE["ProbeRefusal"]("azure_cli_command_failed")
        return changed_attestation

    async def passing_schedule(
        schedule: tuple[Any, ...],
        *,
        ledger: Any,
        pacer: Any,
        **_kwargs: object,
    ) -> Any:
        results = []
        for scheduled in schedule:
            ledger.admit(
                scheduled.reservation_id,
                PROBE["_messages_for_case"](scheduled.case),
                max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
            )
            observation = _observation(scheduled)
            results.append((observation, PROBE["_score_case"](scheduled.case, observation)))
        pacer.admission_count = len(schedule)
        return results, None, True

    reports = _patch_live_main_after_preflight(
        monkeypatch,
        run_schedule=passing_schedule,
        attest_azure_deployment=attest,
    )

    assert PROBE["main"]() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert attestation_calls == 2
    assert len(reports) == 1
    report = reports[0]
    assert report["azureDeploymentAttestationFailureCode"] == expected_failure_code
    assert (report["postRunAzureDeploymentAttestation"] is not None) is post_attestation_present
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["deploymentAttestationStable"] is False
    assert metrics["accountingPassed"] is False
    assert metrics["serverQualificationPassed"] is False
    final_summary = json.loads(captured.out.strip().splitlines()[-1])
    assert final_summary["providerCallCount"] == 40
    assert final_summary["serverQualificationPassed"] is False


def test_azure_attestation_report_contains_only_safe_model_metadata_and_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _valid_attestation()
    sanitized = attestation.sanitized()
    assert set(sanitized) == {
        "deploymentName",
        "modelFormat",
        "modelName",
        "modelVersion",
        "skuName",
        "provisioningState",
        "versionUpgradeOption",
        "enabledSubscriptionCount",
        "endpointHostSha256",
        "accountResourceIdSha256",
        "deploymentResourceIdSha256",
        "deploymentEtagSha256",
    }
    assert all(
        len(str(sanitized[key])) == 64
        for key in (
            "endpointHostSha256",
            "accountResourceIdSha256",
            "deploymentResourceIdSha256",
            "deploymentEtagSha256",
        )
    )

    report_root = (tmp_path / "evaluations").resolve()
    report_path = report_root / "report.json"
    monkeypatch.setitem(PROBE["_write_private_report"].__globals__, "VAR_ROOT", report_root)
    PROBE["_write_private_report"](
        report_path,
        {"azureDeploymentAttestation": sanitized},
    )
    serialized = report_path.read_text()
    for secret in (
        AZURE_ENDPOINT,
        "storyboard-prod.openai.azure.com",
        AZURE_SUBSCRIPTION_ONE,
        AZURE_SUBSCRIPTION_TWO,
        AZURE_RESOURCE_GROUP,
        AZURE_ACCOUNT_NAME,
        AZURE_ACCOUNT_ID,
        AZURE_DEPLOYMENT_ETAG,
        "PRIVATE_TENANT_SENTINEL",
        "PRIVATE_API_KEY_SENTINEL",
        "PRIVATE_AZURE_STDERR_SENTINEL",
    ):
        assert secret not in serialized


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, *_args: Any, **_kwargs: Any) -> object:
        self.calls += 1

        async def iterate():
            if False:
                yield "unused"

        return iterate()


class _FakeStream:
    def __init__(self, items: list[object]) -> None:
        self.items = list(items)
        self.close_calls = 0

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> str | bytes:
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, str | bytes)
        return item

    async def aclose(self) -> None:
        self.close_calls += 1


class _StreamingDelegate:
    def __init__(self, items: list[object]) -> None:
        self.stream_instance = _FakeStream(items)
        self.calls = 0

    def stream(self, *_args: object, **_kwargs: object) -> _FakeStream:
        self.calls += 1
        return self.stream_instance


def test_single_call_client_rejects_second_stream_before_delegation() -> None:
    scheduled = PROBE["SCHEDULE"][0]
    ledger = PROBE["_preflight_budget"](
        PROBE["SCHEDULE"],
        max_cost_nano_usd=500_000_000,
        max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
    )
    delegate = _Delegate()
    client = PROBE["SingleCallBudgetedClient"](
        delegate,
        ledger,
        scheduled.reservation_id,
    )
    messages = PROBE["_messages_for_case"](scheduled.case)

    client.stream(messages, temperature=0.0, max_tokens=PROBE["MAX_OUTPUT_TOKENS"])
    with pytest.raises(PROBE["ProbeRefusal"], match="second provider stream"):
        client.stream(messages, temperature=0.0, max_tokens=PROBE["MAX_OUTPUT_TOKENS"])

    assert delegate.calls == 1
    assert client.call_count == 1
    assert ledger.admitted_count == 1


def test_storyboard_sse_decoder_accepts_every_byte_boundary_and_rejects_truncation() -> None:
    from murmur.live_scene.semantic_storyboard_service_contracts import (
        SemanticStoryboardSceneStreamStartedEventV1,
    )
    from murmur.live_scene.semantic_storyboard_wire import (
        encode_semantic_storyboard_scene_stream_event,
    )

    event = SemanticStoryboardSceneStreamStartedEventV1(
        generation=1,
        attempt=1,
        baseRevision=1,
    )
    encoded = encode_semantic_storyboard_scene_stream_event(event).encode()
    decoder = PROBE["SemanticStoryboardSseDecoder"]()
    decoded: list[object] = []
    for byte in encoded:
        decoded.extend(decoder.feed(bytes((byte,))))
    decoder.finish()

    assert decoded == [event]

    truncated = PROBE["SemanticStoryboardSseDecoder"]()
    truncated.feed(encoded[:-1])
    with pytest.raises(PROBE["ProbeProtocolError"], match="truncated_sse_record"):
        truncated.finish()


@pytest.mark.parametrize(
    "payload",
    [
        b"event: message\ndata: {}\n\n",
        b"id: 1\ndata: {}\n\n",
        b": comment\n\n",
        b"data: \n\n",
        b"data: not-json\n\n",
        b"data: {}\ndata: {}\n\n",
        b"data: {}\r\n\r\n",
    ],
)
def test_storyboard_sse_decoder_rejects_noncanonical_frames(payload: bytes) -> None:
    decoder = PROBE["SemanticStoryboardSseDecoder"]()
    with pytest.raises(PROBE["ProbeProtocolError"]):
        decoder.feed(payload)
        decoder.finish()


def test_storyboard_sse_decoder_rejects_non_bytes_and_oversized_frames() -> None:
    from murmur.live_scene.semantic_storyboard_wire import (
        MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES,
    )

    decoder = PROBE["SemanticStoryboardSseDecoder"]()
    with pytest.raises(TypeError, match="bytes"):
        decoder.feed("data: {}\n\n")  # type: ignore[arg-type]

    oversized = PROBE["SemanticStoryboardSseDecoder"]()
    with pytest.raises(PROBE["ProbeProtocolError"], match="sse_event_too_large"):
        oversized.feed(b"data: " + b"x" * MAX_SEMANTIC_STORYBOARD_SSE_EVENT_BYTES + b"\n\n")


def _ndjson(record: Any) -> str:
    return json.dumps(record.canonical(), separators=(",", ":")) + "\n"


def _abstain(reason: str) -> str:
    return (
        json.dumps(
            {"v": 1, "act": "abstain", "reasonCode": reason},
            separators=(",", ":"),
        )
        + "\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "case_id",
        "stream_items",
        "terminal",
        "accepted",
        "decline_reason",
        "failure_code",
        "rubric_passed",
        "forbidden_mutation",
    ),
    [
        (
            "lower_path_only",
            [_ndjson(LOWER)],
            "model_stop",
            (LOWER,),
            None,
            None,
            True,
            False,
        ),
        (
            "lower_first_equal",
            [_ndjson(LOWER), "not-json\n"],
            "accepted_prefix",
            (LOWER,),
            None,
            None,
            False,
            False,
        ),
        (
            "unsupported_wind",
            [_abstain("unsupported_physics")],
            "declined",
            (),
            "unsupported_physics",
            None,
            True,
            False,
        ),
        (
            "unsupported_launch_height",
            [_abstain("unsupported_initial_condition")],
            "declined",
            (),
            "unsupported_initial_condition",
            None,
            True,
            False,
        ),
        (
            "unsupported_angles",
            [_abstain("unsupported_problem")],
            "declined",
            (),
            "unsupported_problem",
            None,
            True,
            False,
        ),
        (
            "ambiguous_better",
            [_abstain("ambiguous_intent")],
            "declined",
            (),
            "ambiguous_intent",
            None,
            True,
            False,
        ),
        (
            "followup_apex_from_paths",
            [_ndjson(HIGHER_APEX)],
            "model_stop",
            (HIGHER_APEX,),
            None,
            None,
            True,
            False,
        ),
        (
            "lower_first_equal",
            [RuntimeError("PRIVATE_PROVIDER_SENTINEL")],
            "failed",
            (),
            None,
            "provider_error",
            False,
            False,
        ),
    ],
)
async def test_real_service_lifecycle_is_scored_from_canonical_verified_frontiers(
    case_id: str,
    stream_items: list[object],
    terminal: str,
    accepted: tuple[Any, ...],
    decline_reason: str | None,
    failure_code: str | None,
    rubric_passed: bool,
    forbidden_mutation: bool,
) -> None:
    from murmur.live_scene.semantic_storyboard_service import SemanticStoryboardService

    scheduled = _scheduled(1, case_id)
    ledger = PROBE["_preflight_budget"](max_cost_nano_usd=500_000_000)
    delegate = _StreamingDelegate(stream_items)
    client = PROBE["SingleCallBudgetedClient"](
        delegate,
        ledger,
        scheduled.reservation_id,
    )
    observation = await PROBE["_run_case"](
        scheduled,
        service=SemanticStoryboardService(client=client),
        client=client,
    )
    score = PROBE["_score_case"](scheduled.case, observation)

    assert observation.terminal == terminal
    assert observation.accepted_records == accepted
    assert observation.checkpoint_count == len(accepted)
    assert observation.decline_reason == decline_reason
    assert observation.failure_code == failure_code
    assert observation.provider_call_count == delegate.calls == client.call_count == 1
    assert observation.certificate_chain_valid is True
    assert len(observation.program_sha256) == 64
    assert score.rubric_passed is rubric_passed
    assert score.forbidden_mutation is forbidden_mutation
    if accepted:
        assert observation.result_scene_sha256 != observation.base_scene_sha256
        assert observation.result_semantic_sha256 != observation.base_semantic_sha256
    else:
        assert observation.result_scene_sha256 == observation.base_scene_sha256
        assert observation.result_semantic_sha256 == observation.base_semantic_sha256
    assert delegate.stream_instance.close_calls == 1
    assert "PRIVATE_PROVIDER_SENTINEL" not in json.dumps(
        observation.sanitized(PROBE["_sha256_text"](scheduled.case.prompt)),
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_probe_rejects_an_event_after_a_terminal() -> None:
    from murmur.live_scene.semantic_storyboard_service_contracts import (
        SemanticStoryboardSceneStreamDeclinedEventV1,
        SemanticStoryboardSceneStreamStartedEventV1,
    )

    scheduled = _scheduled(1, "unsupported_wind")
    scene, _semantic = PROBE["_certified_frontier"](scheduled.case)
    started = SemanticStoryboardSceneStreamStartedEventV1(
        generation=scheduled.ordinal,
        attempt=1,
        baseRevision=scene.revision,
    )
    declined = SemanticStoryboardSceneStreamDeclinedEventV1(
        generation=scheduled.ordinal,
        attempt=1,
        baseRevision=scene.revision,
        finalRevision=scene.revision,
        reasonCode="unsupported_physics",
        message="This request is outside the verified storyboard boundary.",
    )

    class InvalidLifecycleService:
        def stream_events(self, _request: object) -> Any:
            async def iterate():
                yield started
                yield declined
                yield started

            return iterate()

    ledger = PROBE["_preflight_budget"](max_cost_nano_usd=500_000_000)
    client = PROBE["SingleCallBudgetedClient"](
        _Delegate(),
        ledger,
        scheduled.reservation_id,
    )
    observation = await PROBE["_run_case"](
        scheduled,
        service=InvalidLifecycleService(),
        client=client,
    )

    assert observation.terminal == "protocol_error"
    assert observation.failure_code == "event_after_terminal"
    assert observation.accepted_records == ()
    assert observation.certificate_chain_valid is False


@pytest.mark.asyncio
async def test_protocol_fault_after_a_verified_checkpoint_preserves_the_prefix() -> None:
    from murmur.live_scene.semantic_storyboard_service import SemanticStoryboardService
    from murmur.live_scene.semantic_storyboard_service_contracts import (
        SemanticStoryboardSceneStreamStartedEventV1,
    )

    scheduled = _scheduled(1, "lower_path_only")
    scene, _semantic = PROBE["_certified_frontier"](scheduled.case)
    ledger = PROBE["_preflight_budget"](max_cost_nano_usd=500_000_000)
    delegate = _StreamingDelegate([_ndjson(LOWER)])
    client = PROBE["SingleCallBudgetedClient"](
        delegate,
        ledger,
        scheduled.reservation_id,
    )
    real_service = SemanticStoryboardService(client=client)

    class EventAfterTerminalService:
        def stream_events(self, request: object) -> Any:
            async def iterate():
                async for event in real_service.stream_events(request):
                    yield event
                yield SemanticStoryboardSceneStreamStartedEventV1(
                    generation=scheduled.ordinal,
                    attempt=1,
                    baseRevision=scene.revision,
                )

            return iterate()

    observation = await PROBE["_run_case"](
        scheduled,
        service=EventAfterTerminalService(),
        client=client,
    )

    assert observation.terminal == "protocol_error"
    assert observation.failure_code == "event_after_terminal"
    assert observation.accepted_records == (LOWER,)
    assert observation.checkpoint_count == 1
    assert observation.result_scene_sha256 != observation.base_scene_sha256
    assert observation.result_semantic_sha256 != observation.base_semantic_sha256
    assert observation.certificate_chain_valid is False
    score = PROBE["_score_case"](scheduled.case, observation)
    assert score.safe_terminal is False
    assert score.first_attempt_valid is False
    assert score.rubric_passed is False


@pytest.mark.parametrize(
    ("records", "terminal", "expected", "forbidden"),
    [
        ((LOWER, HIGHER, EQUAL_PATHS), "model_stop", True, False),
        ((LOWER, HIGHER), "model_stop", False, False),
        ((HIGHER, LOWER, EQUAL_PATHS), "model_stop", False, False),
        ((LOWER, HIGHER, RANGE_FORMULA, EQUAL_PATHS), "model_stop", False, True),
        (
            (
                LOWER,
                HIGHER,
                _record("relate", "equal_range", "range_formula", "complementary_angles"),
            ),
            "model_stop",
            False,
            True,
        ),
        ((LOWER, HIGHER, EQUAL_PATHS), "accepted_prefix", False, False),
    ],
)
def test_rubric_enforces_required_forbidden_order_evidence_and_clean_stop(
    records: tuple[Any, ...],
    terminal: str,
    expected: bool,
    forbidden: bool,
) -> None:
    scheduled = _scheduled(1, "lower_first_equal")
    score = PROBE["_score_case"](
        scheduled.case,
        _observation(
            scheduled,
            records=records,
            terminal=terminal,
        ),
    )

    assert score.rubric_passed is expected
    assert score.forbidden_mutation is forbidden


def test_abstention_requires_exact_reason_zero_checkpoints_and_unchanged_frontiers() -> None:
    scheduled = _scheduled(1, "unsupported_wind")
    passed = PROBE["_score_case"](scheduled.case, _observation(scheduled))
    wrong_reason = PROBE["_score_case"](
        scheduled.case,
        _observation(scheduled, abstain_reason="unsupported_intent"),
    )
    mutated = PROBE["_score_case"](
        scheduled.case,
        _observation(scheduled, records=(LOWER,), mutate=True),
    )

    assert passed.safe_terminal is True
    assert passed.rubric_passed is True
    assert passed.forbidden_mutation is False
    assert wrong_reason.rubric_passed is False
    assert mutated.rubric_passed is False
    assert mutated.forbidden_mutation is True


def test_accepted_prefix_is_safe_but_never_first_attempt_valid_or_rubric_complete() -> None:
    scheduled = _scheduled(1, "lower_first_equal")
    observation = _observation(
        scheduled,
        records=(LOWER,),
        terminal="accepted_prefix",
        first_attempt_valid=False,
    )
    score = PROBE["_score_case"](scheduled.case, observation)

    assert score.safe_terminal is True
    assert score.first_attempt_valid is False
    assert score.rubric_passed is False


def test_observed_paid_run_failure_signatures_remain_disqualifying() -> None:
    round_one_generic = {
        "unsupported_launch_height": (
            RANGE_FORMULA,
            COMPLEMENTARY,
            LOWER,
            HIGHER,
            HIGHER_APEX,
        ),
        "unsupported_angles": (
            RANGE_FORMULA,
            COMPLEMENTARY,
            LOWER,
            HIGHER,
            EQUAL_MATH,
        ),
        "ambiguous_better": (
            RANGE_FORMULA,
            COMPLEMENTARY,
            LOWER,
            HIGHER,
            EQUAL_MATH,
        ),
    }
    round_two_generic = (RANGE_FORMULA, COMPLEMENTARY, EQUAL_MATH)
    scores = []

    for scheduled in PROBE["SCHEDULE"]:
        if scheduled.case.case_id in round_one_generic:
            records = (
                round_one_generic[scheduled.case.case_id]
                if scheduled.round_index == 1
                else round_two_generic
            )
            observation = _observation(
                scheduled,
                records=records,
                terminal="model_stop",
                mutate=True,
            )
        elif scheduled.case.case_id == "followup_apex_from_paths" and scheduled.round_index == 2:
            observation = _observation(
                scheduled,
                records=(),
                terminal="declined",
                abstain_reason="no_forward_progress",
                mutate=False,
            )
        else:
            observation = _observation(scheduled)
        scores.append(PROBE["_score_case"](scheduled.case, observation))

    failed = [score for score in scores if not score.rubric_passed]
    assert [(score.round_index, score.case_id) for score in failed] == [
        (1, "unsupported_launch_height"),
        (1, "unsupported_angles"),
        (1, "ambiguous_better"),
        (2, "followup_apex_from_paths"),
        (2, "unsupported_launch_height"),
        (2, "unsupported_angles"),
        (2, "ambiguous_better"),
    ]
    assert sum(score.forbidden_mutation for score in failed) == 6
    followup_failure = next(
        score for score in failed if score.case_id == "followup_apex_from_paths"
    )
    assert followup_failure.forbidden_mutation is False

    metrics = PROBE["_qualification_metrics"](scores)
    assert metrics["rubricPassCount"] == 33
    assert metrics["forbiddenMutationCount"] == 6
    assert metrics["mandatoryCasesPassedBothRounds"] is False
    assert metrics["negativeCasesPassedBothRounds"] is False
    assert metrics["serverQualificationPassed"] is False


def test_qualification_thresholds_and_mandatory_cases_are_fail_closed() -> None:
    scores = _passing_scores()
    metrics = PROBE["_qualification_metrics"](scores)
    assert metrics["serverQualificationPassed"] is True
    assert tuple(
        metrics[key]
        for key in (
            "safeCanonicalTerminalCount",
            "firstAttemptValidCount",
            "rubricPassCount",
            "forbiddenMutationCount",
        )
    ) == (40, 40, 40, 0)

    two_invalid = [
        dataclasses.replace(score, first_attempt_valid=False) if index < 2 else score
        for index, score in enumerate(scores)
    ]
    assert PROBE["_qualification_metrics"](two_invalid)["serverQualificationPassed"] is True
    three_invalid = [
        dataclasses.replace(score, first_attempt_valid=False) if index < 3 else score
        for index, score in enumerate(scores)
    ]
    assert PROBE["_qualification_metrics"](three_invalid)["serverQualificationPassed"] is False

    optional_indices = [
        index
        for index, score in enumerate(scores)
        if score.case_id not in MANDATORY_CASE_IDS | NEGATIVE_CASE_IDS
    ]
    four_rubric_misses = [
        dataclasses.replace(score, rubric_passed=False) if index in optional_indices[:4] else score
        for index, score in enumerate(scores)
    ]
    assert PROBE["_qualification_metrics"](four_rubric_misses)["serverQualificationPassed"] is True
    five_rubric_misses = [
        dataclasses.replace(score, rubric_passed=False) if index in optional_indices[:5] else score
        for index, score in enumerate(scores)
    ]
    assert PROBE["_qualification_metrics"](five_rubric_misses)["serverQualificationPassed"] is False

    one_unsafe = list(scores)
    one_unsafe[0] = dataclasses.replace(one_unsafe[0], safe_terminal=False)
    assert PROBE["_qualification_metrics"](one_unsafe)["serverQualificationPassed"] is False

    one_forbidden = list(scores)
    one_forbidden[0] = dataclasses.replace(one_forbidden[0], forbidden_mutation=True)
    assert PROBE["_qualification_metrics"](one_forbidden)["serverQualificationPassed"] is False
    assert (
        PROBE["_qualification_metrics"](
            scores,
            aborted_reason="calibration_failed_round_2",
        )["serverQualificationPassed"]
        is False
    )


@pytest.mark.parametrize("round_index", [1, 2])
@pytest.mark.parametrize("case_id", sorted(MANDATORY_CASE_IDS | NEGATIVE_CASE_IDS))
def test_each_mandatory_and_negative_case_must_pass_in_both_rounds(
    case_id: str,
    round_index: int,
) -> None:
    scores = _passing_scores()
    index = next(
        index
        for index, score in enumerate(scores)
        if score.case_id == case_id and score.round_index == round_index
    )
    scores[index] = dataclasses.replace(scores[index], rubric_passed=False)

    assert PROBE["_qualification_metrics"](scores)["serverQualificationPassed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("failed_round", "expected_calls"), [(1, 1), (2, 21)])
async def test_calibration_failure_stops_after_first_case_of_its_round(
    failed_round: int,
    expected_calls: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def run_case(scheduled: Any, **_kwargs: object) -> Any:
        calls.append(scheduled.reservation_id)
        observation = _observation(scheduled)
        if scheduled.round_index == failed_round and scheduled.case.case_id == EXPECTED_CASE_IDS[0]:
            return dataclasses.replace(observation, terminal="failed")
        return observation

    monkeypatch.setitem(PROBE["_run_schedule"].__globals__, "_run_case", run_case)
    ledger = PROBE["_preflight_budget"](
        max_cost_nano_usd=500_000_000,
        max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
    )
    results, aborted_reason, provider_client_closed = await PROBE["_run_schedule"](
        PROBE["SCHEDULE"],
        ledger=ledger,
        client_factory=_Delegate,
        pacer=PROBE["DispatchPacer"](0),
    )

    assert len(results) == expected_calls
    assert len(calls) == expected_calls
    assert aborted_reason == f"calibration_failed_round_{failed_round}"
    assert provider_client_closed is True


@pytest.mark.asyncio
async def test_cleanup_failure_is_observed_and_disqualifies_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTransport:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            raise RuntimeError("PRIVATE_CLOSE_SENTINEL")

    class DelegateWithOwnedTransport(_Delegate):
        def __init__(self) -> None:
            super().__init__()
            self.client = FailingTransport()

    async def run_case(scheduled: Any, **_kwargs: object) -> Any:
        return dataclasses.replace(_observation(scheduled), terminal="failed")

    delegate = DelegateWithOwnedTransport()
    monkeypatch.setitem(PROBE["_run_schedule"].__globals__, "_run_case", run_case)
    ledger = PROBE["_preflight_budget"](
        max_cost_nano_usd=500_000_000,
        max_tokens=PROBE["MAX_OUTPUT_TOKENS"],
    )
    results, aborted_reason, provider_client_closed = await PROBE["_run_schedule"](
        PROBE["SCHEDULE"],
        ledger=ledger,
        client_factory=lambda: delegate,
        pacer=PROBE["DispatchPacer"](0),
    )

    assert len(results) == 1
    assert aborted_reason == "calibration_failed_round_1"
    assert delegate.client.close_calls == 1
    assert provider_client_closed is False

    metrics = PROBE["_qualification_metrics"](
        _passing_scores(),
        provider_client_closed=provider_client_closed,
    )
    assert metrics["providerClientClosed"] is False
    assert metrics["accountingPassed"] is False
    assert metrics["serverQualificationPassed"] is False


def test_accounting_requires_reservation_admission_and_provider_call_equality() -> None:
    scores = _passing_scores()
    assert PROBE["_qualification_metrics"](scores)["accountingPassed"] is True
    unstable_attestation = PROBE["_qualification_metrics"](
        scores,
        deployment_attestation_stable=False,
    )
    assert unstable_attestation["deploymentAttestationStable"] is False
    assert unstable_attestation["accountingPassed"] is False
    assert unstable_attestation["serverQualificationPassed"] is False
    for field in ("reservation_count", "pacer_admission_count", "provider_call_count"):
        values = {
            "reservation_count": 40,
            "pacer_admission_count": 40,
            "provider_call_count": 40,
        }
        values[field] = 39
        metrics = PROBE["_qualification_metrics"](scores, **values)
        assert metrics["accountingPassed"] is False
        assert metrics["serverQualificationPassed"] is False
    for field in ("sdk_retry_count", "repair_call_count"):
        metrics = PROBE["_qualification_metrics"](scores, **{field: 1})
        assert metrics["accountingPassed"] is False
        assert metrics["serverQualificationPassed"] is False
    for count in (39, 41):
        metrics = PROBE["_qualification_metrics"](
            scores,
            reservation_count=count,
            pacer_admission_count=count,
            provider_call_count=count,
        )
        assert metrics["accountingPassed"] is False
        assert metrics["serverQualificationPassed"] is False


def test_case_report_redacts_prompt_messages_raw_chunks_and_private_errors() -> None:
    secret = "SECRET_PROMPT_SENTINEL"
    case = dataclasses.replace(_case("lower_path_only"), prompt=secret)
    scheduled = dataclasses.replace(_scheduled(1, "lower_path_only"), case=case)
    observation = _observation(scheduled)
    score = PROBE["_score_case"](case, observation)

    report = observation.sanitized(PROBE["_sha256_text"](secret)) | {"score": score.sanitized()}
    serialized = json.dumps(report, sort_keys=True)

    assert secret not in serialized
    assert report["promptSha256"] == PROBE["_sha256_text"](secret)
    assert not {"prompt", "messages", "rawChunks", "error", "endpoint"} & set(report)


def test_private_report_is_confined_atomic_and_mode_0600(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "evaluations").resolve()
    output = root / "paid-storyboard.json"
    monkeypatch.setitem(PROBE["_write_private_report"].__globals__, "VAR_ROOT", root)
    PROBE["_write_private_report"](output, {"safe": True})

    assert json.loads(output.read_text()) == {"safe": True}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(root.glob("*.tmp"))

    with pytest.raises(PROBE["ProbeRefusal"], match="escaped"):
        PROBE["_write_private_report"](
            tmp_path / "escaped.json",
            {"safe": True},
        )


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _pushed_repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "probe@example.invalid", cwd=repo)
    _git("config", "user.name", "Probe", cwd=repo)
    (repo / "tracked.txt").write_text("one\n")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    _git("remote", "add", "origin", str(remote), cwd=repo)
    _git("push", "-u", "origin", "main", cwd=repo)
    return repo, remote


def test_source_snapshot_refuses_dirty_unpushed_and_detached_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _remote = _pushed_repository(tmp_path)
    monkeypatch.setitem(PROBE["_git"].__globals__, "PROJECT_ROOT", repo)
    snapshot = PROBE["_assert_clean_pushed_head"]()
    assert snapshot.commit == _git("rev-parse", "@{upstream}", cwd=repo)

    (repo / "tracked.txt").write_text("dirty\n")
    with pytest.raises(PROBE["ProbeRefusal"], match="clean"):
        PROBE["_assert_clean_pushed_head"]()
    (repo / "tracked.txt").write_text("one\n")

    (repo / "tracked.txt").write_text("two\n")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "ahead", cwd=repo)
    with pytest.raises(PROBE["ProbeRefusal"], match="pushed upstream"):
        PROBE["_assert_clean_pushed_head"]()

    _git("checkout", "--detach", "@{upstream}", cwd=repo)
    with pytest.raises(PROBE["ProbeRefusal"], match=r"provenance|branch"):
        PROBE["_assert_clean_pushed_head"]()


def test_source_snapshot_refreshes_remote_before_accepting_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, remote = _pushed_repository(tmp_path)
    other = tmp_path / "other"
    _git("clone", "--branch", "main", str(remote), str(other), cwd=tmp_path)
    _git("config", "user.email", "other@example.invalid", cwd=other)
    _git("config", "user.name", "Other", cwd=other)
    (other / "tracked.txt").write_text("remote advanced\n")
    _git("add", "tracked.txt", cwd=other)
    _git("commit", "-m", "remote advance", cwd=other)
    _git("push", cwd=other)

    monkeypatch.setitem(PROBE["_git"].__globals__, "PROJECT_ROOT", repo)
    with pytest.raises(PROBE["ProbeRefusal"], match=r"pushed upstream|remote"):
        PROBE["_assert_clean_pushed_head"]()


def test_source_state_stability_rejects_mid_run_commit_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _remote = _pushed_repository(tmp_path)
    monkeypatch.setitem(PROBE["_git"].__globals__, "PROJECT_ROOT", repo)
    before = PROBE["_assert_clean_pushed_head"]()
    PROBE["_assert_clean_pushed_head"](before)

    (repo / "tracked.txt").write_text("two\n")
    _git("add", "tracked.txt", cwd=repo)
    _git("commit", "-m", "drift", cwd=repo)
    _git("push", cwd=repo)
    with pytest.raises(PROBE["ProbeRefusal"], match="changed during the paid run"):
        PROBE["_assert_clean_pushed_head"](before)
