"""
config.py — centralised environment variable access .

All settings have defaults so the bot can start even if only DISCORD_TOKEN is set.
"""

from __future__ import annotations

import logging
import os
from dotenv import load_dotenv

log = logging.getLogger("jailbot.config")

load_dotenv()


def _get(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def _get_int(key: str, default: int | None = None) -> int | None:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log.warning("Config: %s=%r is not a valid integer — using default %r", key, value, default)
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").strip().lower()
    if not value:
        return default
    if value not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        log.warning("Config: %s=%r is not a recognised boolean — using default %r", key, value, default)
        return default
    return value in ("1", "true", "yes", "on")


# ── Required ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN: str = os.environ.get("DISCORD_TOKEN", "")

# ── Optional ──────────────────────────────────────────────────────────────────

DATABASE_PATH: str = _get("DATABASE_PATH", "data/jailbot.db")

# If set, slash commands are synced instantly to this guild (dev mode).
DEV_GUILD_ID: int | None = _get_int("DEV_GUILD_ID")

# ── Derived helpers ───────────────────────────────────────────────────────────

def require_token() -> str:
    """Return the bot token or raise if it is not configured."""
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is not set. "
            "Copy .env.example to .env and fill in your bot token."
        )
    return DISCORD_TOKEN
