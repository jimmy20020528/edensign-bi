-- Migration 004: 加 realtor_url 列
-- 跑法:docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < scripts/migrations/004_add_realtor_url.sql
--
-- 加这列的原因:
--   Realtor.com 数据通过 RapidAPI realty-in-us 拉,有 detail page URL
--   类似已有的 redfin_url / zillow_url

BEGIN;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS realtor_url TEXT;

COMMIT;
