# ============================================================
# 【这个文件干什么】
#   对外的 HTTP 接口：POST /api/agent/chat
#   把 crud/agent.py 里 Agent 循环产生的"事件"，
#   翻译成 SSE 报文，一行行推给前端。
#
# 【SSE 是什么（简单复习）】
#   一种"服务器一行行往浏览器推文本"的格式。发一条消息要写三行：
#       event: 事件名
#       data:  内容
#       （空行，表示这条发完了）
#
# 【这个接口一共六类事件】
#   mode         开场：告诉前端这次有哪些工具可用
#   tool_call    模型决定调用工具了（前端可以显示"正在查询数据库…"）
#   tool_result  工具执行完了，带着查询结果
#   delta        回答正文的一小段（会来很多次，就是打字机效果）
#   done         收尾：回答字数、耗时、调了几次工具
#   error        出错了
#
#   和项目1 的 ragchat.py 比多了 tool_call / tool_result 两类 ——
#   因为 Agent 的过程比 RAG 复杂，前端需要看到"它此刻在查什么"。
#
# 【注意】
#   配置类错误（比如没配 key）必须在"开始发流"之前抛出去。
#   一旦流开始发，HTTP 状态码就已经发出去了，改不了，只能靠 error 事件告诉前端。
# ============================================================

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.db_conf import get_db
from config.llm_conf import LLM_API_KEY
from crud.agent import run_agent
from models.news import News
from schemas.agent import AgentChatRequest
from tools.registry import TOOLS

router = APIRouter(prefix="/api/agent", tags=["智能体"])


def sse(event: str, data: dict) -> str:
    """
    拼一条 SSE 报文：event 行 + data 行 + 空行。

    "event: " 和 "data: " 后面各有一个空格（少了格式就不对）；
    结尾两个 \n 表示"空一行"，也就是"这条消息发完了"。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _collect_tool_names() -> list:
    """
    从工具清单里取出所有工具名，用来在 mode 事件里告诉前端"有哪些工具"。

    这里故意用 for 循环而不是一行写完，方便你看清每一步在干什么。
    """
    names = []
    for item in TOOLS:
        function_info = item.get("function") or {}
        names.append(function_info.get("name"))
    return names


@router.post("/chat", summary="智能体对话（流式）")
async def agent_chat(
    data: AgentChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    智能体对话主接口。

    Depends(get_db) 是 FastAPI 的依赖注入：
      FastAPI 看到这个参数，会自己去调 config/db_conf.py 里的 get_db()，
      把数据库会话塞进来，请求结束还会帮你关掉。你不用手动开关连接。
    """
    # 配置类错误：必须在开流之前拦下来
    if not LLM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="后端未配置 LLM_API_KEY，请在 .env 中设置",
        )

    async def event_stream():
        started = time.perf_counter()
        answer = ""
        tool_call_count = 0

        # 第一条事件：把有哪些工具告诉前端（前端可以展示成"可用能力"）
        yield sse(
            "mode",
            {
                "mode": "agent",
                "tools": _collect_tool_names(),
            },
        )

        # 消费 Agent 循环产生的事件，逐个翻成 SSE 报文
        async for event_name, payload in run_agent(db, data.question, data.history):
            if event_name == "tool_call":
                tool_call_count = tool_call_count + 1
                yield sse("tool_call", payload)

            elif event_name == "tool_result":
                yield sse("tool_result", payload)

            elif event_name == "answer":
                piece = payload.get("content") or ""
                answer = answer + piece
                yield sse("delta", {"content": piece})

            elif event_name == "error":
                yield sse("error", payload)
                # 出错就直接结束，不再往下发 done（避免前端同时收到 error 和 done）
                return

        # 最后一条事件：回答字数 + 耗时 + 调了几次工具
        latency_ms = int((time.perf_counter() - started) * 1000)
        yield sse(
            "done",
            {
                "answer_length": len(answer),
                "latency_ms": latency_ms,
                "tool_call_count": tool_call_count,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 不加这个，部署到 Nginx 后面会被缓冲，打字机效果就没了
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stats", summary="数据源统计")
async def agent_stats(db: AsyncSession = Depends(get_db)):
    """
    看看新闻库里有几条数据 —— 演示的时候用得上，
    也让接口自己证明了"后端确实连上数据库了"。
    """
    stmt = select(func.count()).select_from(News)
    total = await db.scalar(stmt)

    if total is None:
        total = 0

    return {
        "code": 200,
        "message": "查询成功",
        "data": {"news_count": total},
    }


@router.get("/health", summary="健康检查")
async def health():
    """确认服务活着、key 配上了没有"""
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "service": "news-agent",
            "llm_key_configured": bool(LLM_API_KEY),
        },
    }
