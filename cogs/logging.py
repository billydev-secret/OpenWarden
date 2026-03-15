"""
cogs/logging.py — Centralized mod-log dispatcher .
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import discord
from discord.ext import commands

from utils.embeds import (
    FOOTER_TEXT,
    jail_embed,
    unjail_embed,
    auto_unjail_embed,
    sentence_edit_embed,
    evasion_embed,
    success_embed,
    error_embed,
)

if TYPE_CHECKING:
    from bot import ModBot
    from models.sentence import Sentence


class Logging(commands.Cog):
    """Handles all mod-log channel output."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    # ── Internal helper ────────────────────────────────────────────────────────

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed) -> bool:
        """Send an embed to the guild's log channel. Returns True on success."""
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config.log_channel_id:
            return False

        channel = guild.get_channel(config.log_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            await channel.send(embed=embed)
            return True
        except discord.HTTPException:
            return False

    # ── Public logging methods ─────────────────────────────────────────────────

    async def log_jail(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: Optional[discord.Member | discord.User],
        reason: str,
        duration_str: str,
        release_at: Optional[str],
        sentence_id: int,
    ):
        embed = jail_embed(guild, user, moderator, reason, duration_str, release_at, sentence_id)
        await self._send_log(guild, embed)

    async def log_unjail(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: Optional[discord.Member | discord.User],
        reason: str,
        sentence_id: int,
    ):
        embed = unjail_embed(guild, user, moderator, reason, sentence_id)
        await self._send_log(guild, embed)

    async def log_auto_unjail(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        sentence: "Sentence",
    ):
        embed = auto_unjail_embed(guild, user, sentence, sentence.id)
        await self._send_log(guild, embed)

    async def log_sentence_edit(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: Optional[discord.Member | discord.User],
        old_duration: str,
        new_duration: str,
        reason: str,
        sentence_id: int,
    ):
        embed = sentence_edit_embed(
            guild, user, moderator, old_duration, new_duration, reason, sentence_id
        )
        await self._send_log(guild, embed)

    async def log_evasion(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        sentence: "Sentence",
    ):
        embed = evasion_embed(guild, user, sentence)
        await self._send_log(guild, embed)

    async def log_vote_jail(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        voters: list[int],
        sentence_id: int,
    ):
        embed = discord.Embed(
            title=f"🗳️ Vote Jail Succeeded — Case #{sentence_id}",
            description=(
                f"{user.mention} was jailed by community vote.\n"
                f"**{len(voters)}** member(s) voted."
            ),
            colour=discord.Colour(0x9B59B6),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Vote Count", value=str(len(voters)), inline=True)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_automod_jail(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        rule_id: str,
        duration_str: str,
        sentence_id: int,
    ):
        embed = discord.Embed(
            title=f"🤖 AutoMod Jail — Case #{sentence_id}",
            colour=discord.Colour(0xED4245),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="AutoMod Rule", value=rule_id, inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_appeal_open(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        sentence_id: int,
        channel_id: Optional[int],
    ):
        embed = discord.Embed(
            title="📋 Appeal Opened",
            colour=discord.Colour(0x5865F2),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Sentence ID", value=str(sentence_id), inline=True)
        if channel_id:
            embed.add_field(name="Thread", value=f"<#{channel_id}>", inline=True)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_appeal_close(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        outcome: str,
        staff_id: Optional[int],
        sentence_id: int,
    ):
        colour_map = {
            "accepted": discord.Colour(0x57F287),
            "denied": discord.Colour(0xED4245),
            "reduced": discord.Colour(0xFEE75C),
        }
        colour = colour_map.get(outcome.lower(), discord.Colour(0x95A5A6))
        embed = discord.Embed(
            title=f"📋 Appeal Closed — {outcome.capitalize()}",
            colour=colour,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Outcome", value=outcome.capitalize(), inline=True)
        embed.add_field(name="Sentence ID", value=str(sentence_id), inline=True)
        if staff_id:
            embed.add_field(name="Closed by", value=f"<@{staff_id}>", inline=True)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_mute(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: discord.User | discord.Member,
        duration_str: str,
        method: str,
        reason: str,
    ):
        embed = discord.Embed(
            title="🔇 Member Muted",
            colour=discord.Colour(0xFEE75C),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Method", value=method, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_unmute(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
        moderator: discord.User | discord.Member,
        reason: str,
    ):
        embed = discord.Embed(
            title="🔊 Member Unmuted",
            colour=discord.Colour(0x57F287),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Moderator", value=moderator.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=FOOTER_TEXT)
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed)

    async def log_event(self, guild: discord.Guild, event_type: str, **kwargs):
        """Generic dispatcher — calls the appropriate log method based on event_type."""
        method = getattr(self, f"log_{event_type}", None)
        if method:
            await method(guild, **kwargs)


async def setup(bot: "ModBot"):
    await bot.add_cog(Logging(bot))
