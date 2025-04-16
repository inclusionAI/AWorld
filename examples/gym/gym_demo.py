# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import time

from aworld.config.common import Tools, Agents
from aworld.core.client import Client
from aworld.core.agent.base import Agent, AgentFactory
from aworld.agents.gym.agent import GymDemoAgent as GymAgent

from aworld.config.conf import AgentConfig
from aworld.logs.util import logger
from aworld.core.envs.tool import AsyncTool, ToolFactory
from aworld.core.task import Task
from aworld.virtual_environments.gym_tool.async_openai_gym import OpenAIGym as AOpenAIGym


async def async_run_gym_game(agent: Agent, tool: AsyncTool):
    gym_tool = tool
    logger.info('observation space: {}'.format(gym_tool.env.observation_space))
    logger.info('action space: {}'.format(gym_tool.env.action_space))
    logger.info('rende mode: {}'.format(gym_tool.env.render_mode))

    try:
        # init env tool state
        state, info = await gym_tool.reset()
        while True:
            # render
            await gym_tool.render()
            # agent policy action, also can use llm, only an example
            action = await agent.async_policy(state, info)
            logger.info(f"action: {action}")
            # env tool state and reward info based on action
            state, reward, done, truncated, info = await gym_tool.step(action=action)
            logger.info(f'state: {state}; reward: {reward}')

            if done:
                logger.info("game done!")
                break
            time.sleep(1)
    finally:
        await gym_tool.close()


def main():
    # use default config
    gym_tool = ToolFactory(Tools.GYM.value)
    agent = AgentFactory(Agents.GYM.value, conf=AgentConfig())

    # can run tasks like this:
    res = Task(agent=agent, tools=[gym_tool]).run()
    return res


if __name__ == "__main__":
    # We use it as a showcase to demonstrate the framework's scalability.
    main()

    # Can run the task use utility method, as follows:
    # async run gym
    # agym_tool = ToolFactory("async_"+Tools.GYM.value)
    # agent = GymAgent(AgentConfig())
    # asyncio.run(async_run_gym_game(agent=agent, tool=agym_tool))
