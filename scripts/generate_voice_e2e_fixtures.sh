#!/usr/bin/env bash
set -euo pipefail

voice_fixture_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
voice_fixture_output="${voice_fixture_root}/tests/fixtures/voice/audio"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to compose the deterministic RTC fixtures." >&2
  exit 1
fi

for voice_fixture_source in short-complete.wav interruption.wav; do
  if [[ ! -f "${voice_fixture_output}/${voice_fixture_source}" ]]; then
    echo "Missing checked-in source fixture: ${voice_fixture_source}" >&2
    exit 1
  fi
done

# These are composed only from checked-in PCM sources and deterministic ffmpeg
# generators. They deliberately do not invoke macOS `say`, whose voices can
# drift between operating-system releases.
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -t 8.0 -i anullsrc=r=16000:cl=mono \
  -i "${voice_fixture_output}/short-complete.wav" \
  -f lavfi -t 1.0 -i anullsrc=r=16000:cl=mono \
  -i "${voice_fixture_output}/interruption.wav" \
  -f lavfi -t 1.0 -i anullsrc=r=16000:cl=mono \
  -filter_complex "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[out]" \
  -map "[out]" -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  "${voice_fixture_output}/browser-barge-in.wav"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=523.25:sample_rate=24000:duration=6" \
  -map_metadata -1 -ac 1 -ar 24000 -c:a pcm_s16le \
  "${voice_fixture_output}/assistant-long.wav"

echo "Composed deterministic RTC fixtures in ${voice_fixture_output}"
