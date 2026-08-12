# Synthetic voice fixtures

These WAV files are generated speech or tones, not recordings of a real user. They are intentionally small PCM fixtures for deterministic browser and provider qualification.

The legacy speech fixtures depend on the macOS `say` voice and may change across
OS releases. Regenerate them only intentionally with:

    bash scripts/generate_voice_fixtures.sh

The RTC fixtures are composed deterministically from checked-in PCM inputs and
ffmpeg generators on macOS or Linux:

    bash scripts/generate_voice_e2e_fixtures.sh

The original speech fixtures are mono 16 kHz PCM16. `long-pause.wav` includes an explicit 800 ms pause, and `interruption.wav` is the second utterance used in barge-in tests.

`browser-barge-in.wav` is mono 16 kHz PCM16 and 12.08375 seconds long. Its timeline is 8 seconds of startup silence, the 0.8210625-second `short-complete.wav` utterance, 1 second of silence, the 1.2626875-second `interruption.wav` utterance, then 1 second of trailing silence. The browser creates and publishes the exact fixture track muted, waits for canonical worker Ready, and only then unmutes it; the startup silence gives the isolated production stack deterministic scheduling margin without making silence a substitute for that readiness gate. With the fake STT's 300 ms trailing-silence boundary, the first turn commits around 9.121 seconds and the second speech begins around 9.821 seconds, while the first six-second reply is still playing.

`assistant-long.wav` is a deterministic non-speech tone: mono 24 kHz PCM16, exactly 144,000 samples (6 seconds). It exists to prove decoded remote energy and interruption without any provider, cloned voice, or personal data.

Do not replace these files with real user audio unless consent and repository-retention approval are documented.
