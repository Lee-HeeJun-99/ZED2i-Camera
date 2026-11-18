#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/setup_env.sh"

echo "[ZED 관련 토픽: 이름 | 타입]"
ros2 topic list -t | grep -Ei 'zed|image|depth|point_cloud|imu' || {
  echo "관련 토픽을 찾지 못했습니다. 카메라 노드가 실행 중인지 확인하세요." >&2
  exit 1
}
