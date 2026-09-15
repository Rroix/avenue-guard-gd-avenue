"""Pure, analysis-only historical diagnostics and private export rendering."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

PRESTIGE = ("rate", "feature", "epic", "legendary", "mythic")
MODEL_VERSION = "historical-observed-wave-proxy-v1"
MAX_CSV_BYTES = 2_000_000
MAX_CSV_ROWS = 10_000


def normalize_level_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9]+\.0+", text):
        text = text.split(".", 1)[0]
    if not re.fullmatch(r"[0-9]{1,10}", text) or not 0 < int(text) <= 2_147_483_647:
        return None
    return str(int(text))


def normalize_prestige(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    text = {"featured": "feature", "rated": "rate"}.get(text, text)
    return text if text in PRESTIGE else None


def exact_review_prestige(text: Any) -> str | None:
    # A deliberately narrow whole-text grammar rejects negation, multiple labels,
    # speculation and arbitrary prose. It does not classify generic review outcomes.
    match = re.fullmatch(
        r"\s*(?:(?:sent|recommended|requested|prestige|category)\s*(?:for|as|:|-)?\s*)?"
        r"\b(rate|feature|epic|legendary|mythic)\b[.!]?\s*", str(text or ""), re.IGNORECASE,
    )
    return match.group(1).casefold() if match else None


def audit_settings(data: dict) -> dict:
    raw = data.get("historical_audit", {})
    if not isinstance(raw, dict):
        raise ValueError("historical_audit must be an object")
    result = {"enabled": raw.get("enabled", False), "success_cache_seconds": raw.get("success_cache_seconds", 86400),
              "failure_cache_seconds": raw.get("failure_cache_seconds", 300),
              "lookup_interval_seconds": raw.get("lookup_interval_seconds", 2),
              "allow_csv_hosts": raw.get("allow_csv_hosts", ["docs.google.com", "docs.googleusercontent.com"]),
              "parse_review_text": raw.get("parse_review_text", True),
              "prestige_x": raw.get("prestige_x", {"rate": 0, "feature": None, "epic": None, "legendary": None, "mythic": 5}),
              "cp_formula": raw.get("cp_formula", {"name": "disabled", "points": {}}),
              "candidate_model_version": raw.get("candidate_model_version", MODEL_VERSION)}
    if not isinstance(result["enabled"], bool) or not isinstance(result["parse_review_text"], bool):
        raise ValueError("Audit enabled and parse_review_text must be booleans")
    for key, minimum, maximum in (("success_cache_seconds", 60, 604800), ("failure_cache_seconds", 30, 3600),
                                  ("lookup_interval_seconds", 1, 60)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"historical_audit.{key} must be between {minimum} and {maximum}")
    if not isinstance(result["allow_csv_hosts"], list) or any(
        not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", host)
        for host in result["allow_csv_hosts"]
    ):
        raise ValueError("allow_csv_hosts must contain explicit lowercase DNS names, not wildcards")
    exponents = result["prestige_x"]
    if not isinstance(exponents, dict) or set(exponents) != set(PRESTIGE):
        raise ValueError("prestige_x must specify all five categories, using null for undefined values")
    for value in exponents.values():
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 5):
            raise ValueError("Prestige exponents must be null or finite numbers from 0 to 5")
    if exponents["rate"] != 0 or exponents["mythic"] != 5:
        raise ValueError("Analysis anchors must remain Rate=0 and Mythic=5")
    formula = result["cp_formula"]
    if not isinstance(formula, dict) or not isinstance(formula.get("name"), str) or not formula["name"].strip():
        raise ValueError("cp_formula needs a nonempty candidate name or disabled")
    points = formula.get("points", {})
    if not isinstance(points, dict) or set(points) - {"0", "1", "2", "3", "4+"}:
        raise ValueError("CP candidate points must be a table keyed by 0, 1, 2, 3, 4+")
    for value in points.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1_000_000:
            raise ValueError("CP candidate values must be finite nonnegative numbers")
    version = result["candidate_model_version"]
    if not isinstance(version, str) or not 1 <= len(version) <= 100:
        raise ValueError("candidate_model_version must be a short nonempty name")
    return result


def parse_sheet_csv(payload: bytes, source_name: str = "supplied_csv") -> tuple[list[dict], list[dict]]:
    if len(payload) > MAX_CSV_BYTES:
        raise ValueError("CSV exceeds the 2 MB audit import limit")
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = {re.sub(r"[\s-]+", "_", str(key).strip().casefold()): key for key in (reader.fieldnames or [])}
    level_column = next((headers[key] for key in ("level_id", "id", "gd_id") if key in headers), None)
    label_column = next((headers[key] for key in ("prestige", "category", "send_category", "requested_prestige") if key in headers), None)
    if not level_column or not label_column:
        raise ValueError("CSV needs level_id and prestige columns (or documented aliases)")
    labels, errors = [], []
    digest = hashlib.sha256(payload).hexdigest()
    for index, row in enumerate(reader, 2):
        if index > MAX_CSV_ROWS + 1:
            raise ValueError("CSV exceeds the 10,000 row audit import limit")
        if not any(str(value or "").strip() for value in row.values()):
            continue
        level = normalize_level_id(row.get(level_column))
        prestige = normalize_prestige(row.get(label_column))
        if not level or not prestige:
            errors.append({"row": index, "error": "invalid level ID or missing/invalid prestige"})
            continue
        wave = row.get(headers.get("wave_id", headers.get("wave", "")))
        date = str(row.get(headers.get("request_date", headers.get("date", ""))) or "").strip()
        if wave and not re.fullmatch(r"[0-9]+", str(wave).strip()):
            errors.append({"row": index, "error": "invalid wave qualifier"})
            continue
        if date:
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                errors.append({"row": index, "error": "request_date must be YYYY-MM-DD (UTC)"})
                continue
        labels.append({"evidence_id": f"{digest}:{index}", "level_id": level,
                       "wave_id": int(wave) if wave else None, "request_date": date or None,
                       "prestige": prestige, "source": "google_sheet",
                       "source_detail": json.dumps({"file": source_name[:120], "sha256": digest, "row": index})})
    return labels, errors


def cp_bin(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return str(value) if value < 4 else "4+"


def result_group(result: Any) -> str:
    text = str(result or "").strip().casefold()
    if text in {"sent", "recommended"}:
        return "sent"
    if text == "rejected":
        return "rejected"
    return "other" if text else "pending"


def coverage(count: int, total: int) -> dict:
    return {"count": count, "total": total, "percentage": round(100 * count / total, 2) if total else None}


def build_dataset(requests: list[dict], levels: dict[str, dict], creators: dict[str, dict],
                  labels: list[dict], settings: dict, as_of_ts: int) -> tuple[list[dict], list[dict]]:
    ordered = sorted(requests, key=lambda row: (int(row["guild_id"]), int(row["wave_id"]), int(row["created_ts"]), str(row["requester_id"])))
    candidates = defaultdict(list)
    waves = defaultdict(dict)
    for row in ordered:
        level = normalize_level_id(row["level_id"])
        candidates[level].append(row)
        guild, wave = int(row["guild_id"]), int(row["wave_id"])
        waves[guild][wave] = min(waves[guild].get(wave, int(row["created_ts"])), int(row["created_ts"]))
    evidence = defaultdict(list)
    enrichment = []
    for label in labels:
        matches = [row for row in candidates.get(label["level_id"], [])
                   if (label.get("wave_id") is None or int(row["wave_id"]) == int(label["wave_id"]))
                   and (not label.get("request_date") or datetime.fromtimestamp(int(row["created_ts"]), timezone.utc).strftime("%Y-%m-%d") == label["request_date"])]
        detail = {**label, "match_count": len(matches), "match_confidence": "exact_unique" if len(matches) == 1 else "ambiguous" if matches else "unmatched"}
        enrichment.append(detail)
        if len(matches) == 1:
            row = matches[0]
            evidence[(row["guild_id"], row["wave_id"], str(row["requester_id"]))].append(detail)
    history = defaultdict(list)
    dataset = []
    for original in ordered:
        row = dict(original)
        row["request_row_id"] = f'{row["guild_id"]}:{row["wave_id"]}:{row["requester_id"]}'
        level_id = normalize_level_id(row["level_id"])
        row["level_id_raw"] = row["level_id"]
        row["level_id"] = level_id
        snapshot = levels.get(level_id, {})
        account = snapshot.get("current_uploader_account_id")
        profile = creators.get(str(account), {}) if account else {}
        row.update(snapshot)
        row.update({"gd_lookup_status": snapshot.get("gd_lookup_status", "invalid_id" if not level_id else "not_requested"),
                    "gd_lookup_error": snapshot.get("gd_lookup_error"), "gd_checked_ts": snapshot.get("gd_checked_ts"),
                    "current_exists": snapshot.get("current_exists"), "current_rated": snapshot.get("current_rated")})
        for key in ("current_level_name", "current_uploader_name", "current_uploader_user_id", "current_uploader_account_id",
                    "current_stars", "current_featured", "current_epic", "current_epic_tier_raw", "current_legendary", "current_mythic"):
            row.setdefault(key, None)
        points = profile.get("current_creator_points")
        cp_status = profile.get("cp_lookup_status", "unresolved_account" if not account else "not_requested")
        cp_error = profile.get("cp_lookup_error")
        uploader_user = snapshot.get("current_uploader_user_id")
        if profile.get("user_id") and uploader_user and str(profile["user_id"]) != str(uploader_user):
            points, cp_status, cp_error = None, "identity_conflict", "Profile user ID differs from level uploader user ID"
        row.update({"cp_checked_ts": profile.get("cp_checked_ts"), "current_creator_points": points,
                    "cp_lookup_status": cp_status, "cp_lookup_error": cp_error, "cp_bin": cp_bin(points),
                    "gd_snapshot_cache_hit": bool(snapshot.get("audit_cache_hit")),
                    "gd_snapshot_stale": bool(snapshot.get("audit_snapshot_stale")),
                    "cp_snapshot_cache_hit": bool(profile.get("audit_cache_hit")),
                    "cp_snapshot_stale": bool(profile.get("audit_snapshot_stale"))})
        prior = history[(row["guild_id"], level_id if level_id else row["level_id_raw"])]
        row["request_occurrence_number_for_level"] = len(prior) + 1
        row["previous_request_count_for_level"] = len(prior)
        # Review decisions made after this submission cannot inform its historical past.
        prior_known = [entry for entry in prior if entry.get("reviewed_ts") and int(entry["reviewed_ts"]) <= int(row["created_ts"])]
        row["previous_sent_count"] = sum(result_group(entry.get("historical_result")) == "sent" for entry in prior_known)
        row["previous_rejected_count"] = sum(result_group(entry.get("historical_result")) == "rejected" for entry in prior_known)
        wave_order = sorted(waves[int(row["guild_id"])])
        row["observed_waves_between_requests"] = max(0, wave_order.index(int(row["wave_id"])) - wave_order.index(int(prior[-1]["wave_id"])) - 1) if prior else None
        row["previous_historical_result"] = prior[-1].get("historical_result") if prior else None
        reviewed_ts = row.get("reviewed_ts")
        row["review_hours"] = (int(reviewed_ts) - int(row["created_ts"])) / 3600 if reviewed_ts and int(reviewed_ts) >= int(row["created_ts"]) else None
        source_evidence = list(evidence[(row["guild_id"], row["wave_id"], str(row["requester_id"]))])
        exact = exact_review_prestige(row.get("review_text")) if settings["parse_review_text"] else None
        if exact:
            detail = {"level_id": level_id, "wave_id": row["wave_id"], "prestige": exact, "source": "review_text_exact",
                      "match_confidence": "exact_request", "source_detail": row["request_row_id"]}
            source_evidence.append(detail)
            enrichment.append(detail)
        distinct = {entry["prestige"] for entry in source_evidence}
        conflict = len(distinct) > 1
        for detail in source_evidence:
            detail["request_row_id"] = row["request_row_id"]
            detail["prestige_conflict"] = conflict
        row.update({"prestige": next(iter(distinct)) if len(distinct) == 1 else None,
                    "prestige_source": "google_sheet" if len(distinct) == 1 and any(entry["source"] == "google_sheet" for entry in source_evidence) else "review_text_exact" if len(distinct) == 1 else None,
                    "prestige_conflict": conflict, "prestige_source_detail": json.dumps(source_evidence, separators=(",", ":")) if source_evidence else None})
        row["historically_recommended_candidate"] = result_group(row.get("historical_result")) == "sent"
        approved_age = sum(ts >= int(reviewed_ts) and wave > int(row["wave_id"]) and ts <= as_of_ts for wave, ts in waves[int(row["guild_id"])].items()) if row["historically_recommended_candidate"] and reviewed_ts and int(row["created_ts"]) <= int(reviewed_ts) <= as_of_ts else None
        row["approved_age_observed_waves_proxy"] = approved_age
        row["waiting_basis"] = "approved_age_in_observed_subsequent_waves_not_actual_queue_wait" if approved_age is not None else None
        row["waiting_component_h"] = 1.5 * min(approved_age, 4) ** 1.5 if approved_age is not None else None
        exponent = settings["prestige_x"].get(row["prestige"])
        row["prestige_component_f"] = 1.8 ** exponent - 1 if exponent is not None else None
        formula = settings["cp_formula"]
        row["cp_component_g"] = formula.get("points", {}).get(row["cp_bin"]) if formula["name"] != "disabled" and row["cp_bin"] is not None else None
        row["cp_candidate_formula"] = formula["name"]
        components = [row[key] for key in ("prestige_component_f", "cp_component_g", "waiting_component_h")]
        row["candidate_priority_total"] = sum(components) if all(value is not None for value in components) else None
        gh = [row["cp_component_g"], row["waiting_component_h"]]
        row["known_components_only_g_plus_h_not_full_priority"] = sum(gh) if all(value is not None for value in gh) else None
        row["candidate_model_version"] = settings["candidate_model_version"]
        row["candidate_score_basis"] = "current_CP_and_approved_age_proxy_as_of_audit_not_historical_replay"
        prior.append(original)
        dataset.append(row)
    return dataset, enrichment


def summarize(dataset: list[dict], levels: dict, creators: dict, enrichment: list[dict], run_id: str) -> dict:
    groups = {key: [row for row in dataset if result_group(row.get("historical_result")) == key] for key in ("sent", "rejected", "other", "pending")}
    sent = groups["sent"]
    labelled = [row for row in dataset if row["prestige"] is not None]
    totals = Counter(row["level_id"] for row in dataset if row["level_id"])
    rating = {}
    cp = {}
    for group, rows in groups.items():
        rating[group] = {key: coverage(sum(row.get("current_rated") is value for row in rows), len(rows))
                         for key, value in (("currently_rated", True), ("currently_unrated", False), ("unknown", None))}
        cp[group] = {key: coverage(sum((row["cp_bin"] or "unknown") == key for row in rows), len(rows))
                     for key in ("0", "1", "2", "3", "4+", "unknown")}
    complete = sum(row["candidate_priority_total"] is not None for row in sent)
    return {
        "run_id": run_id, "analysis_only": True,
        "coverage": {"total_historical_requests": len(dataset), "total_distinct_level_ids": len(totals),
                     "total_reviewed": sum(len(groups[key]) for key in ("sent", "rejected", "other")),
                     **{f"historical_{key}": len(rows) for key, rows in groups.items()},
                     "gd_success_distinct_levels": coverage(sum(row.get("gd_lookup_status") == "ok" for row in levels.values()), len(totals)),
                     "gd_success_requests": coverage(sum(row.get("gd_lookup_status") == "ok" for row in dataset), len(dataset)),
                     "known_current_CP_requests": coverage(sum(row["current_creator_points"] is not None for row in dataset), len(dataset)),
                     "known_current_CP_accounts": coverage(sum(row.get("current_creator_points") is not None for row in creators.values()), len(creators)),
                     "prestige_labels": coverage(len(labelled), len(dataset)),
                     "sent_prestige_labels": coverage(sum(row["prestige"] is not None for row in sent), len(sent)),
                     "sent_full_candidate_components": coverage(complete, len(sent)),
                     "sent_missing_full_candidate_components": coverage(len(sent) - complete, len(sent)),
                     "actual_full_historical_replay_supported": False},
        "current_rating_outcomes": rating, "current_CP_distributions": cp,
        "wording": {"sent": "currently rated among historically sent levels",
                    "rejected": "currently rated among historically rejected levels"},
        "prestige_subset": {"counts": {label: sum(row["prestige"] == label for row in labelled) for label in PRESTIGE},
                            "sources": dict(Counter(row["prestige_source"] for row in labelled)),
                            "evidence_sources": dict(Counter(row["source"] for row in enrichment)),
                            "outcomes": {label: dict(Counter("currently_rated" if row.get("current_rated") is True else "currently_unrated" if row.get("current_rated") is False else "unknown" for row in labelled if row["prestige"] == label)) for label in PRESTIGE},
                            "caution": "Incomplete, potentially non-random labelled subset; not representative of all historical sent levels"},
        "repeated_levels": {"distinct_repeated_level_ids": sum(count > 1 for count in totals.values()),
                            "occurrences": [{key: row.get(key) for key in ("request_row_id", "level_id", "wave_id", "observed_waves_between_requests", "historical_result", "previous_historical_result")} for row in dataset if row["previous_request_count_for_level"]]},
        "waiting_diagnostics": {"sent_with_proxy": sum(row["approved_age_observed_waves_proxy"] is not None for row in sent),
                                "observed_wave_age_distribution": dict(Counter(str(row["approved_age_observed_waves_proxy"]) for row in sent)),
                                "caution": "Observed subsequent request waves since approval, measured as of this audit; not actual outreach/queue history. Empty or lost waves are not reconstructable. Repeated requests are not separate confirmed outreach candidates"},
        "data_quality": {"invalid_level_ids": sum(row["level_id"] is None for row in dataset),
                         "prestige_conflicts": sum(row["prestige_conflict"] for row in dataset),
                         "ambiguous_sheet_rows": sum(row["match_confidence"] == "ambiguous" for row in enrichment),
                         "unmatched_sheet_rows": sum(row["match_confidence"] == "unmatched" for row in enrichment),
                         "GD_errors": dict(Counter(row.get("gd_lookup_status", "not_requested") for row in dataset if row.get("gd_lookup_status") != "ok")),
                         "GD_warnings_or_conflicts": sum(bool(row.get("gd_lookup_error")) for row in dataset),
                         "stale_GD_snapshots": sum(bool(row.get("audit_snapshot_stale")) for row in dataset),
                         "stale_CP_snapshots": sum(row["cp_snapshot_stale"] for row in dataset),
                         "reviewed_without_result": sum(row.get("status") == "reviewed" and not row.get("historical_result") for row in dataset),
                         "CP_errors": dict(Counter(row["cp_lookup_status"] for row in dataset if row["current_creator_points"] is None)),
                         "invalid_review_timestamps": sum(bool(row.get("reviewed_ts")) and row["review_hours"] is None for row in dataset),
                         "warning": "Stored Discord IDs are preserved verbatim and may include legacy Turso precision damage; this audit does not repair or expose them publicly"},
        "limitations": ["Current CP and rating are not historical values", "Current rating is observational; no Avenue causation is established",
                        "Missing prestige is never treated as Rate", "No actual outreach ledger is available for a full historical f+g+h replay",
                        "Intermediate prestige exponents and the CP formula are undefined by default", "Historical request edits may have changed the stored level ID before this audit"],
    }


def _csv(rows: list[dict], fallback_columns: tuple[str, ...]) -> bytes:
    columns = list(dict.fromkeys(key for row in rows for key in row)) or list(fallback_columns)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=True)
            # Prevent private user-supplied notes/review text executing as spreadsheet formulas.
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue().encode("utf-8-sig")


def render_exports(dataset: list[dict], levels: dict, creators: dict, enrichment: list[dict], summary: dict) -> dict[str, bytes]:
    count = summary["coverage"]
    lines = ["# Avenue Guard: Private Historical Request Audit", "", "Analysis only. No live request decisions or historical rows were changed.", "",
             "## Coverage", "", f"Historical requests: {count['total_historical_requests']}; distinct valid level IDs: {count['total_distinct_level_ids']}",
             f"Historically sent: {count['historical_sent']}; rejected: {count['historical_rejected']}; other: {count['historical_other']}; pending: {count['historical_pending']}"]
    for key in ("gd_success_requests", "known_current_CP_requests", "prestige_labels", "sent_prestige_labels", "sent_full_candidate_components"):
        entry = count[key]
        percentage = f"{entry['percentage']}%" if entry['percentage'] is not None else "n/a"
        lines.append(f"- {key}: {entry['count']}/{entry['total']} ({percentage})")
    lines += ["", "## Current Rating Outcomes", "", "| Historical result | Currently rated | Currently unrated | Unknown |", "|---|---:|---:|---:|"]
    for group in ("sent", "rejected", "other"):
        table = summary["current_rating_outcomes"][group]
        lines.append(f"| {group.title()} | {table['currently_rated']['count']} | {table['currently_unrated']['count']} | {table['unknown']['count']} |")
    for group in ("sent", "rejected"):
        entry = summary["current_rating_outcomes"][group]["currently_rated"]
        percentage = f"{entry['percentage']}%" if entry['percentage'] is not None else "n/a"
        lines.append(f"\n{summary['wording'][group].capitalize()}: {percentage} of all historical {group} requests (unknowns retained in denominator)")
    lines += ["", "## Current Creator Points", "", "| Historical result | 0 | 1 | 2 | 3 | 4+ | Unknown |", "|---|---:|---:|---:|---:|---:|---:|"]
    for group in ("sent", "rejected"):
        values = summary["current_CP_distributions"][group]
        cells = [f"{values[key]['count']} ({str(values[key]['percentage']) + '%' if values[key]['percentage'] is not None else 'n/a'})" for key in ("0", "1", "2", "3", "4+", "unknown")]
        lines.append("| " + " | ".join([group.title(), *cells]) + " |")
    lines += ["", "## Prestige-Labelled Subset", "", summary["prestige_subset"]["caution"], "",
              "```json", json.dumps(summary["prestige_subset"], indent=2), "```",
              "", "## Waiting and Repeated Levels", "", summary["waiting_diagnostics"]["caution"],
              f"\nDistinct level IDs requested more than once: {summary['repeated_levels']['distinct_repeated_level_ids']}",
              "", "```json", json.dumps(summary["waiting_diagnostics"], indent=2), "```", "", "## Data Quality", "",
              "```json", json.dumps(summary["data_quality"], indent=2), "```", "", "## Interpretation", ""]
    lines += ["- " + item for item in summary["limitations"]]
    lines += ["", "Candidate totals use current CP plus an observed-wave approved-age proxy at audit time. They are not historical allocation rankings.",
              "The actual full historical replay coverage is unmeasurable without outreach events; full candidate component coverage is reported separately.",
              "Requester/reviewer IDs and review text are private. CSV nulls are blank cells; JSON summary unknowns use null."]
    return {"avenue_historical_requests.csv": _csv(dataset, ("request_row_id", "level_id")),
            "avenue_historical_levels_current_snapshot.csv": _csv(list(levels.values()), ("level_id", "current_exists")),
            "avenue_creator_snapshot.csv": _csv(list(creators.values()), ("account_id", "current_creator_points")),
            "avenue_prestige_enrichment.csv": _csv(enrichment, ("level_id", "prestige", "source", "match_confidence")),
            "avenue_historical_audit_summary.json": json.dumps(summary, indent=2, ensure_ascii=True).encode(),
            "avenue_historical_audit.md": ("\n".join(lines) + "\n").encode()}
