"""
cogs/mute.py — Mute/unmute commands.

Uses Discord's native timeout (Member.timeout) for durations ≤ 28 days.
Falls back to a custom Muted role for longer durations.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_timedelta
from utils.embeds import error_embed, success_embed
from utils.permissions import check_hierarchy, is_staff, staff_check

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.mute")


DISCORD_MAX_TIMEOUT_DAYS = 28
MUTED_ROLE_NAME = "Muted"



class Mute(commands.Cog, name="Mute"):
    """Mute and unmute commands."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    async def _get_or_create_muted_role(self, guild: discord.Guild) -> discord.Role:
        """Get or create a Muted role that denies sending messages everywhere."""
        muted_role = discord.utils.get(guild.roles, name=MUTED_ROLE_NAME)
        if muted_role is not None:
            log.debug("Found existing Muted role in guild %s", guild.id)
            return muted_role

        log.info("Creating Muted role in guild %s", guild.id)
        muted_role = await guild.create_role(
            name=MUTED_ROLE_NAME,
            colour=discord.Colour(0x808080),
            reason="mute role creation",
        )

        # Deny send messages in all text channels
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
                try:
                    await channel.set_permissions(
                        muted_role,
                        send_messages=False,
                        speak=False,
                        add_reactions=False,
                        reason="mute role setup",
                    )
                except discord.Forbidden:
                    pass

        return muted_role

    @app_commands.command(name="mute", description="Mute a member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        user="The member to mute.",
        duration="Duration e.g. 10m, 1h, 7d. Omit for indefinite.",
        reason="Reason for muting.",
    )
    @staff_check()
    async def mute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: Optional[str] = None,
        reason: str = "No reason provided",
    ):
        log.debug("/mute invoked by %s(%s) targeting %s(%s) duration=%r",
                  interaction.user, interaction.user.id, user, user.id, duration)
        await interaction.response.defer(ephemeral=True)

        # Hierarchy check
        hierarchy_err = await check_hierarchy(interaction.guild, interaction.guild.me, user)
        if hierarchy_err:
            await interaction.followup.send(embed=error_embed("Hierarchy Error", hierarchy_err), ephemeral=True)
            return

        td: Optional[timedelta] = None
        if duration:
            try:
                td = parse_duration(duration)
            except ValueError as e:
                await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)
                return

        try:
            if td is not None and td.days <= DISCORD_MAX_TIMEOUT_DAYS:
                # Use native Discord timeout
                until = discord.utils.utcnow() + td
                await user.timeout(until, reason=reason)
                method = "Discord Timeout"
            else:
                # Use custom Muted role
                muted_role = await self._get_or_create_muted_role(interaction.guild)
                await user.add_roles(muted_role, reason=reason)
                method = "Muted Role"

            duration_str = format_timedelta(td) if td else "Indefinite"
            log.info(
                "User muted | guild=%s user=%s(%s) method=%s duration=%s by %s(%s)",
                interaction.guild_id, user, user.id, method, duration_str,
                interaction.user, interaction.user.id,
            )

            embed = success_embed(
                "Member Muted",
                f"{user.mention} has been muted.\n"
                f"**Method:** {method}\n"
                f"**Duration:** {duration_str}\n"
                f"**Reason:** {reason}",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            logging_cog = self.bot.get_cog("Logging")
            if logging_cog:
                await logging_cog.log_mute(
                    interaction.guild, user, interaction.user, duration_str, method, reason
                )

        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Permission Error", "I do not have permission to mute this member."),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Error", str(e)), ephemeral=True)

    @app_commands.command(name="unmute", description="Unmute a member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(
        user="The member to unmute.",
        reason="Reason for unmuting.",
    )
    @staff_check()
    async def unmute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "Unmuted by moderator",
    ):
        log.debug("/unmute invoked by %s(%s) targeting %s(%s)",
                  interaction.user, interaction.user.id, user, user.id)
        await interaction.response.defer(ephemeral=True)

        unmuted_something = False

        # Remove native timeout if present
        if user.is_timed_out():
            try:
                await user.timeout(None, reason=reason)
                unmuted_something = True
            except discord.Forbidden:
                pass

        # Remove Muted role if present
        muted_role = discord.utils.get(interaction.guild.roles, name=MUTED_ROLE_NAME)
        if muted_role and muted_role in user.roles:
            try:
                await user.remove_roles(muted_role, reason=reason)
                unmuted_something = True
            except discord.Forbidden:
                pass

        if not unmuted_something:
            await interaction.followup.send(
                embed=error_embed("Not Muted", f"{user.mention} does not appear to be muted."),
                ephemeral=True,
            )
            return

        log.info(
            "User unmuted | guild=%s user=%s(%s) by %s(%s)",
            interaction.guild_id, user, user.id, interaction.user, interaction.user.id,
        )
        await interaction.followup.send(
            embed=success_embed("Member Unmuted", f"{user.mention} has been unmuted.\n**Reason:** {reason}"),
            ephemeral=True,
        )

        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_unmute(interaction.guild, user, interaction.user, reason)


async def setup(bot: "ModBot"):
    await bot.add_cog(Mute(bot))
