"""Provider-free safety tests for Gate 1.8's authenticated acceptance launcher."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from murmur.live_scene.contracts import SceneState
from murmur.live_scene.semantic_storyboard_checkpoint_compiler import (
    ValidatedSemanticStoryboardTransitionV1,
    compile_certified_semantic_storyboard_anchor,
    compile_certified_semantic_storyboard_checkpoint,
)
from murmur.live_scene.semantic_storyboard_contracts import (
    PairedProjectileComparisonSpecV1,
    ProjectileStoryboardSemanticSceneStateV1,
    StoryboardTrajectoryId,
    TraceStoryboardRecordV1,
)
from murmur.live_scene.semantic_storyboard_director import (
    build_semantic_storyboard_director_messages,
)
from murmur.live_scene.semantic_storyboard_requests import (
    SEMANTIC_STORYBOARD_PROTOCOL,
    SemanticStoryboardDirectorRequestV1,
)
from murmur.live_scene.semantic_storyboard_routing import route_semantic_storyboard_record

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "manual" / "serve_authenticated_storyboard_acceptance.py"
SPEC = importlib.util.spec_from_file_location("gate18_authenticated_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
acceptance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = acceptance
SPEC.loader.exec_module(acceptance)


class _RecordingPacer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.reservations: list[Any] = []

    async def admit(self, reservation: Any) -> None:
        self.events.append("pacer")
        self.reservations.append(reservation)


class _Stream:
    def __init__(self, items: list[object]) -> None:
        self.items = list(items)
        self.close_calls = 0

    def __aiter__(self) -> _Stream:
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


class _Delegate:
    def __init__(
        self,
        stream_factory: Callable[[], AsyncIterator[str | bytes]],
        *,
        synchronous_failure: BaseException | None = None,
        close_failure: BaseException | None = None,
    ) -> None:
        self._stream_factory = stream_factory
        self._synchronous_failure = synchronous_failure
        self._close_failure = close_failure
        self.stream_calls = 0
        self.close_calls = 0

    def stream(
        self,
        _messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        **_kwargs: object,
    ) -> AsyncIterator[str | bytes]:
        assert temperature == 0.0
        assert max_tokens == acceptance.MAX_OUTPUT_TOKENS
        self.stream_calls += 1
        if self._synchronous_failure is not None:
            raise self._synchronous_failure
        return self._stream_factory()

    async def aclose(self) -> None:
        self.close_calls += 1
        if self._close_failure is not None:
            raise self._close_failure


def _problem(angles: tuple[int, int] = (30, 60)) -> PairedProjectileComparisonSpecV1:
    return PairedProjectileComparisonSpecV1(speedMps=20, anglesDeg=angles)


def _anchor(
    problem: PairedProjectileComparisonSpecV1 | None = None,
) -> ValidatedSemanticStoryboardTransitionV1:
    return compile_certified_semantic_storyboard_anchor(
        problem or _problem(),
        base_scene=SceneState(revision=0),
        base_semantic_scene=ProjectileStoryboardSemanticSceneStateV1(revision=0),
    )


def _advance_lower(
    anchor: ValidatedSemanticStoryboardTransitionV1,
) -> ValidatedSemanticStoryboardTransitionV1:
    record = TraceStoryboardRecordV1(
        v=1,
        act="trace",
        trajectory_id=StoryboardTrajectoryId.LOWER_ANGLE,
    )
    beat = route_semantic_storyboard_record(
        record,
        problem_spec=anchor.checkpoint.problem_spec,
        semantic_scene=anchor.result_semantic_scene,
    )
    return compile_certified_semantic_storyboard_checkpoint(
        beat,
        base_scene=anchor.result_scene,
        base_semantic_scene=anchor.result_semantic_scene,
    )


def _request(
    transition: ValidatedSemanticStoryboardTransitionV1,
    prompt: str,
    *,
    generation: int = 1,
) -> SemanticStoryboardDirectorRequestV1:
    return SemanticStoryboardDirectorRequestV1(
        protocol=SEMANTIC_STORYBOARD_PROTOCOL,
        routing_mode="director",
        prompt=prompt,
        problem_spec=transition.checkpoint.problem_spec,
        generation=generation,
        base_scene=transition.result_scene,
        base_semantic_scene=transition.result_semantic_scene,
    )


def _messages(request: SemanticStoryboardDirectorRequestV1) -> list[dict[str, str]]:
    return build_semantic_storyboard_director_messages(
        request.prompt,
        request.problem_spec,
        request.base_semantic_scene,
    )


def _attestation(
    *, token_limit_count: int = 12_000
) -> acceptance.paid_probe.AzureDeploymentAttestation:
    digest = "a" * 64
    return acceptance.paid_probe.AzureDeploymentAttestation(
        deployment_name="murmur-gpt-oss-120b",
        model_format="OpenAI-OSS",
        model_name="gpt-oss-120b",
        model_version="1",
        sku_name="GlobalStandard",
        provisioning_state="Succeeded",
        version_upgrade_option="NoAutoUpgrade",
        capacity_units=1,
        request_limit_count=12,
        request_limit_period_seconds=60,
        token_limit_count=token_limit_count,
        token_limit_period_seconds=60,
        enabled_subscription_count=1,
        endpoint_host_sha256=digest,
        account_resource_id_sha256=digest,
        deployment_resource_id_sha256=digest,
        deployment_etag_sha256=digest,
    )


def _source() -> acceptance.AcceptanceSource:
    return acceptance.AcceptanceSource(
        git=acceptance.paid_probe.GitState(
            commit="b" * 40,
            branch="codex/gate18-live-semantic-storyboard",
            upstream="origin/codex/gate18-live-semantic-storyboard",
        ),
        authorization_id="private-acceptance-authorization",
    )


def _guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    events: list[str] | None = None,
    source_guard: Callable[[Any], Any] | None = None,
    attestation: acceptance.paid_probe.AzureDeploymentAttestation | None = None,
    max_cost_nano_usd: int = acceptance.AUTHORIZED_MAX_COST_NANO_USD,
) -> tuple[
    acceptance.AuthenticatedStoryboardAcceptanceGuard,
    _RecordingPacer,
    list[str],
]:
    observed = events if events is not None else []
    root = (tmp_path / "evaluations").resolve()
    monkeypatch.setattr(acceptance, "VAR_ROOT", root)
    pacer = _RecordingPacer(observed)

    def default_source_guard(expected: Any) -> Any:
        observed.append("source")
        return expected

    def consume(source: acceptance.AcceptanceSource) -> Path:
        observed.append("consume")
        marker = tmp_path / f"{name}.consumed"
        marker.write_text(source.authorization_sha256, encoding="utf-8")
        marker.chmod(0o600)
        return marker

    guard = acceptance.AuthenticatedStoryboardAcceptanceGuard(
        source=_source(),
        attestation=attestation or _attestation(),
        output_path=root / name / "report.json",
        pacer=pacer,
        max_cost_nano_usd=max_cost_nano_usd,
        source_guard=source_guard or default_source_guard,
        authorization_consumer=consume,
    )
    return guard, pacer, observed


def _report(guard: acceptance.AuthenticatedStoryboardAcceptanceGuard) -> dict[str, Any]:
    return json.loads(guard.output_path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_exact_two_call_lifecycle_is_bounded_private_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "raw-api-key-secret")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://private-resource.openai.azure.com")
    guard, pacer, events = _guard(tmp_path, monkeypatch, name="bounded")
    anchor = _anchor()
    initial = _request(anchor, acceptance.INITIAL_PROMPT)
    initial_messages = _messages(initial)

    await guard.before_director_dispatch(
        initial,
        initial_messages,
        acceptance.MAX_OUTPUT_TOKENS,
    )
    assert events == ["source", "consume", "pacer", "source"]
    first_report = _report(guard)
    assert first_report["metrics"]["pacerAdmissionCount"] == 1
    assert first_report["metrics"]["providerCallCount"] == 0
    assert first_report["admittedReservedMaxCostUsd"] == "0.003426880"
    assert (
        guard.begin_provider_stream(
            initial_messages,
            temperature=0.0,
            max_tokens=acceptance.MAX_OUTPUT_TOKENS,
        )
        == 1
    )

    continued = _advance_lower(anchor)
    follow_up = _request(continued, acceptance.FOLLOW_UP_PROMPT, generation=2)
    follow_up_messages = _messages(follow_up)
    await guard.before_director_dispatch(
        follow_up,
        follow_up_messages,
        acceptance.MAX_OUTPUT_TOKENS,
    )
    assert events == [
        "source",
        "consume",
        "pacer",
        "source",
        "source",
        "pacer",
        "source",
    ]
    assert (
        guard.begin_provider_stream(
            follow_up_messages,
            temperature=0.0,
            max_tokens=acceptance.MAX_OUTPUT_TOKENS,
        )
        == 2
    )
    with pytest.raises(acceptance.AcceptanceRefusal, match="budget is exhausted"):
        await guard.before_director_dispatch(
            follow_up,
            follow_up_messages,
            acceptance.MAX_OUTPUT_TOKENS,
        )

    report = _report(guard)
    serialized = json.dumps(report, sort_keys=True)
    assert report["admittedReservedMaxCostUsd"] == "0.006853760"
    assert report["metrics"]["pacerAdmissionCount"] == 2
    assert report["metrics"]["providerCallCount"] == 2
    assert [item.reservation_id for item in pacer.reservations] == [
        "authenticated-live-1",
        "authenticated-live-2",
    ]
    assert all(item.dispatch_token_count == 12_000 for item in pacer.reservations)
    assert acceptance.INITIAL_PROMPT not in serialized
    assert acceptance.FOLLOW_UP_PROMPT not in serialized
    assert guard.source.authorization_id not in serialized
    assert "raw-api-key-secret" not in serialized
    assert "private-resource.openai.azure.com" not in serialized
    assert stat.S_IMODE(guard.output_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(guard.output_path.parent.stat().st_mode) == 0o700


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ("wrong_prompt", "wrong_problem", "wrong_max_tokens", "nonempty_initial", "messages"),
)
async def test_unreviewed_initial_context_is_rejected_before_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    guard, _pacer, events = _guard(tmp_path, monkeypatch, name=mutation)
    anchor = _anchor()
    request = _request(anchor, acceptance.INITIAL_PROMPT)
    messages = _messages(request)
    max_tokens = acceptance.MAX_OUTPUT_TOKENS

    if mutation == "wrong_prompt":
        request = _request(anchor, "Draw something else.")
        messages = _messages(request)
    elif mutation == "wrong_problem":
        request = _request(_anchor(_problem((30, 45))), acceptance.INITIAL_PROMPT)
        messages = _messages(request)
    elif mutation == "wrong_max_tokens":
        max_tokens -= 1
    elif mutation == "nonempty_initial":
        request = _request(_advance_lower(anchor), acceptance.INITIAL_PROMPT)
        messages = _messages(request)
    else:
        messages = [dict(item) for item in messages]
        messages[0]["content"] += "\nunreviewed mutation"

    with pytest.raises(acceptance.AcceptanceRefusal):
        await guard.before_director_dispatch(request, messages, max_tokens)

    assert events == []
    assert not guard.authorization_consumed
    assert guard.provider_call_count == 0
    report = _report(guard)
    assert report["metrics"]["pacerAdmissionCount"] == 0
    assert report["admittedReservedMaxCostUsd"] == "0.000000000"


@pytest.mark.asyncio
async def test_empty_follow_up_frontier_is_rejected_without_another_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard, _pacer, events = _guard(tmp_path, monkeypatch, name="empty-follow-up")
    anchor = _anchor()
    initial = _request(anchor, acceptance.INITIAL_PROMPT)
    await guard.before_director_dispatch(
        initial,
        _messages(initial),
        acceptance.MAX_OUTPUT_TOKENS,
    )
    events.clear()
    follow_up = _request(anchor, acceptance.FOLLOW_UP_PROMPT, generation=2)

    with pytest.raises(acceptance.AcceptanceRefusal, match="frontier"):
        await guard.before_director_dispatch(
            follow_up,
            _messages(follow_up),
            acceptance.MAX_OUTPUT_TOKENS,
        )

    assert events == []
    assert _report(guard)["metrics"]["pacerAdmissionCount"] == 1


@pytest.mark.asyncio
async def test_source_is_checked_before_authorization_and_again_after_pacing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _request(_anchor(), acceptance.INITIAL_PROMPT)
    calls = 0

    def reject_immediately(_expected: Any) -> Any:
        raise acceptance.paid_probe.ProbeRefusal("source drift")

    refused, _pacer, events = _guard(
        tmp_path,
        monkeypatch,
        name="source-precheck",
        source_guard=reject_immediately,
    )
    with pytest.raises(acceptance.paid_probe.ProbeRefusal, match="source drift"):
        await refused.before_director_dispatch(
            initial,
            _messages(initial),
            acceptance.MAX_OUTPUT_TOKENS,
        )
    assert events == []
    assert not refused.authorization_consumed

    post_events: list[str] = []

    def reject_after_pacing(expected: Any) -> Any:
        nonlocal calls
        calls += 1
        post_events.append("source")
        if calls == 2:
            raise acceptance.paid_probe.ProbeRefusal("source drift after pacing")
        return expected

    post_guard, _pacer, post_events = _guard(
        tmp_path,
        monkeypatch,
        name="source-postcheck",
        events=post_events,
        source_guard=reject_after_pacing,
    )
    with pytest.raises(acceptance.paid_probe.ProbeRefusal, match="after pacing"):
        await post_guard.before_director_dispatch(
            initial,
            _messages(initial),
            acceptance.MAX_OUTPUT_TOKENS,
        )
    assert post_events == ["source", "consume", "pacer", "source"]
    report = _report(post_guard)
    assert report["metrics"]["pacerAdmissionCount"] == 1
    assert report["metrics"]["providerCallCount"] == 0
    assert report["admittedReservedMaxCostUsd"] == "0.003426880"


async def _admitted_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    delegate: _Delegate,
) -> tuple[
    acceptance.AuthenticatedStoryboardAcceptanceGuard,
    acceptance.GuardedAcceptanceClient,
    list[dict[str, str]],
]:
    guard, _pacer, _events = _guard(tmp_path, monkeypatch, name=name)
    request = _request(_anchor(), acceptance.INITIAL_PROMPT)
    messages = _messages(request)
    await guard.before_director_dispatch(
        request,
        messages,
        acceptance.MAX_OUTPUT_TOKENS,
    )
    return guard, acceptance.GuardedAcceptanceClient(delegate, guard), messages


@pytest.mark.asyncio
async def test_guarded_transport_records_terminals_and_closes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_delegate = _Delegate(lambda: _Stream(["ok"]))
    completed_guard, completed_client, messages = await _admitted_transport(
        tmp_path,
        monkeypatch,
        name="completed",
        delegate=completed_delegate,
    )
    assert [
        item
        async for item in completed_client.stream(
            messages,
            temperature=0.0,
            max_tokens=acceptance.MAX_OUTPUT_TOKENS,
        )
    ] == ["ok"]
    await completed_client.aclose()
    await completed_client.aclose()
    completed_report = _report(completed_guard)
    assert completed_report["metrics"]["streamTerminals"] == {"1": "completed"}
    assert completed_report["metrics"]["clientCloseCount"] == 1
    assert completed_delegate.close_calls == 1

    aborted_stream = _Stream(["unused"])
    aborted_delegate = _Delegate(lambda: aborted_stream)
    aborted_guard, aborted_client, messages = await _admitted_transport(
        tmp_path,
        monkeypatch,
        name="aborted",
        delegate=aborted_delegate,
    )
    observed = aborted_client.stream(
        messages,
        temperature=0.0,
        max_tokens=acceptance.MAX_OUTPUT_TOKENS,
    )
    await observed.aclose()  # type: ignore[attr-defined]
    assert _report(aborted_guard)["metrics"]["streamTerminals"] == {"1": "aborted"}
    assert aborted_stream.close_calls == 1

    failed_delegate = _Delegate(lambda: _Stream([RuntimeError("private provider detail")]))
    failed_guard, failed_client, messages = await _admitted_transport(
        tmp_path,
        monkeypatch,
        name="failed",
        delegate=failed_delegate,
    )
    failed_stream = failed_client.stream(
        messages,
        temperature=0.0,
        max_tokens=acceptance.MAX_OUTPUT_TOKENS,
    )
    with pytest.raises(RuntimeError, match="private provider detail"):
        await anext(failed_stream)
    assert _report(failed_guard)["metrics"]["streamTerminals"] == {"1": "failed"}

    sync_delegate = _Delegate(
        lambda: _Stream([]),
        synchronous_failure=RuntimeError("private synchronous detail"),
    )
    sync_guard, sync_client, messages = await _admitted_transport(
        tmp_path,
        monkeypatch,
        name="sync-failed",
        delegate=sync_delegate,
    )
    with pytest.raises(RuntimeError, match="private synchronous detail"):
        sync_client.stream(
            messages,
            temperature=0.0,
            max_tokens=acceptance.MAX_OUTPUT_TOKENS,
        )
    sync_serialized = json.dumps(_report(sync_guard), sort_keys=True)
    assert _report(sync_guard)["metrics"]["streamTerminals"] == {"1": "failed"}
    assert "private synchronous detail" not in sync_serialized


@pytest.mark.asyncio
async def test_unreviewed_provider_options_never_reach_delegate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delegate = _Delegate(lambda: _Stream([]))
    guard, client, messages = await _admitted_transport(
        tmp_path,
        monkeypatch,
        name="options",
        delegate=delegate,
    )
    with pytest.raises(acceptance.AcceptanceRefusal, match="options changed"):
        client.stream(
            messages,
            temperature=0.0,
            max_tokens=acceptance.MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
    assert delegate.stream_calls == 0
    assert guard.provider_call_count == 0


def test_budget_and_private_path_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(acceptance.AcceptanceRefusal, match="token window"):
        _guard(
            tmp_path,
            monkeypatch,
            name="small-window",
            attestation=_attestation(token_limit_count=acceptance.MAX_OUTPUT_TOKENS),
        )
    with pytest.raises(acceptance.AcceptanceRefusal, match="cost ceiling"):
        _guard(
            tmp_path,
            monkeypatch,
            name="cost-overrun",
            attestation=_attestation(token_limit_count=100_000),
        )
    with pytest.raises(acceptance.AcceptanceRefusal, match=r"USD 0\.01"):
        _guard(
            tmp_path,
            monkeypatch,
            name="wrong-ceiling",
            max_cost_nano_usd=acceptance.AUTHORIZED_MAX_COST_NANO_USD - 1,
        )

    root = (tmp_path / "safe-root").resolve()
    root.mkdir()
    monkeypatch.setattr(acceptance, "VAR_ROOT", root)
    assert acceptance._safe_output_path(str(root / "run" / "report.json")).is_relative_to(root)
    with pytest.raises(acceptance.AcceptanceRefusal, match="inside"):
        acceptance._safe_output_path(str(tmp_path / "outside.json"))
    existing = root / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceRefusal, match="already exist"):
        acceptance._safe_output_path(str(existing))
    target = root / "target.json"
    link = root / "link.json"
    link.symlink_to(target)
    with pytest.raises(acceptance.AcceptanceRefusal, match="symlink"):
        acceptance._safe_output_path(str(link))


def test_private_credential_files_require_regular_mode_0600(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "acceptance.env"
    env_file.write_text("GATE18_TEST_VALUE=loaded\n", encoding="utf-8")
    env_file.chmod(0o600)
    assert acceptance._validate_private_env_file(str(env_file)) == env_file.resolve()
    env_file.chmod(0o644)
    with pytest.raises(acceptance.AcceptanceRefusal, match="0600"):
        acceptance._validate_private_env_file(str(env_file))

    env_target = tmp_path / "target.env"
    env_target.write_text("GATE18_OTHER_VALUE=loaded\n", encoding="utf-8")
    env_target.chmod(0o600)
    env_link = tmp_path / "linked.env"
    env_link.symlink_to(env_target)
    with pytest.raises(acceptance.AcceptanceRefusal, match="non-symlink"):
        acceptance._validate_private_env_file(str(env_link))

    firebase = tmp_path / "firebase.json"
    firebase.write_text("{}", encoding="utf-8")
    firebase.chmod(0o600)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(firebase))
    acceptance._validate_firebase_credentials()
    firebase.chmod(0o644)
    with pytest.raises(acceptance.AcceptanceRefusal, match="0600"):
        acceptance._validate_firebase_credentials()
    firebase.chmod(0o600)
    firebase_link = tmp_path / "firebase-link.json"
    firebase_link.symlink_to(firebase)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(firebase_link))
    with pytest.raises(acceptance.AcceptanceRefusal, match="unsafe"):
        acceptance._validate_firebase_credentials()
