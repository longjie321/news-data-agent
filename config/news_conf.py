# 放"新闻数据源（天聚数行）"的配置。
# API key 从 .env 读，不写死在代码里。

import os

from dotenv import load_dotenv

load_dotenv()

# 天聚数行的 APIKEY（从 .env 读）
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# 接口的基础地址（所有接口都是 基础地址/接口名/index 这个格式）
NEWS_API_BASE = os.getenv("NEWS_API_BASE", "http://api.tianapi.com")

# 全部分类 —— 整个项目只在这里定义一次
CATEGORIES = ["体育", "科技", "财经", "娱乐", "社会", "国内", "国际", "头条"]

# 给模型看的分类清单文本（用顿号连起来，拼进工具描述里）
# "、".join(...) 是字符串方法：用顿号把这个列表连成一整串。
CATEGORY_LIST_TEXT = "、".join(CATEGORIES)

# 【接口名 → 新闻分类】的对应关系
SOURCE_CATEGORY_MAP = {
    "generalnews": "头条",
    "guonei": "国内",
    "world": "国际",
    "keji": "科技",
    "tiyu": "体育",
    "caijing": "财经",
    "social": "社会",
}

# 每次请求最多能取多少条（天聚数行的上限是 50）
PAGE_SIZE = 50


# RSS 数据源（不用注册、不用审核，拿到就能抓）
RSS_SOURCES = {
    "国内": "https://www.chinanews.com.cn/rss/china.xml",
    "国际": "https://www.chinanews.com.cn/rss/world.xml",
    "社会": "https://www.chinanews.com.cn/rss/society.xml",
    "财经": "https://www.chinanews.com.cn/rss/finance.xml",
    "体育": "https://www.chinanews.com.cn/rss/sports.xml",
    "娱乐": "https://www.chinanews.com.cn/rss/culture.xml",
    "头条": "https://www.chinanews.com.cn/rss/scroll-news.xml",
    "科技": "https://www.ithome.com/rss/",
}

# 抓 RSS 时用的浏览器标识。
# 有些站点会拒绝没有 User-Agent 的请求（当成爬虫拦掉），
# 带一个正常的浏览器标识就不会被拦。
RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
