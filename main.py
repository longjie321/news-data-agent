# ============================================================
# 【这个文件干什么】
#   项目的启动入口。运行这个文件，服务就起来了。
#
# 【怎么跑】
#   命令行    ：先 cd 到项目根目录，然后  python main.py
#   PyCharm   ：右键这个文件 → Run 'main'
#   起来之后  ：
#     浏览器打开 http://127.0.0.1:8020        → 对话页面（给人用的）
#     浏览器打开 http://127.0.0.1:8020/docs   → 接口文档（给开发调试用的）
#
# 【注意】
#   端口用 8020，不用 8000 —— 刻意避开项目1（它占 8000），
#   这样两个项目可以同时开着，互不影响。
# ============================================================

# 【导包说明】
#   uvicorn —— 【三方库】ASGI 服务器，FastAPI 的"发动机"，
#              没有它 FastAPI 只是个框架，跑不起来
#   FastAPI —— 【三方库】Web 框架本体
#   Path    —— 【Python 内置】pathlib 模块里的路径工具，用来拼文件路径
#   FileResponse —— 【三方库 FastAPI 自带】专门用来"返回一个文件"的响应类型
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pathlib import Path

# 从 routers 包里导入 agent 模块（就是 routers/agent.py）
from routers import agent

# 静态文件目录（放前端页面的地方）。
# ⚠️ 这里用 Path(__file__).parent 拼出"绝对路径"，而不是写 "static" 这种做法。
#   因为写相对路径的话，"从哪个目录启动程序"会影响能不能找到它 ——
#   这个坑在项目1 的 CHROMA_DIR 上踩过一次（换成相对路径会生成第二份数据）。
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="新闻数据分析智能体（LLM Agent）",
    description="用自然语言查询新闻数据的 AI Agent 服务（DeepSeek Function Calling）",
    version="1.0.0",
)

# 把 agent 模块里的路由挂到 app 上
app.include_router(agent.router)


@app.get("/", summary="对话页面")
async def index():
    """
    根路径：直接返回前端页面（static/index.html）。
    浏览器打开 http://127.0.0.1:8020 就是聊天界面，不是一堆 JSON 了。

    页面和接口由"同一个服务"提供，所以是同源的，
    不需要额外配 CORS（跨域）——这也是把页面放在项目里的一个好处。
    """
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    # ⚠️ 这里传的是字符串 "main:app" 而不是 app 对象本身。
    #   因为 reload=True（改代码自动重启）要求 uvicorn 自己去 import 模块，
    #   不能接一个已经创建好的对象。（项目1 的 main.py 里也是这么写的）
    uvicorn.run("main:app", host="127.0.0.1", port=8020, reload=True)
