# ============================================================
# 【这个文件干什么】
#   定义接口的"请求格式"（前端要传什么）。
#   Pydantic 会在请求进入你的函数之前先做校验：
#   字段少了、类型不对、太长，直接返回 422 错误，你不用自己写 if 判断。
#
# 【和项目1 的差别】
#   项目1 的请求体叫 RagChatRequest，里面有个 top_k（要检索几块）；
#   这里不需要 top_k —— "取几条"变成了模型自己决定的工具参数。
# ============================================================

# 【导包说明】
#   pydantic —— 【三方库】pip 装的，FastAPI 的数据校验就是靠它
#     BaseModel       ：所有请求/响应模型的基类
#     Field           ：给字段加约束（必填、长度限制、说明）
#     field_validator ：字段级的"自定义校验钩子"（下面第 34 行用了，那里有解释）
from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    """一条历史对话消息"""

    # ⚠️ Field(...) 里那三个点不是省略号的意思，是 Pydantic 的约定：
    #    写 "..." 表示"这个字段必填"（不写就会有默认值是可选）
    #    这个写法有点反直觉，特别记一下。
    role: str = Field(..., description="角色：user 或 assistant")

    content: str = Field(
        ...,
        max_length=4000,
        description="消息内容",
    )


class AgentChatRequest(BaseModel):
    """智能体对话请求"""

    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="用户的问题",
    )

    # ⚠️ 为什么写 default_factory=list，而不是直接写 = []？
    #   因为 Python 有个经典坑：可变类型（列表、字典）当默认值会被所有实例共享，
    #   一个请求往里塞了东西，别的请求也会看到。
    #   default_factory 的意思是"每次要默认值的时候，现调一次 list() 造个新的"。
    #
    # ⚠️ max_length=20 是后来排查时补上的 —— 原来这里【没有任何条数上限】：
    #   实测构造 5000 条 history 会被原样放行（每条内容还能塞 4000 字），
    #   一个请求就能把体积撑到几十 MB。后果有两个：
    #     ① 花钱：这些内容都会进模型上下文，按 token 计费
    #     ② 稳定性：请求体过大可能直接把内存打爆
    #   前端本来就只保留最近 6 条，这里给 20 条已经足够宽松了。
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=20,
        description="历史消息，可以不传（最多 20 条）",
    )

    # ⚠️ 光有 min_length=1 拦不住"纯空格" —— 一个空格的长度也是 1。
    #   实测：一个空格 / 三个空格 / 换行加空格，全都能通过校验，
    #   然后真的去调一次大模型（白花钱、还白等几秒）。
    #   （页面上已经做了 trim 检查，但"直接调接口"这条路径没有 ——
    #     校验要放在服务端才可靠，前端那道只能算体验优化。）
    #
    # 【field_validator 是什么（这个语法第一次出现，解释一下）】
    #   它是 Pydantic（【三方库】）提供的"字段级校验钩子"：
    #   字段被赋值之后，这个函数会自动跑一遍。
    #   里面 raise ValueError 就等价于"校验不通过"，
    #   FastAPI 会把它变成 422 响应返回给调用方。
    #   （@classmethod 是 Pydantic 要求的写法，表示这个方法挂在类上而不是实例上）
    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        # 先去掉首尾空白，再判断；顺便把"干净的值"返回去，
        # 这样后面用到的 question 就是修剪过的（不会带着前后空格进提示词）
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("问题不能是空白")

        return cleaned
