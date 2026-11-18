# ZED 2i 30-minute experiment readiness

Recorded: 2026-11-13 (Asia/Seoul)

## Status

`READY_FOR_OPERATOR_ENVIRONMENT_CHECK / NOT_EXECUTED`

No A-H case has been reported as a completed 1,800-second measurement. No robot,
gripper, MoveL, ServoL, Stop, or Hold command was sent or implemented by this
suite.

## Reused evidence and code flow

- The local git checkout at `/home/ubuntu/robot_ws/src/OpenVLA_TEST` is currently
  branch `260731`, not `lhj-research`, and has unrelated user modifications.
  It was inspected read-only and not changed.
- The prepared field bundle contains the Phase 12 `lhj-research` artifacts used
  as the behavioral reference. In particular, the 2026-11-06 A/B/C script uses
  ROS image receipt, repeated `/predict`, and command-free action-preparation
  validation. The 2026-11-05 software-validation artifacts preserve SDK
  `read`, SDK `grab+retrieve`, validity OFF, OpenCV V4L2, and Native V4L2 calls.
- Existing 60-second evidence observed irregular delivery at Native V4L2. This
  is background evidence only and is not substituted for a new 30-minute case.

## Detected local capabilities

- ROS 2 Humble, `rclpy`, ZED SDK Python bindings, OpenCV, psutil, matplotlib,
  Pillow, and `/usr/local/zed/lib/libsl_zed.so` are installed.
- ZED Diagnostic 5.3.0 and `nvidia-smi` executables exist.
- Native V4L2 collector builds successfully with the local C compiler.
- USB topology inspection showed a 5,000 Mbit/s UVC video device during audit.

## Current blockers to actual measurement

- No `/dev/video*` node was visible in the execution environment at readiness
  time, so D-H device-open capability was not confirmed.
- ROS graph discovery was blocked by the managed execution environment's socket
  restriction (`PermissionError: Operation not permitted`), so A-C topic and
  model-server readiness were not confirmed.
- `nvidia-smi` could not communicate with the NVIDIA driver in this environment.
- Kernel log and other inaccessible counters are recorded as `NOT_AVAILABLE`;
  they are never estimated.

Because these prerequisites were not confirmed, running eight 30-minute cases
would create invalid evidence. The suite therefore remains prepared but
unexecuted. Each real run must use the first received frame as time zero, retain
failures, and set `valid_30min_result=true` only after the full 1,800-second
window.
