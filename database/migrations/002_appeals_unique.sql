-- Prevent duplicate open appeals for the same user in the same guild.
CREATE UNIQUE INDEX IF NOT EXISTS idx_appeals_open
    ON appeals(guild_id, user_id)
    WHERE closed_at IS NULL;
