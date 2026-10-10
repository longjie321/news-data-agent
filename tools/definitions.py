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


def _to_int(value, default: int) -> int:
    """
    把模型给的参数挤成整数；实在挤不动就用默认值。

    ★ 为什么需要它（2026-09-19 实测补的）
      模型的参数走的是 JSON，理论上数字就是数字。但实测有两类意外：
        ① "limit": "5"   —— 写成字符串（有的模型就是会这样输出）
        ② "limit": 5.7   —— 写成小数
      原来的代码直接拿它跟 1 比大小，于是：
        字符串 → 抛 TypeError（'<' not supported between 'str' and 'int'），
                 虽然被 run_tool 的兜底接住了，但用户看到的是"工具参数不正确"；
        小数   → 5.7 能通过所有判断，最后被塞进 SQL 变成 LIMIT 5.7，
                 MySQL 语法直接报错 —— 这个更隐蔽，因为拦不住。
      所以统一先 int() 洗一遍：
        字符串 "5" → 5    小数 5.7 → 5（向下取整）    None / "abc" → 默认值
      一句话记住：**模型给的脏数据，要在进数据库之前洗干净。**
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value, default: bool = False) -> bool:
    """
    把模型给的参数挤成布尔值（跟 _to_int 是同一套思路，2026-09-20 加）。

    ★ 为什么不能直接写 `if value`
      模型的参数走 JSON，理论上布尔就是布尔。但实测它会写成字符串：
        "true" / "false" / "1" / "0"
      而 Python 有一条规则：【非空字符串一律为真】——
      也就是说 "false" 会被判成 True！
      后果特别隐蔽：用户说"不用完整正文"，代码却当成"要完整正文"，
      把上千字的正文全塞进上下文（费钱，还可能撑爆）。
      所以统一先转一道，和 _to_int 一个道理：模型给的脏数据，用之前先洗干净。
    """
    # 本来就是布尔，直接用
    if isinstance(value, bool):
        return value

    # 字符串形式：明确认一下"是"和"否"这两组写法
    if isinstance(value, str):
        text = value.strip().lower()

        if text in ("true", "1", "yes", "y", "是", "要", "需要"):
            return True

        if text in ("false", "0", "no", "n", "否", "不要", "不需要"):
            return False

        # 认不出来的字符串 → 交给默认值（不是想当然地当 False）
        return default

    # 数字形式：非 0 即为真（0 和 0.0 都算假）
    if isinstance(value, (int, float)):
        return value != 0

    # 其他一律当没给，用默认值
    return default


def _clean_limit(limit: int) -> int:
    """
    把模型给的 limit 修正到一个合理范围内。

    为什么需要这个函数：
      大模型填参数并不总是靠谱，可能填 0、负数，或者直接填 10000。
      不拦一下的话，要么查出空结果，要么一次拉太多数据。
      （类型不对的情况由 _to_int 处理）
    """
    limit = _to_int(limit, 5)

    if limit < 1:
        return 1

    if limit > MAX_LIMIT:
        return MAX_LIMIT

    return limit


def _clean_days(days: int) -> int:
    """把模型给的 days 修正到 1~365 天之间（跟 _clean_limit 是同一个思路）"""
    days = _to_int(days, 7)

    if days < 1:
        return 1

    if days > 365:
        return 365

    return days


def _since_datetime(days: int) -> datetime:
    """
    算出"最近 N 天"的起始时刻 —— 按【自然日】算，而不是"往前推 N×24 小时"。

    ★ 这是一个实测发现的 bug（2026-09-19 修）
      原来两个工具里写的都是：
          since = datetime.now() - timedelta(days=days)
      也就是"从现在往前数 N×24 小时"。但工具说明里明明写着
      「1 表示今天，7 表示最近一周」—— 说的其实是**日历上的今天和这一周**。

      实测证据（库里当时的数据：9/17 有 31 条、9/18 有 129 条、9/19 有 79 条）：
          问"今天有多少条新闻"
          → 工具的 since = 2026-09-18 12:16，返回 **206 条**
          → 但今天（9/19）真实的条数是 **79 条**
        多出来的 127 条，全是"昨天中午之后"混进来的。
      也就是说：问"今天"，答案错了 1.5 倍，而且从输出上完全看不出错。
      —— 这是最危险的一类 bug：答错了，还很自信。

      改成"从 (days-1) 天前的 00:00 开始"之后：
          days=1 → 今天 00:00 起     （就是口语里的"今天"）
          days=7 → 6 天前 00:00 起   （含今天正好 7 个自然日，即"最近一周"）
      这也更符合人说话的习惯 —— 大家说的"最近一周"是日历上的 7 天，
      不是"168 小时"。所以参数名叫 days（天）而不是 hours，就该按天算。

      （`get_category_trend` 返回的日期序列也是按自然日补 0 的，
        两边口径必须一致，否则序列的第一天会是个不完整的日子。）
    """
    days = _clean_days(days)

    # 先退到 (days-1) 天前，再把时分秒归零 —— 这样 days=1 就正好是"今天 0 点"。
    start = datetime.now() - timedelta(days=days - 1)

    return start.replace(hour=0, minute=0, second=0, microsecond=0)


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


# 传了下面这几个值，等同于"不分类、全都要"
ALL_CATEGORY_ALIASES = ("全部", "所有", "全部分类", "all", "total", "全部新闻")


async def count_news_by_category(db: AsyncSession, category: str = None) -> dict:
    """
    工具① 统计新闻数量。

    不传 category（或传"全部""所有"）：
        返回每个分类各自的篇数 + 总数，一次拿全。
    传 category：
        只统计这一个分类。

    对应问法："体育类有多少篇新闻" / "一共有多少条新闻" / "哪几类各有多少篇"
    """
    # ---------- 分支一：不指定分类 → 一次把所有分类都统计出来 ----------
    #
    # 【为什么要加这个分支 —— 这是实测发现的问题】
    #   原来 category 是必填的，于是用户问最自然的"库里一共有多少条新闻"时，
    #   模型**没有工具能直接回答**，只能硬凑：
    #   实测它去调了 count_news_by_time(days=365)，答成"最近一年共有 239 条"。
    #   数字碰巧对了，但如果库里有超过一年的数据，这个答案就是错的。
    #   另外问"哪几类各有多少篇"时，它连调了 8 次同一个工具才凑齐 ——
    #   现在一次调用就够了（更少的轮次、更少的 token、更快）。
    if not category or str(category).strip() in ALL_CATEGORY_ALIASES:
        # group_by 相当于 SQL 的 GROUP BY category
        # 返回的是 [(分类名, 篇数), ...] 这种一行一行的结构
        stmt = select(News.category, func.count()).group_by(News.category)
        rows = (await db.execute(stmt)).all()

        by_category = {name: count for name, count in rows}

        return {
            "count": sum(by_category.values()),
            "by_category": by_category,
        }

    # ---------- 分支二：指定了分类 → 只数这一个 ----------
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

    # 起始时刻按【自然日】算（days=1 就是今天 0 点）—— 详见 _since_datetime 的说明。
    # ⚠️ 别改回 datetime.now() - timedelta(days=days)：那样 days=1 会变成"过去 24 小时"，
    #    实测问"今天有多少条"答成 206 条，而真实只有 79 条。
    since = _since_datetime(days)

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


async def get_news_detail(
    db: AsyncSession,
    news_id: int,
    full: bool = False,
) -> dict:
    """
    工具⑤ 按编号查一篇新闻的详细信息（含正文）。

    对应问法："搜出来的第二篇讲了什么" / "把浏览量最高那篇的内容说一下"
              / "把最长的那篇完整发给我"（这种要把 full 传 true）

    ★ 这个工具存在的意义，是让"多跳"成为可能：
      用户很难直接说出新闻编号，所以正确的用法是
      「先调 ② search_news / ③ get_top_news / ⑧ get_longest_news 拿到 id，
        再用本工具查内容」。
      单靠它自己，什么问题都答不了 —— 这正是它价值所在（逼模型拆步骤）。

    ⚠️ 正文默认只返回前 300 字：
      一篇新闻正文可能上千字，整个塞给模型会浪费上下文和钱，
      而 300 字足够它回答"讲了什么"。
      只有用户明确要"完整正文 / 全文"时才传 full=true（见下面）。

    【full 参数是 2026-09-20 加的，来自一个真实答不出来的问题】
      用户问"把所有新闻里面最长的一篇完整发给我"，
      而当时这个工具只给 300 字 —— 就算模型找到了那篇，也给不出"完整"的正文。
      「用户要完整」和「代码只给前 300 字」之间的矛盾，得靠一个参数解开。
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
    detail["content_length"] = len(content)

    # full 也要洗一道：模型可能把布尔值写成字符串 "false"，
    # 而 "false" 在 Python 里是【真】—— 不洗的话会被误判成"要完整正文"。
    if _to_bool(full):
        # 用户明确要完整正文 → 不截断。
        # ⚠️ 这里不加长度判断也不会出事：registry.py 的 _limit_result_size 会兜底
        #    （超长就砍短、并打上 truncated 标记告诉模型），所以撑不爆上下文。
        detail["content"] = content
        detail["content_truncated"] = False
    else:
        detail["content"] = content[:300]
        # 明确告诉模型"这段正文是不是被砍过" ——
        # 否则它会把 300 字当成全文，回答时说"这篇文章主要讲了…"却漏掉后半段
        detail["content_truncated"] = len(content) > 300

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

    # ⚠️ 必须和 count_news_by_time 用同一个口径（自然日）。
    #    否则序列的第一天会是个"半天"，和后面补齐的整日 0 天混在一起，趋势读不准。
    since = _since_datetime(days)

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


async def get_longest_news(db: AsyncSession, limit: int = 5) -> dict:
    """
    工具⑧ 按【正文长度】从长到短取前几篇新闻。

    对应问法："哪篇新闻的正文最长" / "内容最多的是哪一篇"
              / "把最长的那篇完整发给我"（拿到 id 后再调 ⑤ 并传 full=true）

    ★ 为什么要加这个工具（2026-09-20，来自一个真实的"答非所问"案例）

      用户问："你把所有新闻里面最长的一篇完整发给我"
      而当时只有 ③ get_top_news（按【浏览量】排）——
      模型手上没有别的牌，就硬着头皮调了它，然后如实说
      "排行榜里不返回正文长度，我做不到"，最后反过来问用户"你想用哪种方式？"

      问题不在模型笨，而在【它手上没有这把牌】：
      "最长"和"最热"是两个完全不同的字段（正文字数 vs 浏览量）。
      工具集缺一个维度，模型就会拿一个看起来相近的工具凑数 ——
      用户看到的就是"答非所问"，而且它给出的答案常常还挺像回事。

      这条经验值得记在面试里：
        **工具集覆盖不到的问法，模型不会说"我不会"，而是会编一个近似答案。**
        所以要按"用户真实会怎么问"来设计工具，而不是按"数据库有哪些字段"。
    """
    limit = _clean_limit(limit)

    # func.char_length(...) 对应 MySQL 的 CHAR_LENGTH()：按【字符】数算长度。
    #   ⚠️ 不要用 func.length()：那个算的是【字节】数，一个汉字占 3 字节，
    #      算出来的"长度"是字数的三倍。虽然排序结果一样，
    #      但返回给模型的 content_length 会是个吓人的数字，它说出来就不对了。
    #   coalesce(...) 是"正文为 NULL 时当成空字符串"，避免 NULL 参与排序时
    #      跑到最前面（MySQL 里 NULL 在 DESC 排序中最小，但行为容易让人误解）。
    length_column = func.char_length(func.coalesce(News.content, ""))

    stmt = (
        select(News)
        .order_by(length_column.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    news_list = []
    for row in rows:
        item = _news_to_dict(row)
        # 把正文长度一并返回 —— 模型回答"最长的是 XXX，共 1501 字"时要用这个数。
        # 注意这里用 len(...) 算的是 Python 层面的字符数，和上面 CHAR_LENGTH 口径一致。
        item["content_length"] = len(row.content or "")
        news_list.append(item)

    return {
        "count": len(news_list),
        "news": news_list,
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
    get_longest_news,
]
