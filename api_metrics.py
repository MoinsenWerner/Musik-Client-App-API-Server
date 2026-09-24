"""SQLite-backed request metrics and aggregation helpers."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone


PERIODS = {
    "1h": ("1 Stunde", 60 * 60, 5 * 60),
    "3h": ("3 Stunden", 3 * 60 * 60, 15 * 60),
    "6h": ("6 Stunden", 6 * 60 * 60, 30 * 60),
    "12h": ("12 Stunden", 12 * 60 * 60, 60 * 60),
    "24h": ("24 Stunden", 24 * 60 * 60, 60 * 60),
    "3d": ("3 Tage", 3 * 24 * 60 * 60, 3 * 60 * 60),
    "1w": ("1 Woche", 7 * 24 * 60 * 60, 6 * 60 * 60),
    "2w": ("2 Wochen", 14 * 24 * 60 * 60, 12 * 60 * 60),
    "1m": ("1 Monat", 30 * 24 * 60 * 60, 24 * 60 * 60),
    "3m": ("3 Monate", 90 * 24 * 60 * 60, 3 * 24 * 60 * 60),
    "6m": ("6 Monate", 182 * 24 * 60 * 60, 7 * 24 * 60 * 60),
    "1y": ("1 Jahr", 365 * 24 * 60 * 60, 14 * 24 * 60 * 60),
    "3y": ("3 Jahre", 3 * 365 * 24 * 60 * 60, 30 * 24 * 60 * 60),
}

_WRITE_LOCK = threading.Lock()


def initialize_metrics_database(database_file):
    with sqlite3.connect(database_file) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS api_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at INTEGER NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                username TEXT NOT NULL,
                status_code INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_requests_time "
            "ON api_requests(requested_at)"
        )


def record_request(database_file, endpoint, method, username, status_code, requested_at=None):
    timestamp = int(requested_at if requested_at is not None else time.time())
    with _WRITE_LOCK, sqlite3.connect(database_file, timeout=10) as connection:
        connection.execute(
            "INSERT INTO api_requests "
            "(requested_at, endpoint, method, username, status_code) VALUES (?, ?, ?, ?, ?)",
            (timestamp, endpoint, method, username or "anonymous", int(status_code)),
        )


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _timeline(bucket_start, bucket_count, bucket_seconds, counts):
    return [
        {
            "timestamp": _iso_timestamp(bucket_start + index * bucket_seconds),
            "count": counts.get(index, 0),
        }
        for index in range(bucket_count)
    ]


def build_metrics_summary(database_file, period="24h", now=None):
    if period not in PERIODS:
        raise ValueError(f"Unbekannter Zeitraum: {period}")

    label, period_seconds, bucket_seconds = PERIODS[period]
    end_timestamp = int(now if now is not None else time.time())
    start_timestamp = end_timestamp - period_seconds
    bucket_start = start_timestamp - (start_timestamp % bucket_seconds)
    bucket_count = ((end_timestamp - bucket_start) // bucket_seconds) + 1

    with sqlite3.connect(database_file) as connection:
        rows = connection.execute(
            "SELECT requested_at, endpoint, method, username, status_code "
            "FROM api_requests WHERE requested_at >= ? AND requested_at <= ? "
            "ORDER BY requested_at",
            (start_timestamp, end_timestamp),
        ).fetchall()

    overall_counts = defaultdict(int)
    bucket_users = defaultdict(set)
    endpoint_data = {}
    user_totals = defaultdict(int)
    user_timeline_counts = defaultdict(lambda: defaultdict(int))
    for requested_at, endpoint, method, username, status_code in rows:
        bucket_index = (requested_at - bucket_start) // bucket_seconds
        overall_counts[bucket_index] += 1
        bucket_users[bucket_index].add(username)
        user_totals[username] += 1
        user_timeline_counts[username][bucket_index] += 1
        key = f"{method} {endpoint}"
        data = endpoint_data.setdefault(
            key,
            {
                "endpoint": endpoint,
                "method": method,
                "total_calls": 0,
                "successful_calls": 0,
                "successful_timeline_counts": defaultdict(int),
                "timeline_counts": defaultdict(int),
                "users": {},
            },
        )
        data["total_calls"] += 1
        if status_code < 400:
            data["successful_calls"] += 1
            data["successful_timeline_counts"][bucket_index] += 1
        data["timeline_counts"][bucket_index] += 1
        user_data = data["users"].setdefault(
            username,
            {"username": username, "total_calls": 0, "timeline_counts": defaultdict(int)},
        )
        user_data["total_calls"] += 1
        user_data["timeline_counts"][bucket_index] += 1

    endpoints = []
    for data in endpoint_data.values():
        users = []
        for user_data in data.pop("users").values():
            user_data["timeline"] = _timeline(
                bucket_start, bucket_count, bucket_seconds, user_data.pop("timeline_counts")
            )
            users.append(user_data)
        users.sort(key=lambda item: (-item["total_calls"], item["username"].lower()))
        data["timeline"] = _timeline(
            bucket_start, bucket_count, bucket_seconds, data.pop("timeline_counts")
        )
        data["successful_timeline"] = _timeline(
            bucket_start, bucket_count, bucket_seconds, data.pop("successful_timeline_counts")
        )
        data["users"] = users
        endpoints.append(data)
    endpoints.sort(key=lambda item: (-item["total_calls"], item["endpoint"], item["method"]))

    users = [
        {
            "username": username,
            "total_calls": count,
            "timeline": _timeline(
                bucket_start, bucket_count, bucket_seconds, user_timeline_counts[username]
            ),
        }
        for username, count in sorted(user_totals.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    return {
        "generated_at": _iso_timestamp(end_timestamp),
        "period": period,
        "period_label": label,
        "period_seconds": period_seconds,
        "from": _iso_timestamp(start_timestamp),
        "to": _iso_timestamp(end_timestamp),
        "bucket_seconds": bucket_seconds,
        "available_periods": [
            {"value": value, "label": details[0]} for value, details in PERIODS.items()
        ],
        "total_calls": len(rows),
        "unique_user_count": len(users),
        "users": users,
        "timeline": _timeline(bucket_start, bucket_count, bucket_seconds, overall_counts),
        "unique_users_timeline": _timeline(
            bucket_start,
            bucket_count,
            bucket_seconds,
            {index: len(usernames) for index, usernames in bucket_users.items()},
        ),
        "endpoints": endpoints,
    }
