-- ============================================================
-- SQL Migrations for run_daily.py Improvements
-- ============================================================
-- Run these in Supabase SQL Editor
-- ============================================================

-- MIGRATION 1: Content Deduplication (from Phase 2)
-- Adds content_hash column to prevent duplicate coupons

ALTER TABLE relevant_story 
ADD COLUMN IF NOT EXISTS content_hash VARCHAR(16);

-- Index for efficient deduplication queries
CREATE INDEX IF NOT EXISTS idx_content_hash_date 
ON relevant_story(user_id, content_hash, date);

COMMENT ON COLUMN relevant_story.content_hash IS 
'MD5 hash of coupon content (code+brand+url+verdict) for deduplication';


-- ============================================================
-- MIGRATION 2: Active Learning System (Phase 3)
-- Stores user corrections for continuous improvement
-- ============================================================

CREATE TABLE IF NOT EXISTS user_corrections (
    id SERIAL PRIMARY KEY,
    media_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    
    -- What AI predicted
    ai_verdict VARCHAR,
    ai_code VARCHAR,
    ai_brand VARCHAR,
    ai_confidence FLOAT,
    
    -- What is actually correct
    correct_verdict VARCHAR NOT NULL,
    correct_code VARCHAR,
    correct_brand VARCHAR,
    
    -- Context for learning
    story_context TEXT,
    correction_note TEXT,
    
    -- Metadata
    corrected_at TIMESTAMP DEFAULT NOW(),
    corrected_by VARCHAR DEFAULT 'user',
    
    -- Ensure one correction per story
    UNIQUE(media_id)
);

-- Indexes for efficient retrieval
CREATE INDEX IF NOT EXISTS idx_corrections_user 
ON user_corrections(user_id, corrected_at DESC);

CREATE INDEX IF NOT EXISTS idx_corrections_verdict 
ON user_corrections(correct_verdict);

-- Comments for documentation
COMMENT ON TABLE user_corrections IS 
'Stores manual corrections for Active Learning. System learns from these to improve accuracy over time.';

COMMENT ON COLUMN user_corrections.story_context IS 
'Text context from the story (caption, OCR, stickers) used for Few-Shot learning';


-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

-- Check if migrations succeeded
SELECT 
    column_name, 
    data_type 
FROM information_schema.columns 
WHERE table_name = 'relevant_story' 
  AND column_name = 'content_hash';

SELECT 
    table_name, 
    column_name 
FROM information_schema.columns 
WHERE table_name = 'user_corrections'
ORDER BY ordinal_position;

-- Check indexes
SELECT 
    indexname, 
    indexdef 
FROM pg_indexes 
WHERE tablename IN ('relevant_story', 'user_corrections')
ORDER BY tablename, indexname;


-- ============================================================
-- EXAMPLE: How to add a correction manually
-- ============================================================

-- Example 1: Correct a false positive (AI said coupon, but it's organic)
INSERT INTO user_corrections (
    media_id,
    user_id,
    ai_verdict,
    ai_code,
    ai_brand,
    ai_confidence,
    correct_verdict,
    story_context,
    correction_note
) VALUES (
    '12345_67890',
    '987654321',
    'coupon',
    'SAVE20',
    'addict',
    0.85,
    'organic',
    'Caption: Family dinner photo. OCR: None. Stickers: None.',
    'This was just a personal photo, not a coupon'
)
ON CONFLICT (media_id) DO UPDATE SET
    correct_verdict = EXCLUDED.correct_verdict,
    correction_note = EXCLUDED.correction_note,
    corrected_at = NOW();


-- Example 2: Correct a missed coupon (AI said organic, but there was a code)
INSERT INTO user_corrections (
    media_id,
    user_id,
    ai_verdict,
    correct_verdict,
    correct_code,
    correct_brand,
    story_context
) VALUES (
    '12346_67891',
    '987654321',
    'organic',
    'coupon',
    'FASHION25',
    'reserved',
    'Caption: קוד הנחה חדש! Stickers: FASHION25 ב-Reserved'
);


-- ============================================================
-- ANALYTICS QUERIES
-- ============================================================

-- How many corrections per user?
SELECT 
    user_id,
    COUNT(*) as total_corrections,
    COUNT(CASE WHEN ai_verdict != correct_verdict THEN 1 END) as verdict_errors,
    COUNT(CASE WHEN ai_code IS NOT NULL AND ai_code != correct_code THEN 1 END) as code_errors
FROM user_corrections
GROUP BY user_id
ORDER BY total_corrections DESC;

-- Most common AI mistakes
SELECT 
    ai_verdict,
    correct_verdict,
    COUNT(*) as count
FROM user_corrections
WHERE ai_verdict != correct_verdict
GROUP BY ai_verdict, correct_verdict
ORDER BY count DESC
LIMIT 10;

-- Recent corrections (for debugging)
SELECT 
    media_id,
    ai_verdict,
    correct_verdict,
    ai_code,
    correct_code,
    corrected_at
FROM user_corrections
ORDER BY corrected_at DESC
LIMIT 20;
