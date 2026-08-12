"""Guarded, provider-free LiveKit media adapters for the offline RTC E2E lane.

This module is intentionally test-only.  Its factory refuses to construct a
profile unless every test guard is explicit, the SFU URL is loopback-only, and
all file paths remain inside the repository's synthetic-fixture and ignored
evidence roots.  The providers implement the public LiveKit Agents 1.6.9
interfaces so the real ``AgentSession`` consumes microphone frames and publishes
audio without contacting a model provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import stat
import threading
import time
import wave
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from livekit import rtc
from livekit.agents import llm, stt, tts
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

from murmur.voice import profile as voice_profile

FAKE_RTC_PROFILE_ID = "fake-rtc-v1"
FAKE_RTC_TRANSCRIPTS = ("Hello tutor.", "Actually, stop.")
FAKE_RTC_REPLY = "This is a deterministic local response used to prove the complete audio path."

E2E_MODE_ENV = "MURMUR_E2E_MODE"
ENVIRONMENT_ENV = "MURMUR_ENVIRONMENT"
PROFILE_ENV = "VOICE_V2_PROFILE_ID"
LIVEKIT_URL_ENV = "LIVEKIT_URL"
ASSISTANT_FIXTURE_ENV = "MURMUR_E2E_ASSISTANT_FIXTURE_PATH"
EVIDENCE_PATH_ENV = "MURMUR_E2E_EVIDENCE_PATH"

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_COMPONENTS = ("stt", "llm", "tts", "fake_media")
_INPUT_SAMPLE_RATE = 16_000
_OUTPUT_SAMPLE_RATE = 24_000
_CHANNELS = 1
_FRAME_SIZE_MS = 20
_SPEECH_RMS_THRESHOLD = 250.0
_SPEECH_PEAK_THRESHOLD = 500
_TRAILING_SILENCE_SECONDS = 0.30

ClockNs = Callable[[], int]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class PcmFixture:
    """Validated mono PCM16 fixture held in memory for deterministic playback."""

    path: Path
    sample_rate: int
    samples_per_channel: int
    pcm: bytes
    sha256: str

    @property
    def duration_seconds(self) -> float:
        return self.samples_per_channel / self.sample_rate


class JsonlEvidenceRecorder:
    """Append small structured evidence records with one atomic file write."""

    def __init__(self, path: Path, *, clock_ns: ClockNs = time.monotonic_ns) -> None:
        self.path = path
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        self._fd = _open_evidence_fd(path)
        self._closed = False

    def record(self, event: str, **fields: object) -> None:
        if not event.strip():
            raise ValueError("evidence event must not be empty")
        payload = dict(fields)
        payload.update(
            {
                "event": event,
                "monotonic_ns": self._clock_ns(),
                "schema_version": 1,
            }
        )
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
        with self._lock:
            if self._closed:
                raise RuntimeError("evidence recorder is closed")
            written = os.write(self._fd, encoded)
            if written != len(encoded):
                raise OSError("incomplete evidence record write")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            os.close(self._fd)


class DeterministicStreamingSTT(stt.STT):
    """Energy-gated streaming STT that reads LiveKit's real input-frame channel."""

    def __init__(
        self,
        recorder: JsonlEvidenceRecorder,
        *,
        transcripts: tuple[str, ...] = FAKE_RTC_TRANSCRIPTS,
        rms_threshold: float = _SPEECH_RMS_THRESHOLD,
        peak_threshold: int = _SPEECH_PEAK_THRESHOLD,
        trailing_silence_seconds: float = _TRAILING_SILENCE_SECONDS,
    ) -> None:
        if not transcripts or any(not text.strip() for text in transcripts):
            raise ValueError("fake STT transcripts must contain non-empty text")
        if rms_threshold <= 0 or peak_threshold <= 0:
            raise ValueError("fake STT energy thresholds must be positive")
        if trailing_silence_seconds <= 0:
            raise ValueError("fake STT trailing silence must be positive")
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
                offline_recognize=True,
            )
        )
        self._recorder = recorder
        self._transcripts = transcripts
        self._rms_threshold = rms_threshold
        self._peak_threshold = peak_threshold
        self._trailing_silence_seconds = trailing_silence_seconds
        self._closed = False

    @property
    def model(self) -> str:
        return FAKE_RTC_PROFILE_ID

    @property
    def provider(self) -> str:
        return "murmur-local"

    async def _recognize_impl(
        self,
        buffer: stt.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        del buffer, language, conn_options
        await asyncio.sleep(0)
        return _transcript_event(
            stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id="fake-stt-offline-1",
            text=self._transcripts[0],
            start_time=0.0,
            end_time=0.0,
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> DeterministicRecognizeStream:
        del language
        if self._closed:
            raise RuntimeError("fake STT is closed")
        return DeterministicRecognizeStream(
            stt=self,
            conn_options=conn_options,
            recorder=self._recorder,
            transcripts=self._transcripts,
            rms_threshold=self._rms_threshold,
            peak_threshold=self._peak_threshold,
            trailing_silence_seconds=self._trailing_silence_seconds,
        )

    async def aclose(self) -> None:
        self._closed = True


class DeterministicRecognizeStream(stt.RecognizeStream):
    """Segments two fixture utterances from ``RecognizeStream._input_ch``."""

    def __init__(
        self,
        *,
        stt: DeterministicStreamingSTT,
        conn_options: APIConnectOptions,
        recorder: JsonlEvidenceRecorder,
        transcripts: tuple[str, ...],
        rms_threshold: float,
        peak_threshold: int,
        trailing_silence_seconds: float,
    ) -> None:
        super().__init__(
            stt=stt,
            conn_options=conn_options,
            sample_rate=_INPUT_SAMPLE_RATE,
        )
        self._recorder = recorder
        self._transcripts = transcripts
        self._rms_threshold = rms_threshold
        self._peak_threshold = peak_threshold
        self._trailing_silence_seconds = trailing_silence_seconds
        self._frame_index = 0
        self._stream_seconds = 0.0
        self._utterance_index = 0
        self._speaking = False
        self._utterance_start_seconds = 0.0
        self._last_active_end_seconds = 0.0
        self._silence_seconds = 0.0

    async def _run(self) -> None:
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                self._finish_utterance(reason="flush")
                continue
            self._accept_frame(item)
        self._finish_utterance(reason="input_closed")

    def _accept_frame(self, frame: rtc.AudioFrame) -> None:
        samples = np.frombuffer(frame.data, dtype="<i2")
        peak = int(np.max(np.abs(samples.astype(np.int32)))) if samples.size else 0
        rms = (
            float(math.sqrt(float(np.mean(np.square(samples, dtype=np.float64)))))
            if samples.size
            else 0.0
        )
        duration_seconds = frame.duration
        frame_start_seconds = self._stream_seconds
        frame_end_seconds = frame_start_seconds + duration_seconds
        active = rms >= self._rms_threshold and peak >= self._peak_threshold
        self._recorder.record(
            "input_frame",
            active=active,
            duration_ms=round(duration_seconds * 1_000, 3),
            frame_index=self._frame_index,
            num_channels=frame.num_channels,
            peak=peak,
            rms=round(rms, 3),
            sample_rate=frame.sample_rate,
            samples_per_channel=frame.samples_per_channel,
        )
        self._frame_index += 1
        self._stream_seconds = frame_end_seconds

        if self._utterance_index >= len(self._transcripts):
            return
        if active:
            if not self._speaking:
                self._start_utterance(frame_start_seconds)
            self._last_active_end_seconds = frame_end_seconds
            self._silence_seconds = 0.0
            return
        if not self._speaking:
            return
        self._silence_seconds += duration_seconds
        if self._silence_seconds >= self._trailing_silence_seconds:
            self._finish_utterance(reason="trailing_silence")

    def _start_utterance(self, start_seconds: float) -> None:
        self._speaking = True
        self._utterance_start_seconds = start_seconds
        self._last_active_end_seconds = start_seconds
        self._silence_seconds = 0.0
        request_id = self._request_id
        text = self._transcripts[self._utterance_index]
        self._event_ch.send_nowait(
            stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH, request_id=request_id)
        )
        self._event_ch.send_nowait(
            _transcript_event(
                stt.SpeechEventType.INTERIM_TRANSCRIPT,
                request_id=request_id,
                text=text,
                start_time=start_seconds,
                end_time=start_seconds,
            )
        )
        self._recorder.record(
            "speech_onset",
            stream_time_seconds=round(start_seconds, 6),
            transcript=text,
            utterance_index=self._utterance_index,
        )
        self._record_transcript("interim", text)

    def _finish_utterance(self, *, reason: str) -> None:
        if not self._speaking:
            return
        request_id = self._request_id
        text = self._transcripts[self._utterance_index]
        self._event_ch.send_nowait(
            _transcript_event(
                stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=request_id,
                text=text,
                start_time=self._utterance_start_seconds,
                end_time=self._last_active_end_seconds,
            )
        )
        self._event_ch.send_nowait(
            stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH, request_id=request_id)
        )
        self._record_transcript("final", text)
        self._recorder.record(
            "speech_end",
            reason=reason,
            stream_time_seconds=round(self._last_active_end_seconds, 6),
            utterance_index=self._utterance_index,
        )
        self._utterance_index += 1
        self._speaking = False
        self._silence_seconds = 0.0

    @property
    def _request_id(self) -> str:
        return f"fake-stt-{self._utterance_index + 1}"

    def _record_transcript(self, transcript_type: str, text: str) -> None:
        self._recorder.record(
            "transcript_emitted",
            text=text,
            transcript_type=transcript_type,
            utterance_index=self._utterance_index,
        )


class DeterministicLLM(llm.LLM):
    """One-chunk local LLM implementation used only by the fake RTC profile."""

    def __init__(self, recorder: JsonlEvidenceRecorder, *, reply: str = FAKE_RTC_REPLY) -> None:
        if not reply.strip():
            raise ValueError("fake LLM reply must not be empty")
        super().__init__()
        self._recorder = recorder
        self._reply = reply
        self._request_count = 0
        self._closed = False

    @property
    def model(self) -> str:
        return FAKE_RTC_PROFILE_ID

    @property
    def provider(self) -> str:
        return "murmur-local"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, object]] = NOT_GIVEN,
    ) -> DeterministicLLMStream:
        del parallel_tool_calls, tool_choice, extra_kwargs
        if self._closed:
            raise RuntimeError("fake LLM is closed")
        self._request_count += 1
        return DeterministicLLMStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options,
            recorder=self._recorder,
            request_id=f"fake-llm-{self._request_count}",
            reply=self._reply,
        )

    async def aclose(self) -> None:
        self._closed = True
        await super().aclose()


class DeterministicLLMStream(llm.LLMStream):
    def __init__(
        self,
        llm: DeterministicLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
        recorder: JsonlEvidenceRecorder,
        request_id: str,
        reply: str,
    ) -> None:
        super().__init__(llm, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._recorder = recorder
        self._request_id = request_id
        self._reply = reply

    async def _run(self) -> None:
        await asyncio.sleep(0)
        self._event_ch.send_nowait(
            llm.ChatChunk(
                id=self._request_id,
                delta=llm.ChoiceDelta(role="assistant", content=self._reply),
            )
        )
        self._recorder.record(
            "llm_chunk_emitted",
            request_id=self._request_id,
            text=self._reply,
        )


class DeterministicTTS(tts.TTS):
    """Paces checked-in PCM at wall-clock speed for interruptible playback."""

    def __init__(
        self,
        recorder: JsonlEvidenceRecorder,
        fixture: PcmFixture,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if fixture.sample_rate != _OUTPUT_SAMPLE_RATE:
            raise ValueError("fake TTS fixture must be 24000 Hz")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=fixture.sample_rate,
            num_channels=_CHANNELS,
        )
        self._recorder = recorder
        self._fixture = fixture
        self._sleep = sleep
        self._request_count = 0
        self._closed = False

    @property
    def model(self) -> str:
        return FAKE_RTC_PROFILE_ID

    @property
    def provider(self) -> str:
        return "murmur-local"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> DeterministicChunkedStream:
        if self._closed:
            raise RuntimeError("fake TTS is closed")
        self._request_count += 1
        return DeterministicChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            recorder=self._recorder,
            fixture=self._fixture,
            request_id=f"fake-tts-{self._request_count}",
            sleep=self._sleep,
        )

    async def aclose(self) -> None:
        self._closed = True


class DeterministicChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: DeterministicTTS,
        input_text: str,
        conn_options: APIConnectOptions,
        recorder: JsonlEvidenceRecorder,
        fixture: PcmFixture,
        request_id: str,
        sleep: Sleep,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._recorder = recorder
        self._fixture = fixture
        self._request_id = request_id
        self._sleep = sleep

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        bytes_per_sample = 2
        samples_per_frame = self._fixture.sample_rate * _FRAME_SIZE_MS // 1_000
        bytes_per_frame = samples_per_frame * bytes_per_sample * _CHANNELS
        output_emitter.initialize(
            request_id=self._request_id,
            sample_rate=self._fixture.sample_rate,
            num_channels=_CHANNELS,
            mime_type="audio/pcm",
            frame_size_ms=_FRAME_SIZE_MS,
            stream=False,
        )
        self._recorder.record(
            "tts_started",
            fixture_sha256=self._fixture.sha256,
            request_id=self._request_id,
            text=self._input_text,
        )
        emitted_frames = 0
        try:
            for offset in range(0, len(self._fixture.pcm), bytes_per_frame):
                chunk = self._fixture.pcm[offset : offset + bytes_per_frame]
                output_emitter.push(chunk)
                chunk_samples = len(chunk) // (bytes_per_sample * _CHANNELS)
                duration_seconds = chunk_samples / self._fixture.sample_rate
                pcm = np.frombuffer(chunk, dtype="<i2")
                peak = int(np.max(np.abs(pcm.astype(np.int32)))) if pcm.size else 0
                self._recorder.record(
                    "tts_frame",
                    duration_ms=round(duration_seconds * 1_000, 3),
                    frame_index=emitted_frames,
                    peak=peak,
                    request_id=self._request_id,
                    samples_per_channel=chunk_samples,
                )
                emitted_frames += 1
                await self._sleep(duration_seconds)
        except asyncio.CancelledError:
            self._recorder.record(
                "tts_cancelled",
                emitted_frames=emitted_frames,
                request_id=self._request_id,
            )
            raise
        self._recorder.record(
            "tts_completed",
            emitted_frames=emitted_frames,
            request_id=self._request_id,
        )


class FakeRtcProfileProvider:
    """Construct one isolated deterministic provider set for each voice job."""

    def __init__(self, *, assistant_fixture_path: Path, evidence_path: Path) -> None:
        self._assistant_fixture_path = assistant_fixture_path
        self._evidence_path = evidence_path

    async def preflight(
        self, scope: voice_profile.VoiceProfileScope
    ) -> voice_profile.ProfilePreflight:
        self._validate_scope(scope)
        _load_pcm_fixture(self._assistant_fixture_path, expected_sample_rate=_OUTPUT_SAMPLE_RATE)
        _probe_evidence_path(self._evidence_path)
        return voice_profile.ProfilePreflight(
            profile_id=FAKE_RTC_PROFILE_ID,
            required_components=_REQUIRED_COMPONENTS,
            ready_components=_REQUIRED_COMPONENTS,
        )

    async def prepare(
        self, scope: voice_profile.VoiceProfileScope
    ) -> voice_profile.PreparedVoiceProfile:
        self._validate_scope(scope)
        fixture = _load_pcm_fixture(
            self._assistant_fixture_path,
            expected_sample_rate=_OUTPUT_SAMPLE_RATE,
        )
        recorder = JsonlEvidenceRecorder(self._evidence_path)
        fake_stt = DeterministicStreamingSTT(recorder)
        fake_llm = DeterministicLLM(recorder)
        fake_tts = DeterministicTTS(recorder, fixture)
        close_lock = asyncio.Lock()
        closed = False

        async def close_profile() -> None:
            nonlocal closed
            async with close_lock:
                if closed:
                    return
                closed = True
                await fake_stt.aclose()
                await fake_llm.aclose()
                await fake_tts.aclose()
                recorder.record(
                    "profile_closed",
                    profile_id=FAKE_RTC_PROFILE_ID,
                    session_id=scope.session_id,
                    voice_call_id=scope.voice_call_id,
                )
                recorder.close()

        try:
            return voice_profile.PreparedVoiceProfile(
                profile_id=FAKE_RTC_PROFILE_ID,
                instructions=scope.system_prompt,
                stt=fake_stt,
                llm=fake_llm,
                tts=fake_tts,
                vad=None,
                close_callback=close_profile,
                session_policy=voice_profile.VoiceSessionPolicy(),
                media_policy=voice_profile.VoiceMediaPolicy(),
            )
        except Exception:
            await close_profile()
            raise

    @staticmethod
    def _validate_scope(scope: voice_profile.VoiceProfileScope) -> None:
        if scope.profile_id != FAKE_RTC_PROFILE_ID:
            raise voice_profile.VoiceProfileUnavailable("fake RTC profile scope mismatch")
        if not scope.system_prompt.strip():
            raise voice_profile.VoiceProfileUnavailable(
                "fake RTC profile requires non-empty instructions"
            )


def create_fake_rtc_profile_provider(
    *,
    e2e_mode: str | None,
    environment: str | None,
    profile_id: str | None,
    livekit_url: str | None,
    assistant_fixture_path: str | os.PathLike[str] | None,
    evidence_path: str | os.PathLike[str] | None,
) -> FakeRtcProfileProvider:
    """Fail closed unless every local test-only boundary is explicit and valid."""

    if e2e_mode != "1":
        raise voice_profile.VoiceProfileUnavailable("fake RTC profile requires E2E mode")
    if environment != "test":
        raise voice_profile.VoiceProfileUnavailable("fake RTC profile requires test environment")
    if profile_id != FAKE_RTC_PROFILE_ID:
        raise voice_profile.VoiceProfileUnavailable("fake RTC profile ID is not selected")
    if not _is_loopback_livekit_url(livekit_url):
        raise voice_profile.VoiceProfileUnavailable("fake RTC profile requires a loopback SFU URL")
    fixture = _validated_fixture_path(assistant_fixture_path)
    evidence = _validated_evidence_path(evidence_path)
    _load_pcm_fixture(fixture, expected_sample_rate=_OUTPUT_SAMPLE_RATE)
    _probe_evidence_path(evidence)
    return FakeRtcProfileProvider(
        assistant_fixture_path=fixture,
        evidence_path=evidence,
    )


def create_fake_rtc_profile_provider_from_environment(
    environ: Mapping[str, str] | None = None,
) -> FakeRtcProfileProvider:
    """Read the narrow fake-profile contract from an explicit environment map."""

    values = os.environ if environ is None else environ
    return create_fake_rtc_profile_provider(
        e2e_mode=values.get(E2E_MODE_ENV),
        environment=values.get(ENVIRONMENT_ENV),
        profile_id=values.get(PROFILE_ENV),
        livekit_url=values.get(LIVEKIT_URL_ENV),
        assistant_fixture_path=values.get(ASSISTANT_FIXTURE_ENV),
        evidence_path=values.get(EVIDENCE_PATH_ENV),
    )


def _transcript_event(
    event_type: stt.SpeechEventType,
    *,
    request_id: str,
    text: str,
    start_time: float,
    end_time: float,
) -> stt.SpeechEvent:
    return stt.SpeechEvent(
        type=event_type,
        request_id=request_id,
        alternatives=[
            stt.SpeechData(
                language="en",
                text=text,
                start_time=max(start_time, 0.0),
                end_time=max(end_time, start_time),
                confidence=1.0,
            )
        ],
    )


def _fixture_root() -> Path:
    return _SOURCE_ROOT / "tests" / "fixtures" / "voice" / "audio"


def _evidence_root() -> Path:
    return _SOURCE_ROOT / "var" / "evals"


def _validated_fixture_path(value: str | os.PathLike[str] | None) -> Path:
    return _validated_path(
        value,
        allowed_root=_fixture_root(),
        kind="assistant fixture",
        must_exist=True,
        suffix=".wav",
    )


def _validated_evidence_path(value: str | os.PathLike[str] | None) -> Path:
    return _validated_path(
        value,
        allowed_root=_evidence_root(),
        kind="evidence",
        must_exist=False,
        suffix=".jsonl",
    )


def _validated_path(
    value: str | os.PathLike[str] | None,
    *,
    allowed_root: Path,
    kind: str,
    must_exist: bool,
    suffix: str,
) -> Path:
    if value is None or not os.fspath(value).strip():
        raise voice_profile.VoiceProfileUnavailable(f"fake RTC {kind} path is required")
    raw = Path(value)
    if not raw.is_absolute() or ".." in raw.parts or raw.suffix.lower() != suffix:
        raise voice_profile.VoiceProfileUnavailable(f"fake RTC {kind} path is unsafe")
    try:
        root = allowed_root.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        if must_exist:
            resolved = raw.resolve(strict=True)
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
        else:
            parent = raw.parent.resolve(strict=True)
            if not parent.is_dir():
                raise NotADirectoryError(parent)
            resolved = parent / raw.name
            if resolved.exists() and not resolved.is_file():
                raise FileNotFoundError(resolved)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise voice_profile.VoiceProfileUnavailable(f"fake RTC {kind} path is unavailable") from exc
    if not resolved.is_relative_to(root):
        raise voice_profile.VoiceProfileUnavailable(f"fake RTC {kind} path is outside its root")
    _reject_symlink_components(raw, root=root)
    return resolved


def _reject_symlink_components(path: Path, *, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise voice_profile.VoiceProfileUnavailable("fake RTC path is not canonical") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise voice_profile.VoiceProfileUnavailable("fake RTC path must not use symlinks")


def _is_loopback_livekit_url(value: str | None) -> bool:
    if value is None:
        return False
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _load_pcm_fixture(path: Path, *, expected_sample_rate: int) -> PcmFixture:
    try:
        with wave.open(str(path), "rb") as fixture:
            if (
                fixture.getnchannels() != _CHANNELS
                or fixture.getsampwidth() != 2
                or fixture.getframerate() != expected_sample_rate
                or fixture.getcomptype() != "NONE"
            ):
                raise ValueError("fixture must be mono PCM16 at the required sample rate")
            samples_per_channel = fixture.getnframes()
            pcm = fixture.readframes(samples_per_channel)
    except (EOFError, OSError, ValueError, wave.Error) as exc:
        raise voice_profile.VoiceProfileUnavailable("fake RTC fixture is not a valid WAV") from exc
    if not pcm or len(pcm) != samples_per_channel * 2:
        raise voice_profile.VoiceProfileUnavailable("fake RTC fixture PCM is empty or truncated")
    samples = np.frombuffer(pcm, dtype="<i2")
    if not np.any(samples):
        raise voice_profile.VoiceProfileUnavailable("fake RTC fixture PCM is silent")
    return PcmFixture(
        path=path,
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
    except OSError as exc:
        raise voice_profile.VoiceProfileUnavailable(
            "fake RTC evidence path is not writable"
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise voice_profile.VoiceProfileUnavailable(
                "fake RTC evidence path is not a regular file"
            )
    except BaseException:
        os.close(fd)
        raise
    return fd
