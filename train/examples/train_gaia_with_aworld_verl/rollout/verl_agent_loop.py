import uuid
from typing import Union, Any

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ConfigDict
from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context
from aworld.dataset.trajectory_strategy import MemoryTrajectoryStrategy
from aworld.logs.util import logger
from aworld.agents.llm_agent import LlmOutputParser
from aworld.config import BaseConfig, ConfigDict, load_config, TaskConfig
from aworld.core.context.amni import AmniContextConfig, AgentContextConfig, ApplicationContext

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from train.examples.train_gaia_with_aworld_verl.rollout.gaia import build_gaia_context_config


class VerlAgentLoop(AworldAgentLoop):
    async def build_context(self, input: Any) -> Context:
        return await ApplicationContext.from_input(task_input=input,
                                                   context_config=build_gaia_context_config())

    async def build_task_config(self) -> TaskConfig:
        return TaskConfig(
            stream=False,
            exit_on_failure=True,
            trajectory_strategy=MemoryTrajectoryStrategy
        )

    async def build_agents(self) -> Union[Agent, Swarm]:
        conf = AgentConfig(
            llm_config=ConfigDict(
                llm_model_name=await self.get_llm_server_model_name(),
                llm_base_url=await self.get_llm_server_address(),
                llm_api_key="123",
                llm_provider="verl",
                params={
                    'client': self.server_manager,
                    "tokenizer": self.tokenizer,
                    "request_id": uuid.uuid4().hex,
                    "tool_parser": "hermes"
                },
                llm_temperature=1.0,
                llm_sync_enabled=True,
                llm_async_enabled=True,
                llm_stream_call=False,
                max_retries=3,
                max_model_len=128000,
                ext_config={},
                top_k=20,
                timeout=7200
            ),
        )

        logger.info(f"agent config: ", conf)
        mcp_config = {'mcpServers': {'virtualpc-mcp-server': {'type': 'streamable-http', 'url': 'http://mcp.aworldagents.com/vpc/mcp', 'headers': {'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY', 'MCP_SERVERS': 'ms-playwright', 'IMAGE_ENV': '{"E2B_API_KEY": "e2b_1a9ada478b1c4a7d53837b9595b8e44e45a6b37a"}', 'IMAGE_VERSION': 'gaia-20251117155359'}, 'timeout': 600, 'sse_read_timeout': 600, 'client_session_timeout_seconds': 600}}}
        return Agent(
            conf=conf,
            name="gaia_super_agent",
            desc="gaia_super_agent",
            system_prompt='''你是一个买票助手和旅行规划达人，接下来你需要完成为用户买机票、旅行规划相关的任务。

今天的日期是 {{current_date}}，如果遇到下周、本周末之类的问题，根据此进行时间推演。

可使用的工具和网址：
1. 你可以使用playwright工具进行浏览器的点击、输入文本框等操作
2. 访问携程网站来完成用户任务并输出答案，网址为：`https://www.ctrip.com`。搜索机票的时候会有浮层`出行提醒`，需要点`我知道了`消除浮层后进行下一步操作

操作要点：
1. 若遇到页面暂时未渲染完毕的情况，等待一会并再次获取页面详情
2. 严格遵守用户的问题中设定的限制条件，包括：时间、地点、直飞或中转、航司名称、是否有行李额度等
3. 一般来说，在携程网站上要先选去程航班，才可以选回程航班，要按这个顺序点击，才能查看出发、回程的航班价格
4. 如果遇到用户设定的出发时间、地点不确定的情况，不要反问用户，给用户提供几种可能的选项即可。但如果遇到`最便宜`等描述，则需要遍历用户要求下的所有可能情况
5. 如果出发地到目的地之间没有直飞航班，且用户没有说只要直飞航班，可以给用户推荐中转航班的详细信息，而不是只回答没有直达航班
6. 如果遇到搜某个时间段内的低价机票，同程提供了`低价日历`的功能，在机票界面可以查看

回答格式：
1. 在给出用户答案的时候，必须在回答中写清楚出发、回程的航班号和时间
2. 最终会展示给用户的回答请用`<answer>xxx</answer>`来输出，思考过程请放在`<think>xxx</think>`中

介绍机票术语：
用户在提问的时候可能会包含机票的一些术语，以下是为你提供的术语介绍。
1. 甩尾：甩尾机票是指旅客购买包含目的地的联程机票，但在中转站下机，放弃后续航段的机票。例如，购买A-B-C的联程机票，实际只乘坐A-B航段，价格可能比A-B直飞更便宜，旅客在B地结束行程，甩掉了B-C这一尾段航班，这就是甩尾机票。这种方式利用了联程机票价格有时低于直飞航班价格的特点，以达到节省旅行成本的目的。
2. 回旋镖：回旋镖机票是一种新兴的机票购买及旅行方式。它指出发地和到达地距离较近，通常为同省或邻近城市，但旅客通过选择远程中转城市，以“绕一大圈”的形式在中转地游玩，再返回出发点附近，从而低成本实现一次性价比极高的远程旅行体验。例如，从杭州去宁波，距离较近，但可以选择绕道烟台中转45小时，在烟台游玩后再前往宁波。或者从福州去厦门，选择在南京停留24小时，在南京游玩后再飞厦门。这种方式不同于传统意义上的中转停留，它更强调利用中转城市进行深度游玩，增加旅行的体验和乐趣。
3. 开口程：是指出发地和回程地不同的机票行程，例如从上海出发去新加坡，然后从新加坡回北京，这种行程就属于开口程。
4. 双截棍：是一种利用超长中转时间，用一张机票玩转两座城市的机票。例如从武汉飞揭阳，在广州白云机场中转7个小时，旅客可以在中转期间游玩广州。
5. 加段：在原本的行程基础上，增加一个或多个航段，以达到降低整体票价目的的机票。例如，购买温哥华-上海-昆明的机票，比直接购买温哥华-上海的机票更便宜，这里上海-昆明就是增加的航段。
''',
            tool_names=[],
            agent_names=[],
            wait_tool_result=False,
            feedback_tool_result=True,
            black_tool_actions={},
            skill_configs=None,
            event_handler_name=None,
            tools_aggregate_func=None,
            mcp_config=mcp_config,
            mcp_servers=list(server_name for server_name in mcp_config.get("mcpServers", {}).keys()),
            model_output_parser=LlmOutputParser(),

        )