# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Union, Optional, TYPE_CHECKING

from aworld.agents.llm_agent import Agent
from aworld.config import RunConfig
from aworld.config.conf import TaskConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.common import ActionModel, Observation
from aworld.core.context.amni import AmniContextConfig
from aworld.core.context.base import Context
from aworld.core.event.base import Message, TopicType
from aworld.core.task import Task, TaskResponse
from aworld.logs.util import logger
from aworld.output.outputs import Outputs
from aworld.runners.utils import choose_runners, execute_runner
from aworld.utils.common import sync_exec

if TYPE_CHECKING:
    from aworld.agents.swarm_composer_agent import SwarmComposerAgent


async def exec_tool(tool_name: str,
                    action_name: str,
                    params: dict,
                    agent_name: str,
                    context: Context,
                    sub_task: bool = False,
                    outputs: Outputs = None,
                    task_group_id: str = None,
                    run_conf: RunConfig = RunConfig(reuse_process=True)) -> TaskResponse:
    """Utility method for executing a tool in a task-oriented manner.

    Args:
        tool_name: Name of tool, required.
        action_name: Action name of tool, required.
        params: Tool params, required.
        agent_name: Agent name, required, can be empty.
        context: Context in the runtime, required.
        sub_task: Is it a subtask with the main task set to False.
        outputs: The same outputs instance, required in subtask.
        task_group_id: ID of group of task.
        run_conf: Task runtime config.
    """
    actions = [ActionModel(tool_name=tool_name, action_name=action_name, params=params, agent_name=agent_name)]
    task = Task(input=actions,
                context=context,
                is_sub_task=sub_task,
                group_id=task_group_id,
                session_id=context.session_id)
    if outputs:
        task.outputs = outputs
    runners = await choose_runners([task], agent_oriented=False)
    res = await execute_runner(runners, run_conf=run_conf)
    resp: TaskResponse = res.get(task.id)
    return resp


async def exec_agent(question: Any,
                     agent: Agent,
                     context: Context,
                     sub_task: bool = False,
                     outputs: Outputs = None,
                     task_group_id: str = None,
                     task_conf: TaskConfig = None,
                     run_conf: RunConfig = RunConfig(reuse_process=True),
                     **kwargs) -> TaskResponse:
    """Utility method for executing an agent in a task-oriented manner.

    Args:
        question: Problems handled by agents.
        agent: Defined intelligent agents that solve specific problems.
        context: Context in the runtime.
        sub_task: Is it a subtask with the main task set to False.
        outputs: The same outputs instance.
        task_group_id: ID of group of task.
        task_conf: Task config.
        run_conf: Task runtime config.
    """
    # Guard clause: Check if context is None
    if context is None:
        raise ValueError(
            f"Context cannot be None when executing agent '{agent.id()}'. "
            f"Please ensure a valid context is passed to the exec_agent function."
        )

    task_id = uuid.uuid1().hex
    info_dict = context.agent_info.get(agent.id(), {})
    use_new_agent = info_dict.get("use_new_agent")
    if use_new_agent:
        override = {}
        if info_dict.get("agent_name"):
            # unique agent_name
            override['name'] = info_dict.get("agent_name")
        if info_dict.get("agent_id"):
            # not new_id or new_id is True, will use the difference agent id
            override["agent_id"] = info_dict.get("agent_id")

        agent = Agent.from_dict(await Agent.to_dict(agent, override=override))

    context_info = context.context_info.get(agent.id(), {})
    session_id = context_info.get("session_id") or context.session_id
    if context_info.get("use_new_context"):
        context = context.deep_copy()

    task = Task(id=task_id,
                input=question,
                agent=agent,
                context=context,
                is_sub_task=sub_task,
                group_id=task_group_id,
                session_id=session_id,
                conf=task_conf)
    if isinstance(question, Observation):
        task.observation = question
        task.input = question.content
    if outputs:
        task.outputs = outputs
    if sub_task and kwargs.get("tool_call_id"):
        context.add_task_node(task.id, context.task_id, caller_agent_info=context.agent_info, tool_call_id=kwargs.get("tool_call_id"))
    runners = await choose_runners([task])
    res = await execute_runner(runners, run_conf=run_conf)
    resp: TaskResponse = res.get(task.id)
    return resp


async def exec_agents(questions: List[Any],
                      agents: List[Agent],
                      context: Context,
                      sub_task: bool = False,
                      outputs: Outputs = None,
                      task_group_id: str = None,
                      task_conf: TaskConfig = None,
                      run_conf: RunConfig = RunConfig(reuse_process=True)) -> List[ActionModel]:
    """Execute the agent list with the questions, using asyncio.

    Args:
        questions: Problems handled by agents.
        agents: Defined intelligent agents that solve specific problem.
        context: Context in the runtime.
        sub_task: Is it a subtask with the main task set to False.
        outputs: The same outputs instance.
        task_group_id: ID of group of task.
        task_conf: Task config.
        run_conf: Task runtime config.
    """
    tasks = []
    if agents:
        for idx, agent in enumerate(agents):
            tasks.append(asyncio.create_task(
                exec_agent(questions[idx], agent, context, sub_task=sub_task, outputs=outputs,
                           task_group_id=task_group_id, task_conf=task_conf, run_conf=run_conf)))

    results = await asyncio.gather(*tasks)
    res = []
    for idx, result in enumerate(results):
        if result.success:
            con = result.answer
        else:
            con = result.msg
        res.append(ActionModel(agent_name=agents[idx].id(), policy_info=con))
    return res


async def exec_process_agents(question: Any,
                              agents: List[Agent],
                              context: Context,
                              sub_task: bool = False,
                              task_group_id: str = None,
                              run_conf: RunConfig = RunConfig(reuse_process=True)):
    """Execute the agent list with the same question, using new process.

    NOTE: Mixing coroutines and processes may lead to unknown issues.

    Args:
        question: Problems handled by agents.
        agents: Defined intelligent agents that solve specific problem.
        context: Context in the runtime.
        sub_task: Is it a subtask with the main task set to False.
        task_group_id: ID of group of task.
        run_conf: Task runtime config.
    """
    tasks = []
    agent_map = {}
    if agents:
        for agent in agents:
            task = Task(input=question, agent=agent, context=context, is_sub_task=sub_task, group_id=task_group_id)
            agent_map[task.id] = agent.id()
            tasks.append(task)

    if not tasks:
        raise RuntimeError("no task need to run.")

    runners = await choose_runners(tasks)
    results = await execute_runner(runners, run_conf=run_conf)

    res = []
    for key, result in results.items():
        res.append(ActionModel(agent_name=agent_map[key], policy_info=result))
    return res


async def exec_tasks(tasks: List[Task], run_conf: RunConfig = RunConfig()) -> Dict[str, TaskResponse]:
    final_tasks = []
    # task list sequence-dependent execution
    if run_conf and run_conf.sequence_dependent:
        return await serial_exec_tasks(tasks=tasks, run_conf=run_conf)

    for task in tasks:
        if not task.group_id:
            task.group_id = uuid.uuid4().hex
        final_tasks.append(task)
    runners = await choose_runners(final_tasks, run_conf=run_conf)
    return await execute_runner(runners, run_conf)


async def serial_exec_tasks(tasks: List[Task], run_conf: RunConfig = RunConfig()) -> Dict[str, TaskResponse]:
    res = {}
    task_input = tasks[0].input
    for task in tasks:
        task.input = task_input
        runners = await choose_runners([task])
        res = await execute_runner(runners, run_conf)
        result: TaskResponse = res.get(task.id)
        if result.success:
            task_input = result.answer
        else:
            task_input = result.msg
    return res


def create_default_swarm_composer_agent(
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None) -> 'SwarmComposerAgent':
    """Create default SwarmComposerAgent instance with environment-based configuration."""
    from aworld.agents.swarm_composer_agent import SwarmComposerAgent
    from aworld.config import AgentConfig, ModelConfig
    import os

    model_name = os.getenv("LLM_MODEL_NAME", "gpt-4")
    provider = os.getenv("LLM_PROVIDER", "openai")
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")

    if not api_key or not model_name:
        raise ValueError(
            "LLM_API_KEY and LLM_MODEL_NAME environment variables must be set to use default SwarmComposerAgent. "
            "Alternatively, pass a custom swarm_composer_agent instance."
        )

    return SwarmComposerAgent(
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=model_name,
                llm_provider=provider,
                llm_api_key=api_key,
                llm_base_url=base_url,
                llm_temperature=0.0
            )
        ),
        skills_path = Path(skills_path) if skills_path else None,
        available_agents = available_agents,
        available_tools = available_tools,
        mcp_config = mcp_config,
        use_self_resources = True
    )


def generate_yaml_path(query: str) -> str:
    """Generate YAML file path based on query and timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
    filename = f"{timestamp}_{query_hash}.yaml"

    # Save to ~/.aworld/tasks/
    base_dir = Path.home() / ".aworld" / "tasks"
    base_dir.mkdir(parents=True, exist_ok=True)

    return str(base_dir / filename)


async def run_swarm_composer_agent_for_yaml(
        query: str,
        swarm_composer_agent: 'SwarmComposerAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        context_config: Optional[AmniContextConfig] = None
) -> str:
    """
    Internal helper: Run SwarmComposerAgent and return YAML string.

    Args:
        query: User query
        swarm_composer_agent: SwarmComposerAgent instance
        skills_path: Path to skills directory
        available_agents: Available agents dict
        available_tools: Available tools list
        mcp_config: MCP configuration
        context_config: Context configuration

    Returns:
        YAML string from SwarmComposerAgent
    """
    # Create or use provided SwarmComposerAgent
    if swarm_composer_agent is None:
        swarm_composer_agent = create_default_swarm_composer_agent(
            skills_path=skills_path,
            available_agents=available_agents,
            available_tools=available_tools,
            mcp_config=mcp_config
        )
        logger.info("📝 Using default SwarmComposerAgent for task planning")
    else:
        logger.info(f"📝 Using custom SwarmComposerAgent: {swarm_composer_agent.name}")

    # Create Task and Message to run SwarmComposerAgent
    from aworld.core.common import Observation
    from aworld.core.event.base import Message
    from aworld.core.context.amni import AmniContext

    # Create minimal context for SwarmComposerAgent execution
    context = AmniContext(config=context_config) if context_config else AmniContext()
    meta_message = Message(
        payload=Observation(content=query),
        sender="system",
    )
    meta_message.context = context

    # Run SwarmComposerAgent (returns AgentMessage with YAML in payload)
    agent_result = await exec_agent(agent=swarm_composer_agent, question=query, context=context)
    return agent_result.answer



