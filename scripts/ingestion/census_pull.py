"""
Edensign BI — Step 2: Census ACS Data Pull
===========================================
从 US Census ACS 5-Year API 拉取 Allston, MA 的人口统计数据
写入 PostgreSQL census_tracts 表

用法:
    cd /Users/jimmy20020528/Desktop/Edensign/bi
    source .venv/bin/activate
    python scripts/census_pull.py

API文档: https://api.census.gov/data/2023/acs/acs5.html
无需API key也能跑 (每天50次限制, 够用了)
"""

import json
import asyncio
from pathlib import Path

import httpx
import asyncpg


# ══════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════

# 项目根目录 (bi/)
ROOT = Path(__file__).parent.parent

# 数据库连接 (对应docker-compose.yml里的配置)
DB_DSN = "postgresql://edensign:edensign_dev@localhost:5432/edensign_bi"

# Census API 基础URL
# 格式: https://api.census.gov/data/{年份}/{数据集}
CENSUS_BASE = "https://api.census.gov/data/2023/acs/acs5"

# Allston 所在的 census tracts
# Suffolk County (025) in Massachusetts (25)
# Allston 大致对应 tracts: 0801.00 - 0810.00
STATE_FIPS = "25"     # Massachusetts
COUNTY_FIPS = "025"   # Suffolk County


# ══════════════════════════════════════════════
# 加载ACS变量配置
# ══════════════════════════════════════════════

def load_acs_config():
    """
    读取 config/acs_variables.json
    返回: (变量代码列表, 变量代码→字段名映射)
    """
    config_path = ROOT / "config" / "acs_variables.json"
    with open(config_path) as f:
        config = json.load(f)

    variables = config["variables"]
    # 提取所有变量代码, 如 ['B19013_001E', 'B01002_001E', ...]
    var_codes = list(variables.keys())
    # 变量代码 → 数据库字段名映射, 如 {'B19013_001E': 'median_income', ...}
    var_to_field = {code: info["field"] for code, info in variables.items()}

    print(f"  已加载 {len(var_codes)} 个ACS变量")
    return var_codes, var_to_field


# ══════════════════════════════════════════════
# 从Census API拉取数据
# ══════════════════════════════════════════════

async def fetch_census_data(var_codes: list[str]) -> list[dict]:
    """
    调用Census ACS API, 拉取Suffolk County所有tract的数据

    API URL 结构:
      https://api.census.gov/data/2023/acs/acs5
        ?get=NAME,{变量1},{变量2},...
        &for=tract:*              ← 所有tract
        &in=state:25+county:025   ← 在MA的Suffolk County里

    返回: [{tract_id: '25025080100', NAME: 'Census Tract 801', B19013_001E: 75000, ...}, ...]
    """
    # Census API 一次最多请求50个变量, 我们有~25个, 一次搞定
    var_str = ",".join(var_codes)

    url = (
        f"{CENSUS_BASE}"
        f"?get=NAME,{var_str}"
        f"&for=tract:*"
        f"&in=state:{STATE_FIPS}%20county:{COUNTY_FIPS}"
    )

    print(f"  请求URL: {url[:80]}...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.json()

    # API返回格式: 第一行是列名, 后面每行是一个tract的数据
    # [["NAME","B19013_001E",...,"state","county","tract"],
    #  ["Census Tract 801","75000",...,"25","025","080100"],
    #  ...]
    headers = raw[0]
    rows = raw[1:]

    print(f"  收到 {len(rows)} 个census tracts")

    # 转成字典列表, 方便后续处理
    tracts = []
    for row in rows:
        d = dict(zip(headers, row))
        # 拼接完整的GEOID: state(2) + county(3) + tract(6) = 11位
        # 如: '25' + '025' + '080100' = '25025080100'
        d["tract_id"] = d["state"] + d["county"] + d["tract"]
        tracts.append(d)

    return tracts


# ══════════════════════════════════════════════
# 计算衍生指标 + 判断buyer archetype
# ══════════════════════════════════════════════

def safe_float(val):
    """安全转换为float, Census API有时返回null或负数表示无数据"""
    if val is None or val == "null" or val == "":
        return None
    try:
        v = float(val)
        return None if v < 0 else v  # 负数在ACS里表示数据不可用
    except (ValueError, TypeError):
        return None


def safe_int(val):
    """安全转换为int"""
    f = safe_float(val)
    return int(f) if f is not None else None


def safe_pct(numerator, denominator):
    """安全计算百分比, 避免除以0"""
    n = safe_float(numerator)
    d = safe_float(denominator)
    if n is None or d is None or d == 0:
        return None
    return round(n / d * 100, 2)


def determine_archetype(row: dict) -> str:
    """
    根据人口统计数据判断这个tract的主要buyer archetype

    逻辑:
    1. median_age < 28 且 pct_owner < 30% → student_budget
    2. median_age < 35 且 pct_families_children < 20% → young_professional
    3. median_age < 45 且 pct_families_children >= 20% → young_family
    4. median_income > 150000 → high_income
    5. median_age >= 55 → empty_nester
    6. median_age 35-55 且 pct_families_children >= 25% → established_family
    7. 其他 → mixed
    """
    age = row.get("median_age")
    income = row.get("median_income")
    pct_fam = row.get("pct_families_children")
    pct_owner = row.get("pct_owner_occupied")

    if age is None:
        return "mixed"

    if age < 28 and (pct_owner or 0) < 30:
        return "student_budget"
    if age < 35 and (pct_fam or 0) < 20:
        return "young_professional"
    if age < 45 and (pct_fam or 0) >= 20:
        return "young_family"
    if (income or 0) > 150000:
        return "high_income"
    if age >= 55:
        return "empty_nester"
    if 35 <= age < 55 and (pct_fam or 0) >= 25:
        return "established_family"
    return "mixed"


def process_tract(raw: dict, var_to_field: dict) -> dict:
    """
    把Census API返回的原始数据转换成数据库字段

    1. 原始变量 → 字段名映射
    2. 计算百分比 (用原始count / 分母)
    3. 判断buyer archetype
    """
    # 先把所有ACS变量存到中间字典, 用field名做key
    fields = {}
    for var_code, field_name in var_to_field.items():
        fields[field_name] = safe_float(raw.get(var_code))

    # 直接可用的字段 (不需要计算)
    result = {
        "tract_id": raw["tract_id"],
        "tract_name": raw.get("NAME", ""),
        "state_fips": STATE_FIPS,
        "county_fips": COUNTY_FIPS,
        "median_income": safe_int(fields.get("median_income")),
        "median_age": fields.get("median_age"),
        "avg_household_size": fields.get("avg_household_size"),
        "median_home_value": safe_int(fields.get("median_home_value")),
        "median_rent": safe_int(fields.get("median_rent")),
        "total_population": safe_int(fields.get("total_population")),
        "total_households": safe_int(fields.get("_total_households")),
    }

    # 计算百分比字段
    pop = fields.get("total_population")
    hh = fields.get("_total_households")
    occ = fields.get("_total_occupied")
    pop25 = fields.get("_pop_25_plus")
    commute = fields.get("_commute_total")
    lang = fields.get("_language_total")

    # Factor #24: 有孩子的家庭占比
    result["pct_families_children"] = safe_pct(
        fields.get("_married_with_children"), hh
    )

    # Factor #25: 自住房占比
    result["pct_owner_occupied"] = safe_pct(
        fields.get("_owner_occupied"), occ
    )

    # Factor #26: 本科+以上学历占比
    bachelors_plus = sum(filter(None, [
        fields.get("_bachelors"),
        fields.get("_masters"),
        fields.get("_professional"),
        fields.get("_doctorate"),
    ]))
    result["pct_bachelors_plus"] = safe_pct(bachelors_plus, pop25) if pop25 else None

    # Factor #27a-d: 种族占比
    result["pct_white"] = safe_pct(fields.get("_white_alone"), pop)
    result["pct_black"] = safe_pct(fields.get("_black_alone"), pop)
    result["pct_asian"] = safe_pct(fields.get("_asian_alone"), pop)
    result["pct_hispanic"] = safe_pct(fields.get("_hispanic"), pop)

    # Factor #31a: 公交通勤占比
    result["pct_transit_commute"] = safe_pct(
        fields.get("_commute_transit"), commute
    )

    # Factor #31b: 骑车+步行通勤占比
    bike_walk = sum(filter(None, [
        fields.get("_commute_bike"),
        fields.get("_commute_walk"),
    ]))
    result["pct_bike_walk_commute"] = safe_pct(bike_walk, commute) if commute else None

    # Factor #32: 只说英语占比
    result["pct_english_only"] = safe_pct(
        fields.get("_english_only"), lang
    )

    # Factor #28: 人口密度 (先留空, 需要tract面积数据才能算)
    result["population_density"] = None

    # 判断buyer archetype
    result["dominant_archetype"] = determine_archetype(result)

    return result


# ══════════════════════════════════════════════
# 写入数据库
# ══════════════════════════════════════════════

async def insert_tracts(tracts: list[dict]):
    """
    用 asyncpg 批量写入 census_tracts 表

    asyncpg 是 PostgreSQL 的异步驱动, 比 psycopg2 快
    这里用 executemany 做批量插入
    """
    conn = await asyncpg.connect(DB_DSN)

    # 定义所有要插入的字段 (跟表结构一致)
    fields = [
        "tract_id", "tract_name", "state_fips", "county_fips",
        "median_income", "median_age", "pct_families_children",
        "pct_owner_occupied", "pct_bachelors_plus",
        "pct_white", "pct_black", "pct_asian", "pct_hispanic",
        "population_density", "avg_household_size",
        "median_home_value", "pct_transit_commute",
        "pct_bike_walk_commute", "pct_english_only", "median_rent",
        "dominant_archetype", "total_population", "total_households",
    ]

    # 构建 INSERT ... ON CONFLICT 语句
    # ON CONFLICT (tract_id) DO UPDATE = 如果tract已存在就更新, 不会报错
    cols = ", ".join(fields)
    # $1, $2, $3, ... 是 asyncpg 的参数占位符
    vals = ", ".join(f"${i+1}" for i in range(len(fields)))
    # SET 子句: 更新所有字段 (除了tract_id)
    updates = ", ".join(
        f"{f} = EXCLUDED.{f}" for f in fields if f != "tract_id"
    )

    sql = f"""
        INSERT INTO census_tracts ({cols})
        VALUES ({vals})
        ON CONFLICT (tract_id) DO UPDATE SET {updates}
    """

    # 把每个tract字典转成有序元组 (asyncpg要求)
    records = []
    for t in tracts:
        records.append(tuple(t.get(f) for f in fields))

    await conn.executemany(sql, records)
    await conn.close()

    print(f"  已写入 {len(records)} 条tract数据到数据库")


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

async def main():
    print("=" * 50)
    print("Edensign BI — Census ACS Data Pull")
    print("=" * 50)

    # 1. 加载变量配置
    print("\n[1/4] 加载ACS变量配置...")
    var_codes, var_to_field = load_acs_config()

    # 2. 调用Census API
    print("\n[2/4] 调用Census ACS API...")
    raw_tracts = await fetch_census_data(var_codes)

    # 3. 处理数据: 计算百分比, 判断archetype
    print("\n[3/4] 处理数据...")
    processed = []
    for raw in raw_tracts:
        t = process_tract(raw, var_to_field)
        processed.append(t)
        # 打印每个tract的摘要
        arch = t["dominant_archetype"]
        age = t["median_age"] or "N/A"
        income = t["median_income"] or "N/A"
        print(f"    {t['tract_name']:30s} | age={age:>5} | income=${income:>7} | → {arch}")

    # 4. 写入数据库
    print("\n[4/4] 写入数据库...")
    await insert_tracts(processed)

    print("\n✅ Done! Census data for Suffolk County loaded.")
    print(f"   Total tracts: {len(processed)}")

    # 找出Allston的tracts (大约080100-081000)
    allston = [t for t in processed if "080" in t["tract_id"][5:11] or "081" in t["tract_id"][5:11]]
    if allston:
        print(f"   Allston area tracts: ~{len(allston)}")


if __name__ == "__main__":
    asyncio.run(main())
