# ============================================================
# 【这个文件干什么】
#   从天聚数行（tianapi.com）抓真实新闻，写进 news 表。
#
# 【用法】
#   python scripts/fetch_news.py                抓所有"已申请"的接口，每个 1 页
#   python scripts/fetch_news.py --pages 2      每个接口抓 2 页（每页 50 条）
#   python scripts/fetch_news.py --dry-run      只打印抓到了什么，不写数据库
#   python scripts/fetch_news.py --replace      先清空库再灌（把测试数据换成真新闻）
#
#   ⚠️ 默认行为是"只新增、不删除"（按标题去重，重复的跳过）。
#      所以如果库里已经有 seed_data.py 造的测试数据，跑完会变成"混合"状态。
#      想彻底换成真实数据，就加 --replace。
#
# 【注意】
#   ① 必须先在 .env 里配好 NEWS_API_KEY
#   ② 必须先去天聚数行"申请"对应的接口，否则会报 code=160（未申请该API）。
#      没申请的接口脚本会自动跳过并告诉你，不会中断。
#   ③ 天聚数行的新闻接口**不返回正文**（官方说涉及版权），
#      所以正文位置存的是它给的 description（新闻描述）。
#      这不影响工具查询 —— 4 个工具查的是分类、标题、浏览量、时间。
#   ④ 免费会员每天 100 次调用，抓多少页自己心里有数（1 页 = 1 次调用）。
# ============================================================

import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.db_conf import DB_URL
from config.news_conf import (
    NEWS_API_BASE,
    NEWS_API_KEY,
    PAGE_SIZE,
    SOURCE_CATEGORY_MAP,
)
from models.news import News

# 请求超时（秒）
REQUEST_TIMEOUT = 20


def pick_first(item: dict, keys: list) -> str:
    """
    从几个"可能的字段名"里，取第一个有值的。

    为什么要这么写：天聚数行不同接口返回的字段名略有差异
    （有的接口叫 title，有的叫 titletext），这样写能兼容。
    这也是"对接第三方接口"时的常见做法 —— 不假设对方一定按你想的来。
    """
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)

    return ""


def parse_publish_time(text: str) -> datetime:
    """
    把接口返回的时间字符串转成 datetime 对象。

    接口给的时间格式不统一（可能是 2026-09-18 10:30，也可能是 2026-09-18），
    所以这里挨个格式试一遍，都不匹配就用"现在"顶上，保证不会因为时间崩溃。
    """
    if not text:
        return datetime.now()

    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return datetime.now()


def parse_item(item: dict, category: str) -> dict:
    """
    把接口返回的一条新闻，整理成"入库需要的字段"。

    返回字典而不是直接造 News 对象，是为了方便 --dry-run 时只看不存。
    """
    title = pick_first(item, ["title", "titletext", "name"])
    description = pick_first(item, ["description", "digest", "content"])
    source = pick_first(item, ["source", "media", "author"])
    ctime = pick_first(item, ["ctime", "pubDate", "time"])

    return {
        "title": title,
        "content": description,
        "category": category,
        "author": source,
        "source": source,
        "views": 0,                                   # 接口不给浏览量，先按 0 存
        "publish_time": parse_publish_time(ctime),
    }


async def fetch_one_api(
    client: httpx.AsyncClient,
    api_name: str,
    category: str,
    pages: int,
) -> list:
    """
    抓一个接口的新闻。

    返回：解析好的新闻字典列表（可能为空）
    如果这个接口没申请或者出错了，会打印原因并返回空列表。
    """
    url = f"{NEWS_API_BASE}/{api_name}/index"
    collected = []

    for page in range(1, pages + 1):
        params = {
            "key": NEWS_API_KEY,
            "num": PAGE_SIZE,
            "page": page,
        }

        try:
            resp = await client.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except Exception as e:
            print(f"    [跳过] {api_name} 第 {page} 页请求失败：{type(e).__name__}: {e}")
            break

        # 接口返回的 HTTP 状态码一般是 200，真正的结果在 body 的 code 字段里
        try:
            data = resp.json()
        except Exception:
            print(f"    [跳过] {api_name} 第 {page} 页返回的不是 JSON")
            break

        code = data.get("code")
        message = data.get("msg")

        if code != 200:
            # 常见的几种：
            #   160 当前KEY未申请该API   → 去天聚数行接口页面点"申请"
            #   150 次数用完            → 明天再来
            #   230 key 无效            → 检查 .env
            print(f"    [跳过] {api_name} 第 {page} 页失败：code={code} msg={message}")
            break

        newslist = data.get("newslist") or []

        if not newslist:
            break

        for item in newslist:
            collected.append(parse_item(item, category))

    return collected


async def main():
    parser = argparse.ArgumentParser(description="从天聚数行抓真实新闻进库")
    parser.add_argument("--pages", type=int, default=1, help="每个接口抓几页（每页 50 条）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写数据库")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="先清空 news 表再灌（把本地测试数据换成真实新闻时用）",
    )
    args = parser.parse_args()

    if not NEWS_API_KEY:
        print("[失败] 没有读到 NEWS_API_KEY —— 请先在 .env 里填写天聚数行的 APIKEY")
        return

    if not DB_URL:
        print("[失败] 没有读到 DB_URL —— 请先在 .env 里填写数据库连接串")
        return

    print(f"开始抓取，共 {len(SOURCE_CATEGORY_MAP)} 个接口，每个抓 {args.pages} 页")
    print("")

    all_items = []

    async with httpx.AsyncClient() as client:
        for api_name, category in SOURCE_CATEGORY_MAP.items():
            print(f"  [{category}] {api_name} 接口 …")

            items = await fetch_one_api(client, api_name, category, args.pages)

            if items:
                print(f"    [成功] 抓到 {len(items)} 条")
                all_items.extend(items)

    print("")
    print(f"抓取结束，共拿到 {len(all_items)} 条")

    if not all_items:
        print("")
        print("一条都没抓到。最可能的原因：")
        print("  ① 那些接口还没在天聚数行'申请'（登录后进接口页面点申请）")
        print("  ② 今天 100 次免费调用用完了")
        print("  ③ .env 里的 NEWS_API_KEY 填错了")
        print("")
        print("可以先跑 python scripts/seed_data.py 用本地测试数据把项目跑起来。")
        return

    # ---- dry-run：只看不存 ----
    if args.dry_run:
        print("")
        print("=== --dry-run 模式，只预览前 3 条，不写数据库 ===")
        for item in all_items[:3]:
            print("  ", item)
        return

    # ---- 写数据库 ----
    engine = create_async_engine(DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    inserted = 0
    skipped = 0

    async with session_factory() as session:
        # 如果传了 --replace，先把老数据全删掉。
        # ⚠️ 这一步不可恢复，所以只有显式加 --replace 才会执行 ——
        #   兜底默认是"只新增、不删除"，避免误操作把库清空。
        if args.replace:
            await session.execute(delete(News))
            print("  [--replace] 已清空原有数据（准备换成真实新闻）")

        # 把库里已有的标题查出来，用来去重
        title_stmt = select(News.title)
        result = await session.execute(title_stmt)

        existing_titles = set()
        for row in result.all():
            existing_titles.add(row[0])

        for item in all_items:
            if item["title"] in existing_titles:
                skipped = skipped + 1
                continue

            if not item["title"]:
                skipped = skipped + 1
                continue

            session.add(News(**item))
            existing_titles.add(item["title"])
            inserted = inserted + 1

        await session.commit()

    await engine.dispose()

    print("")
    print(f"入库完成：新增 {inserted} 条，跳过重复/空标题 {skipped} 条")
    print("")
    print("可以这样验证：")
    print("  SELECT category, COUNT(*) FROM news_agent.news GROUP BY category;")


if __name__ == "__main__":
    asyncio.run(main())
