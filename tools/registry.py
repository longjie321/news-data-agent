# ============================================================
# 【这个文件干什么】
#   把 definitions.py 里那 8 个函数，"登记"成模型能看懂的工具。
#
#   每个工具要登记三样：
#     ① name         工具名（模型要调用时说这个名字）
#     ② description  工具说明 ★★★ 最重要，模型全靠它判断"什么时候用这个工具"
#     ③ parameters   参数结构（JSON Schema 格式），模型靠它知道要传什么参数
#
# 【为什么 description 最重要】
#   模型不知道你写了什么代码，它只读得到这段文字。
#   description 写不清楚，就会出现两种错误：
#     - 该调的时候不调：用户问"体育类多少篇"，它自己编一个数字回答
#     - 不该调的时候乱调：用户说"你好"，它也去查一遍数据库
#   所以每个 description 都写清了两件事：这个工具干什么 + 什么时候用它。
#   （面试聊到"提示词工程"，这就是一个具体可讲的例子）
#
# 【写 description 的一条经验（针对工具有重叠的情况）】
#   工具加到 7 个之后，出现了"功能相近"的工具，比如：
#     ④ count_news_by_time（只给一个总数）  vs  ⑦ get_category_trend（给每天的序列）
#     ① count_news_by_category（一个分类）    vs  ⑥ compare_categories（对比两个分类）
#   这种时候要在 description 里明确写"什么时候**不要**用我"，
#   否则模型容易挑一个近似的糊过去。下面 ⑥⑦ 都加了这样的提示。
#
#   ★ 2026-09-20 又加了一对，而且这一对是"答非所问"的典型来源：
#     ③ get_top_news（按【浏览量】排）  vs  ⑧ get_longest_news（按【正文长度】排）
#   实测的真实案例：用户问"最长的一篇"，模型却调了③（按浏览量排），
#   然后只能说"排行榜不返回正文长度，我做不到"。
#   加上⑧之后，两个工具的 description 里都写明了彼此的区分标准（"最热" vs "最长"）。
#
# 【注意】
#   parameters 的结构是模型那边的接口规范（JSON Schema），
#   结构是固定的，不能自己随手改字段名。
# ============================================================

import json

from sqlalchemy.ext.asyncio import AsyncSession

# 分类清单文本（"体育、科技、财经…"）从 config 那边取，
# 保证"工具描述里给模型看的可选值"和代码里其他地方永远一致 ——
# 以前这串字在下面写了两遍，改分类时很容易漏。
from config.news_conf import CATEGORY_LIST_TEXT
from tools.definitions import (
    compare_categories,
    count_news_by_category,
    count_news_by_time,
    get_category_trend,
    get_longest_news,
    get_news_detail,
    get_top_news,
    search_news,
)

# ------------------------------------------------------------
# 工具清单：发给大模型的就是这个列表
#
# 结构说明：
#   type      固定写 "function"，表示这是"函数型工具"
#   function.name         工具名（对应 definitions.py 里的函数名）
#   function.description  给模型看的说明（中文写清楚，模型能看懂中文）
#   function.parameters   参数表：
#       type        固定写 "object"
#       properties  每个参数的名字、类型、说明
#       required    哪些参数是必填的（没有必填参数就写空列表）
# ------------------------------------------------------------
TOOLS = [
    # ========== 第一批：按"用户怎么问"设计的 ==========
    {
        "type": "function",
        "function": {
            "name": "count_news_by_category",
            "description": (
                # 【描述为什么写成这个结构】
                #   工具描述是"给模型看的说明书"，它怎么排会影响模型选哪个工具。
                #   模型对描述的开头最敏感、对末尾最不敏感，所以：
                #     · 先讲清"这个工具管什么、不管什么"（尤其是**要和别的工具区分**的部分）
                #     · 三种用法按 ①②③ 并列，不把重要的判断埋在一段长句的结尾
                #   实测踩过的坑：原来"要对比两个分类请用 compare_categories"这句
                #   被放在 200 多字描述的**最后一句**，模型会漏读，于是问
                #   "A 和 B 哪个更火"时错调成本工具、只答了一个分类。
                "统计新闻篇数。先按下面的规则选对用法：\n"
                "① 问总数、或问各分类的分布（“一共有多少条新闻”“分成哪几类、各多少篇”）\n"
                "   → 用本工具，category 留空。一次就返回总数 + 每个分类各自的篇数，\n"
                "     比逐个分类查要快得多。\n"
                "② 问某一个分类的数量（“体育类有多少篇”“科技类新闻多不多”）\n"
                "   → 用本工具，传 category。\n"
                "③ 问两个分类的比较（“A 和 B 哪个更火”“A 和 B 哪个新闻多”）\n"
                "   → 不要用本工具，改用 compare_categories。\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "新闻分类名，必须是这八个之一："
                            f"{CATEGORY_LIST_TEXT}。"
                            "如果用户问的是总数、或者要所有分类的分布，这个参数不要传。"
                        ),
                    },
                },
                # 不传 category 也是合法的（返回全部分类 + 总数），所以这里是空列表
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": (
                "按关键词搜索新闻标题，返回匹配到的新闻列表（每条都带 id）。"
                "当用户想找“关于某个话题或某个人物的新闻”时使用。"
                "如果用户还要看某一篇的具体内容，先调用本工具拿到 id，再调用 get_news_detail。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的关键词，比如“人工智能”“梅西”",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回几条，默认 5，最多 20",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_news",
            "description": (
                "按【浏览量】从高到低取前几篇新闻，也就是热门排行榜（每条都带 id）。"
                "当用户问“哪篇最热门”“浏览量最高的是哪篇”时使用。"
                "⚠️ 本工具只按浏览量排。如果用户问的是“最长 / 内容最多 / 字数最多”，"
                "要用 get_longest_news，不要用本工具 —— 两者排序依据完全不同，"
                "用错了会答非所问。"
                "如果用户还要看某一篇的具体内容，再调用 get_news_detail。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "取前几名，默认 5，最多 20",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_news_by_time",
            "description": (
                "统计最近 N 天发布了多少篇新闻，也可以只统计某个分类，返回一个总数。"
                "当用户问“最近一周发了多少篇”“今天有多少条新新闻”"
                "“最近一周科技类有多少篇”时使用。"
                "如果用户想知道的是每天的走势（涨还是跌），改用 get_category_trend。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": (
                            "统计最近多少天：1 表示今天，7 表示最近一周，"
                            "30 表示最近一个月，默认 7"
                        ),
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "可选。只想统计某一个分类时填，比如“科技”。"
                            "不填就表示统计所有分类。"
                            f"可选值：{CATEGORY_LIST_TEXT}"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    # ========== 第二批：按"必须多跳"设计的 ==========
    {
        "type": "function",
        "function": {
            "name": "get_news_detail",
            "description": (
                "按新闻编号查它的详细内容（标题、分类、浏览量、来源、正文）。"
                "当用户问“刚才搜到的那篇讲了什么”“最热门那篇的内容是什么”时使用。\n"
                "⚠️ 编号不能凭空编，必须先调用 search_news、get_top_news 或 get_longest_news "
                "拿到 id，再调本工具。\n"
                "⚠️ 默认只返回正文前 300 字（回答“讲了什么”够用了）。"
                "只有用户明确要“完整正文 / 全文 / 一字不落地发给我”时，才把 full 传成 true。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "news_id": {
                        "type": "integer",
                        "description": (
                            "新闻编号，必须来自 search_news、get_top_news 或 get_longest_news "
                            "返回结果里的 id 字段，不要自己猜一个数字"
                        ),
                    },
                    "full": {
                        "type": "boolean",
                        "description": (
                            "是否返回完整正文。默认 false（只给前 300 字）。"
                            "只有用户明确要求完整正文时才传 true。"
                        ),
                    },
                },
                "required": ["news_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_categories",
            "description": (
                "对比两个分类：分别有多少篇、总浏览量和平均浏览量各是多少，"
                "并直接给出“谁新闻更多”“谁浏览量更高”的结论。"
                "当用户问“体育类和科技类哪个更火”“财经和娱乐哪个新闻多”时使用。"
                "⚠️ 只问一个分类的数量时用 count_news_by_category，不要用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category_a": {
                        "type": "string",
                        "description": f"第一个分类名，可选值：{CATEGORY_LIST_TEXT}",
                    },
                    "category_b": {
                        "type": "string",
                        "description": "第二个分类名，可选值和第一个一样",
                    },
                },
                "required": ["category_a", "category_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_category_trend",
            "description": (
                "按天统计最近 N 天的发稿量，返回每天的条数序列（比如 9-12 是 3 篇、9-13 是 5 篇）。"
                "当用户问“最近发文量在涨还是在跌”“这几天的走势怎么样”时使用。"
                "返回值是一串数据而不是单个数字，需要你自己对比前后几天得出结论。"
                "⚠️ 只问一个总数时用 count_news_by_time，不要用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": (
                            "可选。只看某一个分类时填，比如“科技”；"
                            "不填就表示全部分类。"
                            f"可选值：{CATEGORY_LIST_TEXT}"
                        ),
                    },
                    "days": {
                        "type": "integer",
                        "description": "统计最近多少天，默认 7，最多 365",
                    },
                },
                "required": [],
            },
        },
    },
    # ========== 第三批：按"最容易答非所问"的问法补的（2026-09-20） ==========
    {
        "type": "function",
        "function": {
            "name": "get_longest_news",
            "description": (
                "按【正文长度】从长到短取前几篇新闻（每条都带 id 和 content_length）。"
                "当用户问“正文最长的是哪篇”“内容最多、字数最多的是哪一篇”"
                "“哪篇写得最长”时使用。\n"
                "⚠️ 和 get_top_news 的区别（这两个很容易搞混，一搞混就是答非所问）："
                "问“最热 / 浏览量最高”用 get_top_news（按浏览量排）；"
                "问“最长 / 内容最多 / 字数最多”用本工具（按正文字数排）。\n"
                "如果用户还要看那一篇的完整正文，先调用本工具拿到 id，"
                "再调 get_news_detail 并把 full 传成 true。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "取前几名，默认 5，最多 20",
                    },
                },
                "required": [],
            },
        },
    },
]


# ------------------------------------------------------------
# 工具名 → 实际要执行的函数
#
# 模型返回的只是"工具名"（一个字符串），
# 要执行的话得靠这张表把名字翻译成真正的函数。
# ------------------------------------------------------------
TOOL_NAME_TO_FUNCTION = {
    "count_news_by_category": count_news_by_category,
    "search_news": search_news,
    "get_top_news": get_top_news,
    "count_news_by_time": count_news_by_time,
    "get_news_detail": get_news_detail,
    "compare_categories": compare_categories,
    "get_category_trend": get_category_trend,
    "get_longest_news": get_longest_news,
}


# ------------------------------------------------------------
# 工具结果的长度保护
#
# 【为什么需要】
#   Agent 循环最多跑 8 轮（MAX_ROUNDS，见 crud/agent.py），每一轮的工具结果都会被累加进
#   messages 里、再发给模型。而 search_news 一次最多能返回 20 条新闻 ——
#   几轮下来上下文会明显膨胀：既费钱（按 token 计费），也可能撑爆模型的上下文窗口。
#
#   所以在这里做一道统一关卡：不管哪个工具，返回值太长就截短。
#   （放在这里而不是写在每个工具里，是因为"所有工具都要做同一件事"，
#     写一处就能全都覆盖，以后加新工具也自动生效。）
# ------------------------------------------------------------
MAX_RESULT_CHARS = 6000

# ★ 这几个字段截断时要保留"尾部"而不是"头部"（2026-09-19 实测发现的问题）
#
# 为什么：同样是"一串按顺序排的数据"，保留哪一头完全相反 ——
#   news   新闻列表：按发布时间【倒序】排，最新的在前 → 保留头部（前几条）
#   series 时间序列：按日期【正序】排，最早的在前面 → 必须保留尾部（最近几天）
#
# 原来一律保留前半段，于是实测出一个隐蔽的 bug：
#   问"最近一年发文走势"，get_category_trend(days=365) 返回 365 天，
#   长度超标被砍到 91 项 —— 模型拿到的是【最早那 91 天】，
#   最近三个多月的数据直接消失了。它据此判趋势，结论会完全反过来，
#   而且它自己完全不知道（因为 note 只说"截断了"）。
#   时间序列的价值恰恰在"最近"，所以 series 必须从尾部保留。
TAIL_KEEP_KEYS = ("series",)


def _json_length(data) -> int:
    """算一下这个结果转成 JSON 文本有多少个字符"""
    return len(json.dumps(data, ensure_ascii=False))


def _limit_result_size(result: dict) -> dict:
    """
    结果太长就砍短。优先砍列表类字段（news / series / compare），每次砍一半。

    ★ 关键细节：砍完一定要加上 truncated 标记。
      否则模型看到 3 条数据，会以为"库里就只有 3 条"，答出错误的结论。
      告诉它"结果被截断了"，它才会在回答里说清楚（或者换个更精确的问法）。
      —— 这个坑叫"静默截断"，是数据类接口的经典事故。
    """
    if _json_length(result) <= MAX_RESULT_CHARS:
        return result

    trimmed = dict(result)

    for key in ("news", "series", "compare"):
        items = trimmed.get(key)

        if not isinstance(items, list):
            continue

        # 时间序列从头砍会丢掉"最新"，所以对它反过来保留尾部（见上面 TAIL_KEEP_KEYS）
        keep_tail = key in TAIL_KEEP_KEYS

        while len(items) > 1:
            # 每次砍掉一半，直到长度达标
            half = len(items) // 2
            items = items[-half:] if keep_tail else items[:half]
            trimmed[key] = items

            if _json_length(trimmed) <= MAX_RESULT_CHARS:
                trimmed["truncated"] = True
                # ★ 必须告诉模型"实际保留了几条"，不能只说"已截断"。
                #   否则模型看到 3 条数据，会以为"库里总共就 3 条"，
                #   答出"全文只有 3 篇"这种错误结论。
                trimmed["kept"] = len(items)
                if keep_tail:
                    trimmed["note"] = (
                        "结果过长已截断，只保留了最近 %d 天" % len(items)
                    )
                else:
                    trimmed["note"] = "结果过长已截断，只保留了前 %d 条" % len(items)
                return trimmed

    # 列表砍到只剩一条还是太长（比如单条正文特别长）→ 整体只给一段预览
    return {
        "truncated": True,
        "kept": 0,
        "note": "结果过长，只能给出开头一段预览，不是完整数据",
        "preview": json.dumps(result, ensure_ascii=False)[:MAX_RESULT_CHARS],
    }


async def run_tool(db: AsyncSession, tool_name: str, arguments: dict) -> dict:
    """
    执行一个工具：按名字找到函数 → 把参数传进去 → 拿到结果 → 做长度检查。

    这是 Agent 循环里的"执行"环节，被 crud/agent.py 调用。

    ★ 这里有一个关键设计：不管出什么错，都返回一个带 error 的字典，不往外抛异常。

      为什么要这样？
        大模型看到 {"error": "..."} 之后，会自己想办法补救 ——
        比如参数名写错了，它下次就改对；查不到数据，它换个关键词再试。
        如果直接把异常抛出去，整个请求就断了，用户看到的是报错页面。
        让错误"回到模型手里"，是 Agent 比普通接口更耐用的关键。
    """
    tool_func = TOOL_NAME_TO_FUNCTION.get(tool_name)

    if tool_func is None:
        return {"error": f"没有名叫 {tool_name} 的工具"}

    try:
        # **arguments 是 Python 的"字典解包"语法：
        #   如果 arguments 是 {"keyword": "AI", "limit": 5}，
        #   那 func(db, **arguments) 就等于 func(db, keyword="AI", limit=5)
        #   这样就不用一个个参数去对应，模型给什么就传什么。（这个语法是【内置】的）
        result = await tool_func(db, **arguments)

        # 统一做长度保护，防止多轮累积把上下文撑爆
        return _limit_result_size(result)

    except TypeError as e:
        # 参数对不上：一般是模型给错了参数名，或者少给了必填参数
        return {"error": f"工具参数不正确：{e}"}

    except Exception as e:
        # 兜底：任何其他异常都变成给模型看的说明，不让整个请求崩掉
        return {"error": f"工具执行失败：{type(e).__name__}: {e}"}
