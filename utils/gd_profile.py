"""Direct public GD account lookup, intentionally separate from live validation."""
from __future__ import annotations

import aiohttp

from utils.gd_validation import (
    COMMON_SECRET, _audit_integer, _http_error, _provider_error, _read_provider_text,
)

BOOMLINGS_PROFILE_URL = "https://www.boomlings.com/database/getGJUserInfo20.php"


def parse_creator_profile(text: str, account_id: str) -> dict:
    # Profile resource keys: 2=user ID, 16=account ID, 8=account creator points.
    # Verify identity and field presence instead of trusting a positional/default value.
    parts = text.strip().split(":")
    invalid = _provider_error("boomlings", "Invalid or mismatched account profile", failure_kind="invalid_response")
    if len(parts) % 2 or len(parts) < 6:
        return invalid
    fields = dict(zip(parts[::2], parts[1::2], strict=True))
    if len(fields) != len(parts) // 2:
        return invalid
    returned_account = _audit_integer(fields.get("16"))
    user_id = _audit_integer(fields.get("2"))
    points = _audit_integer(fields.get("8"))
    if returned_account != int(account_id) or not user_id or points is None:
        return invalid
    return {"provider": "boomlings", "ok": True, "account_id": str(returned_account),
            "user_id": str(user_id), "name": fields.get("1"), "current_creator_points": points}


async def fetch_creator_profile(session: aiohttp.ClientSession, account_id: str) -> dict:
    try:
        async with session.post(
            BOOMLINGS_PROFILE_URL,
            data={"targetAccountID": account_id, "secret": COMMON_SECRET},
            headers={"Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded",
                     "Cookie": "gd=1;", "Host": "www.boomlings.com", "User-Agent": ""},
            allow_redirects=False,
        ) as response:
            text, error = await _read_provider_text(response, "boomlings")
            if error:
                return error
            if response.status != 200:
                return _http_error("boomlings", response)
            return parse_creator_profile(text, account_id)
    except (aiohttp.ClientError, TimeoutError) as exc:
        return _provider_error("boomlings", type(exc).__name__, failure_kind="network_error", retryable=True)
