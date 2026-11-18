#!/usr/bin/env python3
"""Measure ROS 2 topic arrival periodicity and save machine-readable results."""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

import rclpy
import yaml
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rosidl_runtime_py.utilities import get_message


def header_stamp_seconds(message):
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return None
    value = float(stamp.sec) + float(stamp.nanosec) * 1e-9
    return value if value > 0.0 else None


def stats(values):
    if not values:
        return {"mean": None, "stdev": None, "min": None, "max": None}
    return {
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


class BaselineNode(Node):
    def __init__(self, selected):
        super().__init__("zed2i_topic_baseline")
        self.selected = selected
        self.samples = {item["name"]: [] for item in selected}
        # Node.subscriptions is a read-only rclpy property.
        self._topic_subscriptions = []

    def attach(self, discovered):
        for item in self.selected:
            name = item["name"]
            types = discovered.get(name, [])
            if not types:
                continue
            message_type = get_message(types[0])

            def callback(message, topic=name):
                wall = time.time()
                mono = time.monotonic()
                previous = self.samples[topic][-1][1] if self.samples[topic] else None
                stamp = header_stamp_seconds(message)
                self.samples[topic].append(
                    (wall, mono, None if previous is None else mono - previous,
                     stamp, None if stamp is None else wall - stamp)
                )

            self._topic_subscriptions.append(
                self.create_subscription(message_type, name, callback, qos_profile_sensor_data)
            )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--discovery-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.duration <= 0 or args.discovery_timeout <= 0:
        parser.error("duration과 discovery-timeout은 0보다 커야 합니다")
    return args


def main():
    args = parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with open(args.config, encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    defaults = config.get("defaults", {})
    selected = []
    for raw in config["topics"]:
        item = dict(defaults)
        item.update(raw)
        selected.append(item)

    rclpy.init()
    node = BaselineNode(selected)
    deadline = time.monotonic() + args.discovery_timeout
    discovered = {}
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)
        discovered = dict(node.get_topic_names_and_types())
        if all(item["name"] in discovered for item in selected if item.get("required", True)):
            break
    node.attach(discovered)

    found = sum(item["name"] in discovered for item in selected)
    print(f"측정 시작: {found}/{len(selected)}개 토픽 발견, {args.duration:g}초")
    started_wall = time.time()
    started_mono = time.monotonic()
    end = started_mono + args.duration
    try:
        while rclpy.ok() and time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=min(0.2, end - time.monotonic()))
    except KeyboardInterrupt:
        print("사용자 요청으로 측정을 조기 종료합니다.")
    actual_duration = time.monotonic() - started_mono

    with open(output / "samples.csv", "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["topic", "arrival_unix_s", "arrival_monotonic_s", "interval_s",
                         "header_stamp_s", "header_latency_s"])
        for item in selected:
            for sample in node.samples[item["name"]]:
                writer.writerow([item["name"], *sample])

    summaries = []
    for item in selected:
        name = item["name"]
        samples = node.samples[name]
        intervals = [row[2] for row in samples if row[2] is not None]
        latencies = [row[4] for row in samples if row[4] is not None]
        expected = float(item["expected_hz"])
        rate = len(samples) / actual_duration if actual_duration else 0.0
        gap_limit = float(item.get("max_gap_factor", 2.0)) / expected
        long_gaps = sum(value > gap_limit for value in intervals)
        gap_ratio = long_gaps / len(intervals) if intervals else 0.0
        discovered_type = discovered.get(name, [])
        required = bool(item.get("required", True))
        if not discovered_type:
            status = "FAIL" if required else "SKIP"
            reasons = ["topic_not_discovered"]
        elif not samples:
            status = "FAIL"
            reasons = ["no_messages"]
        else:
            reasons = []
            if rate < expected * float(item.get("min_rate_ratio", 0.90)):
                reasons.append("rate_below_threshold")
            if gap_ratio > float(item.get("max_gap_ratio", 0.01)):
                reasons.append("too_many_long_gaps")
            status = "FAIL" if reasons else "PASS"
        interval_stats = stats(intervals)
        latency_stats = stats(latencies)
        summaries.append({
            "topic": name,
            "type": discovered_type[0] if discovered_type else None,
            "required": required,
            "status": status,
            "reasons": reasons,
            "message_count": len(samples),
            "duration_s": actual_duration,
            "expected_hz": expected,
            "measured_hz": rate,
            "rate_ratio": rate / expected,
            "interval_s": interval_stats,
            "long_gap_threshold_s": gap_limit,
            "long_gap_count": long_gaps,
            "long_gap_ratio": gap_ratio,
            "header_latency_s": latency_stats,
        })

    payload = {
        "started_unix_s": started_wall,
        "duration_s": actual_duration,
        "config": str(Path(args.config).resolve()),
        "overall_status": "FAIL" if any(x["status"] == "FAIL" for x in summaries) else "PASS",
        "topics": summaries,
    }
    with open(output / "summary.json", "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with open(output / "summary.csv", "w", newline="", encoding="utf-8") as stream:
        fields = ["status", "topic", "type", "required", "message_count", "expected_hz",
                  "measured_hz", "rate_ratio", "interval_mean_s", "interval_stdev_s",
                  "interval_min_s", "interval_max_s", "long_gap_count", "long_gap_ratio",
                  "latency_mean_s", "reasons"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in summaries:
            writer.writerow({
                "status": row["status"], "topic": row["topic"], "type": row["type"],
                "required": row["required"], "message_count": row["message_count"],
                "expected_hz": row["expected_hz"], "measured_hz": row["measured_hz"],
                "rate_ratio": row["rate_ratio"], "interval_mean_s": row["interval_s"]["mean"],
                "interval_stdev_s": row["interval_s"]["stdev"],
                "interval_min_s": row["interval_s"]["min"],
                "interval_max_s": row["interval_s"]["max"],
                "long_gap_count": row["long_gap_count"], "long_gap_ratio": row["long_gap_ratio"],
                "latency_mean_s": row["header_latency_s"]["mean"],
                "reasons": ";".join(row["reasons"]),
            })

    node.destroy_node()
    rclpy.shutdown()
    for row in summaries:
        print(f"{row['status']:4} {row['topic']}: {row['measured_hz']:.2f} Hz "
              f"({row['message_count']} msgs), long gaps={row['long_gap_count']}")
    print(f"전체 판정: {payload['overall_status']}")
    return 1 if payload["overall_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
