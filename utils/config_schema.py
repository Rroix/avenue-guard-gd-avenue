from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Any

CONFIG_SCHEMA_VERSION = 2
RUNTIME_SCHEMA_VERSION = 2
EMBED_SCHEMA_VERSION = 2
DATABASE_SCHEMA_VERSION = 10


@dataclass(frozen=True)
class ConfigIssue:
    path: str
    message: str
    severity: str = "error"

    def render(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass(frozen=True)
class OperationsSettings:
    supervisor_interval_seconds: int = 60
    health_sample_interval_seconds: int = 300
    outbox_poll_seconds: int = 5
    permission_scan_interval_seconds: int = 1800
    smoke_test_delay_seconds: int = 30
    monthly_report_channel_id: int = 0
    monthly_report_day: int = 1
    monthly_report_hour: int = 9
    retention_days: dict[str, int] = field(default_factory=dict)


def _integer(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def operations_settings(data: dict[str, Any]) -> OperationsSettings:
    raw = data.get("operations") if isinstance(data.get("operations"), dict) else {}
    retention = (
        raw.get("retention_days") if isinstance(raw.get("retention_days"), dict) else {}
    )
    normalized_retention = {
        str(table): _integer(days, 365, 1, 3650)
        for table, days in retention.items()
        if str(table)
    }
    return OperationsSettings(
        supervisor_interval_seconds=_integer(
            raw.get("supervisor_interval_seconds"), 60, 15, 3600
        ),
        health_sample_interval_seconds=_integer(
            raw.get("health_sample_interval_seconds"), 300, 30, 86400
        ),
        outbox_poll_seconds=_integer(raw.get("outbox_poll_seconds"), 5, 1, 300),
        permission_scan_interval_seconds=_integer(
            raw.get("permission_scan_interval_seconds"), 1800, 60, 86400
        ),
        smoke_test_delay_seconds=_integer(
            raw.get("smoke_test_delay_seconds"), 30, 0, 600
        ),
        monthly_report_channel_id=_integer(
            raw.get("monthly_report_channel_id"), 0, 0, 2**63 - 1
        ),
        monthly_report_day=_integer(raw.get("monthly_report_day"), 1, 1, 28),
        monthly_report_hour=_integer(raw.get("monthly_report_hour"), 9, 0, 23),
        retention_days=normalized_retention,
    )


def _template_variables(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        try:
            for _, field_name, _, _ in Formatter().parse(value):
                if field_name:
                    found.add(field_name.split(".", 1)[0].split("[", 1)[0])
        except ValueError:
            found.add("<invalid-format>")
    elif isinstance(value, list):
        for item in value:
            found.update(_template_variables(item))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_template_variables(item))
    return found


REQUEST_TEMPLATE_VARIABLES = {
    "creators",
    "duplicate_history_warning",
    "edit_count",
    "gd_info",
    "level_id",
    "level_name",
    "level_showcase",
    "level_validation_warning",
    "notes",
    "pending_color",
    "request_type",
    "request_type_label",
    "requester_id",
    "requester_mention",
    "result",
    "result_color",
    "review",
    "reviewer_mention",
    "submitted_ago",
    "wave_id",
    "sla_indicator",
    "sla_status",
    "sla_hours",
    "correlation_id",
    "showcase",
    "submitted_ts",
    "edit_deadline",
    "edit_deadline_ts",
    "level_validation_sources",
    "level_validation_checked",
    "level_validation_refresh",
    "level_exists",
    "level_rated",
    "level_requires_showcase",
    "gd_level_name",
    "gd_creator",
    "gd_difficulty",
    "gd_length",
    "gd_stars",
    "gd_rated",
    "gd_platformer",
    "reviewer_id",
    "send_type",
    "send_type_label",
    "queue_status",
    "review_system_version",
}

REQUEST_BUTTON_VARIABLES = {
    "state",
    "wave_id",
    "submitted_count",
    "request_limit",
    "close_ts",
    "request_type",
    "request_type_label",
    "request_type_line",
}

WAVE_SUMMARY_VARIABLES = {
    "wave_id",
    "request_type",
    "request_type_label",
    "total_requests",
    "reviewed_count",
    "sent_count",
    "not_sent_count",
    "rejected_count",
    "other_count",
    "level_doesnt_exist_count",
    "stolen_level_count",
    "already_rated_count",
    "pending_count",
    "left_to_review",
    "reviewed_percent",
    "pending_percent",
    "sent_percent",
    "not_sent_percent",
    "sent_percent_reviewed",
    "not_sent_percent_reviewed",
    "reviewer_stats",
    "summary_color",
    "wave_comparison",
    "request_delta",
    "sent_rate_delta",
    "review_system_version",
    "rate_count",
    "feature_count",
    "epic_count",
    "legendary_count",
    "mythic_count",
    "recommendation_breakdown",
}

WEEKLY_SUBMITTED_VARIABLES = REQUEST_TEMPLATE_VARIABLES | {
    "user_id",
    "user_mention",
    "rank",
    "weekly_rank",
    "week_start",
    "request_content",
    "created_ts",
    "review_kind",
}

TEMPLATE_VARIABLES = {
    "request_button_embed": REQUEST_BUTTON_VARIABLES,
    "wave_summary_embed": WAVE_SUMMARY_VARIABLES,
    "weekly_request_dm_embed": {
        "request_text",
        "timeout_hours",
        "deadline",
        "expires_ts",
    },
    "weekly_request_reminder_embed": {"reminder_text", "deadline", "expires_ts"},
    "weekly_request_submitted_embed": WEEKLY_SUBMITTED_VARIABLES,
}


def _is_discord_id(value: Any, *, optional: bool = False) -> bool:
    if optional and (value is None or value == "" or value == 0 or value == "0"):
        return True
    if isinstance(value, bool):
        return False
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _validate_id_list(issues: list[ConfigIssue], path: str, value: Any) -> None:
    if not isinstance(value, list):
        issues.append(ConfigIssue(path, "must be a list of Discord IDs"))
        return
    if any(not _is_discord_id(item) for item in value):
        issues.append(ConfigIssue(path, "contains an invalid Discord ID"))


def validate_config(data: Any) -> list[ConfigIssue]:
    if not isinstance(data, dict):
        return [ConfigIssue("config", "root must be a JSON object")]

    issues: list[ConfigIssue] = []
    version = data.get("schema_version", 1)
    if not isinstance(version, int):
        issues.append(ConfigIssue("schema_version", "must be an integer"))
    elif version > CONFIG_SCHEMA_VERSION:
        issues.append(
            ConfigIssue(
                "schema_version",
                f"{version} is newer than supported version {CONFIG_SCHEMA_VERSION}",
            )
        )
    elif version < CONFIG_SCHEMA_VERSION:
        issues.append(
            ConfigIssue(
                "schema_version",
                f"upgrade recommended to version {CONFIG_SCHEMA_VERSION}",
                "warning",
            )
        )

    embed_version = data.get("embed_schema_version", 1)
    if not isinstance(embed_version, int):
        issues.append(ConfigIssue("embed_schema_version", "must be an integer"))
    elif embed_version != EMBED_SCHEMA_VERSION:
        severity = "error" if embed_version > EMBED_SCHEMA_VERSION else "warning"
        issues.append(
            ConfigIssue(
                "embed_schema_version",
                f"expected version {EMBED_SCHEMA_VERSION}, found {embed_version}",
                severity,
            )
        )

    for section in ("guild", "roles", "channels", "level_requests", "database"):
        if not isinstance(data.get(section), dict):
            issues.append(ConfigIssue(section, "must be a JSON object"))

    request_config = (
        data.get("level_requests")
        if isinstance(data.get("level_requests"), dict)
        else {}
    )
    for key in (
        "request_channel",
        "level_requested",
        "sent_channel",
        "rejected_channel",
    ):
        value = request_config.get(key)
        try:
            valid = int(value or 0) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            issues.append(
                ConfigIssue(f"level_requests.{key}", "must be a Discord channel ID")
            )

    guild = data.get("guild") if isinstance(data.get("guild"), dict) else {}
    if not _is_discord_id(guild.get("allowed_guild_id")):
        issues.append(
            ConfigIssue("guild.allowed_guild_id", "must be a Discord server ID")
        )

    for key in ("required_role_ids", "reviewer_role_ids"):
        _validate_id_list(issues, f"level_requests.{key}", request_config.get(key))
    for key in ("has_requested_role_id", "request_banned_role_id"):
        if not _is_discord_id(request_config.get(key)):
            issues.append(
                ConfigIssue(f"level_requests.{key}", "must be a Discord role ID")
            )

    default_notification = str(
        request_config.get("default_result_notification") or ""
    ).casefold()
    if default_notification not in {"channel", "dm", "both", "none"}:
        issues.append(
            ConfigIssue(
                "level_requests.default_result_notification",
                "must be channel, dm, both, or none",
            )
        )

    thresholds = request_config.get("aging_threshold_hours")
    if (
        not isinstance(thresholds, list)
        or len(thresholds) != 3
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            for value in thresholds
        )
        or list(thresholds) != sorted(thresholds)
    ):
        issues.append(
            ConfigIssue(
                "level_requests.aging_threshold_hours",
                "must be three ascending positive numbers",
            )
        )

    template_keys = (
        "level_requested_embed",
        "level_reviewed_embed",
        "sent_result_embed",
        "rejected_result_embed",
        "other_result_embed",
        "wave_summary_embed",
        "request_button_embed",
        "weekly_request_dm_embed",
        "weekly_request_reminder_embed",
        "weekly_request_submitted_embed",
    )
    for key in template_keys:
        template = request_config.get(key)
        if not isinstance(template, dict):
            issues.append(
                ConfigIssue(f"level_requests.{key}", "must be an embed template object")
            )
            continue
        variables = _template_variables(template)
        if "<invalid-format>" in variables:
            issues.append(
                ConfigIssue(
                    f"level_requests.{key}", "contains an invalid format string"
                )
            )
        allowed_variables = TEMPLATE_VARIABLES.get(key, REQUEST_TEMPLATE_VARIABLES)
        unknown = sorted(variables - allowed_variables)
        if unknown:
            issues.append(
                ConfigIssue(
                    f"level_requests.{key}", f"unknown variables: {', '.join(unknown)}"
                )
            )
        fields = template.get("fields", [])
        if not isinstance(fields, list) or len(fields) > 25:
            issues.append(
                ConfigIssue(
                    f"level_requests.{key}.fields",
                    "must be a list with at most 25 fields",
                )
            )

    operations = data.get("operations", {})
    if operations and not isinstance(operations, dict):
        issues.append(ConfigIssue("operations", "must be a JSON object"))
    elif isinstance(operations, dict):
        retention = operations.get("retention_days", {})
        if retention and not isinstance(retention, dict):
            issues.append(
                ConfigIssue(
                    "operations.retention_days", "must be a table-to-days object"
                )
            )
        elif isinstance(retention, dict):
            for table, days in retention.items():
                if (
                    isinstance(days, bool)
                    or not isinstance(days, int)
                    or not 1 <= days <= 3650
                ):
                    issues.append(
                        ConfigIssue(
                            f"operations.retention_days.{table}",
                            "must be an integer from 1 to 3650",
                        )
                    )
    staff_portal = data.get("staff_portal", {})
    if staff_portal and not isinstance(staff_portal, dict):
        issues.append(ConfigIssue("staff_portal", "must be a JSON object"))
    elif isinstance(staff_portal, dict):
        for key in (
            "judge_role_ids",
            "head_judge_role_ids",
            "admin_role_ids",
            "owner_role_ids",
            "owner_user_ids",
            "dev_user_ids",
        ):
            value = staff_portal.get(key, [])
            if not isinstance(value, list) or any(
                isinstance(item, bool) or not str(item).isascii() or not str(item).isdecimal()
                for item in value
            ):
                issues.append(ConfigIssue(f"staff_portal.{key}", "must be a list of Discord IDs"))
        session_hours = staff_portal.get("session_ttl_hours", 8)
        if isinstance(session_hours, bool) or not isinstance(session_hours, int) or not 1 <= session_hours <= 168:
            issues.append(ConfigIssue("staff_portal.session_ttl_hours", "must be an integer from 1 to 168"))
        stale_hours = staff_portal.get("claim_stale_hours", 48)
        if isinstance(stale_hours, bool) or not isinstance(stale_hours, int) or not 1 <= stale_hours <= 720:
            issues.append(ConfigIssue("staff_portal.claim_stale_hours", "must be an integer from 1 to 720"))
        identity_ttl = staff_portal.get("identity_cache_ttl_seconds", 300)
        if (
            isinstance(identity_ttl, bool)
            or not isinstance(identity_ttl, int)
            or not 30 <= identity_ttl <= 3600
        ):
            issues.append(
                ConfigIssue(
                    "staff_portal.identity_cache_ttl_seconds",
                    "must be an integer from 30 to 3600",
                )
            )
        origins = staff_portal.get("allowed_origins", [])
        if not isinstance(origins, list) or any(
            not str(origin).startswith("https://") for origin in origins
        ):
            issues.append(ConfigIssue("staff_portal.allowed_origins", "must contain HTTPS origins"))
    from utils.historical_audit import audit_settings
    from utils.priority_system import priority_settings
    try:
        audit_settings(data)
    except ValueError as exc:
        issues.append(ConfigIssue("historical_audit", str(exc)))
    try:
        priority_settings(data)
    except ValueError as exc:
        issues.append(ConfigIssue("priority_system", str(exc)))
    return issues
