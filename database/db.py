from __future__ import annotations

import json
import logging
import os
import pathlib
from typing import Optional

import aiosqlite

from models.guild_config import GuildConfig
from models.sentence import Sentence
from models.appeal import Appeal

log = logging.getLogger("jailbot.db")


class AutomodRule:
    def __init__(self, guild_id: int, rule_id: str, duration: int):
        self.guild_id = guild_id
        self.rule_id = rule_id
        self.duration = duration

    @classmethod
    def from_row(cls, row: dict) -> "AutomodRule":
        return cls(
            guild_id=row["guild_id"],
            rule_id=row["rule_id"],
            duration=row["duration"],
        )


class VoteSession:
    def __init__(
        self,
        id: int,
        guild_id: int,
        target_user_id: int,
        channel_id: int,
        message_id: int,
        initiator_id: int,
        voters: list[int],
        threshold: int,
        created_at: str,
        expired: bool,
    ):
        self.id = id
        self.guild_id = guild_id
        self.target_user_id = target_user_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.initiator_id = initiator_id
        self.voters = voters
        self.threshold = threshold
        self.created_at = created_at
        self.expired = expired

    @classmethod
    def from_row(cls, row: dict) -> "VoteSession":
        voters_raw = row["voters"]
        if isinstance(voters_raw, str):
            try:
                voters = json.loads(voters_raw)
            except (json.JSONDecodeError, TypeError):
                voters = []
        else:
            voters = voters_raw or []
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            target_user_id=row["target_user_id"],
            channel_id=row["channel_id"],
            message_id=row["message_id"],
            initiator_id=row["initiator_id"],
            voters=voters,
            threshold=row["threshold"],
            created_at=row["created_at"],
            expired=bool(row["expired"]),
        )


class Database:
    def __init__(self, path: str = "data/jailbot.db"):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        db_path = pathlib.Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        log.debug("Opening database at %s", self.path)

        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        migrations_dir = pathlib.Path(__file__).parent / "migrations"
        for migration_path in sorted(migrations_dir.glob("*.sql")):
            log.debug("Applying migration: %s", migration_path.name)
            sql = migration_path.read_text(encoding="utf-8")
            await self._conn.executescript(sql)
        await self._db.commit()
        log.info("Database initialised at %s", self.path)

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database.initialize() has not been called yet.")
        return self._db

    async def close(self):
        if self._db:
            log.debug("Closing database connection")
            await self._db.close()
            self._db = None

    # -------------------------------------------------------------------------
    # Guild Config
    # -------------------------------------------------------------------------

    async def get_guild_config(self, guild_id: int) -> Optional[GuildConfig]:
        async with self._conn.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return GuildConfig.from_row(dict(row))

    async def upsert_guild_config(self, guild_id: int, **kwargs):
        existing = await self.get_guild_config(guild_id)
        if existing is None:
            await self._conn.execute(
                "INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,)
            )
            await self._conn.commit()

        if not kwargs:
            return

        valid_columns = {
            "jail_role_id",
            "jail_category_id",
            "log_channel_id",
            "appeal_channel_id",
            "default_duration",
            "max_sentence",
            "vote_threshold",
            "dm_on_jail",
            "dm_on_release",
            "staff_role_id",
        }
        filtered = {k: v for k, v in kwargs.items() if k in valid_columns}
        if not filtered:
            return

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [guild_id]
        await self._conn.execute(
            f"UPDATE guild_config SET {set_clause} WHERE guild_id = ?", values
        )
        await self._conn.commit()

    # -------------------------------------------------------------------------
    # Sentences
    # -------------------------------------------------------------------------

    async def insert_sentence(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: Optional[int],
        reason: str,
        release_at: Optional[str],
        source: str,
        role_snapshot: Optional[str],
    ) -> int:
        from discord.utils import utcnow

        jailed_at = utcnow().isoformat()
        log.debug(
            "INSERT sentence guild=%s user=%s source=%s release_at=%s",
            guild_id, user_id, source, release_at,
        )
        async with self._conn.execute(
            """
            INSERT INTO sentences
                (guild_id, user_id, moderator_id, reason, jailed_at, release_at, source, role_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason, jailed_at, release_at, source, role_snapshot),
        ) as cursor:
            sentence_id = cursor.lastrowid
        await self._conn.commit()
        log.debug("Sentence #%s committed", sentence_id)
        return sentence_id

    async def get_active_sentence(self, guild_id: int, user_id: int) -> Optional[Sentence]:
        async with self._conn.execute(
            """
            SELECT * FROM sentences
            WHERE guild_id = ? AND user_id = ? AND released_at IS NULL
            ORDER BY jailed_at DESC LIMIT 1
            """,
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Sentence.from_row(dict(row))

    async def get_sentence(self, sentence_id: int) -> Optional[Sentence]:
        async with self._conn.execute(
            "SELECT * FROM sentences WHERE id = ?", (sentence_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Sentence.from_row(dict(row))

    async def get_expired_sentences(self) -> list[Sentence]:
        from discord.utils import utcnow

        now = utcnow().isoformat()
        log.debug("Querying expired sentences (now=%s)", now)
        async with self._conn.execute(
            """
            SELECT * FROM sentences
            WHERE released_at IS NULL AND release_at IS NOT NULL AND release_at <= ?
            """,
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
        count = len(rows)
        if count:
            log.debug("Found %d expired sentence(s)", count)
        return [Sentence.from_row(dict(r)) for r in rows]

    async def release_sentence(self, sentence_id: int):
        from discord.utils import utcnow

        released_at = utcnow().isoformat()
        log.debug("Releasing sentence #%s at %s", sentence_id, released_at)
        await self._conn.execute(
            "UPDATE sentences SET released_at = ? WHERE id = ?",
            (released_at, sentence_id),
        )
        await self._conn.commit()

    async def update_sentence_release(self, sentence_id: int, new_release_at: Optional[str]):
        log.debug("Updating sentence #%s release_at -> %s", sentence_id, new_release_at)
        await self._conn.execute(
            "UPDATE sentences SET release_at = ? WHERE id = ?",
            (new_release_at, sentence_id),
        )
        await self._conn.commit()

    async def get_sentence_history(self, guild_id: int, user_id: int) -> list[Sentence]:
        async with self._conn.execute(
            """
            SELECT * FROM sentences
            WHERE guild_id = ? AND user_id = ?
            ORDER BY jailed_at DESC
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        return [Sentence.from_row(dict(r)) for r in rows]

    async def count_sentences(self, guild_id: int, user_id: int) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM sentences WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_all_active_sentences(self, guild_id: int) -> list[Sentence]:
        async with self._conn.execute(
            """
            SELECT * FROM sentences
            WHERE guild_id = ? AND released_at IS NULL
            ORDER BY jailed_at ASC
            """,
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [Sentence.from_row(dict(r)) for r in rows]

    # -------------------------------------------------------------------------
    # AutoMod Rules
    # -------------------------------------------------------------------------

    async def insert_automod_rule(self, guild_id: int, rule_id: str, duration: int):
        log.debug("Upsert automod rule guild=%s rule=%s duration=%ss", guild_id, rule_id, duration)
        await self._conn.execute(
            """
            INSERT INTO automod_rules (guild_id, rule_id, duration)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, rule_id) DO UPDATE SET duration = excluded.duration
            """,
            (guild_id, rule_id, duration),
        )
        await self._conn.commit()

    async def get_automod_rule(self, guild_id: int, rule_id: str) -> Optional[AutomodRule]:
        async with self._conn.execute(
            "SELECT * FROM automod_rules WHERE guild_id = ? AND rule_id = ?",
            (guild_id, rule_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return AutomodRule.from_row(dict(row))

    async def list_automod_rules(self, guild_id: int) -> list[AutomodRule]:
        async with self._conn.execute(
            "SELECT * FROM automod_rules WHERE guild_id = ? ORDER BY rule_id",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [AutomodRule.from_row(dict(r)) for r in rows]

    async def delete_automod_rule(self, guild_id: int, rule_id: str):
        log.debug("Deleting automod rule guild=%s rule=%s", guild_id, rule_id)
        await self._conn.execute(
            "DELETE FROM automod_rules WHERE guild_id = ? AND rule_id = ?",
            (guild_id, rule_id),
        )
        await self._conn.commit()

    # -------------------------------------------------------------------------
    # Vote Sessions
    # -------------------------------------------------------------------------

    async def insert_vote_session(
        self,
        guild_id: int,
        target_user_id: int,
        channel_id: int,
        message_id: int,
        initiator_id: int,
        threshold: int,
    ) -> int:
        from discord.utils import utcnow

        created_at = utcnow().isoformat()
        async with self._conn.execute(
            """
            INSERT INTO vote_sessions
                (guild_id, target_user_id, channel_id, message_id, initiator_id,
                 voters, threshold, created_at, expired)
            VALUES (?, ?, ?, ?, ?, '[]', ?, ?, 0)
            """,
            (guild_id, target_user_id, channel_id, message_id, initiator_id, threshold, created_at),
        ) as cursor:
            session_id = cursor.lastrowid
        await self._conn.commit()
        return session_id

    async def get_vote_session(self, session_id: int) -> Optional[VoteSession]:
        async with self._conn.execute(
            "SELECT * FROM vote_sessions WHERE id = ?", (session_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return VoteSession.from_row(dict(row))

    async def get_active_vote_session(
        self, guild_id: int, target_user_id: int
    ) -> Optional[VoteSession]:
        async with self._conn.execute(
            """
            SELECT * FROM vote_sessions
            WHERE guild_id = ? AND target_user_id = ? AND expired = 0
            ORDER BY created_at DESC LIMIT 1
            """,
            (guild_id, target_user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return VoteSession.from_row(dict(row))

    async def get_all_active_vote_sessions(self, guild_id: int) -> list[VoteSession]:
        async with self._conn.execute(
            "SELECT * FROM vote_sessions WHERE guild_id = ? AND expired = 0",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [VoteSession.from_row(dict(r)) for r in rows]

    async def update_vote_voters(self, session_id: int, voters: list[int]):
        voters_json = json.dumps(voters)
        await self._conn.execute(
            "UPDATE vote_sessions SET voters = ? WHERE id = ?",
            (voters_json, session_id),
        )
        await self._conn.commit()

    async def expire_vote_session(self, session_id: int):
        log.debug("Expiring vote session #%s", session_id)
        await self._conn.execute(
            "UPDATE vote_sessions SET expired = 1 WHERE id = ?", (session_id,)
        )
        await self._conn.commit()

    # -------------------------------------------------------------------------
    # Appeals
    # -------------------------------------------------------------------------

    async def insert_appeal(
        self,
        guild_id: int,
        user_id: int,
        sentence_id: int,
        channel_id: Optional[int],
    ) -> int:
        from discord.utils import utcnow

        opened_at = utcnow().isoformat()
        async with self._conn.execute(
            """
            INSERT INTO appeals (guild_id, user_id, sentence_id, channel_id, opened_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, sentence_id, channel_id, opened_at),
        ) as cursor:
            appeal_id = cursor.lastrowid
        await self._conn.commit()
        return appeal_id

    async def get_active_appeal(self, guild_id: int, user_id: int) -> Optional[Appeal]:
        async with self._conn.execute(
            """
            SELECT * FROM appeals
            WHERE guild_id = ? AND user_id = ? AND closed_at IS NULL
            ORDER BY opened_at DESC LIMIT 1
            """,
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Appeal.from_row(dict(row))

    async def get_appeal(self, appeal_id: int) -> Optional[Appeal]:
        async with self._conn.execute(
            "SELECT * FROM appeals WHERE id = ?", (appeal_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return Appeal.from_row(dict(row))

    async def close_appeal(self, appeal_id: int, outcome: str, staff_id: int):
        from discord.utils import utcnow

        closed_at = utcnow().isoformat()
        log.debug("Closing appeal #%s outcome=%s staff=%s", appeal_id, outcome, staff_id)
        await self._conn.execute(
            "UPDATE appeals SET closed_at = ?, outcome = ?, staff_id = ? WHERE id = ?",
            (closed_at, outcome, staff_id, appeal_id),
        )
        await self._conn.commit()

    # -------------------------------------------------------------------------
    # Channel Exclusions
    # -------------------------------------------------------------------------

    async def get_channel_exclusions(self, guild_id: int) -> list[int]:
        async with self._conn.execute(
            "SELECT channel_id FROM channel_exclusions WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["channel_id"] for row in rows]

    async def add_channel_exclusion(self, guild_id: int, channel_id: int):
        await self._conn.execute(
            """
            INSERT INTO channel_exclusions (guild_id, channel_id)
            VALUES (?, ?)
            ON CONFLICT DO NOTHING
            """,
            (guild_id, channel_id),
        )
        await self._conn.commit()

    async def remove_channel_exclusion(self, guild_id: int, channel_id: int):
        await self._conn.execute(
            "DELETE FROM channel_exclusions WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        )
        await self._conn.commit()
