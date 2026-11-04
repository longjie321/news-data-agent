# 智能体的"大脑" —— Agent 主循环。整个项目的核心就在这个文件里。
# 用户提问

import json
import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config.llm_conf import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL_FOR_TOOLS,
    SYSTEM_PROMPT,
)
from tools.registry import TOOLS, run_tool

# 最多允许模型调几轮工具（防止它绕圈子绕不停）
MAX_ROUNDS = 8

# 判断"这句话要不要查数据库"用的两张词表

# 数据类关键词：只要问题里出现这些，就说明用户是来查数据的
DATA_KEYWORDS = [
    "多少", "几篇", "几条", "几个", "哪些", "排行", "排名", "最高", "最多", "最低",
    "统计", "搜索", "搜一下", "查一下", "查询", "列表", "浏览量",
    "新闻", "头条", "报道",
]

# 寒暄类关键词：打招呼、道谢、告别
SMALL_TALK_KEYWORDS = [
    "你好", "您好", "嗨", "哈喽", "hi", "hello", "谢谢", "多谢", "感谢",
    "再见", "拜拜", "在吗", "你是谁", "介绍一下你", "早上好", "晚上好",
]

# "没查库却报出了条数"的特征：数字 + 篇/条
#
# 为什么用这个组合：这是"新闻条数"的固定说法，闲聊和拒答里不会出现；
# 而"我有 7 个工具"这种虽然带数字，但后面跟的不是篇/条，不会误伤。
FABRICATED_COUNT_PATTERN = re.compile(r"\d+\s*[篇条]")


def is_small_talk(question: str) -> bool:
    """
    判断这是不是"打招呼/闲聊"，也就是"不用查数据库也能答"的问题。

    判断顺序很重要：先看有没有数据类关键词，再看有没有寒暄词。
    因为"你好，体育类有多少篇新闻？"这种句子里两个都有，
    但它明显是要查数据的 —— 所以要优先认数据词。
    """
    text = (question or "").strip().lower()

    if not text:
        return False

    # 第一步：只要出现数据类关键词，就不是寒暄
    for word in DATA_KEYWORDS:
        if word in text:
            return False

    # 第二步：出现寒暄词，就算寒暄
    for word in SMALL_TALK_KEYWORDS:
        if word in text:
            return True

    # 第三步：都不是 —— 先当作正常问题，交给下面的兜底逻辑再判断一次
    return False


def has_fabricated_count(text: str) -> bool:
    """
    判断一段回答里，有没有"没查库却报出来的条数"（比如"共有 51 篇"）。

    这是"模型偷懒"最典型的特征：它不调工具，直接从上下文里猜一个数字说出来。
    闲聊和"我查不到"这类拒答不会出现"X 篇 / X 条"，所以这个信号很准。
    """
    return bool(FABRICATED_COUNT_PATTERN.search(text or ""))


def should_force_tool(question: str, chat_only: bool, first_answer: str) -> bool:
    """
    第一轮模型没调工具时，判断这到底是"偷懒"还是"本来就不该调"。

    两个信号，命中任意一个才认为它在偷懒：
      ① 问题里带明确的数据意图词（多少 / 几篇 / 排行 / 浏览量…）
         —— 用户明摆着是来查数的，不查就是偷懒
      ② 它的回答里已经报出了"X 篇 / X 条"
         —— 没查库却说得出条数，那就是编的

    为什么不能简单地"只要不是寒暄就强制"（这是原来的写法，实测有问题）：
      「今天天气怎么样？」会被泛时间词判成数据问题，模型被强行按回去查库。
      明明它本来答得挺好（"天气我查不到，建议看天气 App"），
      结果被逼着随机调一个工具，回答里还混进一段不相关的新闻统计。
      越界问题应该允许模型直接说"我查不到"，而不是硬塞一次查询。
    """
    if chat_only:
        return False

    text = (question or "").lower()

    for word in DATA_KEYWORDS:
        if word in text:
            return True

    return has_fabricated_count(first_answer)


def build_initial_messages(question: str, history: list) -> list:
    """
    拼出最开始的 messages。

    messages 是个列表，每一项是一条消息，长这样：
        {"role": "system",    "content": "..."}
        {"role": "user",      "content": "..."}
        {"role": "assistant", "content": "..."}

    history 里装的是 Pydantic 对象（schemas/agent.py 里的 ChatMessage），
    所以要用 .role / .content 这种"点号"的方式取值，不能像字典那样用方括号。
    """
    messages = []

    messages.append({"role": "system", "content": SYSTEM_PROMPT})

    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": question})

    return messages


def _build_headers() -> dict:
    """请求 DeepSeek 要带的请求头（Authorization 里的 Bearer 后面有个空格，少了会 401）"""
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }


async def call_llm_once(messages: list, force_tool: bool = False) -> dict:
    """
    非流式地问模型一次：你要不要调工具？

    force_tool 参数：
      默认 False —— 让模型自己决定（把它当"自动挡"）。
      传 True    —— 带上 tool_choice="required"，强制它必须调工具。
                    用来兜底"模型偷懒、不查数据直接编答案"的情况（见下面 run_agent）。

    返回值是 choices[0]，也就是那次回答的内容，结构大概是：
        {
          "finish_reason": "tool_calls" 或 "stop",
          "message": {
              "content": "文字回答（要调工具时这里是 None）",
              "tool_calls": [ {...}, {...} ]
          }
        }

    为什么要"非流式"：这一轮的目的只是拿到 tool_calls 或者判断"它不需要工具了"，
    不需要一个字一个字地看它输出，所以用最简单的非流式请求。
    """
    body = {
        "model": LLM_MODEL_FOR_TOOLS,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        # temperature = 0：让"做决定"这一步尽量稳定。
        "temperature": 0,
    }

    if force_tool:
        # ⚠️ tool_choice 是 DeepSeek（和 OpenAI 一致的）接口参数，三个取值：
        body["tool_choice"] = "required"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers=_build_headers(),
            json=body,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"AI 接口错误({resp.status_code})：{resp.text[:200]}")

    data = resp.json()
    choices = data.get("choices") or []

    if not choices:
        raise RuntimeError("AI 接口没有返回 choices")

    return choices[0]


async def stream_llm_text(messages: list):
    """
    流式地问模型一次，把回答一段段吐出来（这就是打字机效果的来源）。

    yield 的是"文字片段"，不是事件，因为调用方只需要拼起来。
    """
    body = {
        "model": LLM_MODEL_FOR_TOOLS,
        "messages": messages,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{LLM_BASE_URL}/chat/completions",
            headers=_build_headers(),
            json=body,
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                raise RuntimeError(f"AI 接口错误({resp.status_code})：{text[:200]}")

            # aiter_lines() 是"异步地按行读"，上游每来一行就走一轮循环
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                # 去掉开头的 "data: "（正好 6 个字符）
                payload = line[6:].strip()

                # 上游发完会送一个 [DONE] 作为结束标记
                if payload == "[DONE]":
                    break

                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    # 流式传输可能把一行 JSON 切成两半，解析失败跳过就行
                    continue

                choices = obj.get("choices") or []
                if not choices:
                    continue

                delta = choices[0].get("delta") or {}
                piece = delta.get("content")

                if piece:
                    yield piece


async def run_agent(db: AsyncSession, question: str, history: list):
    """
    ★ Agent 主循环 —— 整个智能体的核心。

    这是个"异步生成器"：用 yield 一条条往外送事件，调用方边收边处理。
    产出的是一个元组 (事件名, 数据)，事件名有四种：
        "tool_call"    模型决定要调用某个工具了
        "tool_result"  工具执行完了，带着查询结果
        "answer"       最终回答的一小段文字（会来很多次）
        "error"        出问题了

    为什么要设计成"往外送事件"而不是直接返回结果：
      因为接口层要一边执行一边告诉前端"我正在查数据库"，让用户看到进度。
      这和你项目1 里 stream_ai() 往外 yield 正文片段的思路是一样的。
    """
    if not LLM_API_KEY:
        yield ("error", {"message": "后端未配置 LLM_API_KEY，请在 .env 中设置"})
        return

    messages = build_initial_messages(question, history)

    # 寒暄问题（"你好"这类）不需要查数据库，也就不会被强制调工具
    chat_only = is_small_talk(question)

    # ---- 第一段：循环问模型，要不要调工具 ----
    for round_no in range(1, MAX_ROUNDS + 1):
        try:
            choice = await call_llm_once(messages)
        except Exception as e:
            yield ("error", {"message": f"调用模型失败：{e}"})
            return

        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason")
        tool_calls = message.get("tool_calls") or []

        # ---- 兜底：模型想"不查数据直接答"？把它按回去 ----
        force_needed = False

        if round_no == 1:
            first_answer = message.get("content") or ""
            force_needed = should_force_tool(question, chat_only, first_answer)

        if force_needed:
            if finish_reason != "tool_calls" or not tool_calls:
                try:
                    choice = await call_llm_once(messages, force_tool=True)
                except Exception as e:
                    yield ("error", {"message": f"调用模型失败：{e}"})
                    return

                message = choice.get("message") or {}
                finish_reason = choice.get("finish_reason")
                tool_calls = message.get("tool_calls") or []

        # ---- 情况一：模型不要工具了 → 说明该出最终回答了 ----
        # ⚠️ 判断依据是 finish_reason，不是 content 有没有值
        if finish_reason != "tool_calls" or not tool_calls:
            try:
                async for piece in stream_llm_text(messages):
                    yield ("answer", {"content": piece})
            except Exception as e:
                yield ("error", {"message": f"生成回答失败：{e}"})
            return

        # ---- 情况二：模型要调工具 ----
        # 第一步：把模型这条"要求调工具"的消息原样记进 messages。
        # 不加这一条的话，下面 role="tool" 的结果就没有对应的"请求"，接口会报错。
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )

        # 第二步：逐个执行工具
        for call in tool_calls:
            function_info = call.get("function") or {}
            tool_name = function_info.get("name")
            raw_arguments = function_info.get("arguments") or "{}"

            # 模型给的参数是"JSON 字符串"，要转成字典才能用
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                # 模型偶尔会给格式错误的 JSON，这时候就当它没给参数，让工具用默认值
                arguments = {}

            yield ("tool_call", {"name": tool_name, "arguments": arguments})

            result = await run_tool(db, tool_name, arguments)

            yield ("tool_result", {"name": tool_name, "result": result})

            # 第三步：把工具结果作为一条 role="tool" 的消息拼回去
            # ⚠️ tool_call_id 必须和模型给的 id 一模一样，否则下一次请求直接失败
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    # ---- 循环次数用完了还没结束：说明模型在绕圈子，主动停掉 ----
    yield (
        "error",
        {"message": f"工具调用超过 {MAX_ROUNDS} 轮，已停止。请把问题问得更具体一些。"},
    )
