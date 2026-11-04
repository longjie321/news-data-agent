# 定义接口的"请求格式"（前端要传什么）。
# Pydantic 会在请求进入你的函数之前先做校验：

# 【导包说明】
#   pydantic —— pip 装的，FastAPI 的数据校验就是靠它
#     BaseModel       ：所有请求/响应模型的基类
#     Field           ：给字段加约束（必填、长度限制、说明）
#     field_validator ：字段级的"自定义校验钩子"（下面第 34 行用了，那里有解释）
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    """一条历史对话消息"""

    # ⚠️ Field(...) 里那三个点不是省略号的意思，是 Pydantic 的约定：
    role: Literal["user", "assistant"] = Field(
        ...,
        description="角色：只能是 user 或 assistant",
    )

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
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=20,
        description="历史消息，可以不传（最多 20 条）",
    )

    # ⚠️ 光有 min_length=1 拦不住"纯空格" —— 一个空格的长度也是 1。
    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        # 先去掉首尾空白，再判断；顺便把"干净的值"返回去，
        # 这样后面用到的 question 就是修剪过的（不会带着前后空格进提示词）
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("问题不能是空白")

        return cleaned
