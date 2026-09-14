from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

VALID_NOTIFICATION_MODES = {"channel", "dm", "both", "none"}
NOT_SENT_RESULTS = {"rejected", "level_doesnt_exist", "stolen_level", "already_rated"}


def normalize_notification_mode(value: Any) -> str:
    mode = str(value or "channel").strip().casefold()
    return mode if mode in VALID_NOTIFICATION_MODES else "channel"


@dataclass(frozen=True)
class RequestAge:
    seconds: int
    hours: float
    label: str
    indicator: str
    status: str


def request_age(
    created_ts: Any, now_ts: int | None = None, thresholds: Iterable[int] = (12, 24, 48)
) -> RequestAge:
    now = int(now_ts or time.time())
    try:
        seconds = max(0, now - int(created_ts or now))
    except (TypeError, ValueError):
        seconds = 0
    hours = seconds / 3600
    limits = sorted(max(1, int(value)) for value in thresholds)
    while len(limits) < 3:
        limits.append((limits[-1] if limits else 12) * 2)
    if hours >= limits[2]:
        status, indicator = "Overdue", "🔴"
    elif hours >= limits[1]:
        status, indicator = "Due soon", "🟠"
    elif hours >= limits[0]:
        status, indicator = "Aging", "🟡"
    else:
        status, indicator = "Fresh", "🟢"
    if seconds < 60:
        label = "just now"
    elif seconds < 3600:
        label = f"{seconds // 60}m ago"
    elif seconds < 86400:
        label = f"{seconds // 3600}h ago"
    else:
        label = f"{seconds // 86400}d ago"
    return RequestAge(seconds, hours, label, indicator, status)


def rejection_breakdown(rows: Iterable[Any]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        try:
            result = str(row["result"] or "").strip().casefold()
        except Exception:
            result = str(getattr(row, "result", "") or "").strip().casefold()
        if result in NOT_SENT_RESULTS:
            counter[result] += 1
    return dict(counter)


def compare_waves(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, str]:
    if not previous:
        return {
            "wave_comparison": "No earlier wave is available yet.",
            "request_delta": "n/a",
            "sent_rate_delta": "n/a",
        }
    current_total = int(current.get("total_requests", 0) or 0)
    previous_total = int(previous.get("total_requests", 0) or 0)
    current_reviewed = int(current.get("reviewed_count", 0) or 0)
    previous_reviewed = int(previous.get("reviewed_count", 0) or 0)
    current_sent = int(current.get("sent_count", 0) or 0)
    previous_sent = int(previous.get("sent_count", 0) or 0)
    request_delta = current_total - previous_total
    current_rate = current_sent / current_reviewed * 100 if current_reviewed else 0.0
    previous_rate = (
        previous_sent / previous_reviewed * 100 if previous_reviewed else 0.0
    )
    rate_delta = current_rate - previous_rate
    direction = "+" if request_delta > 0 else ""
    rate_direction = "+" if rate_delta > 0 else ""
    return {
        "request_delta": f"{direction}{request_delta}",
        "sent_rate_delta": f"{rate_direction}{rate_delta:.1f} pp",
        "wave_comparison": (
            f"Requests: **{direction}{request_delta}** vs wave {previous.get('wave_id', '?')}\n"
            f"Sent rate: **{rate_direction}{rate_delta:.1f} percentage points**"
        ),
    }
