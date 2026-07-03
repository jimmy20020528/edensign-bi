"""
Edensign BI — Step 4: 用 Gemini 2.5 Pro 给 listing 首图做风格分类

流程:
  1. 从数据库读出所有有 photo_urls 的 listing
  2. 取每条 listing 的首图(photo_urls[0])
  3. 下载图片 bytes → 喂给 Gemini 2.5 Pro
  4. 用 Pydantic schema 强制模型输出结构化 JSON(enum 受约束)
  5. 写入 style_classifications 表

MVP 策略:每条 listing 只分类首图。后续可扩展为多房间分类。
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal, Optional

import asyncpg
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# 从项目根目录的 .env 加载环境变量(在读 os.environ 之前)
load_dotenv(Path(__file__).parent.parent / ".env")

# ══════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════

DB_DSN = "postgresql://edensign:edensign_dev@localhost:5432/edensign_bi"
GEMINI_MODEL = "gemini-2.5-pro"
TAXONOMY_PATH = Path(__file__).parent.parent / "config" / "style_taxonomy.json"
REQUEST_DELAY_SEC = 1.0  # 每次调用间隔,避免撞 RPM 限制
MAX_PHOTOS_PER_LISTING = 12  # 配合分层抽样从 8 升到 12,信号更全
GEMINI_RETRY_MAX = 3       # 503/429 时重试次数
GEMINI_RETRY_BASE_SEC = 4  # 指数退避初始等待秒

# 从环境变量读 API key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY or GEMINI_API_KEY.startswith("your_"):
    print("❌ 错误: 请先在 .env 中设置 GEMINI_API_KEY")
    print("   去 https://aistudio.google.com/app/apikey 申请")
    print("   然后 export GEMINI_API_KEY=xxx  或写入 .env")
    sys.exit(1)


# ══════════════════════════════════════════════
# 结构化输出 Schema(Pydantic)
# Literal 限定枚举值,Gemini responseSchema 会强制遵守
# 这些值必须与 schema.sql 里 CHECK constraint 完全一致
# ══════════════════════════════════════════════

PrimaryStyle = Literal[
    "Modern Minimalist", "Scandinavian", "Mid-Century Modern", "Industrial",
    "Bohemian", "Coastal", "Farmhouse", "Traditional", "Transitional",
    "Contemporary", "Mediterranean", "Japandi", "Art Deco", "French Country",
    "Hampton", "Vintage/Retro", "Glam", "Neoclassical", "Tropical", "Rustic",
    "EmptyRoom",       # 空房,no staging,BI 的 vacant baseline
    "Lived-in",        # 业主/租客自住,non-pro staging,outcome 显著差于 pro
    "Unclassified",    # 模型 confidence<0.5 兜底,BI 应排除
]


class StyleClassification(BaseModel):
    """Gemini 输出的结构化结果,每个字段都对应数据库一列。"""
    primary_style: PrimaryStyle = Field(
        description="主风格,20 风格 + EmptyRoom + Lived-in + Unclassified"
    )
    color_tone: Literal["warm", "cool", "neutral"]
    price_feel: Literal["budget", "mid", "luxury"]
    furniture_density: Literal["sparse", "moderate", "dense"]
    natural_light: Literal["low", "medium", "high"]
    renovation_level: Literal["original", "partial", "full"]
    floor_plan: Literal["open", "semi-open", "closed"]
    kitchen_style: Literal["traditional", "transitional", "modern"] = Field(
        description="若非厨房照片,基于整体装修水平推断"
    )
    bathroom_finish: Literal["basic", "updated", "luxury"] = Field(
        description="若非浴室照片,基于整体装修水平推断"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="模型自评置信度 0-1")
    reasoning: str = Field(description="一两句话解释判断依据,便于 debug")


# ══════════════════════════════════════════════
# Prompt 组装
# ══════════════════════════════════════════════

def build_prompt(taxonomy: dict) -> str:
    """把 taxonomy JSON 展开进 prompt,让模型知道每个枚举值的含义。"""
    styles = taxonomy["primary_styles"]
    subs = taxonomy["sub_attributes"]

    style_lines = "\n".join(f"  - {k}: {v}" for k, v in styles.items())

    sub_blocks = []
    for attr, options in subs.items():
        opts = "; ".join(f"{k}({v})" for k, v in options.items())
        sub_blocks.append(f"  - {attr}: {opts}")
    sub_text = "\n".join(sub_blocks)

    return f"""你是房地产室内 staging 风格专家。以下是**同一套房源的多张照片**。请基于其中的**室内照片**综合判断这套房子的整体 staging 风格,输出一个结构化的风格分类 JSON。

# 主风格(primary_style)— 必须从以下 21 选一:
{style_lines}

# 子属性(必须从给定枚举值里选):
{sub_text}

# 关键判断规则(必须严格按此决策树):

## 第一步 — 看主体状态,3 选 1
- (a) 室内多数照片基本无家具/装饰 → **EmptyRoom**(confidence 高)
- (b) 室内有家具但带明显**居住痕迹**(下面 lived-in 信号清单)→ **Lived-in**
- (c) 室内有家具且看着像 stager 精心布置的 catalog 样板 → **进入第二步选 20 风格之一**

## Lived-in 判别信号(出现 ≥2 个就该归 Lived-in,而非 pro-staged 风格)
- 个人物品: 全家福照片、孩子玩具、宗教/文化摆件、私人证书/海报、宠物用品
- 杂物: 衣物散落、电子产品、餐具、未收纳的日用品、桌面有遥控/账单/水杯
- 不成套家具: 看起来是逐年攒的混搭,而非 stager 一次性配套
- 磨损痕迹: 老旧沙发、墙面有刮痕/钉孔、地板磨损、家具划痕
- 冰箱/橱柜内可见日用品(若照片露出来)
- 墙上贴海报 / 通知 / 日历(staging 不会这样)
- 床上没整齐铺好,枕头不对称,被子不平整

## Pro-Staged 信号(满足这些才往 20 风格归)
- 配色协调、抱枕/窗帘/艺术画相互呼应
- 家具看着是同一批采买的成套家具(stager 通常用配套款)
- 桌面/橱柜表面**几乎没有个人物品**,只有几件 designer 摆设
- "杂志感" / "样板间感"
- 床品笔挺,枕头对称、按数量摆放
- 艺术品中性、不出现私人内容

## Unclassified(很少用,仅兜底)
- 仅当所有照片**严重模糊 / 全是外观航拍 / 灯光差到根本看不清室内** 才归此
- confidence < 0.5
- 否则总能从上面 3 类里选一个

# 其它规则
1. **只基于室内照片判断** — 外观、街景、建筑立面、航拍、地图照片**完全忽略**
2. 多张照片应该给更强的 signal,confidence 应该更高
3. EmptyRoom 和 Lived-in 互斥;Lived-in 和 20 风格互斥
4. 如果一半空房一半有家具/居住:看主导 — 空房 ≥60% → EmptyRoom;否则按有家具部分判
5. kitchen_style / bathroom_finish 基于相关照片;无相关照片就推断
6. reasoning 必须写: 看了 N 张内景照、其中 X 张空房 / Y 张 lived-in / Z 张 pro-staged,以及具体看到的关键证据

请严格按 schema 输出 JSON。"""


# ══════════════════════════════════════════════
# 单张图片分类
# ══════════════════════════════════════════════

async def download_image(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    """下载图片 bytes。Redfin CDN 有时慢,加 15s 超时。"""
    try:
        r = await client.get(url, timeout=15.0, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"    ✗ 图片下载失败: {e}")
        return None


def stratified_sample(urls: list, n: int) -> list:
    """
    分层抽样: 从全量 urls 里等间隔取 n 张,而非简单取前 n 张。
    避免 listing 前 X 张是空房、后 Y 张是 staged 的排版偏差。
    """
    if len(urls) <= n:
        return urls
    if n <= 1:
        return urls[:1]
    step = (len(urls) - 1) / (n - 1)
    indices = [round(i * step) for i in range(n)]
    return [urls[i] for i in indices]


def classify_listing(
    gemini: genai.Client, imgs: list, prompt: str
) -> Optional[StyleClassification]:
    """
    把 listing 的多张照片一起喂给 Gemini 2.5 Pro。
    503/429/RESOURCE_EXHAUSTED 走指数退避重试 4s → 8s → 16s,其它错误立即返 None。
    """
    last_err = None
    for attempt in range(1, GEMINI_RETRY_MAX + 1):
        try:
            parts = [
                types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in imgs
            ]
            parts.append(prompt)
            resp = gemini.models.generate_content(
                model=GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=StyleClassification,
                    temperature=0.2,  # 低 temp 让分类更稳定
                ),
            )
            return resp.parsed
        except Exception as e:
            err_str = str(e)
            last_err = e
            is_retryable = (
                "503" in err_str
                or "UNAVAILABLE" in err_str
                or "429" in err_str
                or "RESOURCE_EXHAUSTED" in err_str
            )
            if is_retryable and attempt < GEMINI_RETRY_MAX:
                wait = GEMINI_RETRY_BASE_SEC * (2 ** (attempt - 1))
                print(
                    f"    ⏳ 第 {attempt}/{GEMINI_RETRY_MAX} 次失败 "
                    f"({err_str[:60]}), 等 {wait}s 重试"
                )
                time.sleep(wait)
                continue
            print(f"    ✗ Gemini 调用失败: {err_str[:120]}")
            return None
    print(f"    ✗ 重试 {GEMINI_RETRY_MAX} 次仍失败: {last_err}")
    return None


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

async def main(past_year_only: bool = False):
    print("=" * 50)
    print("Edensign BI — Step 4: Gemini VLM 风格分类")
    print("=" * 50)

    # 加载 taxonomy
    taxonomy = json.loads(TAXONOMY_PATH.read_text())
    prompt = build_prompt(taxonomy)
    print(f"[1/3] 加载 taxonomy... {len(taxonomy['primary_styles'])} 个主风格")

    # 初始化 Gemini client
    gemini = genai.Client(api_key=GEMINI_API_KEY)
    print(f"      Gemini client 初始化 (模型: {GEMINI_MODEL})")

    # 从数据库读 listing(拉整个 photo_urls 数组,不止首图)
    # 过滤规则:
    #   1. 自己没分类
    #   2. 跨源 dedup: 跳过 canonical_id 指向已分类 Redfin sibling 的行
    #      (典型: Realtor 行 canonical_id='rf_<mls_id>',Redfin 那条已分过)
    #   3. 质量门: Realtor 孤儿行(无 canonical_id 且 list_price 为 NULL)跳过
    #      这类是 Realtor 端同地址另一时间/整栋的脏记录,sqft/photos 跟主记录不匹配
    conn = await asyncpg.connect(DB_DSN)
    where_clause = """
        jsonb_array_length(l.photo_urls) > 0
        AND sc.classification_id IS NULL
        AND NOT EXISTS (
            SELECT 1 FROM style_classifications sc2
            WHERE sc2.listing_id = l.canonical_id
              AND l.canonical_id IS NOT NULL
              AND l.canonical_id <> l.listing_id
        )
        AND NOT (
            l.source = 'realtor'
            AND l.canonical_id IS NULL
            AND l.list_price IS NULL
        )
    """
    if past_year_only:
        where_clause += """
        AND (l.sold_date >= CURRENT_DATE - INTERVAL '365 days'
             OR l.sold_date IS NULL)
        """
    rows = await conn.fetch(f"""
        SELECT l.listing_id, l.address, l.photo_urls
        FROM listings l
        LEFT JOIN style_classifications sc ON sc.listing_id = l.listing_id
        WHERE {where_clause}
        ORDER BY l.listing_id
    """)
    scope = "过去 1 年 sold + active" if past_year_only else "全库"
    print(f"[2/3] 待分类 listing: {len(rows)} 条 ({scope},已分类的跳过)")
    if not rows:
        print("      全部已分类,无需重跑")
        await conn.close()
        return

    # 逐条分类:每条下载多张照片,一次 API 调用综合判断
    print(f"[3/3] 开始分类(每条最多 {MAX_PHOTOS_PER_LISTING} 张图,间隔 {REQUEST_DELAY_SEC}s)...")
    http = httpx.AsyncClient()
    success, failed = 0, 0
    style_counts = {}

    for i, row in enumerate(rows, 1):
        listing_id = row["listing_id"]
        address = row["address"]
        photo_urls = json.loads(row["photo_urls"]) if isinstance(row["photo_urls"], str) else row["photo_urls"]
        # 分层抽样替代顺序前 N(避免前 X 空房后 Y 有家具的排版偏差)
        urls = stratified_sample(list(photo_urls), MAX_PHOTOS_PER_LISTING)
        print(
            f"  [{i}/{len(rows)}] {address[:50]}  "
            f"分层抽 {len(urls)}/{len(photo_urls)} 张图"
        )

        # 并发下载所有照片
        imgs = await asyncio.gather(*(download_image(http, u) for u in urls))
        imgs = [b for b in imgs if b]  # 过滤下载失败的
        if not imgs:
            print(f"    ✗ 全部下载失败")
            failed += 1
            continue

        result = classify_listing(gemini, imgs, prompt)
        if not result:
            failed += 1
            continue

        # 写入数据库(photo_url 记首图 URL 作为引用)
        await conn.execute("""
            INSERT INTO style_classifications (
                listing_id, photo_url, primary_style, color_tone, price_feel,
                furniture_density, natural_light, renovation_level,
                floor_plan, kitchen_style, bathroom_finish, confidence
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """,
            listing_id, urls[0], result.primary_style, result.color_tone,
            result.price_feel, result.furniture_density, result.natural_light,
            result.renovation_level, result.floor_plan, result.kitchen_style,
            result.bathroom_finish, result.confidence,
        )

        style_counts[result.primary_style] = style_counts.get(result.primary_style, 0) + 1
        print(f"      → {result.primary_style} ({result.color_tone}/{result.price_feel}) "
              f"conf={result.confidence:.2f}")
        print(f"      {result.reasoning[:100]}")
        success += 1

        await asyncio.sleep(REQUEST_DELAY_SEC)

    await http.aclose()
    await conn.close()

    # 汇总
    print("=" * 50)
    print(f"✅ 完成! 成功: {success}, 失败: {failed}")
    if style_counts:
        print("\n📊 风格分布:")
        for style, n in sorted(style_counts.items(), key=lambda x: -x[1]):
            print(f"   {style}: {n}")
    print("=" * 50)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument(
        "--past-year-only",
        action="store_true",
        help="只分类过去 1 年 sold 或当前 active 的 listing(George 限定的训练范围)",
    )
    args = p.parse_args()
    asyncio.run(main(past_year_only=args.past_year_only))
