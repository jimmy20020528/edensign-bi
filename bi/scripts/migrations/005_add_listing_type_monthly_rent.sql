-- Migration 005: add listing_type and monthly_rent
-- listing_type distinguishes sold / for_sale / for_rent
-- monthly_rent stores asking rent (USD/month) for for_rent listings

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS listing_type TEXT DEFAULT 'sold',
    ADD COLUMN IF NOT EXISTS monthly_rent INTEGER;

CREATE INDEX IF NOT EXISTS idx_listings_type ON listings (listing_type);
