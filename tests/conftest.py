# ============================================================
# 【这个文件干什么】
#   测试的公共准备工作。pytest 会自动加载这个文件，里面的 fixture
#   对所有测试文件都生效。
#
# 【文件名为什么必须叫 conftest.py】
#   这是 pytest 的约定：它会在测试目录里找这个名字的文件并自动执行，
#   不用你去 import。（conftest = configuration test 的缩写）
# ============================================================

import os
import sys

# 让测试文件能 import 到项目模块。
# 放在 conftest.py 里，等于对 tests/ 下所有测试文件都生效，
# 省得每个测试文件都抄一遍 sys.path 处理。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.db_conf import DB_URL


@pytest.fixture
async def db():
    """
    给测试用的数据库会话。

    【fixture 是什么（这个概念第一次见，解释一下）】
      它就是"测试用的公共准备工作"。在函数上面写 @pytest.fixture 做标记，
      然后测试函数只要把 fixture 的名字写成参数，pytest 就会自动准备好传进来：

          async def test_xxx(db):     ← 这里的 db，就是下面这个函数产出的东西
              ...

      好处：建立连接、关闭连接这些重复代码只写一遍，所有测试共用。

    【为什么用 yield 而不是 return】
      yield 之前的代码是"准备阶段"，yield 之后的代码是"收尾阶段" —— 
      pytest 会先跑到 yield 把 session 交给测试用，测试跑完再回来执行收尾。
      这样"用完自动关连接"就不用每个测试自己写了。
    """
    engine = create_async_engine(DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()
