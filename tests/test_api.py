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
