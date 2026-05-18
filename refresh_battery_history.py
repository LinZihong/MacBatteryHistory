#!/usr/bin/env python3
"""Generate data.js for the local battery history dashboard."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

POWERLOG_DB = Path("/private/var/db/powerlog/Library/BatteryLife/CurrentPowerlog.PLSQL")


def read_powerlog_rows(start: datetime, end: datetime) -> list[tuple[datetime, int, bool]]:
    with sqlite3.connect(POWERLOG_DB) as connection:
        rows = connection.execute(
            """
            SELECT timestamp, Level, IsCharging
            FROM PLBatteryAgent_EventBackward_BatteryUI
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp
            """,
            (start.timestamp(), end.timestamp()),
        ).fetchall()
    return [
        (datetime.fromtimestamp(timestamp), int(level), bool(is_charging))
        for timestamp, level, is_charging in rows
    ]


def read_latest_battery_row() -> tuple[datetime, int, bool] | None:
    with sqlite3.connect(POWERLOG_DB) as connection:
        row = connection.execute(
            """
            SELECT timestamp, Level, IsCharging
            FROM PLBatteryAgent_EventBackward_BatteryUI
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    timestamp, level, is_charging = row
    return datetime.fromtimestamp(timestamp), int(level), bool(is_charging)


def round_down(value: datetime, minutes: int) -> datetime:
    discard = timedelta(
        minutes=value.minute % minutes,
        seconds=value.second,
        microseconds=value.microsecond,
    )
    return value - discard


def build_samples(
    rows: list[tuple[datetime, int, bool]],
    start: datetime,
    end: datetime,
    interval_minutes: int,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    cursor = round_down(start + timedelta(minutes=interval_minutes), interval_minutes)
    idx = 0
    last: tuple[datetime, int, bool] | None = None

    while cursor <= end:
        while idx < len(rows) and rows[idx][0] <= cursor:
            last = rows[idx]
            idx += 1
        if last:
            observed_at, percentage, is_charging = last
            samples.append(
                {
                    "time": cursor.isoformat(timespec="minutes"),
                    "percentage": percentage,
                    "observedAt": observed_at.isoformat(timespec="seconds"),
                    "minutesSinceObservation": int((cursor - observed_at).total_seconds() // 60),
                    "isCharging": is_charging,
                }
            )
        cursor += timedelta(minutes=interval_minutes)
    return samples


def build_change_points(rows: list[tuple[datetime, int, bool]]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    previous: int | None = None
    for stamp, percentage, _ in rows:
        if percentage != previous:
            points.append(
                {
                    "time": stamp.isoformat(timespec="seconds"),
                    "percentage": percentage,
                }
            )
            previous = percentage
    return points


def build_events(rows: list[tuple[datetime, int, bool]]) -> list[dict[str, object]]:
    return [
        {
            "time": stamp.isoformat(timespec="seconds"),
            "percentage": percentage,
            "isCharging": is_charging,
        }
        for stamp, percentage, is_charging in rows
    ]


def build_charging_intervals(rows: list[tuple[datetime, int, bool]]) -> list[dict[str, str]]:
    intervals: list[dict[str, str]] = []
    start: datetime | None = None

    for stamp, _, is_charging in rows:
        if is_charging and start is None:
            start = stamp
        elif not is_charging and start is not None:
            intervals.append(
                {
                    "start": start.isoformat(timespec="seconds"),
                    "end": stamp.isoformat(timespec="seconds"),
                }
            )
            start = None

    if start is not None and rows:
        intervals.append(
            {
                "start": start.isoformat(timespec="seconds"),
                "end": rows[-1][0].isoformat(timespec="seconds"),
            }
        )

    return intervals


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
    rows = read_powerlog_rows(start, end)
    latest = read_latest_battery_row()
    live = (
        {
            "percentage": latest[1],
            "status": "charging" if latest[2] else "discharging",
            "observedAt": latest[0].isoformat(timespec="seconds"),
        }
        if latest is not None
        else None
    )

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
        "chargingIntervals": build_charging_intervals(rows),
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
