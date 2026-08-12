"""Provider-free tests for the minimal LiveKit Voice V2 worker seam."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from livekit import rtc
from livekit.agents import AgentServer
from murmur.core.config import config
from murmur.persistence.models import AgentModel, SessionModel
from murmur.voice.blocking import BoundedSyncRunner
from murmur.voice.bootstrap import (
    VOICE_V2_EVENT_TOPIC,
    CreateDispatchSpec,
    CreateRoomSpec,
    DispatchRecord,
    ParticipantTokenSpec,
    RoomRecord,
    VoiceBootstrapService,
    VoiceBootstrapSettings,
    sign_metadata,
)
from murmur.voice.profile import (
    DeterministicVoiceProfileProvider,
    PreparedVoiceProfile,
    ProfilePreflight,
    VoiceProfileRegistry,
)

_original_signing_secret = config.VOICE_V2_SIGNING_SECRET
try:
    config.VOICE_V2_SIGNING_SECRET = "worker-signing-secret-that-is-long-enough"
    _worker = importlib.import_module("murmur.voice.worker")
finally:
    config.VOICE_V2_SIGNING_SECRET = _original_signing_secret

AgentSessionOwner = _worker.AgentSessionOwner
VoiceJobAuthorizer = _worker.VoiceJobAuthorizer
VoiceJobRejected = _worker.VoiceJobRejected
VoiceSessionLifecycleError = _worker.VoiceSessionLifecycleError
VoiceWorkerSettings = _worker.VoiceWorkerSettings
build_agent_server = _worker.build_agent_server
build_entrypoint = _worker.build_entrypoint
build_request_handler = _worker.build_request_handler
parse_job_metadata = _worker.parse_job_metadata

SECRET = "worker-signing-secret-that-is-long-enough"
PROFILE_ID = "livekit-agents-cascade-v1"
WORKER_NAME = "murmur-voice-v2"
AGENT_IDENTITY = "agent-a1b2c3"
PARTICIPANT_IDENTITY = "user-a1b2c3"
FIXED_NOW = datetime(2026, 8, 12, 8, 1, tzinfo=UTC)
FIXED_NOW_EPOCH = int(FIXED_NOW.timestamp())


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "agent_id": "agent-1",
        "agent_participant_identity": AGENT_IDENTITY,
        "environment": "test",
        "event_topic": VOICE_V2_EVENT_TOPIC,
        "job_expires_at": FIXED_NOW_EPOCH + 300,
        "job_issued_at": FIXED_NOW_EPOCH,
        "participant_identity": PARTICIPANT_IDENTITY,
        "profile_id": PROFILE_ID,
        "room_name": "murmur-test-room-1",
        "runtime": "livekit_v2",
        "session_id": "session-1",
        "trace_id": "trace-1",
        "user_id": "user-1",
        "voice_call_id": "call-1",
        "worker_name": WORKER_NAME,
    }
    values.update(overrides)
    return values


def _job(*, payload: dict[str, object] | None = None, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "metadata": sign_metadata(payload or _payload(), SECRET, purpose="job"),
        "agent_name": WORKER_NAME,
        "room": SimpleNamespace(name="murmur-test-room-1"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _settings(**overrides: object) -> VoiceWorkerSettings:
    values: dict[str, object] = {
        "signing_secret": SECRET,
        "environment": "test",
        "profile_id": PROFILE_ID,
        "worker_name": WORKER_NAME,
        "event_topic": VOICE_V2_EVENT_TOPIC,
        "job_metadata_ttl_seconds": 300,
        "job_metadata_clock_skew_seconds": 30,
        "repository_timeout_seconds": 0.05,
        "participant_wait_timeout_seconds": 0.01,
        "cleanup_timeout_seconds": 0.05,
        "interruption_timeout_seconds": 0.01,
    }
    values.update(overrides)
    return VoiceWorkerSettings(**values)  # type: ignore[arg-type]


class FakeSessionRepo:
    record: SessionModel | None = SessionModel(
        id="session-1",
        user_id="user-1",
        agent_id="agent-1",
    )
    calls = 0

    @classmethod
    def get_by_id(cls, session_id: str) -> SessionModel | None:
        cls.calls += 1
        assert session_id == "session-1"
        return cls.record


class FakeAgentRepo:
    record: AgentModel | None = AgentModel(
        id="agent-1",
        user_id="user-1",
        name="Tutor",
        system_prompt="Answer briefly.",
    )
    calls = 0

    @classmethod
    def get_by_id(cls, agent_id: str) -> AgentModel | None:
        cls.calls += 1
        assert agent_id == "agent-1"
        return cls.record


class BlockingFirstSessionRepo:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self._guard = threading.Lock()

    def get_by_id(self, session_id: str) -> SessionModel | None:
        assert session_id == "session-1"
        with self._guard:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.entered.set()
            self.release.wait()
            self.finished.set()
        return FakeSessionRepo.record


class FailingSessionRepo:
    @staticmethod
    def get_by_id(session_id: str) -> SessionModel | None:
        del session_id
        raise RuntimeError("database unavailable")


class BootstrapMetadataControlPlane:
    def __init__(self) -> None:
        self.room: RoomRecord | None = None
        self.dispatch: DispatchRecord | None = None

    async def get_room(self, room_name: str) -> RoomRecord | None:
        if self.room is not None:
            assert self.room.name == room_name
        return self.room

    async def create_room(self, spec: CreateRoomSpec) -> RoomRecord:
        self.room = RoomRecord(name=spec.name, metadata=spec.metadata)
        return self.room

    async def list_dispatches(self, room_name: str) -> list[DispatchRecord]:
        if self.dispatch is None:
            return []
        assert self.dispatch.room_name == room_name
        return [self.dispatch]

    async def create_dispatch(self, spec: CreateDispatchSpec) -> DispatchRecord:
        self.dispatch = DispatchRecord(
            id="dispatch-1",
            room_name=spec.room_name,
            agent_name=spec.agent_name,
            metadata=spec.metadata,
        )
        return self.dispatch

    def issue_participant_token(self, spec: ParticipantTokenSpec) -> str:
        del spec
        return "participant-token"


async def _wait_for_thread_event(event: threading.Event) -> None:
    observed = await asyncio.wait_for(asyncio.to_thread(event.wait, 1.0), timeout=1.1)
    assert observed


async def _wait_for_runner_idle(runner: BoundedSyncRunner) -> None:
    for _ in range(1_000):
        if runner.inflight_count == 0:
            return
        await asyncio.sleep(0)
    pytest.fail("bounded sync runner did not become idle")


@pytest.fixture(autouse=True)
def _reset_repositories() -> None:
    FakeSessionRepo.record = SessionModel(
        id="session-1",
        user_id="user-1",
        agent_id="agent-1",
    )
    FakeAgentRepo.record = AgentModel(
        id="agent-1",
        user_id="user-1",
        name="Tutor",
        system_prompt="Answer briefly.",
    )
    FakeSessionRepo.calls = 0
    FakeAgentRepo.calls = 0


def _authorizer() -> VoiceJobAuthorizer:
    return VoiceJobAuthorizer(
        _settings(),
        session_repo=FakeSessionRepo,
        agent_repo=FakeAgentRepo,
        clock=lambda: FIXED_NOW,
    )


def test_job_metadata_rejects_tampering_wrong_purpose_and_extra_fields() -> None:
    encoded = sign_metadata(_payload(), SECRET, purpose="job")
    envelope = json.loads(encoded)
    envelope["payload"]["user_id"] = "attacker"

    with pytest.raises(VoiceJobRejected, match="signature"):
        parse_job_metadata(json.dumps(envelope), SECRET)
    with pytest.raises(VoiceJobRejected, match="signature"):
        parse_job_metadata(sign_metadata(_payload(), SECRET, purpose="room"), SECRET)
    with pytest.raises(VoiceJobRejected, match="unexpected fields"):
        parse_job_metadata(
            sign_metadata({**_payload(), "future": "field"}, SECRET, purpose="job"),
            SECRET,
        )
    assert (
        parse_job_metadata(
            sign_metadata(_payload(user_id="contains spaces"), SECRET, purpose="job"),
            SECRET,
        ).user_id
        == "contains spaces"
    )
    with pytest.raises(VoiceJobRejected, match="field job_issued_at"):
        parse_job_metadata(
            sign_metadata(_payload(job_issued_at="1786521660"), SECRET, purpose="job"),
            SECRET,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"job_issued_at": FIXED_NOW_EPOCH + 31}, "issued in the future"),
        (
            {
                "job_issued_at": FIXED_NOW_EPOCH - 330,
                "job_expires_at": FIXED_NOW_EPOCH - 30,
            },
            "has expired",
        ),
        (
            {
                "job_issued_at": FIXED_NOW_EPOCH - 1,
                "job_expires_at": FIXED_NOW_EPOCH + 300,
            },
            "overlong",
        ),
        ({"job_expires_at": FIXED_NOW_EPOCH - 1}, "time window is invalid"),
    ],
)
async def test_authorizer_rejects_invalid_time_window_before_repo_io(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(VoiceJobRejected, match=message):
        await _authorizer().authorize(_job(payload=_payload(**overrides)))

    assert FakeSessionRepo.calls == 0
    assert FakeAgentRepo.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {
            "job_issued_at": FIXED_NOW_EPOCH + 30,
            "job_expires_at": FIXED_NOW_EPOCH + 300,
        },
        {
            "job_issued_at": FIXED_NOW_EPOCH - 329,
            "job_expires_at": FIXED_NOW_EPOCH - 29,
        },
    ],
    ids=["future-at-skew-boundary", "expiry-inside-skew-boundary"],
)
async def test_authorizer_accepts_time_inside_clock_skew_boundary(
    overrides: dict[str, object],
) -> None:
    authorized = await _authorizer().authorize(_job(payload=_payload(**overrides)))

    assert authorized.metadata.session_id == "session-1"
    assert FakeSessionRepo.calls == 1
    assert FakeAgentRepo.calls == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_metadata_clock_skew_seconds": -1},
        {"job_metadata_clock_skew_seconds": 61},
        {
            "job_metadata_ttl_seconds": 30,
            "job_metadata_clock_skew_seconds": 31,
        },
    ],
)
def test_worker_clock_skew_setting_is_bounded(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="clock skew"):
        _settings(**overrides)


@pytest.mark.parametrize(
    "repository_timeout_seconds",
    [0, -1, 31, float("inf"), float("nan")],
)
def test_worker_repository_timeout_is_finite_and_bounded(
    repository_timeout_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="repository timeout"):
        _settings(repository_timeout_seconds=repository_timeout_seconds)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("preflight_timeout_seconds", 0, "preflight timeout"),
        ("connect_timeout_seconds", 31, "connect timeout"),
        ("session_start_timeout_seconds", float("inf"), "session-start timeout"),
        ("event_publish_timeout_seconds", float("nan"), "event-publish timeout"),
        ("input_wait_timeout_seconds", 61, "input wait timeout"),
    ],
)
def test_worker_startup_timeouts_are_finite_and_bounded(
    field: str,
    value: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**{field: value})


@pytest.mark.asyncio
async def test_authorizer_bounds_stalled_repository_and_does_not_start_next_lookup() -> None:
    session_repo = BlockingFirstSessionRepo()
    runner = BoundedSyncRunner(max_workers=1, thread_name_prefix="worker-repo-test")
    authorizer = VoiceJobAuthorizer(
        _settings(repository_timeout_seconds=0.01),
        session_repo=session_repo,
        agent_repo=FakeAgentRepo,
        repository_runner=runner,
        clock=lambda: FIXED_NOW,
    )
    try:
        with pytest.raises(VoiceJobRejected, match="repository lookup timed out"):
            await authorizer.authorize(_job())

        assert session_repo.entered.is_set()
        assert FakeAgentRepo.calls == 0

        with pytest.raises(VoiceJobRejected, match="repository capacity"):
            await authorizer.authorize(_job())
        assert session_repo.calls == 1
        assert FakeAgentRepo.calls == 0
    finally:
        session_repo.release.set()
        await _wait_for_thread_event(session_repo.finished)
        await _wait_for_runner_idle(runner)

    authorized = await authorizer.authorize(_job())
    assert authorized.metadata.session_id == "session-1"
    assert FakeAgentRepo.calls == 1
    await runner.aclose()


@pytest.mark.asyncio
async def test_authorizer_normalizes_unexpected_repository_failure() -> None:
    authorizer = VoiceJobAuthorizer(
        _settings(),
        session_repo=FailingSessionRepo,
        agent_repo=FakeAgentRepo,
        clock=lambda: FIXED_NOW,
    )

    with pytest.raises(VoiceJobRejected, match="repository lookup failed"):
        await authorizer.authorize(_job())


@pytest.mark.parametrize("user_id", ["", "u" * 129])
def test_worker_rejects_invalid_opaque_user_locator(user_id: str) -> None:
    with pytest.raises(VoiceJobRejected, match="field user_id"):
        parse_job_metadata(
            sign_metadata(_payload(user_id=user_id), SECRET, purpose="job"),
            SECRET,
        )


@pytest.mark.asyncio
async def test_bootstrap_metadata_round_trips_opaque_firebase_uid_to_worker() -> None:
    firebase_uid = "learner+voice@example.com-雪"
    session = SessionModel(id="session-1", user_id=firebase_uid, agent_id="agent-1")
    agent = AgentModel(
        id="agent-1",
        user_id=firebase_uid,
        name="Tutor",
        system_prompt="Answer briefly.",
    )
    control_plane = BootstrapMetadataControlPlane()
    service = VoiceBootstrapService(
        control_plane,
        VoiceBootstrapSettings(
            server_url="wss://murmur-test.livekit.cloud",
            environment="test",
            profile_id=PROFILE_ID,
            worker_name=WORKER_NAME,
            event_topic="murmur.voice.v2.events",
            signing_secret=SECRET,
        ),
        session_repo=SimpleNamespace(get_by_id=lambda _: session),
        agent_repo=SimpleNamespace(get_by_id=lambda _: agent),
        clock=lambda: FIXED_NOW,
    )

    result = await service.bootstrap(
        user_id=firebase_uid,
        session_id="session-1",
        voice_call_id="call-1",
    )

    assert control_plane.dispatch is not None
    parsed = parse_job_metadata(control_plane.dispatch.metadata, SECRET)
    assert parsed.user_id == firebase_uid
    assert result.participant_identity.startswith("user-")


def test_default_worker_wires_repository_timeout_without_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "VOICE_V2_SIGNING_SECRET", SECRET)
    monkeypatch.setattr(config, "VOICE_V2_REPOSITORY_TIMEOUT_SECONDS", 1.25)
    monkeypatch.setattr(
        config,
        "VOICE_V2_EVENT_TOPIC",
        "attacker.worker.topic",
        raising=False,
    )

    settings, _profiles = _worker._default_worker()

    assert settings.repository_timeout_seconds == 1.25
    assert settings.event_topic == VOICE_V2_EVENT_TOPIC


def test_worker_settings_reject_noncanonical_event_topic() -> None:
    with pytest.raises(ValueError, match="event_topic must be"):
        _settings(event_topic="murmur.voice.v2.custom")


@pytest.mark.asyncio
async def test_default_bootstrap_and_worker_trim_shared_contract_settings_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from murmur.voice import livekit_control

    firebase_uid = "learner+voice@example.com-雪"
    session = SessionModel(id="session-1", user_id=firebase_uid, agent_id="agent-1")
    agent = AgentModel(
        id="agent-1",
        user_id=firebase_uid,
        name="Tutor",
        system_prompt="Answer briefly.",
    )
    control_plane = BootstrapMetadataControlPlane()
    shared = {
        "VOICE_V2_SIGNING_SECRET": f"  {SECRET}  ",
        "MURMUR_ENVIRONMENT": "  test  ",
        "VOICE_V2_PROFILE_ID": f"  {PROFILE_ID}  ",
        "VOICE_V2_WORKER_NAME": f"  {WORKER_NAME}  ",
    }
    for target in (config, livekit_control.config):
        for name, value in shared.items():
            monkeypatch.setattr(target, name, value)
    monkeypatch.setattr(
        config,
        "VOICE_V2_EVENT_TOPIC",
        "attacker.shared.topic",
        raising=False,
    )
    monkeypatch.setattr(livekit_control.config, "VOICE_RUNTIME", "livekit_v2")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_URL", "  wss://example.test  ")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_KEY", "test-key")
    monkeypatch.setattr(livekit_control.config, "LIVEKIT_API_SECRET", "test-secret")
    monkeypatch.setattr(
        livekit_control,
        "LiveKitControlPlane",
        lambda credentials: control_plane,
    )
    configured_bootstrap = livekit_control.create_default_voice_bootstrap_service()
    assert isinstance(configured_bootstrap, VoiceBootstrapService)
    bootstrap = VoiceBootstrapService(
        control_plane,
        configured_bootstrap.settings,
        session_repo=SimpleNamespace(get_by_id=lambda _: session),
        agent_repo=SimpleNamespace(get_by_id=lambda _: agent),
        clock=lambda: FIXED_NOW,
    )
    result = await bootstrap.bootstrap(
        user_id=firebase_uid,
        session_id="session-1",
        voice_call_id="call-1",
    )
    assert control_plane.dispatch is not None

    worker_settings, _profiles = _worker._default_worker()
    authorized = await VoiceJobAuthorizer(
        worker_settings,
        session_repo=SimpleNamespace(get_by_id=lambda _: session),
        agent_repo=SimpleNamespace(get_by_id=lambda _: agent),
        clock=lambda: FIXED_NOW,
    ).authorize(
        SimpleNamespace(
            metadata=control_plane.dispatch.metadata,
            agent_name=WORKER_NAME,
            room=SimpleNamespace(name=result.room_name),
        )
    )
    parsed = authorized.metadata

    assert worker_settings.environment == parsed.environment == "test"
    assert worker_settings.profile_id == parsed.profile_id == PROFILE_ID
    assert worker_settings.worker_name == parsed.worker_name == WORKER_NAME
    assert worker_settings.event_topic == parsed.event_topic == VOICE_V2_EVENT_TOPIC


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job",
    [
        _job(room=SimpleNamespace(name="wrong-room")),
        _job(payload=_payload(profile_id="wrong-profile")),
        _job(payload=_payload(worker_name="wrong-worker")),
        _job(agent_name="wrong-worker"),
        _job(payload=_payload(environment="production")),
    ],
    ids=["room", "profile", "signed-worker", "dispatch-worker", "environment"],
)
async def test_authorizer_rejects_assignment_mismatch_before_repo_reload(
    job: SimpleNamespace,
) -> None:
    with pytest.raises(VoiceJobRejected):
        await _authorizer().authorize(job)

    assert FakeSessionRepo.calls == 0
    assert FakeAgentRepo.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["session-user", "session-agent", "agent-user"])
async def test_authorizer_reloads_and_rejects_authoritative_ownership_mismatch(
    mismatch: str,
) -> None:
    if mismatch == "session-user":
        FakeSessionRepo.record = SessionModel(
            id="session-1", user_id="other-user", agent_id="agent-1"
        )
    elif mismatch == "session-agent":
        FakeSessionRepo.record = SessionModel(
            id="session-1", user_id="user-1", agent_id="other-agent"
        )
    else:
        FakeAgentRepo.record = AgentModel(
            id="agent-1",
            user_id="other-user",
            name="Other",
            system_prompt="No.",
        )

    with pytest.raises(VoiceJobRejected, match="ownership"):
        await _authorizer().authorize(_job())

    assert FakeSessionRepo.calls == 1
    assert FakeAgentRepo.calls == 1


class FakeRequest:
    def __init__(self, job: SimpleNamespace) -> None:
        self.job = job
        self.accepted: dict[str, str] | None = None
        self.rejected: bool | None = None

    async def accept(self, **kwargs: str) -> None:
        self.accepted = kwargs

    async def reject(self, *, terminate: bool) -> None:
        self.rejected = terminate


class HangingProfileProvider:
    def __init__(self, *, stage: str) -> None:
        self.stage = stage
        self.cancelled = False

    async def preflight(self, scope: object) -> ProfilePreflight:
        del scope
        if self.stage == "preflight":
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return ProfilePreflight(
            profile_id=PROFILE_ID,
            required_components=("stt", "llm", "tts"),
            ready_components=("stt", "llm", "tts"),
        )

    async def prepare(self, scope: object) -> PreparedVoiceProfile:
        del scope
        if self.stage == "prepare":
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return PreparedVoiceProfile(
            profile_id=PROFILE_ID,
            instructions="Answer briefly.",
            stt=object(),
            llm=object(),
            tts=object(),
        )


@pytest.mark.asyncio
async def test_request_preflight_controls_availability_and_uses_signed_agent_identity() -> None:
    unavailable = DeterministicVoiceProfileProvider(
        PROFILE_ID,
        fail_preflight="provider key is invalid",
    )
    handler = build_request_handler(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: unavailable}),
        _settings(),
    )
    rejected = FakeRequest(_job())

    await handler(rejected)  # type: ignore[arg-type]

    assert rejected.rejected is True
    assert rejected.accepted is None
    assert unavailable.prepare_calls == 0

    available = DeterministicVoiceProfileProvider(PROFILE_ID)
    handler = build_request_handler(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: available}),
        _settings(),
    )
    accepted = FakeRequest(_job())

    await handler(accepted)  # type: ignore[arg-type]

    assert accepted.rejected is None
    assert accepted.accepted == {
        "name": "Murmur voice agent",
        "identity": AGENT_IDENTITY,
    }
    assert available.preflight_calls == 1
    assert available.prepare_calls == 0


@pytest.mark.asyncio
async def test_request_rejects_when_profile_preflight_times_out() -> None:
    provider = HangingProfileProvider(stage="preflight")
    handler = build_request_handler(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: provider}),  # type: ignore[dict-item]
        _settings(preflight_timeout_seconds=0.001),
    )
    request = FakeRequest(_job())

    await handler(request)  # type: ignore[arg-type]

    assert request.rejected is True
    assert request.accepted is None
    assert provider.cancelled is True


@pytest.mark.asyncio
async def test_expiry_is_checked_at_request_and_rechecked_at_entrypoint() -> None:
    current_time = [FIXED_NOW]
    authorizer = VoiceJobAuthorizer(
        _settings(),
        session_repo=FakeSessionRepo,
        agent_repo=FakeAgentRepo,
        clock=lambda: current_time[0],
    )
    provider = DeterministicVoiceProfileProvider(PROFILE_ID)
    profiles = VoiceProfileRegistry({PROFILE_ID: provider})
    handler = build_request_handler(authorizer, profiles, _settings())

    expired_request = FakeRequest(
        _job(
            payload=_payload(
                job_issued_at=FIXED_NOW_EPOCH - 330,
                job_expires_at=FIXED_NOW_EPOCH - 30,
            )
        )
    )
    await handler(expired_request)  # type: ignore[arg-type]
    assert expired_request.rejected is True
    assert expired_request.accepted is None

    accepted_request = FakeRequest(_job())
    await handler(accepted_request)  # type: ignore[arg-type]
    assert accepted_request.accepted is not None

    current_time[0] = datetime.fromtimestamp(FIXED_NOW_EPOCH + 330, tz=UTC)
    events: list[str] = []
    entrypoint = build_entrypoint(authorizer, profiles, _settings())
    with pytest.raises(VoiceJobRejected, match="has expired"):
        await entrypoint(FakeContext(_job(), events))  # type: ignore[arg-type]

    assert events == []
    assert provider.prepare_calls == 0


class FakeOwnedSession:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
        start_waits_forever: bool = False,
    ) -> None:
        self.events = events
        self.fail_start = fail_start
        self.start_waits_forever = start_waits_forever
        self.shutdown_calls: list[bool] = []
        self.close_calls = 0
        self.never_interrupt = False
        self.room_options: object | None = None

    async def start(
        self,
        agent: object,
        *,
        room: object,
        room_options: object,
    ) -> None:
        del agent, room
        self.room_options = room_options
        self.events.append("session_start")
        if self.fail_start:
            raise RuntimeError("start failed")
        if self.start_waits_forever:
            await asyncio.Future()

    async def interrupt(self, *, force: bool = False) -> None:
        self.events.append(f"interrupt:{force}")
        if self.never_interrupt:
            await asyncio.Future()

    def shutdown(self, *, drain: bool = True) -> None:
        self.shutdown_calls.append(drain)

    async def aclose(self) -> None:
        self.close_calls += 1
        self.events.append("session_close")


class FakeTrackPublication:
    def __init__(self, source: object, track: object | None) -> None:
        self.source = source
        self.track = track


class FakeParticipant:
    def __init__(
        self,
        identity: str = PARTICIPANT_IDENTITY,
        publications: dict[str, FakeTrackPublication] | None = None,
    ) -> None:
        self.identity = identity
        self.track_publications = (
            publications
            if publications is not None
            else {
                "microphone": FakeTrackPublication(
                    rtc.TrackSource.SOURCE_MICROPHONE,
                    object(),
                )
            }
        )


class FakeRoom:
    def __init__(self) -> None:
        self.listeners: dict[str, list[object]] = {}

    def on(self, event: str, callback: object) -> object:
        self.listeners.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: object) -> None:
        self.listeners[event].remove(callback)

    def emit(self, event: str, *args: object) -> None:
        for callback in list(self.listeners.get(event, [])):
            callback(*args)  # type: ignore[operator]

    def listener_count(self, event: str) -> int:
        return len(self.listeners.get(event, []))


class FakeContext:
    def __init__(
        self,
        job: SimpleNamespace,
        events: list[str],
        *,
        participant_waits_forever: bool = False,
        connect_waits_forever: bool = False,
        participant: FakeParticipant | None = None,
    ) -> None:
        self.job = job
        self.room = FakeRoom()
        self.events = events
        self.shutdown_callbacks: list[object] = []
        self.participant_waits_forever = participant_waits_forever
        self.connect_waits_forever = connect_waits_forever
        self.participant = participant or FakeParticipant()
        self.waited_for_identity: str | None = None

    async def connect(self, *, auto_subscribe: object) -> None:
        del auto_subscribe
        self.events.append("connect")
        if self.connect_waits_forever:
            await asyncio.Future()

    async def wait_for_participant(self, *, identity: str) -> FakeParticipant:
        self.waited_for_identity = identity
        self.events.append("participant_joined")
        if self.participant_waits_forever:
            await asyncio.Future()
        return self.participant

    def add_shutdown_callback(self, callback: object) -> None:
        self.shutdown_callbacks.append(callback)


async def _wait_for_track_listener(room: FakeRoom) -> None:
    for _ in range(1_000):
        if room.listener_count("track_subscribed") == 1:
            return
        await asyncio.sleep(0)
    pytest.fail("microphone listener was not registered")


@pytest.mark.asyncio
async def test_entrypoint_publishes_genuine_ready_only_after_preflight_connect_and_start() -> None:
    events: list[str] = []
    provider = DeterministicVoiceProfileProvider(PROFILE_ID)
    sessions: list[FakeOwnedSession] = []

    def session_factory(prepared: object) -> tuple[FakeOwnedSession, object]:
        del prepared
        events.append("session_construct")
        session = FakeOwnedSession(events)
        sessions.append(session)
        return session, object()

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: provider}),
        _settings(),
        session_factory=session_factory,  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )
    ctx = FakeContext(_job(), events)

    await entrypoint(ctx)  # type: ignore[arg-type]

    assert provider.preflight_calls == 1
    assert provider.prepare_calls == 1
    assert events == [
        "session_construct",
        "connect",
        "participant_joined",
        "session_start",
        "ready",
    ]
    assert len(sessions) == 1
    assert len(ctx.shutdown_callbacks) == 1
    assert ctx.waited_for_identity == PARTICIPANT_IDENTITY
    assert sessions[0].room_options.participant_identity == PARTICIPANT_IDENTITY
    assert sessions[0].room_options.text_input is False


@pytest.mark.asyncio
async def test_entrypoint_prepare_timeout_never_constructs_or_readies() -> None:
    events: list[str] = []
    provider = HangingProfileProvider(stage="prepare")
    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: provider}),  # type: ignore[dict-item]
        _settings(preflight_timeout_seconds=0.001),
        session_factory=lambda _: (FakeOwnedSession(events), object()),  # type: ignore[arg-type]
        ready_publisher=lambda *_: asyncio.Future(),  # type: ignore[arg-type]
    )

    with pytest.raises(TimeoutError):
        await entrypoint(FakeContext(_job(), events))  # type: ignore[arg-type]

    assert events == []
    assert provider.cancelled is True


@pytest.mark.asyncio
async def test_entrypoint_connect_timeout_closes_prepared_profile_and_session() -> None:
    events: list[str] = []
    provider_closed = 0

    async def close_provider() -> None:
        nonlocal provider_closed
        provider_closed += 1

    session = FakeOwnedSession(events)
    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry(
            {
                PROFILE_ID: DeterministicVoiceProfileProvider(
                    PROFILE_ID,
                    close_callback=close_provider,
                )
            }
        ),
        _settings(connect_timeout_seconds=0.001),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
    )

    with pytest.raises(TimeoutError):
        await entrypoint(
            FakeContext(_job(), events, connect_waits_forever=True)  # type: ignore[arg-type]
        )

    assert "session_start" not in events
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1
    assert provider_closed == 1


@pytest.mark.asyncio
async def test_entrypoint_requires_exact_subscribed_microphone_before_ready() -> None:
    events: list[str] = []
    participant = FakeParticipant(publications={})
    session = FakeOwnedSession(events)

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: DeterministicVoiceProfileProvider(PROFILE_ID)}),
        _settings(input_wait_timeout_seconds=0.1),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )
    ctx = FakeContext(_job(), events, participant=participant)
    task = asyncio.create_task(entrypoint(ctx))  # type: ignore[arg-type]
    await _wait_for_track_listener(ctx.room)

    wrong_participant = FakeParticipant(identity="someone-else", publications={})
    wrong_participant_mic = FakeTrackPublication(
        rtc.TrackSource.SOURCE_MICROPHONE,
        object(),
    )
    ctx.room.emit(
        "track_subscribed",
        wrong_participant_mic.track,
        wrong_participant_mic,
        wrong_participant,
    )
    camera = FakeTrackPublication(rtc.TrackSource.SOURCE_CAMERA, object())
    ctx.room.emit("track_subscribed", camera.track, camera, participant)
    await asyncio.sleep(0)
    assert task.done() is False
    assert "session_start" not in events

    microphone = FakeTrackPublication(rtc.TrackSource.SOURCE_MICROPHONE, object())
    participant.track_publications["microphone"] = microphone
    ctx.room.emit("track_subscribed", microphone.track, microphone, participant)
    await task

    assert events[-2:] == ["session_start", "ready"]
    assert ctx.room.listener_count("track_subscribed") == 0


@pytest.mark.asyncio
async def test_entrypoint_input_timeout_never_starts_or_readies_and_removes_listener() -> None:
    events: list[str] = []
    session = FakeOwnedSession(events)

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: DeterministicVoiceProfileProvider(PROFILE_ID)}),
        _settings(input_wait_timeout_seconds=0.001),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )
    ctx = FakeContext(_job(), events, participant=FakeParticipant(publications={}))

    with pytest.raises(TimeoutError):
        await entrypoint(ctx)  # type: ignore[arg-type]

    assert "session_start" not in events
    assert "ready" not in events
    assert ctx.room.listener_count("track_subscribed") == 0
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_entrypoint_session_start_timeout_closes_without_ready() -> None:
    events: list[str] = []
    session = FakeOwnedSession(events, start_waits_forever=True)

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: DeterministicVoiceProfileProvider(PROFILE_ID)}),
        _settings(session_start_timeout_seconds=0.001),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )

    with pytest.raises(TimeoutError):
        await entrypoint(FakeContext(_job(), events))  # type: ignore[arg-type]

    assert "ready" not in events
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_entrypoint_ready_publish_timeout_closes_started_session() -> None:
    events: list[str] = []
    session = FakeOwnedSession(events)
    publish_cancelled = False

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        nonlocal publish_cancelled
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            publish_cancelled = True
            raise

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: DeterministicVoiceProfileProvider(PROFILE_ID)}),
        _settings(event_publish_timeout_seconds=0.001),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )

    with pytest.raises(TimeoutError):
        await entrypoint(FakeContext(_job(), events))  # type: ignore[arg-type]

    assert events[:2] == ["connect", "participant_joined"]
    assert "session_start" in events
    assert publish_cancelled is True
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1


@pytest.mark.asyncio
async def test_entrypoint_never_false_readies_and_cleans_up_failed_start() -> None:
    events: list[str] = []
    provider_closed = 0

    async def close_provider() -> None:
        nonlocal provider_closed
        provider_closed += 1

    provider = DeterministicVoiceProfileProvider(
        PROFILE_ID,
        close_callback=close_provider,
    )
    session = FakeOwnedSession(events, fail_start=True)

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: provider}),
        _settings(),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="start failed"):
        await entrypoint(FakeContext(_job(), events))  # type: ignore[arg-type]

    assert "ready" not in events
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1
    assert provider_closed == 1


@pytest.mark.asyncio
async def test_entrypoint_times_out_waiting_for_exact_participant_and_never_readies() -> None:
    events: list[str] = []
    provider_closed = 0

    async def close_provider() -> None:
        nonlocal provider_closed
        provider_closed += 1

    provider = DeterministicVoiceProfileProvider(
        PROFILE_ID,
        close_callback=close_provider,
    )
    session = FakeOwnedSession(events)

    async def publish_ready(ctx: object, authorized: object, preflight: object) -> None:
        del ctx, authorized, preflight
        events.append("ready")

    entrypoint = build_entrypoint(
        _authorizer(),
        VoiceProfileRegistry({PROFILE_ID: provider}),
        _settings(participant_wait_timeout_seconds=0.001),
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        ready_publisher=publish_ready,  # type: ignore[arg-type]
    )
    ctx = FakeContext(_job(), events, participant_waits_forever=True)

    with pytest.raises(TimeoutError):
        await entrypoint(ctx)  # type: ignore[arg-type]

    assert ctx.waited_for_identity == PARTICIPANT_IDENTITY
    assert "session_start" not in events
    assert "ready" not in events
    assert session.shutdown_calls == [False]
    assert session.close_calls == 1
    assert provider_closed == 1


@pytest.mark.asyncio
async def test_session_owner_bounds_interruption_and_cleanup_is_idempotent() -> None:
    events: list[str] = []
    provider_closed = 0

    async def close_provider() -> None:
        nonlocal provider_closed
        provider_closed += 1

    provider = DeterministicVoiceProfileProvider(
        PROFILE_ID,
        close_callback=close_provider,
    )
    scope = (await _authorizer().authorize(_job())).profile_scope
    _, prepared = await VoiceProfileRegistry({PROFILE_ID: provider}).prepare(scope)
    session = FakeOwnedSession(events)
    owner = AgentSessionOwner(
        prepared,
        session_factory=lambda _: (session, object()),  # type: ignore[arg-type]
        cleanup_timeout_seconds=0.05,
        interruption_timeout_seconds=0.01,
    )
    await owner.start(room=object(), participant_identity=PARTICIPANT_IDENTITY)
    session.never_interrupt = True

    with pytest.raises(VoiceSessionLifecycleError, match="interruption timed out"):
        await owner.interrupt()

    await owner.close()
    await owner.close()

    assert session.shutdown_calls == [False, False]
    assert session.close_calls == 1
    assert provider_closed == 1
    assert owner.closed is True


@pytest.mark.asyncio
async def test_session_owner_uses_one_cleanup_deadline_and_preserves_cancellation() -> None:
    events: list[str] = []
    provider_started = False

    async def slow_provider_close() -> None:
        nonlocal provider_started
        provider_started = True
        await asyncio.Future()

    provider = DeterministicVoiceProfileProvider(
        PROFILE_ID,
        close_callback=slow_provider_close,
    )
    scope = (await _authorizer().authorize(_job())).profile_scope
    _, prepared = await VoiceProfileRegistry({PROFILE_ID: provider}).prepare(scope)
    owner = AgentSessionOwner(
        prepared,
        session_factory=lambda _: (FakeOwnedSession(events), object()),  # type: ignore[arg-type]
        cleanup_timeout_seconds=0.001,
        interruption_timeout_seconds=0.01,
    )

    with pytest.raises(VoiceSessionLifecycleError, match="cleanup timed out"):
        await owner.close()
    assert provider_started is True

    provider = DeterministicVoiceProfileProvider(PROFILE_ID)
    _, prepared = await VoiceProfileRegistry({PROFILE_ID: provider}).prepare(scope)
    hanging_session = FakeOwnedSession(events)

    async def cancel_close() -> None:
        raise asyncio.CancelledError

    hanging_session.aclose = cancel_close  # type: ignore[method-assign]
    owner = AgentSessionOwner(
        prepared,
        session_factory=lambda _: (hanging_session, object()),  # type: ignore[arg-type]
        cleanup_timeout_seconds=0.05,
        interruption_timeout_seconds=0.01,
    )
    with pytest.raises(asyncio.CancelledError):
        await owner.close()


def test_agent_server_registers_exactly_one_named_rtc_session() -> None:
    created: list[AgentServer] = []
    constructor_kwargs: list[dict[str, object]] = []

    def server_factory(**kwargs: object) -> AgentServer:
        constructor_kwargs.append(kwargs)
        server = AgentServer(**kwargs)  # type: ignore[arg-type]
        created.append(server)
        return server

    server = build_agent_server(
        _settings(),
        VoiceProfileRegistry({PROFILE_ID: DeterministicVoiceProfileProvider(PROFILE_ID)}),
        session_repo=FakeSessionRepo,
        agent_repo=FakeAgentRepo,
        server_factory=server_factory,
    )

    assert created == [server]
    assert len(constructor_kwargs) == 1
    assert constructor_kwargs[0]["shutdown_process_timeout"] == 0.05
    assert constructor_kwargs[0]["num_idle_processes"] == 0
    assert constructor_kwargs[0]["load_threshold"] == 0.5
    assert constructor_kwargs[0]["max_retry"] == 2
    assert callable(constructor_kwargs[0]["load_fnc"])
    assert server._entrypoint_fnc is not None
    assert server._request_fnc is not None
    assert server._agent_name == WORKER_NAME
    with pytest.raises(RuntimeError, match="only supports registering only one"):
        server.rtc_session(server._entrypoint_fnc, agent_name=WORKER_NAME)
