#!/usr/bin/env python3
"""Generate data.js for the local battery history dashboard."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


CHARGE_PATTERN = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<offset>[+-]\d{4}) "
    r".*?(?:Charge:\s?)(?P<pct>\d+)%?\)?",
    re.MULTILINE,
)


def read_pmset_log() -> str:
    return subprocess.run(
        ["pmset", "-g", "log"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def read_live_battery() -> dict[str, object] | None:
    result = subprocess.run(
        ["pmset", "-g", "batt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    match = re.search(r"(\d+)%;\s*([^;]+);", result)
    if not match:
        return None
    return {
        "percentage": int(match.group(1)),
        "status": match.group(2).strip(),
    }


def extract_rows(text: str, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
    rows: list[tuple[datetime, int]] = []
    for match in CHARGE_PATTERN.finditer(text):
        stamp = datetime.strptime(match.group("stamp"), "%Y-%m-%d %H:%M:%S")
        if start <= stamp <= end:
            rows.append((stamp, int(match.group("pct"))))
    return rows


def round_down(value: datetime, minutes: int) -> datetime:
    discard = timedelta(
        minutes=value.minute % minutes,
        seconds=value.second,
        microseconds=value.microsecond,
    )
    return value - discard


def build_samples(
    rows: list[tuple[datetime, int]],
    start: datetime,
    end: datetime,
    interval_minutes: int,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    cursor = round_down(start + timedelta(minutes=interval_minutes), interval_minutes)
    idx = 0
    last: tuple[datetime, int] | None = None

    while cursor <= end:
        while idx < len(rows) and rows[idx][0] <= cursor:
            last = rows[idx]
            idx += 1
        if last:
            observed_at, percentage = last
            samples.append(
                {
                    "time": cursor.isoformat(timespec="minutes"),
                    "percentage": percentage,
                    "observedAt": observed_at.isoformat(timespec="seconds"),
                    "minutesSinceObservation": int((cursor - observed_at).total_seconds() // 60),
                }
            )
        cursor += timedelta(minutes=interval_minutes)
    return samples


def build_change_points(rows: list[tuple[datetime, int]]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    previous: int | None = None
    for stamp, percentage in rows:
        if percentage != previous:
            points.append(
                {
                    "time": stamp.isoformat(timespec="seconds"),
                    "percentage": percentage,
                }
            )
            previous = percentage
    return points


def build_events(rows: list[tuple[datetime, int]]) -> list[dict[str, object]]:
    return [
        {
            "time": stamp.isoformat(timespec="seconds"),
            "percentage": percentage,
        }
        for stamp, percentage in rows
    ]


def build_power_usage_buckets(
    samples: list[dict[str, object]],
    live: dict[str, object] | None,
    end: datetime,
    interval_minutes: int = 30,
) -> list[dict[str, object]]:
    if not samples:
        return []

    enriched_samples = list(samples)
    if live is not None:
        enriched_samples.append(
            {
                "time": end.isoformat(timespec="minutes"),
                "percentage": int(live["percentage"]),
            }
        )

    buckets: list[dict[str, object]] = []
    start = datetime.fromisoformat(str(enriched_samples[0]["time"]))
    cursor = round_down(start, interval_minutes)
    step = timedelta(minutes=interval_minutes)
    idx = 0
    last_sample: dict[str, object] | None = None

    while cursor <= end:
        bucket_end = cursor + step
        bucket_start_value: int | None = None
        bucket_end_value: int | None = None

        while idx < len(enriched_samples) and datetime.fromisoformat(str(enriched_samples[idx]["time"])) <= cursor:
            last_sample = enriched_samples[idx]
            idx += 1
        if last_sample is not None:
            bucket_start_value = int(last_sample["percentage"])

        scan_idx = idx
        scan_last = last_sample
        while scan_idx < len(enriched_samples) and datetime.fromisoformat(str(enriched_samples[scan_idx]["time"])) <= bucket_end:
            scan_last = enriched_samples[scan_idx]
            scan_idx += 1
        if scan_last is not None:
            bucket_end_value = int(scan_last["percentage"])

        if bucket_start_value is not None and bucket_end_value is not None:
            delta = bucket_start_value - bucket_end_value
            buckets.append(
                {
                    "time": cursor.isoformat(timespec="minutes"),
                    "dropPercentagePoints": max(delta, 0),
                    "chargeGainPercentagePoints": max(-delta, 0),
                }
            )
        cursor += step
    return buckets




def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=12, help="History window in hours.")
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Sampling interval in minutes for the rendered series.",
    )
    args = parser.parse_args()

    end = datetime.now().replace(microsecond=0)
    start = end - timedelta(hours=args.hours)
    log_text = read_pmset_log()
    rows = extract_rows(log_text, start, end)
    live = read_live_battery()

    samples = build_samples(rows, start, end, args.interval)
    payload = {
        "generatedAt": end.isoformat(timespec="seconds"),
        "windowStart": start.isoformat(timespec="seconds"),
        "windowEnd": end.isoformat(timespec="seconds"),
        "intervalMinutes": args.interval,
        "sampleCount": len(rows),
        "liveBattery": live,
        "events": build_events(rows),
        "samples": samples,
        "changePoints": build_change_points(rows),
        "powerUsageBuckets": build_power_usage_buckets(samples, live, end),
    }

    output = Path(__file__).with_name("data.js")
    output.write_text(
        "window.BATTERY_HISTORY = " + json.dumps(payload, indent=2) + ";\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}")
    print(f"Extracted {len(rows)} recorded battery events")


if __name__ == "__main__":
    main()
