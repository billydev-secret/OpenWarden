"""
utils/permissions.py — Permission and hierarchy helpers.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands

if TYPE_CHECKING:
    from models.guild_config import GuildConfig


async def check_hierarchy(
    guild: discord.Guild,
    bot_member: discord.Member,
    target_member: discord.Member,
    jail_role: Optional[discord.Role] = None,
    invoker: Optional[discord.Member] = None,
) -> Optional[str]:
    """
    Verify the bot (and optionally the invoking moderator) can act on the target.

    Returns an error message string if a check fails, else None.
    """
    # Cannot act on the guild owner
    if target_member.id == guild.owner_id:
        return "I cannot jail the server owner."

    # Bot's top role must be above the target's top role
    if bot_member.top_role <= target_member.top_role:
        return (
            f"My highest role ({bot_member.top_role.mention}) is not above "
            f"{target_member.mention}'s highest role ({target_member.top_role.mention}). "
            "Please move my role higher."
        )

    # If a jail role was provided, the bot must be above it too
    if jail_role and bot_member.top_role <= jail_role:
        return (
            f"My highest role is not above the jail role ({jail_role.mention}). "
            "Please move my role above the jail role."
        )

    # Invoker's top role must be above the target's top role (skip for guild owner)
    if invoker and invoker.id != guild.owner_id:
        if invoker.top_role <= target_member.top_role:
            return (
                f"Your highest role ({invoker.top_role.mention}) is not above "
                f"{target_member.mention}'s highest role ({target_member.top_role.mention})."
            )

    return None


def staff_check(permission_text: str = "You need **Manage Server** permission.") -> app_commands.check:
    """
    Reusable app_commands.check that requires staff permission (admin, manage_guild, or staff role).
    Replaces the copy-pasted _staff_check() that existed in every cog.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        from utils.embeds import error_embed
        config = await interaction.client.db.get_guild_config(interaction.guild_id)
        if not is_staff(interaction.user, config):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", permission_text),
                ephemeral=True,
            )
            return False
        return True
    return app_commands.check(predicate)


async def ensure_configured(
    config: Optional["GuildConfig"],
    required_fields: list[str],
) -> Optional[str]:
    """
    Return an error message if any required field on config is None/falsy.
    Returns None when all required fields are present.
    """
    if config is None:
        return (
            "This server has not been configured yet. "
            "Run `/jail setup` to get started."
        )

    missing = []
    for field in required_fields:
        value = getattr(config, field, None)
        if not value:
            missing.append(f"`{field}`")

    if missing:
        return (
            f"The following configuration fields are missing: {', '.join(missing)}. "
            "Use `/jail config` subcommands to set them."
        )

    return None


def is_staff(member: discord.Member, config: Optional["GuildConfig"] = None) -> bool:
    """
    Return True if the member should be treated as staff.

    A member is staff if they have:
    - Administrator permission, OR
    - Manage Guild permission, OR
    - The designated staff role (if configured)
    """
    if member.guild_permissions.administrator:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if config and config.staff_role_id:
        return any(r.id == config.staff_role_id for r in member.roles)
    return False
