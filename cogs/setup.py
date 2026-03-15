"""
cogs/setup.py — /jail setup and /jail config subcommands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_seconds
from utils.embeds import error_embed, success_embed
from utils.permissions import is_staff, staff_check

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.setup")



class SetupCog(commands.Cog, name="Setup"):
    """Server setup and configuration commands."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    # ── /jail setup ────────────────────────────────────────────────────────────

    @app_commands.command(name="setup", description="Automatically create jail roles and channels.")
    @app_commands.guild_only()
    @staff_check()
    async def jail_setup(self, interaction: discord.Interaction):
        log.info("/jail setup invoked by %s(%s) in guild=%s", interaction.user, interaction.user.id, interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        try:
            # 1. Create Jailed role
            jail_role = discord.utils.get(guild.roles, name="Jailed")
            if jail_role is None:
                log.debug("Creating Jailed role in guild %s", guild.id)
                jail_role = await guild.create_role(
                    name="Jailed",
                    colour=discord.Colour(0x808080),
                    reason="setup",
                )

            # 2. Deny View Channel for Jailed role in all text channels (parallel).
            # Collect jail category channels first so we can re-allow them after.
            jail_category_pre = discord.utils.get(guild.categories, name="🔒 Jail")
            jail_cat_id_pre = jail_category_pre.id if jail_category_pre else None

            async def _deny_channel(ch):
                # Skip channels already inside the jail category — we'll fix them below.
                if jail_cat_id_pre and ch.category_id == jail_cat_id_pre:
                    return
                try:
                    await ch.set_permissions(
                        jail_role,
                        view_channel=False,
                        reason="setup — deny jailed users",
                    )
                except discord.Forbidden:
                    pass

            target_channels = [
                ch for ch in guild.channels
                if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel))
            ]
            await asyncio.gather(*(_deny_channel(ch) for ch in target_channels))

            # 3. Create jail category
            jail_category = jail_category_pre
            if jail_category is None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    jail_role: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=False,
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        manage_messages=True,
                        create_private_threads=True,
                        manage_threads=True,
                    ),
                }
                jail_category = await guild.create_category(
                    "🔒 Jail",
                    overwrites=overwrites,
                    reason="setup",
                )
            else:
                # Ensure bot has thread permissions in existing category
                try:
                    await jail_category.set_permissions(
                        guild.me,
                        view_channel=True,
                        send_messages=True,
                        manage_channels=True,
                        manage_messages=True,
                        create_private_threads=True,
                        manage_threads=True,
                        reason="setup — update bot permissions",
                    )
                except discord.Forbidden:
                    pass

            # 4. Create jail-general channel
            jail_general = discord.utils.get(jail_category.channels, name="jail-general")
            if jail_general is None:
                jail_general = await jail_category.create_text_channel(
                    "jail-general",
                    topic="General channel for jailed members.",
                    reason="setup",
                )

            # 5. Create jail-appeals channel
            jail_appeals = discord.utils.get(jail_category.channels, name="jail-appeals")
            if jail_appeals is None:
                jail_appeals = await jail_category.create_text_channel(
                    "jail-appeals",
                    topic="Use /appeal to open an appeal thread here.",
                    reason="setup",
                )

            # 5b. Explicitly (re-)allow the jail role in jail channels.
            # This corrects any deny override that step 2 may have applied on a previous run.
            _jail_allow = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=False,
            )
            async def _allow_channel(ch):
                try:
                    await ch.set_permissions(jail_role, overwrite=_jail_allow, reason="setup — allow jailed users")
                except discord.Forbidden:
                    pass
            await asyncio.gather(
                _allow_channel(jail_general),
                _allow_channel(jail_appeals),
            )

            # 6. Save to database
            await self.bot.db.upsert_guild_config(
                guild.id,
                jail_role_id=jail_role.id,
                jail_category_id=jail_category.id,
                appeal_channel_id=jail_appeals.id,
            )

            log.info(
                "Setup complete | guild=%s jail_role=%s category=%s",
                guild.id, jail_role.id, jail_category.id,
            )

            embed = success_embed(
                "Setup Complete",
                f"**Jail role:** {jail_role.mention}\n"
                f"**Category:** {jail_category.name}\n"
                f"**General:** {jail_general.mention}\n"
                f"**Appeals:** {jail_appeals.mention}\n\n"
                f"Use `/jail config log-channel` to set a mod-log channel.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Missing Permissions", "I need **Manage Roles** and **Manage Channels** permissions."),
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                embed=error_embed("Setup Failed", str(e)),
                ephemeral=True,
            )

    # ── /jail config group ─────────────────────────────────────────────────────

    config_group = app_commands.Group(
        name="config",
        description="Configure bot settings.",
        guild_only=True,
    )

    @config_group.command(name="role", description="Set the jail role.")
    @app_commands.describe(role="The role to assign to jailed members.")
    @staff_check()
    async def config_role(self, interaction: discord.Interaction, role: discord.Role):
        log.debug("/jail config role: guild=%s role=%s by %s(%s)",
                  interaction.guild_id, role.id, interaction.user, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, jail_role_id=role.id)
        log.info("Config updated | guild=%s jail_role_id=%s", interaction.guild_id, role.id)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Jail role set to {role.mention}."),
            ephemeral=True,
        )

    @config_group.command(name="category", description="Set the jail category channel.")
    @app_commands.describe(category="The category for jail channels.")
    @staff_check()
    async def config_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, jail_category_id=category.id)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Jail category set to **{category.name}**."),
            ephemeral=True,
        )

    @config_group.command(name="log-channel", description="Set the mod-log channel.")
    @app_commands.describe(channel="The channel where mod actions will be logged.")
    @staff_check()
    async def config_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, log_channel_id=channel.id)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Log channel set to {channel.mention}."),
            ephemeral=True,
        )

    @config_group.command(name="appeal-channel", description="Set the appeals channel.")
    @app_commands.describe(channel="The channel where appeal threads will be created.")
    @staff_check()
    async def config_appeal_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, appeal_channel_id=channel.id)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Appeal channel set to {channel.mention}."),
            ephemeral=True,
        )

    @config_group.command(name="default-duration", description="Set the default jail duration.")
    @app_commands.describe(duration="Duration string e.g. 1d, 12h, 2w. Use 'permanent' for indefinite.")
    @staff_check()
    async def config_default_duration(self, interaction: discord.Interaction, duration: str):
        await interaction.response.defer(ephemeral=True)
        try:
            td = parse_duration(duration)
            seconds = int(td.total_seconds()) if td else 0
            await self.bot.db.upsert_guild_config(interaction.guild_id, default_duration=seconds)
            label = format_seconds(seconds) if seconds else "Permanent"
            await interaction.followup.send(
                embed=success_embed("Config Updated", f"Default duration set to **{label}**."),
                ephemeral=True,
            )
        except ValueError as e:
            await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)

    @config_group.command(name="vote-threshold", description="Set votes required to trigger a vote-jail (0 = disabled).")
    @app_commands.describe(threshold="Number of votes required. Set to 0 to disable vote-jail.")
    @staff_check()
    async def config_vote_threshold(self, interaction: discord.Interaction, threshold: int):
        await interaction.response.defer(ephemeral=True)
        if threshold < 0:
            await interaction.followup.send(
                embed=error_embed("Invalid Value", "Threshold must be 0 or greater."),
                ephemeral=True,
            )
            return
        await self.bot.db.upsert_guild_config(interaction.guild_id, vote_threshold=threshold)
        label = f"**{threshold}** votes" if threshold > 0 else "**Disabled**"
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Vote threshold set to {label}."),
            ephemeral=True,
        )

    @config_group.command(name="max-sentence", description="Set the maximum allowed sentence length.")
    @app_commands.describe(duration="Maximum duration e.g. 30d, 1w. Use 'permanent' for no limit.")
    @staff_check()
    async def config_max_sentence(self, interaction: discord.Interaction, duration: str):
        await interaction.response.defer(ephemeral=True)
        try:
            td = parse_duration(duration)
            seconds = int(td.total_seconds()) if td else 0
            await self.bot.db.upsert_guild_config(interaction.guild_id, max_sentence=seconds)
            label = format_seconds(seconds) if seconds else "No limit"
            await interaction.followup.send(
                embed=success_embed("Config Updated", f"Max sentence set to **{label}**."),
                ephemeral=True,
            )
        except ValueError as e:
            await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)

    @config_group.command(name="dm-on-jail", description="Toggle DM notification when a member is jailed.")
    @app_commands.describe(enabled="Whether to DM the member when jailed.")
    @staff_check()
    async def config_dm_on_jail(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, dm_on_jail=enabled)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"DM on jail set to **{'Enabled' if enabled else 'Disabled'}**."),
            ephemeral=True,
        )

    @config_group.command(name="dm-on-release", description="Toggle DM notification when a member is released.")
    @app_commands.describe(enabled="Whether to DM the member when released.")
    @staff_check()
    async def config_dm_on_release(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, dm_on_release=enabled)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"DM on release set to **{'Enabled' if enabled else 'Disabled'}**."),
            ephemeral=True,
        )

    @config_group.command(name="staff-role", description="Set the staff role (can use mod commands).")
    @app_commands.describe(role="Role that grants access to moderation commands.")
    @staff_check()
    async def config_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.upsert_guild_config(interaction.guild_id, staff_role_id=role.id)
        await interaction.followup.send(
            embed=success_embed("Config Updated", f"Staff role set to {role.mention}."),
            ephemeral=True,
        )


async def setup(bot: "ModBot"):
    cog = SetupCog(bot)
    # Register /jail setup and /jail config as subcommands of the /jail group
    # We will attach them to the JailGroup defined in jail.py, but since
    # that requires careful ordering, we add the cog and let jail.py handle grouping.
    await bot.add_cog(cog)
