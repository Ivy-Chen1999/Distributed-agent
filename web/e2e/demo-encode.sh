#!/usr/bin/env bash
# Encode the frames recorded by e2e/demo.spec.ts into one MP4.
#   e2e/demo-encode.sh <raw dir> [output.mp4]
# Each frame is held until the next one's real timestamp (variable-rate screencast -> constant
# 60 fps, the screencast rate), so motion plays at true speed. The desktop clip keeps its
# 2880x1800 device pixels; the phone clip is centred on the same canvas.
set -euo pipefail
RAW=${1:?usage: demo-encode.sh <raw dir> [output.mp4]}
OUT=${2:-$RAW/../womm-demo.mp4}

HERE=$(cd "$(dirname "$0")" && pwd)
concat_list() { node "$HERE/demo-concat.mjs" "$1"; }

concat_list "$RAW/desktop"
concat_list "$RAW/phone"

ffmpeg -v error -y \
  -f concat -safe 0 -i "$RAW/desktop/frames.ffconcat" \
  -f concat -safe 0 -i "$RAW/phone/frames.ffconcat" \
  -filter_complex "\
[0:v]fps=60,scale=2880:1800:flags=lanczos,setsar=1,format=yuv420p[a];\
[1:v]fps=60,scale=-2:1720:flags=lanczos,pad=2880:1800:(ow-iw)/2:(oh-ih)/2:color=0x0F0F0F,setsar=1,format=yuv420p[b];\
[a][b]concat=n=2:v=1:a=0[v]" \
  -map "[v]" -c:v libx264 -preset slow -crf 16 -tune stillimage -profile:v high -movflags +faststart "$OUT"
echo "$OUT"
