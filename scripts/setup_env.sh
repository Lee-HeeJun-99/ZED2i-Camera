#!/usr/bin/env bash
# 이 파일은 실행하지 말고 source 하세요.

if [[ -f /opt/ros/humble/setup.bash ]]; then
  source /opt/ros/humble/setup.bash
else
  echo "ERROR: /opt/ros/humble/setup.bash를 찾을 수 없습니다." >&2
  return 1 2>/dev/null || exit 1
fi

if [[ -f /home/ubuntu/robot_ws/install/setup.bash ]]; then
  source /home/ubuntu/robot_ws/install/setup.bash
fi

# 현재 로그인 환경의 Conda Python(3.14)은 Humble의 rclpy(3.10)와 호환되지 않습니다.
# 각 실행 스크립트는 명시적으로 /usr/bin/python3를 사용합니다.
