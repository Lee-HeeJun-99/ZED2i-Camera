#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$PROJECT_DIR/build"
cc -O2 -Wall -Wextra -Werror "$PROJECT_DIR/scripts/native_v4l2_capture.c" -o "$PROJECT_DIR/build/native_v4l2_capture"
echo "$PROJECT_DIR/build/native_v4l2_capture"
