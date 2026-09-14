from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ScheduledOpening:
    opening_id: int
    open_ts: int
    request_limit: int | None
    close_minutes: int | None
    request_type: str
    message: str

    @classmethod
    def from_row(cls, row: Any) -> ScheduledOpening:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return cls(
            opening_id=int(row["id"]),
            open_ts=int(row["open_ts"]),
            request_limit=int(row["request_limit"])
            if row["request_limit"] is not None
            else None,
            close_minutes=int(row["close_minutes"])
            if row["close_minutes"] is not None
            else None,
            request_type=str(row["request_type"] or "")
            if "request_type" in keys
            else "",
            message=str(row["open_message"] or "") if "open_message" in keys else "",
        )

    def discord_time(self) -> str:
        return f"<t:{self.open_ts}:F> (<t:{self.open_ts}:R>)"


def opening_is_due(open_ts: Any, *, now_ts: int) -> bool:
    try:
        return int(open_ts) <= int(now_ts)
    except (TypeError, ValueError):
        return False


def local_time_round_trip(candidate: datetime, timezone) -> bool:
    timestamp = candidate.timestamp()
    return (
        datetime.fromtimestamp(timestamp, timezone).replace(fold=candidate.fold)
        == candidate
    )
