from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

try:
    from bs4 import BeautifulSoup
except ImportError:  # Production installs Beautiful Soup; the fallback keeps repair tooling usable.
    BeautifulSoup = None


GDBROWSER_LEVEL_HTML_URL = "https://gdbrowser.com/{level_id}"
GDBROWSER_PROFILE_HTML_URL = "https://gdbrowser.com{profile_path}"
GDBROWSER_PROFILE_API_URL = "https://gdbrowser.com/api/profile/{username}"
GDHISTORY_USER_URL = "https://history.geometrydash.eu/api/v1/user/{player_id}/brief/"
CP_RESOLUTION_STATES = {
    "pending",
    "resolving",
    "resolved",
    "conflict",
    "identity_unresolved",
    "providers_unavailable",
    "needs_attention",
    "manual_override",
}
CP_ERROR_CATEGORIES = {
    "timeout",
    "rate_limited",
    "forbidden",
    "not_found",
    "malformed",
    "identity_mismatch",
    "cp_missing",
    "provider_unavailable",
    "conflict",
    "internal",
}


@dataclass(frozen=True)
class CreatorPointsSettings:
    enabled: bool
    providers: dict[str, bool]
    initial_timeout_seconds: float
    provider_timeout_seconds: float
    current_cp_ttl_seconds: int
    level_identity_ttl_seconds: int
    retry_schedule_seconds: tuple[int, ...]
    attention_after_seconds: int
    escalation_after_seconds: int
    max_concurrency: int


def _bounded_number(value: Any, default: float, minimum: float, maximum: float, path: str) -> float:
    try:
        parsed = float(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{path} must be between {minimum:g} and {maximum:g}")
    return parsed


def _bounded_integer(value: Any, default: int, minimum: int, maximum: int, path: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be an integer")
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return parsed


def creator_points_settings(data: dict[str, Any]) -> CreatorPointsSettings:
    priority = data.get("priority_system")
    if not isinstance(priority, dict):
        priority = {}
    raw = priority.get("creator_points")
    if not isinstance(raw, dict):
        raw = {}
    providers = {
        "gdbrowser_html": bool(raw.get("gdbrowser_html_enabled", True)),
        "gdbrowser_api": bool(raw.get("gdbrowser_api_enabled", True)),
        "boomlings": bool(raw.get("boomlings_enabled", True)),
        "gdhistory": bool(raw.get("gdhistory_enabled", True)),
        "gdrateplus": bool(raw.get("gdrateplus_enabled", True)),
    }
    enabled = bool(raw.get("resolution_enabled", True))
    if enabled and not any(providers.values()):
        raise ValueError("creator_points requires at least one enabled provider")
    schedule = raw.get("retry_schedule_seconds", [30, 120, 600, 3600, 21600, 86400])
    if not isinstance(schedule, list) or not schedule:
        raise ValueError("creator_points.retry_schedule_seconds must be a non-empty list")
    retry_schedule = tuple(
        _bounded_integer(value, 30, 1, 604800, "creator_points.retry_schedule_seconds")
        for value in schedule
    )
    if tuple(sorted(retry_schedule)) != retry_schedule:
        raise ValueError("creator_points.retry_schedule_seconds must increase")
    return CreatorPointsSettings(
        enabled=enabled,
        providers=providers,
        initial_timeout_seconds=_bounded_number(raw.get("initial_timeout_seconds"), 8, 2, 30, "creator_points.initial_timeout_seconds"),
        provider_timeout_seconds=_bounded_number(raw.get("provider_timeout_seconds"), 4, 1, 15, "creator_points.provider_timeout_seconds"),
        current_cp_ttl_seconds=_bounded_integer(raw.get("current_cp_ttl_seconds"), 21600, 300, 604800, "creator_points.current_cp_ttl_seconds"),
        level_identity_ttl_seconds=_bounded_integer(raw.get("level_identity_ttl_seconds"), 2592000, 3600, 31536000, "creator_points.level_identity_ttl_seconds"),
        retry_schedule_seconds=retry_schedule,
        attention_after_seconds=_bounded_integer(raw.get("attention_after_seconds"), 900, 60, 604800, "creator_points.attention_after_seconds"),
        escalation_after_seconds=_bounded_integer(raw.get("escalation_after_seconds"), 86400, 300, 2592000, "creator_points.escalation_after_seconds"),
        max_concurrency=_bounded_integer(raw.get("max_concurrency"), 3, 1, 12, "creator_points.max_concurrency"),
    )


def normalize_gd_username(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "").strip()).casefold()


def explicit_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isascii() and text.isdecimal() else None


def response_fingerprint(value: str | bytes | dict[str, Any]) -> str:
    if isinstance(value, dict):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    elif isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode()
    return hashlib.sha256(raw).hexdigest()


def creator_key(*, account_id: Any = None, player_id: Any = None, username: Any = None) -> str | None:
    account = explicit_nonnegative_int(account_id)
    player = explicit_nonnegative_int(player_id)
    normalized = normalize_gd_username(username)
    if account is not None and account > 0:
        return f"account:{account}"
    if player is not None and player > 0:
        return f"player:{player}"
    return f"username:{normalized}" if normalized else None


class _SemanticHTML(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.title = ""
        self.meta_title = ""
        self._stack: list[str | None] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        node_id = values.get("id") or None
        if node_id:
            self.nodes[node_id] = {"attrs": values, "text": ""}
        if tag == "br":
            for parent_id in reversed(self._stack):
                if parent_id:
                    self.nodes[parent_id]["text"] += " "
                    break
        if tag not in self._VOID_TAGS:
            self._stack.append(node_id)
        if tag == "title":
            self._in_title = True
        if tag == "meta" and values.get("property") == "og:title":
            self.meta_title = values.get("content", "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        for node_id in reversed(self._stack):
            if node_id:
                self.nodes[node_id]["text"] += data
                break


def _fallback_document(html: str) -> _SemanticHTML:
    parser = _SemanticHTML()
    parser.feed(str(html or ""))
    return parser


def parse_gdbrowser_level_html(html: str, level_id: str) -> dict[str, Any]:
    fingerprint = response_fingerprint(html)
    soup = BeautifulSoup(str(html or ""), "html.parser") if BeautifulSoup else None
    fallback = None if soup else _fallback_document(html)
    title = (
        soup.title.get_text(" ", strip=True)
        if soup and soup.title
        else (str(fallback.title or "").strip() if fallback else "")
    )
    meta_title = soup.select_one('meta[property="og:title"]') if soup else None
    meta_content = (
        str(meta_title.get("content") or "")
        if meta_title
        else (str(fallback.meta_title or "") if fallback else "")
    )
    if str(level_id) not in title and str(level_id) not in meta_content:
        return {"ok": False, "error_category": "malformed", "response_fingerprint": fingerprint}
    author = soup.select_one("#authorName #authorLink, #authorLink") if soup else fallback.nodes.get("authorLink")
    if author is None:
        return {"ok": False, "error_category": "identity_mismatch", "response_fingerprint": fingerprint}
    href = str((author.get("href") if soup else author["attrs"].get("href")) or "").strip()
    parsed = urlsplit(href)
    path = parsed.path or href
    if not (path.startswith("/u/") or path.startswith("../u/") or path.startswith("/profile/")):
        return {"ok": False, "error_category": "identity_mismatch", "response_fingerprint": fingerprint}
    if path.startswith("../"):
        path = "/" + path[3:]
    author_text = author.get_text(" ", strip=True) if soup else str(author["text"] or "").strip()
    username = re.sub(r"^\s*by\s+", "", author_text, flags=re.I).strip()
    if not username:
        match = re.search(r"\bby\s+(.+)$", meta_content, flags=re.I)
        username = match.group(1).strip() if match else ""
    account_match = re.fullmatch(r"/u/(\d+)\.?/?", path)
    return {
        "ok": bool(username),
        "username": username or None,
        "account_id": int(account_match.group(1)) if account_match else None,
        "player_id": None,
        "profile_path": path,
        "response_fingerprint": fingerprint,
        "error_category": None if username else "identity_mismatch",
    }


def parse_gdbrowser_profile_html(html: str, *, expected_username: str | None = None, expected_account_id: int | None = None) -> dict[str, Any]:
    fingerprint = response_fingerprint(html)
    soup = BeautifulSoup(str(html or ""), "html.parser") if BeautifulSoup else None
    fallback = None if soup else _fallback_document(html)
    title = (
        soup.title.get_text(" ", strip=True)
        if soup and soup.title
        else (str(fallback.title or "").strip() if fallback else "")
    )
    title_match = re.match(r"(.+?)['’]s\s+Profile", title, flags=re.I)
    username = title_match.group(1).strip() if title_match else ""
    cp_node = soup.select_one("#cp") if soup else fallback.nodes.get("cp")
    cp_text = cp_node.get_text(strip=True) if soup and cp_node else (cp_node["text"].strip() if cp_node else None)
    cp = explicit_nonnegative_int(cp_text)
    ids_node = soup.select_one("#accountIDs") if soup else fallback.nodes.get("accountIDs")
    ids_text = ids_node.get_text(" ", strip=True) if soup and ids_node else (str(ids_node["text"] or "").strip() if ids_node else "")
    account_match = re.search(r"\bAccount\s+ID\s*:\s*(\d+)(?!\d)", ids_text, flags=re.I)
    player_match = re.search(r"\bPlayer\s+ID\s*:\s*(\d+)(?!\d)", ids_text, flags=re.I)
    account_id = int(account_match.group(1)) if account_match else None
    player_id = int(player_match.group(1)) if player_match else None
    identity_ok = True
    if expected_account_id is not None:
        identity_ok = account_id == int(expected_account_id)
    if expected_username and username:
        identity_ok = identity_ok and normalize_gd_username(username) == normalize_gd_username(expected_username)
    if not identity_ok:
        error = "identity_mismatch"
    elif cp is None:
        error = "cp_missing"
    elif not (username or account_id or player_id):
        error = "identity_mismatch"
    else:
        error = None
    return {
        "ok": error is None,
        "username": username or expected_username,
        "account_id": account_id,
        "player_id": player_id,
        "creator_points": cp,
        "error_category": error,
        "response_fingerprint": fingerprint,
    }


def parse_gdbrowser_profile_api(payload: Any, *, expected_username: str | None = None, expected_account_id: int | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error_category": "malformed", "response_fingerprint": response_fingerprint(str(payload))}
    username = str(payload.get("username") or "").strip()
    account_id = explicit_nonnegative_int(payload.get("accountID"))
    player_id = explicit_nonnegative_int(payload.get("playerID"))
    cp = explicit_nonnegative_int(payload.get("cp"))
    identity_ok = bool(username or account_id or player_id)
    if expected_account_id is not None:
        identity_ok = identity_ok and account_id == int(expected_account_id)
    if expected_username and username:
        identity_ok = identity_ok and normalize_gd_username(username) == normalize_gd_username(expected_username)
    error = None if identity_ok and cp is not None else ("identity_mismatch" if not identity_ok else "cp_missing")
    return {
        "ok": error is None,
        "username": username or expected_username,
        "account_id": account_id,
        "player_id": player_id,
        "creator_points": cp,
        "error_category": error,
        "response_fingerprint": response_fingerprint(payload),
    }


def identities_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_account = explicit_nonnegative_int(left.get("account_id"))
    right_account = explicit_nonnegative_int(right.get("account_id"))
    if left_account and right_account:
        return left_account == right_account
    left_player = explicit_nonnegative_int(left.get("player_id"))
    right_player = explicit_nonnegative_int(right.get("player_id"))
    left_name = normalize_gd_username(left.get("username"))
    right_name = normalize_gd_username(right.get("username"))
    if left_player and right_player and left_name and right_name:
        return left_player == right_player and left_name == right_name
    return bool(left_name and right_name and left_name == right_name)


def canonical_identity(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [item for item in observations if item.get("username") or item.get("account_id") or item.get("player_id")]
    if not candidates:
        return None
    groups: list[list[dict[str, Any]]] = []
    for item in candidates:
        for group in groups:
            if any(identities_match(item, member) for member in group):
                group.append(item)
                break
        else:
            groups.append([item])
    groups.sort(key=lambda group: (len(group), sum(item.get("account_id") is not None for item in group), sum(item.get("player_id") is not None for item in group)), reverse=True)
    group = groups[0]
    accounts = {explicit_nonnegative_int(item.get("account_id")) for item in group if explicit_nonnegative_int(item.get("account_id"))}
    players = {explicit_nonnegative_int(item.get("player_id")) for item in group if explicit_nonnegative_int(item.get("player_id"))}
    names = [str(item.get("username") or "").strip() for item in group if str(item.get("username") or "").strip()]
    if len(accounts) > 1 or len(players) > 1:
        return None
    confidence = "account_id" if accounts else ("player_username" if players and names else ("username_consensus" if len(group) >= 2 else "single_source"))
    return {
        "username": names[0] if names else None,
        "account_id": next(iter(accounts), None),
        "player_id": next(iter(players), None),
        "confidence": confidence,
        "creator_key": creator_key(account_id=next(iter(accounts), None), player_id=next(iter(players), None), username=names[0] if names else None),
    }


def select_creator_points(observations: list[dict[str, Any]], identity: dict[str, Any]) -> dict[str, Any]:
    usable = [
        item for item in observations
        if item.get("success") and explicit_nonnegative_int(item.get("creator_points")) is not None and identities_match(item, identity)
    ]
    if not usable:
        return {"resolved": False, "state": "providers_unavailable", "creator_points": None}
    direct = [item for item in usable if item.get("provider") == "boomlings" and item.get("method") == "direct_profile"]
    if direct:
        chosen = max(direct, key=lambda item: int(item.get("observed_at") or 0))
        return {"resolved": True, "state": "resolved", "creator_points": int(chosen["creator_points"]), "source": "boomlings", "confidence": "direct_authoritative", "observation": chosen}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in usable:
        grouped.setdefault(int(item["creator_points"]), []).append(item)
    consensus = [
        group for group in grouped.values()
        if len({item.get("provider") for item in group if item.get("provider") != "cache"}) >= 2
    ]
    if consensus:
        consensus.sort(
            key=lambda group: (
                len({item.get("provider") for item in group if item.get("provider") != "cache"}),
                max(int(item.get("observed_at") or 0) for item in group),
            ),
            reverse=True,
        )
        chosen_group = consensus[0]
        chosen = max(chosen_group, key=lambda item: int(item.get("observed_at") or 0))
        return {"resolved": True, "state": "resolved", "creator_points": int(chosen["creator_points"]), "source": "consensus", "confidence": "consensus", "observation": chosen}
    values = set(grouped)
    if len(values) > 1:
        current_html = [item for item in usable if item.get("provider") == "gdbrowser" and item.get("method") == "html_profile" and not item.get("archival")]
        archival_only_others = all(item.get("archival") for item in usable if item not in current_html)
        if current_html and archival_only_others:
            chosen = max(current_html, key=lambda item: int(item.get("observed_at") or 0))
            return {"resolved": True, "state": "resolved", "creator_points": int(chosen["creator_points"]), "source": "gdbrowser_html", "confidence": "verified_single_source", "observation": chosen}
        return {"resolved": False, "state": "conflict", "creator_points": None}
    chosen = max(usable, key=lambda item: (item.get("provider") == "gdbrowser" and item.get("method") == "html_profile", int(item.get("observed_at") or 0)))
    return {"resolved": True, "state": "resolved", "creator_points": int(chosen["creator_points"]), "source": "gdbrowser_html" if chosen.get("provider") == "gdbrowser" and chosen.get("method") == "html_profile" else str(chosen.get("provider")), "confidence": "verified_single_source", "observation": chosen}
