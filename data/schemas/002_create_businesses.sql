-- Hello Desi — Businesses Directory Table
-- Stores Indian businesses across US metro areas

-- Requires pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS businesses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    address TEXT,
    city TEXT NOT NULL,
    state TEXT,
    phone TEXT,
    rating DECIMAL(2,1),
    review_count INTEGER,
    latitude DECIMAL(10,7),
    longitude DECIMAL(10,7),
    source TEXT,  -- 'google_places', 'eknazar', 'sulekha', 'user_submitted'
    source_id TEXT,
    is_featured BOOLEAN DEFAULT false,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Vector similarity search index
CREATE INDEX IF NOT EXISTS idx_businesses_embedding
    ON businesses USING ivfflat (embedding vector_cosine_ops);

-- Composite index for category + city search
CREATE INDEX IF NOT EXISTS idx_businesses_city_category
    ON businesses(city, category);

-- Index for featured businesses
CREATE INDEX IF NOT EXISTS idx_businesses_featured
    ON businesses(is_featured) WHERE is_featured = true;
