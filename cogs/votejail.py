"""
cogs/votejail.py — Community vote-to-jail feature.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import Lock
from datetime import timedelta
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_timedelta
from utils.embeds import FOOTER_TEXT, vote_jail_embed, error_embed, success_embed
from utils.permissions import is_staff

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.votejail")


VOTE_TIMEOUT = 3600  # 1 hour in seconds


class VoteView(discord.ui.View):
    """Persistent vote view for a single vote-jail session."""

    def __init__(
        self,
        bot: "ModBot",
        session_id: int,
        guild_id: int,
        target_user_id: int,
        initiator_id: int,
        threshold: int,
        reason: str,
        voters: Optional[list[int]] = None,
        *,
        timeout: float = VOTE_TIMEOUT,
    ):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.session_id = session_id
        self.guild_id = guild_id
        self.target_user_id = target_user_id
        self.initiator_id = initiator_id
        self.threshold = threshold
        self.reason = reason
        self.voters: list[int] = voters or []
        self._concluded = False
        self._lock = Lock()  # prevents concurrent button clicks racing on threshold

    async def _get_current_embed(self, guild: discord.Guild) -> discord.Embed:
        target = guild.get_member(self.target_user_id) or await self.bot.fetch_user(self.target_user_id)
        initiator = guild.get_member(self.initiator_id) or await self.bot.fetch_user(self.initiator_id)
        return vote_jail_embed(guild, target, initiator, len(self.voters), self.threshold, self.reason)

    async def _conclude(self, message: discord.Message, embed: discord.Embed, expired: bool = False):
        """Disable all buttons and mark the session as expired."""
        if self._concluded:
            return
        self._concluded = True

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await self.bot.db.expire_vote_session(self.session_id)

        # Remove from cog tracking
        votejail_cog: Optional["VoteJail"] = self.bot.get_cog("VoteJail")  # type: ignore[assignment]
        if votejail_cog and self.session_id in votejail_cog.active_views:
            del votejail_cog.active_views[self.session_id]

        try:
            await message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Vote to Jail",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="vote_jail_vote",
    )
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        async with self._lock:
            await self._handle_vote(interaction)

    async def _handle_vote(self, interaction: discord.Interaction):
        if self._concluded:
            await interaction.followup.send(
                embed=error_embed("Vote Ended", "This vote session has already ended."),
                ephemeral=True,
            )
            return

        voter_id = interaction.user.id

        # Cannot vote for yourself
        if voter_id == self.target_user_id:
            await interaction.followup.send(
                embed=error_embed("Cannot Vote", "You cannot vote to jail yourself."),
                ephemeral=True,
            )
            return

        # Cannot vote if you are a bot
        if interaction.user.bot:
            await interaction.followup.send(
                embed=error_embed("Cannot Vote", "Bots cannot vote."),
                ephemeral=True,
            )
            return

        if voter_id in self.voters:
            await interaction.followup.send(
                embed=error_embed("Already Voted", "You have already voted in this session."),
                ephemeral=True,
            )
            return

        self.voters.append(voter_id)
        await self.bot.db.update_vote_voters(self.session_id, self.voters)
        log.debug(
            "Vote cast | session=%s voter=%s(%s) count=%d/%d",
            self.session_id, interaction.user, voter_id, len(self.voters), self.threshold,
        )

        guild = interaction.guild
        new_embed = await self._get_current_embed(guild)

        if len(self.voters) >= self.threshold:
            log.info(
                "Vote threshold reached | session=%s target=%s guild=%s votes=%d",
                self.session_id, self.target_user_id, self.guild_id, len(self.voters),
            )
            # Vote succeeded — jail the target
            target = guild.get_member(self.target_user_id)
            if target:
                jail_cog = self.bot.get_cog("Jail")
                if jail_cog is None:
                    log.error("Vote threshold reached but Jail cog is not loaded — cannot jail %s", self.target_user_id)
                else:
                    try:
                        config = await self.bot.db.get_guild_config(guild.id)
                        duration: Optional[timedelta] = None
                        if config and config.default_duration:
                            duration = timedelta(seconds=config.default_duration)

                        sentence_id = await jail_cog._jail_user(
                            guild=guild,
                            target=target,
                            moderator=None,
                            duration=duration,
                            reason=self.reason,
                            source="vote",
                        )

                        # Log vote jail
                        logging_cog = self.bot.get_cog("Logging")
                        if logging_cog:
                            await logging_cog.log_vote_jail(guild, target, self.voters, sentence_id)

                        new_embed.title = "🗳️ Vote Succeeded — Member Jailed"
                        new_embed.colour = discord.Colour(0x57F287)
                        new_embed.description = (
                            f"{target.mention} has been jailed by community vote!\n"
                            f"**{len(self.voters)}/{self.threshold}** votes cast."
                        )
                    except Exception as e:
                        new_embed.description = f"Vote succeeded but jailing failed: {e}"

            await self._conclude(interaction.message, new_embed)
            await interaction.followup.send(
                embed=success_embed("Vote Cast", "Your vote was the deciding vote! The member has been jailed."),
                ephemeral=True,
            )
        else:
            try:
                await interaction.message.edit(embed=new_embed, view=self)
            except discord.HTTPException:
                pass
            await interaction.followup.send(
                embed=success_embed("Vote Cast", f"Your vote has been recorded. **{len(self.voters)}/{self.threshold}**"),
                ephemeral=True,
            )

    @discord.ui.button(
        label="Cancel Vote",
        style=discord.ButtonStyle.secondary,
        emoji="🚫",
        custom_id="vote_jail_cancel",
    )
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await self.bot.db.get_guild_config(interaction.guild_id)
        if not is_staff(interaction.user, config):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only staff can cancel vote sessions."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if self._concluded:
            await interaction.followup.send(
                embed=error_embed("Already Ended", "This vote session has already ended."),
                ephemeral=True,
            )
            return

        target = interaction.guild.get_member(self.target_user_id)
        target_str = target.mention if target else f"<@{self.target_user_id}>"

        cancel_embed = discord.Embed(
            title="🗳️ Vote Cancelled",
            description=f"The vote to jail {target_str} was cancelled by {interaction.user.mention}.",
            colour=discord.Colour(0x95A5A6),
            timestamp=discord.utils.utcnow(),
        )
        cancel_embed.set_footer(text=FOOTER_TEXT)

        log.info(
            "Vote cancelled | session=%s target=%s by %s(%s)",
            self.session_id, self.target_user_id, interaction.user, interaction.user.id,
        )
        await self._conclude(interaction.message, cancel_embed)
        await interaction.followup.send(
            embed=success_embed("Vote Cancelled", "The vote session has been cancelled."),
            ephemeral=True,
        )

    async def on_timeout(self):
        """Called when the view times out (1 hour)."""
        if self._concluded:
            return

        log.info(
            "Vote expired | session=%s target=%s guild=%s votes=%d/%d",
            self.session_id, self.target_user_id, self.guild_id, len(self.voters), self.threshold,
        )
        await self.bot.db.expire_vote_session(self.session_id)

        votejail_cog: Optional["VoteJail"] = self.bot.get_cog("VoteJail")  # type: ignore[assignment]
        if votejail_cog and self.session_id in votejail_cog.active_views:
            del votejail_cog.active_views[self.session_id]

        # Try to find the message and update it
        try:
            session = await self.bot.db.get_vote_session(self.session_id)
            if session:
                guild = self.bot.get_guild(self.guild_id)
                if guild:
                    channel = guild.get_channel(session.channel_id)
                    if isinstance(channel, discord.TextChannel):
                        message = await channel.fetch_message(session.message_id)
                        target = guild.get_member(self.target_user_id)
                        target_str = target.mention if target else f"<@{self.target_user_id}>"

                        timeout_embed = discord.Embed(
                            title="🗳️ Vote Expired",
                            description=f"The vote to jail {target_str} has expired. Not enough votes were cast.",
                            colour=discord.Colour(0x95A5A6),
                            timestamp=discord.utils.utcnow(),
                        )
                        timeout_embed.set_footer(text=FOOTER_TEXT)

                        for child in self.children:
                            if isinstance(child, discord.ui.Button):
                                child.disabled = True
                        await message.edit(embed=timeout_embed, view=self)
        except Exception:
            log.exception("Error updating expired vote message for session #%s", self.session_id)


class VoteJail(commands.Cog, name="VoteJail"):
    """Vote-to-jail feature."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot
        self.active_views: dict[int, VoteView] = {}

    async def restore_views(self):
        """Re-attach VoteView instances for all active sessions on startup."""
        restored = 0
        expired = 0
        for guild in self.bot.guilds:
            try:
                sessions = await self.bot.db.get_all_active_vote_sessions(guild.id)
                log.debug("Checking %d active vote session(s) in guild %s", len(sessions), guild.id)
                for session in sessions:
                    # Check if the session has expired based on time
                    from datetime import datetime, timezone
                    created = datetime.fromisoformat(session.created_at).replace(tzinfo=timezone.utc)
                    elapsed = (discord.utils.utcnow() - created).total_seconds()
                    remaining = VOTE_TIMEOUT - elapsed

                    if remaining <= 0:
                        log.debug("Vote session #%s expired while bot was offline — marking done", session.id)
                        await self.bot.db.expire_vote_session(session.id)
                        expired += 1
                        continue

                    view = VoteView(
                        bot=self.bot,
                        session_id=session.id,
                        guild_id=session.guild_id,
                        target_user_id=session.target_user_id,
                        initiator_id=session.initiator_id,
                        threshold=session.threshold,
                        reason="(restored)",
                        voters=session.voters,
                        timeout=remaining,
                    )
                    self.bot.add_view(view, message_id=session.message_id)
                    self.active_views[session.id] = view
                    restored += 1
            except Exception:
                log.exception("Error restoring vote sessions for guild %s", guild.id)
        log.info("Vote view restore complete: %d restored, %d expired offline", restored, expired)

    @app_commands.command(name="vote", description="Start a community vote to jail a member.")
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member to vote to jail.",
        reason="Reason for the vote.",
    )
    async def vote_jail(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ):
        log.debug(
            "/jail vote invoked by %s(%s) targeting %s(%s) guild=%s",
            interaction.user, interaction.user.id, user, user.id, interaction.guild_id,
        )
        await interaction.response.defer()

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        if not config or config.vote_threshold == 0:
            await interaction.followup.send(
                embed=error_embed("Disabled", "Vote-jail is not enabled on this server. Set a threshold with `/jail config vote-threshold`."),
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.followup.send(
                embed=error_embed("Cannot Vote", "You cannot vote to jail a bot."),
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.followup.send(
                embed=error_embed("Cannot Vote", "You cannot vote to jail yourself."),
                ephemeral=True,
            )
            return

        # Check for existing active sentence
        existing_sentence = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        if existing_sentence:
            await interaction.followup.send(
                embed=error_embed("Already Jailed", f"{user.mention} is already jailed."),
                ephemeral=True,
            )
            return

        # Check for existing active vote session
        existing_session = await self.bot.db.get_active_vote_session(interaction.guild_id, user.id)
        if existing_session:
            await interaction.followup.send(
                embed=error_embed(
                    "Vote Already Active",
                    f"There is already an active vote session for {user.mention}.",
                ),
                ephemeral=True,
            )
            return

        # Build and send vote embed
        embed = vote_jail_embed(
            interaction.guild, user, interaction.user, 0, config.vote_threshold, reason
        )

        # Create view (message_id placeholder — we'll update after send)
        temp_view = discord.ui.View(timeout=None)
        message = await interaction.followup.send(embed=embed, view=temp_view)

        if message is None:
            # followup.send returns the message object
            message = await interaction.original_response()

        # Save session to DB
        session_id = await self.bot.db.insert_vote_session(
            guild_id=interaction.guild_id,
            target_user_id=user.id,
            channel_id=interaction.channel_id,
            message_id=message.id,
            initiator_id=interaction.user.id,
            threshold=config.vote_threshold,
        )
        log.info(
            "Vote session started | guild=%s session=#%s target=%s(%s) threshold=%d by %s(%s)",
            interaction.guild_id, session_id, user, user.id, config.vote_threshold,
            interaction.user, interaction.user.id,
        )

        # Create the real view
        view = VoteView(
            bot=self.bot,
            session_id=session_id,
            guild_id=interaction.guild_id,
            target_user_id=user.id,
            initiator_id=interaction.user.id,
            threshold=config.vote_threshold,
            reason=reason,
        )

        self.active_views[session_id] = view
        self.bot.add_view(view, message_id=message.id)

        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


async def setup(bot: "ModBot"):
    cog = VoteJail(bot)
    await bot.add_cog(cog)
    # Add vote command to the jail group
    jail_cog = bot.get_cog("Jail")
    if jail_cog and hasattr(jail_cog, "jail_group"):
        # add_cog auto-registered vote_jail as /vote; remove then nest under /jail
        bot.tree.remove_command("vote")
        if jail_cog.jail_group.get_command("vote") is None:
            jail_cog.jail_group.add_command(cog.vote_jail)
