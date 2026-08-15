"""Guarded provider-free Pipecat processors for loopback RTC qualification.

This module is deliberately Pipecat-only.  It does not import the LiveKit fake
profile (or any LiveKit package), because doing so would make the challenger
topology ambiguous.  Real ``InputAudioRawFrame`` objects drive a deterministic
energy-gated STT processor, a real LLM context frame drives the local LLM
processor, and the TTS processor paces checked-in PCM inside its owned Pipecat
``process_frame`` task so a production ``InterruptionFrame`` cancels playback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import struct
import threading
import time
import wave
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from murmur.voice.profile import (
    ProfileAdmission,
    ProfileReadiness,
    ProviderModelReadiness,
    VoiceAPIConnectionPolicy,
    VoiceConnectionPolicy,
    VoiceMediaPolicy,
    VoiceProfileScope,
    VoiceProfileUnavailable,
    VoiceSessionPolicy,
)
from murmur.voice.provider_profiles.pipecat_cascade import PreparedPipecatProfile

PIPECAT_FAKE_RTC_PROFILE_ID = "pipecat-fake-rtc-v1"
PIPECAT_FAKE_RTC_TRANSCRIPTS = ("Hello tutor.", "Actually, stop.")
PIPECAT_FAKE_RTC_REPLY = (
    "This is a deterministic local response used to prove the complete audio path."
)

E2E_MODE_ENV = "MURMUR_E2E_MODE"
ENVIRONMENT_ENV = "MURMUR_ENVIRONMENT"
PROFILE_ENV = "VOICE_V2_PROFILE_ID"
ASSISTANT_FIXTURE_ENV = "MURMUR_PIPECAT_E2E_ASSISTANT_FIXTURE_PATH"
EVIDENCE_PATH_ENV = "MURMUR_PIPECAT_E2E_EVIDENCE_PATH"

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_COMPONENTS = ("stt", "llm", "tts", "fake_media")
_INPUT_SAMPLE_RATE = 16_000
_OUTPUT_SAMPLE_RATE = 24_000
_CHANNELS = 1
_SAMPLE_WIDTH_BYTES = 2
_FRAME_SIZE_MS = 20
_SPEECH_RMS_THRESHOLD = 250.0
_SPEECH_PEAK_THRESHOLD = 500
_TRAILING_SILENCE_SECONDS = 0.30
_MAX_EVIDENCE_BYTES = 8_000_000

Sleep = Callable[[float], Awaitable[None]]
ClockNs = Callable[[], int]


@dataclass(frozen=True)
class PcmFixture:
    """Validated mono PCM16 fixture retained for deterministic playback."""

    sample_rate: int
    samples_per_channel: int
    pcm: bytes
    sha256: str

    @property
    def duration_seconds(self) -> float:
        return self.samples_per_channel / self.sample_rate


class JsonlEvidenceRecorder:
    """Append sanitized per-call evidence without retaining transport secrets."""

    def __init__(
        self,
        path: Path,
        scope: VoiceProfileScope,
        *,
        clock_ns: ClockNs = time.monotonic_ns,
    ) -> None:
        self._scope = scope
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        self._fd = _open_evidence_fd(path)
        self._closed = False

    def record(self, event: str, **fields: object) -> None:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("Pipecat fake evidence event must not be empty")
        payload = {
            **fields,
            "event": event,
            "monotonic_ns": self._clock_ns(),
            "profile_id": self._scope.profile_id,
            "schema_version": 1,
            "session_id": self._scope.session_id,
            "voice_call_id": self._scope.voice_call_id,
        }
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        with self._lock:
            if self._closed:
                raise RuntimeError("Pipecat fake evidence recorder is closed")
            written = os.write(self._fd, encoded)
            if written != len(encoded):
                raise OSError("incomplete Pipecat fake evidence write")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            os.close(self._fd)


class _ProfileLifecycle:
    """Record each processor cleanup and close shared evidence exactly once."""

    def __init__(self, recorder: JsonlEvidenceRecorder) -> None:
        self.recorder = recorder
        self._cleaned: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False

    def processor_cleaned(self, component: str) -> None:
        close_recorder = False
        with self._lock:
            if component in self._cleaned:
                return
            self._cleaned.add(component)
            self.recorder.record("processor_cleaned", component=component)
            if self._cleaned == {"stt", "llm", "tts"} and not self._closed:
                self.recorder.record("profile_closed", cleaned_processors=sorted(self._cleaned))
                self._closed = True
                close_recorder = True
        if close_recorder:
            self.recorder.close()


class DeterministicPipecatSTT(FrameProcessor):
    """Segment two real PCM utterances and emit standard Pipecat transcripts."""

    def __init__(
        self,
        lifecycle: _ProfileLifecycle,
        *,
        user_id: str,
        transcripts: tuple[str, ...] = PIPECAT_FAKE_RTC_TRANSCRIPTS,
        rms_threshold: float = _SPEECH_RMS_THRESHOLD,
        peak_threshold: int = _SPEECH_PEAK_THRESHOLD,
        trailing_silence_seconds: float = _TRAILING_SILENCE_SECONDS,
    ) -> None:
        if not transcripts or any(
            not isinstance(text, str) or not text.strip() for text in transcripts
        ):
            raise ValueError("Pipecat fake transcripts must be non-empty")
        if not user_id:
            raise ValueError("Pipecat fake STT user ID must not be empty")
        if rms_threshold <= 0 or peak_threshold <= 0 or trailing_silence_seconds <= 0:
            raise ValueError("Pipecat fake STT thresholds must be positive")
        super().__init__(name="pipecat-fake-stt")
        self._lifecycle = lifecycle
        self._user_id = user_id
        self._transcripts = transcripts
        self._rms_threshold = rms_threshold
        self._peak_threshold = peak_threshold
        self._trailing_silence_seconds = trailing_silence_seconds
        self._frame_index = 0
        self._utterance_index = 0
        self._speaking = False
        self._silence_seconds = 0.0
        self._cleaned = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame) and direction is FrameDirection.DOWNSTREAM:
            await self._accept_audio(frame)
            return
        await self.push_frame(frame, direction)

    async def _accept_audio(self, frame: InputAudioRawFrame) -> None:
        if (
            frame.sample_rate != _INPUT_SAMPLE_RATE
            or frame.num_channels != _CHANNELS
            or not frame.audio
            or len(frame.audio) % _SAMPLE_WIDTH_BYTES
        ):
            raise ValueError("Pipecat fake STT received an invalid PCM frame")
        samples = tuple(sample[0] for sample in struct.iter_unpack("<h", frame.audio))
        peak = max(abs(sample) for sample in samples)
        rms = math.sqrt(sum(float(sample) * sample for sample in samples) / len(samples))
        duration_seconds = len(samples) / (frame.sample_rate * frame.num_channels)
        active = peak >= self._peak_threshold and rms >= self._rms_threshold
        self._lifecycle.recorder.record(
            "input_frame",
            active=active,
            duration_ms=round(duration_seconds * 1_000, 3),
            frame_index=self._frame_index,
            peak=peak,
            rms=round(rms, 3),
            sample_rate=frame.sample_rate,
            samples_per_channel=len(samples),
        )
        self._frame_index += 1

        if self._utterance_index >= len(self._transcripts):
            return
        if active:
            if not self._speaking:
                self._speaking = True
                self._silence_seconds = 0.0
                text = self._transcripts[self._utterance_index]
                self._lifecycle.recorder.record(
                    "speech_onset",
                    utterance_index=self._utterance_index,
                )
                self._lifecycle.recorder.record(
                    "transcript_emitted",
                    text=text,
                    transcript_type="interim",
                    utterance_index=self._utterance_index,
                )
                await self.push_frame(
                    InterimTranscriptionFrame(
                        text,
                        self._user_id,
                        datetime.now(UTC).isoformat(),
                    )
                )
            self._silence_seconds = 0.0
            return
        if not self._speaking:
            return
        self._silence_seconds += duration_seconds
        if self._silence_seconds >= self._trailing_silence_seconds:
            await self._finish_utterance()

    async def _finish_utterance(self) -> None:
        text = self._transcripts[self._utterance_index]
        utterance_index = self._utterance_index
        self._speaking = False
        self._silence_seconds = 0.0
        self._utterance_index += 1
        self._lifecycle.recorder.record(
            "transcript_emitted",
            text=text,
            transcript_type="final",
            utterance_index=utterance_index,
        )
        await self.push_frame(
            TranscriptionFrame(
                text,
                self._user_id,
                datetime.now(UTC).isoformat(),
                finalized=True,
            )
        )

    async def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        try:
            await super().cleanup()
        finally:
            self._lifecycle.processor_cleaned("stt")


class DeterministicPipecatLLM(FrameProcessor):
    """Turn each production LLM context frame into one fixed local reply."""

    def __init__(
        self,
        lifecycle: _ProfileLifecycle,
        *,
        reply: str = PIPECAT_FAKE_RTC_REPLY,
    ) -> None:
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Pipecat fake LLM reply must not be empty")
        super().__init__(name="pipecat-fake-llm")
        self._lifecycle = lifecycle
        self._reply = reply
        self._response_index = 0
        self._cleaned = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction is FrameDirection.DOWNSTREAM:
            response_index = self._response_index
            self._response_index += 1
            self._lifecycle.recorder.record(
                "llm_response",
                response_index=response_index,
                text=self._reply,
            )
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(self._reply))
            await self.push_frame(LLMFullResponseEndFrame())
            return
        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        try:
            await super().cleanup()
        finally:
            self._lifecycle.processor_cleaned("llm")


class DeterministicPipecatTTS(FrameProcessor):
    """Pace real PCM in the Pipecat-owned task interrupted by system frames."""

    def __init__(
        self,
        lifecycle: _ProfileLifecycle,
        fixture: PcmFixture,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if fixture.sample_rate != _OUTPUT_SAMPLE_RATE or not fixture.pcm:
            raise ValueError("Pipecat fake TTS requires non-empty 24000 Hz PCM")
        super().__init__(name="pipecat-fake-tts")
        self._lifecycle = lifecycle
        self._fixture = fixture
        self._sleep = sleep
        self._response_index = 0
        self._cleaned = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame) and direction is FrameDirection.DOWNSTREAM:
            response_index = self._response_index
            self._response_index += 1
            await self._play_fixture(response_index)
            return
        await self.push_frame(frame, direction)

    async def _play_fixture(self, response_index: int) -> None:
        context_id = f"pipecat-fake-tts-{response_index}"
        samples_per_frame = self._fixture.sample_rate * _FRAME_SIZE_MS // 1_000
        bytes_per_frame = samples_per_frame * _SAMPLE_WIDTH_BYTES * _CHANNELS
        self._lifecycle.recorder.record(
            "tts_started",
            fixture_sha256=self._fixture.sha256,
            response_index=response_index,
        )
        await self.push_frame(TTSStartedFrame(context_id=context_id))
        frame_index = 0
        try:
            for offset in range(0, len(self._fixture.pcm), bytes_per_frame):
                chunk = self._fixture.pcm[offset : offset + bytes_per_frame]
                chunk_samples = len(chunk) // (_SAMPLE_WIDTH_BYTES * _CHANNELS)
                if chunk_samples <= 0:
                    continue
                await self.push_frame(
                    TTSAudioRawFrame(
                        chunk,
                        sample_rate=self._fixture.sample_rate,
                        num_channels=_CHANNELS,
                        context_id=context_id,
                    )
                )
                self._lifecycle.recorder.record(
                    "tts_frame",
                    bytes=len(chunk),
                    frame_index=frame_index,
                    response_index=response_index,
                )
                frame_index += 1
                await self._sleep(chunk_samples / self._fixture.sample_rate)
        except asyncio.CancelledError:
            self._lifecycle.recorder.record(
                "tts_cancelled",
                emitted_frames=frame_index,
                response_index=response_index,
            )
            await asyncio.shield(self.push_frame(TTSStoppedFrame(context_id=context_id)))
            raise
        await self.push_frame(TTSStoppedFrame(context_id=context_id))
        self._lifecycle.recorder.record(
            "tts_completed",
            emitted_frames=frame_index,
            response_index=response_index,
        )

    async def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        try:
            await super().cleanup()
        finally:
            self._lifecycle.processor_cleaned("tts")


class PipecatFakeRtcProvider:
    """Prepare one isolated fake processor set for each authoritative call."""

    def __init__(self, *, assistant_fixture_path: Path, evidence_path: Path) -> None:
        self._assistant_fixture_path = assistant_fixture_path
        self._evidence_path = evidence_path
        self._fixture = _load_pcm_fixture(
            assistant_fixture_path,
            expected_sample_rate=_OUTPUT_SAMPLE_RATE,
        )
        _probe_evidence_path(evidence_path)
        config_material = (
            f"{PIPECAT_FAKE_RTC_PROFILE_ID}:{self._fixture.sha256}:"
            f"{_SPEECH_RMS_THRESHOLD}:{_SPEECH_PEAK_THRESHOLD}:{_TRAILING_SILENCE_SECONDS}"
        )
        self._config_hash = hashlib.sha256(config_material.encode("utf-8")).hexdigest()

    async def admit(self, scope: VoiceProfileScope) -> ProfileAdmission:
        self._validate_scope(scope)
        return ProfileAdmission(
            profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
            required_components=_REQUIRED_COMPONENTS,
            config_hash=self._config_hash,
        )

    async def prepare(self, scope: VoiceProfileScope) -> PreparedPipecatProfile:
        admission = await self.admit(scope)
        recorder = JsonlEvidenceRecorder(self._evidence_path, scope)
        lifecycle = _ProfileLifecycle(recorder)
        stt = DeterministicPipecatSTT(lifecycle, user_id=scope.user_id)
        llm = DeterministicPipecatLLM(lifecycle)
        tts = DeterministicPipecatTTS(lifecycle, self._fixture)
        recorder.record(
            "profile_prepared",
            fixture_duration_ms=round(self._fixture.duration_seconds * 1_000, 3),
            fixture_sha256=self._fixture.sha256,
        )
        streams_ready_recorded = False

        async def wait_streams_ready() -> None:
            nonlocal streams_ready_recorded
            if not streams_ready_recorded:
                recorder.record("streams_ready")
                streams_ready_recorded = True
            await asyncio.sleep(0)

        async def close_profile() -> None:
            await asyncio.gather(stt.cleanup(), llm.cleanup(), tts.cleanup())

        readiness = ProfileReadiness(
            profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
            required_components=_REQUIRED_COMPONENTS,
            ready_components=_REQUIRED_COMPONENTS,
            config_hash=admission.config_hash,
            provider_models=(
                ProviderModelReadiness(
                    component="stt",
                    provider="murmur-local",
                    model="energy-gated-transcript-v1",
                ),
                ProviderModelReadiness(
                    component="llm",
                    provider="murmur-local",
                    model="fixed-response-v1",
                ),
                ProviderModelReadiness(
                    component="tts",
                    provider="murmur-local",
                    model="checked-in-pcm-v1",
                ),
            ),
            limitations=(
                "Provider-free deterministic qualification profile; no model quality claim",
                "Loopback SmallWebRTC only; no TURN, TLS, geography, scale, or cost claim",
            ),
        )
        connection = VoiceAPIConnectionPolicy(
            max_retry=0,
            retry_interval_seconds=0.0,
            timeout_seconds=5.0,
        )
        return PreparedPipecatProfile(
            profile_id=PIPECAT_FAKE_RTC_PROFILE_ID,
            instructions=scope.system_prompt,
            stt=stt,
            llm=llm,
            tts=tts,
            readiness=readiness,
            close_callback=close_profile,
            session_policy=VoiceSessionPolicy(),
            media_policy=VoiceMediaPolicy(
                input_sample_rate=_INPUT_SAMPLE_RATE,
                output_sample_rate=_OUTPUT_SAMPLE_RATE,
                output_track_name="murmur_pipecat_fake_audio",
            ),
            connection_policy=VoiceConnectionPolicy(
                stt=connection,
                llm=connection,
                tts=connection,
            ),
            wait_streams_ready=wait_streams_ready,
        )

    @staticmethod
    def _validate_scope(scope: VoiceProfileScope) -> None:
        if scope.profile_id != PIPECAT_FAKE_RTC_PROFILE_ID:
            raise VoiceProfileUnavailable("Pipecat fake RTC profile scope mismatch")
        if not isinstance(scope.system_prompt, str) or not scope.system_prompt.strip():
            raise VoiceProfileUnavailable("Pipecat fake RTC system prompt is empty")


def build_pipecat_fake_rtc_provider(
    *,
    e2e_mode: str | None,
    environment_name: str | None,
    profile_id: str | None,
    assistant_fixture_path: str | os.PathLike[str] | None,
    evidence_path: str | os.PathLike[str] | None,
) -> PipecatFakeRtcProvider:
    """Fail closed unless every test-only guard and local path is explicit."""

    if e2e_mode != "1" or environment_name != "test":
        raise VoiceProfileUnavailable("Pipecat fake RTC profile requires guarded test mode")
    if profile_id != PIPECAT_FAKE_RTC_PROFILE_ID:
        raise VoiceProfileUnavailable("Pipecat fake RTC profile ID is required")
    fixture = _validated_path(
        assistant_fixture_path,
        allowed_root=_fixture_root(),
        kind="assistant fixture",
        must_exist=True,
    )
    evidence = _validated_path(
        evidence_path,
        allowed_root=_evidence_root(),
        kind="evidence",
        must_exist=False,
    )
    return PipecatFakeRtcProvider(
        assistant_fixture_path=fixture,
        evidence_path=evidence,
    )


def build_pipecat_fake_rtc_provider_from_environment(
    environment: Mapping[str, str] | None = None,
) -> PipecatFakeRtcProvider:
    source = os.environ if environment is None else environment
    return build_pipecat_fake_rtc_provider(
        e2e_mode=source.get(E2E_MODE_ENV),
        environment_name=source.get(ENVIRONMENT_ENV),
        profile_id=source.get(PROFILE_ENV),
        assistant_fixture_path=source.get(ASSISTANT_FIXTURE_ENV),
        evidence_path=source.get(EVIDENCE_PATH_ENV),
    )


def summarize_pipecat_fake_evidence(path: Path, voice_call_id: str) -> dict[str, object]:
    """Return bounded sanitized media/cleanup counters for the E2E status route."""

    records = _read_evidence(path)
    matching = [record for record in records if record.get("voice_call_id") == voice_call_id]
    counts: dict[str, int] = {}
    for record in matching:
        event = record.get("event")
        if isinstance(event, str):
            counts[event] = counts.get(event, 0) + 1
    final_transcripts = [
        str(record["text"])
        for record in matching
        if record.get("event") == "transcript_emitted"
        and record.get("transcript_type") == "final"
        and isinstance(record.get("text"), str)
    ]
    cleaned_processors = sorted(
        {
            str(record["component"])
            for record in matching
            if record.get("event") == "processor_cleaned"
            and record.get("component") in {"stt", "llm", "tts"}
        }
    )
    cleanup_counts = {
        component: sum(
            record.get("event") == "processor_cleaned" and record.get("component") == component
            for record in matching
        )
        for component in ("stt", "llm", "tts")
    }
    profile_close_count = counts.get("profile_closed", 0)
    media_contract_satisfied = (
        counts.get("input_frame", 0) > 0
        and final_transcripts == list(PIPECAT_FAKE_RTC_TRANSCRIPTS)
        and counts.get("llm_response", 0) == len(PIPECAT_FAKE_RTC_TRANSCRIPTS)
        and counts.get("tts_started", 0) == len(PIPECAT_FAKE_RTC_TRANSCRIPTS)
        and counts.get("tts_frame", 0) > 0
        and counts.get("tts_cancelled", 0) == 1
        and counts.get("tts_completed", 0) == 1
        and cleanup_counts == {"stt": 1, "llm": 1, "tts": 1}
        and profile_close_count == 1
    )
    return {
        "input_frame_count": counts.get("input_frame", 0),
        "final_transcripts": final_transcripts,
        "llm_response_count": counts.get("llm_response", 0),
        "tts_frame_count": counts.get("tts_frame", 0),
        "tts_cancelled_count": counts.get("tts_cancelled", 0),
        "cleaned_processors": cleaned_processors,
        "processor_cleanup_counts": cleanup_counts,
        "profile_close_count": profile_close_count,
        "media_contract_satisfied": media_contract_satisfied,
    }


def _fixture_root() -> Path:
    return (_SOURCE_ROOT / "tests" / "fixtures" / "voice" / "audio").resolve()


def _evidence_root() -> Path:
    return (_SOURCE_ROOT / "var" / "evals").resolve()


def _validated_path(
    value: str | os.PathLike[str] | None,
    *,
    allowed_root: Path,
    kind: str,
    must_exist: bool,
) -> Path:
    if value is None or not str(value).strip():
        raise VoiceProfileUnavailable(f"Pipecat fake RTC {kind} path is required")
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(allowed_root)
    except (OSError, ValueError) as exc:
        raise VoiceProfileUnavailable(
            f"Pipecat fake RTC {kind} path is outside its guarded root"
        ) from exc
    if must_exist and (not resolved.is_file() or resolved.is_symlink()):
        raise VoiceProfileUnavailable(f"Pipecat fake RTC {kind} must be a regular file")
    if not must_exist:
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.parent.resolve(strict=True).relative_to(allowed_root)
        except (OSError, ValueError) as exc:
            raise VoiceProfileUnavailable(
                f"Pipecat fake RTC {kind} parent is outside its guarded root"
            ) from exc
    return resolved


def _load_pcm_fixture(path: Path, *, expected_sample_rate: int) -> PcmFixture:
    try:
        with wave.open(str(path), "rb") as fixture:
            if (
                fixture.getnchannels() != _CHANNELS
                or fixture.getsampwidth() != _SAMPLE_WIDTH_BYTES
                or fixture.getframerate() != expected_sample_rate
                or fixture.getcomptype() != "NONE"
            ):
                raise VoiceProfileUnavailable(
                    "Pipecat fake RTC fixture must be mono PCM16 at the required rate"
                )
            samples_per_channel = fixture.getnframes()
            pcm = fixture.readframes(samples_per_channel)
    except (OSError, EOFError, wave.Error) as exc:
        raise VoiceProfileUnavailable("Pipecat fake RTC fixture is not a valid WAV") from exc
    if not pcm or len(pcm) != samples_per_channel * _SAMPLE_WIDTH_BYTES:
        raise VoiceProfileUnavailable("Pipecat fake RTC fixture PCM is empty or truncated")
    if not any(pcm):
        raise VoiceProfileUnavailable("Pipecat fake RTC fixture PCM is silent")
    return PcmFixture(
        sample_rate=expected_sample_rate,
        samples_per_channel=samples_per_channel,
        pcm=pcm,
        sha256=hashlib.sha256(pcm).hexdigest(),
    )


def _probe_evidence_path(path: Path) -> None:
    fd = _open_evidence_fd(path)
    os.close(fd)


def _open_evidence_fd(path: Path) -> int:
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode):
            raise OSError("evidence target is not a regular file")
        return fd
    except OSError as exc:
        try:
            if "fd" in locals():
                os.close(fd)
        except OSError:
            pass
        raise VoiceProfileUnavailable("Pipecat fake RTC evidence path is not writable") from exc


def _read_evidence(path: Path) -> list[dict[str, object]]:
    try:
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise VoiceProfileUnavailable("Pipecat fake RTC evidence is too large")
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise VoiceProfileUnavailable("Pipecat fake RTC evidence is unavailable") from exc
    records: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise VoiceProfileUnavailable("Pipecat fake RTC evidence is invalid") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("event"), str):
            raise VoiceProfileUnavailable("Pipecat fake RTC evidence is invalid")
        records.append(decoded)
    return records


__all__ = [
    "ASSISTANT_FIXTURE_ENV",
    "E2E_MODE_ENV",
    "ENVIRONMENT_ENV",
    "EVIDENCE_PATH_ENV",
    "PIPECAT_FAKE_RTC_PROFILE_ID",
    "PIPECAT_FAKE_RTC_REPLY",
    "PIPECAT_FAKE_RTC_TRANSCRIPTS",
    "PROFILE_ENV",
    "DeterministicPipecatLLM",
    "DeterministicPipecatSTT",
    "DeterministicPipecatTTS",
    "JsonlEvidenceRecorder",
    "PcmFixture",
    "PipecatFakeRtcProvider",
    "build_pipecat_fake_rtc_provider",
    "build_pipecat_fake_rtc_provider_from_environment",
    "summarize_pipecat_fake_evidence",
]
