# ZED 2i baseline test

ZED 2i ROS 2 토픽이 일정한 주기로 들어오는지 측정하고, 기준 결과와 rosbag을
날짜별로 보관하기 위한 작업 폴더입니다.

## 폴더 구성

```text
config/                 측정 대상 토픽과 허용 기준
scripts/                실행 스크립트
data/baseline/          확정한 기준 결과 보관
data/runs/              매 측정의 CSV/JSON 결과
data/rosbags/           선택적으로 녹화한 rosbag
logs/                   실행 로그
reports/                비교/검토 보고서
```

빈 폴더는 `.gitkeep`으로 유지합니다. `data/runs`, `data/rosbags`, `logs`의 실제
측정 파일은 `.gitignore` 대상입니다.

## 1. 카메라 실행

새 터미널에서 ZED wrapper를 실행합니다. 현재 PC의 설치 경로를 반영한 예시는
다음과 같습니다.

```bash
source /opt/ros/humble/setup.bash
source /home/ubuntu/robot_ws/install/setup.bash
ros2 launch zed_wrapper zed_camera.launch.py camera_model:=zed2i
```

설치된 wrapper의 기본 설정은 HD720, grab 30 Hz입니다. Depth 기본값이 `NONE`인
버전에서는 depth/point cloud 측정 전에 launch 인자나 별도 파라미터 파일로
depth mode를 활성화해야 합니다.

## 2. 현재 토픽 확인

```bash
cd /home/ubuntu/zed_2i_test
./scripts/discover_topics.sh
```

카메라 이름 또는 namespace가 다르면 `config/topics.yaml`의 `name`만 실제 출력에
맞춰 수정합니다.

## 3. 기준 측정

기본 60초 측정:

```bash
./scripts/run_baseline.sh
```

시간이나 설정을 바꾸려면:

```bash
./scripts/run_baseline.sh --duration 120 --config config/topics.yaml
```

한 번의 실행 결과는 `data/runs/YYYYmmdd_HHMMSS/` 아래에 생성됩니다.

- `samples.csv`: 메시지별 도착 시각, 주기, header 지연
- `summary.csv`: 사람이 보기 쉬운 토픽별 통계와 PASS/FAIL
- `summary.json`: 후속 자동 비교에 사용할 상세 결과
- `environment.txt`: ROS domain, 토픽 목록, ZED/ROS 환경 정보

필수 토픽이 없거나, 평균 수신률이 설정 기준보다 낮거나, 긴 간격이 너무 많으면
FAIL입니다. `required: false` 토픽은 미발견 시 SKIP 처리합니다.

## 4. rosbag 동시 저장 (선택)

대용량 영상/포인트클라우드는 디스크 사용량이 크므로 필요할 때만 실행합니다.

```bash
./scripts/record_bag.sh 60
```

측정과 bag 녹화는 서로 다른 터미널에서 동시에 실행할 수 있습니다. 최초의 정상
측정 결과를 검토한 뒤 해당 run 폴더를 `data/baseline/`으로 복사하면 기준 데이터로
고정할 수 있습니다.

## 판정값 조정

`config/topics.yaml`에서 토픽별로 다음 값을 조정합니다.

- `expected_hz`: 목표 주파수
- `min_rate_ratio`: 목표 대비 허용할 최소 평균 수신률 (기본 0.90)
- `max_gap_factor`: 목표 주기의 몇 배부터 긴 공백으로 볼지 (기본 2.0)
- `max_gap_ratio`: 전체 주기 중 긴 공백의 최대 비율
- `required`: 토픽 미발견을 실패로 볼지 여부

첫 측정에서는 CPU/GPU 부하가 안정된 뒤 60초 이상 수집하는 것을 권장합니다.

## 30분 A~H 고정 실험

새 실험은 `camera_30min_validation/` 아래에만 기록하며 기존 로그를 덮어쓰지
않습니다. 설정은 `config/camera_30min.yaml`에 있고 유효 실험 시간은 1,800초로
고정됩니다. `--test-duration`은 코드 점검 전용이며 결과를 반드시 `TEST_ONLY`로
표시하므로 30분 결과로 집계할 수 없습니다.

실행 전 다음을 사람이 확인해야 합니다.

- ZED 2i가 의도한 USB 포트에 연결되어 있고 `/dev/video*` 및 권한이 정상인지
- A~C 전에 ZED ROS wrapper가 HD720/30, `bgra8` 토픽을 발행하는지
- A~C에서 이미 로딩된 모델 서버의 `/health`가 정상인지
- D~H 전에 ROS wrapper 등 카메라 점유 프로세스가 종료되었는지
- 케이블/포트/SDK/드라이버 변경 및 케이스 사이 재시작을 실험 기록에 남겼는지

한 케이스씩 순차 실행합니다. 기존 디렉터리가 있으면 실행기는 덮어쓰지 않고
중단합니다.

```bash
cd /home/ubuntu/zed_2i_test
./scripts/run_camera_30min_case.sh A
# ... 환경/점유 상태를 확인한 뒤 B, C, D, E, F, G, H를 각각 실행
```

H는 실행 직전에 Native V4L2 수집기를 빌드합니다. D는 SDK `read()`만, E는
`grab+retrieve_image`, F는 SDK `read+retrieve_image`와 validity check OFF를
사용합니다. G/H의 기본 V4L2 조건은 과거 실험과 같은 stereo 2560x720, 30 FPS,
YUYV이고, 결과 RGB 분석 해상도 1280x720과 혼동하지 않도록 설정 스냅샷에
별도로 남깁니다.

모든 실행이 끝났거나 실패 결과까지 보존한 뒤 집계합니다.

```bash
/usr/bin/python3 scripts/analyze_camera_30min.py camera_30min_validation
```

집계기는 각 케이스에 `frames.csv`, `second_metrics.csv`, `minute_metrics.csv`,
`gap_events.csv`, `resource_metrics.csv`, `summary.json`, `errors.log`를 유지하고,
루트에 `comparison.csv`, `comparison.json`, 그래프 및
`CAMERA_30MIN_REPORT.md`를 생성합니다. 미실행/실패/짧은 실행은
`valid_30min_result=false`로 남으며 정상 완료로 대체되지 않습니다.

C는 이미지 저장, JSONL flush/fsync, 가능한 경우에만 Read-only
`GetRobotState` 및 JointState settle 관찰을 수행합니다. Arm/Gripper/Move/Servo,
Stop/Hold 서비스나 publisher는 만들지 않습니다.
