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
