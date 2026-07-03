-- Migration 001: 把 primary_style CHECK constraint 扩到加 EmptyRoom + Lived-in
-- 跑法:docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < scripts/migrations/001_add_emptyroom_and_livedin.sql

BEGIN;

-- 1) 找到现有 CHECK constraint 名字(Postgres 自动命名 style_classifications_primary_style_check)
ALTER TABLE style_classifications
    DROP CONSTRAINT IF EXISTS style_classifications_primary_style_check;

-- 2) 加新 CHECK,含 EmptyRoom 和 Lived-in
ALTER TABLE style_classifications
    ADD CONSTRAINT style_classifications_primary_style_check
    CHECK (primary_style IN (
        'Modern Minimalist', 'Scandinavian', 'Mid-Century Modern', 'Industrial',
        'Bohemian', 'Coastal', 'Farmhouse', 'Traditional', 'Transitional',
        'Contemporary', 'Mediterranean', 'Japandi', 'Art Deco', 'French Country',
        'Hampton', 'Vintage/Retro', 'Glam', 'Neoclassical', 'Tropical', 'Rustic',
        'EmptyRoom', 'Lived-in', 'Unclassified'
    ));

-- 3) 验证(应输出 23 行,不该有违反 constraint 的旧数据)
-- 不会真在事务里 SELECT,但 commit 前 Postgres 会自动校验现存行
COMMIT;

-- 4) 跑完看一下分布(独立查询)
-- SELECT primary_style, COUNT(*) FROM style_classifications GROUP BY 1 ORDER BY 2 DESC;
