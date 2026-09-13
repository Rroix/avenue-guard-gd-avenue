from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import utils.discord_refs as refs


def test_legacy_rounding_matches_the_ids_seen_in_turso_logs():
    exact_user_id = 1115678273079349288
    rounded_user_id = 1115678273079349248

    assert refs.legacy_rounded_snowflake(exact_user_id) == rounded_user_id
    assert refs.snowflake_matches_legacy(rounded_user_id, exact_user_id)
    assert refs.is_legacy_rounded_snowflake(rounded_user_id)


@pytest.mark.asyncio
async def test_persisted_message_recovers_exact_id_from_nearby_history(monkeypatch):
    class MissingMessage(Exception):
        pass

    exact_message_id = 1548671314074673201
    stored_message_id = refs.legacy_rounded_snowflake(exact_message_id)
    author = SimpleNamespace(id=1454985687177887866)
    expected = SimpleNamespace(
        id=exact_message_id,
        author=author,
        content="Avenue Guard state message",
    )

    class Channel:
        async def fetch_message(self, _message_id):
            raise MissingMessage

        async def history(self, **_kwargs):
            yield SimpleNamespace(
                id=exact_message_id + 1,
                author=SimpleNamespace(id=1),
                content="wrong author",
            )
            yield expected

    monkeypatch.setattr(refs.discord, "NotFound", MissingMessage)
    message, recovered = await refs.fetch_persisted_message(
        Channel(),
        stored_message_id,
        author_id=author.id,
        predicate=lambda candidate: "state message" in candidate.content,
    )

    assert message is expected
    assert recovered is True


@pytest.mark.asyncio
async def test_persisted_message_rejects_wrong_direct_match(monkeypatch):
    class MissingMessage(Exception):
        pass

    exact_message_id = 1548671314074673201
    stored_message_id = refs.legacy_rounded_snowflake(exact_message_id)
    expected_author = SimpleNamespace(id=1454985687177887866)
    expected = SimpleNamespace(id=exact_message_id, author=expected_author)
    wrong = SimpleNamespace(id=stored_message_id, author=SimpleNamespace(id=42))

    class Channel:
        async def fetch_message(self, _message_id):
            return wrong

        async def history(self, **_kwargs):
            yield wrong
            yield expected

    monkeypatch.setattr(refs.discord, "NotFound", MissingMessage)
    message, recovered = await refs.fetch_persisted_message(
        Channel(),
        stored_message_id,
        author_id=expected_author.id,
    )

    assert message is expected
    assert recovered is True


@pytest.mark.asyncio
async def test_persisted_message_rejects_multiple_candidates_in_same_float_bucket(monkeypatch):
    class MissingMessage(Exception):
        pass

    stored_message_id = 1548671314074673152
    author = SimpleNamespace(id=1454985687177887866)
    candidates = [
        SimpleNamespace(id=stored_message_id + 1, author=author),
        SimpleNamespace(id=stored_message_id + 40, author=author),
    ]
    assert all(
        refs.legacy_rounded_snowflake(candidate.id) == stored_message_id
        for candidate in candidates
    )

    class Channel:
        async def fetch_message(self, _message_id):
            raise MissingMessage

        async def history(self, **_kwargs):
            for candidate in candidates:
                yield candidate

    monkeypatch.setattr(refs.discord, "NotFound", MissingMessage)
    with pytest.raises(refs.PersistedReferenceAmbiguous):
        await refs.fetch_persisted_message(
            Channel(),
            stored_message_id,
            author_id=author.id,
        )


@pytest.mark.asyncio
async def test_persisted_channel_recovers_exact_id_from_cache(monkeypatch):
    class MissingChannel(Exception):
        pass

    exact_channel_id = 1548671314074673201
    stored_channel_id = refs.legacy_rounded_snowflake(exact_channel_id)
    expected = SimpleNamespace(id=exact_channel_id)
    guild = SimpleNamespace(
        channels=[expected],
        threads=[],
        get_channel=lambda _channel_id: None,
        fetch_channel=AsyncMock(
            side_effect=AssertionError("cache recovery should avoid REST")
        ),
    )
    monkeypatch.setattr(refs.discord, "NotFound", MissingChannel)

    channel, recovered = await refs.fetch_persisted_channel(guild, stored_channel_id)

    assert channel is expected
    assert recovered is True
    guild.fetch_channel.assert_not_awaited()
