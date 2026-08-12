# Synthetic voice fixtures

These WAV files are generated speech, not recordings of a real user. They are intentionally small, mono, 16 kHz PCM fixtures for deterministic browser and provider qualification.

On macOS with `ffmpeg` installed, regenerate them from the repository root with:

    bash scripts/generate_voice_fixtures.sh

`long-pause.wav` includes an explicit 800 ms pause. `interruption.wav` is the second utterance used in barge-in tests. Do not replace these files with real user audio unless consent and repository-retention approval are documented.
