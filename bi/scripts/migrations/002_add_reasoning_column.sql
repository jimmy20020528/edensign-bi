-- Migration 002: 给 style_classifications 加 reasoning 列
-- Gemini 输出里有 reasoning 字符串(它判断依据),原脚本当 debug print 丢弃了
-- 加这列做 audit trail,以后排查"为什么这条被分到 X 风格"很有用
-- 跑法:docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < scripts/migrations/002_add_reasoning_column.sql

BEGIN;

ALTER TABLE style_classifications
    ADD COLUMN IF NOT EXISTS reasoning TEXT;

COMMIT;
