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

from sqlalchemy import func, select

from models.news import News
from tools.definitions import (
    compare_categories,
    count_news_by_category,
    count_news_by_time,
    get_category_trend,
    get_news_detail,
    get_top_news,
    search_news,
)
from tools.registry import (
    MAX_LIMIT,
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
# 结果长度保护（防止多轮累积撑爆上下文）
# ============================================================
def test_limit_result_size_trims_long_list():
    """超长的结果要被砍短，并且打上截断标记"""
    big = {"news": [{"title": "很长的标题" * 20, "views": 100} for _ in range(200)]}

    trimmed = _limit_result_size(big)

    assert trimmed.get("truncated") is True
    assert "note" in trimmed
    assert len(trimmed["news"]) < 200


def test_limit_result_size_keeps_short_result():
    """没超长的结果不能被动 —— 一个字都不该改"""
    small = {"count": 3, "news": [{"id": 1}, {"id": 2}, {"id": 3}]}

    result = _limit_result_size(small)

    assert result == small
    assert "truncated" not in result
