"""
cogs/exclusions.py — Channel exclusions for jailed members.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.embeds import FOOTER_TEXT, error_embed, success_embed
from utils.permissions import ensure_configured, is_staff, staff_check

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.exclusions")



class Exclusions(commands.Cog, name="Exclusions"):
    """Manage channels that jailed members can still access."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    exclude_group = app_commands.Group(
        name="exclude",
        description="Manage channel exclusions for jailed members.",
        guild_only=True,
    )

    @exclude_group.command(name="add", description="Allow jailed members to access a channel.")
    @app_commands.describe(channel="The channel to allow jailed members to view.")
    @staff_check()
    async def exclude_add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        err = await ensure_configured(config, ["jail_role_id"])
        if err:
            await interaction.followup.send(embed=error_embed("Not Configured", err), ephemeral=True)
            return

        jail_role = interaction.guild.get_role(config.jail_role_id)
        if jail_role is None:
            await interaction.followup.send(
                embed=error_embed("Role Not Found", "The jail role could not be found."),
                ephemeral=True,
            )
            return

        # Check if already excluded
        exclusions = await self.bot.db.get_channel_exclusions(interaction.guild_id)
        if channel.id in exclusions:
            await interaction.followup.send(
                embed=error_embed("Already Excluded", f"{channel.mention} is already an exclusion."),
                ephemeral=True,
            )
            return

        # Update channel permissions
        try:
            await channel.set_permissions(
                jail_role,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason=f"Exclusion added by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Permission Error", "I cannot edit permissions on that channel."),
                ephemeral=True,
            )
            return

        await self.bot.db.add_channel_exclusion(interaction.guild_id, channel.id)
        log.info(
            "Exclusion added | guild=%s channel=%s(%s) by %s(%s)",
            interaction.guild_id, channel.name, channel.id, interaction.user, interaction.user.id,
        )
        await interaction.followup.send(
            embed=success_embed(
                "Exclusion Added",
                f"Jailed members can now access {channel.mention}.",
            ),
            ephemeral=True,
        )

    @exclude_group.command(name="remove", description="Remove a channel exclusion for jailed members.")
    @app_commands.describe(channel="The channel to remove from exclusions.")
    @staff_check()
    async def exclude_remove(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        err = await ensure_configured(config, ["jail_role_id"])
        if err:
            await interaction.followup.send(embed=error_embed("Not Configured", err), ephemeral=True)
            return

        exclusions = await self.bot.db.get_channel_exclusions(interaction.guild_id)
        if channel.id not in exclusions:
            await interaction.followup.send(
                embed=error_embed("Not Excluded", f"{channel.mention} is not in the exclusion list."),
                ephemeral=True,
            )
            return

        jail_role = interaction.guild.get_role(config.jail_role_id)
        if jail_role:
            try:
                await channel.set_permissions(
                    jail_role,
                    view_channel=False,
                    reason=f"Exclusion removed by {interaction.user}",
                )
            except discord.Forbidden:
                pass

        await self.bot.db.remove_channel_exclusion(interaction.guild_id, channel.id)
        log.info(
            "Exclusion removed | guild=%s channel=%s(%s) by %s(%s)",
            interaction.guild_id, channel.name, channel.id, interaction.user, interaction.user.id,
        )
        await interaction.followup.send(
            embed=success_embed(
                "Exclusion Removed",
                f"Jailed members can no longer access {channel.mention}.",
            ),
            ephemeral=True,
        )

    @exclude_group.command(name="list", description="List all channels jailed members can access.")
    @staff_check()
    async def exclude_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        exclusions = await self.bot.db.get_channel_exclusions(interaction.guild_id)
        if not exclusions:
            await interaction.followup.send(
                embed=success_embed("No Exclusions", "No channels are excluded from jail restrictions."),
                ephemeral=True,
            )
            return

        lines = []
        for channel_id in exclusions:
            channel = interaction.guild.get_channel(channel_id)
            if channel:
                lines.append(f"• {channel.mention} (`{channel_id}`)")
            else:
                lines.append(f"• `{channel_id}` (channel not found)")

        embed = discord.Embed(
            title="🔓 Channel Exclusions",
            description="\n".join(lines),
            colour=discord.Colour(0x5865F2),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: "ModBot"):
    cog = Exclusions(bot)
    await bot.add_cog(cog)
    # Add exclude subgroup to jail group
    jail_cog = bot.get_cog("Jail")
    if jail_cog and hasattr(jail_cog, "jail_group"):
        # add_cog auto-registered exclude_group as /exclude; remove then nest under /jail
        bot.tree.remove_command("exclude")
        if jail_cog.jail_group.get_command("exclude") is None:
            jail_cog.jail_group.add_command(cog.exclude_group)
