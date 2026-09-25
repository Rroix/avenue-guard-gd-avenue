from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from typing import Any, Iterable


CREDIBLE_LEVELS = (0.50, 0.80, 0.90, 0.95)


def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(minimum, min(maximum, parsed))


def _integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class ReadinessPolicy:
    min_observations: int
    min_successes: int
    min_failures: int
    max_interval_width: float
    max_stale_seconds: int


@dataclass(frozen=True)
class BayesianSettings:
    enabled: bool
    public_auto_activate: bool
    access_version: str
    rating_version: str
    capacity_version: str
    prior_alpha: float
    prior_beta: float
    carryover_effective_n: float
    subgroup_prior_effective_n: float
    subgroup_min_observations: int
    monte_carlo_draws: int
    access: ReadinessPolicy
    rating: ReadinessPolicy
    calibration_min_predictions: int
    calibration_max_ece: float
    calibration_max_brier: float
    refresh_seconds: int


def bayesian_settings(data: dict[str, Any]) -> BayesianSettings:
    priority = data.get("priority_system") if isinstance(data.get("priority_system"), dict) else {}
    raw = priority.get("bayesian") if isinstance(priority.get("bayesian"), dict) else {}
    access = raw.get("access") if isinstance(raw.get("access"), dict) else {}
    rating = raw.get("rating") if isinstance(raw.get("rating"), dict) else {}
    calibration = raw.get("calibration") if isinstance(raw.get("calibration"), dict) else {}

    def policy(source: dict[str, Any], default_n: int, width: float, stale_days: int) -> ReadinessPolicy:
        return ReadinessPolicy(
            min_observations=_integer(source.get("min_observations"), default_n, 1, 1_000_000),
            min_successes=_integer(source.get("min_successes"), 5, 1, 1_000_000),
            min_failures=_integer(source.get("min_failures"), 5, 1, 1_000_000),
            max_interval_width=_number(source.get("max_90_interval_width"), width, 0.01, 1.0),
            max_stale_seconds=_integer(source.get("max_stale_days"), stale_days, 1, 3650) * 86400,
        )

    versions = raw.get("model_versions") if isinstance(raw.get("model_versions"), dict) else {}
    return BayesianSettings(
        enabled=bool(raw.get("enabled", True)),
        public_auto_activate=bool(raw.get("public_probability_auto_activate", True)),
        access_version=str(versions.get("access") or "access_model_v1"),
        rating_version=str(versions.get("rating") or "rating_model_v1"),
        capacity_version=str(versions.get("capacity") or "capacity_model_v1"),
        prior_alpha=_number(raw.get("prior_alpha"), 1.0, 0.01, 1_000.0),
        prior_beta=_number(raw.get("prior_beta"), 1.0, 0.01, 1_000.0),
        carryover_effective_n=_number(raw.get("carryover_effective_n"), 4.0, 0.0, 100.0),
        subgroup_prior_effective_n=_number(raw.get("subgroup_prior_effective_n"), 4.0, 0.0, 100.0),
        subgroup_min_observations=_integer(raw.get("subgroup_min_observations"), 15, 1, 1_000_000),
        monte_carlo_draws=_integer(raw.get("monte_carlo_draws"), 20_000, 1_000, 500_000),
        access=policy(access, 40, 0.45, 45),
        rating=policy(rating, 25, 0.50, 60),
        calibration_min_predictions=_integer(calibration.get("min_resolved_predictions"), 50, 5, 1_000_000),
        calibration_max_ece=_number(calibration.get("max_ece"), 0.15, 0.01, 1.0),
        calibration_max_brier=_number(calibration.get("max_brier"), 0.30, 0.01, 1.0),
        refresh_seconds=_integer(raw.get("refresh_seconds"), 1800, 60, 86400),
    )


def beta_posterior(successes: int, failures: int, alpha: float = 1.0, beta: float = 1.0) -> tuple[float, float]:
    return alpha + max(0, int(successes)), beta + max(0, int(failures))


def beta_mean(alpha: float, beta: float) -> float:
    return alpha / (alpha + beta)


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1e-300 if abs(d) < 1e-300 else d
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def regularized_beta(x: float, alpha: float, beta: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(alpha + beta) - math.lgamma(alpha) - math.lgamma(beta)
        + alpha * math.log(x) + beta * math.log1p(-x)
    )
    if x < (alpha + 1.0) / (alpha + beta + 2.0):
        return front * _beta_continued_fraction(alpha, beta, x) / alpha
    return 1.0 - front * _beta_continued_fraction(beta, alpha, 1.0 - x) / beta


def beta_quantile(probability: float, alpha: float, beta: float) -> float:
    target = min(1.0, max(0.0, float(probability)))
    if target <= 0:
        return 0.0
    if target >= 1:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(80):
        midpoint = (low + high) / 2.0
        if regularized_beta(midpoint, alpha, beta) < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def credible_intervals(alpha: float, beta: float, levels: Iterable[float] = CREDIBLE_LEVELS) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {}
    for level in levels:
        tail = (1.0 - float(level)) / 2.0
        result[str(int(round(level * 100)))] = [
            beta_quantile(tail, alpha, beta),
            beta_quantile(1.0 - tail, alpha, beta),
        ]
    return result


def evidence_strength(observations: int, interval_90_width: float) -> str:
    if observations < 25 or interval_90_width > 0.55:
        return "limited"
    if observations < 75 or interval_90_width > 0.30:
        return "moderate"
    return "strong"


def model_status(
    *,
    observations: int,
    successes: int,
    failures: int,
    interval_90_width: float,
    freshness_ts: int | None,
    now_ts: int,
    policy: ReadinessPolicy,
    calibration: dict[str, Any] | None = None,
    paused: bool = False,
) -> tuple[str, str]:
    if paused:
        return "paused", "Publication was paused by an authorized administrator."
    if freshness_ts is not None and now_ts - int(freshness_ts) > policy.max_stale_seconds:
        return "degraded", "The newest eligible evidence is stale."
    if calibration and calibration.get("degraded"):
        return "degraded", "Resolved predictions no longer meet calibration safeguards."
    if observations < policy.min_observations or successes < policy.min_successes or failures < policy.min_failures:
        if observations >= max(5, policy.min_observations // 2):
            return "provisional", "Evidence is accumulating but has not met all publication thresholds."
        return "collecting", "Not enough eligible observations have been collected."
    if interval_90_width > policy.max_interval_width:
        return "provisional", "The 90% credible interval is still too wide."
    return "active", "All configured evidence, freshness, and uncertainty thresholds are met."


def calibration_metrics(predictions: Iterable[tuple[float, int]], bins: int = 10) -> dict[str, Any]:
    values = [(min(1.0, max(0.0, float(p))), 1 if int(y) else 0) for p, y in predictions]
    if not values:
        return {"count": 0, "brier": None, "ece": None, "bins": []}
    brier = sum((p - y) ** 2 for p, y in values) / len(values)
    grouped: list[dict[str, Any]] = []
    ece = 0.0
    for index in range(max(1, bins)):
        lower = index / bins
        upper = (index + 1) / bins
        selected = [(p, y) for p, y in values if lower <= p <= upper and (index == bins - 1 or p < upper)]
        if not selected:
            grouped.append({"lower": lower, "upper": upper, "count": 0, "mean_prediction": None, "observed_rate": None})
            continue
        mean_prediction = sum(p for p, _ in selected) / len(selected)
        observed_rate = sum(y for _, y in selected) / len(selected)
        ece += len(selected) / len(values) * abs(mean_prediction - observed_rate)
        grouped.append({"lower": lower, "upper": upper, "count": len(selected), "mean_prediction": mean_prediction, "observed_rate": observed_rate})
    return {"count": len(values), "brier": brier, "ece": ece, "bins": grouped}


def capacity_forecast(alpha: float, beta: float, opportunities: int, *, draws: int = 20_000, seed: int = 0) -> dict[str, Any]:
    count = max(0, int(opportunities))
    sample_count = max(1, int(draws))
    generator = random.Random(int(seed))
    successes: list[int] = []
    for _ in range(sample_count):
        probability = generator.betavariate(alpha, beta)
        successes.append(sum(1 for _ in range(count) if generator.random() < probability))
    successes.sort()

    def lower_quantile(confidence: float) -> int:
        index = max(0, min(sample_count - 1, int(math.floor((1.0 - confidence) * (sample_count - 1)))))
        return successes[index]

    return {
        "expected_successes": count * beta_mean(alpha, beta),
        "k80": lower_quantile(0.80),
        "k90": lower_quantile(0.90),
        "k95": lower_quantile(0.95),
        "draws": sample_count,
        "seed": int(seed),
    }


def capacity_forecast_groups(
    groups: Iterable[tuple[float, float, int]], *, draws: int = 20_000, seed: int = 0
) -> dict[str, Any]:
    normalized = [(float(a), float(b), max(0, int(n))) for a, b, n in groups if int(n) > 0]
    sample_count = max(1, int(draws))
    generator = random.Random(int(seed))
    totals: list[int] = []
    for _ in range(sample_count):
        total = 0
        for alpha, beta, count in normalized:
            probability = generator.betavariate(alpha, beta)
            total += sum(1 for _ in range(count) if generator.random() < probability)
        totals.append(total)
    totals.sort()

    def commitment(confidence: float) -> int:
        index = max(0, min(sample_count - 1, int(math.floor((1.0 - confidence) * (sample_count - 1)))))
        return totals[index]

    return {
        "expected_successes": sum(count * beta_mean(alpha, beta) for alpha, beta, count in normalized),
        "k80": commitment(0.80),
        "k90": commitment(0.90),
        "k95": commitment(0.95),
        "draws": sample_count,
        "seed": int(seed),
    }


def monte_carlo_product(
    access: tuple[float, float],
    rating: tuple[float, float],
    *,
    draws: int = 20_000,
    seed: int = 0,
) -> dict[str, Any]:
    generator = random.Random(int(seed))
    values = sorted(
        generator.betavariate(*access) * generator.betavariate(*rating)
        for _ in range(max(1, int(draws)))
    )
    size = len(values)

    def quantile(probability: float) -> float:
        return values[max(0, min(size - 1, int(round(probability * (size - 1)))))]

    return {
        "mean": sum(values) / size,
        "median": quantile(0.5),
        "intervals": {
            str(int(level * 100)): [quantile((1 - level) / 2), quantile(1 - (1 - level) / 2)]
            for level in CREDIBLE_LEVELS
        },
        "draws": size,
        "seed": int(seed),
    }


def json_compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)
