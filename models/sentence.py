from dataclasses import dataclass
from typing import Optional


@dataclass
class Sentence:
    id: int
    guild_id: int
    user_id: int
    moderator_id: Optional[int]
    reason: str
    jailed_at: str
    release_at: Optional[str]
    released_at: Optional[str]
    source: str
    role_snapshot: Optional[str]

    @classmethod
    def from_row(cls, row: dict) -> "Sentence":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            reason=row["reason"] or "No reason provided",
            jailed_at=row["jailed_at"],
            release_at=row["release_at"],
            released_at=row["released_at"],
            source=row["source"] or "manual",
            role_snapshot=row["role_snapshot"],
        )

    @property
    def is_active(self) -> bool:
        return self.released_at is None

    @property
    def is_permanent(self) -> bool:
        return self.release_at is None
