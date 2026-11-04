# 测试 scripts/fetch_news_rss.py 里的纯函数（不联网、不写库、不花钱）。
# 抓取脚本是"一次性运行"的那种代码 —— 平时没人盯着它，

from scripts.fetch_news_rss import clean_html, fake_views, parse_entry, parse_time


# ============================================================
# clean_html：把 RSS 里的 HTML 片段清成纯文本
# ============================================================
def test_clean_html_strips_tags():
    assert clean_html("<p>你好</p>") == "你好"


def test_clean_html_unescapes_entities():
    assert clean_html("A &amp; B") == "A & B"


def test_clean_html_squeezes_whitespace():
    assert clean_html("  换行\n\n和   空格  ") == "换行 和 空格"


# ============================================================
# parse_time：三种格式都要能认出来
# ============================================================
def test_parse_time_handles_rfc822():
    """最常见的一种：Fri, 18 Sep 2026 14:06:00 +0800"""
    result = parse_time("Fri, 18 Sep 2026 14:06:00 +0800")

    assert result is not None
    assert result.year == 2026
    assert result.month == 9


def test_parse_time_handles_date_only():
    """只有日期的情况：2026-09-18"""
    result = parse_time("2026-09-18")

    assert result is not None
    assert result.strftime("%Y-%m-%d") == "2026-09-18"


def test_parse_time_handles_iso():
    """ISO 格式：2026-09-18T14:06:00+08:00"""
    result = parse_time("2026-09-18T14:06:00+08:00")

    assert result is not None
    assert result.year == 2026


# ★ 下面两条钉住一个 2026-09-19 排查出来的坑
def test_parse_time_returns_none_when_empty():
    """没有时间字段时必须返回 None"""
    assert parse_time("") is None
    assert parse_time(None) is None


def test_parse_time_returns_none_when_unparseable():
    """认不出来的格式也必须返回 None（不能拿"现在"顶上）"""
    assert parse_time("昨天下午") is None
    assert parse_time("2026/09/18 大约吧") is None


def test_parse_entry_keeps_none_when_no_pubdate():
    """
    ★ 端到端地钉住同一件事：RSS 条目里压根没有时间字段时，
      parse_entry 产出的 publish_time 必须是 None（不能偷偷变成"现在"）。

    这样 main() 才能数出"有几条时间不可信"并打印警告。
    """
    import xml.etree.ElementTree as ET

    # 造一条"只有标题和摘要、没有 pubDate"的 RSS 条目
    item = ET.fromstring(
        "<item><title>某条新闻</title><description>摘要内容</description></item>"
    )

    entry = parse_entry(item, "科技", "https://www.ithome.com/rss/")

    assert entry["title"] == "某条新闻"
    assert entry["publish_time"] is None


def test_parse_entry_reads_pubdate_when_present():
    """有时间字段时，正常解析出来"""
    import xml.etree.ElementTree as ET

    item = ET.fromstring(
        "<item><title>某条新闻</title>"
        "<pubDate>Fri, 18 Sep 2026 14:06:00 +0800</pubDate></item>"
    )

    entry = parse_entry(item, "科技", "https://www.ithome.com/rss/")

    assert entry["publish_time"] is not None
    assert entry["publish_time"].year == 2026


# ============================================================
# fake_views：模拟浏览量（必须是确定性的）
# ============================================================
def test_fake_views_is_stable():
    """
    同一个标题必须永远算出同一个值。
    否则每重灌一次数据，排行榜就变一次，看起来像 bug、也没法解释。
    """
    assert fake_views("某条新闻标题") == fake_views("某条新闻标题")


def test_fake_views_differs_between_titles():
    """不同标题一般不该算出同一个值（这里只抽查几个，不做严格保证）"""
    values = {
        fake_views("标题一"),
        fake_views("标题二"),
        fake_views("标题三"),
    }
    assert len(values) >= 2


def test_fake_views_in_expected_range():
    """落在 1000 ~ 19999 之间（代码里就是这么算的）"""
    for title in ["a", "标题二", "很长很长的一条新闻标题" * 3]:
        value = fake_views(title)

        assert 1000 <= value <= 19999
