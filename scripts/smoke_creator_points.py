#!/usr/bin/env python3
"""Optional read-only live smoke test for Creator Points resolution.

This script never opens the Avenue Guard database. Third-party availability is
reported as evidence, not treated as a test failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import ssl
import sys
import time
from urllib.parse import quote

import aiohttp
import certifi

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.creator_points import (
    GDBROWSER_LEVEL_HTML_URL,
    GDBROWSER_PROFILE_API_URL,
    GDBROWSER_PROFILE_HTML_URL,
    canonical_identity,
    parse_gdbrowser_level_html,
    parse_gdbrowser_profile_api,
    parse_gdbrowser_profile_html,
    select_creator_points,
)
from utils.gd_profile import fetch_creator_profile
from utils.gd_validation import (
    fetch_boomlings_level,
    fetch_gdhistory_level,
    fetch_gdrateplus_level,
)


def _level_observation(provider: str, result: dict) -> dict:
    metadata = result.get("audit_metadata") if isinstance(result.get("audit_metadata"), dict) else {}
    return {
        "provider": provider,
        "method": "level",
        "username": result.get("creator"),
        "account_id": metadata.get("uploader_account_id"),
        "player_id": metadata.get("uploader_user_id"),
        "creator_points": metadata.get("creator_points"),
        "observed_at": int(time.time()),
        "success": bool(result.get("ok") and result.get("exists") is True),
        "error_category": None if result.get("ok") else result.get("failure_kind"),
        "archival": provider == "gdhistory",
    }


async def _text(session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
    try:
        async with session.get(
            url,
            headers={"Accept": "text/html,application/json;q=0.8", "User-Agent": "Avenue-Guard/1 creator-resolution-smoke"},
        ) as response:
            return int(response.status), await response.text(errors="replace")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return 0, ""


async def smoke_level(session: aiohttp.ClientSession, level_id: str, *, api_fallback: bool) -> dict:
    provider_results = await asyncio.gather(
        fetch_boomlings_level(session, level_id),
        fetch_gdhistory_level(session, level_id),
        fetch_gdrateplus_level(session, level_id),
        return_exceptions=True,
    )
    observations: list[dict] = []
    provider_status: dict[str, dict] = {}
    for provider, result in zip(("boomlings", "gdhistory", "gdrateplus"), provider_results, strict=True):
        if isinstance(result, Exception):
            provider_status[provider] = {"ok": False, "error": type(result).__name__}
            continue
        provider_status[provider] = {
            "ok": bool(result.get("ok")),
            "exists": result.get("exists"),
            "error": result.get("failure_kind") or result.get("error"),
        }
        level_observation = _level_observation(provider, result)
        observations.append(level_observation)
        if provider == "boomlings" and level_observation["success"] and level_observation.get("account_id"):
            profile = await fetch_creator_profile(session, str(level_observation["account_id"]))
            observations.append(
                {
                    "provider": "boomlings",
                    "method": "direct_profile",
                    "username": profile.get("name") or level_observation.get("username"),
                    "account_id": int(profile["account_id"]) if profile.get("account_id") else level_observation.get("account_id"),
                    "player_id": int(profile["user_id"]) if profile.get("user_id") else level_observation.get("player_id"),
                    "creator_points": profile.get("current_creator_points") if profile.get("ok") else None,
                    "observed_at": int(time.time()),
                    "success": bool(profile.get("ok")),
                    "error_category": None if profile.get("ok") else profile.get("failure_kind"),
                    "archival": False,
                }
            )

    status, html = await _text(session, GDBROWSER_LEVEL_HTML_URL.format(level_id=level_id))
    parsed_level = parse_gdbrowser_level_html(html, level_id) if status == 200 else {"ok": False, "error_category": f"http_{status or 'network'}"}
    provider_status["gdbrowser"] = {"ok": bool(parsed_level.get("ok")), "status": status, "error": parsed_level.get("error_category")}
    if parsed_level.get("ok"):
        observations.append(
            {
                "provider": "gdbrowser",
                "method": "html_level",
                "username": parsed_level.get("username"),
                "account_id": parsed_level.get("account_id"),
                "player_id": parsed_level.get("player_id"),
                "creator_points": None,
                "observed_at": int(time.time()),
                "success": True,
                "error_category": None,
                "archival": False,
            }
        )
        profile_status, profile_html = await _text(
            session,
            GDBROWSER_PROFILE_HTML_URL.format(profile_path=parsed_level["profile_path"]),
        )
        parsed_profile = (
            parse_gdbrowser_profile_html(
                profile_html,
                expected_username=parsed_level.get("username"),
                expected_account_id=parsed_level.get("account_id"),
            )
            if profile_status == 200
            else {"ok": False, "error_category": f"http_{profile_status or 'network'}"}
        )
        observations.append(
            {
                "provider": "gdbrowser",
                "method": "html_profile",
                "username": parsed_profile.get("username") or parsed_level.get("username"),
                "account_id": parsed_profile.get("account_id") or parsed_level.get("account_id"),
                "player_id": parsed_profile.get("player_id"),
                "creator_points": parsed_profile.get("creator_points"),
                "observed_at": int(time.time()),
                "success": bool(parsed_profile.get("ok")),
                "error_category": parsed_profile.get("error_category"),
                "archival": False,
            }
        )
        if api_fallback and not parsed_profile.get("ok"):
            api_status, api_text = await _text(
                session,
                GDBROWSER_PROFILE_API_URL.format(username=quote(str(parsed_level.get("username") or ""))),
            )
            try:
                api_payload = json.loads(api_text) if api_status == 200 else None
            except ValueError:
                api_payload = None
            parsed_api = parse_gdbrowser_profile_api(
                api_payload,
                expected_username=parsed_level.get("username"),
                expected_account_id=parsed_level.get("account_id"),
            )
            observations.append(
                {
                    "provider": "gdbrowser",
                    "method": "api_profile",
                    "username": parsed_api.get("username"),
                    "account_id": parsed_api.get("account_id"),
                    "player_id": parsed_api.get("player_id"),
                    "creator_points": parsed_api.get("creator_points"),
                    "observed_at": int(time.time()),
                    "success": bool(parsed_api.get("ok")),
                    "error_category": parsed_api.get("error_category"),
                    "archival": False,
                }
            )

    identity = canonical_identity(observations)
    accepted = select_creator_points(observations, identity) if identity else {"resolved": False, "state": "identity_unresolved", "creator_points": None}
    safe_observations = [
        {
            "provider": item.get("provider"),
            "method": item.get("method"),
            "username": item.get("username"),
            "account_id": item.get("account_id"),
            "player_id": item.get("player_id"),
            "creator_points": item.get("creator_points"),
            "success": bool(item.get("success")),
            "error_category": item.get("error_category"),
        }
        for item in observations
    ]
    return {
        "level_id": level_id,
        "identity": identity,
        "resolved": bool(accepted.get("resolved")),
        "creator_points": accepted.get("creator_points"),
        "source": accepted.get("source"),
        "confidence": accepted.get("confidence"),
        "state": accepted.get("state"),
        "providers": provider_status,
        "observations": safe_observations,
    }


async def main_async(args) -> int:
    timeout = aiohttp.ClientTimeout(total=max(2.0, float(args.timeout)))
    connector = aiohttp.TCPConnector(
        ssl=ssl.create_default_context(cafile=certifi.where()),
        limit=8,
        limit_per_host=2,
        ttl_dns_cache=300,
    )
    async with aiohttp.ClientSession(timeout=timeout, connector=connector, trust_env=False) as session:
        results = []
        for level_id in args.level:
            results.append(await smoke_level(session, level_id, api_fallback=args.api_fallback))
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Acknowledge that third-party providers will be queried")
    parser.add_argument("--level", action="append", required=True, help="Geometry Dash level ID (repeatable)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Per-request timeout in seconds")
    parser.add_argument("--api-fallback", action="store_true", help="Allow the cautious GDBrowser API fallback")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this smoke test performs real provider requests")
    if any(not value.isascii() or not value.isdecimal() for value in args.level):
        parser.error("every --level value must contain only ASCII digits")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
