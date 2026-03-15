"""
cogs/help.py — /help command with a select-menu-driven reference.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.help")

# ── Colour palette (matches utils/embeds.py) ──────────────────────────────────
_BLUE   = discord.Colour(0x5865F2)
_RED    = discord.Colour(0xED4245)
_GREEN  = discord.Colour(0x57F287)
_YELLOW = discord.Colour(0xFEE75C)
_PURPLE = discord.Colour(0x9B59B6)
_ORANGE = discord.Colour(0xE67E22)
FOOTER  = "Use /jail setup to get started"


# ── One page per section ──────────────────────────────────────────────────────

def _page_overview() -> discord.Embed:
    e = discord.Embed(
        title="🔒 Help",
        description=(
            "A self-hosted moderation bot that isolates disruptive users into a "
            "restricted jail area. Jailed members keep their roles saved and get "
            "them back on release.\n\n"
            "Use the **select menu below** to browse command categories, or pick "
            "a section directly."
        ),
        colour=_BLUE,
        timestamp=discord.utils.utcnow(),
    )
    e.add_field(
        name="📋 Categories",
        value=(
            "🔧 **Setup** — first-time config\n"
            "⚙️ **Config** — adjust settings\n"
            "🔒 **Jail** — jailing members\n"
            "🗳️ **Vote** — community voting\n"
            "🤖 **AutoMod** — rule-triggered jails\n"
            "📜 **Appeals** — appeal system\n"
            "🔓 **Exclusions** — exempt channels\n"
            "🔇 **Mute** — timeout/mute commands"
        ),
        inline=False,
    )
    e.add_field(
        name="⚡ Quick Start",
        value=(
            "1. Invite the bot with **Manage Roles** + **Manage Channels**\n"
            "2. Run `/jail setup` — creates the jail role and channels\n"
            "3. Run `/jail config log-channel #your-mod-log`\n"
            "4. Done! Use `/jail add @user` to jail someone."
        ),
        inline=False,
    )
    e.set_footer(text=FOOTER)
    return e


def _page_setup() -> discord.Embed:
    e = discord.Embed(
        title="🔧 Setup",
        description="Commands for initial server configuration.",
        colour=_BLUE,
        timestamp=discord.utils.utcnow(),
    )
    e.add_field(
        name="`/jail setup`",
        value=(
            "Auto-creates everything needed:\n"
            "• A **Jailed** role (denies View Channel server-wide)\n"
            "• A **🔒 Jail** category with `#jail-general` and `#jail-appeals`\n"
            "• Saves config to the database\n\n"
            "_Requires: Manage Roles, Manage Channels_"
        ),
        inline=False,
    )
    e.set_footer(text=FOOTER)
    return e


def _page_config() -> discord.Embed:
    e = discord.Embed(
        title="⚙️ Config",
        description="Adjust settings after initial setup. All require **Manage Server**.",
        colour=_BLUE,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        ("`/jail config role <@role>`",           "Change which role is used as the jail role."),
        ("`/jail config category <#category>`",   "Change which category is used as the jail area."),
        ("`/jail config log-channel <#channel>`", "Set the mod-log channel for all jail events."),
        ("`/jail config appeal-channel <#ch>`",   "Set the channel where appeal threads are created."),
        ("`/jail config staff-role <@role>`",      "Grant a role access to all moderation commands."),
        ("`/jail config default-duration <dur>`", "Default sentence length when no duration is given (e.g. `1d`, `6h`)."),
        ("`/jail config max-sentence <dur>`",      "Maximum sentence any command can impose."),
        ("`/jail config vote-threshold <int>`",    "Votes needed to trigger a vote-jail. Set `0` to disable."),
        ("`/jail config dm-on-jail <true|false>`", "DM the member when they are jailed."),
        ("`/jail config dm-on-release <true|false>`", "DM the member when they are released."),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text=FOOTER)
    return e


def _page_jail() -> discord.Embed:
    e = discord.Embed(
        title="🔒 Jail Commands",
        description=(
            "Core jailing commands. All require **Moderate Members** or the configured staff role.\n\n"
            "**Duration format:** `2w3d`, `1d12h`, `6h30m`, `30m`, `permanent`"
        ),
        colour=_RED,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        (
            "`/jail add <@user> [duration] [reason]`",
            "Jail a member. Strips their roles (saved for restore), applies the jail role, "
            "logs the action, and optionally DMs them.",
        ),
        (
            "`/jail remove <@user> [reason]`",
            "Manually release a jailed member. Restores their role snapshot.",
        ),
        (
            "`/jail edit <@user> <new_duration> [reason]`",
            "Change the remaining duration of an active sentence.",
        ),
        (
            "`/jail info <@user>`",
            "Show a member's current jail status, reason, time remaining, and total sentence count.",
        ),
        (
            "`/jail list`",
            "Paginated list of everyone currently jailed in this server.",
        ),
        (
            "`/jail history <@user>`",
            "Full paginated sentence history for a member.",
        ),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.add_field(
        name="🛡️ Evasion Protection",
        value=(
            "If a jailed member leaves and rejoins, the jail role is **automatically re-applied** "
            "and the evasion is logged."
        ),
        inline=False,
    )
    e.set_footer(text=FOOTER)
    return e


def _page_vote() -> discord.Embed:
    e = discord.Embed(
        title="🗳️ Vote-Jail",
        description=(
            "Let the community vote to jail a member. "
            "Requires `vote-threshold` to be set to a value > 0 via `/jail config vote-threshold`."
        ),
        colour=_PURPLE,
        timestamp=discord.utils.utcnow(),
    )
    e.add_field(
        name="`/jail vote <@user> [reason]`",
        value=(
            "Starts a 1-hour vote session in the current channel.\n\n"
            "• Members click **Vote to Jail** — one vote per person, no self-votes\n"
            "• When votes reach the threshold, the member is automatically jailed "
            "for the server's default duration\n"
            "• Staff with **Manage Server** can cancel the vote at any time\n"
            "• The embed updates in real-time with the current count"
        ),
        inline=False,
    )
    e.set_footer(text=FOOTER)
    return e


def _page_automod() -> discord.Embed:
    e = discord.Embed(
        title="🤖 AutoMod Integration",
        description=(
            "Link Discord's native AutoMod rules to automatic jail sentences. "
            "When a linked rule fires, the offending member is jailed instantly.\n\n"
            "All subcommands require **Manage Server**."
        ),
        colour=_YELLOW,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        (
            "`/jail automod add <rule_id> <duration>`",
            "Link an AutoMod rule ID to a jail duration. Find the rule ID in "
            "Server Settings → AutoMod → copy the rule ID from the URL.",
        ),
        (
            "`/jail automod list`",
            "Show all linked AutoMod rules and their configured durations.",
        ),
        (
            "`/jail automod remove <rule_id>`",
            "Unlink an AutoMod rule so it no longer triggers automatic jailing.",
        ),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text=FOOTER)
    return e


def _page_appeals() -> discord.Embed:
    e = discord.Embed(
        title="📜 Appeal System",
        description=(
            "Jailed members can open a private appeal thread. "
            "Staff review it and close it with an outcome."
        ),
        colour=_GREEN,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        (
            "`/appeal`  _(jailed members only)_",
            "Opens a **private thread** in the appeals channel and pings the configured staff role. "
            "Only one active appeal per member at a time.",
        ),
        (
            "`/appeal-close <@user> <outcome> [new_duration]`  _(staff only)_",
            "Close an open appeal with one of three outcomes:\n"
            "• **accepted** — immediately releases the member\n"
            "• **denied** — sentence stands unchanged\n"
            "• **reduced** — shortens sentence to `new_duration`\n\n"
            "The appeal thread is archived and locked automatically.",
        ),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text=FOOTER)
    return e


def _page_exclusions() -> discord.Embed:
    e = discord.Embed(
        title="🔓 Channel Exclusions",
        description=(
            "Allow jailed members to see specific channels outside the jail area — "
            "useful for `#rules`, `#announcements`, etc. All require **Manage Server**."
        ),
        colour=_BLUE,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        (
            "`/jail exclude add <#channel>`",
            "Grant the jail role view access to a channel outside the jail category.",
        ),
        (
            "`/jail exclude remove <#channel>`",
            "Revoke the exemption and hide the channel from jailed members again.",
        ),
        (
            "`/jail exclude list`",
            "Show all currently excluded channels.",
        ),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text=FOOTER)
    return e


def _page_mute() -> discord.Embed:
    e = discord.Embed(
        title="🔇 Mute Commands",
        description=(
            "Consistent mute interface that automatically picks the best backend:\n"
            "• ≤ 28 days → Discord's native **timeout**\n"
            "• > 28 days → custom **Muted** role (auto-created, denies Send Messages)\n\n"
            "All require **Moderate Members** or the configured staff role."
        ),
        colour=_YELLOW,
        timestamp=discord.utils.utcnow(),
    )
    fields = [
        (
            "`/mute <@user> [duration] [reason]`",
            "Mute a member. Omit duration for indefinite.",
        ),
        (
            "`/unmute <@user> [reason]`",
            "Remove both a Discord timeout and the Muted role if present.",
        ),
    ]
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    e.set_footer(text=FOOTER)
    return e


# ── Section registry ──────────────────────────────────────────────────────────

SECTIONS: list[tuple[str, str, str, discord.Embed]] = [
    # (select label, emoji, description, embed)
    ("Overview",    "📖", "What this bot does and quick start",    _page_overview()),
    ("Setup",       "🔧", "Initial server configuration",         _page_setup()),
    ("Config",      "⚙️", "Adjust settings after setup",          _page_config()),
    ("Jail",        "🔒", "Jailing, releasing and sentence info",  _page_jail()),
    ("Vote",        "🗳️", "Community vote-to-jail",               _page_vote()),
    ("AutoMod",     "🤖", "Link AutoMod rules to auto-jails",     _page_automod()),
    ("Appeals",     "📜", "Appeal system for jailed members",     _page_appeals()),
    ("Exclusions",  "🔓", "Exempt channels from jail restrictions",_page_exclusions()),
    ("Mute",        "🔇", "Timeout and mute commands",            _page_mute()),
]


# ── View ──────────────────────────────────────────────────────────────────────

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, emoji=emoji, description=desc)
            for label, emoji, desc, _ in SECTIONS
        ]
        super().__init__(
            placeholder="Browse a command category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        for label, _, _, embed in SECTIONS:
            if label == chosen:
                await interaction.response.edit_message(embed=embed)
                return


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minutes
        self.add_item(HelpSelect())

    async def on_timeout(self):
        # Disable the select on timeout
        for item in self.children:
            item.disabled = True


# ── Cog ───────────────────────────────────────────────────────────────────────

class HelpCog(commands.Cog, name="Help"):
    """Help command."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    @app_commands.command(name="help", description="Browse commands and features.")
    @app_commands.guild_only()
    @app_commands.describe(section="Jump directly to a specific section.")
    @app_commands.choices(section=[
        app_commands.Choice(name=f"{emoji} {label}", value=label)
        for label, emoji, _, _ in SECTIONS
    ])
    async def help_command(
        self,
        interaction: discord.Interaction,
        section: str | None = None,
    ):
        log.debug("/help invoked by %s(%s) section=%r", interaction.user, interaction.user.id, section)

        # Find the requested section, default to overview
        embed = SECTIONS[0][3]  # Overview
        if section:
            for label, _, _, page_embed in SECTIONS:
                if label == section:
                    embed = page_embed
                    break

        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: "ModBot"):
    await bot.add_cog(HelpCog(bot))
