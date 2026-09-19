# ============================================================
# 【这个文件干什么】
#   测试 7 个工具函数能不能正常工作（只测数据库这一层，不调用大模型）。
#
# 【怎么跑】
#   在项目根目录执行：
#       .venv\Scripts\python.exe -m pytest
#
# 【为什么要单独测工具】
#   工具是"智能体的手"。手不灵，脑子再聪明也查不出对的数据。
#   单独测还有个好处：出问题时能立刻分清是"工具查错了"还是"模型选错工具了"。
#   —— 这种"分层排查"的思路，面试聊调试时可以讲。
#
# 【这个文件和以前那个 print 版测试的区别】
#   以前是 `python tests/test_tools.py`，靠人眼看打印结果。
#   现在改成 pytest：每个测试自己断言（assert），对就是对、错就是错，
#   跑完直接告诉你"几个通过、几个失败"，还能一条命令全跑一遍。
#
# 【为什么很多测试用 > 0 这种模糊断言】
#   因为库里的数据可能变（重新灌过数据、抓过新新闻）。
#   断言写死具体数字（比如"体育类一定 50 篇"）会导致一换数据测试就红。
#   测试要检验的是"逻辑对不对"，不是"数据是不是恰好这么多"。
# ============================================================

from datetime import datetime

from sqlalchemy import func, select

from models.news import News
from tools.definitions import (
    MAX_LIMIT,
    _clean_days,
    _clean_limit,
    _since_datetime,
    compare_categories,
    count_news_by_category,
    count_news_by_time,
    get_category_trend,
    get_news_detail,
    get_top_news,
    search_news,
)
from tools.registry import (
    TOOLS,
    TOOL_NAME_TO_FUNCTION,
    _limit_result_size,
    run_tool,
)


# ============================================================
# 前置检查
# ============================================================
async def test_database_has_data(db):
    """库里得有数据，后面的测试才有意义"""
    total = await db.scalar(select(func.count()).select_from(News))

    assert total and total > 0, (
        "库里没有数据。请先跑：python scripts/seed_data.py（测试数据）"
        " 或 python scripts/fetch_news.py（真实新闻）"
    )


# ============================================================
# 工具① count_news_by_category
# ============================================================
async def test_count_by_category(db):
    result = await count_news_by_category(db, "体育")

    assert result["category"] == "体育"
    assert isinstance(result["count"], int)
    assert result["count"] > 0


async def test_count_by_category_not_exist(db):
    """不存在的分类应该返回 0，而不是报错崩掉"""
    result = await count_news_by_category(db, "这个分类根本不存在")

    assert result["count"] == 0


# ★ 下面两条是补的：category 变成可选之后的行为
async def test_count_without_category_returns_all(db):
    """
    不传分类 → 返回每个分类的篇数 + 总数。

    为什么要有这个能力：用户问"一共有多少条新闻"是最自然的问法，
    原来没有工具能直接回答，模型只能借 count_news_by_time 硬凑。
    """
    result = await count_news_by_category(db)

    assert "by_category" in result
    assert isinstance(result["by_category"], dict)
    assert len(result["by_category"]) > 0

    # 总数必须等于各分类之和 —— 不然就是查询逻辑写错了
    assert result["count"] == sum(result["by_category"].values())


async def test_count_all_alias_same_as_without(db):
    """传"全部"和不传，结果必须完全一样（别名等价）"""
    without = await count_news_by_category(db)
    with_alias = await count_news_by_category(db, "全部")

    assert with_alias["count"] == without["count"]
    assert with_alias["by_category"] == without["by_category"]


# ============================================================
# 工具② search_news
# ============================================================
async def test_search_news(db):
    result = await search_news(db, "AI", limit=3)

    assert result["keyword"] == "AI"
    assert result["count"] <= 3

    for item in result["news"]:
        # ★ 必须有 id —— 没有 id 的话，工具⑤"查详情"这一跳就走不通了
        assert "id" in item
        assert "title" in item
        assert "views" in item


async def test_search_news_limit_is_clamped(db):
    """模型可能瞎填 limit（比如 9999），工具必须自己兜住"""
    result = await search_news(db, "AI", limit=9999)

    assert result["count"] <= MAX_LIMIT


async def test_search_news_no_match(db):
    """搜不到东西时返回空列表，不报错"""
    result = await search_news(db, "一个绝对搜不到的词abcxyz", limit=5)

    assert result["count"] == 0
    assert result["news"] == []


# ============================================================
# 工具③ get_top_news
# ============================================================
async def test_top_news_is_sorted(db):
    """排行榜必须真的是从高到低（这个如果错了，整个功能就是坏的）"""
    result = await get_top_news(db, limit=5)

    assert result["count"] > 0

    views = [item["views"] for item in result["news"]]
    assert views == sorted(views, reverse=True), "排行榜没有按浏览量倒序排！"

    for item in result["news"]:
        assert "id" in item


# ============================================================
# 工具④ count_news_by_time
# ============================================================
async def test_count_by_time(db):
    result = await count_news_by_time(db, days=7)

    assert result["days"] == 7
    assert result["category"] == "全部"
    assert result["count"] >= 0


async def test_count_by_time_with_category(db):
    """带分类的统计 —— 这个参数是之前测试发现问题后补上的"""
    result = await count_news_by_time(db, days=30, category="科技")

    assert result["category"] == "科技"
    assert result["count"] >= 0


async def test_count_by_time_days_is_clamped(db):
    """days 瞎填（0 / 9999）要被兜住"""
    r1 = await count_news_by_time(db, days=0)
    r2 = await count_news_by_time(db, days=9999)

    assert r1["days"] == 1
    assert r2["days"] == 365


# ============================================================
# 工具⑤ get_news_detail（多跳的第二跳）
# ============================================================
async def test_news_detail_two_hop(db):
    """
    ★ 模拟"多跳"：先查排行拿到 id，再用 id 查详情。
    这正是 Agent 需要做的两步动作 —— 用户不会直接给编号。
    """
    top = await get_top_news(db, limit=1)
    assert top["count"] > 0

    news_id = top["news"][0]["id"]
    detail = await get_news_detail(db, news_id)

    assert detail["id"] == news_id
    assert detail["title"] == top["news"][0]["title"]
    assert "content" in detail


async def test_news_detail_content_is_trimmed(db):
    """正文必须被截断（不然一篇上千字的正文会把上下文撑爆）"""
    top = await get_top_news(db, limit=1)
    news_id = top["news"][0]["id"]

    detail = await get_news_detail(db, news_id)

    assert len(detail["content"]) <= 300
    assert detail["content_length"] >= len(detail["content"])


async def test_news_detail_not_found(db):
    """查不到时不抛异常，而是返回 error 交给模型自己处理"""
    result = await get_news_detail(db, 999999999)

    assert "error" in result


# ============================================================
# 工具⑥ compare_categories
# ============================================================
async def test_compare_categories(db):
    result = await compare_categories(db, "体育", "科技")

    assert len(result["compare"]) == 2

    names = [item["category"] for item in result["compare"]]
    assert "体育" in names and "科技" in names

    # 结论字段必须是这两个分类之一（不能凭空冒出来第三个）
    assert result["more_news"] in ("体育", "科技")
    assert result["more_views"] in ("体育", "科技")


async def test_compare_categories_summary_shape(db):
    """对比里每一项都要有完整的统计数据"""
    result = await compare_categories(db, "财经", "娱乐")

    for item in result["compare"]:
        assert "count" in item
        assert "total_views" in item
        assert "avg_views" in item
        assert "max_views" in item


# ============================================================
# 工具⑦ get_category_trend
# ============================================================
async def test_category_trend_length(db):
    """返回的序列长度必须等于天数（缺的日子要补 0）"""
    result = await get_category_trend(db, days=7)

    assert len(result["series"]) == 7

    for item in result["series"]:
        assert "date" in item
        assert "count" in item


async def test_category_trend_is_continuous_and_sorted(db):
    """
    ★ 日期必须连续且从小到大 —— 这是"看趋势"的前提。
    如果中间缺了几天没补 0，模型会误以为那天是接着下一天的，把趋势读错。
    """
    result = await get_category_trend(db, days=10)

    dates = [item["date"] for item in result["series"]]

    assert dates == sorted(dates), "趋势序列的日期没有按从小到大排！"
    assert len(set(dates)) == 10, "趋势序列里有重复的日期！"


# ============================================================
# 工具注册表的一致性（最容易出错、也最该测的地方）
# ============================================================
def test_schema_matches_function_table():
    """
    ★ 关键一致性检查：
      发给模型的工具名（TOOLS 里的 name），必须和真正能执行的函数表完全对得上。
      这两个地方任何一个改了、另一个忘了改，就会出现
      "模型调了一个根本不存在的工具"——而且只有跑到那一步才会发现。
    """
    schema_names = set()
    for item in TOOLS:
        schema_names.add(item["function"]["name"])

    function_names = set(TOOL_NAME_TO_FUNCTION.keys())

    assert schema_names == function_names, (
        "工具清单和函数表对不上！\n"
        "  只在 schema 里：%s\n"
        "  只在函数表里：%s"
        % (sorted(schema_names - function_names), sorted(function_names - schema_names))
    )


def test_tool_count():
    """工具数量对不上通常意味着加漏了或删漏了"""
    assert len(TOOLS) == 7


def test_every_tool_has_description():
    """每个工具都必须有说明 —— 模型全靠它判断什么时候该用这个工具"""
    for item in TOOLS:
        function_info = item["function"]

        assert function_info.get("description", "").strip(), (
            "工具 %s 没有写 description，模型会不知道什么时候用它"
            % function_info.get("name")
        )

        assert "parameters" in function_info


# ============================================================
# run_tool 的容错（Agent 耐用的关键）
# ============================================================
async def test_run_tool_normal(db):
    result = await run_tool(db, "count_news_by_time", {"days": 7})

    assert "error" not in result
    assert result["days"] == 7


async def test_run_tool_unknown_name(db):
    """工具名不存在 → 返回 error，不崩"""
    result = await run_tool(db, "根本不存在的工具", {})

    assert "error" in result


async def test_run_tool_wrong_arguments(db):
    """参数名写错 → 返回 error，不崩（让模型看到错误自己改）"""
    result = await run_tool(db, "count_news_by_category", {"wrong_arg": "x"})

    assert "error" in result


# ============================================================
# 参数清洗：模型给脏参数时不能崩，也不能把脏值送进 SQL
#
# 【为什么专门测这个】
#   模型的参数走 JSON，实测会出现 "limit": "5"（字符串）和 "limit": 5.7（小数）。
#   字符串会让原来的实现抛 TypeError；小数更危险 —— 它能通过所有判断，
#   最后变成 SQL 的 LIMIT 5.7，MySQL 直接语法报错。
# ============================================================
def test_clean_limit_handles_string():
    """模型把 limit 写成字符串 "5" 要能认出来，而不是抛异常"""
    assert _clean_limit("5") == 5


def test_clean_limit_handles_float():
    """小数的 limit 要转成整数，否则会变成 SQL 里的 LIMIT 5.7"""
    assert _clean_limit(5.7) == 5


def test_clean_limit_rejects_garbage():
    """彻底没法转的值要退回默认值，不能崩"""
    assert _clean_limit(None) == 5
    assert _clean_limit("abc") == 5


def test_clean_limit_clamps_range():
    """越界的值要夹到 1 ~ MAX_LIMIT"""
    assert _clean_limit(0) == 1
    assert _clean_limit(-3) == 1
    assert _clean_limit(10000) == MAX_LIMIT


def test_clean_days_handles_dirty_input():
    """days 也是同一套清洗逻辑"""
    assert _clean_days("7") == 7
    assert _clean_days(7.9) == 7
    assert _clean_days(None) == 7
    assert _clean_days("abc") == 7


def test_clean_days_clamps_range():
    """days 的范围是 1 ~ 365"""
    assert _clean_days(0) == 1
    assert _clean_days(-1) == 1
    assert _clean_days(999) == 365


# ============================================================
# "最近 N 天"要按【自然日】算，不是"往前推 N×24 小时"
#
# 【为什么专门测这个】
#   工具说明里写着「1 表示今天」，但原来实现是 now - timedelta(days=days)，
#   也就是"过去 24 小时"。实测问"今天有多少条"：工具答 206 条、
#   而今天真实只有 79 条 —— 把昨天中午之后的 127 条也算进来了。
#   答错了而且从输出上看不出来，这是最危险的一类 bug。
# ============================================================
def test_since_datetime_days_1_is_today_midnight():
    """days=1 必须落在"今天 00:00"，这样才等于口语里的"今天" """
    since = _since_datetime(1)
    today = datetime.now().date()

    assert since.date() == today, "days=1 应该是今天"
    assert (since.hour, since.minute, since.second, since.microsecond) == (0, 0, 0, 0), (
        "必须是 0 点整，否则会把昨天某个时刻之后的数据也算进'今天'"
    )


def test_since_datetime_days_7_covers_7_calendar_days():
    """days=7 应该是"含今天在内正好 7 个自然日\""""
    since = _since_datetime(7)
    today = datetime.now().date()

    assert (today - since.date()).days == 6, "应该是 6 天前的 0 点（含今天共 7 天）"
    assert since.hour == 0 and since.minute == 0


async def test_count_news_by_time_matches_calendar_day(db):
    """
    ★ days=1 的统计结果，必须等于"从今天 00:00 起"的真实条数。

    这条是给上面那个"答错 1.5 倍"的 bug 上的保险：
    以后谁把 _since_datetime 改回 timedelta 的写法，这里会立刻红。
    """
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    expected = await db.scalar(
        select(func.count()).select_from(News).where(News.publish_time >= midnight)
    )

    result = await count_news_by_time(db, days=1)

    assert result["count"] == expected, (
        "days=1 统计到的条数应该和'今天 0 点起'完全一致（%s vs %s）"
        % (result["count"], expected)
    )


async def test_category_trend_series_starts_at_midnight(db):
    """
    趋势序列的第一天必须和 count_news_by_time 用同一个起点。

    否则序列第一天是个"半天"，和后面补出来的整日 0 混在一起，趋势会读歪。
    """
    result = await get_category_trend(db, days=7)

    assert len(result["series"]) == 7
    assert result["series"][0]["date"] == _since_datetime(7).strftime("%Y-%m-%d")
    assert result["series"][-1]["date"] == datetime.now().strftime("%Y-%m-%d")


# ============================================================
# 结果长度保护（防止多轮累积撑爆上下文）
# ============================================================
def test_limit_result_size_trims_long_list():
    """超长的结果要被砍短，并且打上截断标记"""
    big = {"news": [{"title": "很长的标题" * 20, "views": 100} for _ in range(200)]}

    trimmed = _limit_result_size(big)

    assert trimmed.get("truncated") is True
    assert "note" in trimmed
    assert len(trimmed["news"]) < 200

    # ★ 必须告诉模型"实际留了几条"。
    #   只说"已截断"的话，模型看到 3 条会以为库里总共就 3 条，
    #   答出"全文只有 3 篇"这种错误结论。
    assert "kept" in trimmed
    assert trimmed["kept"] == len(trimmed["news"])


def test_limit_result_size_keeps_short_result():
    """没超长的结果不能被动 —— 一个字都不该改"""
    small = {"count": 3, "news": [{"id": 1}, {"id": 2}, {"id": 3}]}

    result = _limit_result_size(small)

    assert result == small
    assert "truncated" not in result


def test_limit_result_size_keeps_head_for_news_list():
    """
    新闻列表被截断时保留【头部】。

    因为它按发布时间倒序排（最新的在前），保留前几条才是"最新的新闻"。
    """
    big = {"news": [{"id": i, "title": "很长的标题" * 20} for i in range(200)]}

    trimmed = _limit_result_size(big)

    assert trimmed.get("truncated") is True
    assert len(trimmed["news"]) < 200
    # 第一项还是原来第一项（最新那篇）
    assert trimmed["news"][0] == big["news"][0]


def test_limit_result_size_keeps_tail_for_time_series():
    """
    时间序列被截断时必须保留【尾部】—— 这是一个真实踩过的坑。

    问"最近一年发文走势"时，get_category_trend 会返回 365 天，
    长度超标被砍半。原来的实现保留前半段（最早的日子），
    于是【最近几个月的数据直接消失】，模型据此判趋势会完全反过来，
    而且它自己不知道。

    series 是按日期正序排的（最早在前），所以最新数据在尾部。
    """
    days = 400
    big = {
        "category": "全部",
        "days": days,
        "series": [{"date": "2026-01-01", "count": i} for i in range(days)],
    }

    trimmed = _limit_result_size(big)

    assert trimmed.get("truncated") is True
    assert len(trimmed["series"]) < days
    # ★ 最后一项必须还是最新那一天（尾部被保留）
    assert trimmed["series"][-1] == big["series"][-1]
    # ★ 而第一项已经不是最早那天了（头部被丢掉）
    assert trimmed["series"][0] != big["series"][0]
    # note 要说清"保留的是最近若干天"，而不是含糊的"前 N 条"
    assert "最近" in trimmed["note"]


# ============================================================
# 代码卫生：分类清单只能有一份定义（2026-09-19 优化时加的）
#
# 背景：这串分类名原来在 tools/registry.py 里硬编码了两遍
#   （给模型看的"可选值"），scripts/seed_data.py 里还有一份。
#   以后想加一个分类，得记住把所有地方都改掉 ——
#   漏了工具描述那一处，模型看到的"可选值"就是旧的，
#   它会不敢用新分类（或者用了、参数却对不上）。
#   现在统一从 config/news_conf.py 的 CATEGORIES 取。
#   下面两条把这件事钉住。
# ============================================================
def test_registry_descriptions_contain_full_category_list():
    """
    工具描述里必须真的**带着完整的分类清单**（至少 3 处）。

    ⚠️ 这条测试的局限要说清楚（实测发现）：
       它只能验证"描述里有正确的清单"，**区分不出**下面这两种写法 ——
         ① f"{CATEGORY_LIST_TEXT}"     ← 从常量拼的（我们想要的）
         ② "体育、科技、财经、娱乐、社会、国内、国际、头条"  ← 硬编码，但字面一样
       因为字符串内容相同，`in` 判断两种情况都成立。
       真正防硬编码的是下面那条 `test_registry_does_not_hardcode_category_list`
       （它直接读源码找那串字）。
       两条的分工：这条防"清单被人删掉"（删了模型就不知道可选值），
       下面那条防"清单被人写死"。
    """
    from config.news_conf import CATEGORY_LIST_TEXT
    from tools.registry import TOOLS

    found = 0

    for item in TOOLS:
        function = item.get("function") or {}
        parameters = function.get("parameters") or {}
        properties = parameters.get("properties") or {}

        for param_info in properties.values():
            description = param_info.get("description") or ""
            if CATEGORY_LIST_TEXT in description:
                found = found + 1

    assert found >= 3, "工具描述里应该有 3 处以上引用分类清单，实际只有 %d 处" % found


def test_registry_does_not_hardcode_category_list():
    """
    registry.py 的源码里不该再出现完整的分类名列表。

    这条比上面那条更直接：只要有人在 registry 里又写死一遍，它立刻失败。
    """
    import pathlib

    registry_path = pathlib.Path(__file__).parent.parent / "tools" / "registry.py"
    source = registry_path.read_text(encoding="utf-8")

    hardcoded = "体育、科技、财经、娱乐、社会、国内、国际、头条"

    assert hardcoded not in source, (
        "tools/registry.py 里又硬编码分类列表了，应该用 config 里的 CATEGORY_LIST_TEXT"
    )
