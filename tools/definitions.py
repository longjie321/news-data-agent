# ============================================================
# 【这个文件干什么】
#   写 7 个"工具函数" —— 这就是智能体能干的 7 件事。
#   每个函数都是真去查 MySQL，不是装样子做假数据。
#
# 【这 7 个工具是怎么设计出来的 —— 分两批，思路完全不同】
#
#   第一批（① ② ③ ④）：按"用户最可能怎么问"设计的
#     ① 数量统计    count_news_by_category   "体育类有多少篇新闻"
#     ② 关键词查找  search_news              "有没有关于人工智能的新闻"
#     ③ 排行榜      get_top_news             "浏览量最高的是哪篇"
#     ④ 时间统计    count_news_by_time       "最近一周发了多少篇"
#
#   第二批（⑤ ⑥ ⑦）：按"必须多跳"设计的
#     ★ 为什么要加这一批：
#       只有前 4 个工具的时候，"模型自己决定调哪个工具"这件事其实没什么含金量 ——
#       问数量就调①，问排行就调③，一一对应，它根本不需要做决策。
#       （面试官会一句话问到点上："这不就是自然语言转 SQL 吗？"）
#
#     ⑤ 查详情    get_news_detail      要先搜索/排行拿到 id，再查第二跳
#     ⑥ 分类对比  compare_categories   一次工具调用内部查两次，返回可对比的数据
#     ⑦ 时间趋势  get_category_trend   返回"按天的序列"而不是单个数字，要模型自己解读
#
#   加完之后，有三类问题**任何单个工具都答不了**，必须组合：
#     · "搜一下人工智能的新闻，把最热的那篇讲给我听"   → 先② 再⑤
#     · "体育类和科技类哪个更火"                      → ⑥
#     · "最近一周发文量在涨还是在跌"                  → ⑦
#   这才是 Agent 存在的意义：不是用户告诉它查什么，是它自己拆步骤。
#
# 【三条统一约定（很重要，后面到处都在用）】
#   ① 每个函数都接收一个 db（数据库会话），由调用方传进来
#      —— 这样函数自己不负责"开关连接"，干净、也好单独测试。
#   ② 每个函数都返回 dict（字典），不返回 ORM 对象
#      —— 因为返回值最终要变成 JSON 发给大模型。
#   ③ 函数名、参数名都会暴露给大模型看（见 tools/registry.py），
#      所以取名要直观，"days"就比"day_num"好。
# ============================================================

from datetime import datetime, timedelta

# 【导包说明】
#   datetime / timedelta —— 【标准库】Python 自带的日期时间工具
#   sqlalchemy 的 func / select —— 【三方库】func 用来调数据库自带函数（如 COUNT、DATE），
#                                  select 用来拼查询语句（SQLAlchemy 2.0 的新写法）
#   AsyncSession —— 【三方库】异步会话的类型标注，只是写给人看
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.news import News

# 单个工具最多返回多少条（防止模型要 10000 条把内存撑爆）
MAX_LIMIT = 20


def _clean_limit(limit: int) -> int:
    """
    把模型给的 limit 修正到一个合理范围内。

    为什么需要这个函数：
      大模型填参数并不总是靠谱，可能填 0、负数，或者直接填 10000。
      不拦一下的话，要么查出空结果，要么一次拉太多数据。
    """
    if limit is None:
        return 5

    if limit < 1:
        return 1

    if limit > MAX_LIMIT:
        return MAX_LIMIT

    return limit


def _clean_days(days: int) -> int:
    """把模型给的 days 修正到 1~365 天之间（跟 _clean_limit 是同一个思路）"""
    if days is None:
        return 7

    if days < 1:
        return 1

    if days > 365:
        return 365

    return days


def _news_to_dict(row: News) -> dict:
    """
    把一条新闻（数据库里的一行）转成字典。

    为什么单独抽成一个函数：
      搜索、排行、查详情这三个工具都要做"一模一样的字段转换"。
      抄三遍的话，以后想加个字段就容易改漏（只改了两处、漏了第三处）。
      —— 这种"重复三次以上就抽出来"的习惯，面试里可以当代码品味讲。
    """
    return {
        # ★ id 必须返回给模型 —— 因为工具⑤"查详情"就是靠它来查的
        "id": row.id,
        "title": row.title,
        "category": row.category,
        "views": row.views,
        # strftime 是 Python 日期对象的格式化方法：%Y-%m-%d 就是 2026-09-18 这种样子
        "publish_time": row.publish_time.strftime("%Y-%m-%d"),
        "source": row.source,
    }


async def count_news_by_category(db: AsyncSession, category: str) -> dict:
    """
    工具① 统计某个分类下有多少篇新闻。

    对应问法："体育类有多少篇新闻" / "科技类新闻多不多"
    """
    # select(func.count()) 相当于 SQL 的 SELECT COUNT(*)
    # select_from(News) 说明"从 news 这张表里数"
    # where(...) 就是 WHERE 条件
    stmt = select(func.count()).select_from(News).where(News.category == category)

    # db.scalar(...) 表示"执行这条查询，取第一行第一列的那一个值"（这里就是总数）
    total = await db.scalar(stmt)

    if total is None:
        total = 0

    return {
        "category": category,
        "count": total,
    }


async def search_news(db: AsyncSession, keyword: str, limit: int = 5) -> dict:
    """
    工具② 按关键词搜索新闻标题。

    对应问法："有没有关于人工智能的新闻" / "帮我找找梅西相关的报道"
    """
    limit = _clean_limit(limit)

    # SQL 的 LIKE 查询：两边加 % 表示"包含这个词就行"
    # 注意：这种写法用不上索引（前面带 % 索引会失效），数据量小的时候没关系。
    #      数据量大了要换成分词/全文索引，这个点在面试里可以主动讲。
    pattern = f"%{keyword}%"

    stmt = (
        select(News)
        .where(News.title.like(pattern))
        .order_by(News.publish_time.desc())   # desc() = 倒序，最新的排前面
        .limit(limit)
    )

    result = await db.execute(stmt)

    # scalars() 表示"我只要每一行的那个实体对象（News），不要其他包装"
    # .all() 把结果一次性取回来变成列表
    rows = result.scalars().all()

    news_list = []
    for row in rows:
        news_list.append(_news_to_dict(row))

    return {
        "keyword": keyword,
        "count": len(news_list),
        "news": news_list,
    }


async def get_top_news(db: AsyncSession, limit: int = 5) -> dict:
    """
    工具③ 按浏览量从高到低取前几篇新闻（排行榜）。

    对应问法："浏览量最高的是哪篇" / "最热门的新闻"
    """
    limit = _clean_limit(limit)

    stmt = (
        select(News)
        .order_by(News.views.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    news_list = []
    for row in rows:
        news_list.append(_news_to_dict(row))

    return {
        "count": len(news_list),
        "news": news_list,
    }


async def count_news_by_time(
    db: AsyncSession,
    days: int = 7,
    category: str = "",
) -> dict:
    """
    工具④ 统计最近 N 天发布了多少篇新闻。

    对应问法："最近一周发了多少篇" / "今天有多少条新新闻"
              / "最近一周科技类有多少篇"（带分类的时候）

    【为什么要加 category 这个可选参数 —— 这是测试测出来的改动】
      这个工具一开始只有 days 一个参数。
      后来测试时问"最近一周科技类有多少篇新闻"，模型发现没有任何工具能
      同时按"分类 + 时间"统计，只好退而求其次去调了"按分类统计"，
      然后给出了"最近一周科技类 50 篇"这种不准确的答案
      （那 50 篇其实是全部科技类新闻的总数，跟时间没关系）。

      所以给时间统计加上了可选的 category。
      教训：工具设计必须覆盖真实问法，否则模型只能用近似工具糊弄，
           答案看着像模像样，其实是错的。
      （这种"测试发现问题 → 回头改设计"的过程，面试里可以讲）

    为什么参数是 days（最近几天）而不是"起止日期"：
      大模型填两个具体日期容易填错格式，填"最近几天"简单又不容易错。
      这叫"为模型设计参数"。
    """
    days = _clean_days(days)

    # datetime.now() 是"现在"，减去 timedelta(days=N) 就得到"N 天前"
    since = datetime.now() - timedelta(days=days)

    stmt = (
        select(func.count())
        .select_from(News)
        .where(News.publish_time >= since)
    )

    # 如果这次带了分类，就再往查询上加一个条件。
    # 注意这里是"往已有的查询上继续加条件"，不是重新拼一条新语句。
    category_name = "全部"
    if category:
        stmt = stmt.where(News.category == category)
        category_name = category

    total = await db.scalar(stmt)

    if total is None:
        total = 0

    return {
        "days": days,
        "since": since.strftime("%Y-%m-%d"),
        "category": category_name,
        "count": total,
    }


async def get_news_detail(db: AsyncSession, news_id: int) -> dict:
    """
    工具⑤ 按编号查一篇新闻的详细信息（含正文）。

    对应问法："搜出来的第二篇讲了什么" / "把浏览量最高那篇的内容说一下"

    ★ 这个工具存在的意义，是让"多跳"成为可能：
      用户很难直接说出新闻编号，所以正确的用法是
      「先调 ② search_news 或 ③ get_top_news 拿到 id，再用本工具查内容」。
      单靠它自己，什么问题都答不了 —— 这正是它价值所在（逼模型拆步骤）。

    ⚠️ 正文只返回前 300 字：
      一篇新闻正文可能上千字，整个塞给模型会浪费上下文和钱，
      而 300 字足够它回答"讲了什么"。
    """
    stmt = select(News).where(News.id == news_id)

    # db.scalar(...) 返回"第一个结果"，查不到就是 None
    row = await db.scalar(stmt)

    if row is None:
        # 查不到也**不抛异常**，而是返回 error —— 见 registry.py 里关于
        # "让错误回到模型手里"的说明。模型看到"没有这个编号"会换个做法。
        return {"error": f"没有编号为 {news_id} 的新闻"}

    content = row.content or ""

    detail = _news_to_dict(row)
    detail["author"] = row.author
    detail["content"] = content[:300]
    detail["content_length"] = len(content)

    return detail


async def _category_summary(db: AsyncSession, category: str) -> dict:
    """
    统计一个分类的概况：多少篇、总浏览量、平均浏览量、最高浏览量。

    这是工具⑥ 内部用的辅助函数 —— 名字前面那个下划线表示
    "内部使用，不对外暴露给模型"（它不是工具，模型看不到它）。
    """
    # 一次查询里同时算四个统计值（SQL 的 COUNT/SUM/AVG/MAX）
    stmt = (
        select(
            func.count().label("count"),
            func.sum(News.views).label("total_views"),
            func.avg(News.views).label("avg_views"),
            func.max(News.views).label("max_views"),
        )
        .select_from(News)
        .where(News.category == category)
    )

    row = (await db.execute(stmt)).first()

    total_views = row[1] or 0
    avg_views = row[2] or 0

    return {
        "category": category,
        "count": row[0] or 0,
        "total_views": int(total_views),
        # round(x, 1) 表示保留一位小数（平均浏览量是小数，直接给模型看太啰嗦）
        "avg_views": round(float(avg_views), 1),
        "max_views": row[3] or 0,
    }


async def compare_categories(
    db: AsyncSession,
    category_a: str,
    category_b: str,
) -> dict:
    """
    工具⑥ 对比两个分类：谁新闻更多、谁总浏览量更高。

    对应问法："体育类和科技类哪个更火" / "财经和娱乐哪个新闻更多"

    ★ 为什么值得单独做个工具（而不是让模型自己调两次①）：
      模型自己调两次也能拿到两个数字，但它算数不太可靠 ——
      让它比较"50 和 47 哪个大"经常没问题，但换成"总浏览量 98 万和 102 万"
      就容易出错。所以这里把对比结论直接算好给它。
      这也是工具设计的一条经验：**能用代码算准的，别让模型去猜。**
    """
    summary_a = await _category_summary(db, category_a)
    summary_b = await _category_summary(db, category_b)

    if summary_a["count"] >= summary_b["count"]:
        more_news = category_a
    else:
        more_news = category_b

    if summary_a["total_views"] >= summary_b["total_views"]:
        more_views = category_a
    else:
        more_views = category_b

    return {
        "compare": [summary_a, summary_b],
        "more_news": more_news,
        "more_views": more_views,
    }


async def get_category_trend(
    db: AsyncSession,
    category: str = "",
    days: int = 7,
) -> dict:
    """
    工具⑦ 按天统计最近 N 天的发稿量，返回一条"时间序列"。

    对应问法："最近一周发文量是涨还是跌" / "这几天科技类新闻的走势"

    ★ 为什么它比工具④ 更需要模型动脑：
      ④ 只返回一个总数（"最近 7 天 18 篇"），模型没什么可推理的。
      ⑦ 返回的是一串"每天各多少篇"，**趋势要模型自己从序列里读出来**
      —— 是涨、是跌、还是中间有个高峰，代码不替它下结论。
      这是"工具给原料、模型做判断"的分工。
    """
    days = _clean_days(days)
    since = datetime.now() - timedelta(days=days)

    # 【新语法】func.date(News.publish_time)
    #   这是在调用 MySQL 自带的 DATE() 函数，把 "2026-09-18 10:30:00"
    #   切成 "2026-09-18"（只留日期部分）。
    #   这样同一篇新闻的时间就归到"天"上，才能 GROUP BY 按天分组。
    #   .label("day") 是给这一列起个名字，方便后面 group_by 引用。
    day_column = func.date(News.publish_time).label("day")

    stmt = (
        select(day_column, func.count().label("count"))
        .select_from(News)
        .where(News.publish_time >= since)
        .group_by(day_column)      # GROUP BY：把同一天的合成一组
        .order_by(day_column)      # 按日期从小到大排
    )

    category_name = "全部"
    if category:
        stmt = stmt.where(News.category == category)
        category_name = category

    result = await db.execute(stmt)
    rows = result.all()   # 每行是 (日期, 条数) 两个值

    # 数据库只会返回"确实有新闻的那几天"，中间没有新闻的日子会缺掉。
    # 缺掉的日子会让模型读错趋势（它可能以为 9-15 后面直接接 9-17）。
    # 所以这里把缺的日期补成 0，让序列是连续完整的。
    counts_by_day = {}
    for row in rows:
        # row[0] 是 date 对象（或字符串），统一转成 "2026-09-18" 这种文本
        counts_by_day[str(row[0])] = row[1]

    series = []
    # range(days - 1, -1, -1) 会依次给出 days-1, days-2, ... 1, 0
    # 刚好对应"从最早那天，一路数到今天"
    for offset in range(days - 1, -1, -1):
        day_text = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        series.append(
            {
                "date": day_text,
                "count": counts_by_day.get(day_text, 0),
            }
        )

    return {
        "category": category_name,
        "days": days,
        "series": series,
    }


# ------------------------------------------------------------
# 所有工具函数的清单
# registry.py 会遍历这个列表，把每个函数登记成"模型可以调用的工具"。
# 加新工具只需要两步：上面写函数、这里加一行。
# ------------------------------------------------------------
ALL_TOOL_FUNCTIONS = [
    count_news_by_category,
    search_news,
    get_top_news,
    count_news_by_time,
    get_news_detail,
    compare_categories,
    get_category_trend,
]
