from types import SimpleNamespace

import pytest

from utils.gd_validation import (
    combine_level_validation,
    fetch_boomlings_level,
    fetch_gdbrowser_level,
    parse_boomlings_level,
    parse_gdbrowser_level,
    validation_notice,
)


class _FakeContent:
    def __init__(self, body: bytes):
        self.body = body

    async def read(self, limit: int):
        return self.body[:limit]


class _FakeResponse:
    def __init__(self, status: int, body: str, headers=None):
        self.status = status
        self.headers = headers or {}
        self.charset = "utf-8"
        self.content = _FakeContent(body.encode("utf-8"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.request = None

    def get(self, url, **kwargs):
        self.request = SimpleNamespace(method="GET", url=url, **kwargs)
        return self.response

    def post(self, url, **kwargs):
        self.request = SimpleNamespace(method="POST", url=url, **kwargs)
        return self.response


def test_gdbrowser_rejects_a_mismatched_returned_id():
    result = parse_gdbrowser_level({"id": "222222222", "name": "Wrong"}, "111111111")
    assert result["ok"] is False
    assert result["exists"] is None


def test_gdbrowser_rejects_a_success_payload_without_an_id():
    result = parse_gdbrowser_level({"name": "Not enough evidence"}, "111111111")

    assert result["ok"] is False
    assert result["failure_kind"] == "invalid_response"


def test_boomlings_selects_only_the_exact_level_and_maps_metadata():
    response = (
        "1:111111111:2:Example:6:42:9:50:15:4:17:1:18:10:19:1:42:1:43:6"
        "|1:222222222:2:Other:6:43:9:10:15:1:18:0"
        "#42:CreatorName:0|43:OtherCreator:0"
    )
    result = parse_boomlings_level(response, "111111111")

    assert result["exists"] is True
    assert result["name"] == "Example"
    assert result["creator"] == "CreatorName"
    assert result["difficulty"] == "Extreme Demon"
    assert result["length"] == "XL"
    assert result["rated"] is True
    assert result["demon"] is True


def test_boomlings_does_not_accept_a_related_but_different_level():
    response = "1:222222222:2:Related:6:43:9:10:15:1:18:0#43:Creator:0"
    result = parse_boomlings_level(response, "111111111")
    assert result == {"provider": "boomlings", "ok": True, "exists": False}


def test_boomlings_does_not_treat_an_html_success_page_as_a_missing_level():
    result = parse_boomlings_level("<html>Cloudflare challenge</html>", "111111111")

    assert result["ok"] is False
    assert result["failure_kind"] == "invalid_response"


def test_all_requested_sources_must_agree_before_missing_is_confident():
    missing = {"provider": "gdbrowser", "ok": True, "exists": False}
    failed = {"provider": "boomlings", "ok": False, "exists": None, "error": "timeout"}

    uncertain = combine_level_validation("111111111", {"gdbrowser": missing, "boomlings": failed})
    assert uncertain["exists"] is None
    assert uncertain["missing_confident"] is False

    confident = combine_level_validation(
        "111111111",
        {"gdbrowser": missing, "boomlings": {**missing, "provider": "boomlings"}},
    )
    assert confident["exists"] is False
    assert confident["missing_confident"] is True


def test_disagreement_keeps_request_reviewable_and_surfaces_warning():
    result = combine_level_validation(
        "111111111",
        {
            "gdbrowser": {
                "provider": "gdbrowser",
                "ok": True,
                "exists": True,
                "name": "Example",
                "rated": True,
                "demon": True,
            },
            "boomlings": {"provider": "boomlings", "ok": True, "exists": False},
        },
        checked_ts=100,
        expires_ts=200,
    )

    assert result["exists"] is True
    assert result["provider_disagreement"] is True
    assert result["rated"] is True
    assert result["requires_showcase"] is True
    notice = validation_notice(result)
    assert "disagreed" in notice
    assert "Refreshes <t:200:R>" in notice


@pytest.mark.asyncio
async def test_boomlings_uses_current_form_headers_and_classifies_access_denial():
    session = _FakeSession(_FakeResponse(403, "Cloudflare denied this request"))

    result = await fetch_boomlings_level(session, "111111111")

    assert result["failure_kind"] == "access_denied"
    assert result["status_code"] == 403
    assert result["retryable"] is False
    assert session.request.data["gameVersion"] == "22"
    assert session.request.data["binaryVersion"] == "45"
    assert session.request.headers["User-Agent"] == ""
    assert session.request.headers["Cookie"] == "gd=1;"
    assert session.request.headers["Content-Type"] == "application/x-www-form-urlencoded"


@pytest.mark.asyncio
async def test_provider_response_body_is_bounded():
    session = _FakeSession(_FakeResponse(200, "x" * 1_000_001))

    result = await fetch_gdbrowser_level(session, "111111111")

    assert result["ok"] is False
    assert result["failure_kind"] == "invalid_response"
    assert result["error"] == "Response was too large"


@pytest.mark.asyncio
async def test_rate_limit_preserves_bounded_retry_after():
    session = _FakeSession(_FakeResponse(429, "slow down", {"Retry-After": "99999"}))

    result = await fetch_gdbrowser_level(session, "111111111")

    assert result["failure_kind"] == "rate_limited"
    assert result["retryable"] is True
    assert result["retry_after_seconds"] == 3600


@pytest.mark.asyncio
async def test_server_error_body_cannot_be_mistaken_for_a_missing_level():
    session = _FakeSession(_FakeResponse(503, "-1"))

    result = await fetch_gdbrowser_level(session, "111111111")

    assert result["ok"] is False
    assert result["failure_kind"] == "upstream_unavailable"
