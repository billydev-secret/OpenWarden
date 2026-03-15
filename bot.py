"""
bot.py — Entry point.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import asyncio
import pathlib

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database.db import Database

log = logging.getLogger("jailbot")


def setup_logging() -> None:
    """Configure logging to match discord.py's format, with both console and rotating file output."""
    # Use discord's own setup (configures the root logger with its formatter/handler)
    discord.utils.setup_logging(level=logging.DEBUG)

    # Also write to a rotating log file so nothing is lost between restarts
    log_dir = pathlib.Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "jailbot.log",
        maxBytes=8 * 1024 * 1024,  # 8 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(file_handler)

    # Keep discord's own internals at INFO to avoid extreme verbosity
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    # Our loggers at DEBUG
    logging.getLogger("jailbot").setLevel(logging.DEBUG)


class ModBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = False
        intents.auto_moderation_execution = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the jail",
            ),
        )
        self.db: Database = Database(os.getenv("DATABASE_PATH", "data/jailbot.db"))

    async def setup_hook(self):
        log.debug("Initialising database")
        await self.db.initialize()

        # Load cogs in dependency order:
        # logging and setup first (jail.py depends on both), then the rest.
        cog_names = [
            "cogs.logging",
            "cogs.setup",
            "cogs.jail",       # registers /jail group; imports Setup cog subcommands
            "cogs.votejail",   # adds /jail vote to jail group
            "cogs.automod",    # adds /jail automod to jail group
            "cogs.appeals",    # standalone /appeal and /appeal-close
            "cogs.exclusions", # adds /jail exclude to jail group
            "cogs.mute",       # standalone /mute and /unmute
            "cogs.scheduler",  # background task
            "cogs.help",       # /help command
        ]
        for cog in cog_names:
            log.debug("Loading extension: %s", cog)
            await self.load_extension(cog)
            log.debug("Loaded extension: %s", cog)

        # Restore persistent vote views
        votejail_cog = self.get_cog("VoteJail")
        if votejail_cog:
            log.debug("Restoring persistent vote views")
            await votejail_cog.restore_views()

        # Sync slash commands
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:
            guild_obj = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            log.info("Slash commands synced to dev guild %s", dev_guild)
        else:
            await self.tree.sync()
            log.info("Slash commands synced globally")

    async def on_ready(self):
        log.info(
            "Ready: logged in as %s (ID: %s) — connected to %d guild(s)",
            self.user,
            self.user.id,
            len(self.guilds),
        )

    async def close(self):
        log.info("Shutting down — closing database connection")
        await self.db.close()
        await super().close()

async def main():
    load_dotenv()
    setup_logging()

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN is not set. "
            "Copy .env.example to .env and fill in your bot token."
        )

    log.info("Starting bot")
    bot = ModBot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())