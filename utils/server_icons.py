from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import parse_qs, urlparse

VALID_SERVER_ICON_MODES = {"random", "linear", "disabled"}


def normalize_server_icon_mode(value: Any) -> str:
    mode = str(value or "disabled").strip().casefold()
    return mode if mode in VALID_SERVER_ICON_MODES else "disabled"


def is_valid_icon_url(value: Any) -> bool:
    url = str(value or "").strip()
    if not url or len(url) > 2000:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global


def is_expiring_discord_attachment_url(value: Any) -> bool:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    if not (host.endswith("discordapp.net") or host.endswith("discordapp.com")):
        return False
    if "/attachments/" not in parsed.path:
        return False
    query = parse_qs(parsed.query)
    return bool({"ex", "is", "hm"} & set(query))


def server_icon_url_warning(value: Any) -> str:
    if is_expiring_discord_attachment_url(value):
        return "Discord attachment URLs expire and eventually return 404. Use a permanent image URL instead."
    return ""


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


def parse_server_icon_index(value: Any, url_count: int) -> int:
    try:
        index = int(value)
    except Exception:
        return -1
    if index < 0 or index >= max(0, int(url_count)):
        return -1
    return index


def ensure_server_icon_config(config) -> dict:
    background = config.data.setdefault("background", {})
    rotation = background.setdefault("server_icon_rotation", {})
    rotation.setdefault(
        "_comment",
        "Rotates the server icon from configured image URLs. mode is disabled, linear, or random.",
    )
    rotation["mode"] = normalize_server_icon_mode(rotation.get("mode", "disabled"))
    try:
        rotation["interval_seconds"] = max(300, int(rotation.get("interval_seconds", 86400) or 86400))
    except Exception:
        rotation["interval_seconds"] = 86400
    rotation["urls"] = clean_icon_urls(rotation.get("urls", []))
    rotation["current_index"] = parse_server_icon_index(rotation.get("current_index", -1), len(rotation["urls"]))
    current_url = str(rotation.get("current_url", "") or "").strip()
    rotation["current_url"] = current_url if current_url in rotation["urls"] else ""
    try:
        rotation["last_changed_ts"] = max(0, int(rotation.get("last_changed_ts", 0) or 0))
    except Exception:
        rotation["last_changed_ts"] = 0
    rotation["last_error"] = str(rotation.get("last_error", "") or "")[:500]
    try:
        rotation["last_error_ts"] = max(0, int(rotation.get("last_error_ts", 0) or 0))
    except Exception:
        rotation["last_error_ts"] = 0
    return rotation
