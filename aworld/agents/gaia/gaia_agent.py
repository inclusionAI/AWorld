# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import json
import traceback
from enum import Enum
from typing import Any, Dict, List, Tuple, Union

from openai import Stream
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from aworld.agents.gaia.prompts import *
from aworld.agents.gaia.utils import extract_pattern
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.core.agent.base import Agent, AgentFactory, AgentResult, is_agent_by_name
from aworld.core.common import ActionModel, Observation
from aworld.logs.util import logger


class GaiaAgents(Enum):
    """
    Gaia agents for planning and execution tasks
    """

    GAIA_PLAN = "gaia_plan_agent"
    GAIA_EXECUTE = "gaia_execute_agent"


@AgentFactory.register(name=GaiaAgents.GAIA_PLAN.value, desc=GaiaAgents.GAIA_PLAN.value)
class GaiaPlanAgent(Agent):
    """
    Planning agent that decomposes tasks and creates execution plans.
    Responsible for high-level strategy and delegating to execution agents.
    """

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        """
        Initialize the GaiaPlanAgent with configuration.

        Args:
            conf: Agent configuration
            **kwargs: Additional keyword arguments

        Raises:
            Exception: If initialization fails
        """
        try:
            super(GaiaPlanAgent, self).__init__(conf, **kwargs)
            self.desc_transform()
        except Exception as e:
            logger.error(f"Failed to initialize GaiaPlanAgent: {str(e)}")
            raise

    def reset(self, options: Dict[str, Any]):
        """
        Reset the agent state with new options.

        Args:
            options: Configuration options for the agent

        Raises:
            ValueError: If required task is missing
        """
        try:
            options = {} if options is None else options
            self.task = options.get("task")
            if not self.task:
                raise ValueError("Task description is required")

            self.tool_names = options.get("tool_names", [])
            self.handoffs = options.get("agent_names", [])
            self.mcp_servers = options.get("mcp_servers", [])

            # Initialize prompts with task context
            self.first_prompt = init_prompt
            self.system_prompt = plan_system_prompt.format(task=self.task)
            self.agent_prompt = plan_done_prompt.format(task=self.task)
            self.output_prompt = plan_postfix_prompt.format(task=self.task)

            # Reset state tracking variables
            self.first = True
            self._finished = False
            self.trajectory = []
        except Exception as e:
            logger.error(f"Failed to reset GaiaPlanAgent: {str(e)}")
            raise

    def policy(
        self, observation: Observation, info: Dict[str, Any] = None, **kwargs
    ) -> List[ActionModel] | None:
        """
        Execute the agent's policy based on current observation.

        Args:
            observation: The observation from the environment
            info: Additional information from the environment
            **kwargs: Additional keyword arguments

        Returns:
            List of actions to take

        Raises:
            Exception: If LLM query fails
        """
        try:
            # Transform observation to LLM messages
            messages, current_observation = self._transform_messages(observation)

            # Query LLM for completions
            try:
                completions = self.llm.chat.completions.create(
                    messages=messages,
                    model=self.model_name,
                )
            except Exception as e:
                logger.error(f"LLM query failed: {traceback.format_exc()}")
                raise e

            # Parse completions into actions
            results = self._parse_completions(
                completions=completions,
                observation=current_observation,
                info=info,
            )
            logger.success(
                f"{GaiaAgents.GAIA_PLAN.value}'s AgentResult: {results.actions}"
            )

            # Update first interaction flag
            self.first = False
            return results.actions
        except Exception as e:
            logger.error(f"Policy execution failed: {str(e)}")
            raise

    def _transform_messages(
        self, observation: Observation
    ) -> Tuple[List[Dict[str, Any]], Observation]:
        """
        Transform observations into LLM message format.

        Args:
            observation: The observation from the environment

        Returns:
            Tuple of (messages for LLM, current observation)

        Raises:
            ValueError: If message transformation fails
        """
        try:
            messages = []
            # Step 1: Add system message for context
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})

            # Step 2: Add conversation history
            for obs, _, agent_results in self.trajectory:
                completion = agent_results.current_state
                if (
                    not completion
                    or not hasattr(completion, "choices")
                    or not completion.choices
                ):
                    continue

                # Add user message (observation)
                messages.append({"role": "user", "content": obs.content})

                # Add assistant response (may include tool calls)
                assistant_content = completion.choices[0].message.content
                assistant_tool_calls = completion.choices[0].message.tool_calls

                if assistant_tool_calls is not None:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": assistant_tool_calls,
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_content,
                        }
                    )

            # Step 3: Add current message
            current_message = self.first_prompt if self.first else observation.content
            messages.append({"role": "user", "content": current_message})

            # Create a copy of the observation with updated content
            current_observation = copy.deepcopy(observation)
            current_observation.content = current_message

            return messages, current_observation
        except Exception as e:
            logger.error(f"Message transformation failed: {str(e)}")
            raise ValueError(f"Failed to transform messages: {str(e)}")

    def _parse_completions(
        self,
        completions: ChatCompletion | Stream[ChatCompletionChunk],
        observation: Observation,
        info: Dict[str, Any] = None,
    ) -> AgentResult:
        """
        Parse LLM completions into agent actions.

        Args:
            completions: The LLM completion response
            observation: The current observation
            info: Additional information

        Returns:
            AgentResult containing actions and state
        """
        try:
            results = []
            # Handle empty completions
            if not completions or not completions.choices:
                logger.warning(f"Empty LLM result for input: {observation.content}")
                return AgentResult(actions=[], current_state=None)

            is_call_tool = False
            content = completions.choices[0].message.content
            tool_calls = completions.choices[0].message.tool_calls

            # Process tool calls if present
            if tool_calls:
                is_call_tool = True
                for tool_call in tool_calls:
                    full_name: str = tool_call.function.name
                    if not full_name:
                        logger.warning("Tool call missing tool name")
                        continue

                    # Parse tool parameters
                    try:
                        params = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid tool arguments JSON: {e}")
                        continue

                    # Parse tool name and action
                    names = full_name.split("__")
                    tool_name = names[0]

                    # Handle agent vs tool calls
                    if is_agent_by_name(tool_name):
                        results.append(
                            ActionModel(
                                agent_name=tool_name, params=params, policy_info=content
                            )
                        )
                    else:
                        action_name = "__".join(names[1:]) if len(names) > 1 else None
                        results.append(
                            ActionModel(
                                tool_name=tool_name,
                                action_name=action_name,
                                params=params,
                                policy_info=content,
                            )
                        )
            else:
                # Process text response
                if content:
                    # Clean up content formatting
                    content = content.replace("```json", "").replace("```", "")

                # Add appropriate prompt based on task completion
                if "TASK_DONE" not in content:
                    content += self.agent_prompt
                else:
                    # Task is complete, add final output prompt
                    content += self.output_prompt
                    if not self.first:
                        self._finished = True

                # Delegate to execution agent
                results.append(
                    ActionModel(
                        agent_name=GaiaAgents.GAIA_EXECUTE.value, policy_info=content
                    )
                )

            # Create agent result
            agent_result = AgentResult(
                actions=results, current_state=completions, is_call_tool=is_call_tool
            )

            # Update trajectory with new interaction
            self.trajectory.append((observation, info, agent_result))

            return agent_result
        except Exception as e:
            logger.error(f"Completion parsing failed: {str(e)}")
            return AgentResult(actions=[], current_state=completions)


@AgentFactory.register(
    name=GaiaAgents.GAIA_EXECUTE.value, desc=GaiaAgents.GAIA_EXECUTE.value
)
class GaiaExecuteAgent(Agent):
    """
    Execution agent that carries out specific tasks delegated by the planning agent.
    Responsible for interacting with tools and executing concrete actions.
    """

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        """
        Initialize the GaiaExecuteAgent with configuration.

        Args:
            conf: Agent configuration
            **kwargs: Additional keyword arguments

        Raises:
            Exception: If initialization fails
        """
        try:
            super(GaiaExecuteAgent, self).__init__(conf, **kwargs)
            self.desc_transform()
            self.has_summary = False
            self.first = False
        except Exception as e:
            logger.error(f"Failed to initialize GaiaExecuteAgent: {str(e)}")
            raise

    def reset(self, options: Dict[str, Any]):
        """
        Reset the agent state with new options.

        Args:
            options: Configuration options for the agent

        Raises:
            ValueError: If required task is missing
        """
        try:
            options = {} if options is None else options
            self.task = options.get("task")
            if not self.task:
                raise ValueError("Task description is required")

            self.tool_names = options.get("tool_names", [])
            self.handoffs = options.get("agent_names", [])
            self.mcp_servers = options.get("mcp_servers", [])

            # Initialize system prompt with task context
            self.system_prompt = execute_system_prompt.format(task=self.task)

            # Reset state tracking variables
            self._finished = False
            self.has_summary = False
            self.trajectory = []
        except Exception as e:
            logger.error(f"Failed to reset GaiaExecuteAgent: {str(e)}")
            raise

    def policy(
        self, observation: Observation, info: Dict[str, Any] = None, **kwargs
    ) -> List[ActionModel] | None:
        """
        Execute the agent's policy based on current observation.

        Args:
            observation: The observation from the environment
            info: Additional information from the environment
            **kwargs: Additional keyword arguments

        Returns:
            List of actions to take

        Raises:
            Exception: If LLM query fails
        """
        try:
            # Transform observation to LLM messages
            messages, current_observation = self._transform_messages(observation)

            # Query LLM for completions
            try:
                completions = self.llm.chat.completions.create(
                    messages=messages,
                    model=self.model_name,
                )
            except Exception as e:
                logger.error(f"LLM query failed: {traceback.format_exc()}")
                raise e

            # Parse completions into actions
            results = self._parse_completions(
                completions=completions,
                observation=current_observation,
                info=info,
            )
            logger.success(
                f"{GaiaAgents.GAIA_EXECUTE.value}'s AgentResult: {results.actions}"
            )

            self.first = False
            return results.actions
        except Exception as e:
            logger.error(f"Policy execution failed: {str(e)}")
            raise

    def _transform_messages(
        self, observation: Observation
    ) -> Tuple[List[Dict[str, Any]], Observation]:
        """
        Transform observations into LLM message format.

        Args:
            observation: The observation from the environment

        Returns:
            Tuple of (messages for LLM, current observation)

        Raises:
            ValueError: If message transformation fails
        """
        try:
            messages = []
            # Step 1: Add system message for context
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})

            # Step 2: Add conversation history
            for obs, _, agent_results in self.trajectory:
                completion = agent_results.current_state
                if (
                    not completion
                    or not hasattr(completion, "choices")
                    or not completion.choices
                ):
                    continue

                # Add user message (observation)
                messages.append({"role": "user", "content": obs.content})

                # Add assistant response (may include tool calls)
                message = completion.choices[0].message
                assistant_content = message.content
                assistant_tool_calls = message.tool_calls

                if assistant_tool_calls is not None:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": assistant_tool_calls,
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": assistant_content,
                        }
                    )

            # Step 3: Add current message (from observation or error)
            current_message = (
                observation.content
                if observation.content
                else (
                    observation.action_result[0].error
                    if observation.action_result
                    else "No content available"
                )
            )

            # Handle tool call responses
            if self.trajectory:
                (_, _, last_result) = self.trajectory[-1]
                last_completion = last_result.current_state

                if (
                    last_completion
                    and hasattr(last_completion, "choices")
                    and last_completion.choices
                ):
                    last_message = last_completion.choices[0].message
                    last_assistant_tool_calls = last_message.tool_calls

                    if last_assistant_tool_calls:
                        tool_call_messages = []
                        for tool_call in last_assistant_tool_calls:
                            tool_id = tool_call.id
                            if tool_id:
                                tool_call_messages.append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": tool_id,
                                        "content": current_message,
                                    }
                                )

                        if tool_call_messages:
                            messages.extend(tool_call_messages)
                        else:
                            logger.warning("Tool call response missing tool ID")
                            messages.append(
                                {"role": "user", "content": current_message}
                            )
                    else:
                        messages.append({"role": "user", "content": current_message})
                else:
                    messages.append({"role": "user", "content": current_message})
            else:
                messages.append({"role": "user", "content": current_message})

            # Create a copy of the observation with updated content
            current_observation = copy.deepcopy(observation)
            current_observation.content = current_message

            return messages, current_observation
        except Exception as e:
            logger.error(f"Message transformation failed: {str(e)}")
            raise ValueError(f"Failed to transform messages: {str(e)}")

    def _parse_completions(
        self,
        completions: ChatCompletion | Stream[ChatCompletionChunk],
        observation: Observation,
        info: Dict[str, Any] = None,
    ) -> AgentResult:
        """
        Parse LLM completions into agent actions.

        Args:
            completions: The LLM completion response
            observation: The current observation
            info: Additional information

        Returns:
            AgentResult containing actions and state
        """
        try:
            results = []
            # Handle empty completions
            if not completions or not completions.choices:
                logger.warning(f"Empty LLM result for input: {observation.content}")
                return AgentResult(actions=[], current_state=None)

            is_call_tool = False
            content = completions.choices[0].message.content
            tool_calls = completions.choices[0].message.tool_calls

            # Process tool calls if present
            if tool_calls:
                is_call_tool = True
                for tool_call in tool_calls:
                    full_name: str = tool_call.function.name
                    if not full_name:
                        logger.warning("Tool call missing tool name")
                        continue

                    # Parse tool parameters
                    try:
                        params = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid tool arguments JSON: {e}")
                        continue

                    # Parse tool name and action with error handling
                    try:
                        # Try MCP tools format first
                        _, tool_name, action_name = full_name.split("__")
                    except ValueError:
                        # Fall back to standard format
                        try:
                            tool_name, action_name = full_name.split("__")
                        except ValueError:
                            logger.error(f"Invalid tool name format: {full_name}")
                            continue

                    # Create action model for tool
                    action_model_for_tool = ActionModel(
                        tool_name=tool_name,
                        action_name=action_name,
                        params=params,
                        policy_info=content,
                    )
                    logger.debug(
                        f"Tool call: {tool_call}, Action model: {action_model_for_tool}"
                    )
                    results.append(action_model_for_tool)

                # Reset completion flags when using tools
                self._finished = False
                self.has_summary = False
            elif content:
                # Handle text response based on summary state
                if self.has_summary:
                    # Extract final answer if available
                    policy_info = extract_pattern(content, "final_answer")
                    if policy_info:
                        results.append(
                            ActionModel(
                                agent_name=GaiaAgents.GAIA_PLAN.value,
                                policy_info=policy_info,
                            )
                        )
                        self._finished = True
                    else:
                        results.append(
                            ActionModel(
                                agent_name=GaiaAgents.GAIA_PLAN.value,
                                policy_info=content,
                            )
                        )
                else:
                    # First summary response
                    results.append(
                        ActionModel(
                            agent_name=GaiaAgents.GAIA_PLAN.value,
                            policy_info=content,
                        )
                    )
                    self.has_summary = True
            else:
                # Empty content case
                logger.warning("Empty content in LLM response")
                results.append(
                    ActionModel(
                        agent_name=GaiaAgents.GAIA_PLAN.value,
                        policy_info="",
                    )
                )

            # Create agent result
            agent_result = AgentResult(
                actions=results, current_state=completions, is_call_tool=is_call_tool
            )

            # Update trajectory with new interaction
            self.trajectory.append((observation, info, agent_result))

            return agent_result
        except Exception as e:
            logger.error(f"Completion parsing failed: {str(e)}")
            return AgentResult(actions=[], current_state=completions)
