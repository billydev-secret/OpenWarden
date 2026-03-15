"""
cogs/scheduler.py — Background task for auto-releasing expired sentences.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from bot import ModBot

log = logging.getLogger("jailbot.scheduler")


class Scheduler(commands.Cog, name="Scheduler"):
    """Background scheduler for automatic sentence expiry."""

    def __init__(self, bot: "ModBot"):
        self.bot = bot
        self.check_sentences.start()

    def cog_unload(self):
        self.check_sentences.cancel()

    @tasks.loop(seconds=30)
    async def check_sentences(self):
        """Check for and auto-release any expired sentences."""
        log.debug("Scheduler tick — checking for expired sentences")
        try:
            expired = await self.bot.db.get_expired_sentences()
            if expired:
                log.debug("Found %d expired sentence(s) to process", len(expired))
            for sentence in expired:
                try:
                    await self._auto_release(sentence)
                except Exception:
                    log.exception("Error auto-releasing sentence #%s", sentence.id)
        except Exception:
            log.exception("Unhandled error in check_sentences loop")

    async def _auto_release(self, sentence):
        """Auto-release a single expired sentence."""
        log.debug("Auto-releasing sentence #%s (guild=%s user=%s)", sentence.id, sentence.guild_id, sentence.user_id)
        guild = self.bot.get_guild(sentence.guild_id)
        if guild is None:
            log.warning("Guild %s not found for sentence #%s — marking released", sentence.guild_id, sentence.id)
            await self.bot.db.release_sentence(sentence.id)
            return

        config = await self.bot.db.get_guild_config(guild.id)
        if not config:
            log.warning("No config for guild %s — marking sentence #%s released", guild.id, sentence.id)
            await self.bot.db.release_sentence(sentence.id)
            return

        member = guild.get_member(sentence.user_id)

        # Mark as released first to avoid double-processing
        await self.bot.db.release_sentence(sentence.id)

        if member is None:
            log.info(
                "Auto-release: user %s not in guild %s (left server) — sentence #%s marked released",
                sentence.user_id, guild.id, sentence.id,
            )
            logging_cog = self.bot.get_cog("Logging")
            if logging_cog:
                try:
                    user = await self.bot.fetch_user(sentence.user_id)
                    await logging_cog.log_auto_unjail(guild, user, sentence)
                except Exception:
                    log.debug("Could not fetch user %s for auto-unjail log", sentence.user_id)
            return

        # Restore snapshot roles + drop jail role in one PATCH request
        roles_to_restore: list[discord.Role] = []
        if sentence.role_snapshot:
            try:
                snapshot_ids = json.loads(sentence.role_snapshot)
                roles_to_restore = [r for rid in snapshot_ids if (r := guild.get_role(rid)) is not None]
                skipped = len(snapshot_ids) - len(roles_to_restore)
                if skipped:
                    log.debug("%d snapshot role(s) no longer exist for %s(%s)", skipped, member, member.id)
            except json.JSONDecodeError as exc:
                log.warning("Bad role snapshot for sentence #%s: %s", sentence.id, exc)

        managed_roles = [r for r in member.roles if r.managed]
        try:
            await member.edit(roles=managed_roles + roles_to_restore, reason="Sentence expired — auto-release")
            log.debug(
                "Roles restored via edit() for %s(%s): %d role(s) reinstated",
                member, member.id, len(roles_to_restore),
            )
        except discord.Forbidden:
            log.warning("Missing permissions to restore roles for %s(%s)", member, member.id)

        # Send release DM
        if config.dm_on_release:
            from utils.embeds import release_dm_embed
            dm_embed = release_dm_embed(guild, "Your sentence has been completed.")
            try:
                await member.send(embed=dm_embed)
                log.debug("Release DM sent to %s(%s)", member, member.id)
            except discord.HTTPException:
                log.debug("Could not DM %s(%s) on auto-release", member, member.id)

        log.info(
            "Auto-released | guild=%s user=%s(%s) sentence=#%s",
            guild.id, member, member.id, sentence.id,
        )

        # Log auto-release
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog:
            try:
                await logging_cog.log_auto_unjail(guild, member, sentence)
            except Exception:
                log.exception("Error sending auto-unjail log for sentence #%s", sentence.id)

    @check_sentences.before_loop
    async def before_check_sentences(self):
        await self.bot.wait_until_ready()


async def setup(bot: "ModBot"):
    await bot.add_cog(Scheduler(bot))
