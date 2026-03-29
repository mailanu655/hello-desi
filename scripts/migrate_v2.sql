-- Mira v2 Migration: Session persistence, rate limiting, notification logging
-- Run this in Supabase SQL Editor

-- 1. User state table (replaces in-memory _seen_users)
CREATE TABLE IF NOT EXISTS user_state (
    wa_id TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    city TEXT DEFAULT '',
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW(),
    messages_today INTEGER DEFAULT 0,
    message_date DATE DEFAULT CURRENT_DATE
);

CREATE INDEX IF NOT EXISTS idx_user_state_last_active ON user_state(last_active);

-- 2. Notification log (tracks lead alert delivery)
CREATE TABLE IF NOT EXISTS notification_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id TEXT,
    business_name TEXT,
    owner_wa_id TEXT,
    search_query TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'sent',  -- sent, rate_limited, no_owner, failed
    error_msg TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log(created_at);
CREATE INDEX IF NOT EXISTS idx_notification_log_business ON notification_log(business_id);

-- 3. Add deals_this_month counter to subscriptions for deal limit enforcement
-- (We'll count from the deals table directly instead — no schema change needed)
