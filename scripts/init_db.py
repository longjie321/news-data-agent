# ============================================================
# 【这个文件干什么】
#   建数据库 + 建表。项目第一次跑的时候执行一次就行。
#
# 【用法】
#   python scripts/init_db.py            建库建表（已经有了就跳过，不会删数据）
#   python scripts/init_db.py --reset    先删表再重建（会清空数据）
#
# 【注意】
#   ① 只操作 news_agent 这个库，完全不碰项目1 的 news_app。
#   ② 数据不在这里灌。灌数据是另外两个脚本的事：
#        scripts/seed_data.py    本地生成的测试数据（不需要联网）
#        scripts/fetch_news.py   从天聚数行抓真实新闻（需要联网 + 已申请接口）
#   ③ 脚本开头要往 sys.path 里加项目根目录，
#      否则直接跑 scripts/xxx.py 时 Python 找不到 config、models 这些包。
#      （项目1 的 scripts/ingest_news.py 里也有同样一句）
# ============================================================

import argparse
import asyncio
import os
import sys

# 把"项目根目录"加进模块搜索路径
# __file__ 是这个脚本自己的路径，套两层 dirname 就到了项目根
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config.db_conf import DB_NAME, DB_URL, SERVER_URL
from models.news import Base


async def create_database():
    """
    建数据库。

    为什么要用 SERVER_URL 而不是 DB_URL：
      这一步 news_agent 这个库还不存在，用带库名的地址连会直接报错。
      所以要先连到 MySQL 服务器本身（不带库名），再让它建库。
    """
    engine = create_async_engine(SERVER_URL, echo=False)

    async with engine.begin() as conn:
        # text(...) 表示"这是一段原生的 SQL 字符串"
        # CREATE DATABASE IF NOT EXISTS = 已经有这个库就不报错
        await conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )

    await engine.dispose()
    print(f"[1/2] 数据库 `{DB_NAME}` 已就绪")


async def create_tables(reset: bool):
    """
    建表。

    run_sync(...) 的意思是"这里要执行的是同步函数，帮我放进异步环境里跑"。
    SQLAlchemy 的 create_all / drop_all 只有同步版本，所以要这样包一下。
    """
    engine = create_async_engine(DB_URL, echo=False)

    async with engine.begin() as conn:
        if reset:
            await conn.run_sync(Base.metadata.drop_all)
            print("      已删除旧表（因为你加了 --reset）")

        await conn.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("[2/2] 表 news 已建好（含 4 个索引）")


async def main():
    parser = argparse.ArgumentParser(description="初始化 news_agent 的数据库")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="先删表再重建（会清空数据）",
    )
    args = parser.parse_args()

    if not DB_URL:
        print("[失败] 没有读到 DB_URL —— 请把 .env.example 复制成 .env 并填写")
        return

    print(f"目标数据库：{DB_NAME}")

    await create_database()
    await create_tables(reset=args.reset)

    print("")
    print("完成。接下来可以灌数据，二选一：")
    print("  python scripts/seed_data.py      本地生成的测试数据（不用联网）")
    print("  python scripts/fetch_news.py     从天聚数行抓真实新闻（要联网）")


if __name__ == "__main__":
    asyncio.run(main())
