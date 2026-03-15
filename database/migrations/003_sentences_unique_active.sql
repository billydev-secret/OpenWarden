-- Enforce at the database level that a user can only have one active sentence
-- per guild at a time (released_at IS NULL = active).
CREATE UNIQUE INDEX IF NOT EXISTS idx_sentences_one_active
    ON sentences(guild_id, user_id)
    WHERE released_at IS NULL;
