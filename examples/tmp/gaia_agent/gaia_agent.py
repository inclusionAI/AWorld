import os
import traceback
from typing import Dict, Any, List

from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.models.llm import acall_llm_model
from aworld_cli.core import agent
from mcp_config import mcp_config


class SimpleAgent(BaseAgent[Observation, List[ActionModel]]):
    """最简单的可以执行大模型调用的Agent实现"""

    def __init__(self, name: str, conf: AgentConfig = None, desc: str = None,
                 system_prompt: str = None, tool_names: List[str] = None, **kwargs):
        super().__init__(name=name, conf=conf, desc=desc, **kwargs)
        self.system_prompt = system_prompt or "You are a helpful AI assistant."
        self.model_name = conf.llm_config.llm_model_name if conf and conf.llm_config else "gpt-3.5-turbo"

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """执行大模型调用的核心逻辑"""
        try:
            # 初始化工具（参考 llm_agent.py 的 async_desc_transform）
            try:
                await self.async_desc_transform(context=message.context)
            except Exception as e:
                logger.warning(f"{self.name()} get tools desc fail, no tool to use. error: {traceback.format_exc()}")
                self.tools = []

            # 构建消息
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": observation.content}
            ]

            logger.info(f"SimpleAgent {self.name()} 开始调用LLM...")

            # 调用LLM
            tools = self.tools if self.tools else None

            logger.info(f"工具列表tools: {tools}")
            response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=0.7,
                tools=tools
            )

            # 解析响应
            content = response.content or "无有效响应内容"

            logger.info(f"SimpleAgent {self.name()} LLM调用完成")

            # 返回ActionModel列表
            return [ActionModel(
                agent_name=self.name(),
                policy_info=content
            )]

        except Exception as e:
            logger.error(f"SimpleAgent {self.name()} LLM调用失败: {str(e)}")
            return [ActionModel(
                agent_name=self.name(),
                policy_info=f"调用失败: {str(e)}"
            )]


@agent(
    name="SimpleAgent",
    desc="A minimal agent that can perform basic LLM calls"
)
def build_simple_swarm():
    # 创建Agent配置
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        )
    )

    # 从mcp_config中提取所有服务器名称
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # 创建SimpleAgent实例
    simple_agent = SimpleAgent(
        name="simple_agent",
        desc="一个可以进行基本LLM调用和工具调用的简单AI Agent",
        conf=agent_config,
        system_prompt="你是一个有用的AI助手。请根据用户的问题提供准确、有帮助的回答。",
        mcp_servers=mcp_servers,
        mcp_config=mcp_config
    )

    # 返回包含该Agent的Swarm
    return Swarm(simple_agent)
