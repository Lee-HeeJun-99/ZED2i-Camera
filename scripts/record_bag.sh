#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/setup_env.sh"

DURATION="${1:-60}"
if ! [[ "$DURATION" =~ ^[1-9][0-9]*$ ]]; then
  echo "사용법: $0 [녹화시간(초)]" >&2
  exit 2
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT="$PROJECT_DIR/data/rosbags/zed2i_$STAMP"
mkdir -p "$PROJECT_DIR/data/rosbags"

mapfile -t TOPICS < <(/usr/bin/python3 - "$PROJECT_DIR/config/topics.yaml" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as stream:
    for item in yaml.safe_load(stream)["topics"]:
        print(item["name"])
PY
)

echo "${DURATION}초 동안 녹화합니다: $OUTPUT"
timeout --signal=INT "${DURATION}s" ros2 bag record \
  --compression-mode file --compression-format zstd \
  -o "$OUTPUT" "${TOPICS[@]}" || STATUS=$?

# GNU timeout은 제한시간 도달 시 124를 반환하지만 정상적인 예약 종료로 취급합니다.
if [[ "${STATUS:-0}" -ne 0 && "${STATUS:-0}" -ne 124 ]]; then
  exit "$STATUS"
fi
echo "rosbag 저장 완료: $OUTPUT"
