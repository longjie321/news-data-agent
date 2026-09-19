# ============================================================
# 【这个文件干什么】
#   测试 HTTP 接口层 —— 请求进来、响应出去这一层对不对。
#
# 【怎么跑】
#   .venv\Scripts\python.exe -m pytest tests/test_api.py
#
# 【为什么这里不测"成功对话"那一条路径】
#   真正的对话会去调用 DeepSeek 接口，**是要花钱的**。
#   单元测试应该随时能跑、不花钱、不依赖外部服务，
#   所以这里只测"不用调模型就能确定的那些事"：
#     健康检查、统计、页面能不能打开、参数不合法时有没有被拦住。
#   （需要真实调模型的端到端验证，用另一个脚本单独做）
#
# 【TestClient 是什么】
#   FastAPI 自带的测试客户端（底层用 httpx）。它不需要真的启动服务、
#   不需要占端口，你调 client.get(...) 它就内部把请求交给 app 处理。
#   所以测试跑得很快，也不会和正在运行的服务抢端口。
# ============================================================

from fastapi.testclient import TestClient

from crud.agent import has_fabricated_count, is_small_talk, should_force_tool
from main import app

# 整个文件共用一个客户端就行
client = TestClient(app)


# ============================================================
# 基础接口
# ============================================================
def test_health():
    response = client.get("/api/agent/health")

    assert response.status_code == 200

    body = response.json()
    assert body["code"] == 200
    assert body["data"]["service"] == "news-agent"
    # key 有没有配上——没配的话对话功能是坏的，所以这里要检查
    assert body["data"]["llm_key_configured"] is True


def test_stats():
    response = client.get("/api/agent/stats")

    assert response.status_code == 200

    body = response.json()
    assert body["data"]["news_count"] > 0


def test_index_returns_the_chat_page():
    """根路径应该返回对话页面（不是一串 JSON）"""
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "新闻数据智能体" in response.text


# ============================================================
# 参数校验：应该在"进模型之前"就被拦住（也就不会花钱）
# ============================================================
def test_chat_rejects_empty_question():
    response = client.post("/api/agent/chat", json={"question": ""})

    assert response.status_code == 422


def test_chat_rejects_missing_question():
    response = client.post("/api/agent/chat", json={})

    assert response.status_code == 422


def test_chat_rejects_too_long_question():
    """超过 500 字要拒掉，防止有人塞长文本过来烧钱"""
    response = client.post("/api/agent/chat", json={"question": "问" * 501})

    assert response.status_code == 422


def test_chat_rejects_object_history():
    """
    ★ 这条测试是给一个真实踩过的坑上的保险：
      前端曾经因为变量名撞上浏览器的 window.history，
      把 history 发成了一个空对象 {}，结果一直报 422。
      这里明确断言"history 是对象时必须 422"，
      以后要是有人把校验改松了，这条测试会立刻红。
    """
    response = client.post(
        "/api/agent/chat",
        json={"question": "你好", "history": {"role": "user"}},
    )

    assert response.status_code == 422


def test_chat_rejects_bad_history_role():
    """history 里每条消息的 role/content 都要合法"""
    response = client.post(
        "/api/agent/chat",
        json={
            "question": "你好",
            "history": [{"role": "user"}],   # 缺 content
        },
    )

    assert response.status_code == 422


def test_chat_rejects_too_many_history():
    """
    ★ 这条是排查时补的：history 原来【没有条数上限】，
      实测 5000 条会被原样放行（每条内容还能塞 4000 字）→
      一个请求就能把体积撑到几十 MB（既烧 token 又可能打爆内存）。
      现在限制成 20 条，这条测试防止以后被人改回去。
    """
    too_many = [{"role": "user", "content": "x"} for _ in range(21)]

    response = client.post(
        "/api/agent/chat",
        json={"question": "你好", "history": too_many},
    )

    assert response.status_code == 422


def test_history_limit_allows_20():
    """
    20 条以内必须能通过（别把上限卡得太死）。
    这里直接用 Pydantic 构造、不经过接口 —— 走接口会真的调模型、要花钱。
    """
    from schemas.agent import AgentChatRequest

    obj = AgentChatRequest(
        question="测试",
        history=[{"role": "user", "content": "x"}] * 20,
    )

    assert len(obj.history) == 20


# ============================================================
# 接口清单（防止以后改了路由前缀却没人发现）
# ============================================================
def test_openapi_has_all_endpoints():
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    for expected in (
        "/",
        "/api/agent/chat",
        "/api/agent/stats",
        "/api/agent/health",
    ):
        assert expected in paths, "接口 %s 不见了！" % expected


# ============================================================
# Agent 的"兜底要不要强制查库"逻辑（纯函数，不调模型、不花钱）
#
# 这段逻辑很容易改坏、而且坏了的后果很隐蔽：
#   强制多了 → 越界问题（问天气、问无关话题）被硬塞一次数据库查询；
#   强制少了 → 模型偷懒不查库、直接编数字，数据类应用里这是致命的。
# 所以两侧都要钉住。
# ============================================================
def test_greeting_is_small_talk():
    assert is_small_talk("你好") is True
    assert is_small_talk("谢谢啦") is True


def test_data_question_is_not_small_talk():
    """带数据意图词的问题，不能被当成寒暄"""
    assert is_small_talk("科技类有多少篇？") is False


def test_generic_time_word_alone_is_not_data_intent():
    """
    ★ 这是实测踩过的坑，必须钉住：
      "今天天气怎么样？" 里有"今天"。原来"今天"在数据词表里，
      于是被判成数据问题 → 模型被强行按回去查库 →
      随机挑了个工具调，回答里还塞一段不相关的新闻统计。
      泛时间词不该单独构成"数据意图"。
    """
    assert is_small_talk("今天天气怎么样？") is False
    # 不寒暄、也不含强数据词 → 不该强制查库
    assert should_force_tool("今天天气怎么样？", False, "天气我查不到，建议看天气 App") is False


def test_fabricated_count_detected():
    """没查库却报出"X 篇/X 条" → 判定为偷懒，要强制它去查"""
    assert has_fabricated_count("根据数据库，科技类共有 51 篇。") is True
    assert should_force_tool("那科技类有多少篇？", False, "共有 51 篇") is True


def test_fabricated_count_not_fooled_by_tool_count():
    """"我有 7 个工具"这种带数字但不是"篇/条"的，不能误判"""
    assert has_fabricated_count("我有 7 个工具可以帮你查新闻。") is False


def test_force_tool_signal_1_strong_data_word():
    """信号①：问题里有明确数据词 → 第一轮就强制（哪怕它答得挺像样）"""
    assert should_force_tool("库里一共有多少条新闻？", False, "稍等，我看看") is True


def test_force_tool_skipped_for_small_talk():
    """寒暄绝不强制"""
    assert should_force_tool("你好", True, "你好！我是新闻数据助手") is False


def test_plain_chat_question_not_forced():
    """既不是寒暄、也不是查数的普通问题 → 不强制"""
    assert should_force_tool("给我讲个笑话", False, "好的，从前有座山……") is False


# ============================================================
# 流式响应的"最外层兜底"（2026-09-19 排查补上的）
#
# 这个测试**不调模型**：它用一个"假装崩溃的 Agent"替换掉真的 run_agent，
# 所以跑得飞快、也不花钱。
# ============================================================
def test_chat_emits_error_event_when_agent_crashes(monkeypatch):
    """
    ★ Agent 循环抛出未预期的异常时，接口层必须发一条 error 事件，
      而不是把连接直接掐断。

    为什么这条值得测：
      流一旦开始发，HTTP 状态码就改不了了 —— 之后再抛异常，框架只能断连。
      前端看到的现象是"转圈转到一半突然没了"，既没有 error 也没有 done，
      用户会以为卡死。这层兜底就是保证"任何意外都至少能变成一条 error 事件"。

    实测背景：run_agent 内部已经把大部分异常转成了事件，但覆盖不全 ——
      SSE 序列化失败、数据库连接中断、以后新加的代码忘了处理……
      所以接口层必须有最后一道网。
    """
    import routers.agent as router_module

    async def crashing_agent(db, question, history):
        # 先正常发一条事件：模拟"流已经开始发了"这个前提
        yield ("tool_call", {"name": "count_news_by_category", "arguments": {}})
        # 然后抛一个内部没接住的异常
        raise RuntimeError("模拟一个没人处理的崩溃")

    monkeypatch.setattr(router_module, "run_agent", crashing_agent)

    response = client.post("/api/agent/chat", json={"question": "体育类有多少篇新闻"})

    # 状态码仍然 200（流早开始发了，改不了），但内容里必须有 error 事件
    assert response.status_code == 200
    assert "event: error" in response.text
    # 异常类型要告诉前端（方便定位问题）
    assert "RuntimeError" in response.text


def test_crash_error_message_does_not_leak_internals(monkeypatch):
    """
    异常的具体消息**不该**送到浏览器 —— 里面可能带着数据库连接串之类的敏感内容。
    只把"异常类型"告诉前端，完整信息打在服务端控制台。
    （这叫"日志给开发看、报错给用户看"）
    """
    import routers.agent as router_module

    async def crashing_agent(db, question, history):
        yield ("tool_call", {"name": "x", "arguments": {}})
        raise RuntimeError("mysql+aiomysql://root:my-secret-password@127.0.0.1/news")

    monkeypatch.setattr(router_module, "run_agent", crashing_agent)

    response = client.post("/api/agent/chat", json={"question": "测试"})

    assert "my-secret-password" not in response.text
    assert "aiomysql" not in response.text


def test_chat_returns_500_when_no_llm_key(monkeypatch):
    """
    没配 key 时必须**在开流之前**拦下来（返回 500）。

    为什么必须在开流前：流一旦开始发，HTTP 状态码就改不了了 ——
    那时候只能靠 error 事件报错，状态码还是 200，监控和调用方都会误判"成功了"。
    """
    import routers.agent as router_module

    monkeypatch.setattr(router_module, "LLM_API_KEY", "")

    response = client.post("/api/agent/chat", json={"question": "你好"})

    assert response.status_code == 500
    assert "LLM_API_KEY" in response.json()["detail"]


def test_chat_emits_error_event_when_llm_call_fails(monkeypatch):
    """
    上游调用失败（比如 key 无效、余额不足、接口 500）时，
    必须发一条 error 事件，而不是让流断掉。

    这里把 call_llm_once 换成"必然抛异常"的版本 —— 不真的发网络请求，所以不花钱。
    """
    import crud.agent as agent_module

    async def failing_call(messages, force_tool=False):
        raise RuntimeError("AI 接口错误(401)：Authentication Fails")

    monkeypatch.setattr(agent_module, "call_llm_once", failing_call)

    response = client.post("/api/agent/chat", json={"question": "体育类有多少篇新闻"})

    assert response.status_code == 200
    # 开场事件照发（流已经开始了）
    assert "event: mode" in response.text
    # 然后必须有一条 error 把失败告诉前端
    assert "event: error" in response.text
    assert "调用模型失败" in response.text
    # 出错后不该再发 done（否则前端会以为"正常结束了"）
    assert "event: done" not in response.text


def test_chat_rejects_blank_question():
    """
    ★ 纯空白的问题必须被拒绝。

    实测过：`min_length=1` 拦不住 " "（一个空格的长度也是 1），
    会真的去调一次大模型 —— 白花钱、还白等好几秒。
    （页面上有 trim 检查，但直接调接口这条路径没有，所以校验要放服务端。）
    """
    for blank in [" ", "   ", "  \n  "]:
        response = client.post("/api/agent/chat", json={"question": blank})

        assert response.status_code == 422, "空白输入 %r 竟然通过了" % blank


def test_question_is_trimmed():
    """前后空白应该被自动修剪（用 Pydantic 直接构造，不走接口、不花钱）"""
    from schemas.agent import AgentChatRequest

    obj = AgentChatRequest(question="  体育类有多少篇  ")

    assert obj.question == "体育类有多少篇"
