import os
import importlib
import sys
import traceback
import logging
from typing import List, Dict
from .. import AgentModel

logger = logging.getLogger(__name__)

_agent_cache: Dict[str, AgentModel] = {}


def list_agents() -> List[AgentModel]:
    """
    List all cached agents

    Returns:
        List[AgentModel]: The list of agent models
    """
    if len(_agent_cache) == 0:
        for m in _list_agents():
            _agent_cache[m.agent_id] = m
    return _agent_cache


def get_agent_model(agent_id) -> AgentModel:
    """
    Get the agent model by agent name

    Args:
        agent_name: The name of the agent

    Returns:
        AgentModel: The agent model
    """
    if len(_agent_cache) == 0:
        list_agents()
    if agent_id not in _agent_cache:
        raise Exception(f"Agent {agent_id} not found")
    return _agent_cache[agent_id]


def _list_agents() -> List[AgentModel]:
    agents_dir = os.path.join(os.getcwd(), "agent_deploy")

    if not os.path.exists(agents_dir):
        logger.warning(f"Agents directory {agents_dir} does not exist")
        return []

    agents = []
    for agent_id in os.listdir(agents_dir):
        try:
            agent_path = os.path.join(agents_dir, agent_id)
            if os.path.isdir(agent_path):
                agent_file = os.path.join(agent_path, "agent.py")
                if os.path.exists(agent_file):
                    try:
                        agent_instance = _get_agent_instance(agent_id)
                        if hasattr(agent_instance, "agent_name"):
                            agent_name = agent_instance.agent_name()
                        else:
                            agent_name = agent_id
                        if hasattr(agent_instance, "agent_description"):
                            agent_description = agent_instance.agent_description()
                        else:
                            agent_description = ""
                        agent_model = AgentModel(
                            agent_id=agent_id,
                            agent_name=agent_name,
                            agent_description=agent_description,
                            agent_path=agent_path,
                            agent_instance=agent_instance,
                        )

                        agents.append(agent_model)
                        logger.info(
                            f"Loaded agent {agent_id} successfully, path {agent_path}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error loading agent {agent_id}: {traceback.format_exc()}"
                        )
                        continue
                else:
                    logger.warning(f"Agent {agent_id} does not have agent.py file")
        except Exception as e:
            logger.error(
                f"Error loading agent {agent_id}, path {agent_path} : {traceback.format_exc()}"
            )
            continue

    return agents


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
