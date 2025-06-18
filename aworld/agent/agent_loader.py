import os
import importlib
import sys
import traceback
import logging
from typing import List
from .model import AgentModel

logger = logging.getLogger(__name__)

_agent_cache = {}


def list_agents() -> List[AgentModel]:
    if len(_agent_cache) == 0:
        [_agent_cache.add(m.agent_name, m) for m in _list_agents()]
    return _agent_cache

def get_agent_model(agent_name):
    return _agent_cache[agent_name]

def _list_agents() -> List[AgentModel]:
    agents_dir = os.path.join(os.getcwd(), "agent_deploy")

    if not os.path.exists(agents_dir):
        logger.warning(f"Agents directory {agents_dir} does not exist")
        return []

    try:
        agents = []
        for item in os.listdir(agents_dir):
            item_path = os.path.join(agents_dir, item)
            if os.path.isdir(item_path):
                agent_file = os.path.join(item_path, "agent.py")
                if os.path.exists(agent_file):
                    try:
                        AgentModel(agent_id=item, agent_in)
                        agents.append(_get_agent_instance(item))
                    except Exception as e:
                        logger.error(
                            f"Error loading agent {item}: {traceback.format_exc()}"
                        )
                        continue
        return agents
    except OSError as e:
        logger.error(f"Error listing agents: {traceback.format_exc()}")
        return []


def _get_agent_instance(agent_name):
    try:
        agent_package_path = os.path.join(
            os.getcwd(),
            "agent_deploy",
            agent_name,
        )
        agent_module_file = os.path.join(agent_package_path, "agent.py")

        spec = importlib.util.spec_from_file_location(agent_name, agent_module_file)

        if spec is None or spec.loader is None:
            msg = f"Could not load spec for agent {agent_name} from {agent_module_file}"
            logger.error(msg)
            raise Exception(msg)

        agent_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_module)
    except Exception as e:
        msg = f"Error loading agent {agent_name}, cwd:{os.getcwd()}, sys.path:{sys.path}: {traceback.format_exc()}"
        logger.error(msg)
        raise Exception(msg)

    agent = agent_module.AWorldAgent()
    return agent
