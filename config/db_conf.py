# ============================================================
# 【这个文件干什么】
#   集中放"数据库"相关的配置，并提供"拿一个数据库会话"的入口。
#
# 【注意】
#   ① 连接串（含密码）从 .env 读，不写死在代码里。
#   ② 这里是"独立库" —— 库名叫 news_agent，和项目1 的 news_app 完全分开，
#      两个项目的数据互不影响。
#   ③ 引擎用的是"懒加载"：第一次真正要用数据库时才创建，
#      这样 import 本文件不会因为没配 .env 就崩掉。
#      （这个套路你在项目1 的 utils/vectorstore.py 里见过 —— 那里的 _client 也是懒加载）
# ============================================================

import os

# 【导包说明】
#   sqlalchemy.ext.asyncio —— 【三方库】SQLAlchemy 提供的"异步"版本
#     create_async_engine  ：创建异步引擎（真正和数据库打交道的东西）
#     AsyncSession         ：异步会话类
#     async_sessionmaker   ：会话工厂，用它来"开一个会话"
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from dotenv import load_dotenv

load_dotenv()

# 形如：mysql+aiomysql://root:密码@localhost:3306/news_agent?charset=utf8mb4
DB_URL = os.getenv("DB_URL", "")

# 把最后的 "/news_agent?charset=utf8mb4" 去掉，得到"只连 MySQL 服务器"的地址。
# 建库脚本要用它 —— 因为那一刻 news_agent 这个库还不存在，连不上去。
# rsplit("/", 1) 的意思是"从右边按 / 切一刀，最多切一次"，取切完的前半部分。
SERVER_URL = ""
if DB_URL:
    SERVER_URL = DB_URL.rsplit("/", 1)[0]

# 库名：取连接串最后一段，再把 ? 后面的参数去掉
DB_NAME = ""
if DB_URL:
    DB_NAME = DB_URL.rsplit("/", 1)[-1].split("?")[0]

# ------------------------------------------------------------
# 懒加载用的两个"全局变量"
# 一开始都是 None，第一次用到才真正创建，之后就复用同一个。
# ------------------------------------------------------------
_engine = None
_session_factory = None


def get_engine():
    """拿到异步引擎（第一次调用时才真正创建，之后复用）"""
    global _engine

    if _engine is None:
        if not DB_URL:
            raise RuntimeError("未配置 DB_URL，请复制 .env.example 为 .env 并填写")
        _engine = create_async_engine(
            DB_URL,
            echo=False,          # 不打印 SQL 日志（想调试可以临时改成 True）
            pool_pre_ping=True,  # 拿连接前先 ping 一下，避免用到已被数据库断开的死连接
        )

    return _engine


def get_session_factory():
    """拿到会话工厂（第一次调用时才创建）"""
    global _session_factory

    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )

    return _session_factory


async def get_db():
    """
    FastAPI 的"依赖注入"用的函数：给每个请求发一个会话，请求结束自动关掉。

    yield 的用法和项目1 的 config/db_conf.py 完全一样：
      yield 之前 = 请求开始前的准备
      yield 之后 = 请求结束后的收尾（哪怕是出错结束，finally 也会执行）
    """
    factory = get_session_factory()

    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
