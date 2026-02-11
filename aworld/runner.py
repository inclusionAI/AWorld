# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from pathlib import Path
from typing import List, Dict, Union, AsyncGenerator, Tuple, Any, Optional, TYPE_CHECKING

from aworld import trace

if TYPE_CHECKING:
    from aworld.agents.swarm_composer_agent import SwarmComposerAgent
from aworld.config import RunConfig, EvaluationConfig, TaskRunMode
from aworld.config.conf import TaskConfig
from aworld.agents.llm_agent import Agent
from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Config, StreamingMode
from aworld.core.context.amni.config import AmniContextConfig
from aworld.core.event.base import Message
from aworld.core.task import Task, TaskResponse
from aworld.evaluations.base import EvalTask
from aworld.logs.util import logger
from aworld.output import StreamingOutputs
from aworld.runners.evaluate_runner import EvaluateRunner
from aworld.runners.utils import execute_runner, choose_runners
from aworld.utils.common import sync_exec
from aworld.utils.run_util import exec_tasks, generate_yaml_path, create_default_swarm_composer_agent, run_swarm_composer_agent_for_yaml


class Runners:
    """Unified entrance to the utility class of the runnable task of execution."""

    @staticmethod
    def streamed_run_task(task: Task, run_conf: RunConfig = None) -> StreamingOutputs:
        """Run the task in stream output."""

        with trace.task_span("streamed_run_task",
                                   task=task,
                                   attributes={"aworld.trace.id": task.context.trace_id}):
            if not task.conf:
                task.conf = TaskConfig()

            streamed_result = StreamingOutputs(
                input=task.input,
                usage={},
                is_complete=False
            )
            task.outputs = streamed_result
            streamed_result.task_id = task.id

            logger.info(f"start task_id={task.id}, agent={task.agent}, swarm = {task.swarm} ")
            streamed_result._run_impl_task = asyncio.create_task(
                Runners.run_task(task, run_conf=run_conf)
            )
            return streamed_result

    @staticmethod
    async def run_task(task: Union[Task, List[Task]], run_conf: RunConfig = None) -> Dict[str, TaskResponse]:
        """Run tasks for some complex scenarios where agents cannot be directly used.

        Args:
            task: User task define.
            run_conf:
        """
        if isinstance(task, Task):
            task = [task]

        logger.debug(f"task_id: {task[0].id} start")
        result = await exec_tasks(task, run_conf)
        logger.debug(f"task_id: {task[0].id} end")
        return result

    @staticmethod
    def sync_run_task(task: Union[Task, List[Task]], run_conf: Config = None) -> Dict[str, TaskResponse]:
        return sync_exec(Runners.run_task, task=task, run_conf=run_conf)

    @staticmethod
    async def streaming_run_task(
            task: Task,
            streaming_mode: StreamingMode = StreamingMode.CORE,
            run_conf: RunConfig = None
    ) -> AsyncGenerator[Message, None]:
        """Run task with streaming native message.

        Args:
            task: Task to execute.
            streaming_mode: Streaming mode.
            run_conf: Runtime configuration.

        Yields:
            Message objects from the streaming queue.
        """
        if not run_conf:
            run_conf = RunConfig()

        # Set up task with streaming mode
        task.streaming_mode = streaming_mode
        runners = await choose_runners([task])
        runner = runners[0]
        asyncio.create_task(execute_runner(runners, run_conf))

        async for event in runner.streaming():
            yield event

    @staticmethod
    async def streaming_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            streaming_mode: StreamingMode = StreamingMode.CORE,
            tool_names: List[str] = [],
            session_id: str = None,
            run_conf: RunConfig = None
    ) -> AsyncGenerator[Message, None]:
        """Run agent/swarm with streaming native message."""
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names,
                    event_driven=swarm.event_driven, session_id=session_id)
        async for msg in Runners.streaming_run_task(task, streaming_mode, run_conf=run_conf):
            yield msg

    @staticmethod
    def sync_run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None,
            run_conf: RunConfig = None
    ) -> TaskResponse:
        return sync_exec(
            Runners.run,
            input=input,
            agent=agent,
            swarm=swarm,
            tool_names=tool_names,
            session_id=session_id,
            run_conf=run_conf
        )

    @staticmethod
    async def run(
            input: str,
            agent: Agent = None,
            swarm: Swarm = None,
            tool_names: List[str] = [],
            session_id: str = None,
            run_conf: RunConfig = None
    ) -> TaskResponse:
        """Run agent directly with input and tool names.

        Args:
            input: User query.
            agent: An agent with AI model configured, prompts, tools, mcp servers and other agents.
            swarm: Multi-agent topo.
            tool_names: Tool name list.
            session_id: Session id.

        Returns:
            TaskResponse: Task response.
        """
        if agent and swarm:
            raise ValueError("`agent` and `swarm` only choose one.")

        if not input:
            raise ValueError('`input` is empty.')

        if agent:
            agent.task = input
            swarm = Swarm(agent)

        task = Task(input=input, swarm=swarm, tool_names=tool_names,
                    event_driven=swarm.event_driven, session_id=session_id)
        res = await Runners.run_task(task, run_conf=run_conf)
        return res.get(task.id)

    @staticmethod
    async def evaluate(task: EvalTask = None,
                       eval_conf: EvaluationConfig = None,
                       run_conf: RunConfig = None):
        # todo: unify in exec_tasks
        runner = EvaluateRunner(task=task, config=eval_conf)
        return await execute_runner([runner], run_conf)

    @staticmethod
    async def start_agent_server(agent: Union[Agent, Swarm], serving_config):
        """Utility function for start an agent server."""
        from aworld.experimental.a2a.agent_server import AgentServer

        agent_server = AgentServer(agent, serving_config)
        return await agent_server.start()

    @staticmethod
    async def step(task: Task, run_conf: RunConfig = None) -> Tuple[bool, str, TaskResponse]:
        """Run a single step of the task."""
        is_finished = True
        observation = None
        task.conf.run_mode = TaskRunMode.INTERACTIVE
        responses = await Runners.run_task(task, run_conf=run_conf)
        resp = responses.get(task.id)
        if resp.status == "running":
            is_finished = False
            task.observation = observation
        observation = resp.answer if resp else None
        return is_finished, observation, resp

    @staticmethod
    async def evolve(task: Any, evolve_conf=None, run_conf: RunConfig = None):
        """Run evolve task."""
        from train.evolve.config import EvolutionConfig
        from train.evolve.evolution_runner import EvolutionRunner

        if not evolve_conf:
            evolve_conf = EvolutionConfig(run_conf=run_conf)
        if run_conf:
            evolve_conf.run_conf = run_conf
        runner = EvolutionRunner(task=task, config=evolve_conf)
        await execute_runner([runner], run_conf)
    
    # ============================================================
    # SwarmComposerAgent-based task planning and execution
    # ============================================================
    
    @staticmethod
    async def text_to_swarm(
        query: str,
        *,
        swarm_composer_agent: 'SwarmComposerAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        context_config: Optional[AmniContextConfig] = None,
        **swarm_overrides
    ) -> Swarm:
        """
        Convert text query to Swarm using SwarmComposerAgent.
        
        This method generates a reusable Swarm instance from natural language description.
        The Swarm can be used to create multiple Tasks for different queries.
        
        Args:
            query: User query describing the team structure or task requirements
            swarm_composer_agent: SwarmComposerAgent instance (if None, creates a default one)
            skills_path: Path to skills directory for scanning available skills
            available_agents: Dict of predefined agents {agent_id: agent_instance}
            available_tools: List of available tool names
            mcp_config: Global MCP server configurations
            context_config: Context configuration (not used for swarm, kept for consistency)
            **swarm_overrides: Override swarm configs (max_steps, event_driven, etc.)
        
        Returns:
            Swarm instance ready to be used in Task creation
        
        Example:
            >>> # Generate a reusable swarm
            >>> swarm = await Runners.text_to_swarm(
            ...     query="Create a stock analysis team with data collector, analyst, and risk assessor",
            ...     skills_path="./skills"
            ... )
            >>> 
            >>> # Use the swarm for multiple tasks
            >>> task1 = await Runners.text_to_task("Analyze BABA stock", swarm=swarm)
            >>> task2 = await Runners.text_to_task("Analyze TCEHY stock", swarm=swarm)
        """
        from aworld.config.task_loader import load_swarm_from_yaml_dict
        import yaml
        
        # 1. Run SwarmComposerAgent to generate complete YAML
        logger.info(f"🧠 Analyzing query for swarm generation: {query[:100]}..." if len(query) > 100 else f"🧠 Analyzing query for swarm generation: {query}")
        
        yaml_str = await run_swarm_composer_agent_for_yaml(
            swarm_composer_agent=swarm_composer_agent,
            query=query,
            skills_path=skills_path,
            available_agents=available_agents,
            available_tools=available_tools,
            mcp_config=mcp_config,
            context_config=context_config
        )
        
        # 2. Parse YAML string to dict
        try:
            yaml_dict = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML from SwarmComposerAgent: {e}")
        
        # 3. Load Swarm from YAML dict (only extract agents + swarm sections)
        swarm = await load_swarm_from_yaml_dict(
            yaml_dict,
            available_agents=available_agents,
            skills_path=Path(skills_path) if skills_path else None,
            global_mcp_config=yaml_dict.get("mcp_config"),
            **swarm_overrides
        )
        
        logger.info(f"✅ Swarm created: type={swarm.build_type}, agents={len(swarm.agents)}")
        return swarm
    
    @staticmethod
    async def text_to_task(
        query: str,
        *,
        swarm: Swarm = None,
        swarm_composer_agent: 'SwarmComposerAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        context_config: Optional[AmniContextConfig] = None,
        **task_overrides
    ) -> Task:
        """
        Convert text query to Task and Swarm objects using SwarmComposerAgent.
        
        This method supports two modes:
        1. Full generation (swarm=None): Generate both swarm and task from query
        2. Swarm reuse (swarm=provided): Use existing swarm, only create task
        
        Args:
            query: User query to analyze and plan for
            swarm: Optional pre-generated Swarm instance (if None, generates new swarm)
            swarm_composer_agent: SwarmComposerAgent instance (if None, creates a default one)
            skills_path: Path to skills directory for scanning available skills
            available_agents: Dict of predefined agents {agent_id: agent_instance}
            available_tools: List of available tool names
            mcp_config: Global MCP server configurations
            context_config: Context configuration for task execution
            **task_overrides: Override task configs (timeout, session_id, task_id, etc.)
        
        Returns:
            Task instance ready for execution or further processing
        
        Example:
            >>> # Mode 1: Full generation (backward compatible)
            >>> task = await Runners.text_to_task(
            ...     query="Help me find the latest stock price of BABA.",
            ...     skills_path="./skills"
            ... )
            >>> 
            >>> # Mode 2: Swarm reuse
            >>> swarm = await Runners.text_to_swarm("Create stock analysis team")
            >>> task = await Runners.text_to_task(
            ...     query="Analyze BABA stock",
            ...     swarm=swarm
            ... )
        """
        from aworld.config.task_loader import load_task_from_yaml
        import tempfile

        # Check if swarm is provided
        if swarm is None:
            # Mode 1: Full generation - generate both swarm and task
            logger.info(f"🧠 Analyzing query (full generation): {query[:100]}..." if len(query) > 100 else f"🧠 Analyzing query (full generation): {query}")
            
            # Generate swarm first
            swarm = await Runners.text_to_swarm(
                query=query,
                swarm_composer_agent=swarm_composer_agent,
                skills_path=skills_path,
                available_agents=available_agents,
                available_tools=available_tools,
                mcp_config=mcp_config,
                context_config=context_config
            )
            
            # Create Task with generated swarm
            task = Task(
                input=query,
                swarm=swarm,
                tool_names=task_overrides.pop("tool_names", []),
                event_driven=swarm.event_driven,
                session_id=task_overrides.pop("session_id", None),
                id=task_overrides.pop("task_id", None),
                context_config=context_config,
                conf=task_overrides.pop("conf", None),
                **task_overrides
            )
        else:
            # Mode 2: Swarm reuse - use provided swarm, only create task
            logger.info(f"♻️ Reusing provided swarm (type={swarm.build_type}) for query: {query[:100]}..." if len(query) > 100 else f"♻️ Reusing provided swarm (type={swarm.build_type}) for query: {query}")
            
            # Create Task with provided swarm
            task = Task(
                input=query,
                swarm=swarm,
                tool_names=task_overrides.pop("tool_names", []),
                event_driven=swarm.event_driven,
                session_id=task_overrides.pop("session_id", None),
                id=task_overrides.pop("task_id", None),
                context_config=context_config,
                conf=task_overrides.pop("conf", None),
                **task_overrides
            )
        
        logger.info(f"✅ Task created: task_id={task.id}, swarm_type={task.swarm.build_type}")
        return task
    
    @staticmethod
    async def run_by_yaml(
        yaml_path: str,
        *,
        available_agents: Dict[str, BaseAgent] = None,
        skills_path: Union[str, Path] = None,
        context_config: Optional[AmniContextConfig] = None,
        run_conf: RunConfig = None,
        execute: bool = True,
        **task_overrides
    ) -> Union[Task, Tuple[Task, Dict[str, TaskResponse]]]:
        """
        Load Task from YAML configuration and optionally execute it.
        
        This is a foundational method that returns Task object instance.
        Can be used to load and inspect Task before execution, or execute directly.
        
        Args:
            yaml_path: Path to Task YAML file
            available_agents: Dict of predefined agents (for type='predefined')
            skills_path: Path to skills directory (for type='skill')
            context_config: Context configuration for task execution
            run_conf: Runtime configuration
            execute: If True, execute the task and return (task, results); 
                    If False, only load and return task object
            **task_overrides: Override task configs (timeout, session_id, task_id, etc.)
        
        Returns:
            If execute=True: Tuple of (Task, results_dict)
            If execute=False: Task object only
        
        Example:
            >>> # Load and inspect task
            >>> task = await Runners.run_by_yaml("task.yaml", execute=False)
            >>> task.timeout = 300
            >>> 
            >>> # Load and execute
            >>> task, results = await Runners.run_by_yaml(
            ...     "task.yaml",
            ...     available_agents={"agent1": my_agent},
            ...     skills_path="./skills"
            ... )
            >>> print(results[task.id].answer)
        """
        from aworld.config.task_loader import load_task_from_yaml
        
        logger.info(f"📋 Loading task from: {yaml_path}")
        
        # Load Task from YAML
        task = await load_task_from_yaml(
            yaml_path,
            available_agents=available_agents,
            skills_path=Path(skills_path) if skills_path else None,
            context_config=context_config,
            **task_overrides
        )
        
        logger.info(f"✅ Task loaded: id={task.id}, swarm_type={task.swarm.build_type}")
        
        if not execute:
            return task
        
        # Execute Task
        logger.info(f"🚀 Running task: {task.id}")
        results = await Runners.run_task(task, run_conf=run_conf)
        
        logger.info(f"✅ Task completed: {task.id}")
        return task, results
    
    @staticmethod
    async def text_to_run(
        query: str,
        *,
        swarm: Swarm = None,
        swarm_composer_agent: 'SwarmComposerAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        context_config: Optional[AmniContextConfig] = None,
        run_conf: RunConfig = None,
        **task_overrides
    ) -> Tuple[Task, Dict[str, TaskResponse]]:
        """
        Convert text query to execution results in one call.
        
        This is a high-level convenience method that combines text_to_task and execution.
        Now supports optional swarm parameter for swarm reuse.
        
        Args:
            query: User query to analyze and execute
            swarm: Optional pre-generated Swarm instance
            swarm_composer_agent: SwarmComposerAgent for planning (if None, uses default)
            skills_path: Path to skills directory
            available_agents: Dict of predefined agents
            available_tools: List of available tools
            mcp_config: Global MCP server configurations
            context_config: Context configuration for execution
            run_conf: Runtime configuration
            **task_overrides: Override task configs (timeout, session_id, etc.)
        
        Returns:
            Tuple of (Task, results_dict)
        
        Example:
            >>> # Mode 1: Full generation
            >>> task, results = await Runners.text_to_run(
            ...     query="Help me find the latest stock price of BABA.",
            ...     skills_path="./skills"
            ... )
            >>> 
            >>> # Mode 2: Swarm reuse
            >>> swarm = await Runners.text_to_swarm("Create stock analysis team")
            >>> task, results = await Runners.text_to_run(
            ...     query="Analyze BABA stock",
            ...     swarm=swarm
            ... )
        """
        logger.info("🎯 Converting text to execution with SwarmComposerAgent planning...")
        
        # 1. Convert text to Task (with optional swarm)
        task = await Runners.text_to_task(
            query=query,
            swarm=swarm,
            swarm_composer_agent=swarm_composer_agent,
            skills_path=skills_path,
            available_agents=available_agents,
            available_tools=available_tools,
            mcp_config=mcp_config,
            context_config=context_config,
            **task_overrides
        )
        
        # 2. Execute Task
        logger.info(f"🚀 Running task: {task.id}")
        results = await Runners.run_task(task, run_conf=run_conf)
        
        logger.info("🎉 Text-to-run completed!")
        return task, results
