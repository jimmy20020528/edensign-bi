-- ══════════════════════════════════════════════
-- Edensign BI — Database Schema
-- 5张核心表, 存储所有53个factor
-- 自动在 Docker 首次启动时执行
-- ══════════════════════════════════════════════

-- 启用PostGIS扩展 (地理空间查询能力)
CREATE EXTENSION IF NOT EXISTS postgis;

-- 启用pgcrypto (生成UUID)
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ════════════════════════════════════════
-- 表1: listings (房源数据)
-- 来源: Redfin scraper + RentCast API
-- 每行 = 一个已售/在售房源
-- Factor #1-11 (Property Intrinsics)
-- ════════════════════════════════════════
CREATE TABLE listings (
    listing_id      TEXT PRIMARY KEY,           -- Redfin listing ID, 唯一标识
    address         TEXT NOT NULL,              -- 完整地址
    city            TEXT DEFAULT 'Allston',
    state           TEXT DEFAULT 'MA',
    zipcode         TEXT DEFAULT '02134',

    -- 地理位置 (PostGIS GEOGRAPHY类型, 支持球面距离计算)
    lat             DOUBLE PRECISION,           -- 纬度, 如 42.3537
    lng             DOUBLE PRECISION,           -- 经度, 如 -71.1301
    location        GEOGRAPHY(Point, 4326),     -- PostGIS地理点, SRID 4326 = WGS84坐标系

    -- Property Intrinsics (Factor #1-8)
    sqft            INTEGER,                    -- #1  总面积 (平方英尺)
    bedrooms        SMALLINT,                   -- #2  卧室数
    bathrooms       REAL,                       -- #3  浴室数 (REAL因为有2.5浴室这种)
    lot_size        INTEGER,                    -- #4  地块面积 (sqft)
    year_built      SMALLINT,                   -- #5  建造年份
    property_type   TEXT,                       -- #6  类型: Condo, Single Family, Multi-Family, Townhouse
    hoa_fee         INTEGER,                    -- #7  月度HOA费用 (美元)
    parking         TEXT,                       -- #8  停车: Garage, Driveway, Street, None

    -- Transaction Data (Factor #9-11, 部分为derived)
    list_price      INTEGER,                    -- 挂牌价
    sold_price      INTEGER,                    -- 成交价
    days_on_market  SMALLINT,                   -- #10 上市到售出天数
    listed_date     DATE,                       -- 上市日期
    sold_date       DATE,                       -- 成交日期

    -- Photo URLs (VLM分类用)
    photo_urls      JSONB DEFAULT '[]'::jsonb,  -- JSON数组存所有照片URL

    -- Listing type + rent price (added migration 005)
    listing_type    TEXT DEFAULT 'sold',        -- sold | for_sale | for_rent
    monthly_rent    INTEGER,                    -- asking rent USD/month (for_rent only)

    -- External source URLs (added via migration)
    redfin_url          TEXT,
    zillow_url          TEXT,
    realtor_url         TEXT,
    canonical_id        TEXT,
    data_quality_flag   TEXT,

    -- Metadata
    source          TEXT DEFAULT 'redfin',      -- 数据来源标记
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 在location列上建空间索引, 让ST_DWithin半径查询变快
-- GiST = Generalized Search Tree, PostGIS专用索引类型
CREATE INDEX idx_listings_location ON listings USING GIST (location);

-- 在sold_date上建索引, 按时间筛选用
CREATE INDEX idx_listings_sold_date ON listings (sold_date);

-- 自动从lat/lng生成PostGIS geography点的触发器
-- 这样插入数据时只需要提供lat/lng, location字段自动填充
CREATE OR REPLACE FUNCTION update_location()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lng IS NOT NULL THEN
        NEW.location = ST_SetSRID(ST_MakePoint(NEW.lng, NEW.lat), 4326)::geography;
    END IF;
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_location
    BEFORE INSERT OR UPDATE ON listings
    FOR EACH ROW EXECUTE FUNCTION update_location();


-- ════════════════════════════════════════
-- 表2: style_classifications (风格分类)
-- 来源: Edensign VLM (Qwen2.5-VL)
-- 每行 = 一张照片的分类结果
-- Factor #42-50 (Visual / Staging)
-- ════════════════════════════════════════
CREATE TABLE style_classifications (
    classification_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id          TEXT REFERENCES listings(listing_id) ON DELETE CASCADE,
    photo_url           TEXT,

    -- Factor #42: Primary style
    -- 20 个 pro-staged 风格 + EmptyRoom (vacant baseline) + Lived-in (业主自住非 pro)
    -- + Unclassified (模型 confidence<0.5 兜底)
    primary_style       TEXT CHECK (primary_style IN (
        'Modern Minimalist', 'Scandinavian', 'Mid-Century Modern', 'Industrial',
        'Bohemian', 'Coastal', 'Farmhouse', 'Traditional', 'Transitional',
        'Contemporary', 'Mediterranean', 'Japandi', 'Art Deco', 'French Country',
        'Hampton', 'Vintage/Retro', 'Glam', 'Neoclassical', 'Tropical', 'Rustic',
        'EmptyRoom', 'Lived-in', 'Unclassified'
    )),

    -- Factor #43-49: 次级属性 (每个3个枚举值)
    color_tone          TEXT CHECK (color_tone IN ('warm', 'cool', 'neutral')),
    price_feel          TEXT CHECK (price_feel IN ('budget', 'mid', 'luxury')),
    furniture_density   TEXT CHECK (furniture_density IN ('sparse', 'moderate', 'dense')),
    natural_light       TEXT CHECK (natural_light IN ('low', 'medium', 'high')),
    renovation_level    TEXT CHECK (renovation_level IN ('original', 'partial', 'full')),
    floor_plan          TEXT CHECK (floor_plan IN ('open', 'semi-open', 'closed')),
    kitchen_style       TEXT CHECK (kitchen_style IN ('traditional', 'transitional', 'modern')),
    bathroom_finish     TEXT CHECK (bathroom_finish IN ('basic', 'updated', 'luxury')),

    -- VLM confidence (0.0-1.0)
    confidence          REAL DEFAULT 0.0,

    -- Gemini 判断依据(audit trail,以后可查"为什么这条归这风格")
    reasoning           TEXT,

    classified_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_style_listing ON style_classifications (listing_id);
CREATE INDEX idx_style_primary ON style_classifications (primary_style);


-- ════════════════════════════════════════
-- 表3: census_tracts (人口统计)
-- 来源: US Census ACS 5-Year API (免费)
-- 每行 = 一个census tract (约等于一个街区)
-- Factor #22-33 (Demographics)
-- ════════════════════════════════════════
CREATE TABLE census_tracts (
    tract_id        TEXT PRIMARY KEY,           -- Census GEOID, 如 '25025080100'
    tract_name      TEXT,                       -- 可读名称, 如 'Census Tract 801'
    county_fips     TEXT DEFAULT '025',         -- Suffolk County = 025
    state_fips      TEXT DEFAULT '25',          -- Massachusetts = 25

    -- 地理边界 (PostGIS多边形, 用于判断listing属于哪个tract)
    geometry        GEOGRAPHY(MultiPolygon, 4326),

    -- Factor #22-33: Demographics
    median_income           INTEGER,            -- #22 中位家庭收入 (美元/年)
    median_age              REAL,               -- #23 中位年龄
    pct_families_children   REAL,               -- #24 有未成年子女的家庭百分比
    pct_owner_occupied      REAL,               -- #25 自住房比例 (vs 租房)
    pct_bachelors_plus      REAL,               -- #26 本科及以上学历百分比
    pct_white               REAL,               -- #27a 白人百分比
    pct_black               REAL,               -- #27b 黑人百分比
    pct_asian               REAL,               -- #27c 亚裔百分比
    pct_hispanic            REAL,               -- #27d 西班牙裔百分比
    population_density      REAL,               -- #28 人口密度 (人/平方英里)
    avg_household_size      REAL,               -- #29 平均户均人口
    median_home_value       INTEGER,            -- #30 中位房屋价值
    pct_transit_commute     REAL,               -- #31a 公共交通通勤比例
    pct_bike_walk_commute   REAL,               -- #31b 骑车/步行通勤比例
    pct_english_only        REAL,               -- #32 只说英语的家庭比例
    median_rent             INTEGER,            -- #33 中位月租金

    -- 由demographics推导出的buyer archetype
    -- 在数据入库后由Python逻辑计算
    dominant_archetype      TEXT CHECK (dominant_archetype IN (
        'young_professional', 'young_family', 'established_family',
        'high_income', 'student_budget', 'empty_nester', 'mixed'
    )),

    total_population        INTEGER,
    total_households        INTEGER,

    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tract_geometry ON census_tracts USING GIST (geometry);


-- ════════════════════════════════════════
-- 表4: location_scores (位置质量评分)
-- 来源: Walk Score, Google, GreatSchools, FEMA
-- 每行 = 一个listing的位置评分
-- Factor #12-21 (Location Quality)
-- ════════════════════════════════════════
CREATE TABLE location_scores (
    listing_id              TEXT PRIMARY KEY REFERENCES listings(listing_id) ON DELETE CASCADE,
    tract_id                TEXT REFERENCES census_tracts(tract_id),

    -- Factor #12-14: Walk Score API (0-100)
    walk_score              SMALLINT,           -- #12 步行友好度
    transit_score           SMALLINT,           -- #13 公共交通便利度
    bike_score              SMALLINT,           -- #14 骑行友好度

    -- Factor #15: GreatSchools (1-10, 乘10归一化到0-100)
    school_rating_avg       REAL,               -- #15 周边学校平均评分

    -- Factor #16: Crime (per 1,000 residents)
    crime_rate_per_1000     REAL,               -- #16 每千人犯罪率

    -- Factor #17-19: Google Maps/Places API
    nearest_transit_m       INTEGER,            -- #17 最近地铁/公交站距离(米)
    amenity_count_1km       SMALLINT,           -- #18 1km内餐厅+超市+健身房数量
    nearest_park_m          INTEGER,            -- #19 最近公园距离(米)

    -- Factor #20-21: 计算 + FEMA
    noise_level_db          SMALLINT,           -- #20 噪音水平估算(分贝)
    flood_zone              TEXT,               -- #21 FEMA洪泛区: X(安全), A, AE, VE(高危)

    computed_at             TIMESTAMPTZ DEFAULT NOW()
);


-- ════════════════════════════════════════
-- 表5: market_snapshots (市场动态快照)
-- 来源: Redfin聚合 + FRED API
-- 每行 = 一个区域在某一周的市场状况
-- Factor #34-41 (Market Dynamics)
-- ════════════════════════════════════════
CREATE TABLE market_snapshots (
    snapshot_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    area_name           TEXT NOT NULL,              -- 区域名, 如 'Allston'
    snapshot_date       DATE NOT NULL,              -- 快照日期 (每周一)

    -- Factor #34-38: 从listing数据聚合计算
    active_inventory    INTEGER,            -- #34 当前在售房源数
    months_of_supply    REAL,               -- #35 库存/月销量, <3=卖方市场, >6=买方市场
    absorption_rate     REAL,               -- #36 月度吸收率 (售出/总库存)
    mortgage_rate_30yr  REAL,               -- #37 30年固定利率 (来自FRED)
    yoy_price_change    REAL,               -- #38 同比价格变化百分比

    -- Factor #39-41: 计算得出
    seasonality_index   REAL,               -- #39 季节性指数 (当月/年平均)
    avg_close_days      REAL,               -- #40 平均成交周期 (天)
    price_reduction_pct REAL,               -- #41 降价listing占比

    created_at          TIMESTAMPTZ DEFAULT NOW(),

    -- 防止同一区域同一天重复插入
    UNIQUE (area_name, snapshot_date)
);

CREATE INDEX idx_snapshot_area_date ON market_snapshots (area_name, snapshot_date DESC);


-- ════════════════════════════════════════
-- 视图: listing_full (方便查询的联合视图)
-- JOIN 所有5张表, 一次查到listing的全部53个factor
-- ════════════════════════════════════════
CREATE OR REPLACE VIEW listing_full AS
SELECT
    l.*,

    -- Price derived metrics (Factor #9, #11)
    CASE WHEN l.sqft > 0
         THEN ROUND(l.sold_price::numeric / l.sqft, 2)
         ELSE NULL END                          AS price_per_sqft,
    CASE WHEN l.list_price > 0
         THEN ROUND(l.sold_price::numeric / l.list_price, 4)
         ELSE NULL END                          AS sale_to_list_ratio,

    -- Style (top classification per listing)
    sc.primary_style,
    sc.color_tone,
    sc.price_feel,
    sc.confidence         AS style_confidence,

    -- Location scores
    ls.walk_score,
    ls.transit_score,
    ls.bike_score,
    ls.school_rating_avg,
    ls.crime_rate_per_1000,
    ls.nearest_transit_m,
    ls.amenity_count_1km,

    -- Demographics (from the tract this listing falls in)
    ct.median_income,
    ct.median_age,
    ct.pct_families_children,
    ct.dominant_archetype

FROM listings l
LEFT JOIN LATERAL (
    SELECT * FROM style_classifications s
    WHERE s.listing_id = l.listing_id
    ORDER BY s.confidence DESC
    LIMIT 1
) sc ON true
LEFT JOIN location_scores ls ON ls.listing_id = l.listing_id
LEFT JOIN census_tracts ct ON ST_Contains(
    ct.geometry::geometry,
    l.location::geometry
);
