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
#     BaseModel ：所有请求/响应模型的基类
#     Field     ：给字段加约束（必填、长度限制、说明）
from pydantic import BaseModel, Field


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
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="历史消息，可以不传",
    )
