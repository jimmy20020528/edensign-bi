-- Migration 003: 加 zillow_url 列 + canonical_id 列
-- 跑法:docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < scripts/migrations/003_add_zillow_url_and_canonical_id.sql
--
-- 加这两列的原因:
--   1. zillow_url   — Zillow listing 的 detailUrl(类似已有的 redfin_url)
--                     不重命名 redfin_url 是为了向后兼容,Phase 2 可以再统一
--   2. canonical_id — 跨源 dedup 用的物理房产 fingerprint
--                     hash(归一化 streetAddress + zipcode)
--                     同 canonical_id 的多条 listings = 同一物理房产,Redfin/Zillow 各一条

BEGIN;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS zillow_url TEXT,
    ADD COLUMN IF NOT EXISTS canonical_id TEXT;

-- 给 canonical_id 加索引,跨源 dedup 查询会用
CREATE INDEX IF NOT EXISTS idx_listings_canonical_id ON listings(canonical_id);

-- 给 source 加索引,按源 filter 查询会用
CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source);

COMMIT;

-- 跑完看一下:
-- SELECT column_name FROM information_schema.columns
-- WHERE table_name='listings' AND column_name IN ('zillow_url', 'canonical_id');
