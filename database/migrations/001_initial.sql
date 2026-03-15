-- Initial Schema Migration
-- Version: 001

-- Per-guild configuration
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id          INTEGER PRIMARY KEY,
    jail_role_id      INTEGER,
    jail_category_id  INTEGER,
    log_channel_id    INTEGER,
    appeal_channel_id INTEGER,
    default_duration  INTEGER DEFAULT 86400,
    max_sentence      INTEGER DEFAULT 2592000,
    vote_threshold    INTEGER DEFAULT 0,
    dm_on_jail        BOOLEAN DEFAULT 1,
    dm_on_release     BOOLEAN DEFAULT 1,
    staff_role_id     INTEGER
);

-- Active and historical jail sentences
CREATE TABLE IF NOT EXISTS sentences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    moderator_id    INTEGER,
    reason          TEXT DEFAULT 'No reason provided',
    jailed_at       TEXT NOT NULL,
    release_at      TEXT,
    released_at     TEXT,
    source          TEXT DEFAULT 'manual',
    role_snapshot   TEXT,
    FOREIGN KEY (guild_id) REFERENCES guild_config(guild_id)
);

CREATE INDEX IF NOT EXISTS idx_sentences_active ON sentences(guild_id, user_id, released_at);
CREATE INDEX IF NOT EXISTS idx_sentences_release ON sentences(release_at) WHERE released_at IS NULL;

-- AutoMod rule links
CREATE TABLE IF NOT EXISTS automod_rules (
    guild_id    INTEGER NOT NULL,
    rule_id     TEXT NOT NULL,
    duration    INTEGER NOT NULL,
    PRIMARY KEY (guild_id, rule_id)
);

-- Active vote-jail sessions
CREATE TABLE IF NOT EXISTS vote_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    target_user_id  INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    message_id      INTEGER NOT NULL,
    initiator_id    INTEGER NOT NULL,
    voters          TEXT DEFAULT '[]',
    threshold       INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    expired         BOOLEAN DEFAULT 0
);

-- Appeal records
CREATE TABLE IF NOT EXISTS appeals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    sentence_id     INTEGER NOT NULL,
    channel_id      INTEGER,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    outcome         TEXT,
    staff_id        INTEGER,
    FOREIGN KEY (sentence_id) REFERENCES sentences(id)
);

-- Channel exclusions (channels jailed users CAN access)
CREATE TABLE IF NOT EXISTS channel_exclusions (
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
