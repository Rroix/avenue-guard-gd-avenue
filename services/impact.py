from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any


@dataclass(frozen=True)
class EngagementForecast:
    next_7_days: int
    daily_average_7d: float
    daily_average_28d: float
    trend_percent: float
    confidence: str
    anomalies: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "next_7_days": self.next_7_days,
            "daily_average_7d": round(self.daily_average_7d, 2),
            "daily_average_28d": round(self.daily_average_28d, 2),
            "trend_percent": round(self.trend_percent, 1),
            "confidence": self.confidence,
            "anomalies": list(self.anomalies),
        }


def forecast_engagement(series: Iterable[tuple[str, int]]) -> EngagementForecast:
    rows = [(str(day), max(0, int(value or 0))) for day, value in series]
    values = [value for _, value in rows]
    if not values:
        return EngagementForecast(0, 0.0, 0.0, 0.0, "insufficient data", ())

    last_7 = values[-7:]
    last_28 = values[-28:]
    avg_7 = mean(last_7)
    avg_28 = mean(last_28)
    trend = ((avg_7 - avg_28) / avg_28 * 100) if avg_28 else 0.0

    weekday_values: dict[int, list[int]] = {day: [] for day in range(7)}
    for key, value in rows[-84:]:
        try:
            weekday = datetime.fromisoformat(key).replace(tzinfo=timezone.utc).weekday()
        except ValueError:
            continue
        weekday_values[weekday].append(value)
    baseline = avg_7 * 0.65 + avg_28 * 0.35
    projections: list[float] = []
    today = datetime.now(timezone.utc).weekday()
    for offset in range(1, 8):
        samples = weekday_values[(today + offset) % 7]
        seasonal = mean(samples[-8:]) if samples else baseline
        projections.append(max(0.0, baseline * 0.6 + seasonal * 0.4))

    deviation = pstdev(last_28) if len(last_28) > 1 else 0.0
    anomalies: list[str] = []
    if deviation > 0:
        center = avg_28
        for day, value in rows[-14:]:
            z_score = (value - center) / deviation
            if abs(z_score) >= 2.5:
                anomalies.append(f"{day}: {value:,} ({z_score:+.1f}σ)")
    coverage = len(values)
    coefficient = deviation / avg_28 if avg_28 else 1.0
    if coverage >= 56 and coefficient < 0.45:
        confidence = "high"
    elif coverage >= 21 and coefficient < 0.8:
        confidence = "medium"
    else:
        confidence = "low"
    forecast = math.floor(sum(projections) + 0.5)
    return EngagementForecast(
        forecast, avg_7, avg_28, trend, confidence, tuple(anomalies[-5:])
    )
