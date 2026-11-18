#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 ]]; then echo "usage: $0 CASE [runner options]" >&2; exit 2; fi
CASE="$1"; shift
set +u
source "$PROJECT_DIR/scripts/setup_env.sh"
set -u
if [[ "$CASE" == H ]]; then "$PROJECT_DIR/scripts/build_native.sh"; fi
exec /usr/bin/python3 "$PROJECT_DIR/scripts/camera_30min_runner.py" --case "$CASE" "$@"
