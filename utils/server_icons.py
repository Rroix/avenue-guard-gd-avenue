from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

VALID_SERVER_ICON_MODES = {"random", "linear", "disabled"}


def normalize_server_icon_mode(value: Any) -> str:
    mode = str(value or "disabled").strip().casefold()
    return mode if mode in VALID_SERVER_ICON_MODES else "disabled"


def is_valid_icon_url(value: Any) -> bool:
    url = str(value or "").strip()
    if not url or len(url) > 2000:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_icon_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        url = str(item or "").strip()
        if is_valid_icon_url(url) and url not in seen:
            out.append(url)
            seen.add(url)
    return out[:25]


def ensure_server_icon_config(config) -> dict:
    background = config.data.setdefault("background", {})
    rotation = background.setdefault("server_icon_rotation", {})
    rotation.setdefault(
        "_comment",
        "Rotates the server icon from configured image URLs. mode is disabled, linear, or random.",
    )
    rotation["mode"] = normalize_server_icon_mode(rotation.get("mode", "disabled"))
    try:
        rotation["interval_seconds"] = max(600, int(rotation.get("interval_seconds", 86400) or 86400))
    except Exception:
        rotation["interval_seconds"] = 86400
    rotation["urls"] = clean_icon_urls(rotation.get("urls", []))
    try:
        rotation["current_index"] = int(rotation.get("current_index", -1))
    except Exception:
        rotation["current_index"] = -1
    if rotation["current_index"] >= len(rotation["urls"]):
        rotation["current_index"] = -1
    try:
        rotation["last_changed_ts"] = max(0, int(rotation.get("last_changed_ts", 0) or 0))
    except Exception:
        rotation["last_changed_ts"] = 0
    return rotation
