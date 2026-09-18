# ============================================================
# 【这个文件干什么】
#   放"新闻数据源（天聚数行）"的配置。
#
# 【注意】
#   API key 从 .env 读，不写死在代码里。
#   接口地址和"接口对应哪个分类"的映射也放在这里，想抓别的分类只改这一个文件。
# ============================================================

import os

from dotenv import load_dotenv

load_dotenv()

# 天聚数行的 APIKEY（从 .env 读）
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# 接口的基础地址（所有接口都是 基础地址/接口名/index 这个格式）
NEWS_API_BASE = os.getenv("NEWS_API_BASE", "http://api.tianapi.com")

# ------------------------------------------------------------
# 【接口名 → 新闻分类】的对应关系
#
# 天聚数行的新闻接口是按频道分开的，每个接口一类新闻。
# 比如 keji 接口拿到的都是科技新闻，那就全归到"科技"分类里。
#
# 想抓哪几类，就在这里留哪几行。
# 没在天聚数行"申请"过的接口，脚本会自动跳过并提示你（不会报错中断）。
#
# ⚠️ 注意：yule（娱乐）这个接口名是不存在的（实测返回 404 api不存在），
#    所以娱乐类新闻只能用 scripts/seed_data.py 本地补。
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# RSS 数据源（不用注册、不用审核，拿到就能抓）
#
# 【为什么除了 API 还要留一条 RSS 路】
#   天聚数行要实名认证 + 逐个申请接口，审核得等；
#   RSS 是公开发布的，立刻能用。两条路都留着，哪条通用哪条。
#
# 【为什么 8 个分类只需要 6 个地址】
#   门户网站（中新网、人民网）**每个频道各有一个 RSS**，
#   所以一个站点就能提供好几个分类，不用去找 8 个不同站点。
#   中新网一个站就覆盖了国内/国际/社会/财经/体育 5 类。
#
# ⚠️ RSS 源可能随时失效（这是公开源的常态）。
#    运行时某个源挂了，脚本会打印原因并跳过，不影响其他源。
#    真失效了，把对应那行注释掉、或者换一个源就行。
# ------------------------------------------------------------
RSS_SOURCES = {
    "国内": "https://www.chinanews.com.cn/rss/china.xml",
    "国际": "https://www.chinanews.com.cn/rss/world.xml",
    "社会": "https://www.chinanews.com.cn/rss/society.xml",
    "财经": "https://www.chinanews.com.cn/rss/finance.xml",
    "体育": "https://www.chinanews.com.cn/rss/sports.xml",
    "娱乐": "http://www.people.com.cn/rss/ent.xml",
    "科技": "https://www.ithome.com/rss/",
    "头条": "http://www.people.com.cn/rss/politics.xml",
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
