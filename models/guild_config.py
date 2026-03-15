from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuildConfig:
    guild_id: int
    jail_role_id: Optional[int] = None
    jail_category_id: Optional[int] = None
    log_channel_id: Optional[int] = None
    appeal_channel_id: Optional[int] = None
    default_duration: int = 86400
    max_sentence: int = 2592000
    vote_threshold: int = 0
    dm_on_jail: bool = True
    dm_on_release: bool = True
    staff_role_id: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict) -> "GuildConfig":
        return cls(
            guild_id=row["guild_id"],
            jail_role_id=row["jail_role_id"],
            jail_category_id=row["jail_category_id"],
            log_channel_id=row["log_channel_id"],
            appeal_channel_id=row["appeal_channel_id"],
            default_duration=row["default_duration"] or 86400,
            max_sentence=row["max_sentence"] or 2592000,
            vote_threshold=row["vote_threshold"] or 0,
            dm_on_jail=bool(row["dm_on_jail"]),
            dm_on_release=bool(row["dm_on_release"]),
            staff_role_id=row["staff_role_id"],
        )
