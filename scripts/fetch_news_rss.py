# ============================================================
# 【这个文件干什么】
#   从公开的 RSS 源抓真实新闻，写进 news 表。
#
#   它和 scripts/fetch_news.py 的区别：
#     fetch_news.py      走"天聚数行 API"—— 要注册、要实名、要逐个申请接口、要等审核
#     fetch_news_rss.py  走"公开 RSS"     —— 不用注册、不用审核，拿到就能抓
#   两个脚本二选一都行，也可以都跑（按标题去重，不会重复入库）。
#
# 【用法】
#   python scripts/fetch_news_rss.py                 抓所有源，每个源取前 30 条
#   python scripts/fetch_news_rss.py --per-feed 50   每个源取前 50 条
#   python scripts/fetch_news_rss.py --dry-run       只打印抓到了什么，不写数据库
#   python scripts/fetch_news_rss.py --replace       先清空 news 表再灌（换成真实数据用）
#
# 【抓哪几个源、对应哪个分类】
#   都在 config/news_conf.py 的 RSS_SOURCES 里，想加减源只改那个文件。
#
# ⚠️ 【必须知道的三个事实】
#   ① RSS 只给"标题 + 摘要 + 时间 + 链接"，**不给正文全文**，
#      所以正文位置存的是它的摘要（description）。对工具查询没影响 ——
#      工具查的是分类、标题、浏览量、时间。
#   ② RSS **不给浏览量**（那是只有平台自己才有的业务数据），
#      所以浏览量是按标题算出来的**模拟值**。见 fake_views() 的说明。
#   ③ RSS 源可能随时失效。某个源挂了不会影响其他源，脚本会打印原因并继续。
# ============================================================

import argparse
import asyncio
import hashlib
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.db_conf import DB_URL
from config.news_conf import RSS_HEADERS, RSS_SOURCES
from models.news import News

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 北京时间（用来把 RSS 里带时区的时间统一成本地时间）
BEIJING = timezone(timedelta(hours=8))

# 从域名猜"来源媒体名"，用来填 news.source 字段
SITE_NAMES = {
    "chinanews.com.cn": "中新网",
    "people.com.cn": "人民网",
    "ithome.com": "IT之家",
    "leiphone.com": "雷锋网",
    "sspai.com": "少数派",
}


def clean_html(text: str) -> str:
    """
    把 RSS 摘要里的 HTML 清理成纯文本。

    RSS 的 description 通常长这样：
        <p>据新华社报道，<strong>某某</strong>表示……</p>
    里面带着标签和 &amp; 这种转义字符，不清理的话存进库里很难看。

    【用到的两个东西（都是标准库）】
      re.sub(r"<[^>]+>", "", text)  —— 用正则把 <...> 形式的标签删掉
      html.unescape(text)           —— 把 &amp; &quot; 这种还原成 & "
    """
    if not text:
        return ""

    # 先把 <br> <p> 这类换行标签换成空格，避免删标签时把两句话粘在一起
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", " ", text, flags=re.IGNORECASE)

    # 删掉其余所有标签
    text = re.sub(r"<[^>]+>", "", text)

    # 还原 HTML 转义字符
    text = html.unescape(text)

    # 把连续空白（换行、多个空格）压成一个空格
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_time(text: str) -> datetime:
    """
    把 RSS 给的时间字符串转成 datetime。

    不同源给的时间格式差别很大，常见三种：
        Fri, 18 Sep 2026 14:06:00 +0800     ← RFC 822 格式（最标准）
        2025-06-05                          ← 只有日期
        2026-09-18T14:06:00+08:00           ← ISO 格式
    所以这里挨个试，都不成功就用"现在"顶上 —— 保证不会因为时间格式崩溃。

    【用到的东西（标准库）】
      email.utils.parsedate_to_datetime —— 专门解析 RFC 822 那种 "Fri, 18 Sep ..." 格式
    """
    if not text:
        return datetime.now()

    text = text.strip()

    # ① 试 RFC 822 格式
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        if dt is not None:
            # 带时区的话，统一换成北京时间再去掉时区标记
            # （数据库里的字段是不带时区的 DATETIME）
            if dt.tzinfo is not None:
                dt = dt.astimezone(BEIJING).replace(tzinfo=None)
            return dt
    except Exception:
        pass

    # ② 试几种常见的字符串格式
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    # ③ 试 ISO 格式（fromisoformat 能处理 2026-09-18T14:06:00+08:00）
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(BEIJING).replace(tzinfo=None)
        return dt
    except ValueError:
        pass

    return datetime.now()


def fake_views(title: str) -> int:
    """
    根据标题算一个"稳定的模拟浏览量"。

    ⚠️ 先说清楚：**这不是真实浏览量，是模拟值。**
       公开的 RSS 源不可能提供"浏览量"这种只有平台自己才有的业务数据。

    为什么要这么处理：
      项目里有"浏览量排行"这个功能（工具③ get_top_news），
      如果所有新闻的浏览量都是 0，那个功能就废了、演示时也看不出来。
      所以按标题算一个 1000~19999 之间的数，让排行榜有东西可排。

    为什么用"标题"当种子，而不是用随机数：
      如果用随机数，每次重跑数据，同一篇新闻的浏览量都会变，
      重灌一次排行榜就换一批 —— 看起来像 bug，没法解释。
      用标题做种子，同一篇新闻每次算出来都一样，稳定。
      （这个手法叫"确定性伪随机"，面试聊到数据处理可以提一句。）

    【用到的东西】
      hashlib.md5 —— 【标准库】算摘要；这里拿它当一个稳定的"散列函数"用
      int(x, 16)  —— 【内置】把十六进制字符串转成整数
    """
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()

    # 取前 8 位十六进制转成整数，范围落在 1000~19999
    number = int(digest[:8], 16)

    return 1000 + number % 19000


def guess_source(url: str) -> str:
    """从 RSS 地址的域名猜来源媒体名（猜不到就返回域名本身）"""
    for domain, name in SITE_NAMES.items():
        if domain in url:
            return name

    # 退而求其次：把域名抠出来当来源名
    match = re.search(r"https?://([^/]+)/", url)
    if match:
        return match.group(1)

    return "未知来源"


def parse_entry(item, category: str, feed_url: str) -> dict:
    """
    把 RSS 里的一条 <item> 整理成"入库需要的字段"。

    RSS 的结构（每个 <item> 是一篇新闻）：
        <item>
          <title>标题</title>
          <link>链接</link>
          <description>摘要</description>
          <pubDate>发布时间</pubDate>
        </item>
    """

    def pick(names):
        """按候选标签名挨个找，找到第一个有内容的就返回"""
        for name in names:
            node = item.find(name)
            if node is not None and (node.text or "").strip():
                return (node.text or "").strip()
        return ""

    title = clean_html(pick(["title"]))
    description = clean_html(pick(["description", "summary"]))
    pub_text = pick(["pubDate", "published", "updated"])

    source_name = guess_source(feed_url)

    return {
        "title": title,
        "content": description,
        "category": category,
        "author": source_name,
        "source": source_name,
        "views": fake_views(title) if title else 0,
        "publish_time": parse_time(pub_text),
    }


async def fetch_one_feed(
    client: httpx.AsyncClient,
    category: str,
    feed_url: str,
    per_feed: int,
) -> list:
    """
    抓一个 RSS 源。

    返回解析好的新闻字典列表。
    源挂了就打印原因、返回空列表 —— 不影响其他源。
    """
    try:
        resp = await client.get(
            feed_url,
            headers=RSS_HEADERS,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except Exception as e:
        print("      [跳过] 请求失败：%s: %s" % (type(e).__name__, str(e)[:60]))
        return []

    if resp.status_code != 200:
        print("      [跳过] HTTP %d" % resp.status_code)
        return []

    # 【新东西】解析 XML
    #   ET.fromstring(...) 把一段 XML 文本变成"可以按标签查找"的树结构。
    #   之后 item.find("title") 就能取到 <title> 标签里的内容。
    try:
        root = ET.fromstring(resp.content)
    except Exception as e:
        print("      [跳过] 返回的不是合法 XML：%s" % str(e)[:60])
        return []

    items = root.findall(".//item")

    # 有些站用 Atom 格式，标签是 <entry> 而不是 <item>
    if not items:
        atom_ns = "{http://www.w3.org/2005/Atom}"
        items = root.findall(".//" + atom_ns + "entry")

    if not items:
        print("      [跳过] 解析出来 0 条")
        return []

    collected = []

    for item in items[:per_feed]:
        entry = parse_entry(item, category, feed_url)

        if not entry["title"]:
            continue

        collected.append(entry)

    print("      [成功] %d 条" % len(collected))

    return collected


async def main():
    parser = argparse.ArgumentParser(description="从公开 RSS 源抓真实新闻进库")
    parser.add_argument("--per-feed", type=int, default=30, help="每个源取前几条，默认 30")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写数据库")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="先清空 news 表再灌（把本地测试数据换成真实新闻时用）",
    )
    args = parser.parse_args()

    if not DB_URL:
        print("[失败] 没有读到 DB_URL —— 请先在 .env 里填写数据库连接串")
        return

    print("开始抓取，共 %d 个 RSS 源，每个取前 %d 条" % (len(RSS_SOURCES), args.per_feed))
    print("")

    all_items = []

    async with httpx.AsyncClient() as client:
        for category, feed_url in RSS_SOURCES.items():
            print("  [%s] %s" % (category, feed_url))

            items = await fetch_one_feed(client, category, feed_url, args.per_feed)

            all_items.extend(items)

    print("")
    print("抓取结束，共拿到 %d 条" % len(all_items))

    if not all_items:
        print("")
        print("一条都没抓到。可能的原因：")
        print("  ① 网络不通（这些源都在国内，正常应该能访问）")
        print("  ② 这些源刚好都失效了（公开源失效是常事，换几个地址即可）")
        print("")
        print("可以先跑 python scripts/seed_data.py 用本地测试数据把项目跑起来。")
        return

    # ---- 打印时间范围，帮忙判断数据新不新 ----
    times = [item["publish_time"] for item in all_items]
    print("  最早一条：%s" % min(times).strftime("%Y-%m-%d"))
    print("  最新一条：%s" % max(times).strftime("%Y-%m-%d"))

    # ---- dry-run：只看不存 ----
    if args.dry_run:
        print("")
        print("=== --dry-run 模式，只看前 5 条，不写数据库 ===")
        for item in all_items[:5]:
            print("")
            print("  分类   ：%s" % item["category"])
            print("  标题   ：%s" % item["title"][:60])
            print("  来源   ：%s" % item["source"])
            print("  时间   ：%s" % item["publish_time"])
            print("  浏览量 ：%d（模拟值）" % item["views"])
            print("  摘要   ：%s" % item["content"][:80])
        return

    # ---- 写数据库 ----
    engine = create_async_engine(DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    inserted = 0
    skipped = 0

    async with session_factory() as session:
        # 如果传了 --replace，先把老数据全删掉
        # ⚠️ 不可恢复，所以只有显式加 --replace 才执行
        if args.replace:
            await session.execute(delete(News))
            print("")
            print("  [--replace] 已清空原有数据（准备换成真实新闻）")

        # 把库里已有的标题查出来，用来去重
        result = await session.execute(select(News.title))
        existing_titles = set()
        for row in result.all():
            existing_titles.add(row[0])

        for item in all_items:
            if item["title"] in existing_titles:
                skipped = skipped + 1
                continue

            session.add(News(**item))
            existing_titles.add(item["title"])
            inserted = inserted + 1

        await session.commit()

    await engine.dispose()

    print("")
    print("入库完成：新增 %d 条，跳过重复 %d 条" % (inserted, skipped))
    print("")
    print("可以这样验证：")
    print("  python -c \"import asyncio;from sqlalchemy import func,select;...\"")
    print("  或者直接看接口：启动服务后打开 http://127.0.0.1:8020/api/agent/stats")


if __name__ == "__main__":
    asyncio.run(main())
