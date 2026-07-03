"""
Edensign BI — Step 3b: Fetch Listing Photos
=============================================
遍历数据库中的listing, 从Redfin详情页抓取照片URL
更新 listings 表的 photo_urls 字段

用法:
    python scripts/fetch_photos.py
"""

import asyncio
import json
import re

import httpx
import asyncpg


DB_DSN = "postgresql://edensign:edensign_dev@localhost:5432/edensign_bi"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


async def fetch_photos_from_page(client: httpx.AsyncClient, page_url: str) -> list:
    """
    从 Redfin 详情页 HTML 中提取照片 URL

    Redfin 把照片存在 ssl.cdn-redfin.com 上
    URL格式: https://ssl.cdn-redfin.com/photo/NNN/bigphoto/XXX/XXXXXXX.jpg
    页面HTML里有很多这种URL, 用正则提取
    """
    try:
        resp = await client.get(page_url)
        if resp.status_code != 200:
            return []

        html = resp.text
        photos = []

        # 提取 bigphoto (高清大图)
        big = re.findall(
            r'(https://ssl\.cdn-redfin\.com/photo/\d+/bigphoto/[^"\'\\\s]+\.jpg)',
            html
        )
        photos.extend(big)

        # 如果没有bigphoto, 试试其他格式
        if not photos:
            other = re.findall(
                r'(https://ssl\.cdn-redfin\.com/photo/[^"\'\\\s]+\.(?:jpg|jpeg|webp))',
                html
            )
            photos.extend(other)

        # 去重 (同一张图可能出现多次)
        seen = set()
        unique = []
        for p in photos:
            base = p.split("?")[0]
            if base not in seen:
                seen.add(base)
                unique.append(base)

        return unique[:15]

    except Exception as e:
        print(f"    ⚠ 请求失败: {e}")
        return []


async def main():
    print("=" * 50)
    print("Edensign BI — Fetch Listing Photos")
    print("=" * 50)

    conn = await asyncpg.connect(DB_DSN)

    # 读取有 redfin_url 但没照片的 listing
    rows = await conn.fetch("""
        SELECT listing_id, address, redfin_url
        FROM listings
        WHERE redfin_url IS NOT NULL
          AND redfin_url != ''
          AND (photo_urls = '[]'::jsonb OR photo_urls IS NULL)
        ORDER BY listing_id
    """)
    print(f"\n  找到 {len(rows)} 条listing需要抓照片")

    if not rows:
        print("  没有需要处理的listing, 退出")
        await conn.close()
        return

    async with httpx.AsyncClient(
        timeout=20.0, headers=HEADERS, follow_redirects=True
    ) as client:
        success = 0
        failed = 0

        for i, row in enumerate(rows):
            lid = row["listing_id"]
            addr = row["address"]
            redfin_path = row["redfin_url"]

            # 构造完整URL
            page_url = f"https://www.redfin.com{redfin_path}"
            print(f"\n  [{i+1}/{len(rows)}] {addr[:45]}")
            print(f"    → {page_url[:65]}...")

            photos = await fetch_photos_from_page(client, page_url)

            if photos:
                await conn.execute(
                    "UPDATE listings SET photo_urls = $1 WHERE listing_id = $2",
                    json.dumps(photos), lid
                )
                print(f"    ✓ {len(photos)} 张照片")
                success += 1
            else:
                print(f"    ✗ 没找到照片")
                failed += 1

            # 每次请求间隔3秒
            await asyncio.sleep(3)

    await conn.close()
    print(f"\n{'=' * 50}")
    print(f"  完成! 成功: {success}, 失败: {failed}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    asyncio.run(main())
