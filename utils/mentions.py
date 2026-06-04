from __future__ import annotations

import discord


def no_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)


def user_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)


def user_and_role_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=False)
