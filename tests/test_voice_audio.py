"""Unit contracts for provider-neutral audio conversion."""

import numpy as np
from murmur.voice.audio import audioframe_to_pcm16_bytes


class _FakeFrame:
    def __init__(self, samples_array: np.ndarray, samples: int) -> None:
        self._samples_array = samples_array
        self.samples = samples

    def to_ndarray(self) -> np.ndarray:
        return self._samples_array.copy()


def test_audioframe_conversion_transposes_planar_channels() -> None:
    planar = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.int16)
    frame = _FakeFrame(planar, samples=3)

    pcm = audioframe_to_pcm16_bytes(frame)

    assert pcm == planar.T.tobytes()


def test_audioframe_conversion_scales_float_samples_to_pcm16() -> None:
    floating = np.array([-1.0, 0.0, 0.5, 1.0], dtype=np.float32)
    frame = _FakeFrame(floating, samples=4)

    pcm = audioframe_to_pcm16_bytes(frame)

    expected = (floating.reshape(-1, 1) * 32767).astype(np.int16)
    assert pcm == expected.tobytes()
