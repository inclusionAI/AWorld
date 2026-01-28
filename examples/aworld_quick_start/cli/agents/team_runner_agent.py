# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from aworld.agents.llm_agent import Agent
from aworld.config import TaskConfig
from aworld.core.common import Observation, ActionModel
from aworld.core.context.amni import ApplicationContext, workspace_repo, AmniContextConfig, TaskInput
from aworld.core.context.amni.config import get_default_config, ContextEnvConfig
from aworld.experimental.metalearning.knowledge.knowledge import TrajectoryService
from aworld.core.context.amni.utils.storage import FileTrajectoryStorage
from aworld.core.event.base import Message
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.output.base import TopologyOutput
from aworld.runner import Runners


class TeamRunContent(BaseModel):
    """Team run content data model"""
    team_name: str
    task_input: Optional[str] = None


class TeamRunnerAgent(Agent):
    """Team Runner Agent - Responsible for loading and executing team tasks core logic"""

    def __init__(self, **kwargs):
        """Initialize Team Runner Agent"""
        super().__init__(**kwargs)
        logger.info(f"TeamRunnerAgent|Initialization completed, agent_name={self.name()}")

    def _parse_content_to_team_run_content(self, content: str) -> TeamRunContent:
        """
        Parse and convert from observation.content to TeamRunContent object
        
        Args:
            content: Observation content, which may be a JSON string or text containing JSON
            
        Returns:
            TeamRunContent object
        """
        try:
            # Try to parse JSON directly
            if isinstance(content, str):
                # Try to extract JSON code block
                if "```json" in content:
                    start = content.find("```json") + 7
                    end = content.find("```", start)
                    json_str = content[start:end].strip()
                elif "```" in content:
                    start = content.find("```") + 3
                    end = content.find("```", start)
                    json_str = content[start:end].strip()
                else:
                    json_str = content.strip()

                params = json.loads(json_str)
            else:
                # If content is already a dict
                params = content if isinstance(content, dict) else {}

            # Support nested JSON format, e.g.: {"content": "{\"team_name\": \"...\"}"}
            if isinstance(params, dict) and "team_name" not in params and "content" in params:
                try:
                    inner_content = params["content"]
                    if isinstance(inner_content, str):
                        # Try to parse inner_content
                        # Handle possible markdown json block
                        if "```json" in inner_content:
                            start = inner_content.find("```json") + 7
                            end = inner_content.find("```", start)
                            inner_content = inner_content[start:end].strip()
                        elif "```" in inner_content:
                            start = inner_content.find("```") + 3
                            end = inner_content.find("```", start)
                            inner_content = inner_content[start:end].strip()
                        
                        inner_params = json.loads(inner_content)
                        if isinstance(inner_params, dict):
                            params.update(inner_params)
                except Exception as e:
                    # Ignore parsing errors, continue using original params
                    logger.warning(f"TeamRunnerAgent|Failed to parse nested content: {e}")

            # Convert to TeamRunContent object
            team_run_content = TeamRunContent(**params)

            if not team_run_content.team_name:
                raise ValueError("team_name is required in content")

            return team_run_content
        except (json.JSONDecodeError, ValueError, Exception) as e:
            logger.error(f"TeamRunnerAgent|Failed to parse content: {e}, content: {content}")
            raise ValueError(f"Failed to parse content to TeamRunContent: {e}")

    async def build_task_context(self, parent_context: ApplicationContext, task_input: TaskInput,
                                 context_config: AmniContextConfig,
                                 **kwargs) -> ApplicationContext:
        """
        Build task context
        
        Args:
            parent_context: Parent context
            task_input: Task input
            context_config: Context configuration
            
        Returns:
            New application context
        """
        # 1. init workspace
        workspace = await workspace_repo.get_session_workspace(session_id=task_input.session_id)

        # 2. init context
        context = await ApplicationContext.from_input(task_input, workspace=workspace, context_config=context_config)

        # 3. outputs
        context.put('outputs', parent_context.outputs)
        return context

    def build_context_config(self, context):
        config = get_default_config()
        config.debug_mode = True
        config.agent_config = context.get_agent_context_config(namespace="default")
        config.agent_config.meta_learning = True
        config.env_config = ContextEnvConfig(
            env_type="remote",
            env_config={
                "URL": "http://mcp.aworldagents.com/vpc-pre/mcp",
                "TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY",
                "IMAGE_VERSION": "mobile-20251226115035"
            }
        )
        return config

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """
        Execute core logic of team observer
        
        Args:
            observation: Observation object containing parameters to be processed
            info: Additional information
            message: Message object containing context information
            
        Returns:
            List of action models containing execution results
        """
        logger.info(f"TeamRunnerAgent|Starting async_policy execution, observation.content type={type(observation.content)}")

        # Set unfinished status
        self._finished = False

        try:
            # Validate context
            if not message or not message.context:
                raise ValueError("message or message.context is required")

            if not isinstance(message.context, ApplicationContext):
                raise ValueError("context is not ApplicationContext")

            context: ApplicationContext = message.context
            logger.debug(f"TeamRunnerAgent|Getting data from context")

            # Parse parameters and convert to TeamRunContent
            content = observation.content
            team_run_content = self._parse_content_to_team_run_content(content)
            team_name = team_run_content.team_name

            logger.info(f"TeamRunnerAgent|Preparing to load team: {team_run_content.team_name}, task_input: {team_run_content.task_input}")

            # Load latest version swarm from agent_registry_service and execute
            swarm_registry_service = context.swarm_registry_service
            swarm, agents = await swarm_registry_service.load_swarm_and_agents(team_name=team_name, session_id=context.session_id)
            swarm_source, agents_source = await swarm_registry_service.load_swarm_and_agents_as_source(name=team_name, session_id=context.session_id)
            logger.info(f"TeamRunnerAgent|Preparing to save meta: {team_run_content.team_name}, swarm_source: {swarm_source}, agents_source: {agents_source}")
            await TrajectoryService.save_meta(context=context, swarm_source=swarm_source, agents_source=agents_source)

            # Output topology structure
            if hasattr(swarm, 'topology'):
                try:
                    def _serialize_topology(topo):
                        if isinstance(topo, (list, tuple)):
                            return [_serialize_topology(item) for item in topo]
                        elif hasattr(topo, 'name'):
                            name = topo.name() if callable(topo.name) else topo.name
                            return {"name": name, "type": type(topo).__name__}
                        return str(topo)

                    topo_data = _serialize_topology(swarm.topology)
                    
                    # Collect agent details
                    agent_details = {}
                    
                    # Handle agents being dict or list
                    agent_iter = agents.values() if isinstance(agents, dict) else agents
                    
                    for agent in agent_iter:
                        details = {
                            "type": type(agent).__name__,
                            "description": agent.desc() if hasattr(agent, 'desc') else "",
                            "system_prompt": agent.system_prompt if hasattr(agent, 'system_prompt') else "",
                            "tools": [],
                            "skills": [],
                            "mcp_servers": []
                        }
                        
                        # Tools
                        if hasattr(agent, 'tool_names'):
                            details["tools"] = agent.tool_names
                        
                        # Skills
                        if hasattr(agent, 'skill_configs') and agent.skill_configs:
                            if isinstance(agent.skill_configs, dict):
                                details["skills"] = list(agent.skill_configs.keys())

                        # MCP Servers
                        if hasattr(agent, 'mcp_servers') and agent.mcp_servers:
                            details["mcp_servers"] = agent.mcp_servers
                        
                        agent_name = agent.name() if callable(agent.name) else agent.name
                        agent_details[agent_name] = details

                    await context.outputs.add_output(TopologyOutput(
                        topology=topo_data,
                        team_name=team_name,
                        agent_details=agent_details,
                        task_id=context.task_id
                    ))
                except Exception as e:
                    logger.warning(f"TeamRunnerAgent|Failed to output topology structure: {e} {traceback.format_exc()}")

            logger.info(f"TeamRunnerAgent|Successfully loaded team: {team_name}, number of agents: {len(agents)}")

            # team_runner_agent by default should record trajectory task_id to artifact
            sub_context = await context.build_sub_context(sub_task_content=content,
                                                          sub_task_id=f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                                                          agents=swarm.agents)

            # Create task
            task = Task(
                input=content,
                swarm=swarm,
                context=sub_context,
                outputs=context.outputs,
                conf=TaskConfig(trajectory_storage=FileTrajectoryStorage)
            )

            # Execute task
            logger.info(f"TeamRunnerAgent|Starting swarm task execution")
            task_response = await Runners.run_task(task=task)

            # Get result
            result_content = task_response.answer if hasattr(task_response, 'answer') else str(task_response)
            logger.info(f"TeamRunnerAgent|Swarm execution completed, result length: {len(result_content) if result_content else 0}")

            # Mark as completed
            self._finished = True

            # Return result
            return [ActionModel(
                agent_name=self.id(),
                policy_info=result_content
            )]

        except Exception as e:
            fail_error = str(e)
            logger.error(f"TeamRunnerAgent|async_policy execution failed: {fail_error}")
            logger.warn(f"TeamRunnerAgent|Detailed error information: {traceback.format_exc()}")

            # Mark as completed (even if failed)
            self._finished = True

            # Return error information
            return [ActionModel(
                agent_name=self.id(),
                policy_info=f"Execution failed: {fail_error}"
            )]
