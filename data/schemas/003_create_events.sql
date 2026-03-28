-- Hello Desi — Community Events Table

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    city TEXT NOT NULL,
    venue TEXT,
    event_date TIMESTAMPTZ,
    category TEXT,  -- 'cultural', 'religious', 'social', 'professional'
    source TEXT,
    source_url TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for upcoming events by city
CREATE INDEX IF NOT EXISTS idx_events_city_date
    ON events(city, event_date);

-- Index for category filtering
CREATE INDEX IF NOT EXISTS idx_events_category
    ON events(category);
