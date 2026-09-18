# ============================================================
# 【这个文件干什么】
#   定义"新闻表"(news) 的模型 —— 整个智能体就只查这一张表。
#   SQLAlchemy 靠这个类知道表叫什么、有哪些字段，才能建表、才能写查询。
#
# 【为什么表要这么设计】
#   4 个工具各自要查什么，表就得有什么，一一对应：
#     按分类统计    → 需要 category 字段
#     按浏览量排行  → 需要 views 字段
#     按时间统计    → 需要 publish_time 字段
#     按关键词搜索  → 需要 title / content 字段
#   所以这是"先想好工具要查什么，再定字段"，不是随便堆字段。
#
# 【表设计上的一个取舍 —— 面试可以讲】
#   这里 category 直接存"体育""科技"这样的分类名字符串，
#   没有另建一张分类表、也没有外键。
#   原因：分类只有固定的 8 个，永远不会新增也不会改名，
#         存字符串能省掉一次 JOIN，查起来更直接（这叫"适度反范式"）。
#   对比：项目1 用的是 news.category_id + 分类表外键，
#         那种写法适合"分类会变、需要统一改名"的场景。两种都没错，看需求。
#
# 【注意】
#   字段类型和长度都别随便改，改了要重新建表（跑 scripts/init_db.py --reset）。
# ============================================================

from datetime import datetime

# 【导包说明】
#   sqlalchemy —— 【三方库】pip 装的（requirements.txt 里有）
#     BigInteger     ：长整型，主键用它
#     DateTime       ：日期时间类型
#     Index          ：用来声明索引
#     Integer        ：整型
#     String         ：变长字符串，可以指定最大长度
#     Text           ：长文本（比 String 长得多，不用指定长度）
#     func           ：调用数据库自带的函数，比如 func.now() 就是 MySQL 的 NOW()
#   sqlalchemy.orm 里三个：
#     DeclarativeBase ：SQLAlchemy 2.0 的"基类"写法（老写法是 declarative_base()）
#     Mapped          ：类型标注，写在变量名后面，表示"这一列是什么类型"
#     mapped_column   ：声明一列，填它的类型和属性
from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    所有模型类的基类。
    SQLAlchemy 喜欢"继承同一个基类"的类，才能把它们收集起来一次性建表。
    """
    pass


class News(Base):
    """新闻表：智能体查询的唯一数据源"""

    __tablename__ = "news"

    # Mapped[int] 里写的是 Python 类型，mapped_column 里写的是数据库类型。
    # 两者要对得上（int ↔ BigInteger / Integer，str ↔ String / Text）。

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键，自增",
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="标题",
    )

    # content 可能为空（有些新闻源不给正文），所以类型写成 str | None
    # "str | None" 是 Python 3.10+ 的写法，等于"字符串或者空"
    content: Mapped[str | None] = mapped_column(
        Text,
        comment="正文（可能为空）",
    )

    category: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="分类：体育/科技/财经/娱乐/社会/国内/国际/头条",
    )

    # default="" 是"插入时如果不给值，就用空字符串"
    # 为什么不用 None：字符串字段给空串比给 None 更好处理（拼接、显示都不会报错）
    author: Mapped[str] = mapped_column(
        String(50),
        default="",
        comment="作者/供稿单位",
    )

    source: Mapped[str] = mapped_column(
        String(50),
        default="",
        comment="来源媒体",
    )

    views: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="浏览量",
    )

    publish_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="发布时间",
    )

    # server_default=func.now() 表示"这条默认值由数据库来填"（MySQL 的 CURRENT_TIMESTAMP）。
    # 和上面的 default="" 区别：
    #   default=        → Python 这边填（插入时 SQLAlchemy 补上）
    #   server_default= → 数据库那边填（建表语句里写进 DEFAULT）
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        comment="入库时间",
    )

    # __table_args__ 里放"不属于任何单一列"的东西，这里就是 4 个索引 + 表注释。
    # 索引为什么是这 4 个 —— 照着"工具会怎么查"来的：
    #   idx_category          按分类筛选
    #   idx_publish_time      按时间范围筛选
    #   idx_views             按浏览量排序取前几名
    #   idx_category_publish  "某个分类 + 某段时间"这种组合查询（最常用）
    # 注意 idx_category_publish 的字段顺序不能反：
    #   联合索引遵循"最左前缀原则"，category 放在左边，单独查 category 时才能用上它。
    __table_args__ = (
        Index("idx_category", "category"),
        Index("idx_publish_time", "publish_time"),
        Index("idx_views", "views"),
        Index("idx_category_publish", "category", "publish_time"),
        {"comment": "新闻表：智能体查询的数据源"},
    )

    def __repr__(self) -> str:
        """
        __repr__ 是 Python 的内置约定方法：print(对象) 或者调试时显示成什么样。
        不写也能跑，写了调试时更好看。title[:20] 是"只取标题前 20 个字"。
        """
        return f"<News id={self.id} category={self.category} title={self.title[:20]}>"
