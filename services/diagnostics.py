from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import discord


@dataclass(frozen=True)
class PermissionDrift:
    resource_type: str
    resource_id: int
    resource_name: str
    missing: tuple[str, ...]


DEFAULT_CHANNEL_PERMISSIONS = ("view_channel", "send_messages", "embed_links")
CHANNEL_CONFIG_SPECS = (
    (("channels", "general_logging_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "global_error_log_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "dm_fail_log_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "appeals_log_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "reports_log_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "bot_issues_log_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "transcript_requests_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("channels", "weekly_request_channel_ID"), DEFAULT_CHANNEL_PERMISSIONS),
    (
        ("channels", "autodelete_channel_id"),
        ("view_channel", "read_message_history", "manage_messages"),
    ),
    (
        ("channels", "review_access_channel_id"),
        ("view_channel", "read_message_history", "manage_messages"),
    ),
    (("level_requests", "request_channel"), DEFAULT_CHANNEL_PERMISSIONS),
    (
        ("level_requests", "level_requested"),
        (*DEFAULT_CHANNEL_PERMISSIONS, "read_message_history"),
    ),
    (("level_requests", "sent_channel"), DEFAULT_CHANNEL_PERMISSIONS),
    (("level_requests", "rejected_channel"), DEFAULT_CHANNEL_PERMISSIONS),
    (("impact", "report_channel_id"), (*DEFAULT_CHANNEL_PERMISSIONS, "attach_files")),
    (("operations", "monthly_report_channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (
        ("database", "backups", "channel_id"),
        (*DEFAULT_CHANNEL_PERMISSIONS, "attach_files"),
    ),
    (("background", "daily_summary", "channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("background", "weekly_recap", "channel_id"), DEFAULT_CHANNEL_PERMISSIONS),
    (("tickets", "ticket_category_id"), ("view_channel", "manage_channels")),
)


def scan_permission_drift(bot, guild: discord.Guild) -> list[PermissionDrift]:
    member = guild.me
    if member is None:
        return [PermissionDrift("guild", 0, "Bot member", ("member unavailable",))]
    channel_specs: dict[int, set[str]] = {}
    for path, required_permissions in CHANNEL_CONFIG_SPECS:
        try:
            channel_id = int(bot.config.get(*path, default=0) or 0)
        except (TypeError, ValueError):
            channel_id = 0
        if channel_id:
            channel_specs.setdefault(channel_id, set()).update(required_permissions)
    findings: list[PermissionDrift] = []
    for channel_id, required_permissions in sorted(channel_specs.items()):
        channel = guild.get_channel(channel_id) or guild.get_thread(channel_id)
        if channel is None:
            findings.append(
                PermissionDrift(
                    "channel", channel_id, "Missing channel", ("channel unavailable",)
                )
            )
            continue
        permissions = channel.permissions_for(member)
        missing = tuple(
            name
            for name in sorted(required_permissions)
            if not bool(getattr(permissions, name, False))
        )
        if missing:
            findings.append(
                PermissionDrift(
                    "channel",
                    channel_id,
                    str(getattr(channel, "name", channel_id)),
                    missing,
                )
            )

    role_ids: set[int] = set(bot.config.get_int_list("roles", "admin_owner_role_ids"))
    mod_role_id = bot.config.get_int("roles", "MOD_ROLE_ID", default=0)
    if mod_role_id:
        role_ids.add(mod_role_id)
    role_ids.update(bot.config.get_int_list("level_requests", "required_role_ids"))
    role_ids.update(bot.config.get_int_list("level_requests", "reviewer_role_ids"))
    assignable_ids = {
        int(value)
        for value in (
            bot.config.get_int("level_requests", "has_requested_role_id", default=0),
            bot.config.get_int("level_requests", "request_banned_role_id", default=0),
            bot.config.get_int("roles", "review_access_role_id", default=0),
            bot.config.get_int("roles", "restriction_role_ID", default=0),
            bot.config.get_int("roles", "gambling_reward_role_id", default=0),
            bot.config.get_int("roles", "rps_streak_role_id", default=0),
        )
        if int(value or 0) > 0
    }
    role_ids.update(assignable_ids)
    for raw in bot.config.get("autoDM", "entries", default=[]) or []:
        if isinstance(raw, dict):
            try:
                role_ids.add(int(raw.get("role_id") or 0))
            except (TypeError, ValueError):
                pass
    role_ids.discard(0)
    for role_id in sorted(role_ids):
        role = guild.get_role(role_id)
        if role is None:
            findings.append(
                PermissionDrift("role", role_id, "Missing role", ("role unavailable",))
            )
        elif role_id in assignable_ids and member.top_role <= role:
            findings.append(
                PermissionDrift(
                    "role", role_id, role.name, ("role hierarchy blocks assignment",)
                )
            )
    return findings


async def persist_permission_drift(
    db, guild_id: int, findings: list[PermissionDrift]
) -> None:
    now = int(time.time())
    active = {(item.resource_type, str(item.resource_id)): item for item in findings}
    rows = await db.fetchall(
        "SELECT resource_type,resource_id,status FROM permission_drift_events WHERE guild_id=?",
        (guild_id,),
    )
    previous = {
        (str(row["resource_type"]), str(row["resource_id"])): str(row["status"])
        for row in rows
    }
    statements: list[tuple[str, tuple[Any, ...]]] = []
    for (resource_type, resource_id), item in active.items():
        detail = json.dumps({"name": item.resource_name, "missing": item.missing})
        statements.append(
            (
                "INSERT INTO permission_drift_events(guild_id,resource_type,resource_id,status,detail_json,first_seen_ts,last_seen_ts) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild_id,resource_type,resource_id) DO UPDATE SET "
                "status='open',detail_json=excluded.detail_json,last_seen_ts=excluded.last_seen_ts",
                (guild_id, resource_type, resource_id, "open", detail, now, now),
            )
        )
    for (resource_type, resource_id), status in previous.items():
        if status == "open" and (resource_type, resource_id) not in active:
            statements.append(
                (
                    "UPDATE permission_drift_events SET status='resolved',last_seen_ts=? "
                    "WHERE guild_id=? AND resource_type=? AND resource_id=?",
                    (now, guild_id, resource_type, resource_id),
                )
            )
    if statements:
        await db.execute_transaction(statements, retry_safe=True)
