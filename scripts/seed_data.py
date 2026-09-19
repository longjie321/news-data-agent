# ============================================================
# 【这个文件干什么】
#   往 news 表里灌"本地生成的测试数据"：8 个分类，每类 50 条，一共 400 条。
#
# 【什么时候用它】
#   ① 天聚数行的接口还没申请下来时，先用它把项目跑起来
#   ② 演示时数据量稳定可控（随机种子写死了，每次生成的数据一模一样）
#
# 【它和 fetch_news.py 的区别】
#   seed_data.py   本地造数据，不联网，随时能跑
#   fetch_news.py  从天聚数行抓真实新闻，要联网 + 已申请接口
#   两个可以混着用：先 seed 一批，再 fetch 一批。
#
# 【⚠️ 诚实说明】
#   这个脚本生成的是"看起来像新闻的测试数据"，不是真新闻。
#   面试被问到就如实说：数据是自己造的测试数据，重点是验证 Agent 的工具调用链路。
#   想换真实数据，跑 fetch_news.py。
#
# 【用法】
#   python scripts/seed_data.py            灌数据（表里已有数据就跳过）
#   python scripts/seed_data.py --reset    先清空 news 表再灌
# ============================================================

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.db_conf import DB_URL
from config.news_conf import CATEGORIES
from models.news import News

# 随机种子：固定一个数，保证每次都生成同样的数据（出问题时好复现）
RANDOM_SEED = 20260918

# 每个分类灌多少条
PER_CATEGORY = 50

# ⚠️ 分类清单从 config/news_conf.py 取（那里是整个项目唯一的定义处）。
#    以前这个脚本里也写了一份，加分类的时候很容易和工具描述对不上。

# 分类 → 标题模板
TITLE_TEMPLATES = {
    "体育": [
        "全国田径锦标赛男子百米决赛爆冷夺冠",
        "女排世界联赛中国队取得三连胜",
        "中超联赛第十八轮焦点战握手言和",
        "CBA 新赛季揭幕战主队大胜",
        "马拉松赛事报名人数创下新高",
        "乒乓球世界杯国乒包揽四强",
        "青少年足球训练营在多地同时开营",
        "羽毛球公开赛混双组合晋级决赛",
        "篮球名宿谈青训体系改革方向",
        "城市业余联赛参赛队伍突破千支",
        "冬奥冠军回母校分享训练经历",
        "省运会筹备工作进入冲刺阶段",
    ],
    "科技": [
        "国产大模型开源新版本参数规模翻倍",
        "新一代国产芯片完成流片验证",
        "自动驾驶测试里程突破新纪录",
        "量子计算原型机取得阶段性进展",
        "开源社区发布年度生态报告",
        "低空经济试点城市范围扩大",
        "国产数据库在金融行业完成替换",
        "AI 辅助诊断系统通过临床评估",
        "新型储能电池循环寿命明显提升",
        "卫星互联网完成首次组网测试",
        "工业机器人产量连续三季度增长",
        "脑机接口临床试验启动招募",
    ],
    "财经": [
        "央行公布最新社会融资规模数据",
        "制造业采购经理指数重回扩张区间",
        "上半年外贸进出口总值实现增长",
        "多家银行下调存款挂牌利率",
        "新能源车出口量创下单月新高",
        "一线城市二手房成交量回暖",
        "民营经济发展支持政策细则落地",
        "跨境电商综合试验区新增城市",
        "消费市场暑期档表现好于预期",
        "上市公司半年报披露接近尾声",
        "人民币汇率双向波动保持稳定",
        "绿色债券发行规模继续扩大",
    ],
    "娱乐": [
        "暑期档电影票房突破新关口",
        "国产动画电影海外发行版权售出",
        "音乐节巡演宣布加开场次",
        "电视剧品质盛典公布入围名单",
        "话剧年度巡演启动全国站次",
        "综艺节目改版后收视回升",
        "青年演员凭现实题材作品获奖",
        "纪录片团队历时三年完成拍摄",
        "经典影片修复版重映引发怀旧潮",
        "网络文学改编剧集正式开机",
        "交响乐团开启海外巡演",
        "博物馆文创联名产品上线售罄",
    ],
    "社会": [
        "多地推进老旧小区加装电梯",
        "城市地铁新线路开通试运营",
        "社区食堂覆盖范围进一步扩大",
        "暑期托管服务惠及双职工家庭",
        "快递进村工程覆盖大部分行政村",
        "多地调整最低工资标准",
        "公共场所母婴室建设标准发布",
        "农村饮水安全工程完成验收",
        "城市书房免费开放时间延长",
        "适老化改造补贴申请通道开启",
        "垃圾分类示范片区经验开始推广",
        "山区学校营养餐标准提高",
    ],
    "国内": [
        "多部门联合发布促消费新举措",
        "新一轮基础设施投资计划公布",
        "全国夏粮收购工作顺利结束",
        "医保药品目录调整方案公开征求意见",
        "职业教育产教融合试点范围扩大",
        "城市更新行动进入全面实施阶段",
        "全国统一大市场建设持续推进",
        "中小企业数字化转型获专项支持",
        "秋冬季大气污染治理方案对外发布",
        "粮食主产区迎来丰收季",
        "水利重点工程完成年度建设节点",
        "县域商业体系建设加快落地",
    ],
    "国际": [
        "多国代表就气候议题举行磋商",
        "全球大宗商品价格出现波动",
        "区域性自贸协定完成新一轮谈判",
        "国际航运运价指数连续回落",
        "多边开发银行上调区域增长预期",
        "跨国药企公布新药三期临床数据",
        "主要经济体公布最新通胀数据",
        "国际航展签订多项采购协议",
        "全球半导体供应链出现结构调整",
        "多国推进可再生能源装机目标",
        "国际组织发布年度发展报告",
        "跨境旅游市场持续恢复",
    ],
    "头条": [
        "多项便民新政本月起正式施行",
        "全国性行业标准发布实施",
        "重大科技基础设施通过验收",
        "区域协调发展战略取得新进展",
        "重点民生工程提前完成年度目标",
        "高质量发展调研行活动启动",
        "公共服务均等化水平持续提升",
        "规模性设备更新政策效果显现",
        "重点产业链供应链韧性增强",
        "城乡融合发展试点成效初显",
        "全国性职业技能大赛落下帷幕",
        "绿色低碳转型取得明显成效",
    ],
}

# 标题后缀：同一件事被不同媒体/不同时间报道时，标题上常带点小尾巴。
# 加上这个是为了让 400 条数据里"标题完全一样"的情况变少，演示时看着更真实。
# 故意放了两个空字符串 —— 让一部分标题保持不带后缀的自然样子。
TITLE_SUFFIXES = [
    "",
    "",
    "（附现场图）",
    "（视频报道）",
    "（详情）",
    "（更新）",
    "（组图）",
]

# 正文结尾的几种说法（随机拼，让正文不至于完全一样）
CONTENT_TAILS = [
    "相关部门表示，下一步将继续完善配套措施，确保各项部署落实到位。",
    "业内人士分析认为，这一变化将对行业格局产生持续影响。",
    "据悉，相关工作已在多个地区同步启动，后续进展将持续向社会公布。",
    "有关负责人介绍，此轮调整充分考虑了各方意见，力求稳妥推进。",
    "专家指出，从长期看这一趋势符合预期，但仍需关注执行层面的细节。",
]

# 作者/来源
SOURCES = ["新华社", "人民日报", "央视新闻", "中国新闻网", "经济日报", "科技日报"]


def make_one_news(category: str, rng: random.Random, now: datetime) -> News:
    """
    造一条新闻。

    rng 是 random.Random 实例，用它取随机数（固定种子的那个）。
    """
    templates = TITLE_TEMPLATES[category]

    # 先挑一个标题模板，再随机加个后缀（也可能不带后缀）
    # 这样同一个模板生成出来的标题不会长得一模一样
    title = rng.choice(templates) + rng.choice(TITLE_SUFFIXES)

    # 正文：标题 + 两句套话，拼成 100 字左右
    content = title + "。"
    content = content + "记者了解到，相关情况已于近日公布，各方反应总体积极。"
    content = content + rng.choice(CONTENT_TAILS)
    content = content + "更多细节仍在跟进中。"

    # 浏览量分三档，让"排行榜"工具有明显的区分度：
    #   60% 的新闻是几千，30% 是几千到八千，10% 是八千以上
    level = rng.choices(population=[1, 2, 3], weights=[60, 30, 10])[0]

    if level == 1:
        views = rng.randint(200, 3000)
    elif level == 2:
        views = rng.randint(3000, 8000)
    else:
        views = rng.randint(8000, 20000)

    # 发布时间：过去 30 天内随机某一天
    publish_time = now - timedelta(
        days=rng.randint(0, 29),
        hours=rng.randint(0, 23),
        minutes=rng.randint(0, 59),
    )

    return News(
        title=title,
        content=content,
        category=category,
        author=rng.choice(SOURCES),
        source=rng.choice(SOURCES),
        views=views,
        publish_time=publish_time,
    )


async def main():
    parser = argparse.ArgumentParser(description="往 news 表灌测试数据")
    parser.add_argument("--reset", action="store_true", help="先清空 news 表再灌")
    args = parser.parse_args()

    if not DB_URL:
        print("[失败] 没有读到 DB_URL —— 请把 .env.example 复制成 .env 并填写")
        return

    engine = create_async_engine(DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # 先看看表里本来有多少条
        count_stmt = select(func.count()).select_from(News)
        existing = await session.scalar(count_stmt)

        if existing is None:
            existing = 0

        if args.reset and existing > 0:
            await session.execute(delete(News))
            await session.commit()
            print(f"已清空 news 表（原本有 {existing} 条）")
            existing = 0

        if existing > 0:
            print(f"表里已经有 {existing} 条数据了，跳过灌数据。")
            print("想重新灌：加 --reset 参数，例如 python scripts/seed_data.py --reset")
            await engine.dispose()
            return

        rng = random.Random(RANDOM_SEED)
        now = datetime.now()

        rows = []
        for category in CATEGORIES:
            for _ in range(PER_CATEGORY):
                rows.append(make_one_news(category, rng, now))

        session.add_all(rows)
        await session.commit()

        total = await session.scalar(count_stmt)

    await engine.dispose()

    print(f"灌数据完成：共 {total} 条（{len(CATEGORIES)} 个分类 × 每类 {PER_CATEGORY} 条）")
    print("")
    print("验证一下，可以在 MySQL 里跑：")
    print("  SELECT category, COUNT(*) FROM news_agent.news GROUP BY category;")


if __name__ == "__main__":
    asyncio.run(main())
