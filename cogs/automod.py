"""
cogs/automod.py — AutoMod integration .
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_seconds
from utils.embeds import FOOTER_TEXT, error_embed, success_embed
from utils.permissions import is_staff, staff_check

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.automod")



class AutoMod(commands.Cog, name="AutoMod"):
    """AutoMod rule to jail sentence mapping."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    automod_group = app_commands.Group(
        name="automod",
        description="Manage AutoMod rule → jail mappings.",
        guild_only=True,
    )

    @automod_group.command(name="add", description="Link an AutoMod rule to a jail duration.")
    @app_commands.describe(
        rule_id="The AutoMod rule ID to link.",
        duration="Jail duration when this rule triggers e.g. 1h, 2d.",
    )
    @staff_check()
    async def automod_add(self, interaction: discord.Interaction, rule_id: str, duration: str):
        log.debug("/jail automod add: guild=%s rule_id=%s duration=%r by %s(%s)",
                  interaction.guild_id, rule_id, duration, interaction.user, interaction.user.id)
        await interaction.response.defer(ephemeral=True)

        try:
            td = parse_duration(duration)
        except ValueError as e:
            await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)
            return

        seconds = int(td.total_seconds()) if td else 0
        await self.bot.db.insert_automod_rule(interaction.guild_id, rule_id, seconds)
        label = format_seconds(seconds) if seconds else "Permanent"
        log.info("AutoMod rule linked | guild=%s rule=%s duration=%s", interaction.guild_id, rule_id, label)

        await interaction.followup.send(
            embed=success_embed(
                "AutoMod Rule Added",
                f"Rule `{rule_id}` will now trigger a **{label}** jail sentence.",
            ),
            ephemeral=True,
        )

    @automod_group.command(name="list", description="List all AutoMod rule → jail mappings.")
    @staff_check()
    async def automod_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        rules = await self.bot.db.list_automod_rules(interaction.guild_id)
        if not rules:
            await interaction.followup.send(
                embed=success_embed("No Rules", "No AutoMod rules are linked to jail sentences."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🤖 AutoMod Jail Rules",
            colour=discord.Colour(0x5865F2),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=FOOTER_TEXT)

        for rule in rules:
            label = format_seconds(rule.duration) if rule.duration else "Permanent"
            embed.add_field(name=f"Rule `{rule.rule_id}`", value=f"Duration: **{label}**", inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @automod_group.command(name="remove", description="Remove an AutoMod rule → jail mapping.")
    @app_commands.describe(rule_id="The AutoMod rule ID to unlink.")
    @staff_check()
    async def automod_remove(self, interaction: discord.Interaction, rule_id: str):
        await interaction.response.defer(ephemeral=True)

        existing = await self.bot.db.get_automod_rule(interaction.guild_id, rule_id)
        if not existing:
            await interaction.followup.send(
                embed=error_embed("Not Found", f"No jail mapping found for rule `{rule_id}`."),
                ephemeral=True,
            )
            return

        await self.bot.db.delete_automod_rule(interaction.guild_id, rule_id)
        log.info("AutoMod rule unlinked | guild=%s rule=%s by %s(%s)",
                 interaction.guild_id, rule_id, interaction.user, interaction.user.id)
        await interaction.followup.send(
            embed=success_embed("Rule Removed", f"Rule `{rule_id}` has been unlinked."),
            ephemeral=True,
        )

    # ── AutoMod event listener ─────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        """Handle AutoMod actions and jail members if a matching rule is configured."""
        log.debug("on_automod_action: guild=%s rule=%s user=%s", execution.guild_id, execution.rule_id, execution.user_id)
        try:
            if execution.guild_id is None:
                return

            rule = await self.bot.db.get_automod_rule(execution.guild_id, str(execution.rule_id))
            if rule is None:
                log.debug("No jail mapping for automod rule %s in guild %s", execution.rule_id, execution.guild_id)
                return

            guild = self.bot.get_guild(execution.guild_id)
            if guild is None:
                return

            config = await self.bot.db.get_guild_config(guild.id)
            if not config or not config.jail_role_id:
                return

            member = guild.get_member(execution.user_id)
            if member is None:
                return

            # Skip if already jailed
            existing = await self.bot.db.get_active_sentence(guild.id, member.id)
            if existing:
                return

            duration: Optional[timedelta] = timedelta(seconds=rule.duration) if rule.duration else None
            duration_str = format_seconds(rule.duration) if rule.duration else "Permanent"

            jail_cog = self.bot.get_cog("Jail")
            if jail_cog is None:
                log.error("Jail cog not loaded — cannot process automod jail for rule %s in guild %s", execution.rule_id, execution.guild_id)
                return

            log.info(
                "AutoMod trigger | guild=%s rule=%s user=%s(%s) duration=%s",
                guild.id, execution.rule_id, member, member.id, duration_str,
            )
            sentence_id = await jail_cog._jail_user(
                guild=guild,
                target=member,
                moderator=None,
                duration=duration,
                reason=f"AutoMod rule triggered (ID: {execution.rule_id})",
                source="automod",
            )

            logging_cog = self.bot.get_cog("Logging")
            if logging_cog:
                await logging_cog.log_automod_jail(
                    guild, member, str(execution.rule_id), duration_str, sentence_id
                )

        except Exception:
            log.exception("Error handling automod action rule=%s guild=%s", execution.rule_id, execution.guild_id)


async def setup(bot: "ModBot"):
    cog = AutoMod(bot)
    await bot.add_cog(cog)
    # Add automod subgroup to jail group
    jail_cog = bot.get_cog("Jail")
    if jail_cog and hasattr(jail_cog, "jail_group"):
        # add_cog auto-registered automod_group as /automod; remove then nest under /jail
        bot.tree.remove_command("automod")
        if jail_cog.jail_group.get_command("automod") is None:
            jail_cog.jail_group.add_command(cog.automod_group)
