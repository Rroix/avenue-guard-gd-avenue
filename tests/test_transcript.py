from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from utils.transcript import build_text_transcript


class FakeChannel:
    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = []

    def history(self, *, limit, oldest_first):
        self.calls.append((limit, oldest_first))

        async def iterator():
            messages = self.messages if oldest_first else list(reversed(self.messages))
            for message in messages[:limit]:
                yield message

        if limit is None:
            async def complete_iterator():
                messages = self.messages if oldest_first else list(reversed(self.messages))
                for message in messages:
                    yield message

            return complete_iterator()
        return iterator()


def make_message(message_id: int, content: str):
    return SimpleNamespace(
        id=message_id,
        created_at=datetime(2026, 7, message_id, tzinfo=timezone.utc),
        edited_at=None,
        author=SimpleNamespace(id=100 + message_id),
        content=content,
        attachments=[],
        embeds=[],
        stickers=[],
        reference=None,
    )


@pytest.mark.asyncio
async def test_transcript_defaults_to_complete_chronological_history():
    channel = FakeChannel(
        [
            make_message(1, "first"),
            make_message(2, "second"),
            make_message(3, "third"),
        ]
    )

    transcript = (await build_text_transcript(channel)).read().decode("utf-8")

    assert channel.calls == [(None, True)]
    assert transcript.index("first") < transcript.index("second") < transcript.index("third")
    assert "Older messages were omitted" not in transcript


@pytest.mark.asyncio
async def test_limited_transcript_keeps_newest_messages_and_discloses_truncation():
    channel = FakeChannel(
        [
            make_message(1, "oldest"),
            make_message(2, "middle"),
            make_message(3, "newest"),
        ]
    )

    transcript = (await build_text_transcript(channel, limit=2)).read().decode("utf-8")

    assert channel.calls == [(3, False)]
    assert "Older messages were omitted" in transcript
    assert "oldest" not in transcript
    assert transcript.index(": middle") < transcript.index(": newest")
