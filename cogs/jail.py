"""
cogs/jail.py — Core jail/unjail commands and evasion protection.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from utils.duration import parse_duration, format_seconds, format_timedelta
from utils.embeds import (
    FOOTER_TEXT,
    error_embed,
    success_embed,
    info_embed,
    jail_dm_embed,
    release_dm_embed,
    evasion_embed,
)
from utils.permissions import check_hierarchy, ensure_configured, is_staff, staff_check
from utils.pagination import PaginatedView

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.jail")


class Jail(commands.Cog, name="Jail"):
    """Core jail commands and evasion protection."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _jail_user(
        self,
        guild: discord.Guild,
        target: discord.Member,
        moderator: Optional[discord.Member | discord.User],
        duration: Optional[timedelta],
        reason: str,
        source: str = "manual",
    ) -> int:
        """
        Apply the jail role, take a role snapshot, insert a sentence record,
        send DMs if configured, and log the action.

        Returns the sentence_id.
        """
        log.debug(
            "_jail_user: guild=%s target=%s(%s) source=%s duration=%s",
            guild.id, target, target.id, source, duration,
        )
        config = await self.bot.db.get_guild_config(guild.id)

        jail_role = guild.get_role(config.jail_role_id) if config and config.jail_role_id else None
        if jail_role is None:
            raise ValueError("Jail role not found. Run `/jail setup` or `/jail config role`.")

        # Cap duration to max_sentence
        if duration is not None and config and config.max_sentence:
            max_td = timedelta(seconds=config.max_sentence)
            if duration > max_td:
                log.debug("Duration capped from %s to max_sentence %ss", duration, config.max_sentence)
                duration = max_td

        # Snapshot all assignable roles (exclude @everyone, jail role, and Discord-managed roles
        # like Nitro Boost which cannot be manually removed or restored)
        roles_to_strip = [
            r for r in target.roles
            if r != guild.default_role and r != jail_role and not r.managed
        ]
        snapshot_ids = [r.id for r in roles_to_strip]
        role_snapshot = json.dumps(snapshot_ids)
        log.debug("Role snapshot for %s: %s", target.id, snapshot_ids)

        # Compute release time
        release_at: Optional[str] = None
        if duration is not None:
            release_at = (discord.utils.utcnow() + duration).isoformat()

        sentence_id = await self.bot.db.insert_sentence(
            guild_id=guild.id,
            user_id=target.id,
            moderator_id=moderator.id if moderator else None,
            reason=reason,
            release_at=release_at,
            source=source,
            role_snapshot=role_snapshot,
        )

        # Set the member's entire role list in one PATCH request:
        # keep only managed roles (e.g. Nitro Boost) + the jail role.
        managed_roles = [r for r in target.roles if r.managed]
        try:
            await target.edit(roles=managed_roles + [jail_role], reason=f"Jailed — {reason}")
            log.debug(
                "Roles set via edit() for %s(%s): stripped %d, applied jail role",
                target, target.id, len(roles_to_strip),
            )
        except discord.Forbidden:
            log.warning("Missing permissions to modify roles for %s(%s)", target, target.id)

        # Send DM (fire-and-forget, don't block the jail flow)
        async def _send_jail_dm():
            if config and config.dm_on_jail:
                duration_str = format_timedelta(duration) if duration else "Permanent"
                dm_embed = jail_dm_embed(guild, reason, duration_str, release_at)
                try:
                    await target.send(embed=dm_embed)
                    log.debug("Jail DM sent to %s(%s)", target, target.id)
                except discord.HTTPException:
                    log.debug("Could not DM %s(%s) — DMs likely disabled", target, target.id)

        await _send_jail_dm()

        log.info(
            "User jailed | guild=%s user=%s(%s) sentence=#%s source=%s duration=%s reason=%r",
            guild.id, target, target.id, sentence_id, source,
            format_timedelta(duration) if duration else "permanent", reason,
        )

        # Log
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            duration_str = format_timedelta(duration) if duration else "Permanent"
            await logging_cog.log_jail(
                guild, target, moderator, reason, duration_str, release_at, sentence_id
            )

        return sentence_id

    async def _unjail_user(
        self,
        guild: discord.Guild,
        target: discord.Member,
        sentence_id: int,
        reason: str,
        moderator_id: Optional[int] = None,
    ):
        """
        Remove the jail role, restore role snapshot, mark sentence released,
        send release DM, and log the action.
        """
        log.debug(
            "_unjail_user: guild=%s target=%s(%s) sentence=#%s reason=%r",
            guild.id, target, target.id, sentence_id, reason,
        )
        config = await self.bot.db.get_guild_config(guild.id)
        sentence = await self.bot.db.get_sentence(sentence_id)

        jail_role = guild.get_role(config.jail_role_id) if config and config.jail_role_id else None

        # Restore snapshot roles + drop jail role in one PATCH request
        roles_to_restore: list[discord.Role] = []
        if sentence and sentence.role_snapshot:
            try:
                snapshot_ids = json.loads(sentence.role_snapshot)
                roles_to_restore = [r for rid in snapshot_ids if (r := guild.get_role(rid)) is not None]
                skipped = len(snapshot_ids) - len(roles_to_restore)
                if skipped:
                    log.debug("%d snapshot role(s) no longer exist for %s(%s), skipping", skipped, target, target.id)
            except json.JSONDecodeError as exc:
                log.warning("Bad role snapshot for sentence #%s: %s", sentence_id, exc)

        # Keep any managed roles the member currently has, add back the snapshot
        managed_roles = [r for r in target.roles if r.managed]
        new_roles = managed_roles + roles_to_restore
        try:
            await target.edit(roles=new_roles, reason=f"Released — {reason}")
            log.debug(
                "Roles restored via edit() for %s(%s): %d role(s) reinstated",
                target, target.id, len(roles_to_restore),
            )
        except discord.Forbidden:
            log.warning("Missing permissions to restore roles for %s(%s)", target, target.id)

        await self.bot.db.release_sentence(sentence_id)

        # Send DM
        if config and config.dm_on_release:
            dm_embed = release_dm_embed(guild, reason)
            try:
                await target.send(embed=dm_embed)
                log.debug("Release DM sent to %s(%s)", target, target.id)
            except discord.HTTPException:
                log.debug("Could not DM %s(%s) on release — DMs likely disabled", target, target.id)

        log.info(
            "User unjailed | guild=%s user=%s(%s) sentence=#%s reason=%r",
            guild.id, target, target.id, sentence_id, reason,
        )

        # Log
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            moderator = guild.get_member(moderator_id) if moderator_id else None
            await logging_cog.log_unjail(guild, target, moderator, reason, sentence_id)

    # ── /jail add ──────────────────────────────────────────────────────────────

    jail_group = app_commands.Group(
        name="jail",
        description="Jail management commands.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @jail_group.command(name="add", description="Jail a member.")
    @app_commands.describe(
        user="The member to jail.",
        duration="Duration e.g. 1d, 6h30m, 2w. Leave blank for default. Use 'permanent' for indefinite.",
        reason="Reason for jailing.",
    )
    @staff_check()
    async def jail_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: Optional[str] = None,
        reason: str = "No reason provided",
    ):
        log.debug(
            "/jail add invoked by %s(%s) targeting %s(%s) duration=%r reason=%r",
            interaction.user, interaction.user.id, user, user.id, duration, reason,
        )
        await interaction.response.defer()

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        err = await ensure_configured(config, ["jail_role_id"])
        if err:
            await interaction.followup.send(embed=error_embed("Not Configured", err), ephemeral=True)
            return

        # Check if already jailed
        existing = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        if existing:
            await interaction.followup.send(
                embed=error_embed("Already Jailed", f"{user.mention} is already jailed (Case #{existing.id})."),
                ephemeral=True,
            )
            return

        # Hierarchy check
        bot_member = interaction.guild.me
        jail_role = interaction.guild.get_role(config.jail_role_id)
        hierarchy_err = await check_hierarchy(interaction.guild, bot_member, user, jail_role, invoker=interaction.user)
        if hierarchy_err:
            await interaction.followup.send(embed=error_embed("Hierarchy Error", hierarchy_err), ephemeral=True)
            return

        # Parse duration
        td: Optional[timedelta] = None
        if duration:
            try:
                td = parse_duration(duration)
            except ValueError as e:
                await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)
                return
        else:
            if config.default_duration:
                td = timedelta(seconds=config.default_duration)

        try:
            sentence_id = await self._jail_user(
                guild=interaction.guild,
                target=user,
                moderator=interaction.user,
                duration=td,
                reason=reason,
                source="manual",
            )

            duration_str = format_timedelta(td) if td else "Permanent"
            await interaction.followup.send(
                embed=success_embed(
                    f"Member Jailed — Case #{sentence_id}",
                    f"{user.mention} has been jailed.\n"
                    f"**Duration:** {duration_str}\n"
                    f"**Reason:** {reason}",
                )
            )
        except ValueError as e:
            await interaction.followup.send(embed=error_embed("Error", str(e)), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Unexpected Error", str(e)), ephemeral=True)

    # ── /jail remove ───────────────────────────────────────────────────────────

    @jail_group.command(name="remove", description="Release a jailed member.")
    @app_commands.describe(
        user="The member to release.",
        reason="Reason for releasing.",
    )
    @staff_check()
    async def jail_remove(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "Released by moderator",
    ):
        log.debug(
            "/jail remove invoked by %s(%s) targeting %s(%s)",
            interaction.user, interaction.user.id, user, user.id,
        )
        await interaction.response.defer()

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        err = await ensure_configured(config, ["jail_role_id"])
        if err:
            await interaction.followup.send(embed=error_embed("Not Configured", err), ephemeral=True)
            return

        sentence = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        if sentence is None:
            await interaction.followup.send(
                embed=error_embed("Not Jailed", f"{user.mention} does not have an active jail sentence."),
                ephemeral=True,
            )
            return

        try:
            await self._unjail_user(
                guild=interaction.guild,
                target=user,
                sentence_id=sentence.id,
                reason=reason,
                moderator_id=interaction.user.id,
            )
            await interaction.followup.send(
                embed=success_embed(
                    f"Member Released — Case #{sentence.id}",
                    f"{user.mention} has been released from jail.\n**Reason:** {reason}",
                )
            )
        except Exception as e:
            await interaction.followup.send(embed=error_embed("Unexpected Error", str(e)), ephemeral=True)

    # ── /jail edit ─────────────────────────────────────────────────────────────

    @jail_group.command(name="edit", description="Edit the remaining duration of a jail sentence.")
    @app_commands.describe(
        user="The jailed member.",
        new_duration="New duration from now e.g. 2d, 1h. Use 'permanent' for indefinite.",
        reason="Reason for editing.",
    )
    @staff_check()
    async def jail_edit(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        new_duration: str,
        reason: str = "Sentence edited by moderator",
    ):
        log.debug(
            "/jail edit invoked by %s(%s) targeting %s(%s) new_duration=%r",
            interaction.user, interaction.user.id, user, user.id, new_duration,
        )
        await interaction.response.defer()

        sentence = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        if sentence is None:
            await interaction.followup.send(
                embed=error_embed("Not Jailed", f"{user.mention} does not have an active jail sentence."),
                ephemeral=True,
            )
            return

        config = await self.bot.db.get_guild_config(interaction.guild_id)

        # Compute old duration label
        if sentence.release_at:
            old_release = datetime.fromisoformat(sentence.release_at).replace(tzinfo=timezone.utc)
            old_remaining = old_release - discord.utils.utcnow()
            old_label = format_timedelta(old_remaining) if old_remaining.total_seconds() > 0 else "Expired"
        else:
            old_label = "Permanent"

        try:
            td = parse_duration(new_duration)
        except ValueError as e:
            await interaction.followup.send(embed=error_embed("Invalid Duration", str(e)), ephemeral=True)
            return

        # Cap to max
        if td is not None and config and config.max_sentence:
            max_td = timedelta(seconds=config.max_sentence)
            if td > max_td:
                td = max_td

        new_release_at: Optional[str] = None
        if td is not None:
            new_release_at = (discord.utils.utcnow() + td).isoformat()

        await self.bot.db.update_sentence_release(sentence.id, new_release_at)

        new_label = format_timedelta(td) if td else "Permanent"
        log.info(
            "Sentence edited | guild=%s user=%s(%s) sentence=#%s %r -> %r by %s(%s)",
            interaction.guild_id, user, user.id, sentence.id,
            old_label, new_label, interaction.user, interaction.user.id,
        )

        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_sentence_edit(
                interaction.guild, user, interaction.user,
                old_label, new_label, reason, sentence.id
            )

        await interaction.followup.send(
            embed=success_embed(
                f"Sentence Edited — Case #{sentence.id}",
                f"{user.mention}'s sentence has been updated.\n"
                f"**Old duration:** {old_label}\n"
                f"**New duration:** {new_label}\n"
                f"**Reason:** {reason}",
            )
        )

    # ── /jail info ─────────────────────────────────────────────────────────────

    @jail_group.command(name="info", description="View jail info for a member.")
    @app_commands.describe(user="The member to check.")
    @staff_check()
    async def jail_info(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)

        sentence = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        history_count = await self.bot.db.count_sentences(interaction.guild_id, user.id)

        embed = info_embed(interaction.guild, user, sentence, history_count)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /jail list ─────────────────────────────────────────────────────────────

    @jail_group.command(name="list", description="List all currently jailed members.")
    @staff_check()
    async def jail_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        sentences = await self.bot.db.get_all_active_sentences(interaction.guild_id)

        if not sentences:
            await interaction.followup.send(
                embed=success_embed("Jail is Empty", "No members are currently jailed."),
                ephemeral=True,
            )
            return

        now = discord.utils.utcnow()
        PAGE_SIZE = 10
        pages: list[discord.Embed] = []

        for i in range(0, len(sentences), PAGE_SIZE):
            chunk = sentences[i : i + PAGE_SIZE]
            embed = discord.Embed(
                title=f"🔒 Currently Jailed — {len(sentences)} member(s)",
                colour=discord.Colour(0xED4245),
                timestamp=now,
            )
            embed.set_footer(text=FOOTER_TEXT)

            for s in chunk:
                user = interaction.guild.get_member(s.user_id)
                user_str = user.mention if user else f"<@{s.user_id}>"

                if s.release_at:
                    rel = datetime.fromisoformat(s.release_at).replace(tzinfo=timezone.utc)
                    remaining = rel - now
                    if remaining.total_seconds() > 0:
                        time_str = f"<t:{int(rel.timestamp())}:R>"
                    else:
                        time_str = "Expiring soon"
                else:
                    time_str = "Permanent"

                embed.add_field(
                    name=f"Case #{s.id} — {user_str}",
                    value=f"**Reason:** {s.reason}\n**Release:** {time_str}\n**Source:** {s.source}",
                    inline=False,
                )

            pages.append(embed)

        view = PaginatedView(pages)
        view.message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True, wait=True)

    # ── /jail history ──────────────────────────────────────────────────────────

    @jail_group.command(name="history", description="View a member's full jail history.")
    @app_commands.describe(user="The member whose history to view.")
    @staff_check()
    async def jail_history(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        history = await self.bot.db.get_sentence_history(interaction.guild_id, user.id)

        if not history:
            await interaction.followup.send(
                embed=success_embed("No History", f"{user.mention} has no jail history."),
                ephemeral=True,
            )
            return

        PAGE_SIZE = 10
        pages: list[discord.Embed] = []

        for i in range(0, len(history), PAGE_SIZE):
            chunk = history[i : i + PAGE_SIZE]
            embed = discord.Embed(
                title=f"📋 Jail History — {user.display_name} ({len(history)} records)",
                colour=discord.Colour(0x5865F2),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text=FOOTER_TEXT)

            for s in chunk:
                jailed_ts = int(
                    datetime.fromisoformat(s.jailed_at).replace(tzinfo=timezone.utc).timestamp()
                )
                status = "Active" if s.released_at is None else "Released"

                if s.release_at:
                    rel_ts = int(
                        datetime.fromisoformat(s.release_at).replace(tzinfo=timezone.utc).timestamp()
                    )
                    duration_str = f"Until <t:{rel_ts}:f>"
                else:
                    duration_str = "Permanent"

                embed.add_field(
                    name=f"Case #{s.id} [{status}]",
                    value=(
                        f"**Jailed:** <t:{jailed_ts}:f>\n"
                        f"**Duration:** {duration_str}\n"
                        f"**Reason:** {s.reason}\n"
                        f"**Source:** {s.source}"
                    ),
                    inline=False,
                )

            pages.append(embed)

        view = PaginatedView(pages)
        view.message = await interaction.followup.send(embed=pages[0], view=view, ephemeral=True, wait=True)

    # ── /jail isolate / unisolate ──────────────────────────────────────────────

    @jail_group.command(name="isolate", description="Move a jailed member to a private cell (removes them from jail-general).")
    @app_commands.describe(
        user="The jailed member to isolate.",
        reason="Reason for isolation.",
    )
    @staff_check()
    async def jail_isolate(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "Isolated by moderator",
    ):
        await interaction.response.defer(ephemeral=True)

        sentence = await self.bot.db.get_active_sentence(interaction.guild_id, user.id)
        if sentence is None:
            await interaction.followup.send(
                embed=error_embed("Not Jailed", f"{user.mention} is not currently jailed."),
                ephemeral=True,
            )
            return

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        if not config or not config.jail_category_id:
            await interaction.followup.send(
                embed=error_embed("Not Configured", "Jail category is not configured. Run `/jail setup`."),
                ephemeral=True,
            )
            return

        jail_category = interaction.guild.get_channel(config.jail_category_id)
        if not isinstance(jail_category, discord.CategoryChannel):
            await interaction.followup.send(
                embed=error_embed("Config Error", "Jail category not found."),
                ephemeral=True,
            )
            return

        # Check for existing isolated channel
        existing = discord.utils.get(jail_category.channels, name=f"isolated-{user.id}")
        if existing:
            await interaction.followup.send(
                embed=error_embed("Already Isolated", f"{user.mention} is already isolated in {existing.mention}."),
                ephemeral=True,
            )
            return

        # Deny the user from jail-general
        jail_general = discord.utils.get(jail_category.text_channels, name="jail-general")
        if jail_general:
            try:
                await jail_general.set_permissions(
                    user,
                    view_channel=False,
                    send_messages=False,
                    reason=f"Isolate — {reason}",
                )
            except discord.Forbidden:
                pass

        # Build isolated channel overwrites
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
            interaction.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
            ),
        }
        if config.staff_role_id:
            staff_role = interaction.guild.get_role(config.staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        try:
            isolated_ch = await jail_category.create_text_channel(
                f"isolated-{user.id}",
                overwrites=overwrites,
                topic=f"Isolated cell for {user.display_name} — {reason}",
                reason=f"Isolate — {reason}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("Permission Error", "I cannot create channels in the jail category."),
                ephemeral=True,
            )
            return

        await isolated_ch.send(
            content=user.mention,
            embed=discord.Embed(
                title="🔒 You have been isolated",
                description=f"You have been moved to a private cell.\n**Reason:** {reason}",
                colour=discord.Colour(0xED4245),
                timestamp=discord.utils.utcnow(),
            ).set_footer(text=FOOTER_TEXT),
        )

        log.info(
            "Isolated | guild=%s user=%s(%s) isolated_channel=%s by %s(%s)",
            interaction.guild_id, user, user.id, isolated_ch.id, interaction.user, interaction.user.id,
        )
        await interaction.followup.send(
            embed=success_embed(
                "Member Isolated",
                f"{user.mention} has been moved to {isolated_ch.mention}.\n"
                f"They no longer have access to jail-general.\n**Reason:** {reason}",
            ),
            ephemeral=True,
        )

    @jail_group.command(name="unisolate", description="Return an isolated member to the general jail population.")
    @app_commands.describe(user="The member to unisolate.")
    @staff_check()
    async def jail_unisolate(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.db.get_guild_config(interaction.guild_id)
        if not config or not config.jail_category_id:
            await interaction.followup.send(
                embed=error_embed("Not Configured", "Jail category is not configured."),
                ephemeral=True,
            )
            return

        jail_category = interaction.guild.get_channel(config.jail_category_id)
        if not isinstance(jail_category, discord.CategoryChannel):
            await interaction.followup.send(
                embed=error_embed("Config Error", "Jail category not found."),
                ephemeral=True,
            )
            return

        isolated_ch = discord.utils.get(jail_category.channels, name=f"isolated-{user.id}")
        if isolated_ch is None:
            await interaction.followup.send(
                embed=error_embed("Not Isolated", f"{user.mention} does not have an isolation channel."),
                ephemeral=True,
            )
            return

        try:
            await isolated_ch.delete(reason=f"Unisolate — {user}")
        except discord.Forbidden:
            pass

        # Remove the member-specific deny from jail-general
        jail_general = discord.utils.get(jail_category.text_channels, name="jail-general")
        if jail_general:
            try:
                await jail_general.set_permissions(user, overwrite=None, reason="unisolate")
            except discord.Forbidden:
                pass

        log.info(
            "Unisolated | guild=%s user=%s(%s) by %s(%s)",
            interaction.guild_id, user, user.id, interaction.user, interaction.user.id,
        )
        await interaction.followup.send(
            embed=success_embed("Isolation Ended", f"{user.mention} has been returned to the general jail population."),
            ephemeral=True,
        )

    # ── /jail roster ───────────────────────────────────────────────────────────

    @jail_group.command(name="roster", description="Show everyone currently in jail (visible to all in jail channels).")
    @staff_check()
    async def jail_roster(self, interaction: discord.Interaction):
        """Posts a non-ephemeral list of current inmates — useful for displaying in jail-general."""
        await interaction.response.defer()

        sentences = await self.bot.db.get_all_active_sentences(interaction.guild_id)

        if not sentences:
            await interaction.followup.send(
                embed=success_embed("Jail is Empty", "No members are currently jailed.")
            )
            return

        now = discord.utils.utcnow()
        embed = discord.Embed(
            title=f"🔒 Current Inmates — {len(sentences)}",
            colour=discord.Colour(0xED4245),
            timestamp=now,
        )
        embed.set_footer(text=FOOTER_TEXT)

        for s in sentences:
            member = interaction.guild.get_member(s.user_id)
            name = member.display_name if member else f"<@{s.user_id}>"
            if s.release_at:
                rel = datetime.fromisoformat(s.release_at).replace(tzinfo=timezone.utc)
                time_str = f"<t:{int(rel.timestamp())}:R>"
            else:
                time_str = "Permanent"
            embed.add_field(
                name=name,
                value=f"Release: {time_str} • Reason: {s.reason[:60]}",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ── Auto-deny new channels ──────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        """Automatically deny the jail role on any newly created channel."""
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel)):
            return
        try:
            config = await self.bot.db.get_guild_config(channel.guild.id)
            if not config or not config.jail_role_id:
                return
            # Skip channels inside the jail category
            if config.jail_category_id and channel.category_id == config.jail_category_id:
                return
            jail_role = channel.guild.get_role(config.jail_role_id)
            if jail_role is None:
                return
            await channel.set_permissions(
                jail_role,
                view_channel=False,
                reason="— auto-deny jail role on new channel",
            )
            log.debug("Auto-denied jail role on new channel %s(%s) in guild %s", channel.name, channel.id, channel.guild.id)
        except discord.Forbidden:
            log.warning("Missing permissions to auto-deny jail role on channel %s(%s)", channel.name, channel.id)
        except Exception:
            log.exception("Error in on_guild_channel_create for %s(%s)", channel.name, channel.id)

    # ── Evasion protection ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Re-apply jail role if a member joins while serving a sentence (evasion)."""
        log.debug("on_member_join: %s(%s) guild=%s", member, member.id, member.guild.id)
        try:
            sentence = await self.bot.db.get_active_sentence(member.guild.id, member.id)
            if sentence is None:
                return

            log.warning(
                "Evasion detected | guild=%s user=%s(%s) sentence=#%s — re-applying jail role",
                member.guild.id, member, member.id, sentence.id,
            )

            config = await self.bot.db.get_guild_config(member.guild.id)
            if not config or not config.jail_role_id:
                return

            jail_role = member.guild.get_role(config.jail_role_id)
            if jail_role is None:
                return

            await member.add_roles(jail_role, reason="Jail evasion — sentence still active")
            log.debug("Jail role re-applied to evader %s(%s)", member, member.id)

            logging_cog = self.bot.get_cog("Logging")
            if logging_cog:
                await logging_cog.log_evasion(member.guild, member, sentence)

        except Exception:
            log.exception("Error in on_member_join evasion check for %s(%s)", member, member.id)


async def setup(bot: "ModBot"):
    cog = Jail(bot)
    # Add setup subcommands to the jail group before add_cog registers it
    setup_cog = bot.get_cog("Setup")
    if setup_cog:
        # add_cog auto-registered these as top-level commands; remove then re-nest under /jail
        bot.tree.remove_command("setup")
        bot.tree.remove_command("config")
        if cog.jail_group.get_command("setup") is None:
            cog.jail_group.add_command(setup_cog.jail_setup)
        if cog.jail_group.get_command("config") is None:
            cog.jail_group.add_command(setup_cog.config_group)
    # add_cog automatically registers class-level app_commands.Group attributes
    await bot.add_cog(cog)
