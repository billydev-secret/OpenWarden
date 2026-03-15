"""
cogs/appeals.py — Jail appeal system.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_timedelta
from utils.embeds import FOOTER_TEXT, appeal_embed, error_embed, success_embed
from utils.permissions import ensure_configured, is_staff, staff_check

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.appeals")


class Appeals(commands.Cog, name="Appeals"):
    """Appeal system for jailed members."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    @app_commands.command(name="appeal", description="Open an appeal for your jail sentence.")
    @app_commands.guild_only()
    async def appeal_open(self, interaction: discord.Interaction):
        log.debug("/appeal invoked by %s(%s) in guild=%s", interaction.user, interaction.user.id, interaction.guild_id)
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        err = await ensure_configured(config, ["jail_role_id", "appeal_channel_id"])
        if err:
            await interaction.followup.send(embed=error_embed("Not Configured", err), ephemeral=True)
            return

        # Must be jailed
        sentence = await self.bot.db.get_active_sentence(interaction.guild_id, interaction.user.id)
        if sentence is None:
            await interaction.followup.send(
                embed=error_embed("Not Jailed", "You do not have an active jail sentence to appeal."),
                ephemeral=True,
            )
            return

        # Check for existing open appeal
        existing_appeal = await self.bot.db.get_active_appeal(interaction.guild_id, interaction.user.id)
        if existing_appeal:
            channel_mention = f"<#{existing_appeal.channel_id}>" if existing_appeal.channel_id else "your appeal thread"
            await interaction.followup.send(
                embed=error_embed(
                    "Appeal Already Open",
                    f"You already have an open appeal. Check {channel_mention}.",
                ),
                ephemeral=True,
            )
            return

        # Fetch appeals channel
        appeal_channel = interaction.guild.get_channel(config.appeal_channel_id)
        if not isinstance(appeal_channel, discord.TextChannel):
            await interaction.followup.send(
                embed=error_embed("Channel Error", "The appeal channel is not a valid text channel."),
                ephemeral=True,
            )
            return

        # Create thread — prefer private (requires Community or Boost), fall back to public.
        thread: Optional[discord.Thread] = None
        thread_id: Optional[int] = None

        try:
            thread = await appeal_channel.create_thread(
                name=f"appeal-{interaction.user.name}-{sentence.id}",
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason=f"Appeal opened by {interaction.user}",
            )
            # Add appellant explicitly (private thread requires this)
            try:
                await thread.add_user(interaction.user)
            except discord.HTTPException:
                pass
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed(
                    "Permission Error",
                    "I cannot create threads in the appeal channel. "
                    "Ensure I have **Create Private Threads** and **Manage Threads** permissions.",
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            # Private threads unavailable (server not Community / insufficient boost) — use public thread.
            log.info("Private thread unavailable in guild=%s; falling back to public thread for appeal", interaction.guild_id)
            try:
                thread = await appeal_channel.create_thread(
                    name=f"appeal-{interaction.user.name}-{sentence.id}",
                    type=discord.ChannelType.public_thread,
                    reason=f"Appeal opened by {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    embed=error_embed(
                        "Permission Error",
                        "I cannot create threads in the appeal channel. "
                        "Ensure I have **Create Public Threads** permission.",
                    ),
                    ephemeral=True,
                )
                return
            except discord.HTTPException as e:
                await interaction.followup.send(
                    embed=error_embed("Error", f"Failed to create appeal thread: {e}"),
                    ephemeral=True,
                )
                return

        thread_id = thread.id

        # Post opening message
        try:
            opening_embed = appeal_embed(interaction.guild, interaction.user)
            opening_embed.add_field(name="Sentence ID", value=str(sentence.id), inline=True)
            opening_embed.add_field(name="Reason", value=sentence.reason, inline=False)
            if sentence.release_at:
                from datetime import datetime, timezone
                ts = int(datetime.fromisoformat(sentence.release_at).replace(tzinfo=timezone.utc).timestamp())
                opening_embed.add_field(name="Release Time", value=f"<t:{ts}:F>", inline=True)
            else:
                opening_embed.add_field(name="Release Time", value="Permanent", inline=True)

            await thread.send(
                content=f"{interaction.user.mention} — Please describe why you believe your sentence should be reduced or removed.",
                embed=opening_embed,
            )

            if config.staff_role_id:
                staff_role = interaction.guild.get_role(config.staff_role_id)
                if staff_role:
                    await thread.send(f"{staff_role.mention} — New appeal to review.")
        except discord.HTTPException:
            log.warning("Failed to post opening message in appeal thread %s", thread_id)

        # Save appeal to DB — if this fails, clean up the thread we just created
        try:
            appeal_id = await self.bot.db.insert_appeal(
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                sentence_id=sentence.id,
                channel_id=thread_id,
            )
        except Exception as e:
            log.exception("DB insert failed for appeal; deleting thread %s", thread_id)
            if thread:
                try:
                    await thread.delete()
                except discord.HTTPException:
                    pass
            await interaction.followup.send(
                embed=error_embed("Error", f"Failed to save appeal: {e}"),
                ephemeral=True,
            )
            return
        log.info(
            "Appeal opened | guild=%s user=%s(%s) sentence=#%s appeal=#%s thread=%s",
            interaction.guild_id, interaction.user, interaction.user.id,
            sentence.id, appeal_id, thread_id,
        )

        # Log appeal
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_appeal_open(
                interaction.guild, interaction.user, sentence.id, thread_id
            )

        thread_mention = thread.mention if thread else "your thread"
        await interaction.followup.send(
            embed=success_embed(
                "Appeal Opened",
                f"Your appeal has been opened at {thread_mention}.\n"
                "Staff will review it shortly.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="appeal-close", description="Close an appeal for a member (staff only).")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        user="The member whose appeal to close.",
        outcome="Outcome: accepted, denied, or reduced.",
        new_duration="If outcome is 'reduced', the new remaining duration e.g. 1d.",
    )
    @app_commands.choices(
        outcome=[
            app_commands.Choice(name="Accepted (release immediately)", value="accepted"),
            app_commands.Choice(name="Denied (keep sentence)", value="denied"),
            app_commands.Choice(name="Reduced (shorten sentence)", value="reduced"),
        ]
    )
    @staff_check()
    async def appeal_close(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        outcome: str,
        new_duration: Optional[str] = None,
    ):
        log.debug(
            "/appeal-close invoked by %s(%s) for %s(%s) outcome=%s",
            interaction.user, interaction.user.id, user, user.id, outcome,
        )
        await interaction.response.defer(ephemeral=True)

        appeal = await self.bot.db.get_active_appeal(interaction.guild_id, user.id)
        if appeal is None:
            await interaction.followup.send(
                embed=error_embed("No Open Appeal", f"{user.mention} does not have an open appeal."),
                ephemeral=True,
            )
            return

        sentence = await self.bot.db.get_sentence(appeal.sentence_id)

        # Validate "reduced" inputs BEFORE touching the database.
        new_release_at: Optional[str] = None
        result_description = ""
        if outcome == "accepted":
            result_description = f"{user.mention} has been released from jail."

        elif outcome == "reduced":
            if new_duration is None:
                await interaction.followup.send(
                    embed=error_embed("Duration Required", "Please provide `new_duration` when using 'reduced'."),
                    ephemeral=True,
                )
                return
            try:
                td = parse_duration(new_duration)
            except ValueError as e:
                await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)
                return
            if td:
                new_release_at = (discord.utils.utcnow() + td).isoformat()
            label = format_timedelta(td) if td else "Permanent"
            result_description = f"{user.mention}'s sentence has been reduced to **{label}**."

        else:  # denied
            result_description = f"{user.mention}'s appeal has been denied. The sentence stands."

        # All inputs valid — commit to DB now.
        await self.bot.db.close_appeal(appeal.id, outcome, interaction.user.id)

        if outcome == "accepted":
            if sentence:
                jail_cog = self.bot.get_cog("Jail")
                if jail_cog and user in interaction.guild.members:
                    await jail_cog._unjail_user(
                        guild=interaction.guild,
                        target=user,
                        sentence_id=sentence.id,
                        reason=f"Appeal accepted by {interaction.user}",
                        moderator_id=interaction.user.id,
                    )

        elif outcome == "reduced":
            if sentence:
                await self.bot.db.update_sentence_release(sentence.id, new_release_at)

        # Update the appeal thread
        if appeal.channel_id:
            thread = interaction.guild.get_channel(appeal.channel_id)
            if isinstance(thread, discord.Thread):
                outcome_embed = discord.Embed(
                    title=f"📋 Appeal {outcome.capitalize()}",
                    description=result_description,
                    colour=discord.Colour(0x57F287) if outcome == "accepted" else (
                        discord.Colour(0xFEE75C) if outcome == "reduced" else discord.Colour(0xED4245)
                    ),
                    timestamp=discord.utils.utcnow(),
                )
                outcome_embed.add_field(name="Closed by", value=interaction.user.mention, inline=True)
                outcome_embed.set_footer(text=FOOTER_TEXT)

                try:
                    await thread.send(embed=outcome_embed)
                    await thread.edit(archived=True, locked=True, reason=f"Appeal {outcome}")
                except discord.HTTPException:
                    pass

        log.info(
            "Appeal closed | guild=%s user=%s(%s) outcome=%s by %s(%s) sentence=#%s",
            interaction.guild_id, user, user.id, outcome,
            interaction.user, interaction.user.id, appeal.sentence_id,
        )

        # Log appeal close
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_appeal_close(
                interaction.guild, user, outcome, interaction.user.id,
                appeal.sentence_id,
            )

        await interaction.followup.send(
            embed=success_embed(
                f"Appeal {outcome.capitalize()}",
                result_description,
            ),
            ephemeral=True,
        )


async def setup(bot: "ModBot"):
    await bot.add_cog(Appeals(bot))
