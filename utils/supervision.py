from __future__ import annotations

import asyncio


async def start_cog_background(bot, cog_name: str) -> None:
    """Serialize initial startup and supervisor repairs for each cog."""
    locks = getattr(bot, "_background_start_locks", None)
    if locks is None:
        locks = bot._background_start_locks = {}
    lock = locks.setdefault(cog_name, asyncio.Lock())
    async with lock:
        cog = bot.get_cog(cog_name)
        start = getattr(cog, "start_background", None)
        if callable(start):
            await start()
