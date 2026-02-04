# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
from pathlib import Path
from typing import List, Dict, Union, AsyncGenerator, Tuple, Any, Optional, TYPE_CHECKING

from aworld import trace

if TYPE_CHECKING:
    from aworld.agents.meta_agent import MetaAgent
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
from aworld.utils.run_util import exec_tasks, generate_yaml_path, create_default_meta_agent


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
    # Auto Run Task: MetaAgent-based task planning and execution
    # ============================================================
    
    @staticmethod
    async def plan_task(
        query: str,
        *,
        meta_agent: 'MetaAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        output_yaml: str = None,
        auto_save: bool = True
    ) -> str:
        """
        Use MetaAgent to analyze query and generate Task YAML.
        
        This is step 1 of the two-step auto_run_task workflow.
        The generated YAML can be reviewed/modified before execution.
        
        Args:
            query: User query to analyze and plan for
            meta_agent: MetaAgent instance (if None, creates a default one)
            skills_path: Path to skills directory for scanning available skills
            available_agents: Dict of predefined agents {agent_id: agent_instance}
            available_tools: List of available tool names
            mcp_config: Global MCP server configurations
            output_yaml: Output YAML path (if None, auto-generate to ~/.aworld/tasks/)
            auto_save: Whether to save YAML to file (if False, returns YAML string)
        
        Returns:
            Path to generated YAML file (if auto_save=True) or YAML string (if auto_save=False)
        
        Example:
            >>> yaml_path = await Runners.plan_task(
            ...     query="Help me find the latest one-week stock price of BABA and analyze the trend.",
            ...     skills_path="./skills",
            ...     output_yaml="./my_task.yaml"
            ... )
            >>> # Review/modify YAML...
            >>> results = await Runners.execute_plan(yaml_path, skills_path="./skills")
        """
        from aworld.agents.meta_agent import MetaAgent
        
        # 1. Create or use provided MetaAgent
        if meta_agent is None:
            meta_agent = create_default_meta_agent()
            logger.info("📝 Using default MetaAgent for task planning")
        else:
            logger.info(f"📝 Using custom MetaAgent: {meta_agent.name}")
        
        # 2. Call MetaAgent to generate YAML
        logger.info(f"🧠 Analyzing query: {query[:100]}..." if len(query) > 100 else f"🧠 Analyzing query: {query}")
        
        yaml_str = await meta_agent.plan_task(
            query=query,
            skills_path=Path(skills_path) if skills_path else None,
            available_agents=available_agents,
            available_tools=available_tools,
            mcp_config=mcp_config
        )
        
        # 3. Save YAML if requested
        if auto_save:
            if not output_yaml:
                # Auto-generate path by default: ~/.aworld/tasks/{timestamp}_{hash}.yaml
                output_yaml = generate_yaml_path(query)
            
            output_path = Path(output_yaml)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(yaml_str)
            
            logger.info(f"✅ Task YAML saved to: {output_path}")
            return str(output_path)
        else:
            logger.info("✅ Task YAML generated (not saved)")
            return yaml_str
    
    @staticmethod
    async def execute_plan(
        yaml_path: str,
        *,
        available_agents: Dict[str, BaseAgent] = None,
        skills_path: Union[str, Path] = None,
        context_config: Optional[AmniContextConfig] = None,
        run_conf: RunConfig = None,
        **task_overrides
    ) -> Dict[str, TaskResponse]:
        """
        Execute a Task from YAML plan.
        
        This is step 2 of the two-step auto_run_task workflow.
        Loads the YAML, instantiates agents/swarm, and executes the task.
        
        Args:
            yaml_path: Path to Task YAML file (generated by plan_task)
            available_agents: Dict of predefined agents (for type='predefined')
            skills_path: Path to skills directory (for type='skill')
            context_config: Context configuration for task execution
            run_conf: Runtime configuration
            **task_overrides: Override task configs (timeout, session_id, task_id, etc.)
        
        Returns:
            Task execution results {task_id: TaskResponse}
        
        Example:
            >>> results = await Runners.execute_plan(
            ...     "task_plan.yaml",
            ...     available_agents={"search_agent": my_search_agent},
            ...     skills_path="./skills"
            ... )
            >>> print(results[task_id].answer)
        """
        from aworld.config.task_loader import load_task_from_yaml
        
        logger.info(f"📋 Executing plan from: {yaml_path}")
        
        # 1. Load Task from YAML
        task = await load_task_from_yaml(
            yaml_path,
            available_agents=available_agents,
            skills_path=Path(skills_path) if skills_path else None,
            context_config=context_config,
            **task_overrides
        )
        
        # 2. Execute Task
        logger.info(f"🚀 Running task: {task.id}")
        results = await Runners.run_task(task, run_conf=run_conf)
        
        logger.info(f"✅ Task completed: {task.id}")
        return results
    
    @staticmethod
    async def auto_run_task(
        query: str,
        *,
        meta_agent: 'MetaAgent' = None,
        skills_path: Union[str, Path] = None,
        available_agents: Dict[str, BaseAgent] = None,
        available_tools: List[str] = None,
        mcp_config: Dict[str, Any] = None,
        context_config: Optional[AmniContextConfig] = None,
        run_conf: RunConfig = None,
        output_yaml: str = None,
        save_plan: bool = True,
        **task_overrides
    ) -> Tuple[Dict[str, TaskResponse], str]:
        """
        Auto-run task: plan + execute in one call.
        
        Combines plan_task and execute_plan into a single convenient interface.
        
        Args:
            query: User query to analyze and execute
            meta_agent: MetaAgent for planning (if None, uses default)
            skills_path: Path to skills directory
            available_agents: Dict of predefined agents
            available_tools: List of available tools
            mcp_config: Global MCP server configurations
            context_config: Context configuration for execution
            run_conf: Runtime configuration
            output_yaml: Output YAML path (if None and save_plan=True, auto-generate)
            save_plan: Whether to save Task YAML to file
            **task_overrides: Override task configs (timeout, session_id, etc.)
        
        Returns:
            Tuple of (task_results, yaml_path_or_string)
            - task_results: {task_id: TaskResponse}
            - yaml_path_or_string: Path to saved YAML if save_plan=True, else YAML string
        
        Example:
            >>> results, yaml_path = await Runners.auto_run_task(
            ...     query="Help me find the latest one-week stock price of BABA and analyze the trend.",
            ...     skills_path="./skills",
            ...     save_plan=True
            ... )
            >>> print(f"Plan saved at: {yaml_path}")
            >>> print(f"Answer: {results[task_id].answer}")
        """
        logger.info("🎯 Auto-running task with MetaAgent planning...")
        
        # 1. Plan
        yaml_path_or_str = await Runners.plan_task(
            query=query,
            meta_agent=meta_agent,
            skills_path=skills_path,
            available_agents=available_agents,
            available_tools=available_tools,
            mcp_config=mcp_config,
            output_yaml=output_yaml,
            auto_save=save_plan
        )
        
        # 2. Execute
        # If save_plan=False, yaml_path_or_str is YAML string, need to save temporarily
        if not save_plan:
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
                f.write(yaml_path_or_str)
                temp_yaml_path = f.name
            yaml_to_execute = temp_yaml_path
        else:
            yaml_to_execute = yaml_path_or_str
        
        results = await Runners.execute_plan(
            yaml_path=yaml_to_execute,
            available_agents=available_agents,
            skills_path=skills_path,
            context_config=context_config,
            run_conf=run_conf,
            **task_overrides
        )
        
        # Clean up temp file if needed
        if not save_plan:
            import os
            os.unlink(temp_yaml_path)
        
        logger.info("🎉 Auto-run task completed!")
        return results, yaml_path_or_str
