"""
utils/embeds.py — Rich embed factory functions .
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from models.sentence import Sentence

# ── Colour palette ─────────────────────────────────────────────────────────────
COLOUR_RED    = discord.Colour(0xED4245)   # jail / error
COLOUR_GREEN  = discord.Colour(0x57F287)   # unjail / success
COLOUR_YELLOW = discord.Colour(0xFEE75C)   # edit / warning
COLOUR_ORANGE = discord.Colour(0xE67E22)   # evasion
COLOUR_PURPLE = discord.Colour(0x9B59B6)   # vote
COLOUR_BLUE   = discord.Colour(0x5865F2)   # info / neutral

FOOTER_TEXT = ""


def _now() -> datetime:
    return discord.utils.utcnow()


def _base(title: str, colour: discord.Colour, description: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, colour=colour, timestamp=_now())
    embed.set_footer(text=FOOTER_TEXT)
    return embed


# ── Moderation log embeds ──────────────────────────────────────────────────────

def jail_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    moderator: discord.Member | discord.User | None,
    reason: str,
    duration_str: str,
    release_at: Optional[str],
    case_id: int,
) -> discord.Embed:
    embed = _base(f"🔒 Member Jailed — Case #{case_id}", COLOUR_RED)
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    mod_value = moderator.mention if moderator else "AutoMod"
    embed.add_field(name="Moderator", value=mod_value, inline=True)
    embed.add_field(name="Duration", value=duration_str, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if release_at:
        ts = int(datetime.fromisoformat(release_at).replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Release", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    else:
        embed.add_field(name="Release", value="Permanent", inline=False)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def unjail_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    moderator: discord.Member | discord.User | None,
    reason: str,
    case_id: int,
) -> discord.Embed:
    embed = _base(f"🔓 Member Released — Case #{case_id}", COLOUR_GREEN)
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    mod_value = moderator.mention if moderator else "System"
    embed.add_field(name="Released by", value=mod_value, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def auto_unjail_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    sentence: "Sentence",
    case_id: int,
) -> discord.Embed:
    embed = _base(f"🔓 Sentence Completed — Case #{case_id}", COLOUR_GREEN)
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    embed.add_field(name="Released by", value="Scheduler (auto)", inline=True)
    embed.add_field(name="Original Reason", value=sentence.reason, inline=False)
    if sentence.jailed_at:
        ts = int(datetime.fromisoformat(sentence.jailed_at).replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Jailed At", value=f"<t:{ts}:F>", inline=True)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def sentence_edit_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    moderator: discord.Member | discord.User | None,
    old_duration: str,
    new_duration: str,
    reason: str,
    case_id: int,
) -> discord.Embed:
    embed = _base(f"✏️ Sentence Edited — Case #{case_id}", COLOUR_YELLOW)
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    mod_value = moderator.mention if moderator else "System"
    embed.add_field(name="Moderator", value=mod_value, inline=True)
    embed.add_field(name="Old Duration", value=old_duration, inline=True)
    embed.add_field(name="New Duration", value=new_duration, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def evasion_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    sentence: "Sentence",
) -> discord.Embed:
    embed = _base("⚠️ Jail Evasion Detected", COLOUR_ORANGE)
    embed.description = (
        f"{user.mention} left the server and rejoined while serving a sentence. "
        "The jail role has been re-applied."
    )
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    embed.add_field(name="Sentence ID", value=str(sentence.id), inline=True)
    if sentence.release_at:
        ts = int(datetime.fromisoformat(sentence.release_at).replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Release", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    else:
        embed.add_field(name="Release", value="Permanent", inline=False)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


def vote_jail_embed(
    guild: discord.Guild,
    target: discord.Member | discord.User,
    initiator: discord.Member | discord.User,
    vote_count: int,
    threshold: int,
    reason: str,
) -> discord.Embed:
    progress = "█" * vote_count + "░" * max(0, threshold - vote_count)
    embed = _base("🗳️ Vote to Jail", COLOUR_PURPLE)
    embed.description = (
        f"A vote has been started to jail {target.mention}.\n"
        f"**{vote_count}/{threshold}** votes required.\n\n"
        f"`{progress}`"
    )
    embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Started by", value=initiator.mention, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)
    return embed


def info_embed(
    guild: discord.Guild,
    user: discord.User | discord.Member,
    sentence: Optional["Sentence"],
    history_count: int,
) -> discord.Embed:
    if sentence:
        embed = _base(f"🔒 Jail Info — {user.display_name}", COLOUR_RED)
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Case ID", value=str(sentence.id), inline=True)
        embed.add_field(name="Source", value=sentence.source, inline=True)
        embed.add_field(name="Reason", value=sentence.reason, inline=False)
        if sentence.jailed_at:
            ts = int(datetime.fromisoformat(sentence.jailed_at).replace(tzinfo=timezone.utc).timestamp())
            embed.add_field(name="Jailed At", value=f"<t:{ts}:F>", inline=True)
        if sentence.release_at:
            ts = int(datetime.fromisoformat(sentence.release_at).replace(tzinfo=timezone.utc).timestamp())
            embed.add_field(name="Release", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=True)
        else:
            embed.add_field(name="Release", value="Permanent", inline=True)
        embed.add_field(name="Total Sentences", value=str(history_count), inline=True)
    else:
        embed = _base(f"✅ Not Jailed — {user.display_name}", COLOUR_GREEN)
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Status", value="Not currently jailed", inline=True)
        embed.add_field(name="Total Sentences", value=str(history_count), inline=True)

    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed


# ── Utility embeds ─────────────────────────────────────────────────────────────

def error_embed(title: str, description: str) -> discord.Embed:
    embed = _base(f"❌ {title}", COLOUR_RED, description)
    return embed


def success_embed(title: str, description: str) -> discord.Embed:
    embed = _base(f"✅ {title}", COLOUR_GREEN, description)
    return embed


# ── DM embeds ─────────────────────────────────────────────────────────────────

def jail_dm_embed(
    guild: discord.Guild,
    reason: str,
    duration_str: str,
    release_at: Optional[str],
) -> discord.Embed:
    embed = _base(f"🔒 You have been jailed in {guild.name}", COLOUR_RED)
    embed.description = (
        "You have been placed in jail. "
        "You will not be able to access most channels until your sentence is complete."
    )
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    embed.add_field(name="Duration", value=duration_str, inline=True)
    if release_at:
        ts = int(datetime.fromisoformat(release_at).replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="Release", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=True)
    else:
        embed.add_field(name="Release", value="Permanent (appeal may be available)", inline=True)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def release_dm_embed(guild: discord.Guild, reason: str) -> discord.Embed:
    embed = _base(f"🔓 You have been released in {guild.name}", COLOUR_GREEN)
    embed.description = "Your jail sentence has ended. You now have access to the server again."
    embed.add_field(name="Reason / Note", value=reason or "Sentence completed", inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def appeal_embed(guild: discord.Guild, user: discord.User | discord.Member) -> discord.Embed:
    embed = _base("📋 Appeal Opened", COLOUR_BLUE)
    embed.description = (
        f"An appeal has been opened for {user.mention}. "
        "Staff will review this appeal shortly."
    )
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=True)
    if user.display_avatar:
        embed.set_thumbnail(url=user.display_avatar.url)
    return embed
