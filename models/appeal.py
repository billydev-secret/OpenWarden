from dataclasses import dataclass
from typing import Optional


@dataclass
class Appeal:
    id: int
    guild_id: int
    user_id: int
    sentence_id: int
    channel_id: Optional[int]
    opened_at: str
    closed_at: Optional[str]
    outcome: Optional[str]
    staff_id: Optional[int]

    @classmethod
    def from_row(cls, row: dict) -> "Appeal":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            sentence_id=row["sentence_id"],
            channel_id=row["channel_id"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            outcome=row["outcome"],
            staff_id=row["staff_id"],
        )

    @property
    def is_open(self) -> bool:
        return self.closed_at is None
