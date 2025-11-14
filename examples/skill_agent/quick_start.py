import asyncio
import os
import traceback
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from aworld.config import TaskConfig
from aworld.core.context.amni import TaskInput, ApplicationContext
from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel, init_middlewares
from aworld.core.task import Task
from aworld.runner import Runners
from examples.skill_agent.agents.swarm import build_swarm


async def build_task(task_content: str, context_config, session_id: str = None, task_id: str = None) -> Task:
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    if not task_id:
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 1. build task input
    task_input = TaskInput(
        user_id=f"user",
        session_id=session_id,
        task_id=task_id,
        task_content=task_content,
        origin_user_input=task_content
    )

    # 2. build swarm
    swarm = build_swarm()


    # 3. build context
    async def build_context(_task_input: TaskInput) -> ApplicationContext:
        """Important Config"""
        return await ApplicationContext.from_input(_task_input, context_config=context_config)

    context = await build_context(task_input)
    await context.init_swarm_state(swarm)

    # 3. build task with context
    return Task(
        id=context.task_id,
        user_id=context.user_id,
        session_id=context.session_id,
        input=context.task_input,
        endless_threshold=5,
        swarm=swarm,
        context=context,
        conf=TaskConfig(
            stream=False,
            exit_on_failure=True
        ),
        timeout=60 * 60
    )

async def run(user_input: str):
    # 1. init middlewares
    load_dotenv()
    init_middlewares()

    # 2. build context config
    context_config = AmniConfigFactory.create(
        AmniConfigLevel.NAVIGATOR,
        debug_mode=True
    )

    # 3. build task
    task = await build_task(user_input, context_config)

    # 4. run task
    try:
        result = await Runners.run_task(task=task)
        print(result[task.id].answer)
        if not os.path.exists("results"):
            os.makedirs("results")
        with open(f"results/{task.id}.txt", "w") as f:
            f.write(result[task.id].answer)
    except Exception as err:
        print(f"err is {err}, trace is {traceback.format_exc()}")


if __name__ == '__main__':
    #
    # asyncio.run(run(user_input="分析一下https://mp.weixin.qq.com/s/CBJdjjgQXerm90UAgMEZSg 这个微信文章`browser_snapshot`获取不了完整内容 结合`browser_evaluate`获取完整内容 然后 使用MEMC原则给我总结一下 我是一个程序员 工作8年 并且结合第一性原理帮我解释"))
    # 分析微博
    # asyncio.run(run(user_input="分析一下这个大V 查看近一个月的微博 https://m.weibo.cn/profile/1497035431"))
    # asyncio.run(run(user_input="将/Users/wuhulala/PycharmProjects/AWorld/examples/skill_agent/results/xx.md 生成小红书的文案  并且生成多个长图 这个图你要支持中文"))
    # asyncio.run(run(user_input="纳瓦尔2025深度访谈 讲的观点有哪些 使用金字塔原理的MEMC原则保证完整性 运用第一性原理去解读"))
    # asyncio.run(run(user_input="截至2024年12月31日，2024年上海黄金交易所Au(T+D)合约的“最高价”与“最低价”之差约为多少元/克？"))
    # asyncio.run(run(user_input="https://huggingface.co/datasets/xbench/DeepSearch 帮我把这个数据下载然后解密给我么，请参考xbench_evals github repo中的解密代码获取纯文本数据。"))
    # asyncio.run(run(user_input="帮我看看这周日从杭州到郑州的机票价格和时间(必须使用code mode 模式， 你应该先使用访问网站，然后获取到所有的元素之后再用codemode进行填充)"))
    # asyncio.run(run(user_input="read https://arxiv.org/pdf/2510.23595v1 and tell me the abstract and conclusion of this paper"))
    # asyncio.run(run(user_input="read https://arxiv.org/pdf/2511.08892 给我读一下这个论文 告诉我结论 和 他们的benchmark"))
    asyncio.run(run(user_input="read https://arxiv.org/pdf/2511.08892 给我读一下这个论文 重点关注Benchmark这一章"))
    # asyncio.run(run(user_input="Help me find the latest week stock price of BABA. And Analysis the trend of news."))
