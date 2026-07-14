from __future__ import annotations

import json
import time
from typing import Any, Dict

import aiohttp


GDBROWSER_LEVEL_URL = "https://gdbrowser.com/api/level/{level_id}"
BOOMLINGS_LEVEL_URL = "https://www.boomlings.com/database/getGJLevels21.php"
# Public Geometry Dash protocol value, not an application credential.
COMMON_SECRET = "Wmfd2893gb7"  # nosec B105


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on", "epic", "legendary", "mythic"}


def _kv_pairs(text: str) -> dict[str, str]:
    parts = str(text or "").split(":")
    return {parts[i]: parts[i + 1] for i in range(0, max(len(parts) - 1, 0), 2)}


def _boomlings_creator_map(creators_text: str) -> dict[str, str]:
    creators: dict[str, str] = {}
    for item in str(creators_text or "").split("|"):
        parts = item.split(":")
        if len(parts) >= 2:
            creators[parts[0]] = parts[1]
    return creators


def _demon_difficulty(code: Any) -> str:
    return {
        3: "Easy Demon",
        4: "Medium Demon",
        0: "Hard Demon",
        5: "Insane Demon",
        6: "Extreme Demon",
    }.get(_as_int(code, -99), "Demon")


def _classic_difficulty(code: Any) -> str:
    return {
        0: "N/A",
        10: "Easy",
        20: "Normal",
        30: "Hard",
        40: "Harder",
        50: "Insane",
    }.get(_as_int(code), "Unknown")


def _length_name(code: Any) -> str:
    return {
        0: "Tiny",
        1: "Short",
        2: "Medium",
        3: "Long",
        4: "XL",
        5: "Platformer",
    }.get(_as_int(code, -1), "Unknown")


def _provider_error(provider: str, message: str) -> dict[str, Any]:
    return {"provider": provider, "ok": False, "exists": None, "error": message}


def parse_gdbrowser_level(payload: Any, level_id: str) -> dict[str, Any]:
    if payload == -1 or str(payload).strip() == "-1":
        return {"provider": "gdbrowser", "ok": True, "exists": False}
    if not isinstance(payload, dict):
        return _provider_error("gdbrowser", "Unexpected response")

    returned_id = str(payload.get("id") or "").strip()
    if returned_id and returned_id != str(level_id).strip():
        return _provider_error("gdbrowser", "Response level ID did not match the requested ID")

    difficulty = str(payload.get("difficulty") or "")
    length = str(payload.get("length") or "")
    stars = _as_int(payload.get("stars"), 0)
    featured = _as_bool(payload.get("featured"))
    epic = _as_bool(payload.get("epic")) or _as_int(payload.get("epic"), 0) > 0
    cp = _as_int(payload.get("cp"), 0)
    demon = _as_bool(payload.get("demon")) or "demon" in difficulty.casefold()
    platformer = _as_bool(payload.get("platformer")) or "platformer" in length.casefold()

    return {
        "provider": "gdbrowser",
        "ok": True,
        "exists": True,
        "level_id": returned_id or str(level_id),
        "name": str(payload.get("name") or ""),
        "creator": str(payload.get("author") or ""),
        "difficulty": difficulty or "Unknown",
        "length": length or "Unknown",
        "stars": stars,
        "rated": stars > 0 or featured or epic or cp > 0,
        "featured": featured,
        "epic": epic,
        "demon": demon,
        "platformer": platformer,
    }


def parse_boomlings_level(text: str, level_id: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw or raw == "-1":
        return {"provider": "boomlings", "ok": True, "exists": False}

    sections = raw.split("#")
    levels_text = sections[0] if sections else ""
    creators = _boomlings_creator_map(sections[1] if len(sections) > 1 else "")
    level_parts = [part for part in levels_text.split("|") if part]
    if not level_parts:
        return {"provider": "boomlings", "ok": True, "exists": False}

    selected = None
    for item in level_parts:
        parsed = _kv_pairs(item)
        if str(parsed.get("1") or "") == str(level_id):
            selected = parsed
            break
    if selected is None:
        # Search endpoints can return related/popular levels even when the
        # exact ID is absent. Treating the first result as the requested level
        # can validate and display metadata for the wrong submission.
        return {"provider": "boomlings", "ok": True, "exists": False}

    parsed_id = str(selected.get("1") or level_id)
    stars = _as_int(selected.get("18"), 0)
    feature_score = _as_int(selected.get("19"), 0)
    epic = _as_int(selected.get("42"), 0)
    demon = _as_bool(selected.get("17"))
    length_code = _as_int(selected.get("15"), -1)
    platformer = length_code == 5
    difficulty = _demon_difficulty(selected.get("43")) if demon else _classic_difficulty(selected.get("9"))
    if platformer and stars > 0:
        difficulty = f"{difficulty} Platformer" if difficulty != "Unknown" else "Platformer"

    return {
        "provider": "boomlings",
        "ok": True,
        "exists": True,
        "level_id": parsed_id,
        "name": str(selected.get("2") or ""),
        "creator": creators.get(str(selected.get("6") or ""), ""),
        "difficulty": difficulty,
        "length": _length_name(length_code),
        "stars": stars,
        "rated": stars > 0 or feature_score > 0 or epic > 0,
        "featured": feature_score > 0,
        "epic": epic > 0,
        "demon": demon,
        "platformer": platformer,
    }


async def fetch_gdbrowser_level(session: aiohttp.ClientSession, level_id: str) -> dict[str, Any]:
    try:
        async with session.get(GDBROWSER_LEVEL_URL.format(level_id=level_id)) as resp:
            text = await resp.text()
            if resp.status == 404 or text.strip() == "-1":
                return {"provider": "gdbrowser", "ok": True, "exists": False}
            if resp.status >= 400:
                return _provider_error("gdbrowser", f"HTTP {resp.status}")
            try:
                payload = json.loads(text)
            except Exception:
                payload = text
            return parse_gdbrowser_level(payload, level_id)
    except Exception as e:
        return _provider_error("gdbrowser", type(e).__name__)


async def fetch_boomlings_level(session: aiohttp.ClientSession, level_id: str) -> dict[str, Any]:
    payload = {
        "str": str(level_id),
        "type": "10",
        "secret": COMMON_SECRET,
    }
    headers = {"User-Agent": ""}
    try:
        async with session.post(BOOMLINGS_LEVEL_URL, data=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status >= 400:
                return _provider_error("boomlings", f"HTTP {resp.status}")
            return parse_boomlings_level(text, level_id)
    except Exception as e:
        return _provider_error("boomlings", type(e).__name__)


def combine_level_validation(
    level_id: str,
    provider_results: Dict[str, dict[str, Any]],
    checked_ts: int | None = None,
    expires_ts: int | None = None,
) -> dict[str, Any]:
    checked_ts = int(checked_ts or time.time())
    expires_ts = int(expires_ts or checked_ts)
    results = {str(k): dict(v or {}) for k, v in provider_results.items()}
    successful = [result for result in results.values() if result.get("ok")]
    existing = [result for result in successful if result.get("exists") is True]
    missing = [result for result in successful if result.get("exists") is False]
    failed = [result for result in results.values() if not result.get("ok")]
    disagreement = bool(existing and missing)
    all_requested_succeeded = bool(results) and not failed

    if existing:
        exists: bool | None = True
    elif missing and all_requested_succeeded:
        exists = False
    else:
        exists = None

    missing_confident = exists is False and all_requested_succeeded and bool(missing)
    rated = any(bool(result.get("rated")) for result in existing)
    requires_showcase = any(bool(result.get("demon")) or bool(result.get("platformer")) for result in existing)
    chosen = existing[0] if existing else {}

    warnings: list[str] = []
    if disagreement:
        warnings.append("GDBrowser and the GD API disagreed. Please check this level manually.")
    elif exists is None and missing:
        warnings.append("This level doesn't seem to exist, but one validation source failed, so it was not auto-blocked.")
    elif exists is None:
        warnings.append("Level validation could not run right now. Please check this level manually.")
    elif exists is False and not missing_confident:
        warnings.append("This level doesn't seem to exist, but validation was not confident enough to block it.")
    if rated:
        warnings.append("This level seems to have been rated already.")
    if requires_showcase:
        warnings.append("This level appears to be a demon or platformer; a showcase is required.")

    sources = []
    for provider, result in sorted(results.items()):
        if result.get("ok") and result.get("exists") is True:
            status = "found"
        elif result.get("ok") and result.get("exists") is False:
            status = "missing"
        else:
            status = f"failed ({result.get('error') or 'unknown'})"
        sources.append(f"{provider}: {status}")

    return {
        "level_id": str(level_id),
        "checked_ts": checked_ts,
        "expires_ts": expires_ts,
        "providers": results,
        "exists": exists,
        "missing_confident": missing_confident,
        "rated": rated,
        "requires_showcase": requires_showcase,
        "warnings": warnings,
        "provider_disagreement": disagreement,
        "level_name": str(chosen.get("name") or ""),
        "creator": str(chosen.get("creator") or ""),
        "stars": chosen.get("stars"),
        "difficulty": str(chosen.get("difficulty") or ""),
        "length": str(chosen.get("length") or ""),
        "featured": bool(chosen.get("featured")),
        "epic": bool(chosen.get("epic")),
        "demon": bool(chosen.get("demon")),
        "platformer": bool(chosen.get("platformer")),
        "source_summary": " | ".join(sources),
    }


def validation_notice(result: dict[str, Any]) -> str:
    if not result:
        return ""
    warnings = [str(item) for item in result.get("warnings") or [] if str(item).strip()]
    source_summary = str(result.get("source_summary") or "").strip()
    expires_ts = _as_int(result.get("expires_ts"), 0)
    checked_ts = _as_int(result.get("checked_ts"), 0)

    parts: list[str] = []
    if warnings:
        parts.extend(warnings)
    else:
        parts.append("No validation warnings.")
    if source_summary:
        parts.append(f"Sources: {source_summary}.")
    if checked_ts:
        parts.append(f"Checked <t:{checked_ts}:R>.")
    if expires_ts:
        parts.append(f"Refreshes <t:{expires_ts}:R>.")
    return "\n".join(parts)[:1024]
