from utils.gd_validation import (
    combine_level_validation,
    parse_boomlings_level,
    parse_gdbrowser_level,
    validation_notice,
)


def test_gdbrowser_rejects_a_mismatched_returned_id():
    result = parse_gdbrowser_level({"id": "222222222", "name": "Wrong"}, "111111111")
    assert result["ok"] is False
    assert result["exists"] is None


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
