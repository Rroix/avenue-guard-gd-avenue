from __future__ import annotations

from copy import deepcopy
from typing import Any


SERVER_ICON_SETTING = "background.server_icon_rotation"
FORUM_RULES_SETTING = "forum.required_rules"


def _forum_entries(config) -> list[dict[str, Any]]:
    root = config.data.get("forum_first_message", {})
    if not isinstance(root, dict):
        return []
    entries = root.get("entries")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    if root.get("forum_channel_id"):
        return [root]
    return []


def collect_forum_required_rules(config) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    keys = (
        "required_word",
        "required_word_match_mode",
        "missing_required_word_dm",
        "required_word_dm_message",
        "required_word_delete_delay_seconds",
    )
    for entry in _forum_entries(config):
        try:
            forum_id = str(int(entry.get("forum_channel_id")))
        except (TypeError, ValueError):
            continue
        result[forum_id] = {key: deepcopy(entry[key]) for key in keys if key in entry}
    return result


def apply_forum_required_rules(config, stored: Any) -> None:
    if not isinstance(stored, dict):
        return
    for entry in _forum_entries(config):
        try:
            forum_id = str(int(entry.get("forum_channel_id")))
        except (TypeError, ValueError):
            continue
        values = stored.get(forum_id)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if key in {
                "required_word",
                "required_word_match_mode",
                "missing_required_word_dm",
                "required_word_dm_message",
                "required_word_delete_delay_seconds",
            }:
                entry[key] = deepcopy(value)


async def load_runtime_config_overrides(bot) -> None:
    icon_config = await bot.db.get_runtime_setting(SERVER_ICON_SETTING)
    if isinstance(icon_config, dict):
        background = bot.config.data.setdefault("background", {})
        if isinstance(background, dict):
            background["server_icon_rotation"] = icon_config

    forum_rules = await bot.db.get_runtime_setting(FORUM_RULES_SETTING)
    apply_forum_required_rules(bot.config, forum_rules)


async def persist_server_icon_config(bot, value: dict[str, Any]) -> None:
    await bot.db.set_runtime_setting(SERVER_ICON_SETTING, deepcopy(value))


async def persist_forum_required_rules(bot) -> None:
    await bot.db.set_runtime_setting(FORUM_RULES_SETTING, collect_forum_required_rules(bot.config))
