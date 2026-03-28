-- Hello Desi — Users Table
-- Stores WhatsApp user profiles and preferences

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wa_id TEXT UNIQUE NOT NULL,
    name TEXT,
    city TEXT,
    language TEXT DEFAULT 'en',
    interests TEXT[],
    is_premium BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Index for fast lookups by WhatsApp ID
CREATE INDEX IF NOT EXISTS idx_users_wa_id ON users(wa_id);

-- Index for city-based queries
CREATE INDEX IF NOT EXISTS idx_users_city ON users(city);
