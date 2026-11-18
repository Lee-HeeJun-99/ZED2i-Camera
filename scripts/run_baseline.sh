#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/setup_env.sh"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="$PROJECT_DIR/data/runs/$STAMP"
mkdir -p "$OUTPUT_DIR" "$PROJECT_DIR/logs"

{
  echo "collected_at=$(date --iso-8601=seconds)"
  echo "hostname=$(hostname)"
  echo "ros_distro=${ROS_DISTRO:-unknown}"
  echo "ros_domain_id=${ROS_DOMAIN_ID:-0}"
  echo "rmw_implementation=${RMW_IMPLEMENTATION:-default}"
  echo
  echo "[zed packages]"
  ros2 pkg list | grep '^zed' || true
  echo
  echo "[topic list]"
  ros2 topic list -t || true
} > "$OUTPUT_DIR/environment.txt"

set +e
/usr/bin/python3 "$PROJECT_DIR/scripts/topic_baseline.py" \
  --config "$PROJECT_DIR/config/topics.yaml" \
  --output "$OUTPUT_DIR" "$@" 2>&1 | tee "$PROJECT_DIR/logs/$STAMP.log"
STATUS=${PIPESTATUS[0]}
set -e

echo "결과: $OUTPUT_DIR"
exit "$STATUS"
