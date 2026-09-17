from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


LEGACY_REVIEW_SYSTEM = "legacy"
PPS_V1_REVIEW_SYSTEM = "pps_v1"
PPS_SEND_TYPES = ("rate", "feature", "epic", "legendary", "mythic")
PPS_SEND_TYPE_LABELS = {
    "rate": "Rate",
    "feature": "Feature",
    "epic": "Epic",
    "legendary": "Legendary",
    "mythic": "Mythic",
}
PPS_QUEUE_ACTIVE_STATES = ("queued", "in_cycle")
PPS_QUEUE_REFRESH_STATES = (*PPS_QUEUE_ACTIVE_STATES, "awaiting_outcome")
PPS_ROUTE_TYPES = ("direct", "network", "stream", "event", "other")
PPS_ATTEMPT_STATUSES = ("planned", "attempted", "submitted_to_mod", "failed")
PUBLIC_PRIORITY_BAND_THRESHOLDS = (
    (0.10, "top_priority"),
    (0.30, "high_priority"),
    (0.70, "standard_priority"),
    (1.00, "lower_priority"),
)
PUBLIC_QUEUE_STATES = {
    "queued",
    "in_cycle",
    "awaiting_outcome",
    "rated",
    "withdrawn",
    "invalid",
}


@dataclass(frozen=True)
class PrioritySettings:
    new_wave_version: str
    model_version: str
    prestige_base: float
    prestige_x: dict[str, float]
    creator_numerator: float
    creator_slope: float
    creator_offset: float
    creator_zero_from_cp: int
    waiting_multiplier: float
    waiting_exponent: float
    waiting_score_cap_cycles: int
    cp_refresh_seconds: int
    level_refresh_seconds: int
    outcome_window_seconds: int
    maintenance_interval_seconds: int
    maintenance_batch_size: int
    failure_retry_seconds: int


def public_priority_band(position: Any, total: Any) -> str | None:
    """Return a privacy-safe queue band without exposing rank or score.

    The percentile is position / total with inclusive 10%, 30% and 70%
    boundaries. Position one is always top priority for small queues. Invalid
    or inactive ranks stay null.
    """
    if isinstance(position, bool) or isinstance(total, bool):
        return None
    try:
        normalized_position = int(position)
        normalized_total = int(total)
    except (TypeError, ValueError):
        return None
    if (
        normalized_position < 1
        or normalized_total < 1
        or normalized_position > normalized_total
    ):
        return None
    if normalized_position == 1:
        return "top_priority"
    percentile = normalized_position / normalized_total
    for threshold, band in PUBLIC_PRIORITY_BAND_THRESHOLDS:
        if percentile <= threshold:
            return band
    return "lower_priority"


def public_lifecycle_state(
    queue_state: Any,
    *,
    submitted_to_mod_at: Any = None,
    rated_observed_at: Any = None,
    rated_within_window: Any = None,
    outcome_window_completed_at: Any = None,
) -> dict[str, str]:
    """Separate public queue, outreach and outcome concepts conservatively."""
    state = str(queue_state or "").strip().casefold()
    public_queue_state = state if state in PUBLIC_QUEUE_STATES else "unknown"
    submitted = _positive_int_or_none(submitted_to_mod_at)
    rated_observed = _positive_int_or_none(rated_observed_at)
    outcome_completed = _positive_int_or_none(outcome_window_completed_at)

    outreach = {
        "queued": "queued_for_outreach",
        "in_cycle": "outreach_in_progress",
        "awaiting_outcome": "reached_moderator" if submitted else "unknown",
        "rated": "outreach_complete" if submitted else "unknown",
        "withdrawn": "withdrawn",
        "invalid": "level_unavailable",
    }.get(public_queue_state, "unknown")

    if rated_observed or public_queue_state == "rated" or (
        outcome_completed and rated_within_window in (1, "1", True)
    ):
        outcome = "rated"
    elif submitted and outcome_completed and rated_within_window in (0, "0", False):
        outcome = "not_observed_rated_within_window"
    elif submitted:
        outcome = "awaiting_outcome"
    else:
        outcome = "unknown"

    return {
        "public_queue_state": public_queue_state,
        "public_outreach_state": outreach,
        "public_outcome_state": outcome,
    }


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _number(
    value: Any,
    default: float,
    *,
    minimum: float,
    maximum: float,
    path: str,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a number")
    try:
        parsed = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a number") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError(f"{path} must be between {minimum:g} and {maximum:g}")
    return parsed


def _integer(
    value: Any,
    default: int,
    *,
    minimum: int,
    maximum: int,
    path: str,
) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be an integer")
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return parsed


def priority_settings(data: dict[str, Any]) -> PrioritySettings:
    raw = data.get("priority_system")
    if not isinstance(raw, dict):
        raw = {}
    version = str(raw.get("new_wave_version") or PPS_V1_REVIEW_SYSTEM).strip().casefold()
    if version not in {LEGACY_REVIEW_SYSTEM, PPS_V1_REVIEW_SYSTEM}:
        raise ValueError("new_wave_version must be legacy or pps_v1")
    model_version = str(raw.get("model_version") or PPS_V1_REVIEW_SYSTEM).strip()
    if model_version != PPS_V1_REVIEW_SYSTEM:
        raise ValueError("model_version must be pps_v1 for this release")

    prestige_raw = raw.get("prestige_x") if isinstance(raw.get("prestige_x"), dict) else {}
    prestige_defaults = {
        "rate": 0.0,
        "feature": 1.25,
        "epic": 2.5,
        "legendary": 3.75,
        "mythic": 5.0,
    }
    prestige_x = {
        key: _number(
            prestige_raw.get(key),
            default,
            minimum=0.0,
            maximum=10.0,
            path=f"prestige_x.{key}",
        )
        for key, default in prestige_defaults.items()
    }
    if any(
        prestige_x[left] >= prestige_x[right]
        for left, right in zip(PPS_SEND_TYPES, PPS_SEND_TYPES[1:], strict=False)
    ):
        raise ValueError("prestige_x values must increase from rate through mythic")

    creator = raw.get("creator_opportunity") if isinstance(raw.get("creator_opportunity"), dict) else {}
    waiting = raw.get("waiting") if isinstance(raw.get("waiting"), dict) else {}
    return PrioritySettings(
        new_wave_version=version,
        model_version=model_version,
        prestige_base=_number(raw.get("prestige_base"), 1.8, minimum=1.01, maximum=10.0, path="prestige_base"),
        prestige_x=prestige_x,
        creator_numerator=_number(creator.get("numerator"), 0.25, minimum=0.0001, maximum=100.0, path="creator_opportunity.numerator"),
        creator_slope=_number(creator.get("slope"), 0.06, minimum=0.0001, maximum=100.0, path="creator_opportunity.slope"),
        creator_offset=_number(creator.get("offset"), 0.01, minimum=0.0001, maximum=100.0, path="creator_opportunity.offset"),
        creator_zero_from_cp=_integer(creator.get("zero_from_cp"), 4, minimum=0, maximum=100000, path="creator_opportunity.zero_from_cp"),
        waiting_multiplier=_number(waiting.get("multiplier"), 1.5, minimum=0.0, maximum=100.0, path="waiting.multiplier"),
        waiting_exponent=_number(waiting.get("exponent"), 1.5, minimum=0.1, maximum=10.0, path="waiting.exponent"),
        waiting_score_cap_cycles=_integer(waiting.get("score_cap_cycles"), 4, minimum=0, maximum=100, path="waiting.score_cap_cycles"),
        cp_refresh_seconds=_integer(raw.get("cp_refresh_hours"), 24, minimum=1, maximum=720, path="cp_refresh_hours") * 3600,
        level_refresh_seconds=_integer(raw.get("level_refresh_hours"), 6, minimum=1, maximum=720, path="level_refresh_hours") * 3600,
        outcome_window_seconds=_integer(raw.get("outcome_window_days"), 30, minimum=1, maximum=3650, path="outcome_window_days") * 86400,
        maintenance_interval_seconds=_integer(raw.get("maintenance_interval_seconds"), 300, minimum=60, maximum=86400, path="maintenance_interval_seconds"),
        maintenance_batch_size=_integer(raw.get("maintenance_batch_size"), 5, minimum=1, maximum=25, path="maintenance_batch_size"),
        failure_retry_seconds=_integer(raw.get("failure_retry_minutes"), 15, minimum=1, maximum=1440, path="failure_retry_minutes") * 60,
    )


def normalize_send_type(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in PPS_SEND_TYPES else None


def send_type_label(value: Any) -> str:
    normalized = normalize_send_type(value)
    return PPS_SEND_TYPE_LABELS.get(normalized or "", "Unknown")


def prestige_component(send_type: str, settings: PrioritySettings) -> tuple[float, float]:
    normalized = normalize_send_type(send_type)
    if normalized is None:
        raise ValueError("Unknown PPS send type")
    tier = settings.prestige_x[normalized]
    return tier, settings.prestige_base**tier - 1.0


def creator_opportunity_component(
    creator_points: int | None,
    settings: PrioritySettings,
) -> float | None:
    if creator_points is None:
        return None
    points = max(0, int(creator_points))
    if points >= settings.creator_zero_from_cp:
        return 0.0
    denominator = settings.creator_slope * points + settings.creator_offset
    return max(0.0, math.log(settings.creator_numerator / denominator))


def waiting_component(waiting_cycles: int, settings: PrioritySettings) -> float:
    capped = min(max(0, int(waiting_cycles)), settings.waiting_score_cap_cycles)
    return settings.waiting_multiplier * capped**settings.waiting_exponent


def score_components(
    send_type: str,
    creator_points: int | None,
    waiting_cycles: int,
    settings: PrioritySettings,
) -> dict[str, Any]:
    tier, prestige = prestige_component(send_type, settings)
    creator = creator_opportunity_component(creator_points, settings)
    waiting = waiting_component(waiting_cycles, settings)
    complete = creator is not None
    return {
        "prestige_t": tier,
        "prestige_component_f": prestige,
        "creator_component_g": creator,
        "waiting_component_h": waiting,
        "priority_points": prestige + creator + waiting if complete else None,
        "priority_complete": int(complete),
        "model_version": settings.model_version,
    }
