# coding: utf-8
# Copyright (c) 2025 inclusionAI.


def get_agent_desc():
    from aworld.agents import agent_desc
    return agent_desc


def get_agent_desc_by_name(name: str):
    return get_agent_desc().get(name, None)


def is_agent_by_name(name: str) -> bool:
    from aworld.core.agent.base import AgentFactory
    return name in AgentFactory
