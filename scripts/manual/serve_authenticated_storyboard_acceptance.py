#!/usr/bin/env python3
"""Serve Gate 1.8's bounded authenticated live-product acceptance run.

The server uses the real Firebase-protected product endpoint and the attested
Azure deployment.  It permits only the reviewed initial and follow-up prompts,
reserves the full attested token window for each of exactly two Director calls,
disables SDK retries, and records only sanitized private accounting evidence.
Reflex anchoring and browser Replay remain provider-free.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import stat
import sys
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from murmur.live_scene.semantic_storyboard_director import (  # noqa: E402
    build_semantic_storyboard_director_messages,
)
from murmur.live_scene.semantic_storyboard_requests import (  # noqa: E402
    SemanticStoryboardDirectorRequestV1,
)

from scripts.manual import probe_semantic_storyboard as paid_probe  # noqa: E402

VAR_ROOT = (PROJECT_ROOT / "var" / "live-scene" / "evaluations").resolve()

ACTIVE_LIVE_ACCEPTANCE_AUTHORIZATION_ID: str | None = None
AUTHORIZED_MAX_COST_NANO_USD = 10_000_000
MAX_PROVIDER_CALLS = 2
MAX_OUTPUT_TOKENS = paid_probe.MAX_OUTPUT_TOKENS
INITIAL_PROMPT = (
    "Trace the lower-angle path first, then the higher-angle path, and then compare "
    "their heights and landing ranges."
)
FOLLOW_UP_PROMPT = "Why are the ranges equal?"
ALLOWED_PROMPTS = (INITIAL_PROMPT, FOLLOW_UP_PROMPT)
EVIDENCE_SCOPE = "authenticated_live_semantic_storyboard_acceptance"
PROVIDER_COST_ACKNOWLEDGEMENT = "I_ACCEPT_AUTHENTICATED_STORYBOARD_PROVIDER_COST"


class AcceptanceRefusal(ValueError):
    """Fixed, payload-free refusal before an unreviewed provider dispatch."""


class _Pacer(Protocol):
    async def admit(self, reservation: paid_probe.Reservation) -> None: ...


class _ProviderClient(Protocol):
    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        **kwargs: object,
    ) -> AsyncIterator[str | bytes]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AcceptanceSource:
    git: paid_probe.GitState
    authorization_id: str

    @property
    def authorization_sha256(self) -> str:
        return paid_probe._sha256_text(self.authorization_id)


@dataclass(frozen=True, slots=True)
class _AcceptanceReservation:
    reservation_id: str
    max_input_tokens: int
    max_output_tokens: int
    reserved_cost_nano_usd: int

    @property
    def dispatch_token_count(self) -> int:
        return self.max_input_tokens + self.max_output_tokens

    def bind_messages(self, message_sha256: str) -> paid_probe.Reservation:
        return paid_probe.Reservation(
            reservation_id=self.reservation_id,
            message_sha256=message_sha256,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            reserved_cost_nano_usd=self.reserved_cost_nano_usd,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _safe_output_path(raw_path: str) -> Path:
    unresolved = Path(raw_path).expanduser()
    if not unresolved.is_absolute():
        unresolved = PROJECT_ROOT / unresolved
    if os.path.lexists(unresolved) and unresolved.is_symlink():
        raise AcceptanceRefusal("--output must not be a symlink")
    candidate = unresolved.resolve()
    if candidate == VAR_ROOT or VAR_ROOT not in candidate.parents:
        raise AcceptanceRefusal("--output must resolve inside var/live-scene/evaluations")
    if os.path.lexists(candidate):
        raise AcceptanceRefusal("--output must not already exist")
    return candidate


def _atomic_private_replace(path: Path, payload: Mapping[str, object]) -> None:
    resolved = path.resolve()
    if resolved == VAR_ROOT or VAR_ROOT not in resolved.parents:
        raise AcceptanceRefusal("private evidence path escaped its root")
    parent = resolved.parent
    if os.path.lexists(parent) and (parent.is_symlink() or not parent.is_dir()):
        raise AcceptanceRefusal("private evidence directory is unsafe")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    if os.path.lexists(resolved) and (resolved.is_symlink() or not resolved.is_file()):
        raise AcceptanceRefusal("private evidence output is unsafe")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    temporary = parent / f".{resolved.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, resolved)
        os.chmod(resolved, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    if stat.S_IMODE(parent.stat().st_mode) != 0o700:
        raise AcceptanceRefusal("private evidence directory permissions were not retained")
    if stat.S_IMODE(resolved.stat().st_mode) != 0o600:
        raise AcceptanceRefusal("private evidence permissions were not retained")


def _consume_authorization(source: AcceptanceSource) -> Path:
    if (
        not ACTIVE_LIVE_ACCEPTANCE_AUTHORIZATION_ID
        or source.authorization_id != ACTIVE_LIVE_ACCEPTANCE_AUTHORIZATION_ID
    ):
        raise AcceptanceRefusal("live acceptance requires a fresh source-pinned authorization")
    root = paid_probe._paid_authorization_root()
    if os.path.lexists(root) and (root.is_symlink() or not root.is_dir()):
        raise AcceptanceRefusal("paid authorization store is unsafe")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    marker = root / f"{source.authorization_sha256}.consumed.json"
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AcceptanceRefusal("paid authorization was already consumed") from exc
    payload = {
        "authorizationIdSha256": source.authorization_sha256,
        "consumedAt": datetime.now(UTC).isoformat(),
        "evidenceScope": EVIDENCE_SCOPE,
        "sourceCommit": source.git.commit,
    }
    with os.fdopen(descriptor, "wb") as handle:
        handle.write((json.dumps(payload, sort_keys=True) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    if stat.S_IMODE(marker.stat().st_mode) != 0o600:
        raise AcceptanceRefusal("paid authorization permissions were not retained")
    return marker


def _extract_reviewed_context(
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, object], dict[str, object]]:
    if not isinstance(messages, list) or len(messages) != 2:
        raise AcceptanceRefusal("provider messages do not match the reviewed storyboard shape")
    if any(
        not isinstance(item, dict)
        or frozenset(item) != {"role", "content"}
        or not isinstance(item.get("content"), str)
        for item in messages
    ) or [item.get("role") for item in messages] != ["system", "user"]:
        raise AcceptanceRefusal("provider messages do not match the reviewed storyboard shape")
    user_row = messages[1]
    if not isinstance(user_row["content"], str):
        raise AcceptanceRefusal("provider messages do not contain one reviewed user context")
    prefix = "USER_PROMPT_JSON:"
    lines = user_row["content"].splitlines()
    prompt_rows = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
    if len(prompt_rows) != 1:
        raise AcceptanceRefusal("provider messages do not contain one bounded prompt")
    try:
        prompt = json.loads(prompt_rows[0])
    except json.JSONDecodeError as exc:
        raise AcceptanceRefusal("provider prompt encoding is invalid") from exc
    if not isinstance(prompt, str):
        raise AcceptanceRefusal("provider prompt encoding is invalid")

    def object_after(label: str) -> dict[str, object]:
        positions = [index for index, line in enumerate(lines) if line == label]
        if len(positions) != 1 or positions[0] + 1 >= len(lines):
            raise AcceptanceRefusal("provider context is outside the reviewed storyboard shape")
        try:
            value = json.loads(lines[positions[0] + 1])
        except json.JSONDecodeError as exc:
            raise AcceptanceRefusal("provider context encoding is invalid") from exc
        if not isinstance(value, dict):
            raise AcceptanceRefusal("provider context encoding is invalid")
        return value

    problem = object_after("BOUND_PROBLEM_JSON:")
    frontier = object_after("CURRENT_ACCEPTED_STORYBOARD_FRONTIER_JSON:")
    return prompt, problem, frontier


def _acceptance_reservations(
    attestation: paid_probe.AzureDeploymentAttestation,
) -> tuple[_AcceptanceReservation, ...]:
    max_input_tokens = attestation.token_limit_count - MAX_OUTPUT_TOKENS
    reserved_cost = (
        max_input_tokens * paid_probe.INPUT_NANO_USD_PER_TOKEN
        + MAX_OUTPUT_TOKENS * paid_probe.OUTPUT_NANO_USD_PER_TOKEN
    )
    return tuple(
        _AcceptanceReservation(
            reservation_id=f"authenticated-live-{index + 1}",
            max_input_tokens=max_input_tokens,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            reserved_cost_nano_usd=reserved_cost,
        )
        for index in range(MAX_PROVIDER_CALLS)
    )


class AuthenticatedStoryboardAcceptanceGuard:
    """One-shot two-call budget, source guard, quota pacer, and evidence ledger."""

    def __init__(
        self,
        *,
        source: AcceptanceSource,
        attestation: paid_probe.AzureDeploymentAttestation,
        output_path: Path,
        pacer: _Pacer,
        max_cost_nano_usd: int = AUTHORIZED_MAX_COST_NANO_USD,
        source_guard: Callable[[paid_probe.GitState], paid_probe.GitState] | None = None,
        authorization_consumer: Callable[[AcceptanceSource], Path] = _consume_authorization,
    ) -> None:
        if max_cost_nano_usd != AUTHORIZED_MAX_COST_NANO_USD:
            raise AcceptanceRefusal("live acceptance requires the reviewed USD 0.01 ceiling")
        if attestation.token_limit_count <= MAX_OUTPUT_TOKENS:
            raise AcceptanceRefusal("attested token window cannot admit the reviewed output cap")
        self.source = source
        self.attestation = attestation
        self.output_path = output_path
        self._pacer = pacer
        self._source_guard = source_guard or (
            lambda expected: paid_probe._assert_clean_pushed_head(expected)
        )
        self._authorization_consumer = authorization_consumer
        self._async_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._authorization_marker: Path | None = None
        self._pacer_admissions = 0
        self._provider_calls = 0
        self._admitted_message_hashes: dict[int, str] = {}
        self._stream_terminals: dict[int, str] = {}
        self._client_closes = 0
        self._post_attestation: paid_probe.AzureDeploymentAttestation | None = None
        self._post_attestation_failure: str | None = None
        self._server_stopped_at: str | None = None

        self._reservations = _acceptance_reservations(attestation)
        if self.reserved_max_cost_nano_usd > max_cost_nano_usd:
            raise AcceptanceRefusal("reviewed live subset exceeds its provider cost ceiling")
        self._write_evidence()

    @property
    def reserved_max_cost_nano_usd(self) -> int:
        return sum(item.reserved_cost_nano_usd for item in self._reservations)

    @property
    def provider_call_count(self) -> int:
        with self._state_lock:
            return self._provider_calls

    @property
    def authorization_consumed(self) -> bool:
        with self._state_lock:
            return self._authorization_marker is not None

    def _snapshot(self) -> dict[str, object]:
        with self._state_lock:
            admitted_cost = sum(
                item.reserved_cost_nano_usd for item in self._reservations[: self._pacer_admissions]
            )
            post_attestation = self._post_attestation
            return {
                "schemaVersion": 1,
                "generatedAt": datetime.now(UTC).isoformat(),
                "evidenceScope": EVIDENCE_SCOPE,
                **self.source.git.sanitized(),
                "authorizationIdSha256": self.source.authorization_sha256,
                "authorizationConsumed": self._authorization_marker is not None,
                "azureDeploymentAttestation": self.attestation.sanitized(),
                "postRunAzureDeploymentAttestation": (
                    None if post_attestation is None else post_attestation.sanitized()
                ),
                "postRunAzureDeploymentAttestationFailureCode": self._post_attestation_failure,
                "limits": {
                    "maxCostUsd": paid_probe._format_nano_usd(AUTHORIZED_MAX_COST_NANO_USD),
                    "maxOutputTokensPerCall": MAX_OUTPUT_TOKENS,
                    "maxProviderCalls": MAX_PROVIDER_CALLS,
                    "sdkMaxRetries": 0,
                    "repairCalls": 0,
                },
                "reservations": [
                    {
                        "reservationId": item.reservation_id,
                        "promptSha256": paid_probe._sha256_text(ALLOWED_PROMPTS[index]),
                        "messageSha256": self._admitted_message_hashes.get(index + 1),
                        "maxInputTokens": item.max_input_tokens,
                        "maxOutputTokens": item.max_output_tokens,
                        "reservedMaxCostUsd": paid_probe._format_nano_usd(
                            item.reserved_cost_nano_usd
                        ),
                    }
                    for index, item in enumerate(self._reservations)
                ],
                "admittedReservedMaxCostUsd": paid_probe._format_nano_usd(admitted_cost),
                "metrics": {
                    "pacerAdmissionCount": self._pacer_admissions,
                    "providerCallCount": self._provider_calls,
                    "streamTerminals": {
                        str(index): self._stream_terminals[index]
                        for index in sorted(self._stream_terminals)
                    },
                    "clientCloseCount": self._client_closes,
                    "deploymentAttestationStable": (
                        None if post_attestation is None else post_attestation == self.attestation
                    ),
                    "serverStoppedAt": self._server_stopped_at,
                },
                "costEvidence": "conservative_reserved_upper_bound_not_billed_usage",
            }

    def _write_evidence(self) -> None:
        snapshot = self._snapshot()
        serialized = _canonical_json(snapshot)
        forbidden = (
            self.source.authorization_id,
            os.getenv("AZURE_OPENAI_API_KEY", ""),
            os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        )
        if any(value and value in serialized for value in forbidden):
            raise AcceptanceRefusal("private evidence contains a configured secret")
        _atomic_private_replace(self.output_path, snapshot)

    async def before_director_dispatch(
        self,
        request: SemanticStoryboardDirectorRequestV1,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> None:
        async with self._async_lock:
            if max_tokens != MAX_OUTPUT_TOKENS:
                raise AcceptanceRefusal("provider parameters changed after reviewed preflight")
            if not isinstance(request, SemanticStoryboardDirectorRequestV1):
                raise AcceptanceRefusal("provider request is outside the reviewed live subset")
            expected_messages = build_semantic_storyboard_director_messages(
                request.prompt,
                request.problem_spec,
                request.base_semantic_scene,
            )
            if messages != expected_messages:
                raise AcceptanceRefusal("provider messages changed after reviewed construction")
            prompt, problem, frontier = _extract_reviewed_context(messages)
            with self._state_lock:
                ordinal = self._pacer_admissions
            if ordinal >= MAX_PROVIDER_CALLS:
                raise AcceptanceRefusal("the reviewed live acceptance budget is exhausted")
            if prompt != ALLOWED_PROMPTS[ordinal]:
                raise AcceptanceRefusal("provider prompt is outside the reviewed live subset")
            if problem != {"v": 1, "speedMps": 20, "anglesDeg": [30, 60]}:
                raise AcceptanceRefusal("provider problem is outside the reviewed live subset")
            request_prompt = getattr(request, "prompt", None)
            request_problem = getattr(request, "problem_spec", None)
            if request_prompt != prompt or not callable(
                getattr(request_problem, "model_dump", None)
            ):
                raise AcceptanceRefusal("provider request is outside the reviewed live subset")
            if request_problem.model_dump(mode="json", by_alias=True) != problem:
                raise AcceptanceRefusal("provider request is outside the reviewed live subset")
            accepted_records = frontier.get("acceptedRecords")
            revision = frontier.get("revision")
            if (
                frozenset(frontier) != {"revision", "acceptedRecords"}
                or not isinstance(accepted_records, list)
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision != len(accepted_records) + 1
                or (ordinal == 0 and accepted_records)
                or (ordinal == 1 and not 1 <= len(accepted_records) <= 5)
            ):
                raise AcceptanceRefusal("provider frontier is outside the reviewed live subset")
            input_bound = paid_probe._message_input_token_bound(messages)
            reservation = self._reservations[ordinal]
            if input_bound > reservation.max_input_tokens:
                raise AcceptanceRefusal("provider context exceeds the attested token window")
            message_sha256 = paid_probe._messages_sha256(messages)
            await asyncio.to_thread(self._source_guard, self.source.git)
            if ordinal == 0:
                marker = await asyncio.to_thread(self._authorization_consumer, self.source)
                with self._state_lock:
                    self._authorization_marker = marker
                self._write_evidence()
            await self._pacer.admit(reservation.bind_messages(message_sha256))
            with self._state_lock:
                self._pacer_admissions += 1
                self._admitted_message_hashes[self._pacer_admissions] = message_sha256
            self._write_evidence()
            await asyncio.to_thread(self._source_guard, self.source.git)

    def begin_provider_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> int:
        if temperature != 0.0 or max_tokens != MAX_OUTPUT_TOKENS:
            raise AcceptanceRefusal("provider parameters changed after reviewed preflight")
        message_sha256 = paid_probe._messages_sha256(messages)
        with self._state_lock:
            ordinal = self._provider_calls + 1
            if ordinal > self._pacer_admissions or ordinal > MAX_PROVIDER_CALLS:
                raise AcceptanceRefusal("provider stream has no admitted acceptance reservation")
            if self._admitted_message_hashes.get(ordinal) != message_sha256:
                raise AcceptanceRefusal("provider messages changed after reviewed admission")
            self._provider_calls = ordinal
        self._write_evidence()
        return ordinal

    def record_stream_terminal(self, ordinal: int, terminal: str) -> None:
        if terminal not in {"completed", "aborted", "failed"}:
            raise TypeError("terminal must be completed, aborted, or failed")
        with self._state_lock:
            self._stream_terminals.setdefault(ordinal, terminal)
        self._write_evidence()

    def record_client_close(self) -> None:
        with self._state_lock:
            self._client_closes += 1
        self._write_evidence()

    def finalize(
        self,
        *,
        post_attestation: paid_probe.AzureDeploymentAttestation | None,
        failure_code: str | None,
    ) -> None:
        with self._state_lock:
            self._post_attestation = post_attestation
            self._post_attestation_failure = failure_code
            self._server_stopped_at = datetime.now(UTC).isoformat()
        self._write_evidence()


class _ObservedStream:
    def __init__(
        self,
        upstream: AsyncIterator[str | bytes],
        guard: AuthenticatedStoryboardAcceptanceGuard,
        ordinal: int,
    ) -> None:
        self._upstream = upstream
        self._guard = guard
        self._ordinal = ordinal
        self._settled = False

    def __aiter__(self) -> _ObservedStream:
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return await anext(self._upstream)
        except StopAsyncIteration:
            self._settle("completed")
            raise
        except asyncio.CancelledError:
            self._settle("aborted")
            raise
        except BaseException:
            self._settle("failed")
            raise

    def _settle(self, terminal: str) -> None:
        if self._settled:
            return
        self._settled = True
        self._guard.record_stream_terminal(self._ordinal, terminal)

    async def aclose(self) -> None:
        close = getattr(self._upstream, "aclose", None)
        try:
            if close is not None:
                await close()
        finally:
            self._settle("aborted")


class GuardedAcceptanceClient:
    """Wrap one zero-retry provider client behind the shared acceptance ledger."""

    def __init__(
        self,
        delegate: _ProviderClient,
        guard: AuthenticatedStoryboardAcceptanceGuard,
    ) -> None:
        self._delegate = delegate
        self._guard = guard
        self._closed = False

    def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        **kwargs: object,
    ) -> AsyncIterator[str | bytes]:
        if kwargs:
            raise AcceptanceRefusal("provider options changed after reviewed preflight")
        ordinal = self._guard.begin_provider_stream(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            upstream = self._delegate.stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except BaseException:
            self._guard.record_stream_terminal(ordinal, "failed")
            raise
        return _ObservedStream(upstream, self._guard, ordinal)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._delegate.aclose()
        finally:
            self._guard.record_client_close()


def _validate_private_env_file(raw_path: str) -> Path:
    unresolved = Path(raw_path).expanduser()
    if unresolved.is_symlink():
        raise AcceptanceRefusal("--env-file must name a regular non-symlink file")
    path = unresolved.resolve()
    if not path.is_file():
        raise AcceptanceRefusal("--env-file must name a regular non-symlink file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AcceptanceRefusal("--env-file must be owner-only mode 0600")
    paid_probe._load_env_file(str(path))
    return path


def _validate_firebase_credentials() -> None:
    raw_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
    if not raw_path:
        raise AcceptanceRefusal("Firebase service-account credentials are unavailable")
    unresolved = Path(raw_path).expanduser()
    if unresolved.is_symlink():
        raise AcceptanceRefusal("Firebase service-account credentials are unsafe")
    path = unresolved.resolve()
    if not path.is_file():
        raise AcceptanceRefusal("Firebase service-account credentials are unsafe")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise AcceptanceRefusal("Firebase service-account credentials must be mode 0600")


def _validate_cli(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "::1"}:
        raise AcceptanceRefusal("authenticated acceptance must bind only to loopback")
    if args.frontend_origin not in {
        "http://127.0.0.1:3102",
        "http://localhost:3102",
    }:
        raise AcceptanceRefusal("authenticated acceptance requires the reviewed local frontend")
    if not 1 <= args.port <= 65_535:
        raise AcceptanceRefusal("--port must be between 1 and 65535")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise AcceptanceRefusal("--timeout-seconds must be finite and positive")
    if args.acknowledge_paid_provider != PROVIDER_COST_ACKNOWLEDGEMENT:
        raise AcceptanceRefusal("live acceptance requires the exact provider-cost acknowledgement")
    if (
        not ACTIVE_LIVE_ACCEPTANCE_AUTHORIZATION_ID
        or args.authorization_id != ACTIVE_LIVE_ACCEPTANCE_AUTHORIZATION_ID
    ):
        raise AcceptanceRefusal("no matching fresh live-acceptance authorization is active")
    max_cost = paid_probe._parse_budget_nano_usd(args.max_cost_usd)
    if max_cost != AUTHORIZED_MAX_COST_NANO_USD:
        raise AcceptanceRefusal("live acceptance requires the reviewed USD 0.01 ceiling")
    return max_cost


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--max-cost-usd", required=True)
    parser.add_argument("--acknowledge-paid-provider", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--frontend-origin", default="http://127.0.0.1:3102")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    guard: AuthenticatedStoryboardAcceptanceGuard | None = None
    endpoint = ""
    deployment = ""
    try:
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"
        _validate_private_env_file(args.env_file)
        os.environ["ALLOWED_CORS_ORIGINS"] = args.frontend_origin
        max_cost = _validate_cli(args)
        _validate_firebase_credentials()
        source = AcceptanceSource(
            git=paid_probe._assert_clean_pushed_head(),
            authorization_id=args.authorization_id,
        )
        output = _safe_output_path(args.output)
        endpoint, deployment = paid_probe._configured_azure_target()
        attestation = paid_probe._attest_azure_deployment(endpoint, deployment)
        quota = paid_probe._dispatch_quota_from_attestation(
            attestation,
            minimum_start_interval_seconds=0.0,
        )
        reservations = _acceptance_reservations(attestation)
        paid_probe._plan_dispatch_schedule(reservations, quota)
        pacer = paid_probe.DispatchPacer(quota)
        guard = AuthenticatedStoryboardAcceptanceGuard(
            source=source,
            attestation=attestation,
            output_path=output,
            pacer=pacer,
            max_cost_nano_usd=max_cost,
        )

        from murmur.api.application import create_application
        from murmur.live_scene import SceneAuthoringService

        provider_factory = paid_probe._provider_factory(attestation)

        def create_guarded_client() -> GuardedAcceptanceClient:
            return GuardedAcceptanceClient(provider_factory(), guard)

        async def reject_unreviewed_provider_dispatch() -> None:
            from murmur.live_scene import SceneAdmissionError

            raise SceneAdmissionError(
                "provider_budget_exhausted",
                "Only the reviewed semantic-storyboard acceptance flow is enabled.",
            )

        service = SceneAuthoringService(
            client_factory=create_guarded_client,
            temperature=0.0,
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout_seconds=args.timeout_seconds,
            before_provider_dispatch=reject_unreviewed_provider_dispatch,
            semantic_storyboard_before_provider_dispatch=guard.before_director_dispatch,
        )
        app = create_application(
            scene_authoring_service=service,
            scene_authoring_enabled=True,
        )

        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port, reload=False, access_log=False)
        return 0
    except AcceptanceRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except paid_probe.ProbeRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        if guard is not None:
            post_attestation = None
            failure_code = None
            if guard.authorization_consumed:
                try:
                    paid_probe._assert_clean_pushed_head(guard.source.git)
                    post_attestation = paid_probe._attest_azure_deployment(
                        endpoint,
                        deployment,
                    )
                except Exception:
                    failure_code = "post_run_attestation_failed"
            guard.finalize(
                post_attestation=post_attestation,
                failure_code=failure_code,
            )


if __name__ == "__main__":
    raise SystemExit(main())
