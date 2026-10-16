-- ============================================================
-- MEDIA2TEXT DATABASE
-- PostgreSQL schema + safe migration for an existing database
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    email VARCHAR(150) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transcriptions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    audio_filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL,
    media_type VARCHAR(20) NOT NULL,
    language VARCHAR(30) NOT NULL DEFAULT 'mr-IN',
    transcript TEXT DEFAULT '',
    target_language VARCHAR(20),
    translated_text TEXT,
    pdf_filename VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_transcription_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);


-- ------------------------------------------------------------
-- USER ACTIVITY / USAGE TRACKING (SAFE MIGRATION)
-- These columns preserve all existing accounts and records.
-- ------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS total_usage_seconds INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count INTEGER DEFAULT 0;

UPDATE users
SET total_usage_seconds = 0
WHERE total_usage_seconds IS NULL;

UPDATE users
SET login_count = 0
WHERE login_count IS NULL;

-- ------------------------------------------------------------
-- SAFE MIGRATION FOR OLD transcriptions TABLE
-- These commands do NOT delete the database or old records.
-- ------------------------------------------------------------

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS audio_filename VARCHAR(255);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS stored_filename VARCHAR(255);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS media_type VARCHAR(20);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS language VARCHAR(30);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS transcript TEXT DEFAULT '';

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS translated_text TEXT;

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS pdf_filename VARCHAR(255);

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE transcriptions
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Backfill old records before applying NOT NULL constraints.
UPDATE transcriptions
SET original_filename = COALESCE(
    original_filename,
    audio_filename,
    'unknown_media'
)
WHERE original_filename IS NULL;

UPDATE transcriptions
SET audio_filename = COALESCE(
    audio_filename,
    original_filename
)
WHERE audio_filename IS NULL;

UPDATE transcriptions
SET stored_filename = COALESCE(
    stored_filename,
    audio_filename,
    original_filename
)
WHERE stored_filename IS NULL;

UPDATE transcriptions
SET media_type = COALESCE(media_type, 'audio')
WHERE media_type IS NULL;

UPDATE transcriptions
SET language = COALESCE(language, 'mr-IN')
WHERE language IS NULL;

UPDATE transcriptions
SET transcript = COALESCE(transcript, '')
WHERE transcript IS NULL;

UPDATE transcriptions
SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)
WHERE created_at IS NULL;

UPDATE transcriptions
SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
WHERE updated_at IS NULL;

-- Apply constraints after backfilling.
ALTER TABLE transcriptions
ALTER COLUMN audio_filename SET NOT NULL;

ALTER TABLE transcriptions
ALTER COLUMN original_filename SET NOT NULL;

ALTER TABLE transcriptions
ALTER COLUMN stored_filename SET NOT NULL;

ALTER TABLE transcriptions
ALTER COLUMN media_type SET NOT NULL;

ALTER TABLE transcriptions
ALTER COLUMN language SET NOT NULL;

ALTER TABLE transcriptions
ALTER COLUMN transcript SET DEFAULT '';

ALTER TABLE transcriptions
ALTER COLUMN pdf_filename DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_transcriptions_user_id
ON transcriptions(user_id);

CREATE INDEX IF NOT EXISTS idx_transcriptions_created_at
ON transcriptions(created_at);

-- Final structure check:
SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'transcriptions'
ORDER BY ordinal_position;
