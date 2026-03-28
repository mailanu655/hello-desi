-- Hello Desi — Classifieds Table

CREATE TABLE IF NOT EXISTS classifieds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    type TEXT NOT NULL,  -- 'roommate', 'sale', 'carpool', 'service', 'job'
    title TEXT NOT NULL,
    description TEXT,
    city TEXT NOT NULL,
    price DECIMAL(10,2),
    status TEXT DEFAULT 'active',  -- 'active', 'closed', 'expired'
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for browsing by type + city
CREATE INDEX IF NOT EXISTS idx_classifieds_type_city
    ON classifieds(type, city) WHERE status = 'active';

-- Auto-expire after 30 days (run via Supabase cron or scheduled task)
-- UPDATE classifieds SET status = 'expired'
-- WHERE status = 'active' AND created_at < now() - interval '30 days';
