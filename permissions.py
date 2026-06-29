from __future__ import annotations

import discord

# Any of these guild permissions marks a "moderator". Used to gate staff actions
# in the bot layer (not via Discord default_permissions) so the bot owner can also
# be allowed explicitly where needed.
_MOD_PERMS = (
    "manage_guild",
    "manage_messages",
    "moderate_members",
    "kick_members",
    "ban_members",
)


def is_mod(user: discord.abc.User | discord.Member) -> bool:
    perms = getattr(user, "guild_permissions", None)
    return perms is not None and any(getattr(perms, name) for name in _MOD_PERMS)
