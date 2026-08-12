#!/usr/bin/env bash
set -euo pipefail

voice_fixture_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
voice_fixture_output="${voice_fixture_root}/tests/fixtures/voice/audio"
voice_fixture_tmp_dir="$(mktemp -d)"

cleanup_voice_fixture_tmp() {
  rm -rf "${voice_fixture_tmp_dir}"
}
trap cleanup_voice_fixture_tmp EXIT

if ! command -v say >/dev/null 2>&1; then
  echo "The macOS 'say' command is required to regenerate these synthetic fixtures." >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to regenerate these synthetic fixtures." >&2
  exit 1
fi

mkdir -p "${voice_fixture_output}"

say -v Samantha -r 180 -o "${voice_fixture_tmp_dir}/short.aiff" "Hello tutor."
say -v Samantha -r 180 -o "${voice_fixture_tmp_dir}/pause-one.aiff" "Explain the"
say -v Samantha -r 180 -o "${voice_fixture_tmp_dir}/pause-two.aiff" "Pythagorean theorem."
say -v Samantha -r 175 -o "${voice_fixture_tmp_dir}/hinglish.aiff" \
  "Delhi ki flight fifteen August ko."
say -v Samantha -r 190 -o "${voice_fixture_tmp_dir}/interrupt.aiff" "Actually, stop."

ffmpeg -hide_banner -loglevel error -y \
  -i "${voice_fixture_tmp_dir}/short.aiff" \
  -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  "${voice_fixture_output}/short-complete.wav"

ffmpeg -hide_banner -loglevel error -y \
  -i "${voice_fixture_tmp_dir}/pause-one.aiff" \
  -f lavfi -t 0.8 -i anullsrc=r=16000:cl=mono \
  -i "${voice_fixture_tmp_dir}/pause-two.aiff" \
  -filter_complex "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]" \
  -map "[out]" -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  "${voice_fixture_output}/long-pause.wav"

ffmpeg -hide_banner -loglevel error -y \
  -i "${voice_fixture_tmp_dir}/hinglish.aiff" \
  -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  "${voice_fixture_output}/hinglish-entities.wav"

ffmpeg -hide_banner -loglevel error -y \
  -i "${voice_fixture_tmp_dir}/interrupt.aiff" \
  -map_metadata -1 -ac 1 -ar 16000 -c:a pcm_s16le \
  "${voice_fixture_output}/interruption.wav"

echo "Generated synthetic voice fixtures in ${voice_fixture_output}"
